"""2026-10-14: factor momentum — which factors to use, decided by their realised record (research/factor_timing.py)."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_platform.research import factor_timing
from quant_platform.research.daily import DailyRule, FactorPanel
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def market(days, stocks=40, seed=4, length=2000):
    """The same paths whatever ``days`` is cut to: the noise is drawn for ``length`` sessions, then cut."""
    noise = np.random.default_rng(seed).normal(0, 0.012, (stocks, length))[:, :len(days)]
    closes = {}
    for number in range(stocks):
        moves = (number - stocks / 2) * 0.0001 + noise[number]
        closes[f"{1101 + number}.TW"] = dict(zip(days, np.maximum(30 * np.cumprod(1 + moves), 11.0)))
    closes["0050.TW"] = {day: 50.0 for day in days}
    data = LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 5e9 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})
    return FactorPanel(Panel(data))


def test_a_record_is_the_same_whether_or_not_later_data_exists(monkeypatch):
    """No look-ahead: cut the data at a session and its record does not change."""
    monkeypatch.setattr(factor_timing, "WINDOW", 60)
    monkeypatch.setattr(factor_timing, "LEAST", 40)
    days = weekdays(date(2015, 1, 1), date(2017, 6, 30))
    names = ["momentum_3", "reversal_1", "low_volatility_60"]
    full, _ = factor_timing.records(market(days), names)
    cut_at = len(days) - 150
    cut, _ = factor_timing.records(market(days[:cut_at]), names)
    assert full.iloc[cut_at - 1].notna().all()
    assert np.allclose(full.iloc[cut_at - 1].to_numpy(), cut.iloc[cut_at - 1].to_numpy())
    assert full.iloc[: factor_timing.HORIZON + 40 - 1].isna().all().all()            # nothing realised yet


def test_each_session_uses_its_best_positive_records(monkeypatch):
    days = weekdays(date(2016, 1, 4), date(2016, 1, 8))                               # 5 sessions
    fp = market(days, stocks=3)
    record = pd.DataFrame({"a": [0.02, 0.02, -0.01, np.nan, -0.01], "b": [0.01, 0.03, 0.01, 0.01, -0.02]})
    ranks = {"a": np.tile(np.array([[1.0], [0.5], [0.0]], dtype=np.float32), (1, 5)),
             "b": np.tile(np.array([[0.0], [0.5], [1.0]], dtype=np.float32), (1, 5))}
    monkeypatch.setattr(factor_timing, "records", lambda _fp, _names=None: (record, ranks))
    monkeypatch.setattr(factor_timing, "TOP", 1)
    scores = factor_timing.scores(fp)
    assert list(scores[:4, 0]) == [1.0, 0.0, 0.0, 0.0] and np.isnan(scores[4, 0])   # day 4: nothing positive
    monkeypatch.setattr(factor_timing, "TOP", 2)
    assert factor_timing.scores(fp)[0, 0] == pytest.approx(0.5)                     # both positive: averaged


def test_the_meta_factor_is_a_rule_factor_with_one_untuned_rule():
    from quant_platform.research import daily

    assert "factor_momentum" in daily.RULE_FACTORS and "factor_momentum" not in daily.FACTOR_LABELS
    rules = daily.factor_momentum_batch()
    assert len(rules) == 1 and rules[0].factors == {"factor_momentum": 1.0} and rules[0].hold == "trend"
    assert not set(factor_timing.LEFT_OUT) & set(factor_timing.base_factors())
    assert isinstance(rules[0], DailyRule)
