"""2026-10-14: the technical robot 2.0 (research/rl_timing.py) — always invested, choosing factor weights."""
import json
from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import rl_timing
from quant_platform.research.daily import FactorPanel
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


class Stub:
    """A two-factor, four-stock timeline with known forward returns."""

    names = ["a", "b"]
    percentiles = np.array([[[1.0, 0.8, 0.2, 0.0], [0.0, 0.2, 0.8, 1.0]]] * 2, dtype=np.float32)   # decision × factor × stock
    forward = np.array([[0.10, 0.06, -0.02, -0.04], [0.10, 0.06, -0.02, -0.04]])
    bench = np.array([0.01, 0.01])


def test_the_reward_is_the_account_against_0050_less_the_swaps(monkeypatch):
    monkeypatch.setattr(rl_timing, "TOP", 2)
    stub = Stub()
    stub.picks = rl_timing.Timeline.picks.__get__(stub)
    stub.rewards = rl_timing.Timeline.rewards.__get__(stub)
    on_a = np.array([[[1.0, 0.0]], [[1.0, 0.0]]])                                # decision × rollout × factor
    rewards = stub.rewards(on_a, [0, 1])
    first = np.log1p(0.08 - rl_timing.COST_SWAP) - np.log1p(0.01)               # buys both: two swaps of two
    assert rewards[0, 0] == pytest.approx(first) and rewards[1, 0] == pytest.approx(np.log1p(0.08) - np.log1p(0.01))
    on_b_then_a = np.array([[[0.0, 1.0]], [[1.0, 0.0]]])
    rewards = stub.rewards(on_b_then_a, [0, 1])
    assert rewards[0, 0] == pytest.approx(np.log1p(-0.03 - rl_timing.COST_SWAP) - np.log1p(0.01))
    assert rewards[1, 0] == pytest.approx(np.log1p(0.08 - rl_timing.COST_SWAP) - np.log1p(0.01))   # swapped both


def market(days, stocks=30, seed=1):
    """Stocks drift by their 3-month momentum rank: momentum pays, the other technical factors are noise."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 0.01, (stocks, len(days)))
    drift = np.linspace(-0.0008, 0.0008, stocks)
    closes = {}
    for number in range(stocks):
        closes[f"{1101 + number}.TW"] = dict(zip(days, np.maximum(30 * np.cumprod(1 + drift[number] + noise[number]), 11.0)))
    closes["0050.TW"] = {day: 50.0 for day in days}
    data = LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 5e9 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})
    return data, FactorPanel(Panel(data))


def test_the_robot_learns_to_lean_on_the_factor_that_pays(monkeypatch, tmp_path):
    monkeypatch.setattr(rl_timing, "EPOCHS", 60)
    monkeypatch.setattr(rl_timing, "ROLLOUTS", 32)
    monkeypatch.setattr(rl_timing, "SEEDS", (0,))
    monkeypatch.setattr(rl_timing, "TOP", 5)
    monkeypatch.setattr(rl_timing, "FIRST_TEST_YEAR", 2019)
    from quant_platform.research import factor_timing

    monkeypatch.setattr(factor_timing, "WINDOW", 120)
    monkeypatch.setattr(factor_timing, "LEAST", 60)
    days = weekdays(date(2015, 1, 1), date(2019, 12, 31))
    data, fp = market(days)
    meta = rl_timing.walk_forward(fp, rl_timing.benchmark_units(data, fp.sessions), tmp_path)
    assert meta["weights"] and set(meta["yearly"]) == {"2019"}
    heaviest = dict(meta["yearly"]["2019"]["heaviest"])
    trend_like = {"momentum_3", "momentum_6", "trend_200", "high_52w", "ma_cross_20_60", "momentum_12_1", "breakout_55",
                  "fip_12", "imom_12", "resid_mom_12", "macd_hist", "rsi_14", "kd_k", "bollinger_b"}
    assert set(heaviest) & trend_like                                            # it leans on what pays
    first_decision = min(date.fromisoformat(day) for day in meta["weights"])
    assert first_decision.year == 2019                                           # weights only for test years
    scores = rl_timing.scores(fp, tmp_path)
    before = fp.index[first_decision] - 1
    assert np.isnan(scores[before]).all() and np.isfinite(scores[fp.index[first_decision]]).any()


def test_the_scores_use_the_latest_decision_only(tmp_path):
    days = weekdays(date(2016, 1, 4), date(2017, 6, 30))
    _data, fp = market(days, stocks=6)
    names = rl_timing.factor_names()
    equal = [1 / len(names)] * len(names)
    tilted = [0.0] * len(names)
    tilted[names.index("momentum_3")] = 1.0
    decided = {days[300].isoformat(): equal, days[320].isoformat(): tilted}
    (tmp_path / "meta.json").write_text(json.dumps({"weights": decided}), encoding="utf-8")
    scores = rl_timing.scores(fp, tmp_path)
    alike = rl_timing.scores(fp, tmp_path, "equal")
    assert np.isnan(scores[299]).all()
    assert np.allclose(np.nan_to_num(scores[310]), np.nan_to_num(alike[310]), atol=0.05)
    momentum = fp.matrix("momentum_3")[:, 330]
    row = int(np.nanargmax(momentum))
    assert scores[330, row] == pytest.approx(np.nanmax(scores[330]))


def test_the_robot_21_sees_every_full_history_factor_and_the_model_scores():
    from quant_platform.research import daily

    names = rl_timing.factor_names(rl_timing.ALL_VERSION)
    assert set(daily.MODEL_FACTORS) <= set(names) and set(rl_timing.factor_names()) <= set(names)
    assert not {"revenue_yoy", "revenue_yoy_3m", "revenue_accel", "market_cap"} & set(names)
    assert not (set(daily.TIME_FACTORS) | set(daily.SHORT_FACTORS) | set(daily.ALERT_FACTORS)) & set(names)
    rules = daily.robot21_batch()
    assert [rule.factors for rule in rules] == [{"rl_all": 1.0}, {"all_equal": 1.0}]
