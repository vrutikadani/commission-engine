from decimal import Decimal as D

import pytest

from commission_engine.engine import (Deal, Rep, build_statement, credit_for, leadership_commission,
                                      load_config, tier_rate, tiered_commission)

CFG = load_config("config/plans_q3.json")
AE = CFG["plans"]["AE"]


def test_retro_rate_applies_to_every_dollar():
    # 80% of 250K = 200K -> 10% on all of it
    assert tiered_commission(200_000, AE) == D("20000")
    assert tiered_commission(199_999, AE) == D("199999") * D("0.05")


def test_marginal_mode_works_like_brackets():
    plan = dict(AE, rate_mode="marginal")
    assert tiered_commission(220_000, plan) == D("200000") * D("0.05") + D("20000") * D("0.10")


def test_top_tier():
    assert tier_rate(D("275000"), 250_000, AE["ladder"]) == D("0.14")


def test_tier_basis_can_differ_from_quota():
    plan = dict(CFG["plans"]["ACCOUNT_MGR"], quota=D("230000"), tier_basis=D("200000"))
    assert tier_rate(180_000, plan["tier_basis"], plan["ladder"]) == D("0.08")


@pytest.mark.parametrize("deposit,quota_rev,flat", [(12000, 30000, 0), (7500, 0, 1500), (2500, 0, 0)])
def test_ifs_deposit_rules(deposit, quota_rev, flat):
    c = credit_for(Deal("D", "x", "2026-07", 30000, deal_type="ifs", deposit=deposit), AE, CFG["ifs_rules"])
    assert c.quota_revenue == quota_rev and c.flat_commission == flat


def test_account_manager_revenue_is_net_of_discounts():
    c = credit_for(Deal("D", "x", "2026-07", 10000, discount=800, deal_type="consumables"),
                   CFG["plans"]["ACCOUNT_MGR"], CFG["ifs_rules"])
    assert c.quota_revenue == 9200


def test_net_payable_is_earned_minus_advances_and_can_be_negative():
    # big July, nothing after: July advance at a high monthly tier, quarter lands in tier 1
    deals = [Deal("D1", "A", "2026-07", 75_000)]
    s = build_statement(Rep("A", "AE"), deals, CFG)
    assert s.earned_commission == D("3750.00")      # 75K at 5%
    assert s.advances["2026-07"] == D("7500.00")    # 75K at 10% against a monthly quota
    assert s.net_payable == s.earned - s.advances_paid
    assert s.net_payable < 0


def test_leadership_accelerators_and_attainment_cap():
    plan = CFG["plans"]["DIRECTOR"]
    q = plan["quota"]
    lc = leadership_commission(q * D("1.2"), plan)
    assert lc["accelerator"] == D("0.15") * q * D("0.01") + D("0.05") * q * D("0.02")
    # nothing extra above the 150% attainment cap
    assert leadership_commission(q * D("1.7"), plan)["accelerator"] == \
        leadership_commission(q * D("1.5"), plan)["accelerator"]


def test_leadership_revenue_cap():
    plan = dict(CFG["plans"]["DIRECTOR"], quota=D("10000"), target_commission=D("40000"))
    lc = leadership_commission(10_000, plan)
    assert lc["cap_applied"] and lc["total"] == D("1000")


def test_leader_rolls_up_team_revenue():
    deals = [Deal("D1", "A", "2026-07", 100_000), Deal("D2", "B", "2026-08", 50_000)]
    s = build_statement(Rep("Boss", "DIRECTOR"), deals, CFG, team={"A": "AE", "B": "AE"})
    assert s.revenue == 150_000


def test_deals_outside_quarter_are_ignored():
    s = build_statement(Rep("A", "AE"), [Deal("D1", "A", "2026-06", 99_000)], CFG)
    assert s.revenue == 0
