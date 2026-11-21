"""The US overheating condition and the share held that reads it (空頭防護第十四版, 2026-11-22)."""
from datetime import date, timedelta

import numpy as np

from quant_platform.research import daily, market_signals, pool, stress, us_factors
from quant_platform.research.costs import CostModel
from quant_platform.research.daily import DailyRule, FactorPanel
from quant_platform.research.stock_forward import StockForwardTracker
from quant_platform.research.stock_rules import Panel
from tests.test_research_daily import market, weekdays

FREE = CostModel(fee_rate=0.0, minimum_fee=0, slippage_bps=0.0)


def test_the_count_reads_only_readings_dated_before_the_session(monkeypatch):
    start = date(2020, 1, 1)
    days = [start + timedelta(days=number) for number in range(400)]
    low = {day: -float(number) for number, day in enumerate(days)}               # always its lowest so far: never high
    high = {**low, **{day: 100.0 for day in days[300:]}}                         # jumps on day 300 and stays
    readings = {f"{market_signals.MACRO}{name}": (high if number < 3 else low)
                for number, name in enumerate(market_signals.HOT_READINGS)}
    monkeypatch.setattr(market_signals, "us_macro", lambda history: readings)
    counts = market_signals.us_hot("unused", [days[299], days[300], days[301], days[399]])
    # the reading dated day 300 is known from the session after it, not on its own date
    assert counts[days[299]] == 0 and counts[days[300]] == 0 and counts[days[301]] == 3 and counts[days[399]] == 3
    assert market_signals.us_hot("unused", [days[0]]) == {}                       # nothing known yet: left out
    assert market_signals.HOT_COUNT == 3 and len(market_signals.HOT_READINGS) == 5


def _panel(below_from: int | None, hot: set[int]):
    days = weekdays(date(2019, 1, 1), date(2021, 12, 31))
    path = [20 * 1.001 ** number for number in range(len(days))]
    data = market(days, {f"{1101 + number}.TW": dict(zip(days, path)) for number in range(4)})
    taiex = np.linspace(100, 300, len(days))
    if below_from is not None:
        taiex = np.concatenate([taiex[:below_from], np.full(len(days) - below_from, 50.0)])
    fp = FactorPanel(Panel(data), market=taiex)
    fp.us_hot_counts = {day: (market_signals.HOT_COUNT if position in hot else market_signals.HOT_COUNT - 1)
                        for position, day in enumerate(days)}
    return days, data, fp


def test_the_half_version_holds_half_below_the_line_or_while_the_us_overheats():
    days, data, fp = _panel(below_from=700, hot=set(range(400, 450)) | set(range(720, 740)))
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=4, vol_scale="us_hot_half", execution="same")
    levels = daily.exposure_schedule(data, fp, rule, FREE)
    assert levels[days[399]] == 1.0 and levels[days[400]] == 0.5 and levels[days[449]] == 0.5 and levels[days[450]] == 1.0
    assert levels[days[710]] == 0.5 and levels[days[730]] == 0.5                  # below the line: half, never less
    assert set(levels.values()) == {0.5, 1.0}
    half = daily.exposure_schedule(data, fp, rule.model_copy(update={"vol_scale": "taiex_half"}), FREE)
    assert all(levels[day] <= half[day] for day in days)                          # never holds more than the half guard
    later = daily.exposure_schedule(data, fp, rule.model_copy(update={"execution": "next"}), FREE)
    assert later[days[400]] == 1.0 and later[days[401]] == 0.5                    # decided at a close, held the next session


def test_the_quarter_version_cuts_again_only_below_the_line():
    days, data, fp = _panel(below_from=700, hot=set(range(400, 450)) | set(range(720, 740)))
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=4, vol_scale="us_hot_quarter", execution="same")
    levels = daily.exposure_schedule(data, fp, rule, FREE)
    assert levels[days[420]] == 1.0                                               # overheated above the line: untouched
    assert levels[days[710]] == 0.5 and levels[days[730]] == 0.25 and levels[days[745]] == 0.5
    assert set(levels.values()) == {0.25, 0.5, 1.0}


def test_the_setting_is_registered_where_a_share_held_is():
    for name in daily.US_HOT_SCALES:
        assert name in daily.MARKET_SCALES and name in pool.SCALE_WORDS
        assert DailyRule(name="x", factors={"momentum_3": 1.0}, vol_scale=name).canonical()["vol_scale"] == name
    assert {"bear_us_hot", "bear_us_hot_quarter"} <= set(stress.SHORT)
    assert len(daily.BATCHES["bear14"].__doc__) > 0 and len(daily.BEAR_US_HOT) == 2
    item = {"kind": "daily", "rule": {"factors": {"momentum_3": 1.0}, "vol_scale": "us_hot_half"}}
    assert "us_hot" in StockForwardTracker.uses(None, item)


def test_the_readings_are_refreshed_for_an_account_that_reads_them(tmp_path, monkeypatch):
    from quant_platform.research.history import us

    asked = {}
    monkeypatch.setattr(us, "fetch_revised", lambda history, pause, only, **extra: asked.setdefault("revised", only) and {})
    monkeypatch.setattr(us, "fetch_daily", lambda history, pause, only, **extra: asked.setdefault("daily", only) and {})
    assert us_factors.refresh_hot(tmp_path)["fetched"] is True
    assert asked == {"revised": us_factors.HOT_REVISED, "daily": us_factors.HOT_DAILY}

    def broken(history, pause, only, **extra):
        raise OSError("no answer")
    monkeypatch.setattr(us, "fetch_revised", broken)
    assert us_factors.refresh_hot(tmp_path)["fetched"] is False                   # the files are kept, the orders go on
