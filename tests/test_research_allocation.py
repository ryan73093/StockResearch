from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quant_platform.research.allocation import adjust_gaps, target_weights
from quant_platform.research.cashflow import ContributionPlan
from quant_platform.research.engine import simulate
from quant_platform.research.market import MarketData
from quant_platform.research.spec import BASELINES, StrategySpec


def weekdays(start, count):
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def market(sessions, closes, unit_ratios=None):
    return MarketData(
        sessions=sessions,
        closes={asset: dict(zip(sessions, values)) for asset, values in closes.items()},
        unit_ratios=unit_ratios or {},
        tax_kind={"0050": "stock_etf", "0056": "stock_etf", "006208": "stock_etf", "00679B": "bond_etf"},
    )


def test_existing_specs_keep_their_hashes():
    # Recorded before the extension; trials, reports and approvals are keyed on these.
    assert {key: spec.spec_hash for key, spec in BASELINES.items()} == {
        "benchmark_dca": "62e7bc5050d5a93865a7312ffef2266b8241955d181e0118fc457c6eaafc40f9",
        "fixed_day_dca": "86e61f274bd8fedae80c30f2091647d20a2b1f82041971791dd99f6c0345af44",
        "ma_value": "2926000f2f7fabc589ad2c95e1c4844d24208db464c577955fdcade16f84ff11",
        "rebalance_80_20": "3544f02c05d9116966284acd843291044271aca09d4ae5973791409aa1a167f4",
    }
    assert "defensive" not in BASELINES["benchmark_dca"].canonical_json()
    assert BASELINES["benchmark_dca"].signals == set() and BASELINES["ma_value"].signals == {"0050"}


@pytest.mark.parametrize(("allocation", "message"), [
    ({"weights": {"0050": 1.0}, "defensive": {"weights": {"00679B": 0.5}}}, "防守配置權重合計必須為 1"),
    ({"weights": {"0050": 1.0}, "rotation": {"candidates": ["0050", "0056"], "top": 2}}, "挑選檔數必須少於候選數"),
    ({"weights": {"0050": 1.0}, "rotation": {"candidates": ["0056", "006208"], "core": {"0050": 1.0}}},
     "核心配置權重合計必須小於 1"),
    ({"weights": {"0050": 1.0}, "rotation": {"candidates": ["0056", "2330"]}}, "不在清單內"),
])
def test_extension_rules_are_validated(allocation, message):
    with pytest.raises(ValueError, match=message):
        StrategySpec.model_validate({"name": "x", "allocation": allocation})


def test_split_adjusted_closes_by_hand():
    days = weekdays(date(2025, 6, 16), 5)
    data = market(days, {"0050": [100.0, 102.0, 26.0, 26.5, 27.0]}, {"0050": {days[2]: 4.0}})

    assert data.trailing("0050", days[4], 5) == [100.0, 102.0, 26.0, 26.5, 27.0]
    assert data.adjusted_trailing("0050", days[4], 5) == [25.0, 25.5, 26.0, 26.5, 27.0]
    assert data.adjusted_trailing("0050", days[1], 2) == [100.0, 102.0]          # before the split
    assert data.adjusted_trailing("0050", days[4], 3) == [26.0, 26.5, 27.0]      # after it
    # Live closes carry no ratios: Taiwan's ±10% limit makes a 75% overnight move a 1:4 split.
    assert adjust_gaps([100.0, 101.0, 25.5, 26.0]) == [25.0, 25.25, 25.5, 26.0]
    assert adjust_gaps([100.0, 95.0, 104.0]) == [100.0, 95.0, 104.0]


def test_moving_average_does_not_read_a_split_as_a_fall():
    days = weekdays(date(2024, 6, 3), 250)
    closes = [100.0] * 230 + [25.0] * 20                                         # 1:4 on session 231
    data = market(days, {"0050": closes}, {"0050": {days[230]: 4.0}})

    result = simulate(BASELINES["ma_value"], data, ContributionPlan(), contributions=[(days[-1], 10_000.0)])

    # Adjusted, every close is 25: not below the 200-day average, so 0.5 × 10,000 = 5,000 is invested:
    # 198 × 25.05 + fee 20 = 4,979.90 (the raw closes would have read 25 < 92.5 and doubled it).
    assert [(trade.asset, trade.shares, trade.price) for trade in result.trades] == [("0050", 198, 25.05)]


def test_trend_control_switches_to_the_defensive_weights():
    days = weekdays(date(2015, 1, 5), 60)
    data = market(days, {"0050": [100.0] * 40 + [80.0] * 20, "00679B": [25.0] * 60})
    spec = StrategySpec.model_validate({
        "name": "趨勢：跌破 20 日均線改債", "allocation": {
            "weights": {"0050": 1.0}, "rebalance": "band",
            "defensive": {"weights": {"00679B": 1.0}, "ma_sessions": 20},
        },
    })

    result = simulate(spec, data, ContributionPlan(), contributions=[(days[29], 10_000.0), (days[44], 10_000.0)])

    # Session 30: 100 is not below its average → 99 × 100.20 of 0050 (+ fee 20; cash 60.20 left).
    # Session 45: 80 < (15 × 100 + 5 × 80) / 20 = 95 → all into 00679B: sell 99 × 79.80 (fee 20, tax 7),
    # then 17,933.40 buys 714 × 25.05 + fee 25.
    assert [(trade.day, trade.asset, trade.side, trade.shares, trade.price) for trade in result.trades] == [
        (days[29], "0050", "BUY", 99, 100.2),
        (days[44], "0050", "SELL", 99, 79.8),
        (days[44], "00679B", "BUY", 714, 25.05),
    ]


