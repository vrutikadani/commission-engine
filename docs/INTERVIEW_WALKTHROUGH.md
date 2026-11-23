# Interview walkthrough

A short tour of the code, in the order I would explain it.

## What this is, and what it is not

- This is a synthetic Python reference engine. All plans, names, quotas and deals are made up.
- It is not the commission process I run at work. Real payouts at work go through a governed
  spreadsheet model and CRM reports, with review and sign-off before payroll.
- This repo shows the same kinds of rules as code, with tests, so the logic can be read and checked.

## Why no LLM touches the calculation

- Pay must be exact and repeatable. The same inputs must give the same cents every time.
- Every number must trace to a deal row and a plan constant. A model's output cannot be traced that way.
- Plan rules are fixed in config. There is nothing to guess, so a model adds risk and no value.
- If AI is used at all, it belongs outside the math: for example, drafting a plain-language
  explanation of a statement that a person then checks against the numbers.

## 1. Inputs and checks

- Config loads with `parse_float=Decimal`, so `0.1` is exactly 0.1:
  `load_config` in `commission_engine/engine.py`.
- `load_deals` stops on a duplicate `deal_id`, a month that is not `YYYY-MM`, or an amount that is
  blank, missing or not a number. Errors name the file line. Blank discount or deposit counts as zero.
- `validate_deals` stops on an owner not in the roster, an unknown deal type, a role with no plan,
  negative money, or a discount larger than the deal.
- Tests: `tests/test_money_and_audit.py`, section "input validation".

## 2. Credit rules

- `credit_for` decides how much of one deal counts toward quota.
- IFS (financing) deals use the deposit thresholds in `ifs_rules`: full credit, a flat payment with
  no quota credit, or nothing.
- Account-manager plans pay net of discounts. The discount is recorded as `excluded`.
- Every deal gets a `rule` label, for example `consumables: net of discounts`.

## 3. Rate math

- `tier_rate` finds the highest ladder step reached. No float tolerance is needed with Decimal.
- `tiered_commission` supports `retro` (reached rate on every dollar) and `marginal` (bracket) modes.
- `leadership_commission` pays base in proportion to attainment, adds accelerators by band,
  caps attainment, then caps the total as a share of revenue.

## 4. One statement per person

`build_statement` in `engine.py`:

- Collects the person's in-quarter deals. For a leader, the team's deals too.
- Leader roll-up uses each seller's own plan for credit. A $10K deal with a $1K discount sold by an
  account manager credits $9K to the leader, not $10K. Test:
  `test_leader_rollup_uses_each_sellers_credit_basis`.
- Rounds to the cent, half up, at three points only: each monthly advance, the quarter commission,
  and each deal's share. Test: `test_decimal_avoids_float_drift` (20.90 x 5% = 1.045 rounds to 1.05;
  float gives 1.04).
- Payroll columns:
  - `earned` = quarter commission + flat IFS payments + bonuses
  - `advances_paid` = monthly advances already paid
  - `net_payable` = `earned` - `advances_paid`
  - `payable_action` = `PAY`, or `RECOVER` when net payable is negative
- Test: `test_earned_advances_and_net_payable_reconcile`.

## 5. Audit trail

- Every deal becomes an `AuditLine`: deal id, rule, gross, excluded, credited, flat, commission share,
  and the fast-MQL bonus if the deal earned one.
- `_allocate` splits the quarter commission across deals by credited revenue, to the cent, using
  largest remainder. Commission + flat + bonus over the lines always sums to earned pay exactly.
- Tests: `test_audit_lines_trace_to_deal_rows_and_sum_to_commission`, `test_non_ifs_deals_have_audit_lines`.

## 6. Reconciliation

`reconcile` in `engine.py` returns a list of problems. `run` raises `ReconciliationError` if it is
not empty. It checks:

- every in-quarter deal is credited exactly once to its owner. A leader's own deals count on the
  leader's statement; a leader's roll-up lines for team deals are not counted again
- no credited deal is missing from the log
- credited + excluded = gross on every line
- statement revenue, commission, flat pay and bonuses equal the sum of their lines, and the lines
  sum to earned pay
- leader credit per deal equals seller credit per deal

`rule_summary` prints totals by rule, so the deal log can be tied out by rule.
Test: `test_reconciliation_catches_a_missing_deal`.

## 7. Outputs

`commission_engine/report.py`:

- `write_csv`: payroll file with `earned`, `advances_paid`, `net_payable`, `payable_action`.
- `write_audit_csv`: one row per deal per person credited, with commission, flat and bonus columns.
- `write_excel`: dashboard with live formulas. The Total row sums revenue over seller rows only
  (the leader row repeats the same deals) and leaves quota blank (quotas overlap).
  Test: `test_excel_total_row_does_not_double_count` reads the file back with openpyxl.

## Questions I would expect

- **Why retro tiers?** It is a plan-design choice in the sample config. It creates a cliff at each
  break. `marginal` mode removes the cliff.
- **What if a deal moves months?** It is paid in the month on the log. Checks catch bad formats,
  not wrong facts. A real process needs a CRM tie-out before payroll.
- **What is not modeled?** Cross-quarter clawbacks, deal splits, mid-quarter role changes.

## Run

```bash
python run_quarter.py      # writes output/ and prints the reconciliation by rule
python -m pytest -q
```
