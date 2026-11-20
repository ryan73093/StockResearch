"""2026-11-20 (使用者：繼續，還有什麼): the forced short-cover window before an ex-dividend date, from what was known."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
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


class Margin:
    """The margin table's short balance (lots), already as known on each session."""

    def __init__(self, fp, balances):
        self.frame = pd.DataFrame({symbol: balances.get(symbol, 0.0) for symbol in fp.symbols}, index=fp.sessions)

    def available(self):
        return True

    def _daily(self, dataset, field):
        assert (dataset, field) == ("TaiwanStockMarginPurchaseShortSale", "ShortSaleTodayBalance")
        return self.frame


def panel(days, plans, balances, chips=True):
    closes = {symbol: {day: 50.0 for day in days} for symbol in ("1101.TW", "1102.TW", "0050.TW")}
    data = LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 5e9 for day in days} for symbol in closes},   # 100,000 lots a day
                      factors={}, predictions={}, notes={"dividend_plans": plans})
    fp = FactorPanel(Panel(data))
    fp.chips = Margin(fp, balances) if chips else None
    return fp


def test_short_cover_ahead_is_days_to_cover_inside_the_window_only():
    days = weekdays(date(2024, 5, 1), date(2024, 8, 30))
    announced, ex_day = days[10], days[40]                     # 2024-05-15, 2024-06-26
    fp = panel(days, {"1101.TW": [(announced, ex_day, 2.0)]}, {"1101.TW": 200_000.0, "1102.TW": 300_000.0})
    values = fp.matrix("short_cover_ahead")[fp.row["1101.TW"]]
    inside = [index for index, day in enumerate(days)
              if daily.SHORT_COVER_TO < (ex_day - day).days <= daily.SHORT_COVER_FROM]
    assert inside and values[inside] == pytest.approx(np.full(len(inside), 2.0))        # 200,000 / 100,000 lots
    assert values[inside[0] - 1] == 0.0 and values[inside[-1] + 1] == 0.0
    assert values[40] == 0.0                                                             # not on the ex-date
    assert fp.matrix("short_cover_ahead")[fp.row["1102.TW"]].max() == 0.0               # shorts but no plan: 0


def test_short_cover_ahead_waits_for_the_announcement_and_needs_the_margin_table():
    days = weekdays(date(2024, 5, 1), date(2024, 8, 30))
    ex_day = days[40]
    fp = panel(days, {"1101.TW": [(days[30], ex_day, 2.0)]}, {"1101.TW": 200_000.0})    # announced inside the window
    values = fp.matrix("short_cover_ahead")[fp.row["1101.TW"]]
    assert values[30] == 0.0 and values[31] == pytest.approx(2.0)                        # the session after
    bare = panel(days, {"1101.TW": [(days[10], ex_day, 2.0)]}, {}, chips=False)
    assert np.isnan(bare.matrix("short_cover_ahead")).all()


def test_short_cover_ahead_is_a_rule_factor_held_out_of_the_searches_until_judged():
    from quant_platform.research import categories, model, scan

    assert "short_cover_ahead" in daily.RULE_FACTORS and "short_cover_ahead" not in daily.FACTOR_LABELS
    assert "short_cover_ahead" in scan.LEFT_OUT and "short_cover_ahead" in scan.SHORT
    assert "short_cover_ahead" not in model.features()
    assert categories.FACTOR_FAMILY["short_cover_ahead"] == "籌碼"
