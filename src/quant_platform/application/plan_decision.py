"""Today's advice from the investor's plan (S5-W02, first version).

The plan names a strategy (a built-in baseline for now). On the strategy's
invest session this service turns the plan amount, the real account's cash
and holdings and the signal asset's closes into after-hours odd-lot orders
with the same sizing, allocation, fee and fill rules as the research engine
(research/engine.py, research/costs.py). On other sessions it says that no
action is needed and when the next invest session is. It never places
orders; the investor copies them to the broker.
"""

from __future__ import annotations

import math
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from quant_platform.research.costs import BROKERS, CostModel, affordable_shares, fill_price
from quant_platform.research.spec import BASELINES, StrategySpec

TAIPEI = ZoneInfo("Asia/Taipei")
MARKET_CLOSE = time(13, 30)
SUFFIXES = ("TW", "TWO")


@dataclass(frozen=True, slots=True)
class PlanOrder:
    symbol: str
    side: str
    shares: int
    limit_price: float
    reference_close: float
    amount: float
    fee: int
    tax: int


@dataclass(frozen=True)
class PlanDecision:
    kind: str  # invest | rebalance | idle | wait_close | no_plan | missing_data
    headline: str
    plan_version: int | None
    strategy: str
    invest_day: date | None
    orders: list[PlanOrder] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    budget: float = 0.0
    data_time: str = ""


def clamped_date(year: int, month: int, day: int) -> date:
    """``day`` in that month, or the month's last day when it is shorter (31 → 30/28)."""
    return date(year, month, min(day, monthrange(year, month)[1]))


def invest_session(spec: StrategySpec, salary_day: int, today: date, calendar) -> date:
    """This month's invest session for the spec, or next month's once passed."""
    day_of_month = salary_day if spec.contribution.invest_on == "contribution_day" else spec.contribution.day_of_month

    def session_for(year: int, month: int) -> date:
        target = clamped_date(year, month, day_of_month)
        return target if calendar.is_trading_day(target) else calendar.next_trading_day(target)

    session = session_for(today.year, today.month)
    if session < today:
        year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        session = session_for(year, month)
    return session


