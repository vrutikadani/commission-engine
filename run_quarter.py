"""Run a full quarter close on synthetic data.

    python run_quarter.py
"""
from pathlib import Path

from commission_engine import run
from commission_engine.engine import load_config, rule_summary
from commission_engine.report import write_audit_csv, write_csv, write_excel

CONFIG = "config/plans_q3.json"
config = load_config(CONFIG)
statements = run("data/deals_q3.csv", "data/reps.csv", CONFIG)  # raises if it does not reconcile

Path("output").mkdir(exist_ok=True)
write_csv(statements, "output/payouts_q3.csv")
write_audit_csv(statements, "output/deal_audit_q3.csv")
write_excel(statements, "output/commission_dashboard_q3.xlsx", config["quarter"]["label"])

print(f"{'Rep':<14}{'Role':<13}{'Revenue':>10}{'Attain':>8}{'Rate':>7}{'Earned':>10}{'Advanced':>10}{'Net':>10}")
for s in statements:
    rate = f"{s.rate:.0%}" if s.rate is not None else "-"
    print(f"{s.rep:<14}{s.role:<13}{s.revenue/1000:>9.1f}K{s.attainment:>8.0%}{rate:>7}"
          f"{s.earned/1000:>9.1f}K{s.advances_paid/1000:>9.1f}K{s.net_payable/1000:>9.1f}K"
          f"{'  RECOVER' if s.payable_action == 'RECOVER' else ''}")

print("\nReconciliation (owner lines, each deal counted once):")
for r in rule_summary(statements, config):
    print(f"  {r['rule']:<34}{r['deals']:>3} deals  gross {r['gross']/1000:>6.1f}K  "
          f"credited {r['credited']/1000:>6.1f}K  excluded {r['excluded']/1000:>5.1f}K")
net = sum(s.net_payable for s in statements)
print(f"\nNet payable this payroll ${net/1000:.1f}K  ->  output/payouts_q3.csv, "
      "output/deal_audit_q3.csv, output/commission_dashboard_q3.xlsx")
