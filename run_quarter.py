"""Run a full quarter close on synthetic data.

    python run_quarter.py
"""
from pathlib import Path

from commission_engine import run
from commission_engine.engine import load_config
from commission_engine.report import write_csv, write_excel

CONFIG = "config/plans_q3.json"
statements = run("data/deals_q3.csv", "data/reps.csv", CONFIG)
label = load_config(CONFIG)["quarter"]["label"]

Path("output").mkdir(exist_ok=True)
write_csv(statements, "output/payouts_q3.csv")
write_excel(statements, "output/commission_dashboard_q3.xlsx", label)

print(f"{'Rep':<14}{'Role':<13}{'Revenue':>10}{'Attain':>8}{'Rate':>7}{'Payout':>10}{'True-up':>10}")
for s in statements:
    rate = f"{s.rate:.0%}" if s.rate is not None else "-"
    print(f"{s.rep:<14}{s.role:<13}{s.revenue/1e3:>9.1f}K{s.attainment:>8.0%}{rate:>7}"
          f"{s.total_payout/1e3:>9.1f}K{s.true_up/1e3:>9.1f}K")
total = sum(s.total_payout for s in statements)
print(f"\nTotal payout ${total/1e3:.1f}K  ->  output/payouts_q3.csv, output/commission_dashboard_q3.xlsx")