class PlanDecisionService:
    def __init__(self, plans, account, bars, calendar_store, costs: CostModel | None = None) -> None:
        self._plans = plans
        self._account = account
        self._bars = bars
        self._calendar_store = calendar_store
        self._fixed_costs = costs  # otherwise the plan's broker profile

    def _closes(self, code: str, now: datetime) -> tuple[str | None, list[tuple[date, float]]]:
        for suffix in SUFFIXES:
            bars = self._bars.list_bars(f"{code}.{suffix}", as_of=now)
            if bars:
                return f"{code}.{suffix}", [
                    (bar.event_time.astimezone(TAIPEI).date(), float(bar.close)) for bar in bars
                ]
        return None, []

    def decide(self, now: datetime | None = None) -> PlanDecision:
        now = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
        today = now.date()
        plan = self._plans.current()
        if plan is None:
            return PlanDecision("no_plan", "還沒有投資計畫", None, "", None)
        spec = BASELINES.get(plan.strategy_key)
        if spec is None:
            return PlanDecision("missing_data", f"找不到策略 {plan.strategy_key}", plan.version, plan.strategy_key, None)
        calendar = self._calendar_store.calendar("TW")
        session = invest_session(spec, plan.salary_day, today, calendar)
        base = dict(plan_version=plan.version, strategy=spec.name, invest_day=session)
        if session != today:
            return PlanDecision(
                "idle", f"今天不需操作：下次投入日 {session:%m/%d}", reasons=[
                    f"「{spec.name}」只在投入日操作；每月投入 {plan.monthly_amount:,.0f} 元。"
                ], **base,
            )
        if now.time() < MARKET_CLOSE:
            return PlanDecision(
                "wait_close", "今天是投入日：13:30 收盤後產生委託", reasons=[
                    "盤後零股 13:40 開始收單；委託以今日收盤價計算。"
                ], **base,
            )

        prices: dict[str, float] = {}
        histories: dict[str, list[tuple[date, float]]] = {}
        for code in sorted(set(spec.assets) | {spec.signal}):
            symbol, history = self._closes(code, now)
            if not history or history[-1][0] != today:
                return PlanDecision(
                    "missing_data", f"{code} 今日收盤尚未取得，稍後重新整理", reasons=[
                        "盤後資料通常在 13:45 前後齊全。"
                    ], **base,
                )
            histories[code] = history
            prices[code] = history[-1][1]

        broker = BROKERS.get(getattr(plan, "broker", "") or "conservative", BROKERS["conservative"])
        costs = self._fixed_costs or broker.cost_model()
        account = self._account.overview(include_shadow=False)
        month_key = (today.year, today.month)
        deposited = any(
            flow.kind == "deposit" and (flow.day.year, flow.day.month) == month_key for flow in account.flows
        )
        new_money = float(plan.monthly_amount)
        cash = account.cash + (0.0 if deposited else new_money)
        reasons = [] if deposited else [f"本月入金尚未記錄，先以計畫金額 {new_money:,.0f} 元計算。"]
        multiplier, why = self._multiplier(spec, histories[spec.signal])
        if why:
            reasons.append(why)
        if spec.sizing.type == "all_cash":
            budget = cash
        else:
            budget = min(cash, new_money * multiplier)
            cap = new_money * spec.sizing.max_reserve_months
            if cash - budget > cap:
                budget = cash - cap
                reasons.append(f"閒置現金超過 {spec.sizing.max_reserve_months:g} 個月上限，多投入超出部分。")

        holdings = {item.symbol: item for item in account.holdings}
        values = {code: (holdings[code].shares * prices[code] if code in holdings else 0.0) for code in spec.assets}
        orders: list[PlanOrder] = []
        kind = "invest"
        weights = spec.allocation.weights
        invested = sum(values.values())
        if len(weights) > 1 and spec.allocation.rebalance == "band" and invested > 0:
            drift = max(abs(values[code] / invested - weights[code]) for code in weights)
            if drift > spec.allocation.band:
                kind = "rebalance"
                reasons.append(f"配置偏離目標 {drift:.1%}，超過 {spec.allocation.band:.0%} 區間，賣高買低。")
                target_total = invested + budget
                for code in sorted(weights):
                    excess = values[code] - weights[code] * target_total
                    if excess > 0:
                        price = fill_price(prices[code], "SELL", costs.slippage_bps)
                        shares = min(holdings[code].shares, math.floor(excess / price))
                        if shares > 0:
                            amount = shares * price
                            fee = costs.fee(amount)
                            tax = costs.tax(amount, _tax_kind(code), "SELL")
                            orders.append(PlanOrder(code, "SELL", shares, price, prices[code], amount, fee, tax))
                            budget += amount - fee - tax
                            values[code] -= shares * prices[code]
        budget = max(0.0, budget)
        target_total = sum(values.values()) + budget
        gaps = {code: max(0.0, weights[code] * target_total - values[code]) for code in weights}
        gap_total = sum(gaps.values())
        remaining = budget
        for code in sorted(weights):
            allowance = budget * gaps[code] / gap_total if gap_total > 0 else budget * weights[code]
            allowance = min(allowance, remaining)
            price = fill_price(prices[code], "BUY", costs.slippage_bps)
            shares = affordable_shares(allowance, price, costs)
            if shares > 0:
                amount = shares * price
                fee = costs.fee(amount)
                orders.append(PlanOrder(code, "BUY", shares, price, prices[code], amount, fee, 0))
                remaining -= amount + fee
        fee_terms = "原價" if costs.fee_discount >= 1 else f"{costs.fee_discount * 10:g} 折"
        reasons.append(
            f"限價＝收盤加 {costs.slippage_bps:g} bps 進位到升降單位（盤後零股成交價中位數約高於收盤 13–18 bps）；"
            f"手續費以{broker.name}估算（{fee_terms}、每筆最低 {costs.minimum_fee} 元"
            f"{'；月退的退佣不計入' if '月退' in broker.rebate else ''}），實際以對帳單為準。"
        )
        if not orders:
            return PlanDecision(
                "idle", "今天是投入日，但可用資金不足以買進 1 股", reasons=reasons, budget=budget,
                data_time=f"{today:%m/%d} 收盤", **base,
            )
        headline = "今天依計畫投入：" + "、".join(
            f"{'買進' if order.side == 'BUY' else '賣出'} {order.symbol} {order.shares:,} 股" for order in orders
        )
        return PlanDecision(
            kind, headline, orders=orders, reasons=reasons, budget=budget,
            data_time=f"{today:%m/%d} 收盤", **base,
        )

    @staticmethod
    def _multiplier(spec: StrategySpec, history: list[tuple[date, float]]) -> tuple[float, str]:
        sizing = spec.sizing
        closes = [close for _, close in history]
        if sizing.type == "moving_average":
            window = closes[-sizing.ma_sessions:]
            if len(window) < sizing.ma_sessions:
                return 1.0, f"{spec.signal} 行情不足 {sizing.ma_sessions} 個交易日，先用 1 倍。"
            average = sum(window) / len(window)
            weak = window[-1] < average
            factor = sizing.weak_multiplier if weak else sizing.strong_multiplier
            return factor, (
                f"{spec.signal} 收盤 {window[-1]:,.2f} {'低於' if weak else '高於'} {sizing.ma_sessions} 日均線 "
                f"{average:,.2f}，投入當月新資金的 {factor:g} 倍。"
            )
        if sizing.type == "drawdown":
            window = closes[-252:]
            drawdown = window[-1] / max(window) - 1
            weak = drawdown <= -sizing.drawdown_threshold
            factor = sizing.weak_multiplier if weak else sizing.strong_multiplier
            return factor, (
                f"{spec.signal} 距一年高點 {drawdown:+.1%}（門檻 {-sizing.drawdown_threshold:.0%}），投入 {factor:g} 倍。"
            )
        return 1.0, ""


def _tax_kind(code: str) -> str:
    from quant_platform.application.actual_account import tax_kind

    return tax_kind(code)
