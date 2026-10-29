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


def test_the_guarded_pool_keeps_the_200_session_guards_only():
    """2026-10-25 (research_method §7 組合搜尋第八輪)."""
    from quant_platform.research.blend_scan import guarded
    from quant_platform.research.daily import DailyRule

    base = {"name": "x", "factors": {"trend_200": 1.0}}
    assert guarded({"rule": DailyRule(**base, vol_scale="taiex_half")})
    assert guarded({"rule": DailyRule(**base, market_filter="taiex_200")})
    assert not guarded({"rule": DailyRule(**base)}) and not guarded({"rule": DailyRule(**base, market_filter="taiex_200s")})


def test_the_blocks_cover_every_combination_in_order():
    import itertools

    for size in (2, 3):
        joined = np.concatenate(list(bs.combo_blocks(7, size, block=4)))
        assert joined.tolist() == [list(combo) for combo in itertools.combinations(range(7), size)]


def test_the_recent_gate_and_ranking():
    """2026-10-28 (第九輪): from the split month a blend must beat 0050 (time-weighted); the score is 1/4 the yearly
    excess before it and 3/4 the one after — a rule that only won early is left out, one that wins after ranks first."""
    paid = np.full(25, 100.0)
    bench = 100 * 1.01 ** np.arange(25)                               # 0050 +1% a month
    early_only = 100 * np.r_[1.02 ** np.arange(13), 1.02 ** 12 * 1.005 ** np.arange(1, 13)]
    late = 100 * np.r_[1.01 ** np.arange(13), 1.01 ** 12 * 1.02 ** np.arange(1, 13)]
    found = bs.measure(np.stack([early_only, late]), bench, paid, [], split=13)
    assert found["recent"][0] < 0 < found["recent"][1]
    assert found["early"][0] > 0 and abs(found["early"][1]) < 1e-9
    assert found["recent"][1] == pytest.approx((1.02 / 1.01) ** 12 - 1)
    months = [f"{2019 + (index + 8) // 12}-{(index + 8) % 12 + 1:02d}" for index in range(25)]
    assert months[13] == "2020-10"

    def rule(trial, values):
        return {**member(trial, values), "bench": bench, "paid": paid, "months": months}
    other = 100 * np.r_[1.0, np.cumprod(np.where(np.arange(24) % 2, 0.015, 0.009) + 1)]   # +1.2% a month, uneven
    members = [rule(10, early_only), rule(12, late), rule(13, other)]
    found = bs.search(members, sizes=(2,), recent_split=13)
    assert found["ranking"] == "recent" and found["finalists"]
    best = found["finalists"][0]
    assert 12 in best["members"] and best["recent"] > 0
    assert best["score"] == pytest.approx(0.25 * best["early"] + 0.75 * best["recent"], abs=2e-4)


def test_the_frontier_search_keeps_only_blends_no_member_beats_on_both():
    """2026-10-28 (第十輪): a member that makes more and falls less than a blend knocks it out; a blend between a rich
    rule and a calm one that neither beats on both stays."""
    wild = [100, 118, 106, 90, 102, 120, 150, 190.0]               # the most money, falls 24% when 0050 falls 19%
    calm = [100, 103, 108, 104, 109, 112, 118, 134.0]               # little money, up while 0050 falls
    best = [100, 104, 109, 106, 111, 125, 150, 200.0]               # more money than the wild one and calm in the fall
    members = [member(10, wild), member(12, calm)]
    found = bs.search(members, sizes=(2,), frontier=True)
    assert [item["members"] for item in found["finalists"]] == [[10, 12]]      # neither beats the blend on both
    members.append(member(14, best))
    found = bs.search(members, sizes=(2,), frontier=True)
    assert found["passed"] == 0 and not found["finalists"]                    # #14 alone beats every blend on both


def test_the_fourth_member_goes_only_on_the_triples_that_stayed():
    """2026-10-28 (第十一輪): with ``extend`` each kept triple is tried again with every member numbered after its three;
    the counts add up and a finalist may hold four rules, each a different family."""
    rng = np.random.default_rng(3)
    paths = []
    for number in range(6):
        steps = 1 + rng.normal(0.03, 0.05, 7)
        paths.append(list(100 * np.r_[1.0, np.cumprod(steps)]))
    members = [member(10 + number, path) for number, path in enumerate(paths)]
    plain = bs.search(members, sizes=(2, 3), frontier=True)
    found = bs.search(members, sizes=(2, 3), frontier=True, extend=True)
    assert found["candidates"] >= plain["candidates"]
    assert all(len(item["members"]) <= 4 for item in found["finalists"])
    assert all(len(set(item["members"])) == len(item["members"]) for item in found["finalists"])


