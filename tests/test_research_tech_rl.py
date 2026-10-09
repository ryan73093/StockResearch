"""2026-10-10: the technical-analysis RL robot (research/tech_rl.py) and gbm-1.4.0's features."""

from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import model, tech_rl
from quant_platform.research.daily import DailyRule, FactorPanel, daily_rankings
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel

SMALL = {"iterations": 25, "envs": 128, "episode": 30, "minibatch": 1024, "threads": 1}


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def trending(stocks=30, seed=5, end=date(2017, 6, 30), changed_after=None):
    """Half the stocks drift up for a stretch and then down, the other half the other way round: the
    recent trend tells which way a stock goes next — a purely technical edge."""
    rng = np.random.default_rng(seed)
    days = weekdays(date(2013, 1, 1), end)
    closes = {}
    for number in range(stocks):
        phase = (np.arange(len(days)) // 120 + number) % 2
        drift = np.where(phase == 0, 0.003, -0.003)
        series = 20 * np.cumprod(1 + drift + rng.normal(0, 0.01, len(days)))
        if changed_after is not None:
            series = np.where(np.array(days) > changed_after, series * 3, series)
        closes[f"{1101 + number}.TW"] = dict(zip(days, np.maximum(series, 10.5)))
    closes["0050.TW"] = {day: 100.0 for day in days}
    data = LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 50_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})
    return data, FactorPanel(Panel(data))


def test_the_robot_sees_only_price_and_volume():
    from quant_platform.research.chips import CHIP_FACTORS
    from quant_platform.research.daily import MODEL_FACTORS, STATEMENT_FACTORS

    assert not set(tech_rl.TECH_FEATURES) & (set(CHIP_FACTORS) | set(STATEMENT_FACTORS) | set(MODEL_FACTORS))
    assert tech_rl.inputs() == len(tech_rl.TECH_FEATURES) + 2 + 4


def test_the_robot_learns_to_ride_a_technical_trend():
    _data, fp = trending()
    board = tech_rl.Board(fp)
    low = next(index for index in range(board.sessions) if board.eligible[index].any())
    high = fp.index[next(day for day in fp.sessions if day.year == 2017)]
    policy, curve = tech_rl.train_policy(board, low, high, 0, {**SMALL, "iterations": 40})
    assert np.mean(curve[-5:]) > np.mean(curve[:5])                  # it earns more as it learns
    check = tech_rl.simulate_agent(board, [policy], high, board.sessions - 1)
    assert check["excess_per_in_session"] > 0                        # out of sample it is in the right stocks


def test_training_never_sees_the_year_it_trades(tmp_path):
    _data, fp = trending()
    _later, changed = trending(changed_after=date(2016, 12, 30))       # only 2017 differs
    first = tech_rl.train(fp, tmp_path / "a", years=[2017], config=SMALL, seeds=(0,))
    second = tech_rl.train(changed, tmp_path / "b", years=[2017], config=SMALL, seeds=(0,))
    import torch

    left = torch.load(tmp_path / "a" / "2017-seed0.pt", weights_only=True)
    right = torch.load(tmp_path / "b" / "2017-seed0.pt", weights_only=True)
    assert all(torch.equal(left[key], right[key]) for key in left)
    assert first["years"]["2017"]["trained_until"] < "2017-01-01"
    assert second["years"]["2017"]["trained_until"] == first["years"]["2017"]["trained_until"]


def test_in_a_rule_the_robot_buys_only_what_it_wants_and_sells_by_itself(tmp_path):
    _data, fp = trending()
    tech_rl.train(fp, tmp_path / tech_rl.TECH_VERSION, years=[2017], config=SMALL, seeds=(0,))
    fp.models = tmp_path
    score = fp.matrix("rl_tech")
    first = fp.index[next(day for day in fp.sessions if day.year == 2017)]
    assert np.isfinite(score[:, first:]).any() and not np.isfinite(score[:, :first]).any()
    rule = DailyRule(name="x", factors={"rl_tech": 1.0}, exit_model="tech1", keep=5, min_hold=250, top=5)
    days = [day for day in fp.sessions if day.year == 2017]
    ranks = daily_rankings(fp, rule, days[0], days[-1])
    previous: set[str] = set()
    sold = 0
    for day in days:
        now = set(ranks[day])
        for symbol in now - previous:                                 # a new buy: the robot wanted it
            assert score[fp.row[symbol], fp.index[day]] >= 0.5
        sold += len(previous - now)
        previous = now
    assert sold > 0                                                   # it also decides to get out
    assert "tech" in __import__("quant_platform.research.daily", fromlist=["x"]).input_digests(
        tmp_path / "history", ["rl_tech"], True)


def test_the_new_model_leaves_out_monthly_revenue():
    names = model.version_features("gbm-1.4.0")
    assert not set(model.MONTHLY_REVENUE) & set(names) and {"fip_12", "imom_12", "resid_mom_12"} <= set(names)
    assert len(names) == 36 and model.VERSIONS["gbm-1.4.0"]["label"] == "excess"
    assert model.version_features("gbm-1.2.0") == model.features()


def test_the_weekly_robot_decides_every_five_sessions(tmp_path):
    _data, fp = trending()
    meta = tech_rl.train(fp, tmp_path / "tech-rl-1.1.0", years=[2017], seeds=(0,), version="tech-rl-1.1.0",
                         config={"iterations": 30, "envs": 128, "minibatch": 1024, "threads": 1})
    assert meta["version"] == "tech-rl-1.1.0" and meta["config"]["step"] == 5 and meta["config"]["episode"] == 26
    assert meta["years"]["2017"]["excess_per_in_session"] > 0           # the planted trend, five sessions at a time
    fp.models = tmp_path
    rule = DailyRule(name="x", factors={"rl_tech_weekly": 1.0}, exit_model="tech2", keep=5, min_hold=250, top=5,
                     check="weekly")
    days = [day for day in fp.sessions if day.year == 2017]
    ranks = daily_rankings(fp, rule, days[0], days[-1])
    assert ranks and all(len(names) <= 5 for names in ranks.values())