def test_trend_control_into_cash_and_back():
    days = weekdays(date(2010, 1, 4), 80)
    closes = [100.0] * 40 + [80.0] * 20 + [120.0] * 20
    data = market(days, {"0050": closes})
    spec = StrategySpec.model_validate({"name": "趨勢：跌破 20 日均線空手", "allocation": {
        "weights": {"0050": 1.0}, "rebalance": "band", "defensive": {"ma_sessions": 20}}})
    assert spec.assets == ["0050"] and spec.signals == {"0050"}

    result = simulate(spec, data, ContributionPlan(), contributions=[
        (days[29], 10_000.0), (days[44], 10_000.0), (days[64], 10_000.0)])

    # Session 45: 80 < 95 → sell the 99 units, keep the cash (17,933.40); session 65: 120 is above
    # (5 × 80 + 15 × 120) / 20 = 110 → 27,933.40 back into 0050: 231 × 120.25 + fee 39 = 27,816.75
    # (232 would need 27,898 + 39 = 27,937).
    assert [(trade.day, trade.side, trade.shares, trade.price) for trade in result.trades] == [
        (days[29], "BUY", 99, 100.2), (days[44], "SELL", 99, 79.8), (days[64], "BUY", 231, 120.25)]


def test_rotation_with_a_core_buys_the_strongest_satellite():
    days = weekdays(date(2015, 1, 5), 30)
    data = market(days, {
        "0050": [100.0] * 30,
        "0056": [30.0 + 0.1 * index for index in range(30)],            # rising
        "006208": [100.0 - 0.5 * index for index in range(30)],         # falling
    })
    allocation = {"weights": {"0050": 1.0},
                  "rotation": {"candidates": ["0056", "006208"], "lookback_sessions": 20, "core": {"0050": 0.8}}}
    momentum = StrategySpec.model_validate({"name": "核心＋衛星：動能", "allocation": allocation})
    reversal = StrategySpec.model_validate({"name": "核心＋衛星：反轉", "allocation": {
        **allocation, "rotation": {**allocation["rotation"], "mode": "reversal"}}})

    trailing = lambda asset, sessions: data.adjusted_trailing(asset, days[24], sessions)  # noqa: E731
    # 20 sessions to session 25: 0056 30.4 → 32.4 (+6.6%), 006208 98 → 88 (−10.2%).
    assert target_weights(momentum, trailing) == {"0050": 0.8, "0056": pytest.approx(0.2), "006208": 0.0}
    assert target_weights(reversal, trailing) == {"0050": 0.8, "0056": 0.0, "006208": pytest.approx(0.2)}

    result = simulate(momentum, data, ContributionPlan(), contributions=[(days[24], 10_000.0)])
    # 8,000 → 79 × 100.20 of 0050; 2,000 → 60 × 32.47 of 0056 (+ fee 20 each).
    assert [(trade.asset, trade.shares, trade.price) for trade in result.trades] == [("0050", 79, 100.2),
                                                                                  ("0056", 60, 32.47)]


def test_today_uses_the_same_targets_and_ignores_a_split():
    from zoneinfo import ZoneInfo

    from quant_platform.application.plan_decision import PlanDecisionService
    from quant_platform.domain.entities import InvestmentPlan
    from quant_platform.market_calendar import TradingCalendar

    taipei = ZoneInfo("Asia/Taipei")
    calendar = TradingCalendar("TW", [], covered_years=[2026])
    days = [day for day in weekdays(date(2025, 12, 1), 300) if calendar.is_trading_day(day) and day <= date(2026, 10, 5)]
    trend = StrategySpec.model_validate({"name": "趨勢：跌破 20 日均線改債", "allocation": {
        "weights": {"0050": 1.0}, "defensive": {"weights": {"00679B": 1.0}, "ma_sessions": 20}}})

    class Plans:
        def __init__(self, key):
            self.plan = InvestmentPlan(1, datetime(2026, 9, 1, tzinfo=UTC), Decimal(10_000), 5, key, 0.3)

        def current(self):
            return self.plan

        def strategies(self):
            return {**BASELINES, "trend": trend}

    class Bars:
        def __init__(self, closes):
            self.closes = closes

        def list_bars(self, symbol, as_of=None):
            return [SimpleNamespace(event_time=datetime(day.year, day.month, day.day, 13, 30, tzinfo=taipei), close=close)
                    for day, close in self.closes.get(symbol, [])]

    account = SimpleNamespace(overview=lambda include_shadow=True: SimpleNamespace(cash=30_000.0, holdings=[], flows=[]))
    calendars = SimpleNamespace(calendar=lambda market: calendar)
    now = datetime(2026, 10, 5, 14, 0, tzinfo=taipei)

    falling = {"0050.TW": [(day, 100.0 if index < len(days) - 5 else 80.0) for index, day in enumerate(days)],
               "00679B.TWO": [(day, 25.0) for day in days]}
    decision = PlanDecisionService(Plans("trend"), account, Bars(falling), calendars).decide(now)
    assert {order.symbol for order in decision.orders} == {"00679B"}
    assert any("改用防守配置：00679B 100%" in reason for reason in decision.reasons)

    split = {"0050.TW": [(day, 100.0 if index < len(days) - 30 else 25.0) for index, day in enumerate(days)]}
    decision = PlanDecisionService(Plans("ma_value"), account, Bars(split), calendars).decide(now)
    assert decision.budget == pytest.approx(5_000)          # 0.5 ×: a split is not a fall below the average
    assert any("高於 200 日均線" in reason for reason in decision.reasons)
