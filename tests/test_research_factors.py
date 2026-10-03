from datetime import date, timedelta

from quant_platform.research.factors import monthly_factor_table, summarize, verdict
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel


def weekdays(start, count):
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def test_a_factor_that_predicts_returns_has_a_high_ic_and_its_opposite_a_low_one():
    days = weekdays(date(2014, 1, 1), 760)           # to late 2016: inside the development period
    closes = {f"{1101 + i}.TW": {day: 20 * (1 + (i - 30) * 0.00003) ** n for n, day in enumerate(days)} for i in range(60)}
    closes["0050.TW"] = {day: 100.0 for day in days}
    data = LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 50_000_000.0 for day in days} for symbol in closes},
                      factors={}, predictions={})
    panel = Panel(data)
    rows = monthly_factor_table(panel, ["momentum_6", "reversal_1"], date(2015, 2, 1), date(2016, 11, 30))
    momentum = [row for row in rows if row["factor"] == "momentum_6"]
    reversal = [row for row in rows if row["factor"] == "reversal_1"]
    # steady growth: past winners are next month's winners, the 1-month reversal ranks them the other way
    assert len(momentum) >= 18 and min(row["ic"] for row in momentum) > 0.99 and all(row["spread"] > 0 for row in momentum)
    assert max(row["ic"] for row in reversal) < -0.99
    summary = summarize(rows, ["momentum_6", "reversal_1"])
    dev = summary["momentum_6"]["periods"]["development"]
    assert dev["positive"] == 1.0 and dev["spread_year"] > 0 and "validation" not in summary["momentum_6"]["periods"]
    assert summary["momentum_6"]["label"] == "6 個月動能" and set(summary["momentum_6"]["spread_by_year"]) == {"2015", "2016"}


def test_verdicts():
    def periods(*tops):
        return {key: {"top_excess_year": top} for key, top in zip(("development", "validation", "final"), tops)}

    # the top fifth against the average eligible stock, a year: what a long-only rule buys
    cases = [periods(0.07, 0.05, 0.09), periods(0.05, -0.04, 0.03), periods(-0.03, -0.05, -0.02),
             periods(0.01, -0.01, 0.03), periods(0.05, 0.05)]
    assert [verdict(item) for item in cases] == ["強", "不穩", "反向", "弱", "弱"]
