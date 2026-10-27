"""2026-10-28 (research_method §7「RL 第四版」): the concentration bandit learns from the years before each year."""
import numpy as np
import pandas as pd

from quant_platform.research import rl_concentration


def test_the_bandit_learns_which_arm_the_state_favours_and_is_judged_on_the_late_years():
    rng = np.random.default_rng(1)
    weeks = 52 * 10
    dates = pd.bdate_range("2016-01-04", periods=weeks * 5)[::5][:weeks]
    calm = rng.random(weeks) < 0.5
    frame = pd.DataFrame({"date": [day.date().isoformat() for day in dates],
                          "taiex_trend": np.where(calm, 0.1, -0.1) + rng.normal(0, 0.01, weeks),
                          "taiex_vol": rng.random(weeks), "spread": rng.random(weeks), "lead": rng.random(weeks),
                          "breadth": rng.random(weeks)})
    # calm weeks: the top 10 earn most; the others: the top 30
    frame["reward_10"] = np.where(calm, 0.02, -0.02) + rng.normal(0, 0.002, weeks)
    frame["reward_20"] = 0.0 + rng.normal(0, 0.002, weeks)
    frame["reward_30"] = np.where(calm, -0.01, 0.01) + rng.normal(0, 0.002, weeks)
    frame["year"] = frame["date"].str[:4].astype(int)
    choices = rl_concentration.walk_forward(frame)
    assert choices["year"].min() == 2018                                   # two years learned before the first test
    late = choices[choices["date"] >= "2021-01-01"]
    right = np.where(frame.set_index("date").loc[late["date"], "taiex_trend"] > 0, 10, 30)
    assert (late["choice"].to_numpy() == right).mean() > 0.95
    found = rl_concentration.summary(choices)
    assert found["passed"] and found["late"]["bandit"] > found["late"]["fixed_10"] > found["late"]["fixed_20"] - 0.5


def test_the_online_bandit_learns_within_the_run_and_is_judged_on_the_account():
    """2026-10-28 (RL 第四版第二版): LinUCB updated every week; a fall is penalised; the account's multiple and
    deepest fall decide."""
    rng = np.random.default_rng(2)
    weeks = 52 * 9
    dates = pd.bdate_range("2017-01-02", periods=weeks * 5)[::5][:weeks]
    calm = rng.random(weeks) < 0.6
    frame = pd.DataFrame({"date": [day.date().isoformat() for day in dates],
                          "taiex_trend": np.where(calm, 0.1, -0.1), "taiex_vol": rng.random(weeks),
                          "spread": rng.random(weeks), "lead": rng.random(weeks), "breadth": rng.random(weeks)})
    # calm weeks: 10 stocks earn more; stormy weeks: 10 stocks fall hard, 30 stocks hold
    frame["ret_10"] = np.where(calm, 0.02, -0.08)
    frame["ret_20"] = np.where(calm, 0.012, -0.03)
    frame["ret_30"] = np.where(calm, 0.008, 0.0)
    for arm in (10, 20, 30):
        frame[f"reward_{arm}"] = frame[f"ret_{arm}"]
    frame["year"] = frame["date"].str[:4].astype(int)
    choices = rl_concentration.linucb(frame)
    late = choices[choices["date"] >= "2021-01-01"]
    stormy = frame.set_index("date").loc[late["date"], "taiex_trend"].to_numpy() < 0
    assert (late["choice"].to_numpy()[stormy] == 30).mean() > 0.8 and (late["choice"].to_numpy()[~stormy] == 10).mean() > 0.8
    found = rl_concentration.online_summary(choices)
    assert found["passed"] and found["late"]["bandit"]["max_drawdown"] > found["late"]["fixed_10"]["max_drawdown"]
    assert rl_concentration.account(pd.Series([0.1, -0.5, 0.2])) == {"multiple": 0.66, "max_drawdown": -0.5}
