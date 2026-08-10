from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

import pandas as pd

from quant_platform.application.ports import MarketBarRepository


ODD_LOT_SYMBOLS = ("0050.TW", "006208.TW", "00878.TW")


@dataclass(frozen=True, slots=True)
class OddLotAssumptions:
    monthly_budget: int = 10_000
    salary_day: int = 5
    commission_rate: float = 0.001425
    commission_discount: float = 0.28
    minimum_fee: int = 1
    slippage_bps: float = 10.0
    sell_tax_rate: float = 0.001


@dataclass(frozen=True, slots=True)
class OddLotStrategyResult:
    strategy: str
    description: str
    terminal_value: float
    total_contributions: float
    profit: float
    money_weighted_return: float
    annualized_return: float
    excess_annualized_return: float
    max_drawdown: float
    purchase_count: int
    skipped_months: int
    latest_allocation: str
    curve_dates: tuple[str, ...]
    curve_values: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class OddLotResearchOverview:
    assumptions: OddLotAssumptions
    results: tuple[OddLotStrategyResult, ...]
    benchmark_annualized_return: float
    best_strategy: OddLotStrategyResult | None
    data_start: date | None
    data_end: date | None
    observation_count: int
    research_ready: bool
    quality_notes: tuple[str, ...]
    limitations: tuple[str, ...]


