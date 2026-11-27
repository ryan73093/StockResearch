"""2026-11-27 RL 第十一版 (research/rl_holdings.py): the robot also sees what each rule holds at the month's end."""
from datetime import date

import numpy as np

from quant_platform.research import rl_holdings, rl_select


def test_the_list_in_force_is_the_last_one_decided_by_the_months_last_session():
    sessions = [date(2020, 1, 30), date(2020, 1, 31), date(2020, 2, 3), date(2020, 2, 27), date(2020, 3, 2)]
    ends = rl_holdings.month_ends(sessions, date(2020, 1, 1), date(2020, 2, 28))
    assert ends == {"2020-01": 1, "2020-02": 3}                       # March ends after the last day: not a month
    ranks = {date(2020, 1, 31): ["a"], date(2020, 2, 3): ["b", "c"], date(2020, 3, 2): ["z"]}
    assert rl_holdings.in_force(ranks, sessions, ends) == {"2020-01": ["a"], "2020-02": ["b", "c"]}
    assert rl_holdings.in_force({date(2020, 2, 3): ["b"]}, sessions, ends) == {"2020-02": ["b"]}   # nothing yet in January


def test_the_holdings_inputs_read_only_that_months_lists_and_weigh_a_blends_parts():
    months = ["2020-01", "2020-02"]
    table = np.tile(np.linspace(0, 1, 6)[:, None], (1, len(rl_holdings.PLACES)))      # stock n's place n/5
    table[5] = np.nan                                                                # a stock without values
    stock_places = {month: table for month in months}
    industries = ["x", "x", "y", "y", "z", "z"]
    parts = [{"2020-01": [0, 1], "2020-02": [1, 2]}, {"2020-01": [4], "2020-02": []}, {"2020-02": [3, 5]}]
    rules = [[(0, 1.0)], [(1, 1.0)], [(0, 0.25), (1, 0.75)], [(2, 1.0)]]
    held = rl_holdings.held_features(parts, rules, months, stock_places, industries)
    width = len(rl_holdings.PLACES)
    assert held.shape == (2, 4, len(rl_holdings.HELD))
    first = held[0]
    assert np.isclose(first[0, 0], 0.1) and first[0, width] == 2 and first[0, width + 1] == 1.0       # both of x
    assert np.isnan(first[0, width + 2])                                              # no month before the first
    assert np.isclose(first[0, width + 3], 0.5)                       # stocks 0 and 1: rules 0 and 2 of the four
    assert np.isclose(first[2, 0], 0.25 * 0.1 + 0.75 * 0.8) and np.isclose(first[2, width], 0.25 * 2 + 0.75 * 1)
    assert first[3, width] == 0 and np.isnan(first[3, 0])             # nothing decided yet: count 0, the rest none
    second = held[1]
    assert np.isclose(second[0, width + 2], 0.5)                      # stock 1 of [1, 2] was held the month before
    assert second[1, width] == 0 and np.isnan(second[1, 0])
    assert np.isclose(second[2, 0], second[0, 0])                     # the blend's empty part does not weigh
    assert np.isclose(second[3, 0], 0.6) and second[3, width] == 2    # the stock without values is not averaged
    again = rl_holdings.held_features([{**part, "2020-02": [0]} for part in parts], rules, months, stock_places,
                                      industries)
    assert np.array_equal(again[0], held[0], equal_nan=True)          # January does not move when February does


def _data(rules=120, months=100, seed=3):
    rng = np.random.default_rng(seed)
    base = rng.normal(0.01, 0.04, months)
    skill = np.linspace(-0.01, 0.01, rules)
    returns = base[None, :] + skill[:, None] + rng.normal(0, 0.01, (rules, months))
    return {"months": [f"{2015 + m // 12}-{1 + m % 12:02d}" for m in range(months)], "base": base, "returns": returns,
            "trials": list(range(rules)), "names": [str(n) for n in range(rules)],
            "families": [f"f{n % 6}" for n in range(rules)], "guard": np.zeros(rules), "blend": np.zeros(rules)}


def test_the_two_robots_and_the_fixed_ways_are_followed_and_judged_as_the_tenth_version(monkeypatch):
    monkeypatch.setattr(rl_select, "PICK", 5)
    monkeypatch.setattr(rl_holdings, "KEEP", 15)
    data = _data()
    rng = np.random.default_rng(1)
    held = rng.normal(size=(100, 120, len(rl_holdings.HELD)))
    held[:, :, 0] = np.linspace(0, 1, 120)[None, :]                   # one input knows which rules are better
    start = data["months"].index(rl_select.FIRST) - 1
    runs, found = rl_holdings.follows(data, held, start)
    assert set(found) == {"robot", "robot_plain", "last12", "to_date", "all"}
    assert len(runs["robot"]["gains"]) == 100 - start - 1 and len(runs["robot"]["held"]) == 5
    judgment = rl_select.judge(found)
    assert judgment["best_fixed"] in ("last12", "to_date", "all") and judgment["years"] >= 1
    related = rl_holdings.relations(data, held, start)
    assert related["held_momentum_12_1"] > 0.2 and abs(related["held_count"]) < 0.1
    later = {**data, "returns": data["returns"].copy()}
    later["returns"][:, -1] += 0.3                                    # the last month is no robot's input
    assert rl_holdings.follows(later, held, start)[0]["robot"]["held"] == runs["robot"]["held"]
