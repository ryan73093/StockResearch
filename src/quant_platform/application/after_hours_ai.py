from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from zoneinfo import ZoneInfo

from quant_platform.application.ports import (
    DailyDecisionRepository,
    MarketBarRepository,
    PortfolioResearchRepository,
    ResearchUniverseRepository,
)


ZERO = Decimal("0")
MAX_ODD_LOT_ORDER = 999
MAX_DAILY_ACTIONS = 3
MAX_DAILY_BUYS = 2
RESEARCH_SIMULATION_WEIGHT = 0.05
RESEARCH_ONLY_GATES = frozenset({
    "模型尚未通過候選門檻",
    "策略整合尚未通過候選門檻",
    "模型預測早於最新收盤快照",
})


@dataclass(frozen=True, slots=True)
class ModelReplayPolicy:
    """Point-in-time exposure rules applied to persisted model predictions."""

    name: str = "AI 模型排序＋0050 核心"
    normal_gross_weight: float = 0.80
    rebalance_every_sessions: int = 5
    active_count: int = 3
    minimum_predicted_return: float = 0.007
    minimum_rank: float = 0.75
    breadth_threshold: float = 0.50
    strong_breadth_core_weight: float = 0.40
    weak_breadth_core_weight: float = 0.60
    maximum_active_weight: float = 0.20
    active_weighting: str = "equal"
    minimum_trade_weight: float = 0.0
    evaluation_start: date | None = None
    trend_lookback_sessions: int = 0
    trend_threshold: float = 0.0
    defensive_gross_weight: float = 0.80
    defensive_core_weight: float | None = None
    defensive_active_scale: float = 1.0
    drawdown_trigger: float | None = None
    drawdown_guard_gross_weight: float = 0.10
    drawdown_guard_rebalances: int = 0
    execution_mode: str = "next_open"


BASELINE_MODEL_REPLAY_POLICY = ModelReplayPolicy()
DEFENSIVE_TREND_MODEL_REPLAY_POLICY = ModelReplayPolicy(
    name="AI 模型排序＋189 日趨勢風控",
    normal_gross_weight=0.76,
    trend_lookback_sessions=189,
    trend_threshold=0.03,
    defensive_gross_weight=0.05,
)
AFTER_HOURS_CLOSE_PROXY_MODEL_REPLAY_POLICY = ModelReplayPolicy(
    name="AI 模型排序＋盤後零股收盤價代理",
    execution_mode="after_hours_close_proxy",
)
BENCHMARK_AWARE_LOW_TURNOVER_MODEL_REPLAY_POLICY = ModelReplayPolicy(
    name="0050 核心＋超額報酬 GPU 低週轉衛星",
    normal_gross_weight=1.0,
    rebalance_every_sessions=20,
    active_count=5,
    minimum_predicted_return=0.007,
    minimum_rank=0.90,
    strong_breadth_core_weight=0.80,
    weak_breadth_core_weight=0.80,
    maximum_active_weight=0.10,
    minimum_trade_weight=0.02,
    execution_mode="after_hours_close_proxy",
)


@dataclass(frozen=True, slots=True)
class AfterHoursOrderDraft:
    symbol: str
    side: str
    quantity: int
    odd_lot_order_count: int
    reference_price: Decimal
    estimated_amount: Decimal
    estimated_cost: Decimal
    current_quantity: int
    target_quantity: int
    target_weight: float
    predicted_return_5d: float | None
    score: float
    reason: str


@dataclass(frozen=True, slots=True)
class AfterHoursWatchItem:
    symbol: str
    status: str
    score: float
    predicted_return_5d: float | None
    suggested_weight: float
    blocker: str


@dataclass(frozen=True, slots=True)
class AfterHoursPlan:
    generated_at: datetime
    decision_time: datetime | None
    equity: Decimal
    cash: Decimal
    orders: tuple[AfterHoursOrderDraft, ...]
    watchlist: tuple[AfterHoursWatchItem, ...]
    headline: str
    mode: str
    hard_rules: tuple[str, ...]
    submission_allowed: bool = True
    snapshot_kind: str = "current"

    @property
    def buy_count(self) -> int:
        return sum(item.side == "BUY" for item in self.orders)

    @property
    def sell_count(self) -> int:
        return sum(item.side == "SELL" for item in self.orders)

    @property
    def estimated_turnover(self) -> Decimal:
        return sum((item.estimated_amount for item in self.orders), ZERO)

    def to_markdown(self) -> str:
        lines = [
            (
                "## 盤後 AI 零股決策（只讀回看）"
                if not self.submission_allowed else "## 盤後 AI 零股決策"
            ),
            f"- 結論：{self.headline}",
            f"- 買進 {self.buy_count} 檔；賣出 {self.sell_count} 檔",
            f"- 模擬資產：NT$ {self.equity:,.0f}；現金：NT$ {self.cash:,.0f}",
        ]
        if self.orders:
            lines.append("")
            lines.append("### 委託草稿")
            for item in self.orders:
                action = "買進" if item.side == "BUY" else "賣出"
                lines.append(
                    f"- {item.symbol}：{action} {item.quantity} 股，"
                    f"參考價 {item.reference_price:,.2f}，原因：{item.reason}"
                )
        else:
            lines.append("- 今日沒有通過全部閘門的委託。")
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class AfterHoursSimulationTrade:
    event_time: datetime
    symbol: str
    side: str
    quantity: int
    price: Decimal
    gross_amount: Decimal
    cost: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class AfterHoursSimulationPoint:
    event_time: datetime
    equity: Decimal
    cash: Decimal
    gross_exposure: float
    benchmark_equity: Decimal | None = None


@dataclass(frozen=True, slots=True)
class AfterHoursRegimeResult:
    key: str
    label: str
    session_count: int
    strategy_return: float
    benchmark_return: float
    excess_return: float
    passed: bool


@dataclass(frozen=True, slots=True)
class AfterHoursValidation:
    status: str
    conclusion: str
    strategy_name: str
    strategy_gate: str
    decision_sessions: int
    decision_symbols: int
    candidate_signals: int
    simulated_orders: int
    simulated_round_trips: int
    total_return: float
    benchmark_return: float | None
    excess_return: float | None
    max_drawdown: float
    turnover: float
    paper_orders: int
    paper_fills: int
    paper_return: float
    active_universe: int
    decision_covered: int
    data_ready: int
    discovered_common_stocks: int
    simulation_start: datetime | None
    simulation_end: datetime | None
    trades: tuple[AfterHoursSimulationTrade, ...]
    equity_curve: tuple[AfterHoursSimulationPoint, ...]
    research_return: float | None = None
    research_benchmark_return: float | None = None
    research_excess_return: float | None = None
    replay_vs_research_gap: float | None = None
    regime_results: tuple[AfterHoursRegimeResult, ...] = ()
    execution_assumption: str = "下一交易日開盤成交"


@dataclass(frozen=True, slots=True)
class AfterHoursPaperSubmission:
    submitted: int
    reused: int
    rejected: int