class OddLotResearchService:
    """Researches low-frequency odd-lot contribution rules without claiming broker fills."""

    def __init__(self, bars: MarketBarRepository) -> None:
        self._bars = bars

    def run(self, assumptions: OddLotAssumptions | None = None) -> OddLotResearchOverview:
        policy = assumptions or OddLotAssumptions()
        if policy.monthly_budget < 1_000:
            raise ValueError("每月投入金額至少 1,000 元")
        if not 1 <= policy.salary_day <= 28:
            raise ValueError("薪資日必須介於 1 到 28 日")
        prices = self._prices()
        benchmark_frame = prices.get("0050.TW")
        if benchmark_frame is None or len(benchmark_frame) < 260:
            return OddLotResearchOverview(
                policy, (), 0.0, None, None, None, 0, False,
                ("至少需要 260 個交易日才能建立初步研究。",),
                ("0050 歷史行情不足 260 個交易日，請先更新行情資料。",),
            )
        schedule = self._schedule(benchmark_frame.index, policy.salary_day)
        strategies = (
            ("固定買進 0050", "每月固定投入，不擇時；作為受薪族基準。", self._fixed_selector),
            ("0050 趨勢過濾", "收盤價高於 200 日均線且半年動能為正才投入，否則保留現金。", self._trend_selector),
            ("ETF 相對動能", "每月在 0050、006208、00878 中選擇一年／半年動能較佳且為正者。", self._relative_selector),
        )
        raw = [
            self._simulate(name, description, selector, prices, schedule, policy)
            for name, description, selector in strategies
        ]
        benchmark_annualized = raw[0]["annualized_return"]
        results = tuple(
            OddLotStrategyResult(
                strategy=item["strategy"],
                description=item["description"],
                terminal_value=item["terminal_value"],
                total_contributions=item["total_contributions"],
                profit=item["profit"],
                money_weighted_return=item["money_weighted_return"],
                annualized_return=item["annualized_return"],
                excess_annualized_return=item["annualized_return"] - benchmark_annualized,
                max_drawdown=item["max_drawdown"],
                purchase_count=item["purchase_count"],
                skipped_months=item["skipped_months"],
                latest_allocation=item["latest_allocation"],
                curve_dates=tuple(item["curve_dates"]),
                curve_values=tuple(item["curve_values"]),
            )
            for item in raw
        )
        quality_notes: list[str] = []
        if len(benchmark_frame) < 756:
            quality_notes.append("歷史少於 756 個交易日（約三年），尚未涵蓋足夠市場循環。")
        if len(schedule) < 36:
            quality_notes.append("每月投入次數少於 36 次，樣本不足以形成可執行候選。")
        quality_notes.append("尚無 14:30 盤後零股實際成交價與未成交紀錄，成交品質門檻未通過。")
        research_ready = not quality_notes
        eligible = [
            item for item in results
            if research_ready
            and item.excess_annualized_return > 0.01
            and item.max_drawdown >= results[0].max_drawdown - 0.05
        ]
        best = max(eligible, key=lambda item: item.annualized_return) if eligible else None
        return OddLotResearchOverview(
            assumptions=policy,
            results=results,
            benchmark_annualized_return=benchmark_annualized,
            best_strategy=best,
            data_start=benchmark_frame.index[0].date(),
            data_end=benchmark_frame.index[-1].date(),
            observation_count=len(benchmark_frame),
            research_ready=research_ready,
            quality_notes=tuple(quality_notes),
            limitations=(
                "日線沒有 14:30 盤後零股實際成交價，暫以當日調整收盤價加滑價估算。",
                "盤後零股同價委託採電腦隨機排序，回測無法保證成交。",
                "最低手續費由各券商自行訂定，必須依你的券商修改參數。",
                "ETF 還原價只近似總報酬；配息、分割後股數與真實帳務仍須券商資料校正，不能直接當成交帳本。",
                "最佳策略是歷史研究結果，不保證未來超越 0050 或大盤。",
            ),
        )

    def _prices(self) -> dict[str, pd.Series]:
        output: dict[str, pd.Series] = {}
        for symbol in ODD_LOT_SYMBOLS:
            rows = self._bars.list_bars(symbol)
            if not rows:
                continue
            values = {
                item.event_time.date(): float(item.adjusted_close or item.close)
                for item in rows
            }
            output[symbol] = pd.Series(values, dtype=float).sort_index()
            output[symbol].index = pd.to_datetime(output[symbol].index)
        return output

    @staticmethod
    def _schedule(index: pd.DatetimeIndex, salary_day: int) -> list[pd.Timestamp]:
        grouped: dict[tuple[int, int], list[pd.Timestamp]] = {}
        for item in index:
            grouped.setdefault((item.year, item.month), []).append(item)
        return [
            next((item for item in dates if item.day >= salary_day), dates[-1])
            for dates in grouped.values()
        ]

    @staticmethod
    def _fixed_selector(current: pd.Timestamp, prices: dict[str, pd.Series]) -> str | None:
        return "0050.TW"

    @staticmethod
    def _trend_selector(current: pd.Timestamp, prices: dict[str, pd.Series]) -> str | None:
        history = prices["0050.TW"].loc[:current]
        if len(history) < 200:
            return "0050.TW"
        return "0050.TW" if history.iloc[-1] > history.iloc[-200:].mean() and history.iloc[-1] > history.iloc[-126] else None

    @staticmethod
    def _relative_selector(current: pd.Timestamp, prices: dict[str, pd.Series]) -> str | None:
        scores: dict[str, float] = {}
        for symbol, series in prices.items():
            history = series.loc[:current]
            if len(history) < 252:
                continue
            scores[symbol] = 0.5 * (history.iloc[-1] / history.iloc[-126] - 1) + 0.5 * (
                history.iloc[-1] / history.iloc[-252] - 1
            )
        if not scores:
            return "0050.TW"
        symbol = max(scores, key=scores.get)
        return symbol if scores[symbol] > 0 else None

    def _simulate(self, name, description, selector, prices, schedule, policy) -> dict[str, object]:
        cash = 0.0
        holdings = {symbol: 0 for symbol in prices}
        contributions = 0.0
        purchase_count = skipped = 0
        curve_dates: list[str] = []
        curve_values: list[float] = []
        cash_flows: list[tuple[date, float]] = []
        latest_allocation = "現金"
        for current in schedule:
            cash += policy.monthly_budget
            contributions += policy.monthly_budget
            cash_flows.append((current.date(), -float(policy.monthly_budget)))
            target = selector(current, prices)
            if target is None or current not in prices[target].index:
                skipped += 1
            else:
                execution_price = float(prices[target].loc[current]) * (1 + policy.slippage_bps / 10_000)
                shares = max(0, int(cash // execution_price))
                while shares:
                    gross = shares * execution_price
                    fee = max(policy.minimum_fee, gross * policy.commission_rate * policy.commission_discount)
                    if gross + fee <= cash:
                        cash -= gross + fee
                        holdings[target] += shares
                        purchase_count += 1
                        latest_allocation = target
                        break
                    shares -= 1
                if shares == 0:
                    skipped += 1
            value = cash + sum(
                shares * self._price_on_or_before(prices[symbol], current)
                for symbol, shares in holdings.items()
            )
            curve_dates.append(current.date().isoformat())
            curve_values.append(value)
        last_date = schedule[-1]
        gross_terminal = cash + sum(
            shares * self._price_on_or_before(prices[symbol], last_date)
            for symbol, shares in holdings.items()
        )
        securities_value = gross_terminal - cash
        liquidation_cost = securities_value * policy.sell_tax_rate + max(
            policy.minimum_fee,
            securities_value * policy.commission_rate * policy.commission_discount,
        ) if securities_value else 0.0
        terminal = gross_terminal - liquidation_cost
        cash_flows.append((last_date.date(), terminal))
        annualized = self._xirr(cash_flows)
        return {
            "strategy": name,
            "description": description,
            "terminal_value": terminal,
            "total_contributions": contributions,
            "profit": terminal - contributions,
            "money_weighted_return": terminal / contributions - 1 if contributions else 0.0,
            "annualized_return": annualized,
            "max_drawdown": self._contribution_adjusted_drawdown(curve_values, policy.monthly_budget),
            "purchase_count": purchase_count,
            "skipped_months": skipped,
            "latest_allocation": latest_allocation,
            "curve_dates": curve_dates,
            "curve_values": curve_values,
        }

    @staticmethod
    def _price_on_or_before(series: pd.Series, current: pd.Timestamp) -> float:
        history = series.loc[:current]
        return float(history.iloc[-1]) if len(history) else 0.0

    @staticmethod
    def _xirr(cash_flows: list[tuple[date, float]]) -> float:
        start = cash_flows[0][0]
        def value(rate: float) -> float:
            return sum(amount / (1 + rate) ** ((when - start).days / 365.25) for when, amount in cash_flows)
        low, high = -0.95, 5.0
        if value(low) * value(high) > 0:
            return 0.0
        for _ in range(100):
            middle = (low + high) / 2
            if value(low) * value(middle) <= 0:
                high = middle
            else:
                low = middle
        return (low + high) / 2

    @staticmethod
    def _contribution_adjusted_drawdown(values: list[float], contribution: float) -> float:
        if len(values) < 2:
            return 0.0
        index = 1.0
        peak = 1.0
        worst = 0.0
        for previous, current in zip(values, values[1:]):
            base = previous if previous > 0 else 1.0
            period_return = (current - contribution) / base - 1
            index *= 1 + period_return
            peak = max(peak, index)
            worst = min(worst, index / peak - 1)
        return worst
