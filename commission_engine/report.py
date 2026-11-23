"""Outputs: a payout CSV for payroll, a deal-level audit CSV, and an Excel workbook."""
from __future__ import annotations

import csv
from pathlib import Path

from .engine import Statement, cents

COLUMNS = ["rep", "role", "quota", "revenue", "attainment", "rate", "earned_commission",
           "flat_payments", "bonuses", "earned", "advances_paid", "net_payable", "payable_action"]

AUDIT_COLUMNS = ["rep", "role", "deal_id", "owner", "month", "deal_type", "rule", "gross",
                 "excluded", "credited_revenue", "flat_commission", "commission", "bonus"]


def _is_leader(s: Statement) -> bool:
    """Leadership statements have no tier rate. Their revenue rolls up the team's deals,
    so totals count each deal once by summing seller rows only."""
    return s.rate is None


def _row(s: Statement) -> dict:
    return {
        "rep": s.rep, "role": s.role, "quota": cents(s.quota), "revenue": cents(s.revenue),
        "attainment": round(s.attainment, 4), "rate": s.rate if s.rate is not None else "",
        "earned_commission": cents(s.earned_commission), "flat_payments": cents(s.flat_payments),
        "bonuses": cents(s.bonuses), "earned": cents(s.earned), "advances_paid": cents(s.advances_paid),
        "net_payable": cents(s.net_payable), "payable_action": s.payable_action,
    }


def write_csv(statements: list[Statement], path: str | Path) -> None:
    """Payroll file. net_payable is what to pay now; negative means recover from the rep."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for s in statements:
            w.writerow(_row(s))


def write_audit_csv(statements: list[Statement], path: str | Path) -> None:
    """One row per deal per person credited. commission + flat_commission + bonus over a
    person's rows sums to their earned pay."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=AUDIT_COLUMNS)
        w.writeheader()
        for s in statements:
            for l in s.lines:
                w.writerow({"rep": s.rep, "role": s.role, "deal_id": l.deal_id, "owner": l.owner,
                            "month": l.month, "deal_type": l.deal_type, "rule": l.rule,
                            "gross": cents(l.gross), "excluded": cents(l.excluded),
                            "credited_revenue": cents(l.credited_revenue),
                            "flat_commission": cents(l.flat_commission),
                            "commission": cents(l.commission), "bonus": cents(l.bonus)})


def write_excel(statements: list[Statement], path: str | Path, quarter_label: str) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Dashboard"
    ink, slate = "1F2A44", "E8EDF5"
    ws["A1"] = f"{quarter_label} Commission Dashboard"
    ws["A1"].font = Font(size=16, bold=True, color=ink)

    headers = ["Rep", "Role", "Quota ($K)", "Revenue ($K)", "Attainment", "Tier rate",
               "Commission ($K)", "Flat + bonus ($K)", "Earned ($K)", "Advances paid ($K)",
               "Net payable ($K)", "Action"]
    ws.append([])
    ws.append(headers)
    for c in ws[3]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=ink)
        c.alignment = Alignment(horizontal="center", wrap_text=True)

    k = lambda v: v / 1000
    seller_rows = []
    for s in statements:
        ws.append([s.rep, s.role, k(s.quota), k(s.revenue), s.attainment, s.rate,
                   k(s.earned_commission), k(s.flat_payments + s.bonuses), None,
                   k(s.advances_paid), None, s.payable_action])
        r = ws.max_row
        # live formulas so reviewers can audit them in Excel
        ws[f"I{r}"] = f"=G{r}+H{r}"
        ws[f"K{r}"] = f"=I{r}-J{r}"
        if not _is_leader(s):
            seller_rows.append(r)
    first, last = 4, ws.max_row
    revenue_total = "=SUM(" + ",".join(f"D{r}" for r in seller_rows) + ")" if seller_rows else 0
    ws.append(["Total", "", None, revenue_total, "", "",
               f"=SUM(G{first}:G{last})", f"=SUM(H{first}:H{last})", f"=SUM(I{first}:I{last})",
               f"=SUM(J{first}:J{last})", f"=SUM(K{first}:K{last})", ""])
    total_row = ws.max_row
    for c in ws[total_row]:
        c.font = Font(bold=True, color=ink)
        c.fill = PatternFill("solid", fgColor=slate)
    ws.append([])
    ws.append(["Revenue total counts each deal once (seller rows only). Leader revenue is a "
               "roll-up of the same deals. Quotas are not summed because leader and rep quotas overlap."])
    ws.append(["Net payable = earned - advances paid. A negative value (RECOVER) is owed back by the rep."])

    for row in ws.iter_rows(min_row=4, max_row=total_row):
        for c in row[2:4] + row[6:11]:
            c.number_format = '$#,##0.0"K";-$#,##0.0"K"'
        row[4].number_format = "0.0%"
        row[5].number_format = "0.0%"
    for i, w in enumerate([18, 13, 11, 12, 11, 9, 12, 12, 11, 12, 12, 10], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    audit = wb.create_sheet("Deal Audit")
    audit.append(["Rep", "Role", "Deal", "Owner", "Month", "Type", "Rule", "Gross ($)",
                  "Excluded ($)", "Credited ($)", "Flat ($)", "Commission share ($)", "Bonus ($)"])
    for c in audit[1]:
        c.font = Font(bold=True)
    for s in statements:
        for l in s.lines:
            audit.append([s.rep, s.role, l.deal_id, l.owner, l.month, l.deal_type, l.rule,
                          l.gross, l.excluded, l.credited_revenue, l.flat_commission, l.commission,
                          l.bonus])
    for row in audit.iter_rows(min_row=2):
        for c in row[7:13]:
            c.number_format = "$#,##0.00"
    for i, w in enumerate([18, 13, 10, 16, 9, 12, 42, 12, 12, 12, 10, 14, 10], 1):
        audit.column_dimensions[get_column_letter(i)].width = w

    notes = wb.create_sheet("Audit Notes")
    notes.append(["Rep", "Note"])
    for s in statements:
        for n in s.notes:
            notes.append([s.rep, n])
    notes.column_dimensions["A"].width = 18
    notes.column_dimensions["B"].width = 80
    wb.save(path)
