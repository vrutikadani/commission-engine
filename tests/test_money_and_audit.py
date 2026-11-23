"""Money precision, payroll columns, roll-up basis, validation, reconciliation, audit trail."""
import re
from decimal import Decimal

import pytest

from commission_engine.engine import (Deal, InputError, Rep, build_statement, cents, load_config,
                                      load_deals, reconcile, run, tiered_commission, validate_deals)

CFG = load_config("config/plans_q3.json")
AE = CFG["plans"]["AE"]
D = Decimal


# ------------------------------------------------------------------ Decimal money
def test_config_and_deals_use_decimal():
    assert isinstance(AE["quota"], Decimal)
    assert isinstance(AE["ladder"][1]["rate"], Decimal)
    assert isinstance(Deal("D", "x", "2026-07", "10.10").amount, Decimal)


def test_cents_rounds_half_up():
    assert cents(D("1.005")) == D("1.01")
    assert cents(D("2.675")) == D("2.68")
    assert cents(D("-1.005")) == D("-1.01")


def test_decimal_avoids_float_drift():
    # 20.90 * 5% is exactly 1.045, which rounds half-up to 1.05.
    # Binary float stores 20.90 a hair low, so the same math rounds to 1.04.
    assert round(20.90 * 0.05, 2) == 1.04
    assert cents(tiered_commission(D("20.90"), AE)) == D("1.05")

    # Small deal amounts: float sums drift, Decimal sums are exact.
    assert sum([0.10, 0.10, 0.10]) != 0.30
    deals = [Deal(f"D{i}", "A", "2026-09", "0.10") for i in range(3)]
    s = build_statement(Rep("A", "AE"), deals, CFG)
    assert s.revenue == D("0.30")
    assert s.earned_commission == D("0.02")  # 0.015 rounded half-up


def test_statement_money_is_quantized_to_cents():
    deals = [Deal("D1", "A", "2026-07", "33333.33"), Deal("D2", "A", "2026-08", "11111.11")]
    s = build_statement(Rep("A", "AE"), deals, CFG)
    for v in [s.earned_commission, s.earned, s.advances_paid, s.net_payable, *s.advances.values()]:
        assert v == cents(v)


# ------------------------------------------------------------------ payroll columns
def test_earned_advances_and_net_payable_reconcile():
    deals = [Deal("D1", "A", "2026-07", 75_000), Deal("D2", "A", "2026-08", 20_000)]
    s = build_statement(Rep("A", "AE"), deals, CFG)
    assert s.advances_paid == sum(s.advances.values())
    assert s.advances_paid + s.net_payable == s.earned
    assert s.earned == s.earned_commission + s.flat_payments + s.bonuses


def test_negative_net_payable_is_labeled_recovery():
    s = build_statement(Rep("A", "AE"), [Deal("D1", "A", "2026-07", 75_000)], CFG)
    assert s.net_payable < 0
    assert s.payable_action == "RECOVER"
    assert any("recover" in n.lower() for n in s.notes)


def test_positive_net_payable_is_labeled_pay():
    s = build_statement(Rep("A", "AE"), [Deal("D1", "A", "2026-09", 75_000)], CFG)
    assert s.advances_paid == 0 and s.net_payable == s.earned and s.payable_action == "PAY"


# ------------------------------------------------------------------ leader roll-up basis
def test_leader_rollup_uses_each_sellers_credit_basis():
    deals = [
        Deal("D1", "AE1", "2026-07", 100_000),
        Deal("D2", "AM1", "2026-08", 10_000, discount=1_000, deal_type="consumables"),
    ]
    team = {"AE1": "AE", "AM1": "ACCOUNT_MGR"}
    s = build_statement(Rep("Boss", "DIRECTOR"), deals, CFG, team=team)
    # AM is paid on net (9,000), so the leader is credited net too, not gross 10,000.
    assert s.revenue == D("109000")


def test_leader_rollup_applies_ifs_rules():
    deals = [Deal("D1", "AE1", "2026-07", 30_000, deal_type="ifs", deposit=7_500)]
    s = build_statement(Rep("Boss", "DIRECTOR"), deals, CFG, team={"AE1": "AE"})
    assert s.revenue == 0 and s.flat_payments == 0


