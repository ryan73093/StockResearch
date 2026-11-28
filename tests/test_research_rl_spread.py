"""2026-11-28 RL 第十二版 (research/rl_spread.py): the to-date way, passing over rules that hold the same stocks."""
import numpy as np

from quant_platform.research import rl_select, rl_spread


def test_shared_is_over_the_smaller_list_and_an_empty_list_shares_nothing():
    assert rl_spread.shared(frozenset({1, 2, 3, 4}), frozenset({3, 4})) == 1.0
    assert rl_spread.shared(frozenset({1, 2, 3, 4}), frozenset({4, 5})) == 0.5
    assert rl_spread.shared(frozenset(), frozenset({1})) == 0.0


def test_a_rule_holding_what_a_chosen_one_holds_is_passed_over_and_only_fills_what_is_left(monkeypatch):
    monkeypatch.setattr(rl_select, "PICK", 3)
    monkeypatch.setattr(rl_select, "PER_FAMILY", 5)
    scores = np.array([9.0, 8.0, 7.0, 6.0, 5.0])
    lists = [frozenset({1, 2}), frozenset({1, 2}), frozenset({2, 3}), frozenset({7, 8}), frozenset()]
    families = ["a", "b", "c", "d", "e"]
    assert rl_spread.choose(scores, [], families, lists.__getitem__, None, 5) == ([0, 1, 2], 0)   # as rl_select's
    assert rl_spread.choose(scores, [], families, lists.__getitem__, None, 5)[0] == rl_select.choose(scores, [],
                                                                                                    families, 5)
    assert rl_spread.choose(scores, [], families, lists.__getitem__, 0.5, 5) == ([0, 3, 4], 0)    # 1 and 2 share half
    assert rl_spread.choose(scores, [], families, lists.__getitem__, 0.6, 5) == ([0, 2, 3], 0)    # 2 shares half only
    assert rl_spread.choose(scores[:3], [], families[:3], lists.__getitem__, 0.5, 5) == ([0, 1, 2], 2)   # filled
    assert rl_spread.choose(scores, [3], families, lists.__getitem__, 0.5, 5) == ([3, 0, 4], 0)   # the held one first


def _data(rules=60, months=100, seed=3):
    rng = np.random.default_rng(seed)
    base = rng.normal(0.01, 0.04, months)
    returns = base[None, :] + np.linspace(-0.01, 0.01, rules)[:, None] + rng.normal(0, 0.01, (rules, months))
    return {"months": [f"{2015 + m // 12}-{1 + m % 12:02d}" for m in range(months)], "base": base, "returns": returns,
            "trials": list(range(rules)), "names": [str(n) for n in range(rules)],
            "families": [f"f{n % 6}" for n in range(rules)], "guard": np.zeros(rules), "blend": np.zeros(rules)}


def test_without_a_limit_the_account_is_rl_selects_and_with_one_it_holds_more_different_stocks(monkeypatch):
    monkeypatch.setattr(rl_select, "PICK", 5)
    data = _data()
    rules = [[(f"k{n // 2}", 1.0)] for n in range(60)]                 # neighbours two by two hold the same list
    have = {f"k{n}": {month: [n * 10 + i for i in range(10)] for month in data["months"]} for n in range(30)}
    have["k29"] = {}                                                   # one part never holds anything
    start = data["months"].index(rl_select.FIRST) - 1
    runs, found = rl_spread.follows(data, rules, have, start)
    active = data["returns"] - data["base"][None, :]
    same = rl_select.follow(data, start, lambda at: active[:, :at + 1].sum(axis=1), rl_spread.KEEP)
    assert runs["to_date"]["gains"] == same["gains"] and runs["to_date"]["held"] == same["held"]
    assert set(found) == {"spread", "spread_30", "spread_70", "to_date", "last12", "all"}
    assert found["spread"]["different_stocks"] > found["to_date"]["different_stocks"]
    assert found["spread"]["filled"] == 0 and len(runs["spread"]["held"]) == 5
    judgment = rl_select.judge({"robot": found["spread"], **{n: found[n] for n in ("last12", "to_date", "all")}})
    assert judgment["best_fixed"] in ("last12", "to_date", "all")
    later = {**data, "returns": data["returns"].copy()}
    later["returns"][:, -1] += 0.3                                     # the last month is nobody's input
    assert rl_spread.follows(later, rules, have, start)[0]["spread"]["held"] == runs["spread"]["held"]
