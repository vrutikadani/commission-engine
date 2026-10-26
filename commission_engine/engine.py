"""Commission engine.

Turns a deal log + a plan config into each rep's commission, monthly advances and
quarterly true-up. Every number traces back to a deal row and a plan constant.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------- data loading
@dataclass
class Deal:
    deal_id: str
    owner: str
    month: str            # YYYY-MM (close month)
    amount: float         # gross amount
    discount: float = 0.0 # discounts, rebates, giveaways
    deal_type: str = "capital"   # capital | ifs | consumables
    deposit: float = 0.0  # IFS deposit
    fast_mql: bool = False


@dataclass
class Rep:
    name: str
    role: str
    manager: str = ""


def load_deals(path: str | Path) -> list[Deal]:
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            rows.append(Deal(
                deal_id=r["deal_id"], owner=r["owner"], month=r["month"],
                amount=float(r["amount"]), discount=float(r.get("discount") or 0),
                deal_type=r.get("deal_type") or "capital", deposit=float(r.get("deposit") or 0),
                fast_mql=(r.get("fast_mql", "").strip().lower() in {"1", "true", "yes"}),
            ))
    return rows


def load_reps(path: str | Path) -> list[Rep]:
    with open(path, newline="") as f:
        return [Rep(r["name"], r["role"], r.get("manager", "")) for r in csv.DictReader(f)]


def load_config(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


# ---------------------------------------------------------------- credit rules
@dataclass
class Credit:
    quota_revenue: float = 0.0   # counts toward quota and tier
    flat_commission: float = 0.0 # paid as-is, no quota credit
    notes: list[str] = field(default_factory=list)


def credit_for(deal: Deal, plan: dict, ifs: dict) -> Credit:
    """How much of one deal counts toward quota, and any flat payments."""
    c = Credit()
    if deal.deal_type == "ifs":
        if deal.deposit >= ifs["full_credit_min_deposit"]:
            c.quota_revenue = deal.amount
            c.notes.append(f"{deal.deal_id}: IFS deposit {deal.deposit:,.0f} -> full credit")
        elif deal.deposit >= ifs["flat_min_deposit"]:
            c.flat_commission = ifs["flat_commission"]
            c.notes.append(f"{deal.deal_id}: IFS deposit {deal.deposit:,.0f} -> flat {ifs['flat_commission']:,.0f}, no quota credit")
        else:
            c.notes.append(f"{deal.deal_id}: IFS deposit {deal.deposit:,.0f} -> no commission")
        return c
    amount = deal.amount - deal.discount if plan.get("revenue") == "net_of_discounts" else deal.amount
    c.quota_revenue = amount
    return c


# ---------------------------------------------------------------- rate math
def tier_rate(revenue: float, basis: float, ladder: list[dict]) -> float:
    """Highest ladder rate whose break (as % of basis) the revenue has reached."""
    rate = ladder[0]["rate"]
    for step in ladder:
        if revenue >= step["from_pct"] * basis - 1e-9:
            rate = step["rate"]
    return rate


def tiered_commission(revenue: float, plan: dict) -> float:
    basis = plan.get("tier_basis", plan["quota"])
    ladder = plan["ladder"]
    if plan.get("rate_mode", "retro") == "retro":
        # rate reached applies to every dollar of the period
        return revenue * tier_rate(revenue, basis, ladder)
    # marginal: each slice earns its own rate (like tax brackets)
    total, breaks = 0.0, [s["from_pct"] * basis for s in ladder] + [float("inf")]
    for i, step in enumerate(ladder):
        lo, hi = breaks[i], breaks[i + 1]
        if revenue > lo:
            total += (min(revenue, hi) - lo) * step["rate"]
    return total


def leadership_commission(revenue: float, plan: dict) -> dict:
    quota, target = plan["quota"], plan["target_commission"]
    attain = revenue / quota if quota else 0.0
    base_attain = min(attain, 1.0) if plan.get("base_cap_at_100") else attain
    base_attain = min(base_attain, plan.get("attainment_cap", base_attain))
    base = target * base_attain

    accel = 0.0
    capped = min(attain, plan.get("attainment_cap", attain))
    for band in plan.get("accelerators", []):
        lo, hi = band["from_pct"] * quota, band["to_pct"] * quota
        top = capped * quota
        if top > lo:
            accel += (min(top, hi) - lo) * band["rate"]

    total = base + accel
    cap = plan.get("max_pct_of_revenue")
    capped_total = min(total, cap * revenue) if cap else total
    return {"attainment": attain, "base": base, "accelerator": accel,
            "total": capped_total, "cap_applied": capped_total < total}


# ---------------------------------------------------------------- statement
@dataclass
class Statement:
    rep: str
    role: str
    quota: float
    revenue: float
    attainment: float
    rate: float | None
    earned_quarter: float
    advances: dict[str, float]
    true_up: float
    flat_payments: float
    bonuses: float
    total_payout: float
    notes: list[str]


def build_statement(rep: Rep, deals: list[Deal], config: dict, team: set[str] | None = None) -> Statement:
    """team: for leadership plans, the names whose deals roll up to this leader."""
    plan = config["plans"][rep.role]
    ifs = config["ifs_rules"]
    months = config["quarter"]["months"]
    owners = (team or set()) | {rep.name} if plan["type"] == "leadership" else {rep.name}
    mine = [d for d in deals if d.owner in owners and d.month in months]

    by_month = {m: 0.0 for m in months}
    flat, notes = 0.0, []
    for d in mine:
        c = credit_for(d, plan, ifs)
        by_month[d.month] += c.quota_revenue
        flat += c.flat_commission
        notes += c.notes
    revenue = sum(by_month.values())

    bonuses = plan.get("bonus_per_fast_mql", 0) * sum(1 for d in mine if d.fast_mql) if plan["type"] != "leadership" else 0
    if bonuses:
        notes.append(f"fast-MQL bonus: {bonuses:,.0f}")

    if plan["type"] == "leadership":
        flat = 0.0  # flat IFS payments belong to the selling rep, not the leader
        lc = leadership_commission(revenue, plan)
        if lc["cap_applied"]:
            notes.append(f"capped at {plan['max_pct_of_revenue']:.0%} of revenue")
        earned = lc["total"]
        return Statement(rep.name, rep.role, plan["quota"], revenue, lc["attainment"], None,
                         earned, {}, earned, flat, bonuses, earned + flat + bonuses, notes)

    earned = tiered_commission(revenue, plan)
    rate = tier_rate(revenue, plan.get("tier_basis", plan["quota"]), plan["ladder"])

    advances: dict[str, float] = {}
    if plan["cadence"] == "monthly_advance_with_true_up":
        # Each closed month is advanced at the rate that month alone reaches against a
        # monthly share of quota; the last month is settled by the quarterly true-up.
        monthly_plan = dict(plan, quota=plan["quota"] / len(months))
        if "tier_basis" in plan:
            monthly_plan["tier_basis"] = plan["tier_basis"] / len(months)
        for m in months[:-1]:
            advances[m] = tiered_commission(by_month[m], monthly_plan)
    true_up = earned - sum(advances.values())   # negative = recoverable overpayment
    if true_up < 0:
        notes.append(f"true-up negative ({true_up:,.2f}): advances exceeded quarter earnings")

    return Statement(rep.name, rep.role, plan["quota"], revenue,
                     revenue / plan["quota"] if plan["quota"] else 0.0, rate,
                     earned, advances, true_up, flat, bonuses,
                     earned + flat + bonuses, notes)


def run(deals_path, reps_path, config_path) -> list[Statement]:
    config = load_config(config_path)
    deals = load_deals(deals_path)
    reps = load_reps(reps_path)
    teams: dict[str, set[str]] = {}
    for r in reps:
        if r.manager:
            teams.setdefault(r.manager, set()).add(r.name)
    return [build_statement(r, deals, config, teams.get(r.name)) for r in reps]
