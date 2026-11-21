"""2026-10-22: the market signals' diagnostic by hand."""
from datetime import date, timedelta

from quant_platform.research import market_signals


def test_a_percentile_uses_only_earlier_values():
    values = {date(2020, 1, 1) + timedelta(days=n): float(n % 7) for n in range(300)}
    ranks = market_signals.percentiles(values)
    first = min(ranks)
    assert first == date(2020, 1, 1) + timedelta(days=250)
    earlier = [values[day] for day in sorted(values) if day < first]
    assert ranks[first] == sum(value <= values[first] for value in earlier) / len(earlier)


def test_a_signal_that_falls_before_crashes_is_useful(monkeypatch):
    monkeypatch.setattr(market_signals, "PERIODS", {"a": (date(2016, 1, 1), date(2016, 12, 31)),
                                                    "b": (date(2017, 1, 1), date(2017, 12, 31))})
    days = [date(2015, 1, 1) + timedelta(days=n) for n in range(1200)]
    # 0050 falls 15% over the 60 days after every 100th day and recovers after
    level, index = 100.0, {}
    for number, day in enumerate(days):
        phase = number % 100
        level *= 0.9973 if phase < 60 else 1.004
        index[day] = level
    signal = {day: (0.0 if number % 100 < 5 else 1.0 + (number % 7)) for number, day in enumerate(days)}
    found = market_signals.study(index, {"x": signal})["x"]
    assert set(found["periods"]) == {"a", "b"}
    assert found["periods"]["a"]["worst"] == 1                      # the lowest fifth sees the falls


def test_the_us_series_come_from_the_database_and_are_judged_on_their_own_periods(tmp_path):
    """2026-11-21 (使用者：有把美股的資料也放進來一起跑嗎): the US daily bars the main database holds, read-only — a
    session dated D is a signal dated D (used from the Taiwan session after it); judged on US_PERIODS."""
    import sqlite3
    from datetime import timedelta

    database = tmp_path / "quant_platform.db"
    with sqlite3.connect(database) as connection:
        connection.execute("create table market_bars (symbol text, market text, interval text, event_time text, "
                           "close real, adjusted_close real)")
        day = date(2020, 1, 2)
        for number in range(260):
            for symbol, level in (("^VIX", 20.0 + number % 7), ("^SOX", 1000.0 * 1.001 ** number), ("TWD=X", 30.0)):
                connection.execute("insert into market_bars values (?, 'US', '1d', ?, ?, ?)",
                                   (symbol, f"{day.isoformat()} 21:00:00.000000", level, None))
            day += timedelta(days=1)
    found = market_signals.us_series(database)
    assert all(name.startswith(market_signals.US) for name in found)
    vix = found[f"{market_signals.US}VIX（水準）"]
    assert vix[date(2020, 1, 2)] == 20.0 and len(vix) == 260
    change = found[f"{market_signals.US}VIX（5 日變化）"]
    assert min(change) == date(2020, 1, 7) and change[date(2020, 1, 7)] == (20.0 + 5 % 7) - 20.0
    moved = found[f"{market_signals.US}費城半導體（20 日報酬）"]
    assert abs(moved[date(2020, 1, 22)] - (1.001 ** 20 - 1)) < 1e-9
    above = found[f"{market_signals.US}費城半導體（對 200 日均線）"]
    assert min(above) == date(2020, 1, 2) + timedelta(days=199) and above[min(above)] > 0
    assert f"{market_signals.US}那斯達克（20 日報酬）" not in found          # no bars: left out
    assert market_signals.us_series(tmp_path / "missing.db") == {}
    index = {date(2021, 1, 1) + timedelta(days=n): 100.0 + n for n in range(1500)}
    signal = {day - timedelta(days=1): float(n % 50) for n, day in enumerate(sorted(index))}
    signal.update({date(2020, 1, 1) + timedelta(days=n): float(n % 50) for n in range(366)})
    report = market_signals.study(index, {f"{market_signals.US}試": signal})[f"{market_signals.US}試"]
    assert set(report["periods"]) == set(market_signals.US_PERIODS)
