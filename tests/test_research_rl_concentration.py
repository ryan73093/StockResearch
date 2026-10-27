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