def test_each_size_is_counted_and_chosen_apart():
    """2026-10-29 (第十二輪): round eleven had no four-member finalist — a four grew from a kept triple and shared three
    families with it, so it was always skipped once the triple was taken. With ``per_size`` each size has its own
    finalists and counts."""
    rng = np.random.default_rng(5)
    members = []
    for number in range(8):
        steps = 1 + rng.normal(0.03, 0.05, 7)
        members.append(member(10 + number, list(100 * np.r_[1.0, np.cumprod(steps)])))
    found = bs.search(members, sizes=(2, 3), frontier=True, extend=True, per_size=2)
    assert set(found["sizes"]) <= {"2", "3", "4"} and found["sizes"]["2"]["candidates"] == 28
    assert sum(item["passed"] for item in found["sizes"].values()) == found["passed"]
    by_size = {}
    for item in found["finalists"]:
        by_size.setdefault(len(item["members"]), []).append(item)
    assert all(len(items) <= 2 for items in by_size.values())
    for size, counts in found["sizes"].items():                      # every size that passed has a finalist
        assert (counts["passed"] > 0) == (int(size) in by_size)


def test_the_downside_risk_and_the_role_blocks_by_hand():
    """2026-10-29 (空頭防護第十二版): the loss in 0050's falling months only; one attacker with the rest defenders."""
    bench = bs.month_returns(BENCH, PAID)                         # falls in months 2 and 3 (index 1, 2)
    returns = np.array([[0.05, -0.10, -0.20, 0.0, 0.1, 0.1, 0.1],
                        [-0.30, 0.02, -0.04, 0.0, 0.1, 0.1, 0.1],
                        [0.0, 0.01, 0.02, -0.5, 0.1, 0.1, 0.1]])
    risk = bs.downside_risk(returns, bench)
    assert risk[0] == pytest.approx(np.sqrt((0.10 ** 2 + 0.20 ** 2) / 2))
    assert risk[1] == pytest.approx(np.sqrt((0.0 + 0.04 ** 2) / 2))          # its −30% came while 0050 rose
    assert risk[2] == pytest.approx(1e-3)                                     # never lost while 0050 fell: the floor
    pairs = np.concatenate(list(bs.role_blocks(np.array([5, 0]), np.array([1, 3, 2]), 2)))
    assert sorted(map(tuple, pairs.tolist())) == [(0, 1), (0, 2), (0, 3), (1, 5), (2, 5), (3, 5)]
    triples = np.concatenate(list(bs.role_blocks(np.array([0]), np.array([1, 2, 3]), 3)))
    assert sorted(map(tuple, triples.tolist())) == [(0, 1, 2), (0, 1, 3), (0, 2, 3)]
    assert list(bs.role_blocks(np.array([0]), np.array([1]), 3)) == []


def test_the_bear_search_pairs_attackers_with_calm_defenders_at_downside_shares():
    """An attacker with defenders only, shares by 1 / the loss in 0050's falls, never further than 0050 in a fall; each
    finalist's twin at the usual shares."""
    wild = [100, 118, 106, 90, 102, 120, 150, 190.0]               # the attacker: falls 24% when 0050 falls 19%
    calm = [100, 103, 108, 104, 109, 112, 118, 134.0]               # up while 0050 falls
    steady = [100, 104, 101, 98, 104, 110, 117, 128.0]              # down 6% while 0050 falls 19%
    members = [member(10, wild), member(12, calm), member(13, steady)]
    attack, defense = bs.bear_roles(members)
    assert attack.tolist() == [0] and defense.tolist() == [1, 2]
    found = bs.search(members, sizes=(2, 3), roles=(attack, defense), weighting="downside", calm_floor=0.0,
                      frontier=True, room=bs.DRAWDOWN_ROOM)
    assert found["roles"] == {"attack": 1, "defense": 2} and found["weighting"] == "downside"
    assert found["candidates"] == 2 + 1                               # (wild, calm), (wild, steady), (wild, calm, steady)
    assert found["finalists"]
    values = np.stack([np.array(m["values"]) for m in members])
    risk = bs.downside_risk(bs.month_returns(values, PAID), bs.month_returns(BENCH, PAID))
    for item in found["finalists"]:
        assert 10 in item["members"] and item["dev_gap"] >= 0
        weights = [1 / risk[[m["trial"] for m in members].index(t)] for t in item["members"]]
        assert item["shares"] == pytest.approx([w / sum(weights) for w in weights], abs=1e-3)
        twin, shares = bs.volatility_twin(members, item)
        assert twin.name.startswith("組合搜尋（份額照波動）") and twin.rule_hash != item["blend"].rule_hash
        assert sum(shares) == pytest.approx(1.0, abs=1e-9)


