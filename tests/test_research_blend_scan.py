"""2026-10-18 (使用者：繼續研究有什麼更好的策略): the search over accounts split across rules, stage 1 on the
development months of the members' registered accounts."""
import numpy as np
import pytest

from quant_platform.research import blend_scan as bs
from quant_platform.research.daily import DailyRule

PAID = np.full(8, 100.0)                                         # no money after the start: the values are the returns
BENCH = np.array([100, 110, 99, 89.1, 98, 108, 119, 130.0])     # 0050 falls 19% from month 1 to month 3


def member(trial, values):
    return {"trial": trial, "name": f"每天 規則{trial}：前 {trial} 名", "tier": "T0 候選",
            "rule": DailyRule(name=f"規則{trial}", factors={"trend_200": 1.0}, top=trial),
            "values": np.array(values, dtype=float), "bench": BENCH, "paid": PAID}


def test_the_measures_by_hand():
    calm = np.array([[100, 108, 102, 96, 104, 113, 123, 134.0]])
    found = bs.measure(calm, BENCH, PAID, bs.falls(bs.month_returns(BENCH, PAID)))
    assert found["excess"][0] == pytest.approx(0.04)                         # (134 - 130) / 100
    assert found["drawdown"][0] == pytest.approx(96 / 108 - 1)
    assert found["bench_drawdown"] == pytest.approx(89.1 / 110 - 1)
    assert found["gap"][0] == pytest.approx((96 / 108 - 1) - (89.1 / 110 - 1))   # fell 8 points less than 0050


def test_the_search_keeps_calm_blends_apart_from_near_copies():
    wild = [100, 118, 106, 90, 102, 120, 150, 190.0]                # makes the most, falls 24% when 0050 falls 19%
    copy = [value * (1 + 0.001 * (n % 2)) for n, value in enumerate(wild)]
    calm = [100, 103, 108, 104, 109, 112, 118, 134.0]                # up 1% while 0050 falls
    weak = [100, 99, 101, 100, 98, 101, 99, 102.0]
    members = [member(10, wild), member(11, copy), member(12, calm), member(13, weak)]
    found = bs.search(members, count=5)
    assert found["candidates"] == 6 + 4 and found["families"] == 3          # the wild rule and its copy: one family
    assert found["finalists"]
    returns = bs.month_returns(np.stack([np.array(m["values"]) for m in members]), PAID)
    volatility = returns.std(axis=1, ddof=1)
    for item in found["finalists"]:
        assert not {10, 11} <= set(item["members"])                          # never a rule with its near copy
        assert item["dev_excess"] > 0 and item["dev_gap"] >= -bs.CALM_GAP
        assert sum(item["shares"]) == pytest.approx(1.0, abs=1e-9)
        weights = [1 / volatility[[m["trial"] for m in members].index(t)] for t in item["members"]]
        assert item["shares"] == pytest.approx([w / sum(weights) for w in weights], abs=1e-3)   # risk parity
        assert item["blend"].sleeves[0].rule.top in (10, 11, 12, 13)
    # the wild rule with the calm one, once: the copy's blend is the same two families
    assert len(found["finalists"]) == 1 and found["passed"] == 2
    families = [frozenset(10 if t == 11 else t for t in item["members"]) for item in found["finalists"]]
    assert all(len(a & b) < 2 for n, a in enumerate(families) for b in families[n + 1:])


def test_a_blend_already_registered_is_not_run_again():
    wild = [100, 118, 106, 90, 102, 120, 150, 190.0]
    calm = [100, 103, 108, 104, 109, 112, 118, 134.0]
    members = [member(10, wild), member(12, calm)]
    first = bs.search(members)
    assert first["finalists"]
    again = bs.search(members, taken={item["blend"].rule_hash for item in first["finalists"]})
    assert again["finalists"] == []


def test_the_role_accounts_take_their_shares_by_risk_parity(monkeypatch):
    """2026-10-20: one sleeve a role (ROLE_SPLITS), shares by 1 / the development months' volatility."""
    import numpy as np

    from quant_platform.research import blend_scan
    from quant_platform.research.daily import DailyRule

    months = [f"20{15 + index // 12}-{index % 12 + 1:02d}" for index in range(12)]
    paid = np.arange(12, dtype=float) * 10_000 + 300_000
    rng = np.random.default_rng(1)

    def member(trial, swing):
        growth = np.cumprod(1 + rng.normal(0.01, swing, 12))
        return {"trial": trial, "name": f"規則 {trial}", "tier": "T0 候選", "months": months,
                "rule": DailyRule(name=f"規則 {trial}", factors={"trend_200": 1.0}),
                "values": paid * growth, "bench": paid * 1.0, "paid": paid}
    pool = [member(4941, 0.10), member(4589, 0.05), member(5052, 0.05), member(4547, 0.02), member(5040, 0.08)]
    monkeypatch.setattr(blend_scan, "pool", lambda research=None: pool)
    blends = blend_scan.role_blends()
    assert len(blends) == len(blend_scan.ROLE_SPLITS)
    shares = [sleeve.share for sleeve in blends[0].sleeves]
    volatility = [blend_scan.month_returns(item["values"], paid).std(ddof=1) for item in pool[:4]]
    expected = (1 / np.array(volatility)) / (1 / np.array(volatility)).sum()
    assert np.allclose(shares, expected, atol=0.002) and abs(sum(shares) - 1) < 1e-9
    assert shares[0] == min(shares)                                   # the most volatile role gets the least
    monkeypatch.setattr(blend_scan, "pool", lambda research=None: pool[1:])
    assert blend_scan.role_blends() == []                             # a member missing: no account
