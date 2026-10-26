"""Outputs: a payout CSV for payroll and an Excel workbook for the CFO and reps."""
from __future__ import annotations

import csv
from pathlib import Path

from .engine import Statement

COLUMNS = ["rep", "role", "quota", "revenue", "attainment", "rate", "earned_quarter",
           "advances_total", "true_up", "flat_payments", "bonuses", "total_payout"]


def _row(s: Statement) -> dict:
    return {
        "rep": s.rep, "role": s.role, "quota": round(s.quota, 2), "revenue": round(s.revenue, 2),
        "attainment": round(s.attainment, 4), "rate": s.rate if s.rate is not None else "",
        "earned_quarter": round(s.earned_quarter, 2), "advances_total": round(sum(s.advances.values()), 2),
        "true_up": round(s.true_up, 2), "flat_payments": round(s.flat_payments, 2),
        "bonuses": round(s.bonuses, 2), "total_payout": round(s.total_payout, 2),
    }


def write_csv(statements: list[Statement], path: str | Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for s in statements:
            w.writerow(_row(s))


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
               "Earned ($K)", "Advances ($K)", "True-up ($K)", "Flat + bonus ($K)", "Total payout ($K)"]
    ws.append([])
    ws.append(headers)
    for c in ws[3]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor=ink)
        c.alignment = Alignment(horizontal="center", wrap_text=True)

    k = lambda v: v / 1000
    for s in statements:
        ws.append([s.rep, s.role, k(s.quota), k(s.revenue), s.attainment, s.rate,
                   k(s.earned_quarter), k(sum(s.advances.values())), k(s.true_up),
                   k(s.flat_payments + s.bonuses), None])
        r = ws.max_row
        # payout is a live formula so reviewers can audit it in Excel
        ws[f"K{r}"] = f"=G{r}+J{r}"
    first, last = 4, ws.max_row
    ws.append(["Total", "", f"=SUM(C{first}:C{last})", f"=SUM(D{first}:D{last})", "", "",
               f"=SUM(G{first}:G{last})", f"=SUM(H{first}:H{last})", f"=SUM(I{first}:I{last})",
               f"=SUM(J{first}:J{last})", f"=SUM(K{first}:K{last})"])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True, color=ink)
        c.fill = PatternFill("solid", fgColor=slate)

    for row in ws.iter_rows(min_row=4, max_row=ws.max_row):
        for c in row[2:4] + row[6:11]:
            c.number_format = '$#,##0.0"K"'
        row[4].number_format = "0.0%"
        row[5].number_format = "0.0%"
    for i, w in enumerate([18, 13, 11, 12, 11, 9, 11, 12, 11, 13, 14], 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    notes = wb.create_sheet("Audit Notes")
    notes.append(["Rep", "Note"])
    for s in statements:
        for n in s.notes:
            notes.append([s.rep, n])
    notes.column_dimensions["A"].width = 18
    notes.column_dimensions["B"].width = 80
    wb.save(path)