def test_the_contribution_shares_by_hand():
    """2026-10-29 (空頭防護第十三版): equal parts in the blend's CVaR — 20 months, the worst 5% is one month."""
    attack = np.full(20, 0.02)
    attack[3] = -0.10                                              # loses 10% in the month everything falls
    calm = np.full(20, 0.01)
    calm[3] = -0.02
    shares = bs.contribution_shares(np.stack([attack, calm])[None])[0]
    assert shares == pytest.approx([1 / 6, 5 / 6], abs=1e-5)       # 1 / 10% and 1 / 2%, normalised
    assert shares[0] * 0.10 == pytest.approx(shares[1] * 0.02, abs=1e-6)   # the parts are equal
    gainer = np.full(20, 0.01)
    gainer[3] = 0.03                     # gains in that month: counts as losing 0.1%, so it takes nearly everything —
    shares = bs.contribution_shares(np.stack([attack, gainer])[None])[0]     # until the blend no longer loses there
    assert 0 < shares[0] < 0.1 and shares.sum() == pytest.approx(1.0)        # (no equal parts exist: it settles low)
    many = bs.contribution_shares(np.random.default_rng(1).normal(0.01, 0.05, (50, 3, 135)))
    assert many.sum(axis=1) == pytest.approx(np.ones(50)) and (many > 0).all()


def test_the_bear13_search_shares_by_equal_cvar_parts():
    wild = [100, 118, 106, 90, 102, 120, 150, 190.0]
    calm = [100, 103, 108, 104, 109, 112, 118, 134.0]
    steady = [100, 104, 101, 98, 104, 110, 117, 128.0]
    members = [member(10, wild), member(12, calm), member(13, steady)]
    found = bs.search(members, sizes=(2, 3), roles=bs.bear_roles(members), weighting="contribution", calm_floor=0.0,
                      frontier=True, room=bs.DRAWDOWN_ROOM)
    assert found["weighting"] == "contribution" and found["finalists"]
    returns = bs.month_returns(np.stack([np.array(m["values"]) for m in members]), PAID)
    trials = [m["trial"] for m in members]
    for item in found["finalists"]:
        rows = [trials.index(t) for t in item["members"]]
        assert item["shares"] == pytest.approx(bs.contribution_shares(returns[rows][None])[0].tolist(), abs=1e-3)


def test_how_far_a_blend_pushes_the_frontier_by_hand():
    """2026-10-29 (組合搜尋第十三輪): money over the most money among the frontier points no worse in 0050's falls."""
    front_money, front_gap = np.array([800.0, 600.0, 300.0]), np.array([-0.08, 0.02, 0.07])
    found = bs.reach(np.array([700.0, 700.0, 900.0, 200.0]), np.array([0.03, -0.10, 0.0, 0.09]),
                     front_money, front_gap, 100.0)
    assert found[0] == pytest.approx(700 / 300 - 1)        # calmer than 0.02: only the 0.07 point is no worse
    assert found[1] == pytest.approx(700 / 800 - 1)        # riskier than all: the most money anywhere (inside)
    assert found[2] == pytest.approx(900 / 600 - 1)        # at 0.0: the 0.02 and 0.07 points, 600 the most
    assert found[3] == pytest.approx(200 / 100 - 1)        # calmer than every point: over 0050's money


def test_the_reach_search_counts_registered_blends_on_the_frontier():
    wild = [100, 118, 106, 90, 102, 120, 150, 190.0]
    calm = [100, 103, 108, 104, 109, 112, 118, 134.0]
    steady = [100, 104, 101, 98, 104, 110, 117, 128.0]
    members = [member(10, wild), member(12, calm), member(13, steady)]
    plain = bs.search(members, sizes=(2,), frontier=True, rank="frontier", recent_split=4, references={"money": np.zeros(0), "gap": np.zeros(0)})
    assert plain["rank"] == "frontier" and plain["finalists"]
    best = plain["finalists"][0]
    # a registered blend with more money and a smaller fall than every pair: nothing stays
    covered = bs.search(members, sizes=(2,), frontier=True, rank="frontier", recent_split=4,
                        references={"money": np.array([1e6]), "gap": np.array([0.5])})
    assert covered["finalists"] == [] and covered["frontier_points"] == 1
    # the reach is the ranking: the finalists in falling reach
    scores = [item["score"] for item in plain["finalists"]]
    assert scores == sorted(scores, reverse=True) and best["score"] > 0
