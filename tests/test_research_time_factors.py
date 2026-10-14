"""2026-10-14 (使用者：因子裡面有考慮到時間的部分嗎？): the calendar and event-time factors, each from what was known."""
from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import daily
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


def panel(days, closes, notes=None):
    closes = {**closes, "0050.TW": {day: 50.0 for day in days}}
    data = LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 5e9 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={}, notes=notes or {})
    return FactorPanel(Panel(data))


def test_season_month_uses_earlier_years_only():
    days = weekdays(date(2012, 12, 3), date(2016, 12, 30))      # December 2012: January 2013 has a return
    level, prices = 100.0, {}
    for day in days:                                  # flat, except every January it climbs 1% a session
        if day.month == 1:
            level *= 1.01
        prices[day] = level
    fp = panel(days, {"1101.TW": prices, "1102.TW": {day: 100.0 for day in days}})
    values = fp.matrix("season_month")[fp.row["1101.TW"]]
    jan_2014 = values[fp.index[next(day for day in days if day.year == 2014 and day.month == 1)]]
    jan_2015 = values[fp.index[next(day for day in days if day.year == 2015 and day.month == 1)]]
    feb_2015 = values[fp.index[next(day for day in days if day.year == 2015 and day.month == 2)]]
    assert np.isnan(jan_2014)                         # one earlier January (2013) is not enough
    assert jan_2015 > 0.2 and abs(feb_2015) < 1e-6     # Januaries 2013 and 2014; Februaries are flat
    flat = fp.matrix("season_month")[fp.row["1102.TW"]]
    assert abs(flat[fp.index[days[-1]]]) < 1e-6


def test_dividend_ahead_starts_the_session_after_the_announcement_and_ends_before_the_ex_date():
    days = weekdays(date(2024, 5, 1), date(2024, 8, 30))
    announced, ex_day = days[10], days[40]
    notes = {"dividend_plans": {"1101.TW": [(announced, ex_day, 2.0)]}}
    fp = panel(days, {"1101.TW": {day: 50.0 for day in days}, "1102.TW": {day: 50.0 for day in days}}, notes)
    values = fp.matrix("dividend_ahead")[fp.row["1101.TW"]]
    assert values[40 - daily.DIVIDEND_AHEAD - 1] == 0.0 and values[40 - daily.DIVIDEND_AHEAD] == pytest.approx(0.04)
    assert values[39] == pytest.approx(0.04) and values[40] == 0.0
    late = {"dividend_plans": {"1101.TW": [(days[35], ex_day, 2.0)]}}    # announced 5 sessions before
    values = panel(days, {"1101.TW": {day: 50.0 for day in days}}, late).matrix("dividend_ahead")[0]
    assert values[35] == 0.0 and values[36] == pytest.approx(0.04)
    assert fp.matrix("dividend_ahead")[fp.row["1102.TW"]].max() == 0.0     # nothing announced: 0, still ranked


def test_the_quarter_end_window_comes_from_the_calendar():
    days = [date(2024, 3, 1), date(2024, 3, 4), date(2024, 3, 29), date(2024, 4, 1), date(2024, 4, 15),
            date(2024, 5, 31), date(2024, 12, 31)]
    assert list(daily.quarter_weights(days)) == [0.0, 1.0, 1.0, -1.0, 0.0, 0.0, 1.0]


def test_time_factors_are_rule_factors_but_not_model_features():
    from quant_platform.research import model, scan

    assert set(daily.TIME_FACTORS) <= set(daily.FACTOR_LABELS) and set(daily.TIME_FACTORS) <= set(scan.signals())
    assert not set(daily.TIME_FACTORS) & set(model.features())
    assert all(name in scan.SHORT for name in daily.TIME_FACTORS)