# ------------------------------------------------------------------ input validation
HEADER = "deal_id,owner,month,amount,discount,deal_type,deposit,fast_mql\n"


def _write(tmp_path, body):
    p = tmp_path / "deals.csv"
    p.write_text(HEADER + body)
    return p


def test_duplicate_deal_id_rejected(tmp_path):
    p = _write(tmp_path, "D1,A,2026-07,100,0,capital,0,0\nD1,A,2026-08,200,0,capital,0,0\n")
    with pytest.raises(InputError, match="duplicate deal_id D1"):
        load_deals(p)


@pytest.mark.parametrize("month", ["2026-7", "2026-13", "July", "2026/07", ""])
def test_malformed_month_rejected(tmp_path, month):
    p = _write(tmp_path, f"D1,A,{month},100,0,capital,0,0\n")
    with pytest.raises(InputError, match="month"):
        load_deals(p)


def test_bad_amount_rejected(tmp_path):
    p = _write(tmp_path, "D1,A,2026-07,12k,0,capital,0,0\n")
    with pytest.raises(InputError, match="amount"):
        load_deals(p)


def test_unknown_owner_rejected():
    with pytest.raises(InputError, match="unknown owner 'Ghost'"):
        validate_deals([Deal("D1", "Ghost", "2026-07", 100)], [Rep("A", "AE")], CFG)


def test_unknown_deal_type_rejected():
    with pytest.raises(InputError, match="deal_type"):
        validate_deals([Deal("D1", "A", "2026-07", 100, deal_type="lease")], [Rep("A", "AE")], CFG)


# ------------------------------------------------------------------ reconciliation + audit trail
@pytest.fixture(scope="module")
def quarter():
    statements = run("data/deals_q3.csv", "data/reps.csv", "config/plans_q3.json")
    return statements, load_deals("data/deals_q3.csv")


def test_reconciliation_passes_on_sample_data(quarter):
    statements, deals = quarter
    assert reconcile(statements, deals, CFG) == []


def test_reconciliation_catches_a_missing_deal(quarter):
    statements, deals = quarter
    extra = deals + [Deal("D-9999", "Riley Chen", "2026-07", 1_000)]
    problems = reconcile(statements, extra, CFG)
    assert any("D-9999" in p for p in problems)


def test_every_in_quarter_deal_credited_exactly_once(quarter):
    statements, deals = quarter
    seen = [l.deal_id for s in statements if s.role != "DIRECTOR" for l in s.lines]
    assert sorted(seen) == sorted(d.deal_id for d in deals if d.month in CFG["quarter"]["months"])


def test_audit_lines_trace_to_deal_rows_and_sum_to_commission(quarter):
    statements, deals = quarter
    by_id = {d.deal_id: d for d in deals}
    for s in statements:
        if s.role == "DIRECTOR":
            continue
        assert sum(l.commission for l in s.lines) == s.earned_commission
        assert sum(l.flat_commission for l in s.lines) == s.flat_payments
        for l in s.lines:
            d = by_id[l.deal_id]
            assert l.gross == d.amount
            assert l.credited_revenue + l.excluded == d.amount
            assert l.rule


def test_non_ifs_deals_have_audit_lines():
    deals = [Deal("D1", "A", "2026-07", "1000.00"), Deal("D2", "A", "2026-08", "2000.00")]
    s = build_statement(Rep("A", "AE"), deals, CFG)
    assert [l.deal_id for l in s.lines] == ["D1", "D2"]
    assert all(l.rule == "capital: full credit" for l in s.lines)
    assert sum(l.commission for l in s.lines) == s.earned_commission


def test_leader_audit_lines_sum_to_revenue(quarter):
    statements, _ = quarter
    leader = next(s for s in statements if s.role == "DIRECTOR")
    assert sum(l.credited_revenue for l in leader.lines) == leader.revenue
    assert sum(l.commission for l in leader.lines) == leader.earned_commission


