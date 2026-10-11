"""2026-10-11: the truncation check (research/leakcheck.py)."""

from datetime import date, timedelta

import numpy as np

from quant_platform.research import leakcheck
from quant_platform.research.daily import FactorPanel
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel


def market():
    rng = np.random.default_rng(1)
    days, day = [], date(2022, 1, 3)
    while day <= date(2023, 12, 29):
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    closes, opens = {}, {}
    for number in range(8):
        series = 20 * np.cumprod(1 + rng.normal(0.0003, 0.012, len(days)))
        closes[f"{1101 + number}.TW"] = dict(zip(days, np.maximum(series, 10.5)))
        opens[f"{1101 + number}.TW"] = dict(zip(days, np.maximum(series, 10.5) * 0.995))
    closes["0050.TW"] = {day: 100.0 for day in days}
    return LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 50_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={}, opens=opens)


def test_price_factors_do_not_peek_and_a_peeking_one_is_caught(monkeypatch):
    data = market()
    full = FactorPanel(Panel(data))
    day = data.sessions[400]
    cut = FactorPanel(Panel(leakcheck.truncated_data(data, day)))
    for factor in ("momentum_3", "trend_200", "rsi_14", "macd_hist", "fip_12", "imom_12", "resid_mom_12", "high_52w"):
        found = leakcheck.compare(full, cut, factor, day)
        assert found["checked"] > 0 and found["differ"] == 0, factor

    original = FactorPanel._compute

    def peeking(self, factor):
        if factor == "reversal_1":                                       # tomorrow's price: a leak
            return (self._prices.shift(-1) / self._prices - 1).to_numpy()
        return original(self, factor)

    monkeypatch.setattr(FactorPanel, "_compute", peeking)
    full, cut = FactorPanel(Panel(data)), FactorPanel(Panel(leakcheck.truncated_data(data, day)))
    assert leakcheck.compare(full, cut, "reversal_1", day)["differ"] > 0
