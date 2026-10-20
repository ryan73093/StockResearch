"""2026-10-20: the holding styles side by side (research/holding.py)."""
from quant_platform.research.daily import DailyRule, holding_key
from quant_platform.research.holding import style_of


def test_the_styles_and_the_strategy_they_share():
    base = DailyRule(name="a", factors={"trend_200": 1.0}, top=20)
    fixed = base.canonical()
    trend = DailyRule(name="b", factors={"trend_200": 1.0}, top=20, hold="trend", min_hold=0).canonical()
    free = DailyRule(name="c", factors={"trend_200": 1.0}, top=20, min_hold=0).canonical()
    assert [style_of(spec) for spec in (fixed, trend, free)] == ["固定持有 20 天", "月線上續抱", "不設最短持有"]
    assert holding_key(fixed) == holding_key(trend) == holding_key(free)              # the same strategy
    assert holding_key(DailyRule(name="a", factors={"trend_200": 1.0}, top=30).canonical()) != holding_key(fixed)
    assert style_of(DailyRule(name="d", factors={"trend_200": 1.0}, min_hold=10).canonical()) is None