# ------------------------------------------------------------------ Excel Total row
def _refs(formula):
    out = []
    for a, b in re.findall(r"([A-Z]+\d+)(?::([A-Z]+\d+))?", formula):
        if b:
            col, r1, r2 = a[0], int(a[1:]), int(b[1:])
            out += [f"{col}{r}" for r in range(r1, r2 + 1)]
        else:
            out.append(a)
    return out


def test_excel_total_row_does_not_double_count(quarter, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from commission_engine.report import write_excel
    statements, _ = quarter
    path = tmp_path / "dash.xlsx"
    write_excel(statements, path, "Q3 2026")
    ws = openpyxl.load_workbook(path)["Dashboard"]
    headers = [c.value for c in ws[3]]
    rev_col = headers.index("Revenue ($K)") + 1
    quota_col = headers.index("Quota ($K)") + 1
    total_row = next(r for r in range(4, ws.max_row + 1) if ws.cell(r, 1).value == "Total")

    rev_formula = ws.cell(total_row, rev_col).value
    refs = _refs(rev_formula)
    leader_rows = [r for r in range(4, total_row) if ws.cell(r, 2).value == "DIRECTOR"]
    col = ws.cell(4, rev_col).column_letter
    assert not any(f"{col}{r}" in refs for r in leader_rows)
    total = sum(Decimal(str(ws[ref].value)) for ref in refs)
    ic_revenue = sum(s.revenue for s in statements if s.role != "DIRECTOR") / 1000
    assert total == ic_revenue
    # quotas overlap (leader quota covers the team), so they are not summed
    assert ws.cell(total_row, quota_col).value in (None, "")


def test_excel_has_payroll_columns_and_deal_audit(quarter, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from commission_engine.report import write_excel
    statements, _ = quarter
    path = tmp_path / "dash.xlsx"
    write_excel(statements, path, "Q3 2026")
    wb = openpyxl.load_workbook(path)
    headers = [c.value for c in wb["Dashboard"][3]]
    for h in ["Earned ($K)", "Advances paid ($K)", "Net payable ($K)", "Action"]:
        assert h in headers
    assert "Deal Audit" in wb.sheetnames


# ------------------------------------------------------------------ required amount
def test_blank_amount_rejected(tmp_path):
    p = _write(tmp_path, "D1,A,2026-07,,0,capital,0,0\n")
    with pytest.raises(InputError, match="amount"):
        load_deals(p)


def test_missing_amount_column_rejected(tmp_path):
    p = tmp_path / "deals.csv"
    p.write_text("deal_id,owner,month,discount,deal_type,deposit,fast_mql\nD1,A,2026-07,0,capital,0,0\n")
    with pytest.raises(InputError, match="amount"):
        load_deals(p)


def test_deal_with_blank_amount_rejected():
    with pytest.raises(InputError, match="amount"):
        Deal("D1", "A", "2026-07", "")
    with pytest.raises(InputError, match="amount"):
        Deal("D1", "A", "2026-07", None)


def test_blank_optional_money_fields_default_to_zero(tmp_path):
    p = _write(tmp_path, "D1,A,2026-07,100,,capital,,0\n")
    (d,) = load_deals(p)
    assert d.amount == D("100") and d.discount == 0 and d.deposit == 0


# ------------------------------------------------------------------ fast-MQL bonus audit lines
def _line_total(l):
    return l.commission + l.flat_commission + l.bonus


def test_fast_mql_bonus_is_recorded_on_its_deal_line():
    deals = [Deal("D1", "A", "2026-07", 20_000, fast_mql=True), Deal("D2", "A", "2026-09", 30_000)]
    s = build_statement(Rep("A", "INSIDE"), deals, CFG)
    bonus = {l.deal_id: l.bonus for l in s.lines}
    assert bonus == {"D1": cents(CFG["plans"]["INSIDE"]["bonus_per_fast_mql"]), "D2": 0}
    assert sum(l.bonus for l in s.lines) == s.bonuses
    assert sum(_line_total(l) for l in s.lines) == s.earned


def test_sample_audit_lines_sum_to_earned(quarter):
    statements, _ = quarter
    assert any(s.bonuses for s in statements)
    for s in statements:
        assert sum((_line_total(l) for l in s.lines), D(0)) == s.earned, s.rep


def test_reconcile_catches_bonus_line_mismatch(quarter):
    import copy
    statements, deals = quarter
    statements = copy.deepcopy(statements)
    s = next(s for s in statements if s.bonuses)
    line = next(l for l in s.lines if l.bonus)
    line.bonus -= D("0.01")
    problems = reconcile(statements, deals, CFG)
    assert any(s.rep in p and "bonus" in p for p in problems)


def test_audit_csv_has_bonus_and_seller_rows_sum_to_earned(quarter, tmp_path):
    import csv
    from collections import defaultdict
    from commission_engine.report import write_audit_csv
    statements, _ = quarter
    path = tmp_path / "audit.csv"
    write_audit_csv(statements, path)
    rows = list(csv.DictReader(open(path, newline="")))
    assert "bonus" in rows[0]
    totals = defaultdict(D)
    for r in rows:
        totals[r["rep"]] += D(r["commission"]) + D(r["flat_commission"]) + D(r["bonus"])
    for s in statements:
        assert totals[s.rep] == s.earned, s.rep


def test_excel_deal_audit_has_bonus_column(quarter, tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from commission_engine.report import write_excel
    statements, _ = quarter
    path = tmp_path / "dash.xlsx"
    write_excel(statements, path, "Q3 2026")
    ws = openpyxl.load_workbook(path)["Deal Audit"]
    headers = [c.value for c in ws[1]]
    assert "Bonus ($)" in headers
    col = headers.index("Bonus ($)")
    rep_col = headers.index("Rep")
    for s in statements:
        sheet_bonus = sum(D(str(r[col].value)) for r in ws.iter_rows(min_row=2) if r[rep_col].value == s.rep)
        assert sheet_bonus == s.bonuses, s.rep


# ------------------------------------------------------------------ leader's own deals
def _roster_with_director_deal(tmp_path):
    deals = tmp_path / "deals.csv"
    deals.write_text(HEADER
                     + "D1,Avery Brooks,2026-07,30000,0,capital,0,0\n"
                     + "D2,Morgan Hale,2026-08,40000,0,capital,0,0\n")
    reps = tmp_path / "reps.csv"
    reps.write_text("name,role,manager\nAvery Brooks,AE,Morgan Hale\nMorgan Hale,DIRECTOR,\n")
    return deals, reps


def test_director_owned_deal_reconciles_once(tmp_path):
    from commission_engine.engine import rule_summary
    deals_path, reps_path = _roster_with_director_deal(tmp_path)
    statements = run(deals_path, reps_path, "config/plans_q3.json")  # must not raise
    director = next(s for s in statements if s.role == "DIRECTOR")
    assert sorted(l.deal_id for l in director.lines) == ["D1", "D2"]
    assert director.revenue == D("70000")
    assert reconcile(statements, load_deals(deals_path), CFG) == []
    summary = rule_summary(statements, CFG)
    assert sum(r["deals"] for r in summary) == 2
    assert sum(r["gross"] for r in summary) == D("70000")


def test_director_owned_deal_counted_once_not_with_rollup_lines(tmp_path):
    import copy
    deals_path, reps_path = _roster_with_director_deal(tmp_path)
    try:
        statements = run(deals_path, reps_path, "config/plans_q3.json")
    except Exception:
        pytest.fail("director-owned deal should reconcile")
    log = load_deals(deals_path)
    # the director's roll-up line for the AE's deal D1 is not a second credit
    assert not any("D1" in p for p in reconcile(statements, log, CFG))
    # but a director-owned deal credited twice is still caught
    statements = copy.deepcopy(statements)
    director = next(s for s in statements if s.role == "DIRECTOR")
    director.lines.append(copy.deepcopy(next(l for l in director.lines if l.deal_id == "D2")))
    assert any("D2" in p and "2 times" in p for p in reconcile(statements, log, CFG))
