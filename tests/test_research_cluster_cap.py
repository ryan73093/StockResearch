"""2026-10-17 (docs/research_dl_exits_review.md R2): at most a share of the picks from one group of stocks that moved
together, the groups re-made every month from the last 120 sessions' return correlation."""
from datetime import date, timedelta

import numpy as np

from quant_platform.research import daily
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


def two_groups():
    """Group A (rows 0-9) moves together and rises fastest; group B (10-19) moves together with another driver."""
    days = weekdays(date(2019, 1, 1), date(2020, 12, 31))
    rng = np.random.default_rng(3)
    driver_a, driver_b = rng.normal(0.002, 0.02, len(days)), rng.normal(0.0005, 0.02, len(days))
    closes = {}
    for n in range(20):
        driver = driver_a if n < 10 else driver_b
        moves = driver + rng.normal(0, 0.002, len(days))
        closes[f"{1101 + n}.TW"] = dict(zip(days, 30 * np.cumprod(1 + moves)))
    closes["0050.TW"] = {day: 50.0 for day in days}
    data = LegacyData(sessions=days, closes=closes, traded_value={s: {d: 5e9 for d in v} for s, v in closes.items()},
                      factors={}, predictions={})
    return days, FactorPanel(Panel(data))


def test_the_groups_follow_the_returns_and_the_cap_spreads_the_picks(monkeypatch):
    monkeypatch.setattr(daily, "CLUSTERS", 2)
    days, fp = two_groups()
    position = fp.index[next(day for day in days if day >= date(2020, 6, 1))]
    group = {symbol: fp.cluster(symbol, position) for symbol in fp.symbols if symbol != "0050.TW"}
    first, second = sorted(fp.symbols)[:10], sorted(fp.symbols)[10:20]
    assert len({group[symbol] for symbol in first}) == 1 and len({group[symbol] for symbol in second}) == 1
    assert group[first[0]] != group[second[0]]
    assert fp.cluster(first[0], position + 1) == group[first[0]]          # the same month, the same groups
    plain = DailyRule(name="m", factors={"momentum_3": 1.0}, top=10, check="weekly")
    capped = plain.model_copy(update={"cluster_cap": 0.3})
    assert capped.cluster_limit == 3 and plain.cluster_limit is None
    assert "cluster_cap" not in plain.canonical() and capped.rule_hash != plain.rule_hash
    start = date(2020, 6, 1)
    picks = daily.daily_rankings(fp, plain, start, date(2020, 6, 30))
    capped_picks = daily.daily_rankings(fp, capped, start, date(2020, 6, 30))
    day = min(capped_picks)
    assert sum(1 for symbol in picks[day] if symbol in first) > 3          # momentum alone buys group A
    counts = {}
    for symbol in capped_picks[day]:
        counts[fp.cluster(symbol, fp.index[day])] = counts.get(fp.cluster(symbol, fp.index[day]), 0) + 1
    assert max(counts.values()) <= 3
