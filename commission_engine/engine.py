"""Commission engine.

Turns a deal log + a plan config into each rep's commission, monthly advances and
net payable. Every number traces back to a deal row and a plan constant.

Money is decimal.Decimal end to end. Amounts are rounded to the cent, half up, at
three defined points: each monthly advance, the quarter commission, and the
per-deal allocation of that commission. Rates and attainment are never rounded.
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

CENT = Decimal("0.01")
ZERO = Decimal("0")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
DEAL_TYPES = {"capital", "ifs", "consumables"}


class InputError(ValueError):
    """Bad input data: the run stops before any payout is calculated."""


class ReconciliationError(ValueError):
    """Credited deals do not tie back to the deal log."""


def cents(x) -> Decimal:
    """Round money to the cent, half up (1.005 -> 1.01, -1.005 -> -1.01)."""
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def _dec(value, what: str, required: bool = False) -> Decimal:
    """Parse money. A blank optional field is zero; a blank required field is an error."""
    if isinstance(value, Decimal):
        d = value
    else:
        if isinstance(value, float):
            value = repr(value)
        text = str(value).strip() if value is not None else ""
        if not text:
            if required:
                raise InputError(f"{what} is blank; a number is required")
            return ZERO
        try:
            d = Decimal(text)
        except InvalidOperation:
            raise InputError(f"{what}: {value!r} is not a number") from None
    if not d.is_finite():
        raise InputError(f"{what}: {value!r} is not a finite number")
    return d


# ---------------------------------------------------------------- data loading
@dataclass
class Deal:
    deal_id: str
    owner: str
    month: str              # YYYY-MM (close month)
    amount: Decimal         # gross amount
    discount: Decimal = ZERO   # discounts, rebates, giveaways
    deal_type: str = "capital"   # capital | ifs | consumables
    deposit: Decimal = ZERO    # IFS deposit
    fast_mql: bool = False

    def __post_init__(self):
        self.amount = _dec(self.amount, f"deal {self.deal_id} amount", required=True)
        self.discount = _dec(self.discount, f"deal {self.deal_id} discount")
        self.deposit = _dec(self.deposit, f"deal {self.deal_id} deposit")


@dataclass
class Rep:
    name: str
    role: str
    manager: str = ""


def load_deals(path: str | Path) -> list[Deal]:
    """Read the deal log. Rejects duplicate ids, bad months, blank amounts and non-numeric money."""
    rows, seen = [], {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        if "amount" not in (reader.fieldnames or []):
            raise InputError(f"{path}: no amount column")
        for line_no, r in enumerate(reader, start=2):
            where = f"{path} line {line_no}"
            deal_id = (r.get("deal_id") or "").strip()
            owner = (r.get("owner") or "").strip()
            month = (r.get("month") or "").strip()
            if not deal_id:
                raise InputError(f"{where}: missing deal_id")
            if deal_id in seen:
                raise InputError(f"{where}: duplicate deal_id {deal_id} (first seen on line {seen[deal_id]})")
            seen[deal_id] = line_no
            if not owner:
                raise InputError(f"{where}: deal {deal_id} has no owner")
            if not MONTH_RE.match(month):
                raise InputError(f"{where}: deal {deal_id} month {month!r} is not YYYY-MM")
            rows.append(Deal(
                deal_id=deal_id, owner=owner, month=month,
                amount=_dec(r.get("amount"), f"{where}: deal {deal_id} amount", required=True),
                discount=_dec(r.get("discount"), f"{where}: deal {deal_id} discount"),
                deal_type=(r.get("deal_type") or "capital").strip(),
                deposit=_dec(r.get("deposit"), f"{where}: deal {deal_id} deposit"),
                fast_mql=((r.get("fast_mql") or "").strip().lower() in {"1", "true", "yes"}),
            ))
    return rows


def load_reps(path: str | Path) -> list[Rep]:
    with open(path, newline="") as f:
        return [Rep(r["name"].strip(), r["role"].strip(), (r.get("manager") or "").strip())
                for r in csv.DictReader(f)]


def load_config(path: str | Path) -> dict:
    """Numbers in the config load as Decimal, so 0.1 means exactly 0.1."""
    return json.loads(Path(path).read_text(), parse_float=Decimal, parse_int=Decimal)


def validate_deals(deals: list[Deal], reps: list[Rep], config: dict) -> None:
    """Stop the run on data that would produce confident, wrong payouts."""
    names = {r.name for r in reps}
    for r in reps:
        if r.role not in config["plans"]:
            raise InputError(f"rep {r.name!r} has role {r.role!r}, which has no plan in the config")
        if r.manager and r.manager not in names:
            raise InputError(f"rep {r.name!r} has unknown manager {r.manager!r}")
    for d in deals:
        if d.owner not in names:
            raise InputError(f"deal {d.deal_id}: unknown owner {d.owner!r} (not in the rep roster)")
        if d.deal_type not in DEAL_TYPES:
            raise InputError(f"deal {d.deal_id}: deal_type {d.deal_type!r} is not one of {sorted(DEAL_TYPES)}")
        if d.amount < 0 or d.discount < 0 or d.deposit < 0:
            raise InputError(f"deal {d.deal_id}: amount, discount and deposit must not be negative")
        if d.discount > d.amount:
            raise InputError(f"deal {d.deal_id}: discount {d.discount} is larger than amount {d.amount}")


# ---------------------------------------------------------------- credit rules
@dataclass
class Credit:
    quota_revenue: Decimal = ZERO    # counts toward quota and tier
    flat_commission: Decimal = ZERO  # paid as-is, no quota credit
    excluded: Decimal = ZERO         # part of the gross amount that earns no credit
    rule: str = ""
    notes: list[str] = field(default_factory=list)


def credit_for(deal: Deal, plan: dict, ifs: dict) -> Credit:
    """How much of one deal counts toward quota, and any flat payments."""
    c = Credit()
    if deal.deal_type == "ifs":
        if deal.deposit >= ifs["full_credit_min_deposit"]:
            c.quota_revenue = deal.amount
            c.rule = "ifs: full credit"
            c.notes.append(f"{deal.deal_id}: IFS deposit {deal.deposit:,.0f} -> full credit")
        elif deal.deposit >= ifs["flat_min_deposit"]:
            c.flat_commission = cents(ifs["flat_commission"])
            c.excluded = deal.amount
            c.rule = "ifs: flat, no quota credit"
            c.notes.append(f"{deal.deal_id}: IFS deposit {deal.deposit:,.0f} -> flat {ifs['flat_commission']:,.0f}, no quota credit")
        else:
            c.excluded = deal.amount
            c.rule = "ifs: no commission"
            c.notes.append(f"{deal.deal_id}: IFS deposit {deal.deposit:,.0f} -> no commission")
        return c
    if plan.get("revenue") == "net_of_discounts":
        c.quota_revenue = deal.amount - deal.discount
        c.excluded = deal.discount
        c.rule = f"{deal.deal_type}: net of discounts"
    else:
        c.quota_revenue = deal.amount
        c.rule = f"{deal.deal_type}: full credit"
    return c


# ---------------------------------------------------------------- rate math
def tier_rate(revenue, basis, ladder: list[dict]) -> Decimal:
    """Highest ladder rate whose break (as % of basis) the revenue has reached."""
    revenue, basis = Decimal(revenue), Decimal(basis)
    rate = ladder[0]["rate"]
    for step in ladder:
        if revenue >= step["from_pct"] * basis:
            rate = step["rate"]
    return Decimal(rate)


def tiered_commission(revenue, plan: dict) -> Decimal:
    """Unrounded commission for one period. Callers round with cents()."""
    revenue = Decimal(revenue)
    basis = Decimal(plan.get("tier_basis", plan["quota"]))
    ladder = plan["ladder"]
    if plan.get("rate_mode", "retro") == "retro":
        # rate reached applies to every dollar of the period
        return revenue * tier_rate(revenue, basis, ladder)
    # marginal: each slice earns its own rate (like tax brackets)
    total = ZERO
    breaks = [s["from_pct"] * basis for s in ladder] + [None]
    for i, step in enumerate(ladder):
        lo, hi = breaks[i], breaks[i + 1]
        if revenue > lo:
            top = revenue if hi is None else min(revenue, hi)
            total += (top - lo) * step["rate"]
    return total


def leadership_commission(revenue, plan: dict) -> dict:
    revenue = Decimal(revenue)
    quota, target = Decimal(plan["quota"]), Decimal(plan["target_commission"])
    attain = revenue / quota if quota else ZERO
    base_attain = min(attain, Decimal(1)) if plan.get("base_cap_at_100") else attain
    base_attain = min(base_attain, plan.get("attainment_cap", base_attain))
    base = target * base_attain

    accel = ZERO
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
class AuditLine:
    """One deal row as credited to one person. Ties payouts back to the deal log."""
    deal_id: str
    owner: str
    month: str
    deal_type: str
    rule: str
    gross: Decimal
    excluded: Decimal
    credited_revenue: Decimal
    flat_commission: Decimal
    commission: Decimal = ZERO   # this deal's share of the quarter commission
    bonus: Decimal = ZERO        # fast-MQL bonus earned on this deal


@dataclass
class Statement:
    rep: str
    role: str
    quota: Decimal
    revenue: Decimal
    attainment: Decimal
    rate: Decimal | None
    earned_commission: Decimal   # tier / leadership commission for the quarter
    advances: dict[str, Decimal]
    flat_payments: Decimal
    bonuses: Decimal
    earned: Decimal              # earned_commission + flat_payments + bonuses
    advances_paid: Decimal       # already paid in earlier payrolls
    net_payable: Decimal         # earned - advances_paid; negative means recover
    payable_action: str          # PAY or RECOVER
    notes: list[str]
    lines: list[AuditLine]


def _allocate(total: Decimal, lines: list[AuditLine]) -> None:
    """Split a commission across deals by credited revenue, to the cent, summing exactly."""
    base = sum((l.credited_revenue for l in lines), ZERO)
    if not lines or base == 0 or total == 0:
        return
    raw = [total * l.credited_revenue / base for l in lines]
    shares = [r.quantize(CENT, rounding=ROUND_DOWN) for r in raw]
    left = int((total - sum(shares, ZERO)) / CENT)
    order = sorted(range(len(lines)), key=lambda i: (raw[i] - shares[i], -i), reverse=True)
    for i in order[:left]:
        shares[i] += CENT
    for line, share in zip(lines, shares):
        line.commission = share


def build_statement(rep: Rep, deals: list[Deal], config: dict,
                    team: dict[str, str] | None = None) -> Statement:
    """team: for leadership plans, {seller name: seller role} for the people who roll up.

    Each team deal is credited to the leader on the seller's own basis (for example
    net of discounts for account managers), so the leader and the seller agree.
    """
    plan = config["plans"][rep.role]
    ifs = config["ifs_rules"]
    months = config["quarter"]["months"]
    is_leader = plan["type"] == "leadership"
    if team is not None and not isinstance(team, dict):
        raise TypeError("team must map seller name -> seller role")
    sellers = {**(team or {}), rep.name: rep.role} if is_leader else {rep.name: rep.role}
    mine = [d for d in deals if d.owner in sellers and d.month in months]

    by_month = {m: ZERO for m in months}
    flat, notes, lines = ZERO, [], []
    bonus_each = ZERO if is_leader else cents(plan.get("bonus_per_fast_mql", 0))
    for d in mine:
        seller_role = sellers[d.owner]
        c = credit_for(d, config["plans"][seller_role], ifs)
        rule = c.rule if d.owner == rep.name else f"roll-up ({seller_role} basis) {c.rule}"
        line_flat = ZERO if is_leader else c.flat_commission  # flat IFS pay belongs to the seller
        lines.append(AuditLine(d.deal_id, d.owner, d.month, d.deal_type, rule, d.amount,
                               c.excluded, c.quota_revenue, line_flat,
                               bonus=bonus_each if d.fast_mql else ZERO))
        by_month[d.month] += c.quota_revenue
        flat += line_flat
        notes += c.notes
    revenue = sum(by_month.values(), ZERO)

    bonuses = sum((l.bonus for l in lines), ZERO)
    if bonuses:
        notes.append(f"fast-MQL bonus: {bonuses:,.2f}")

    advances: dict[str, Decimal] = {}
    if is_leader:
        lc = leadership_commission(revenue, plan)
        if lc["cap_applied"]:
            notes.append(f"capped at {plan['max_pct_of_revenue']:.0%} of revenue")
        earned_commission, rate, attainment = cents(lc["total"]), None, lc["attainment"]
    else:
        earned_commission = cents(tiered_commission(revenue, plan))
        rate = tier_rate(revenue, plan.get("tier_basis", plan["quota"]), plan["ladder"])
        attainment = revenue / plan["quota"] if plan["quota"] else ZERO
        if plan["cadence"] == "monthly_advance_with_true_up":
            # Each closed month is advanced at the rate that month alone reaches against a
            # monthly share of quota; the last month is settled by the quarter close.
            monthly_plan = dict(plan, quota=plan["quota"] / len(months))
            if "tier_basis" in plan:
                monthly_plan["tier_basis"] = plan["tier_basis"] / len(months)
            for m in months[:-1]:
                advances[m] = cents(tiered_commission(by_month[m], monthly_plan))

    _allocate(earned_commission, lines)
    earned = earned_commission + flat + bonuses
    advances_paid = sum(advances.values(), ZERO)
    net_payable = earned - advances_paid
    action = "RECOVER" if net_payable < 0 else "PAY"
    if action == "RECOVER":
        notes.append(f"net payable {net_payable:,.2f}: advances paid exceed quarter earnings; "
                     f"recover {-net_payable:,.2f}")

    return Statement(rep.name, rep.role, plan["quota"], revenue, attainment, rate,
                     earned_commission, advances, flat, bonuses, earned, advances_paid,
                     net_payable, action, notes, lines)


# ---------------------------------------------------------------- reconciliation
def reconcile(statements: list[Statement], deals: list[Deal], config: dict) -> list[str]:
    """Check credited deals tie back to the deal log. Returns a list of problems (empty = ok).

    - Every in-quarter deal is credited exactly once to the person who owns it. Owner lines
      are counted on every statement, a leader's included; a leader's roll-up lines for
      team deals are not, so team deals are not counted twice.
    - For every line, credited revenue + excluded amount = the deal's gross amount.
    - Statement totals equal the sum of their deal lines, and lines sum to earned pay.
    - A leader is credited the same revenue per deal as the owner.
    """
    months = set(config["quarter"]["months"])
    log = {d.deal_id: d for d in deals if d.month in months}
    leader_roles = {k for k, p in config["plans"].items() if p["type"] == "leadership"}
    owned = _owner_lines(statements)
    owner_line = {l.deal_id: l for _, l in owned}
    problems = []

    counts = Counter(l.deal_id for _, l in owned)
    for deal_id, d in log.items():
        if counts[deal_id] == 0:
            problems.append(f"{deal_id}: in the deal log ({d.owner}, {d.month}) but not credited to its owner")
        elif counts[deal_id] > 1:
            problems.append(f"{deal_id}: credited {counts[deal_id]} times")
    for deal_id in counts:
        if deal_id not in log:
            problems.append(f"{deal_id}: credited but not in this quarter's deal log")

    for s in statements:
        for l in s.lines:
            d = log.get(l.deal_id)
            if d is not None and l.gross != d.amount:
                problems.append(f"{s.rep} / {l.deal_id}: gross {l.gross} != deal log {d.amount}")
            if l.credited_revenue + l.excluded != l.gross:
                problems.append(f"{s.rep} / {l.deal_id}: credited + excluded != gross")
            if s.role in leader_roles and l.deal_id in owner_line:
                if l.credited_revenue != owner_line[l.deal_id].credited_revenue:
                    problems.append(f"{s.rep} / {l.deal_id}: leader credit {l.credited_revenue} "
                                    f"!= seller credit {owner_line[l.deal_id].credited_revenue}")
        if sum((l.credited_revenue for l in s.lines), ZERO) != s.revenue:
            problems.append(f"{s.rep}: revenue does not equal the sum of deal lines")
        if s.lines and sum((l.commission for l in s.lines), ZERO) != s.earned_commission and s.revenue:
            problems.append(f"{s.rep}: commission does not equal the sum of deal lines")
        if sum((l.flat_commission for l in s.lines), ZERO) != s.flat_payments:
            problems.append(f"{s.rep}: flat payments do not equal the sum of deal lines")
        if sum((l.bonus for l in s.lines), ZERO) != s.bonuses:
            problems.append(f"{s.rep}: bonuses do not equal the sum of deal lines")
        line_pay = sum((l.commission + l.flat_commission + l.bonus for l in s.lines), ZERO)
        if line_pay != s.earned:
            problems.append(f"{s.rep}: deal lines sum to {line_pay}, not earned {s.earned}")
    return problems


def _owner_lines(statements: list[Statement]) -> list[tuple[Statement, AuditLine]]:
    """Lines where the statement's person owns the deal: every seller line, plus any deal a
    leader owns directly. Leader roll-up lines for team deals are left out."""
    return [(s, l) for s in statements for l in s.lines if l.owner == s.rep]


def rule_summary(statements: list[Statement], config: dict) -> list[dict]:
    """Deal log totals by credit rule, on owner lines only (each deal once)."""
    out: dict[str, dict] = defaultdict(lambda: {"deals": 0, "gross": ZERO, "excluded": ZERO,
                                                "credited": ZERO, "flat": ZERO})
    for _, l in _owner_lines(statements):
        row = out[l.rule]
        row["deals"] += 1
        row["gross"] += l.gross
        row["excluded"] += l.excluded
        row["credited"] += l.credited_revenue
        row["flat"] += l.flat_commission
    return [{"rule": k, **v} for k, v in sorted(out.items())]


def run(deals_path, reps_path, config_path) -> list[Statement]:
    config = load_config(config_path)
    deals = load_deals(deals_path)
    reps = load_reps(reps_path)
    validate_deals(deals, reps, config)
    roles = {r.name: r.role for r in reps}
    teams: dict[str, dict[str, str]] = {}
    for r in reps:
        if r.manager:
            teams.setdefault(r.manager, {})[r.name] = roles[r.name]
    statements = [build_statement(r, deals, config, teams.get(r.name)) for r in reps]
    problems = reconcile(statements, deals, config)
    if problems:
        raise ReconciliationError("reconciliation failed:\n  " + "\n  ".join(problems))
    return statements
