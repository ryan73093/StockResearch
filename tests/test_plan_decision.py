from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from quant_platform.application.plan_decision import PlanDecisionService, invest_session
from quant_platform.domain.entities import ActualCashFlow, InvestmentPlan
from quant_platform.market_calendar import MarketClosure, TradingCalendar
from quant_platform.research.spec import BASELINES

TAIPEI = ZoneInfo("Asia/Taipei")
CALENDAR = TradingCalendar("TW", [MarketClosure(date(2026, 10, 9), "國慶日補假")], covered_years=[2026])


class Calendars:
    def calendar(self, market):
        return CALENDAR


class Plans:
    def __init__(self, strategy="benchmark_dca", amount=10_000, day=5):
        self.plan = InvestmentPlan(1, datetime(2026, 9, 1, tzinfo=UTC), Decimal(amount), day, strategy, 0.3)

    def current(self):
        return self.plan


class Account:
    def __init__(self, cash=0.0, holdings=(), flows=()):
        self.value = SimpleNamespace(cash=cash, holdings=list(holdings), flows=list(flows))

    def overview(self, include_shadow=True):
        return self.value


class Bars:
    """0050.TW (and 00679B.TWO) closes for the sessions before and on the day."""

    def __init__(self, closes):
        self.closes = closes

    def list_bars(self, symbol, as_of=None):
        series = self.closes.get(symbol, [])
        return [
            SimpleNamespace(event_time=datetime.combine(day, time(13, 30), TAIPEI), close=close)
            for day, close in series
        ]


def sessions_until(end, count):
    days, day = [], end
    while len(days) < count:
        if CALENDAR.is_trading_day(day):
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


def at(day, hour=14, minute=0):
    return datetime.combine(day, time(hour, minute), TAIPEI)


def service(strategy="benchmark_dca", closes=None, account=None, day=5):
    closes = closes or {"0050.TW": [(item, 100.0) for item in sessions_until(date(2026, 10, 5), 300)]}
    return PlanDecisionService(Plans(strategy, day=day), account or Account(), Bars(closes), Calendars())


def test_invest_session_moves_past_holidays_and_rolls_over():
    assert invest_session(BASELINES["benchmark_dca"], 9, date(2026, 10, 1), CALENDAR) == date(2026, 10, 12)
    assert invest_session(BASELINES["fixed_day_dca"], 5, date(2026, 10, 7), CALENDAR) == date(2026, 11, 6)


def test_idle_and_before_close():
    idle = service().decide(at(date(2026, 10, 2)))
    assert idle.kind == "idle" and "10/05" in idle.headline

    early = service().decide(at(date(2026, 10, 5), 11))
    assert early.kind == "wait_close" and not early.orders


def test_dca_invest_day_by_hand():
    decision = service().decide(at(date(2026, 10, 5)))

    assert decision.kind == "invest"
    order = decision.orders[0]
    # Limit = close 100 + 1% = 101.00 (0.05 tick); even at the limit 98 × 101 + fee 20 = 9,918 fits in
    # 10,000 (99 × 101 + 20 = 10,019 does not). Estimates use the expected fill 100.20 (close + 20 bps).
    assert (order.symbol, order.side, order.shares, order.limit_price) == ("0050", "BUY", 98, 101.00)
    assert (order.expected_price, order.amount, order.fee, order.worst_case) == (100.20, pytest.approx(98 * 100.2), 20, 9_918)
    assert decision.budget == 10_000  # the month's deposit is not recorded yet, so the plan amount is used
    assert any("本月入金尚未記錄" in reason for reason in decision.reasons)


def test_the_plans_broker_sets_the_fee_estimate():
    plans = Plans()
    plans.plan = InvestmentPlan(1, datetime(2026, 9, 1, tzinfo=UTC), Decimal(10_000), 5, "benchmark_dca", 0.3,
                                broker="taishin")
    closes = {"0050.TW": [(item, 100.0) for item in sessions_until(date(2026, 10, 5), 300)]}
    decision = PlanDecisionService(plans, Account(), Bars(closes), Calendars()).decide(at(date(2026, 10, 5)))

    order = decision.orders[0]
    # At the limit 98 × 101 = 9,898 + fee 14 fits (99 × 101 + 14 = 10,013 does not); the estimate
    # 98 × 100.20 = 9,819.60 pays floor(9,819.60 × 0.1425%) = 13 at list price (conservative: 20).
    assert (order.shares, order.fee) == (98, 13)
    assert any("台新證券" in reason and "月退的退佣不計入" in reason for reason in decision.reasons)


