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
