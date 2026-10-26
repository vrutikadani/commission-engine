import pytest

from commission_engine.engine import (Deal, Rep, build_statement, credit_for, leadership_commission,
                                      load_config, tier_rate, tiered_commission)

CFG = load_config("config/plans_q3.json")
AE = CFG["plans"]["AE"]


def test_retro_rate_applies_to_every_dollar():
    # 75% of 240K = 180K -> 12% on all of it
    assert tiered_commission(180_000, AE) == pytest.approx(180_000 * 0.12)
    assert tiered_commission(179_999, AE) == pytest.approx(179_999 * 0.07)


def test_marginal_mode_works_like_brackets():
    plan = dict(AE, rate_mode="marginal")
    expected = 180_000 * 0.07 + 20_000 * 0.12
    assert tiered_commission(200_000, plan) == pytest.approx(expected)


def test_top_tier():
    assert tier_rate(240_000 * 1.22, 240_000, AE["ladder"]) == 0.15


def test_tier_basis_can_differ_from_quota():
    plan = dict(CFG["plans"]["ACCOUNT_MGR"], quota=105_000, tier_basis=100_000)
    assert tier_rate(100_000, plan["tier_basis"], plan["ladder"]) == 0.13


@pytest.mark.parametrize("deposit,quota_rev,flat", [(9000, 30000, 0), (5000, 0, 2000), (1000, 0, 0)])
def test_ifs_deposit_rules(deposit, quota_rev, flat):
    c = credit_for(Deal("D", "x", "2026-07", 30000, deal_type="ifs", deposit=deposit), AE, CFG["ifs_rules"])
    assert c.quota_revenue == quota_rev and c.flat_commission == flat


def test_account_manager_revenue_is_net_of_discounts():
    c = credit_for(Deal("D", "x", "2026-07", 10000, discount=800, deal_type="consumables"),
                   CFG["plans"]["ACCOUNT_MGR"], CFG["ifs_rules"])
    assert c.quota_revenue == 9200


def test_true_up_is_quarter_earned_minus_advances_and_can_be_negative():
    # big July, nothing after: July advance at a high monthly tier, quarter lands in tier 1
    deals = [Deal("D1", "A", "2026-07", 70_000)]
    s = build_statement(Rep("A", "AE"), deals, CFG)
    assert s.earned_quarter == pytest.approx(70_000 * 0.07)
    assert s.advances["2026-07"] == pytest.approx(70_000 * 0.12)
    assert s.true_up == pytest.approx(s.earned_quarter - sum(s.advances.values()))
    assert s.true_up < 0


def test_leadership_accelerators_and_attainment_cap():
    plan = CFG["plans"]["DIRECTOR"]
    q = plan["quota"]
    lc = leadership_commission(q * 1.15, plan)
    expected_accel = 0.10 * q * 0.02 + 0.05 * q * 0.03
    assert lc["accelerator"] == pytest.approx(expected_accel)
    # nothing extra above 130%
    assert leadership_commission(q * 1.5, plan)["accelerator"] == pytest.approx(
        leadership_commission(q * 1.3, plan)["accelerator"])


def test_leadership_revenue_cap():
    plan = dict(CFG["plans"]["DIRECTOR"], quota=10_000, target_commission=25_000)
    lc = leadership_commission(10_000, plan)
    assert lc["cap_applied"] and lc["total"] == pytest.approx(1_700)


def test_leader_rolls_up_team_revenue():
    deals = [Deal("D1", "A", "2026-07", 100_000), Deal("D2", "B", "2026-08", 50_000)]
    s = build_statement(Rep("Boss", "DIRECTOR"), deals, CFG, team={"A", "B"})
    assert s.revenue == 150_000


def test_deals_outside_quarter_are_ignored():
    s = build_statement(Rep("A", "AE"), [Deal("D1", "A", "2026-06", 99_000)], CFG)
    assert s.revenue == 0
