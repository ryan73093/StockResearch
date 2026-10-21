"""2026-10-21 (使用者：評估長資料跟短歷史的權重): the window study's scores and correlations by hand."""
import numpy as np
import pytest

from quant_platform.research import window_weight


def test_the_scores_by_hand():
    past = np.array([[0.0, 0.0, 0.03], [np.nan, 0.02, 0.04]])
    assert window_weight.score(past, "window:all") == pytest.approx([0.01, 0.03])
    assert window_weight.score(past, "window:12") == pytest.approx([0.01, 0.03])           # shorter history: all of it
    # half-life 1 month: weights 1/4, 1/2, 1 from the oldest; a missing month carries no weight
    assert window_weight.score(past, "decay:1") == pytest.approx([0.03 / 1.75, (0.01 + 0.04) / 1.5])
    long = np.tile(np.array([[0.01], [0.0]]), 70)
    long[1, -60:] = 0.02                                       # the second rule: 0 for 10 months, then 0.02
    assert window_weight.score(long, "blend:0.5") == pytest.approx([0.01, 0.5 * (0.02 * 60 / 70) + 0.5 * 0.02])


def test_a_persistent_ranking_correlates_fully_and_a_short_history_is_as_reliable(monkeypatch):
    months = [f"{2015 + (n + 5) // 12}-{(n + 5) % 12 + 1:02d}" for n in range(130)]     # 2015-06 … 2026-03
    rules = 30
    gaps = np.repeat(np.linspace(-0.01, 0.01, rules)[:, None], len(months), axis=1)
    found = window_weight.study(months, gaps)
    assert found["summary"]["window:all"]["ic"] == pytest.approx(1.0)
    assert found["summary"]["window:60"]["ic"] == pytest.approx(1.0) and found["reliability"] == pytest.approx(1.0)
    assert found["summary"]["window:all"]["top_beat"] == pytest.approx(1.0)
    assert "沒過門檻" in found["verdict_main"] and "短歷史賽道可行" in found["verdict_short"]
    # the ranking turns over every 5 years: the old months mislead, the last ones do not
    flipped = gaps.copy()
    flipped[:, :60] *= -1
    found = window_weight.study(months, flipped)
    late = [row for row in found["rows"] if row["cutoff"] >= "2022-02"]
    assert all(row["ic"] > 0.99 for row in late if row["method"] == "window:24")


def test_the_short_track_grades_a_rule_on_the_history_it_has():
    # 2026-10-21 (使用者：繼續做短歷史賽道): an account from 2021-07 has no separate 2020-10 check, its excess is
    # discounted by the study's ratio and it must win 60% of its 1-year windows
    from quant_platform.research import daily

    base = {"start": "2021-07-01", "full_period_excess": 0.30, "since_2020_excess": -0.5,
            "windows": {"1y": {"count": 40, "win_ratio": 0.65}, "3y": {"count": 15, "win_ratio": 0.8, "median_excess": 0.1}},
            "max_drawdown": -0.30, "benchmark_max_drawdown": -0.28}
    assert daily.short_track(base) and daily.gate(base) == []
    grade, why = daily.tier(base)
    assert grade == "T0 候選" and why.startswith("短歷史賽道（2021-07 起）")
    weaker = {**base, "windows": {**base["windows"], "1y": {"count": 40, "win_ratio": 0.55}}}
    assert daily.gate(weaker) == ["1 年視窗勝率 55% < 60%"] and daily.tier(weaker)[0] == "T2"
    lost = {**base, "full_period_excess": -0.01}
    assert daily.tier(lost)[0] == "T3" and "2021-07 起輸 0050" in daily.gate(lost)[0]
    long = {**base, "start": "2015-06-01"}                      # the main track: the 2020-10 check stays
    assert not daily.short_track(long) and daily.tier(long)[0] == "T2"
    assert daily.gate({**long, "windows": {**base["windows"], "1y": {"count": 40, "win_ratio": 0.55}},
                       "since_2020_excess": 0.1}) == []         # 50% is enough there


def test_a_rule_may_combine_five_factors():
    # 2026-10-21: the second large scan's sets of five stopped at stage 2 (at most 4 factors)
    from quant_platform.research import scan

    rule = scan.to_rule({"factors": ["ml_gbm", "trend_200", "rsi_14", "high_52w", "bollinger_b"], "universe": "all",
                         "weighting": "equal", "check": "weekly"})
    assert len(rule.factors) == 5