class AfterHoursAiService:
    """Converts approved research decisions into auditable odd-lot order drafts."""

    def __init__(
        self,
        decisions: DailyDecisionRepository,
        bars: MarketBarRepository,
        paper_trading_service: object,
        universe: ResearchUniverseRepository | None = None,
        universe_history_service: object | None = None,
        portfolio_repository: PortfolioResearchRepository | None = None,
        model_repository: object | None = None,
    ) -> None:
        self._decisions = decisions
        self._bars = bars
        self._paper = paper_trading_service
        self._universe = universe
        self._universe_history = universe_history_service
        self._portfolios = portfolio_repository
        self._models = model_repository
        self._validation_cache: tuple[float, AfterHoursValidation] | None = None
        self._validation_lock = threading.Lock()
        self._validation_job: threading.Thread | None = None
        self._validation_pending = False
        self._validation_error: str | None = None
        self._model_validation_cache: tuple[float, AfterHoursValidation | None] | None = None
        self._model_validation_job: threading.Thread | None = None
        self._model_validation_pending = False
        self._model_validation_error: str | None = None
        self._plan_cache: tuple[float, AfterHoursPlan] | None = None
        self._plan_lock = threading.Lock()

    @staticmethod
    def _status(value: str) -> str:
        normalized = value.strip().upper()
        return {
            "CANDIDATE": "候選",
            "WATCH": "觀察",
            "AVOID": "避免",
            "INSUFFICIENT": "資料不足",
        }.get(normalized, value)

    @staticmethod
    def _blockers(decision: object) -> tuple[str, ...]:
        output: list[str] = []
        for attribute in ("risks_json", "gate_checks_json"):
            try:
                values = json.loads(getattr(decision, attribute, "") or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                values = []
            if isinstance(values, list):
                output.extend(str(item) for item in values)
        return tuple(output)

    @classmethod
    def _execution_blockers(cls, decision: object) -> tuple[str, ...]:
        """Keep data/risk blockers hard while allowing research-only paper trades."""
        return tuple(
            item for item in cls._blockers(decision)
            if item not in RESEARCH_ONLY_GATES
        )

    def _latest_price(self, symbol: str, now: datetime) -> Decimal | None:
        bars = self._bars.list_bars(symbol, as_of=now)
        return Decimal(bars[-1].close) if bars else None

    @staticmethod
    def _money(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))

    @staticmethod
    def _estimated_cost(side: str, gross: Decimal) -> Decimal:
        commission = max(Decimal("1"), gross * Decimal("0.001425"))
        slippage = gross * Decimal("0.001")
        tax = gross * Decimal("0.003") if side == "SELL" else ZERO
        return AfterHoursAiService._money(commission + slippage + tax)

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    def _decision_history(self) -> tuple[object, ...]:
        loader = getattr(self._decisions, "list_history", None)
        if callable(loader):
            values = loader("TW")
        else:
            values = self._decisions.list_latest("TW")
        # A rerun can save more than one research version for the same stock/session.
        # The last computed record is the only one allowed into the simulation.
        taipei = ZoneInfo("Asia/Taipei")
        unique: dict[tuple[date, str], object] = {}
        for item in sorted(
            values,
            key=lambda value: (
                self._aware(value.event_time),
                self._aware(value.computed_at),
            ),
        ):
            unique[(
                self._aware(item.event_time).astimezone(taipei).date(), item.symbol
            )] = item
        return tuple(sorted(
            unique.values(),
            key=lambda item: (self._aware(item.event_time), item.symbol),
        ))

    def _universe_coverage(
        self, decisions: tuple[object, ...], now: datetime
    ) -> tuple[int, int, int, int]:
        active_assets = (
            tuple(self._universe.list_active("TW")) if self._universe is not None else ()
        )
        active = sum(
            item.asset_type.upper() in {"EQUITY", "ETF"}
            for item in active_assets
        )
        covered_symbols = {item.symbol for item in decisions}
        ready = 0
        eligible = {
            item.symbol
            for item in active_assets
            if item.asset_type.upper() in {"EQUITY", "ETF"}
        }
        # Do one grouped query for the whole universe.  The old loop opened one
        # SQLite query per stock, which made the first /ai-trading request look
        # frozen when the universe was large.
        count_sessions = getattr(self._bars, "count_daily_sessions", None)
        if callable(count_sessions):
            sessions_by_symbol = count_sessions("TW", as_of=now)
            ready = sum(
                sessions_by_symbol.get(symbol, 0) >= 252
                for symbol in eligible
            )
        else:
            for symbol in eligible:
                if len(self._bars.list_bars(symbol, as_of=now)) >= 252:
                    ready += 1
        discovered = active
        if self._universe_history is not None:
            members = self._universe_history.members_on(
                now.astimezone(ZoneInfo("Asia/Taipei")).date(), "TW"
            )
            discovered = len({
                item.symbol
                for item in members
                if re.fullmatch(r"\d{4}\.(?:TW|TWO)", item.symbol)
            })
        return active, len(covered_symbols), ready, discovered

    def _latest_portfolio_artifact(self) -> object | None:
        if self._portfolios is None:
            return None
        runs = tuple(self._portfolios.list_runs("TW"))
        if not runs:
            return None
        latest_computed = max(self._aware(item.computed_at) for item in runs)
        latest_batch = [
            item
            for item in runs
            if self._aware(item.computed_at) == latest_computed and item.id is not None
        ]
        if not latest_batch:
            return None
        selected = max(
            latest_batch,
            key=lambda item: (
                item.promotion_gate == "CANDIDATE",
                float(item.excess_to_benchmark),
                float(item.sharpe),
                float(item.total_return),
            ),
        )
        return self._portfolios.get(int(selected.id))

    def _canonical_portfolio_validation(
        self, as_of: datetime
    ) -> AfterHoursValidation | None:
        """Read the persisted research equity curve without inventing stock trades.

        Portfolio weights allocate capital across each stock's *strategy return*,
        not directly across the stock itself. Replaying those weights as shares
        changes the strategy and previously produced the misleading 48.13%.
        """
        artifact = self._latest_portfolio_artifact()
        if artifact is None or not artifact.equity:
            return None
        run = artifact.run
        initial_cash = Decimal("1000000")
        allocations = sorted(
            artifact.allocations, key=lambda item: self._aware(item.event_time)
        )
        allocation_index = 0
        cash_weight = 1.0
        curve: list[AfterHoursSimulationPoint] = []
        for point in sorted(
            artifact.equity, key=lambda item: self._aware(item.event_time)
        ):
            event_time = self._aware(point.event_time)
            while (
                allocation_index < len(allocations)
                and self._aware(allocations[allocation_index].event_time) <= event_time
            ):
                cash_weight = float(allocations[allocation_index].cash_weight)
                allocation_index += 1
            equity = self._money(initial_cash * Decimal(str(point.equity)))
            curve.append(AfterHoursSimulationPoint(
                event_time=event_time,
                equity=equity,
                cash=self._money(equity * Decimal(str(cash_weight))),
                gross_exposure=max(0.0, min(1.0, 1.0 - cash_weight)),
                benchmark_equity=self._money(
                    initial_cash * Decimal(str(point.benchmark_equity))
                ),
            ))

        method_label = {
            "cvar": "條件風險值配置（CVaR）",
            "equal_weight": "平均配置",
            "inverse_volatility": "反波動配置",
            "minimum_variance": "最小變異配置",
            "mean_variance": "報酬風險配置",
            "risk_parity": "風險平價",
            "hierarchical_risk_parity": "階層風險平價",
            "kelly": "凱利配置（Kelly）",
            "black_litterman": "Black-Litterman 配置",
            "benchmark_core_satellite": "大盤核心＋主動選股",
        }.get(run.method, run.method)
        active_assets = (
            tuple(self._universe.list_active("TW")) if self._universe is not None else ()
        )
        active_count = sum(
            item.asset_type.upper() in {"EQUITY", "ETF"} for item in active_assets
        )
        covered = len(tuple(self._decisions.list_latest("TW")))
        account = self._paper.overview(now=as_of)
        paper_orders = len(tuple(getattr(account, "orders", ())))
        paper_fills = len(tuple(getattr(account, "fills", ())))
        paper_return = float(getattr(account, "total_return", 0) or 0)
        status = "可比較" if run.promotion_gate == "CANDIDATE" else "研究中"
        relative_word = "多賺" if run.excess_to_benchmark >= 0 else "少賺"
        conclusion = (
            f"正式答案：{method_label} 報酬 {run.total_return:+.2%}；"
            f"同期間買 0050 是 {run.benchmark_return:+.2%}，"
            f"所以策略{relative_word} {abs(run.excess_to_benchmark):.2%}。"
            "舊版顯示的 +48.13% 是把「策略權重」錯當成「股票持股權重」"
            "重新買賣後得到的另一套結果，並不是同一個策略，已停止採用。"
        )
        return AfterHoursValidation(
            status=status,
            conclusion=conclusion,
            strategy_name=f"{method_label}研究策略",
            strategy_gate="候選" if run.promotion_gate == "CANDIDATE" else "研究中",
            decision_sessions=run.observation_count,
            decision_symbols=run.asset_count,
            candidate_signals=0,
            simulated_orders=0,
            simulated_round_trips=0,
            total_return=float(run.total_return),
            benchmark_return=float(run.benchmark_return),
            excess_return=float(run.excess_to_benchmark),
            max_drawdown=float(run.max_drawdown),
            turnover=float(run.turnover),
            paper_orders=paper_orders,
            paper_fills=paper_fills,
            paper_return=paper_return,
            active_universe=active_count,
            decision_covered=covered,
            data_ready=run.asset_count,
            discovered_common_stocks=active_count,
            simulation_start=curve[0].event_time,
            simulation_end=curve[-1].event_time,
            trades=(),
            equity_curve=tuple(curve),
            research_return=float(run.total_return),
            research_benchmark_return=float(run.benchmark_return),
            research_excess_return=float(run.excess_to_benchmark),
            replay_vs_research_gap=None,
        )

    def _portfolio_validation(
        self, as_of: datetime
    ) -> AfterHoursValidation | None:
        """Replay persisted portfolio allocations as next-session odd-lot trades."""
        artifact = self._latest_portfolio_artifact()
        if artifact is None or not artifact.allocations:
            return None

        run = artifact.run
        allocations = sorted(
            artifact.allocations, key=lambda item: self._aware(item.event_time)
        )
        allocation_weights = [
            (
                self._aware(item.event_time),
                {
                    str(symbol): max(float(weight), 0.0)
                    for symbol, weight in json.loads(item.weights_json).items()
                },
            )
            for item in allocations
        ]
        symbols = sorted({
            symbol
            for _, weights in allocation_weights
            for symbol, weight in weights.items()
            if weight > 0
        })
        if len(symbols) < 2:
            return None

        bars_by_symbol = {
            symbol: tuple(sorted(
                self._bars.list_bars(symbol, as_of=as_of),
                key=lambda item: self._aware(item.event_time),
            ))
            for symbol in set(symbols) | {"0050.TW"}
        }
        def adjusted_close(bar: object) -> Decimal:
            value = getattr(bar, "adjusted_close", None)
            return Decimal(value) if value is not None else Decimal(bar.close)

        def adjusted_open(bar: object) -> Decimal:
            raw_close = Decimal(bar.close)
            if raw_close <= ZERO:
                return Decimal(bar.open)
            return Decimal(bar.open) * adjusted_close(bar) / raw_close

        benchmark_bars = bars_by_symbol.get("0050.TW", ())
        if len(benchmark_bars) < 2:
            return None

        benchmark_calendar = [
            item
            for item in benchmark_bars
            if self._aware(item.event_time) <= self._aware(run.data_end)
        ]
        schedules: dict[date, tuple[datetime, dict[str, float]]] = {}
        for signal_time, weights in allocation_weights:
            execution = next(
                (
                    item
                    for item in benchmark_calendar
                    if self._aware(item.event_time) > signal_time
                ),
                None,
            )
            if execution is not None:
                schedules[self._aware(execution.event_time).date()] = (
                    signal_time,
                    weights,
                )
        if not schedules:
            return None

        first_date = min(schedules)
        calendar = [
            item
            for item in benchmark_calendar
            if self._aware(item.event_time).date() >= first_date
        ]
        bars_by_date = {
            symbol: {
                self._aware(item.event_time).date(): item
                for item in values
            }
            for symbol, values in bars_by_symbol.items()
        }
        initial_cash = Decimal("1000000")
        cash = initial_cash
        positions: dict[str, int] = {}
        last_close: dict[str, Decimal] = {}
        trades: list[AfterHoursSimulationTrade] = []
        curve: list[AfterHoursSimulationPoint] = []
        turnover_amount = ZERO
        peak = initial_cash
        max_drawdown = Decimal("0")
        method_label = {
            "cvar": "CVaR（條件風險值）",
            "equal_weight": "等權重",
            "inverse_volatility": "反波動率",
            "minimum_variance": "最小變異數",
            "mean_variance": "均值－變異數",
            "risk_parity": "風險平價",
            "hierarchical_risk_parity": "階層式風險平價",
            "kelly": "Kelly 資金配置",
            "black_litterman": "Black-Litterman",
        }.get(run.method, run.method)

        for benchmark_bar in calendar:
            event_time = self._aware(benchmark_bar.event_time)
            event_date = event_time.date()
            for symbol in symbols:
                bar = bars_by_date.get(symbol, {}).get(event_date)
                if bar is not None:
                    last_close[symbol] = Decimal(bar.close)

            scheduled = schedules.get(event_date)
            if scheduled is not None:
                signal_time, weights = scheduled
                open_prices = {
                    symbol: Decimal(bar.open)
                    for symbol in symbols
                    if (bar := bars_by_date.get(symbol, {}).get(event_date)) is not None
                    and Decimal(bar.open) > ZERO
                }
                marked_value = sum(
                    (
                        open_prices.get(symbol, last_close.get(symbol, ZERO)) * quantity
                        for symbol, quantity in positions.items()
                    ),
                    ZERO,
                )
                equity_at_open = cash + marked_value
                targets = {
                    symbol: int(
                        (
                            equity_at_open
                            * Decimal(str(min(weights.get(symbol, 0.0), 0.20)))
                            / open_prices[symbol]
                        ).to_integral_value(rounding=ROUND_DOWN)
                    )
                    for symbol in symbols
                    if symbol in open_prices
                }

                # Sell reductions first so their cash can fund the same rebalance.
                for symbol in sorted(set(positions) | set(targets)):
                    current = positions.get(symbol, 0)
                    target = targets.get(symbol, current)
                    quantity = max(current - target, 0)
                    price = open_prices.get(symbol)
                    if quantity <= 0 or price is None:
                        continue
                    gross = self._money(price * quantity)
                    cost = self._estimated_cost("SELL", gross)
                    cash += gross - cost
                    turnover_amount += gross
                    remaining = current - quantity
                    if remaining:
                        positions[symbol] = remaining
                    else:
                        positions.pop(symbol, None)
                    trades.append(AfterHoursSimulationTrade(
                        event_time=event_time,
                        symbol=symbol,
                        side="SELL",
                        quantity=quantity,
                        price=self._money(price),
                        gross_amount=gross,
                        cost=cost,
                        reason=(
                            f"{method_label} 歷史再平衡；"
                            f"目標權重 {weights.get(symbol, 0.0):.1%}；"
                            f"訊號日 {signal_time.astimezone(ZoneInfo('Asia/Taipei')).date()}"
                        ),
                    ))

                for symbol in sorted(targets):
                    current = positions.get(symbol, 0)
                    quantity = max(targets[symbol] - current, 0)
                    price = open_prices[symbol]
                    if quantity <= 0:
                        continue
                    affordable = int(
                        (cash / (price * Decimal("1.006"))).to_integral_value(
                            rounding=ROUND_DOWN
                        )
                    )
                    quantity = min(quantity, affordable)
                    if quantity <= 0:
                        continue
                    gross = self._money(price * quantity)
                    cost = self._estimated_cost("BUY", gross)
                    while quantity > 0 and gross + cost > cash:
                        quantity -= 1
                        gross = self._money(price * quantity)
                        cost = self._estimated_cost("BUY", gross) if quantity else ZERO
                    if quantity <= 0:
                        continue
                    cash -= gross + cost
                    turnover_amount += gross
                    positions[symbol] = current + quantity
                    trades.append(AfterHoursSimulationTrade(
                        event_time=event_time,
                        symbol=symbol,
                        side="BUY",
                        quantity=quantity,
                        price=self._money(price),
                        gross_amount=gross,
                        cost=cost,
                        reason=(
                            f"{method_label} 歷史再平衡；"
                            f"目標權重 {weights.get(symbol, 0.0):.1%}；"
                            f"訊號日 {signal_time.astimezone(ZoneInfo('Asia/Taipei')).date()}"
                        ),
                    ))

            market_value = sum(
                (
                    last_close.get(symbol, ZERO) * quantity
                    for symbol, quantity in positions.items()
                ),
                ZERO,
            )
            equity = self._money(cash + market_value)
            peak = max(peak, equity)
            drawdown = equity / peak - Decimal("1") if peak else Decimal("0")
            max_drawdown = min(max_drawdown, drawdown)
            curve.append(AfterHoursSimulationPoint(
                event_time=event_time,
                equity=equity,
                cash=self._money(cash),
                gross_exposure=float(market_value / equity) if equity else 0.0,
            ))

        if not curve:
            return None
        ending_equity = curve[-1].equity
        total_return = float(ending_equity / initial_cash - Decimal("1"))
        benchmark_start = Decimal(calendar[0].close)
        benchmark_end = Decimal(calendar[-1].close)
        benchmark_return = (
            float(benchmark_end / benchmark_start - Decimal("1"))
            if benchmark_start > ZERO else None
        )
        buys = sum(item.side == "BUY" for item in trades)
        sells = sum(item.side == "SELL" for item in trades)
        active, covered, ready, discovered = self._universe_coverage(
            tuple(self._decisions.list_latest("TW")), as_of
        )
        account = self._paper.overview(now=as_of)
        paper_orders = len(tuple(getattr(account, "orders", ())))
        paper_fills = len(tuple(getattr(account, "fills", ())))
        paper_return = float(getattr(account, "total_return", 0) or 0)
        gate = "候選" if run.promotion_gate == "CANDIDATE" else "研究中"
        research_return = float(run.total_return)
        research_benchmark_return = float(run.benchmark_return)
        research_excess_return = float(run.excess_to_benchmark)
        replay_gap = total_return - research_return
        metrics_consistent = abs(replay_gap) <= 0.05
        enough_history = len(curve) >= 252 and len(trades) >= 20
        outperformed = (
            benchmark_return is not None and total_return > benchmark_return
        )
        status = (
            "候選待向前驗證"
            if (
                metrics_consistent
                and run.promotion_gate == "CANDIDATE"
                and enough_history
                and outperformed
            )
            else "驗證不一致"
            if not metrics_consistent
            else "研究中"
        )
        comparison = (
            f"高於 0050 {total_return - benchmark_return:+.2%}"
            if outperformed and benchmark_return is not None
            else f"低於 0050 {total_return - (benchmark_return or 0.0):+.2%}"
        )
        conclusion = (
            (
                f"同一策略的正式樣本外回測為 {research_return:+.2%}，"
                f"但成交重播為 {total_return:+.2%}，相差 {replay_gap:+.2%}。"
                "兩條計算路徑尚未對齊，已阻擋策略升級；在釐清交易日、"
                "權重生效時間與報酬定義前，不得把較高數字當成策略成效。"
            )
            if not metrics_consistent
            else (
                f"已用 {method_label} 的歷史再平衡權重，按下一交易日開盤模擬 "
                f"{len(trades)} 筆買賣、{len(curve)} 個交易日；"
                f"扣成本報酬 {total_return:+.2%}，{comparison}。"
                f"目前門檻為「{gate}」，所以這是可稽核的歷史模擬，不是實盤保證。"
            )
        )
        return AfterHoursValidation(
            status=status,
            conclusion=conclusion,
            strategy_name=method_label,
            strategy_gate="阻擋" if not metrics_consistent else gate,
            decision_sessions=len(curve),
            decision_symbols=len(symbols),
            candidate_signals=sum(
                weight > 0
                for _, weights in allocation_weights
                for weight in weights.values()
            ),
            simulated_orders=len(trades),
            simulated_round_trips=min(buys, sells),
            total_return=total_return,
            benchmark_return=benchmark_return,
            excess_return=(
                total_return - benchmark_return
                if benchmark_return is not None else None
            ),
            max_drawdown=float(max_drawdown),
            turnover=float(turnover_amount / initial_cash),
            paper_orders=paper_orders,
            paper_fills=paper_fills,
            paper_return=paper_return,
            active_universe=active,
            decision_covered=covered,
            data_ready=ready,
            discovered_common_stocks=discovered,
            simulation_start=curve[0].event_time,
            simulation_end=curve[-1].event_time,
            trades=tuple(reversed(trades)),
            equity_curve=tuple(curve),
            research_return=research_return,
            research_benchmark_return=research_benchmark_return,
            research_excess_return=research_excess_return,
            replay_vs_research_gap=replay_gap,
        )

    def _model_prediction_validation(
        self,
        as_of: datetime,
        model_names: set[str] | None = None,
        policy: ModelReplayPolicy | None = None,
    ) -> AfterHoursValidation | None:
        """Replay persisted out-of-sample ML ranks as executable odd-lot trades."""
        replay_policy = policy or BASELINE_MODEL_REPLAY_POLICY
        if self._models is None:
            return None
        experiments = tuple(self._models.list_runs("TW"))
        if not experiments:
            return None
        if model_names is None:
            selected_experiments = self._select_replay_experiments(
                experiments, {"torch_cuda_mlp"}
            ) or self._select_replay_experiments(
                experiments, {"ridge_linear"}
            )
        else:
            selected_experiments = self._select_replay_experiments(
                experiments, model_names
            )
        if not selected_experiments:
            return None
        experiment_ids = [int(item.id) for item in selected_experiments if item.id is not None]
        loader = getattr(self._models, "list_predictions_for_experiments", None)
        predictions = (
            loader(experiment_ids)
            if callable(loader)
            else [
                item
                for item in self._models.list_predictions("TW")
                if item.experiment_id in experiment_ids
            ]
        )
        experiment_by_id = {
            int(item.id): item for item in selected_experiments if item.id is not None
        }
        # Latest production predictions are stored beside historical OOS rows.
        # Only dates inside each experiment's untouched OOS interval are replayed.
        historical = [
            item
            for item in predictions
            if item.experiment_id in experiment_by_id
            and self._aware(item.event_time)
            <= self._aware(experiment_by_id[item.experiment_id].data_end)
            and self._aware(item.available_time) <= as_of
            and (
                replay_policy.execution_mode != "after_hours_close_proxy"
                or self._aware(item.available_time)
                <= self._aware(item.event_time) + timedelta(hours=1)
            )
        ]
        grouped: dict[datetime, dict[str, list[object]]] = {}
        for item in historical:
            grouped.setdefault(self._aware(item.event_time), {}).setdefault(
                item.symbol, []
            ).append(item)
        signal_dates = [
            event_time
            for event_time, values in sorted(grouped.items())
            if len(values) >= 10
            and (
                replay_policy.evaluation_start is None
                or event_time.date() >= replay_policy.evaluation_start
            )
        ]
        if len(signal_dates) < 20:
            return None

        schedules: list[tuple[datetime, dict[str, float], dict[str, str]]] = []
        for sequence, signal_time in enumerate(signal_dates):
            if sequence % max(1, replay_policy.rebalance_every_sessions) != 0:
                continue
            day = grouped[signal_time]
            listed_symbols: set[str] | None = None
            if self._universe_history is not None:
                listed_symbols = {
                    item.symbol
                    for item in self._universe_history.members_on(
                        signal_time.date(), "TW"
                    )
                }
            scored: list[tuple[float, str, float, float, float]] = []
            positive_count = 0
            for symbol, values in day.items():
                if listed_symbols is not None and symbol not in listed_symbols:
                    continue
                estimates = [float(item.predicted_value) for item in values]
                ranks = [float(item.rank_score) for item in values]
                predicted = sum(estimates) / len(estimates)
                rank = sum(ranks) / len(ranks)
                dispersion = (
                    sum((value - predicted) ** 2 for value in estimates) / len(estimates)
                ) ** 0.5
                if predicted > 0:
                    positive_count += 1
                score = predicted - 0.5 * dispersion
                if (
                    symbol != "0050.TW"
                    and predicted > replay_policy.minimum_predicted_return
                    and rank >= replay_policy.minimum_rank
                ):
                    scored.append((score, symbol, predicted, rank, dispersion))
            scored.sort(reverse=True)
            breadth = positive_count / max(len(day), 1)
            core_weight = (
                replay_policy.strong_breadth_core_weight
                if breadth >= replay_policy.breadth_threshold
                else replay_policy.weak_breadth_core_weight
            )
            core_weight = min(max(core_weight, 0.0), replay_policy.normal_gross_weight)
            active_budget = max(replay_policy.normal_gross_weight - core_weight, 0.0)
            selected = scored[:max(0, replay_policy.active_count)]
            weights = {"0050.TW": core_weight}
            reasons = {
                "0050.TW": (
                    f"0050 核心部位 {core_weight:.0%}；全市場模型正報酬比例 {breadth:.0%}"
                )
            }
            if selected:
                if replay_policy.active_weighting == "score":
                    preferences = [max(item[0], 1e-8) for item in selected]
                else:
                    preferences = [1.0] * len(selected)
                preference_total = sum(preferences)
                for preference, (_, symbol, predicted, rank, dispersion) in zip(
                    preferences, selected, strict=True
                ):
                    active_weight = min(
                        replay_policy.maximum_active_weight,
                        active_budget * preference / max(preference_total, 1e-8),
                    )
                    weights[symbol] = active_weight
                    reasons[symbol] = (
                        f"樣本外五日預測 {predicted:+.2%}；"
                        f"橫斷面排名 {rank:.0%}；模型分歧 {dispersion:.2%}"
                    )
            schedules.append((signal_time, weights, reasons))
        if not schedules:
            return None

        selected_symbols = sorted({
            symbol
            for _, weights, _ in schedules
            for symbol, weight in weights.items()
            if weight > 0
        })
        bulk_loader = getattr(self._bars, "list_bars_for_symbols", None)
        if callable(bulk_loader):
            loaded = bulk_loader(
                selected_symbols,
                start=schedules[0][0] - timedelta(days=14),
                end=min(as_of, schedules[-1][0] + timedelta(days=14)),
                as_of=as_of,
            )
            bars_by_symbol = {
                symbol: tuple(sorted(
                    loaded.get(symbol, ()),
                    key=lambda item: self._aware(item.event_time),
                ))
                for symbol in selected_symbols
            }
        else:
            bars_by_symbol = {
                symbol: tuple(sorted(
                    self._bars.list_bars(symbol, as_of=as_of),
                    key=lambda item: self._aware(item.event_time),
                ))
                for symbol in selected_symbols
            }

        if replay_policy.trend_lookback_sessions > 0:
            history_padding = timedelta(
                days=max(30, replay_policy.trend_lookback_sessions * 2)
            )
            extended_benchmark = tuple(sorted(
                (
                    item
                    for item in self._bars.list_bars("0050.TW", as_of=as_of)
                    if self._aware(item.event_time) >= schedules[0][0] - history_padding
                ),
                key=lambda item: self._aware(item.event_time),
            ))
            if extended_benchmark:
                bars_by_symbol["0050.TW"] = extended_benchmark

        def adjusted_close(bar: object) -> Decimal:
            value = getattr(bar, "adjusted_close", None)
            return Decimal(value) if value is not None else Decimal(bar.close)

        def adjusted_open(bar: object) -> Decimal:
            raw_close = Decimal(bar.close)
            if raw_close <= ZERO:
                return Decimal(bar.open)
            return Decimal(bar.open) * adjusted_close(bar) / raw_close

        benchmark_bars = bars_by_symbol.get("0050.TW", ())
        if len(benchmark_bars) < 2:
            return None
        if replay_policy.trend_lookback_sessions > 0:
            risk_managed_schedules = []
            for signal_time, weights, reasons in schedules:
                known_bars = [
                    item
                    for item in benchmark_bars
                    if self._aware(item.event_time) <= signal_time
                    and self._aware(item.available_time) <= signal_time
                ]
                trend: float | None = None
                lookback = replay_policy.trend_lookback_sessions
                if len(known_bars) > lookback:
                    start_close = adjusted_close(known_bars[-lookback - 1])
                    end_close = adjusted_close(known_bars[-1])
                    if start_close > ZERO:
                        trend = float(end_close / start_close - Decimal("1"))
                gross_target = replay_policy.normal_gross_weight
                state = "正常曝險"
                defensive = bool(
                    trend is not None and trend < replay_policy.trend_threshold
                )
                if defensive:
                    state = "防守曝險"
                original_gross = sum(weights.values())
                if defensive and replay_policy.defensive_core_weight is not None:
                    managed_weights = {
                        symbol: (
                            replay_policy.defensive_core_weight
                            if symbol == "0050.TW"
                            else weight * replay_policy.defensive_active_scale
                        )
                        for symbol, weight in weights.items()
                    }
                    gross_target = sum(managed_weights.values())
                else:
                    if defensive:
                        gross_target = replay_policy.defensive_gross_weight
                    scale = (
                        gross_target / original_gross if original_gross > 0 else 0.0
                    )
                    managed_weights = {
                        symbol: weight * scale for symbol, weight in weights.items()
                    }
                trend_text = "歷史不足" if trend is None else f"{trend:+.1%}"
                managed_reasons = {
                    symbol: (
                        f"{reason}；{lookback} 日趨勢 {trend_text}；"
                        f"{state} {gross_target:.0%}"
                    )
                    for symbol, reason in reasons.items()
                }
                risk_managed_schedules.append(
                    (signal_time, managed_weights, managed_reasons)
                )
            schedules = risk_managed_schedules
        final_signal = schedules[-1][0]
        same_session_execution = (
            replay_policy.execution_mode == "after_hours_close_proxy"
        )
        calendar = [
            item
            for item in benchmark_bars
            if (
                schedules[0][0] <= self._aware(item.event_time)
                if same_session_execution
                else schedules[0][0] < self._aware(item.event_time)
            )
            and self._aware(item.event_time)
            <= min(as_of, final_signal + timedelta(days=12))
        ]
        if not calendar:
            return None
        execution_schedules: dict[date, tuple[datetime, dict[str, float], dict[str, str]]] = {}
        for signal_time, weights, reasons in schedules:
            if same_session_execution:
                execution = next(
                    (
                        item for item in benchmark_bars
                        if self._aware(item.event_time).date()
                        == signal_time.date()
                    ),
                    None,
                )
            else:
                execution = next(
                    (
                        item for item in benchmark_bars
                        if self._aware(item.event_time) > signal_time
                    ),
                    None,
                )
            if execution is not None:
                execution_schedules[self._aware(execution.event_time).date()] = (
                    signal_time, weights, reasons
                )
        if not execution_schedules:
            return None
        first_execution = min(execution_schedules)
        calendar = [
            item for item in calendar
            if self._aware(item.event_time).date() >= first_execution
        ]
        bars_by_date = {
            symbol: {
                self._aware(item.event_time).date(): item for item in values
            }
            for symbol, values in bars_by_symbol.items()
        }

        initial_cash = Decimal("1000000")

        benchmark_base_close = adjusted_close(calendar[0])
        cash = initial_cash
        positions: dict[str, int] = {}
        last_close: dict[str, Decimal] = {}
        trades: list[AfterHoursSimulationTrade] = []
        curve: list[AfterHoursSimulationPoint] = []
        turnover_amount = ZERO
        peak = initial_cash
        guard_peak = initial_cash
        guard_rebalances_remaining = 0
        max_drawdown = Decimal("0")
        candidate_signals = sum(
            symbol != "0050.TW"
            for _, weights, _ in schedules
            for symbol in weights
        )

        for benchmark_bar in calendar:
            event_time = self._aware(benchmark_bar.event_time)
            event_date = event_time.date()
            for symbol in selected_symbols:
                bar = bars_by_date.get(symbol, {}).get(event_date)
                if bar is not None:
                    last_close[symbol] = adjusted_close(bar)
            scheduled = execution_schedules.get(event_date)
            if scheduled is not None:
                signal_time, weights, reasons = scheduled
                execution_prices = {
                    symbol: (
                        adjusted_close(bar)
                        if same_session_execution
                        else adjusted_open(bar)
                    )
                    for symbol in selected_symbols
                    if (bar := bars_by_date.get(symbol, {}).get(event_date)) is not None
                    and (
                        adjusted_close(bar) > ZERO
                        if same_session_execution
                        else Decimal(bar.open) > ZERO
                    )
                }
                marked = sum(
                    execution_prices.get(symbol, last_close.get(symbol, ZERO)) * quantity
                    for symbol, quantity in positions.items()
                )
                equity_at_execution = cash + marked
                if (
                    replay_policy.drawdown_trigger is not None
                    and guard_rebalances_remaining <= 0
                    and guard_peak > ZERO
                    and float(equity_at_execution / guard_peak - Decimal("1"))
                    <= replay_policy.drawdown_trigger
                ):
                    guard_rebalances_remaining = max(
                        1, replay_policy.drawdown_guard_rebalances
                    )
                    guard_peak = equity_at_execution
                if guard_rebalances_remaining > 0:
                    scheduled_gross = sum(weights.values())
                    guard_scale = (
                        replay_policy.drawdown_guard_gross_weight / scheduled_gross
                        if scheduled_gross > 0 else 0.0
                    )
                    weights = {
                        symbol: weight * guard_scale
                        for symbol, weight in weights.items()
                    }
                    reasons = {
                        symbol: (
                            f"{reason}；組合跌幅保護啟動，總曝險 "
                            f"{replay_policy.drawdown_guard_gross_weight:.0%}"
                        )
                        for symbol, reason in reasons.items()
                    }
                    guard_rebalances_remaining -= 1
                targets = {
                    symbol: int(
                        (
                            equity_at_execution
                            * Decimal(str(weight))
                            / execution_prices[symbol]
                        )
                        .to_integral_value(rounding=ROUND_DOWN)
                    )
                    for symbol, weight in weights.items()
                    if symbol in execution_prices and weight > 0
                }
                if replay_policy.minimum_trade_weight > 0 and equity_at_execution > ZERO:
                    for symbol in set(positions) | set(targets):
                        price = execution_prices.get(symbol)
                        if price is None or price <= ZERO:
                            continue
                        current_quantity = positions.get(symbol, 0)
                        target_quantity = targets.get(symbol, 0)
                        trade_weight = float(
                            price * abs(target_quantity - current_quantity)
                            / equity_at_execution
                        )
                        if trade_weight < replay_policy.minimum_trade_weight:
                            if current_quantity > 0:
                                targets[symbol] = current_quantity
                            else:
                                targets.pop(symbol, None)
                for symbol in sorted(set(positions) | set(targets)):
                    current = positions.get(symbol, 0)
                    target = targets.get(symbol, 0)
                    quantity = max(current - target, 0)
                    price = execution_prices.get(symbol)
                    if quantity <= 0 or price is None:
                        continue
                    gross = self._money(price * Decimal("0.999") * quantity)
                    cost = self._estimated_cost("SELL", gross)
                    cash += gross - cost
                    turnover_amount += gross
                    remaining = current - quantity
                    if remaining:
                        positions[symbol] = remaining
                    else:
                        positions.pop(symbol, None)
                    trades.append(AfterHoursSimulationTrade(
                        event_time=(
                            signal_time + timedelta(hours=1)
                            if same_session_execution else event_time
                        ), symbol=symbol, side="SELL",
                        quantity=quantity, price=self._money(price * Decimal("0.999")),
                        gross_amount=gross, cost=cost,
                        reason=(
                            f"AI 五日再平衡減碼；訊號日 {signal_time.date()}；"
                            f"新目標權重 {weights.get(symbol, 0.0):.1%}；"
                            + (
                                "13:30 收盤價加滑價代理 14:30 盤後零股成交"
                                if same_session_execution
                                else "下一交易日開盤成交"
                            )
                        ),
                    ))
                for symbol in sorted(targets):
                    current = positions.get(symbol, 0)
                    quantity = max(targets[symbol] - current, 0)
                    price = execution_prices[symbol] * Decimal("1.001")
                    if quantity <= 0:
                        continue
                    affordable = int(
                        (cash / (price * Decimal("1.006"))).to_integral_value(
                            rounding=ROUND_DOWN
                        )
                    )
                    quantity = min(quantity, affordable)
                    if quantity <= 0:
                        continue
                    gross = self._money(price * quantity)
                    cost = self._estimated_cost("BUY", gross)
                    while quantity > 0 and gross + cost > cash:
                        quantity -= 1
                        gross = self._money(price * quantity)
                        cost = self._estimated_cost("BUY", gross) if quantity else ZERO
                    if quantity <= 0:
                        continue
                    cash -= gross + cost
                    turnover_amount += gross
                    positions[symbol] = current + quantity
                    trades.append(AfterHoursSimulationTrade(
                        event_time=(
                            signal_time + timedelta(hours=1)
                            if same_session_execution else event_time
                        ), symbol=symbol, side="BUY",
                        quantity=quantity, price=self._money(price),
                        gross_amount=gross, cost=cost,
                        reason=(
                            f"AI 五日再平衡買進；{reasons.get(symbol, '模型候選')}；"
                            + (
                                "13:30 收盤價加滑價代理 14:30 盤後零股成交"
                                if same_session_execution
                                else "下一交易日開盤成交"
                            )
                        ),
                    ))
            market_value = sum(
                last_close.get(symbol, ZERO) * quantity
                for symbol, quantity in positions.items()
            )
            equity = self._money(cash + market_value)
            peak = max(peak, equity)
            guard_peak = max(guard_peak, equity)
            drawdown = equity / peak - Decimal("1") if peak else Decimal("0")
            max_drawdown = min(max_drawdown, drawdown)
            curve.append(AfterHoursSimulationPoint(
                event_time=event_time, equity=equity, cash=self._money(cash),
                gross_exposure=float(market_value / equity) if equity else 0.0,
                benchmark_equity=(
                    self._money(
                        initial_cash * adjusted_close(benchmark_bar) / benchmark_base_close
                    )
                    if benchmark_base_close > ZERO else None
                ),
            ))
        if len(curve) < 2:
            return None
        total_return = float(curve[-1].equity / initial_cash - Decimal("1"))
        benchmark_start = adjusted_close(calendar[0])
        benchmark_end = adjusted_close(calendar[-1])
        benchmark_return = (
            float(benchmark_end / benchmark_start - Decimal("1"))
            if benchmark_start > ZERO else None
        )
        excess = (
            total_return - benchmark_return if benchmark_return is not None else None
        )
        buys = sum(item.side == "BUY" for item in trades)
        sells = sum(item.side == "SELL" for item in trades)
        regime_results = self._regime_results(curve)
        regime_ready = len(regime_results) == 4 and all(
            item.passed for item in regime_results
        )
        survivorship_safe = True
        if self._universe_history is not None:
            survivorship_safe = self._universe_history.audit(
                curve[0].event_time.date(), "TW"
            ).survivorship_safe
        enough_history = len(curve) >= 756 and len(trades) >= 30
        outperformed = excess is not None and excess > 0
        risk_ok = max_drawdown >= Decimal("-0.25")
        passed = (
            enough_history and outperformed and risk_ok
            and regime_ready and survivorship_safe
        )
        status = "候選待向前驗證" if passed else "研究中"
        comparison = (
            f"比 0050 多賺 {excess:+.2%}"
            if excess is not None and excess >= 0
            else f"比 0050 少賺 {abs(excess or 0):.2%}"
        )
        failed_reasons: list[str] = []
        if not enough_history:
            failed_reasons.append("歷史少於 756 個交易日或 30 筆成交")
        if not outperformed:
            failed_reasons.append("扣成本後沒有贏過 0050")
        if not risk_ok:
            failed_reasons.append(f"最大跌幅 {float(max_drawdown):.2%} 超過 25% 上限")
        if not regime_ready:
            failed_labels = "、".join(
                item.label for item in regime_results if not item.passed
            ) or "市場狀態樣本不足"
            failed_reasons.append(f"未在所有市場狀態勝出：{failed_labels}")
        if not survivorship_safe:
            failed_reasons.append("歷史股票池仍缺精確上市／下市資料，可能高估績效")
        conclusion = (
            f"AI 模型樣本外預測產生 {len(trades):,} 筆可重播買賣，"
            f"共 {len(curve):,} 個交易日；扣成本報酬 {total_return:+.2%}，"
            f"同期 0050 {benchmark_return:+.2%}，{comparison}。"
            + (
                "歷史門檻通過，下一步是累積真實時間向前模擬；目前仍不是實盤保證。"
                if passed
                else "不升級：" + "；".join(failed_reasons) + "。"
            )
        )
        active, covered, ready, discovered = self._universe_coverage(
            tuple(self._decisions.list_latest("TW")), as_of
        )
        account = self._paper.overview(now=as_of)
        return AfterHoursValidation(
            status=status, conclusion=conclusion,
            strategy_name=replay_policy.name,
            strategy_gate="候選" if passed else "研究中",
            decision_sessions=len(curve), decision_symbols=len(selected_symbols),
            candidate_signals=candidate_signals, simulated_orders=len(trades),
            simulated_round_trips=min(buys, sells), total_return=total_return,
            benchmark_return=benchmark_return, excess_return=excess,
            max_drawdown=float(max_drawdown),
            turnover=float(turnover_amount / initial_cash),
            paper_orders=len(tuple(getattr(account, "orders", ()))),
            paper_fills=len(tuple(getattr(account, "fills", ()))),
            paper_return=float(getattr(account, "total_return", 0) or 0),
            active_universe=active, decision_covered=covered, data_ready=ready,
            discovered_common_stocks=discovered,
            simulation_start=curve[0].event_time,
            simulation_end=curve[-1].event_time,
            trades=tuple(reversed(trades[-500:])), equity_curve=tuple(curve),
            regime_results=regime_results,
            execution_assumption=(
                "13:30 收盤價加 10 bps 滑價，代理 14:30 盤後零股成交；"
                "尚無足夠實際盤後零股成交價與未成交紀錄"
                if same_session_execution
                else "訊號後下一交易日開盤價加 10 bps 滑價"
            ),
        )

    def _select_replay_experiments(
        self,
        experiments: tuple[object, ...] | list[object],
        model_names: set[str],
    ) -> tuple[object, ...]:
        """Select the newest replayable version without hiding valid history.

        A newly saved challenger with negative rank IC is still important audit
        evidence, but it must not replace an older positive, sufficiently covered
        experiment and make the historical-replay page appear empty.
        """
        coverage_loader = getattr(self._models, "prediction_coverage", None)
        latest_by_model: dict[str, object] = {}
        for experiment in experiments:
            if (
                experiment.id is None
                or experiment.model_name == "historical_mean"
                or experiment.model_name not in model_names
                or (experiment.rank_ic or 0) <= 0
                or (
                    callable(coverage_loader)
                    and coverage_loader(int(experiment.id))[1] < 20
                )
            ):
                continue
            current = latest_by_model.get(experiment.model_name)
            if current is None or self._aware(experiment.computed_at) > self._aware(
                current.computed_at
            ):
                latest_by_model[experiment.model_name] = experiment
        return tuple(latest_by_model.values())

    def model_oos_validation(
        self,
        now: datetime | None = None,
        model_names: set[str] | None = None,
        policy: ModelReplayPolicy | None = None,
    ) -> AfterHoursValidation | None:
        """Return the longer historical ML replay separately from saved decisions."""
        return self._model_prediction_validation(
            self._aware(now or datetime.now(UTC)),
            model_names=model_names,
            policy=policy,
        )

    def model_forward_tracking(self, now: datetime | None = None) -> dict[str, object] | None:
        """Describe the locked research version and evidence accumulated after it."""
        if self._models is None:
            return None
        experiments = [
            item for item in self._models.list_runs("TW")
            if item.id is not None
            and item.model_name in {"torch_cuda_mlp", "ridge_linear"}
        ]
        if not experiments:
            return None
        model = max(
            experiments,
            key=lambda item: (
                item.promotion_gate == "CANDIDATE",
                item.model_name == "torch_cuda_mlp",
                self._aware(item.computed_at),
            ),
        )
        completed_at = self._aware(model.computed_at)
        taipei = ZoneInfo("Asia/Taipei")
        tracked_sessions = {
            self._aware(item.event_time).astimezone(taipei).date()
            for item in self._decision_history()
            if self._aware(item.event_time) > completed_at
        }
        paper = self._paper.overview(now=self._aware(now or datetime.now(UTC)))
        orders = [
            item for item in getattr(paper, "orders", ())
            if self._aware(item.submitted_at) > completed_at
        ]
        fills = [
            item for item in getattr(paper, "fills", ())
            if self._aware(item.executed_at) > completed_at
        ]
        return {
            "id": int(model.id),
            "name": model.model_name,
            "label_name": model.label_name,
            "version": model.model_version,
            "completed_at": completed_at,
            "tracking_started_at": completed_at,
            "tracked_sessions": len(tracked_sessions),
            "paper_orders": len(orders),
            "paper_fills": len(fills),
            "target_sessions": 252,
            "target_fills": 20,
            "estimated_minimum_end": completed_at + timedelta(days=365),
            "robust_target_sessions": 504,
        }

    def model_validation_for_page(self) -> AfterHoursValidation | None:
        """Warm the multi-year model replay without delaying the page response."""
        with self._validation_lock:
            if (
                self._model_validation_cache is not None
                and time.monotonic() - self._model_validation_cache[0] < 900
            ):
                return self._model_validation_cache[1]
            # An expired snapshot is not usable and must not prevent the
            # background worker from starting again.
            self._model_validation_cache = None
            if (
                (
                    self._model_validation_job is None
                    or not self._model_validation_job.is_alive()
                )
                and not self._model_validation_pending
            ):
                self._model_validation_error = None
                self._model_validation_pending = True
                timer = threading.Timer(0.1, self._start_model_validation_job)
                timer.daemon = True
                timer.start()
        return None

    def model_validation_status(self) -> dict[str, object]:
        with self._validation_lock:
            ready = (
                self._model_validation_cache is not None
                and time.monotonic() - self._model_validation_cache[0] < 900
            )
            running = self._model_validation_pending or bool(
                self._model_validation_job is not None
                and self._model_validation_job.is_alive()
            )
            available = bool(
                ready
                and self._model_validation_cache is not None
                and self._model_validation_cache[1] is not None
            )
            return {
                "ready": ready,
                "running": running,
                "available": available,
                "status": (
                    "完成" if available else
                    "沒有足夠預測" if ready else
                    "失敗" if self._model_validation_error else
                    "計算中" if running else "尚未啟動"
                ),
                "error": self._model_validation_error,
            }

    def _start_model_validation_job(self) -> None:
        with self._validation_lock:
            self._model_validation_pending = False
            if (
                self._model_validation_cache is not None
                and time.monotonic() - self._model_validation_cache[0] < 900
            ):
                return
            self._model_validation_cache = None
            self._model_validation_job = threading.Thread(
                target=self._warm_model_validation_cache,
                name="after-hours-model-validation",
                daemon=True,
            )
            self._model_validation_job.start()

    def _warm_model_validation_cache(self) -> None:
        try:
            value = self._model_prediction_validation(
                datetime.now(UTC),
                policy=BENCHMARK_AWARE_LOW_TURNOVER_MODEL_REPLAY_POLICY,
            )
        except Exception as exc:  # background work must never take down the web app
            with self._validation_lock:
                self._model_validation_error = str(exc)
            return
        with self._validation_lock:
            self._model_validation_cache = (time.monotonic(), value)
            self._model_validation_error = None

    @staticmethod
    def _regime_results(
        curve: list[AfterHoursSimulationPoint],
    ) -> tuple[AfterHoursRegimeResult, ...]:
        if len(curve) < 127 or any(item.benchmark_equity is None for item in curve):
            return ()
        strategy = [float(item.equity) for item in curve]
        benchmark = [float(item.benchmark_equity or 0) for item in curve]
        strategy_returns = [0.0] + [
            strategy[index] / strategy[index - 1] - 1
            for index in range(1, len(strategy))
        ]
        benchmark_returns = [0.0] + [
            benchmark[index] / benchmark[index - 1] - 1
            for index in range(1, len(benchmark))
        ]
        buckets: dict[str, list[int]] = {
            "bull": [], "bear": [], "sideways": [], "high_volatility": [],
        }
        for index in range(126, len(curve)):
            trend = benchmark[index] / benchmark[index - 126] - 1
            recent = benchmark_returns[max(1, index - 19):index + 1]
            mean = sum(recent) / len(recent)
            annual_volatility = (
                sum((value - mean) ** 2 for value in recent) / len(recent)
            ) ** 0.5 * math.sqrt(252)
            if trend > 0.08:
                buckets["bull"].append(index)
            elif trend < -0.08:
                buckets["bear"].append(index)
            else:
                buckets["sideways"].append(index)
            if annual_volatility >= 0.25:
                buckets["high_volatility"].append(index)
        labels = {
            "bull": "牛市", "bear": "熊市", "sideways": "盤整",
            "high_volatility": "高波動",
        }
        output: list[AfterHoursRegimeResult] = []
        for key, indexes in buckets.items():
            strategy_return = math.prod(1 + strategy_returns[i] for i in indexes) - 1
            benchmark_return = math.prod(1 + benchmark_returns[i] for i in indexes) - 1
            excess = strategy_return - benchmark_return
            output.append(AfterHoursRegimeResult(
                key=key, label=labels[key], session_count=len(indexes),
                strategy_return=strategy_return,
                benchmark_return=benchmark_return,
                excess_return=excess,
                passed=len(indexes) >= 20 and excess > 0,
            ))
        return tuple(output)

    def _compute_validation(self, now: datetime | None = None) -> AfterHoursValidation:
        """Walk historical saved decisions forward to the next session open.

        This validates this portfolio rule itself. It intentionally does not reuse
        the single-stock backtest headline as proof of portfolio performance.
        """
        as_of = self._aware(now or datetime.now(UTC))
        history = self._decision_history()
        # What the user actually needs is a replay of the decisions this system
        # saved on each historical date.  Prefer that evidence whenever it
        # exists; model-zoo and portfolio artifacts remain fallbacks for a fresh
        # database that has not accumulated daily decisions yet.
        if not history:
            model_validation = self._model_prediction_validation(as_of)
            if model_validation is not None:
                return model_validation
            portfolio_replay = self._portfolio_validation(as_of)
            if portfolio_replay is not None:
                return portfolio_replay
            portfolio_validation = self._canonical_portfolio_validation(as_of)
            if portfolio_validation is not None:
                return portfolio_validation
        symbols = sorted({item.symbol for item in history})
        active, covered, ready, discovered = self._universe_coverage(
            tuple(self._decisions.list_latest("TW")), as_of
        )
        account = self._paper.overview(now=as_of)
        paper_orders = len(tuple(getattr(account, "orders", ())))
        paper_fills = len(tuple(getattr(account, "fills", ())))
        paper_return = float(getattr(account, "total_return", 0) or 0)

        if not history:
            return AfterHoursValidation(
                status="未驗證",
                conclusion="沒有歷史決策可供組合模擬，不能宣稱策略有效。",
                strategy_name="每日決策重播",
                strategy_gate="資料不足",
                decision_sessions=0, decision_symbols=0, candidate_signals=0,
                simulated_orders=0, simulated_round_trips=0, total_return=0.0,
                benchmark_return=None, excess_return=None, max_drawdown=0.0,
                turnover=0.0, paper_orders=paper_orders, paper_fills=paper_fills,
                paper_return=paper_return, active_universe=active,
                decision_covered=covered, data_ready=ready,
                discovered_common_stocks=discovered, simulation_start=None,
                simulation_end=None, trades=(), equity_curve=(),
            )

        taipei = ZoneInfo("Asia/Taipei")
        decisions_by_date: dict[date, list[object]] = {}
        for item in history:
            decisions_by_date.setdefault(
                self._aware(item.event_time).astimezone(taipei).date(), []
            ).append(item)
        decisions_by_session = {
            max(self._aware(item.event_time) for item in values): values
            for values in decisions_by_date.values()
        }
        sessions = sorted(decisions_by_session)
        requested_symbols = sorted(set(symbols) | {"0050.TW"})
        bulk_loader = getattr(self._bars, "list_bars_for_symbols", None)
        if callable(bulk_loader):
            window_start = sessions[0] - timedelta(days=14)
            window_end = min(as_of, sessions[-1] + timedelta(days=14))
            loaded = bulk_loader(
                requested_symbols,
                start=window_start,
                end=window_end,
                as_of=as_of,
            )
            bars_by_symbol = {
                symbol: tuple(sorted(
                    loaded.get(symbol, ()), key=lambda item: self._aware(item.event_time)
                ))
                for symbol in requested_symbols
            }
        else:
            bars_by_symbol = {
                symbol: tuple(self._bars.list_bars(symbol, as_of=as_of))
                for symbol in requested_symbols
            }
        initial_cash = Decimal("1000000")
        cash = initial_cash
        positions: dict[str, int] = {}
        trades: list[AfterHoursSimulationTrade] = []
        curve: list[AfterHoursSimulationPoint] = []
        turnover_amount = ZERO
        peak = initial_cash
        max_drawdown = Decimal("0")

        def bar_at_or_before(symbol: str, event_time: datetime):
            return next(
                (
                    item for item in reversed(bars_by_symbol.get(symbol, ()))
                    if self._aware(item.event_time) <= event_time
                ),
                None,
            )

        def next_bar(symbol: str, event_time: datetime):
            return next(
                (
                    item for item in bars_by_symbol.get(symbol, ())
                    if self._aware(item.event_time) > event_time
                ),
                None,
            )

        for decision_time, day_decisions in sorted(decisions_by_session.items()):
            # A decision can only be evaluated after a later market session
            # exists.  Weekends and the newest unobserved decision stay saved,
            # but must not create a fake zero-return point.
            market_execution = next_bar("0050.TW", decision_time)
            if market_execution is None:
                continue
            decision_map = {item.symbol: item for item in day_decisions}

            # Execute explicit risk exits at the next open.
            for symbol, quantity in tuple(positions.items()):
                decision = decision_map.get(symbol)
                status = self._status(decision.status) if decision else "資料不足"
                if status not in {"避免", "資料不足"}:
                    continue
                execution = next_bar(symbol, decision_time)
                if execution is None:
                    continue
                price = Decimal(execution.open) * Decimal("0.999")
                gross = self._money(price * quantity)
                cost = self._estimated_cost("SELL", gross)
                cash += gross - cost
                turnover_amount += gross
                positions.pop(symbol, None)
                trades.append(AfterHoursSimulationTrade(
                    event_time=self._aware(execution.event_time), symbol=symbol,
                    side="SELL", quantity=quantity, price=self._money(price),
                    gross_amount=gross, cost=cost,
                    reason="決策轉為避免或資料不足，下一交易日開盤退出",
                ))

            candidates = sorted(
                (
                    item for item in day_decisions
                    if self._status(item.status) in {"候選", "觀察"}
                    and not self._execution_blockers(item)
                    and (item.predicted_return_5d or 0) > 0
                    and (
                        self._status(item.status) == "候選"
                        or ((item.model_rank or 0) >= 0.65 and item.score >= 0.65)
                    )
                ),
                key=lambda item: (
                    (item.predicted_return_5d or 0)
                    - 0.5 * (item.prediction_dispersion or 0)
                    + 0.05 * item.score
                ),
                reverse=True,
            )
            marked_value = sum(
                (
                    Decimal(bar.close) * positions.get(symbol, 0)
                    for symbol in positions
                    if (bar := bar_at_or_before(symbol, decision_time)) is not None
                ),
                ZERO,
            )
            equity = cash + marked_value
            gross_limit = equity * Decimal("0.80")
            current_gross = marked_value
            buy_count = 0
            for decision in candidates:
                if buy_count >= MAX_DAILY_BUYS:
                    break
                execution = next_bar(decision.symbol, decision_time)
                if execution is None:
                    continue
                price = Decimal(execution.open) * Decimal("1.001")
                research_only = self._status(decision.status) != "候選"
                target_weight = min(
                    float(decision.suggested_weight) or RESEARCH_SIMULATION_WEIGHT,
                    RESEARCH_SIMULATION_WEIGHT if research_only else 0.20,
                )
                target_value = equity * Decimal(str(target_weight))
                current_quantity = positions.get(decision.symbol, 0)
                target_quantity = int(
                    (target_value / price).to_integral_value(rounding=ROUND_DOWN)
                )
                quantity = max(target_quantity - current_quantity, 0)
                remaining_gross = max(gross_limit - current_gross, ZERO)
                affordable = min(
                    int((cash / (price * Decimal("1.003"))).to_integral_value(
                        rounding=ROUND_DOWN
                    )),
                    int((remaining_gross / price).to_integral_value(
                        rounding=ROUND_DOWN
                    )),
                )
                quantity = min(quantity, affordable)
                if quantity <= 0:
                    continue
                gross = self._money(price * quantity)
                cost = self._estimated_cost("BUY", gross)
                cash -= gross + cost
                current_gross += gross
                turnover_amount += gross
                positions[decision.symbol] = current_quantity + quantity
                buy_count += 1
                trades.append(AfterHoursSimulationTrade(
                    event_time=self._aware(execution.event_time),
                    symbol=decision.symbol, side="BUY", quantity=quantity,
                    price=self._money(price), gross_amount=gross, cost=cost,
                    reason=(
                        "研究模擬：排名通過且無資料風險，以 5% 小部位於下一開盤買進"
                        if research_only else
                        "通過候選與風險閘門，依目標權重於下一開盤買進"
                    ),
                ))

            point_time = self._aware(market_execution.event_time)
            market_value = sum(
                (
                    Decimal(bar.close) * quantity
                    for symbol, quantity in positions.items()
                    if (bar := bar_at_or_before(symbol, point_time)) is not None
                ),
                ZERO,
            )
            equity = self._money(cash + market_value)
            peak = max(peak, equity)
            drawdown = equity / peak - Decimal("1") if peak else Decimal("0")
            max_drawdown = min(max_drawdown, drawdown)
            curve.append(AfterHoursSimulationPoint(
                event_time=point_time, equity=equity, cash=self._money(cash),
                gross_exposure=float(market_value / equity) if equity else 0.0,
            ))

        ending_equity = curve[-1].equity if curve else initial_cash
        total_return = float(ending_equity / initial_cash - Decimal("1"))
        benchmark_bars = bars_by_symbol.get("0050.TW", ())
        benchmark_start = (
            bar_at_or_before("0050.TW", sessions[0]) if sessions else None
        )
        benchmark_end = (
            bar_at_or_before("0050.TW", curve[-1].event_time)
            if curve else None
        )
        benchmark_return = (
            float(Decimal(benchmark_end.close) / Decimal(benchmark_start.close) - Decimal("1"))
            if benchmark_start is not None and benchmark_end is not None
            and Decimal(benchmark_start.close) > ZERO
            else None
        )
        if benchmark_start is not None and Decimal(benchmark_start.close) > ZERO:
            benchmark_base = Decimal(benchmark_start.close)
            curve = [
                AfterHoursSimulationPoint(
                    event_time=point.event_time,
                    equity=point.equity,
                    cash=point.cash,
                    gross_exposure=point.gross_exposure,
                    benchmark_equity=(
                        self._money(
                            initial_cash
                            * Decimal(benchmark_bar.close)
                            / benchmark_base
                        )
                        if (
                            benchmark_bar := bar_at_or_before(
                                "0050.TW", point.event_time
                            )
                        ) is not None
                        else None
                    ),
                )
                for point in curve
            ]
        buys = sum(item.side == "BUY" for item in trades)
        sells = sum(item.side == "SELL" for item in trades)
        candidate_signals = sum(
            self._status(item.status) in {"候選", "觀察"}
            and not self._execution_blockers(item)
            and (item.predicted_return_5d or 0) > 0
            and (
                self._status(item.status) == "候選"
                or ((item.model_rank or 0) >= 0.65 and item.score >= 0.65)
            )
            for item in history
        )
        evaluated_sessions = len(curve)
        if evaluated_sessions < 60 or len(trades) < 20:
            status = "未驗證"
            conclusion = (
                f"只有 {evaluated_sessions} 個已有後續行情的決策日、"
                f"{len(trades)} 筆模擬委託，"
                "樣本不足，現在不能判定策略有效或可投入真實資金。"
            )
        else:
            status = "初步可評估"
            excess = total_return - (benchmark_return or 0.0)
            conclusion = (
                "扣除成本後暫時優於 0050，但仍須跨市場狀態與更長期間驗證。"
                if excess > 0 and max_drawdown > Decimal("-0.20")
                else "扣除成本後未證明優於 0050，策略不得晉級。"
            )
        return AfterHoursValidation(
            status=status, conclusion=conclusion,
            strategy_name="每日決策重播",
            strategy_gate="初步可評估" if status == "初步可評估" else "資料不足",
            decision_sessions=evaluated_sessions, decision_symbols=len(symbols),
            candidate_signals=candidate_signals, simulated_orders=len(trades),
            simulated_round_trips=min(buys, sells), total_return=total_return,
            benchmark_return=benchmark_return,
            excess_return=(
                total_return - benchmark_return
                if benchmark_return is not None else None
            ),
            max_drawdown=float(max_drawdown),
            turnover=float(turnover_amount / initial_cash),
            paper_orders=paper_orders, paper_fills=paper_fills,
            paper_return=paper_return, active_universe=active,
            decision_covered=covered, data_ready=ready,
            discovered_common_stocks=discovered,
            simulation_start=sessions[0] if sessions else None,
            simulation_end=curve[-1].event_time if curve else sessions[-1],
            trades=tuple(reversed(trades[-100:])),
            equity_curve=tuple(curve),
        )

    def validation(self, now: datetime | None = None) -> AfterHoursValidation:
        """Return a recent immutable validation snapshot for UI reads.

        The historical replay is intentionally expensive. A page refresh must not
        recompute the same 211 trades while its source tables are unchanged.
        Explicit as-of calls remain uncached for deterministic research tests.
        """
        if now is not None:
            return self._compute_validation(now)
        with self._validation_lock:
            if (
                self._validation_cache is not None
                and time.monotonic() - self._validation_cache[0] < 300
            ):
                return self._validation_cache[1]
            value = self._compute_validation()
            self._validation_cache = (time.monotonic(), value)
            return value

    def validation_for_page(self) -> AfterHoursValidation:
        """Return a fast page snapshot and warm the expensive replay in background."""
        with self._validation_lock:
            if (
                self._validation_cache is not None
                and time.monotonic() - self._validation_cache[0] < 300
            ):
                return self._validation_cache[1]
            # Drop stale values before scheduling. Previously the timer fired,
            # saw a non-None (but expired) cache, and exited without doing work.
            self._validation_cache = None
            if (
                (self._validation_job is None or not self._validation_job.is_alive())
                and not self._validation_pending
            ):
                self._validation_error = None
                self._validation_pending = True
                timer = threading.Timer(0.1, self._start_validation_job)
                timer.daemon = True
                timer.start()
        decisions = tuple(self._decisions.list_latest("TW"))
        latest_time = max((self._aware(item.event_time) for item in decisions), default=None)
        return AfterHoursValidation(
            status="建立中" if self._validation_error is None else "建立失敗",
            conclusion=(
                "歷史驗證正在背景建立；頁面先顯示今日盤後決策，完成後重新整理即可看到完整報酬與交易歷程。"
                if self._validation_error is None
                else f"歷史驗證建立失敗：{self._validation_error}"
            ),
            strategy_name="每日決策重播",
            strategy_gate="研究中",
            decision_sessions=1 if decisions else 0,
            decision_symbols=len(decisions),
            candidate_signals=sum(self._status(item.status) == "候選" for item in decisions),
            simulated_orders=0,
            simulated_round_trips=0,
            total_return=0.0,
            benchmark_return=None,
            excess_return=None,
            max_drawdown=0.0,
            turnover=0.0,
            paper_orders=0,
            paper_fills=0,
            paper_return=0.0,
            active_universe=0,
            decision_covered=len(decisions),
            data_ready=0,
            discovered_common_stocks=0,
            simulation_start=latest_time,
            simulation_end=latest_time,
            trades=(),
            equity_curve=(),
        )

    def validation_status(self) -> dict[str, object]:
        """Expose honest background state for the page poller."""
        with self._validation_lock:
            ready = (
                self._validation_cache is not None
                and time.monotonic() - self._validation_cache[0] < 300
            )
            running = self._validation_pending or bool(
                self._validation_job is not None and self._validation_job.is_alive()
            )
            return {
                "ready": ready,
                "running": running,
                "status": (
                    "完成" if ready else "失敗" if self._validation_error else
                    "計算中" if running else "尚未啟動"
                ),
                "error": self._validation_error,
            }

    def _start_validation_job(self) -> None:
        with self._validation_lock:
            self._validation_pending = False
            if (
                self._validation_cache is not None
                and time.monotonic() - self._validation_cache[0] < 300
            ):
                return
            self._validation_cache = None
            self._validation_job = threading.Thread(
                target=self._warm_validation_cache,
                name="after-hours-validation",
                daemon=True,
            )
            self._validation_job.start()

    def _warm_validation_cache(self) -> None:
        try:
            value = self._compute_validation()
        except Exception as exc:  # background work must never take down the web app
            with self._validation_lock:
                self._validation_error = str(exc)
            return
        with self._validation_lock:
            self._validation_cache = (time.monotonic(), value)
            self._validation_error = None

    def invalidate_validation_cache(self) -> None:
        with self._validation_lock:
            self._validation_cache = None
            self._validation_error = None
        with self._plan_lock:
            self._plan_cache = None

    def plan_for_page(self) -> AfterHoursPlan:
        """Reuse the same plan while its decisions and paper account are unchanged."""
        with self._plan_lock:
            if (
                self._plan_cache is not None
                and time.monotonic() - self._plan_cache[0] < 300
            ):
                return self._plan_cache[1]
            value = self.generate()
            self._plan_cache = (time.monotonic(), value)
            return value

    def submit_to_paper(
        self, plan: AfterHoursPlan | None = None, now: datetime | None = None
    ) -> AfterHoursPaperSubmission:
        """Persist today's approved drafts in the paper broker, idempotently."""
        submitted_at = self._aware(now or datetime.now(UTC))
        self.invalidate_validation_cache()
        value = plan or self.generate(submitted_at)
        if not value.submission_allowed:
            return AfterHoursPaperSubmission(submitted=0, reused=0, rejected=0)
        taipei = ZoneInfo("Asia/Taipei")
        today = submitted_at.astimezone(taipei).date()
        overview = self._paper.overview(now=submitted_at)
        existing = {
            (
                item.symbol,
                item.side,
                self._aware(item.submitted_at).astimezone(taipei).date(),
            )
            for item in getattr(overview, "orders", ())
        }
        daily_action_count = sum(item[2] == today for item in existing)
        submitted = reused = rejected = 0
        for draft in value.orders:
            key = (draft.symbol, draft.side, today)
            if key in existing:
                reused += 1
                continue
            if daily_action_count >= MAX_DAILY_ACTIONS:
                break
            order = self._paper.submit_order(
                draft.symbol, draft.side, draft.quantity, now=submitted_at
            )
            existing.add(key)
            daily_action_count += 1
            submitted += 1
            status = getattr(order, "status", None)
            status_value = getattr(status, "value", status)
            if str(status_value).lower() == "rejected":
                rejected += 1
        return AfterHoursPaperSubmission(
            submitted=submitted, reused=reused, rejected=rejected
        )

    def generate(self, now: datetime | None = None) -> AfterHoursPlan:
        generated_at = now or datetime.now(UTC)
        if generated_at.tzinfo is None:
            generated_at = generated_at.replace(tzinfo=UTC)
        decisions = tuple(self._decisions.list_latest("TW"))
        decision_time = max((item.event_time for item in decisions), default=None)
        taipei = ZoneInfo("Asia/Taipei")
        try:
            benchmark_bars = self._bars.list_bars(
                "0050.TW", interval="1d", as_of=generated_at
            )
            latest_market_time = max(
                (item.event_time for item in benchmark_bars), default=None
            )
        except Exception:
            latest_market_time = None
        generated_date = generated_at.astimezone(taipei).date()
        market_date = (
            self._aware(latest_market_time).astimezone(taipei).date()
            if latest_market_time else None
        )
        decision_date = (
            self._aware(decision_time).astimezone(taipei).date()
            if decision_time else None
        )
        decision_is_fresh = bool(
            decision_time
            and decision_date == generated_date
            and market_date == generated_date
        )
        # People who can only review the system on weekends still need the
        # latest Friday-close plan for the next trading session. Keep the
        # window narrow so an old snapshot can never silently become current.
        weekend_preview = bool(
            generated_date.weekday() >= 5
            and market_date is not None
            and decision_date is not None
            and 0 <= (decision_date - market_date).days <= 1
            and 1 <= (generated_date - market_date).days <= 3
        )
        historical_preview = bool(
            generated_date.weekday() < 5
            and not decision_is_fresh
            and market_date is not None
            and decision_date is not None
            and market_date == decision_date
            and 1 <= (generated_date - decision_date).days <= 4
        )
        decision_is_viewable = (
            decision_is_fresh or weekend_preview or historical_preview
        )
        local_time = generated_at.astimezone(taipei).time()
        in_submission_window = (
            datetime.min.replace(hour=13, minute=40).time()
            <= local_time
            < datetime.min.replace(hour=14, minute=30).time()
        )
        expired_same_day = decision_is_fresh and not in_submission_window
        submission_allowed = (
            decision_is_fresh
            and generated_date.weekday() < 5
            and in_submission_window
        )
        execution_note = (
            "回看：依當日 13:30 收盤資料重建原本應在 13:40～14:30 "
            "建立的盤後零股草稿；現在禁止補送"
            if historical_preview or expired_same_day
            else (
                "執行：13:40～14:30 盤後零股限價草稿，14:30 集合競價，"
                "參考價為 13:30 收盤價且不保證成交"
            )
        )
        account = self._paper.overview(now=generated_at)
        positions = {
            item.position.symbol: item
            for item in account.positions
        }
        bulk_price_loader = getattr(self._bars, "latest_closes", None)
        if callable(bulk_price_loader):
            prices = {
                symbol: Decimal(str(value))
                for symbol, value in bulk_price_loader(
                    [item.symbol for item in decisions], generated_at
                ).items()
            }
        else:
            prices = {}
        watchlist: list[AfterHoursWatchItem] = []
        orders: list[AfterHoursOrderDraft] = []
        decision_by_symbol = {item.symbol: item for item in decisions}

        for decision in decisions:
            status = self._status(decision.status)
            blockers = self._blockers(decision)
            price = prices.get(decision.symbol)
            if price is None and not callable(bulk_price_loader):
                price = self._latest_price(decision.symbol, generated_at)
                if price is not None:
                    prices[decision.symbol] = price
            blocker = "；".join(blockers)
            if price is None:
                blocker = "缺少可用收盤價" if not blocker else f"{blocker}；缺少可用收盤價"
            if not decision_is_viewable:
                blocker = (
                    "決策不是今日盤後快照"
                    if not blocker else f"{blocker}；決策不是今日盤後快照"
                )
            if status != "候選" or blocker or price is None:
                watchlist.append(AfterHoursWatchItem(
                    symbol=decision.symbol,
                    status=status,
                    score=float(decision.score),
                    predicted_return_5d=decision.predicted_return_5d,
                    suggested_weight=float(decision.suggested_weight),
                    blocker=blocker or "尚未通過候選門檻",
                ))

        # Risk exits are evaluated before new buys.
        for symbol, position_view in positions.items():
            if len(orders) >= MAX_DAILY_ACTIONS:
                break
            if not decision_is_viewable:
                break
            decision = decision_by_symbol.get(symbol)
            status = self._status(decision.status) if decision else "資料不足"
            if status not in {"避免", "資料不足"}:
                continue
            quantity = int(position_view.position.quantity)
            if quantity <= 0:
                continue
            price = prices.get(symbol) or Decimal(position_view.mark_price)
            if price <= ZERO:
                continue
            gross = self._money(price * quantity)
            reason = (
                "訊號：系統決策轉為避免；部位：全部退出；風險：降低虧損曝險；"
                if status == "避免"
                else "訊號：缺少有效決策；部位：全部退出；風險：禁止持有無法驗證的標的；"
            )
            reason += execution_note
            orders.append(AfterHoursOrderDraft(
                symbol=symbol,
                side="SELL",
                quantity=quantity,
                odd_lot_order_count=math.ceil(quantity / MAX_ODD_LOT_ORDER),
                reference_price=price,
                estimated_amount=gross,
                estimated_cost=self._estimated_cost("SELL", gross),
                current_quantity=quantity,
                target_quantity=0,
                target_weight=0.0,
                predicted_return_5d=(
                    decision.predicted_return_5d if decision else None
                ),
                score=float(decision.score) if decision else 0.0,
                reason=reason,
            ))

        available_cash = Decimal(account.account.cash)
        candidates = sorted(
            (
                item for item in decisions
                if self._status(item.status) in {"候選", "觀察"}
                and decision_is_viewable
                and not self._execution_blockers(item)
                and (item.predicted_return_5d or 0) > 0
                and (
                    self._status(item.status) == "候選"
                    or ((item.model_rank or 0) >= 0.65 and item.score >= 0.65)
                )
            ),
            key=lambda item: (
                (item.predicted_return_5d or 0)
                - 0.5 * (item.prediction_dispersion or 0)
                + 0.05 * item.score
            ),
            reverse=True,
        )
        buy_count = 0
        for decision in candidates:
            if len(orders) >= MAX_DAILY_ACTIONS or buy_count >= MAX_DAILY_BUYS:
                break
            price = prices.get(decision.symbol) or self._latest_price(
                decision.symbol, generated_at
            )
            if price is None or price <= ZERO:
                continue
            research_only = self._status(decision.status) != "候選"
            target_weight = min(
                float(decision.suggested_weight) or RESEARCH_SIMULATION_WEIGHT,
                RESEARCH_SIMULATION_WEIGHT if research_only else 0.20,
            )
            target_value = Decimal(account.equity) * Decimal(str(target_weight))
            current_quantity = (
                int(positions[decision.symbol].position.quantity)
                if decision.symbol in positions else 0
            )
            target_quantity = int(
                (target_value / price).to_integral_value(rounding=ROUND_DOWN)
            )
            quantity = max(target_quantity - current_quantity, 0)
            affordable = int(
                (available_cash / (price * Decimal("1.003")))
                .to_integral_value(rounding=ROUND_DOWN)
            )
            quantity = min(quantity, affordable)
            if quantity <= 0:
                continue
            gross = self._money(price * quantity)
            cost = self._estimated_cost("BUY", gross)
            available_cash -= gross + cost
            buy_count += 1
            signal_label = (
                "五日相對 0050 預估超額"
                if "相對 0050" in (decision.reasons_json or "")
                else "五日預估報酬"
            )
            orders.append(AfterHoursOrderDraft(
                symbol=decision.symbol,
                side="BUY",
                quantity=quantity,
                odd_lot_order_count=math.ceil(quantity / MAX_ODD_LOT_ORDER),
                reference_price=price,
                estimated_amount=gross,
                estimated_cost=cost,
                current_quantity=current_quantity,
                target_quantity=target_quantity,
                target_weight=target_weight,
                predicted_return_5d=decision.predicted_return_5d,
                score=float(decision.score),
                reason=(
                    f"訊號：{signal_label} {(decision.predicted_return_5d or 0):+.2%}、"
                    f"模型排名 {(decision.model_rank or 0):.0%}、"
                    f"綜合分數 {float(decision.score):.2f}；"
                    f"部位：目標 {target_weight:.1%}、買進 {quantity} 股；"
                    + (
                        "風險：模型排名通過但策略尚未晉級，只做 5% 研究模擬；"
                        if research_only
                        else "風險：已通過候選與資料閘門，仍受單股 20% 上限；"
                    )
                    + execution_note
                ),
            ))

        if historical_preview:
            snapshot_date = decision_date.isoformat() if decision_date else "最近交易日"
            if orders:
                headline = (
                    f"事後補算：{snapshot_date} 盤後模擬會買進 "
                    f"{sum(item.side == 'BUY' for item in orders)} 筆、賣出 "
                    f"{sum(item.side == 'SELL' for item in orders)} 筆（當時未送單）"
                )
            else:
                headline = (
                    f"事後補算：{snapshot_date} 盤後沒有符合條件的模擬委託"
                )
        elif expired_same_day:
            if orders:
                headline = (
                    f"今日盤後回看：原清單買進 "
                    f"{sum(item.side == 'BUY' for item in orders)} 筆、賣出 "
                    f"{sum(item.side == 'SELL' for item in orders)} 筆"
                    "（委託時段已結束，禁止補送）"
                )
            else:
                headline = "今日盤後回看：沒有符合條件的模擬委託"
        elif orders and weekend_preview:
            headline = (
                "休市預覽：下個交易日模擬買進 "
                f"{sum(item.side == 'BUY' for item in orders)} 筆、賣出 "
                f"{sum(item.side == 'SELL' for item in orders)} 筆"
            )
        elif orders:
            headline = f"產生 {sum(item.side == 'BUY' for item in orders)} 筆買進、{sum(item.side == 'SELL' for item in orders)} 筆賣出草稿"
        elif generated_date.weekday() >= 5:
            headline = "今日休市：不交易"
        elif decisions and not decision_is_fresh:
            headline = "今日不交易：決策資料不是今日盤後快照"
        elif decisions:
            headline = "今日不交易：沒有標的通過全部研究與風險閘門"
        else:
            headline = "今日不交易：尚未產生每日決策"
        return AfterHoursPlan(
            generated_at=generated_at,
            decision_time=decision_time,
            equity=Decimal(account.equity),
            cash=Decimal(account.account.cash),
            orders=tuple(orders),
            watchlist=tuple(
                item for item in watchlist
                if item.symbol not in {order.symbol for order in orders}
            )[:12],
            headline=headline,
            mode=(
                "歷史盤後決策／只讀回看／禁止補送委託"
                if historical_preview
                else (
                    "今日盤後決策／時段已結束／禁止補送委託"
                    if expired_same_day
                    else (
                        "休市預覽／下個交易日模擬委託／禁止送出委託"
                        if weekend_preview
                        else "模擬委託草稿／真實交易關閉"
                    )
                )
            ),
            hard_rules=(
                "正式候選必須通過全部閘門；未晉級標的只能進入小部位研究模擬。",
                "13:30 收盤資料完成後產生訊號；13:40～14:30 只建立盤後零股限價草稿，14:30 集合競價一次撮合。",
                "交易日只接受當日 13:30 收盤快照；週末僅預覽下一交易日清單，禁止把舊訊號當成當日委託。",
                "錯過交易時段後仍保留最近盤後決策供回看，但只標示為事後補算，禁止補送或冒充當時成交。",
                "每日最多 3 個買賣動作；風險賣出優先，其餘名額只保留分數最高的買進。",
                "尚未晉級的模型只准建立最多 2 檔、單檔 5% 的研究模擬，不代表可實盤候選。",
                "單一股票目標權重上限 20%，總曝險沿用模擬帳戶風險限制。",
                "盤後零股每筆最多 999 股；超過時必須拆單。",
                "參考價是 13:30 收盤價；14:30 盤後零股成交價尚未產生，限價、排隊與未成交都必須另外追蹤。",
                "系統只建立草稿，不會連接券商或自動送出真實委託。",
            ),
            submission_allowed=submission_allowed,
            snapshot_kind=(
                "historical" if historical_preview
                else "expired" if expired_same_day
                else "weekend" if weekend_preview
                else "current"
            ),
        )
