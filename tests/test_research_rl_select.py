"""2026-11-27 RL 第十版 (research/rl_select.py): the robot picks rules month by month."""
import numpy as np

from quant_platform.research import rl_select


def _data(rules=120, months=100, seed=3):
    rng = np.random.default_rng(seed)
    base = rng.normal(0.01, 0.04, months)
    skill = np.linspace(-0.01, 0.01, rules)                       # the last rules are steadily better
    returns = base[None, :] + skill[:, None] + rng.normal(0, 0.01, (rules, months))
    names = [f"2015-{1 + m % 12:02d}" for m in range(months)]
    return {"months": [f"{2015 + m // 12}-{1 + m % 12:02d}" for m in range(months)], "base": base, "returns": returns,
            "trials": list(range(rules)), "names": names * 2, "families": [f"f{n % 6}" for n in range(rules)],
            "guard": np.zeros(rules), "blend": np.zeros(rules)}


def test_a_months_features_do_not_move_when_later_months_change():
    data = _data()
    before = rl_select.features(data, 60)
    data["returns"][:, 61:] += 0.5
    data["base"][61:] -= 0.3
    assert before.shape == (120, len(rl_select.FEATURES)) and np.array_equal(before, rl_select.features(data, 60))


def test_the_holdings_keep_what_is_still_near_the_top_and_cap_a_family(monkeypatch):
    monkeypatch.setattr(rl_select, "PICK", 4)
    monkeypatch.setattr(rl_select, "KEEP", 8)
    monkeypatch.setattr(rl_select, "PER_FAMILY", 2)
    scores = np.arange(12, dtype=float)                           # rule 11 the best
    families = ["a"] * 6 + ["b"] * 3 + ["c"] * 3
    assert rl_select.choose(scores, [], families) == [11, 10, 8, 7]          # two of c, then two of b
    assert rl_select.choose(scores, [5, 2], families)[0] == 5                # 5 is within the top 8 and stays; 2 goes
    assert 2 not in rl_select.choose(scores, [5, 2], families)


def test_the_robot_learns_only_from_months_that_had_ended_and_is_judged_as_written(monkeypatch):
    monkeypatch.setattr(rl_select, "PICK", 5)
    monkeypatch.setattr(rl_select, "KEEP", 15)
    data = _data()
    start = 70
    seen = []
    real = rl_select.features

    def spy(data, at):
        seen.append(at)
        return real(data, at)
    monkeypatch.setattr(rl_select, "features", spy)
    score = rl_select.robot(data, start)
    first = score(start)
    assert max(seen) == start and min(seen) == rl_select.WARM - 1          # samples: months 35..69, valued: month 70
    run = rl_select.follow(data, start, score)
    found = rl_select.summary(run, data["base"][start + 1:])
    assert len(run["months"]) == 100 - start - 1 and run["months"][0] == data["months"][start + 1]
    assert first.shape == (120,) and found["multiple"] > rl_select.summary(
        rl_select.follow(data, start, lambda at: None), data["base"][start + 1:])["multiple"]      # it finds the better rules
    assert run["swapped"][0] == 1.0 and found["months"] == len(run["gains"])
    years = {"2021": 0.1, "2022": 0.1, "2023": 0.1, "2024": 0.1, "2025": 0.1, "2026": 0.1}
    low = {year: 0.05 for year in years}
    fixed = {"multiple": 2.0, "years": low}
    passed = rl_select.judge({"robot": {"multiple": 2.5, "years": {**years, "2020": 0.0}}, "last12": fixed, "to_date": fixed, "all": fixed})
    assert passed["passed"] and passed["years_better"] == 6 and passed["years"] == 6
    behind = rl_select.judge({"robot": {"multiple": 1.9, "years": years}, "last12": fixed, "to_date": fixed, "all": fixed})
    assert not behind["passed"] and not behind["money_not_less"]              # more years but less money: not passed