def test_recorded_deposit_is_not_counted_twice():
    flows = [ActualCashFlow(1, date(2026, 10, 5), "deposit", Decimal(10_000))]
    decision = service(account=Account(cash=10_000.0, flows=flows)).decide(at(date(2026, 10, 5)))

    assert decision.budget == 10_000 and decision.orders[0].shares == 98


def test_moving_average_doubles_below_the_average():
    days = sessions_until(date(2026, 10, 5), 300)
    falling = {"0050.TW": [(day, 200.0 - index * 0.3) for index, day in enumerate(days)]}
    decision = service("ma_value", closes=falling, account=Account(cash=30_000.0)).decide(at(date(2026, 10, 5)))

    assert decision.budget == pytest.approx(20_000)   # 2 × the month's 10,000 while cash allows
    assert any("低於 200 日均線" in reason for reason in decision.reasons)


def test_missing_close_waits_for_data():
    stale = {"0050.TW": [(day, 100.0) for day in sessions_until(date(2026, 10, 2), 10)]}
    decision = service(closes=stale).decide(at(date(2026, 10, 5)))
    assert decision.kind == "missing_data" and "0050" in decision.headline


def test_band_rebalance_sells_the_overweight_asset():
    days = sessions_until(date(2026, 10, 5), 30)
    closes = {"0050.TW": [(day, 100.0) for day in days], "00679B.TWO": [(day, 25.0) for day in days]}
    holdings = [SimpleNamespace(symbol="0050", shares=1_000), SimpleNamespace(symbol="00679B", shares=0)]
    decision = service("rebalance_80_20", closes=closes, account=Account(cash=0.0, holdings=holdings)).decide(
        at(date(2026, 10, 5))
    )

    assert decision.kind == "rebalance"
    sides = {order.symbol: order.side for order in decision.orders}
    assert sides == {"0050": "SELL", "00679B": "BUY"}


def test_order_limit_lets_the_auction_fill_inside_the_price_limits():
    from quant_platform.research.costs import order_limit

    # Close + 1% rounded up to the tick (0.05 from 50, 0.01 below); sells close − 1% rounded down.
    assert order_limit(100.0, "BUY") == 101.00 and order_limit(113.0, "BUY") == 114.15
    assert order_limit(36.13, "BUY") == 36.50 and order_limit(100.0, "SELL") == 99.00
    # Never beyond the day's limit around the previous close: 110.60 → limit-up 110.00, 89.55 → limit-down 90.00.
    assert order_limit(109.5, "BUY", previous_close=100.0) == 110.00
    assert order_limit(90.5, "SELL", previous_close=100.0) == 90.00


def test_rebalance_buys_only_with_what_the_sale_brings_at_its_limit():
    days = sessions_until(date(2026, 10, 5), 30)
    closes = {"0050.TW": [(day, 100.0) for day in days], "00679B.TWO": [(day, 25.0) for day in days]}
    holdings = [SimpleNamespace(symbol="0050", shares=1_000), SimpleNamespace(symbol="00679B", shares=0)]
    decision = service("rebalance_80_20", closes=closes, account=Account(cash=0.0, holdings=holdings)).decide(
        at(date(2026, 10, 5))
    )
    sell = next(order for order in decision.orders if order.side == "SELL")
    buy = next(order for order in decision.orders if order.side == "BUY")
    # Sell 120 × 0050 (excess 12,000 at the expected 99.80); at its limit 99.00 it brings
    # 11,880 − fee 20 − tax 11 = 11,849. The buy gets the unrecorded month's 10,000 + 11,849 = 21,849:
    # 864 × 25.25 + fee 31 = 21,847 even if both orders fill at their limits.
    assert (sell.shares, sell.limit_price, sell.worst_case) == (120, 99.00, 11_849)
    assert (buy.shares, buy.limit_price, buy.worst_case) == (864, 25.25, 21_847)
    assert buy.worst_case <= 10_000 + sell.worst_case
