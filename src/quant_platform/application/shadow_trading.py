from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal

import numpy as np

from quant_platform.application.ports import (
    MarketBarRepository, ResearchUniverseRepository, ShadowTradingRepository,
)
from quant_platform.domain.entities import ShadowOrder, ShadowOrderStatus
from quant_platform.execution import DisabledBrokerAdapter, TaiwanExecutionCostModel
from quant_platform.reinforcement_learning.agents import policy_from_neural_research
from quant_platform.reinforcement_learning.environment import (
    LinearAllocationPolicy, RlEnvironmentService, latest_market_observation,
)


@dataclass(frozen=True, slots=True)
class ShadowTradingAssumptions:
    research_notional_twd: Decimal = Decimal("1000000")
    minimum_target_change: float = 0.001


@dataclass(frozen=True, slots=True)
class ShadowGenerationResult:
    order: ShadowOrder
    reused: bool


@dataclass(frozen=True, slots=True)
class ShadowProcessResult:
    evaluated: int
    pending: int


@dataclass(frozen=True, slots=True)
class ShadowDailyResult:
    generated: int
    reused: int
    skipped: int
    evaluated: int
    pending: int


@dataclass(frozen=True, slots=True)
class ShadowTradingOverview:
    orders: tuple[ShadowOrder, ...]
    pending_count: int
    evaluated_count: int
    no_action_count: int
    research_only_count: int
    attributed_pnl_twd: Decimal
    average_return_rate: float | None
    broker_capability: object
    assumptions: ShadowTradingAssumptions


class ShadowTradingService:
    algorithms = ("cpu", "ppo", "dqn")

    def __init__(
        self,
        repository: ShadowTradingRepository,
        bars: MarketBarRepository,
        universe: ResearchUniverseRepository,
        rl_service: RlEnvironmentService,
        assumptions: ShadowTradingAssumptions | None = None,
    ) -> None:
        self._repository = repository
        self._bars = bars
        self._universe = universe
        self._rl = rl_service
        self._assumptions = assumptions or ShadowTradingAssumptions()
        self._costs = TaiwanExecutionCostModel()
        self._broker = DisabledBrokerAdapter()

    @staticmethod
    def _normalize(raw_symbol: str) -> str:
        symbol = raw_symbol.strip().upper()
        return f"{symbol}.TW" if symbol.isdigit() else symbol

    def _research_and_policy(self, algorithm: str, symbol: str):
        overview = self._rl.overview(symbol)
        normalized = algorithm.strip().lower()
        if normalized == "cpu":
            research = overview.cpu_agent
            if research is None:
                raise ValueError("尚無 CPU 代理研究，請先到強化學習實驗室訓練")
            fold = research.folds[-1]
            policy = LinearAllocationPolicy(
                weights=np.asarray(fold.policy_weights, dtype=float),
                feature_mean=np.asarray(fold.feature_mean, dtype=float),
                feature_std=np.asarray(fold.feature_std, dtype=float),
            )
            return research, policy
        research = next(
            (item for item in overview.neural_agents if item.algorithm == normalized), None
        )
        if research is None:
            raise ValueError(f"尚無 {normalized.upper()} 研究，請先到強化學習實驗室訓練")
        return research, policy_from_neural_research(research)

    def generate(self, raw_symbol: str, algorithm: str) -> ShadowGenerationResult:
        symbol = self._normalize(raw_symbol)
        if algorithm.strip().lower() not in self.algorithms:
            raise ValueError("影子交易只支援 CPU、PPO 或 DQN 代理")
        asset = self._universe.get(symbol)
        if asset is None:
            raise ValueError("股票不在研究股票池")
        bars = self._bars.list_bars(symbol)
        if len(bars) < 21:
            raise ValueError("至少需要 21 筆日線才能建立影子決策")
        research, policy = self._research_and_policy(algorithm, symbol)
        latest = bars[-1]
        existing = self._repository.get_decision(
            symbol, algorithm.lower(), research.experiment_id, latest.event_time
        )
        if existing:
            return ShadowGenerationResult(existing, reused=True)
        previous = next(
            (
                item for item in self._repository.list_orders()
                if item.symbol == symbol and item.algorithm == algorithm.lower()
            ),
            None,
        )
        previous_weight = previous.target_weight if previous else 0.0
        observation = latest_market_observation(
            bars, cash_weight=1 - previous_weight, position_weight=previous_weight
        )
        target_weight = max(0.0, min(0.8, float(policy(observation))))
        weight_change = target_weight - previous_weight
        notional = self._costs.money(
            self._assumptions.research_notional_twd * Decimal(str(abs(weight_change)))
        )
        quantity = int(notional / Decimal(latest.close)) if notional > 0 else 0
        no_action = (
            abs(weight_change) < self._assumptions.minimum_target_change or quantity < 1
        )
        side = "HOLD" if no_action else ("BUY" if weight_change > 0 else "SELL")
        status = ShadowOrderStatus.NO_ACTION if no_action else ShadowOrderStatus.PENDING
        reason = (
            "目標權重變化低於一股或最低變化門檻。"
            if no_action else
            "研究觀察：尚未通過候選門檻，不具券商執行權限。"
            if research.summary.promotion_gate != "CANDIDATE" else
            "通過研究候選門檻；仍需人工核准，且券商連線目前停用。"
        )
        order = ShadowOrder(
            id=None, symbol=symbol, algorithm=algorithm.lower(),
            experiment_id=research.experiment_id,
            decision_time=latest.event_time,
            data_available_time=latest.available_time,
            reference_price=Decimal(latest.close),
            previous_target_weight=previous_weight, target_weight=target_weight,
            side=side, quantity=0 if no_action else quantity,
            notional_twd=Decimal("0") if no_action else notional,
            status=status, promotion_gate=research.summary.promotion_gate,
            reason=reason, created_at=datetime.now(UTC), execution_time=None,
            fill_price=None, gross_twd=None, commission_twd=None,
            transaction_tax_twd=None, pnl_twd=None, return_rate=None,
            benchmark_return=None,
        )
        order_id = self._repository.save(order)
        return ShadowGenerationResult(replace(order, id=order_id), reused=False)

    def process_pending(self) -> ShadowProcessResult:
        evaluated = 0
        for order in self._repository.list_pending():
            bars = self._bars.list_bars(order.symbol)
            decision_time = (
                order.decision_time if order.decision_time.tzinfo
                else order.decision_time.replace(tzinfo=UTC)
            )
            next_bar = next(
                (
                    item for item in bars
                    if (
                        item.event_time if item.event_time.tzinfo
                        else item.event_time.replace(tzinfo=UTC)
                    ) > decision_time
                ),
                None,
            )
            if next_bar is None:
                continue
            asset = self._universe.get(order.symbol)
            asset_type = asset.asset_type if asset else "EQUITY"
            fill_price = self._costs.slipped_price(Decimal(next_bar.open), order.side)
            gross = self._costs.money(fill_price * order.quantity)
            commission = self._costs.commission(gross, order.quantity)
            transaction_tax = (
                self._costs.sell_tax(gross, asset_type)
                if order.side == "SELL" else Decimal("0")
            )
            close_value = Decimal(next_bar.close) * order.quantity
            pnl = (
                close_value - gross - commission
                if order.side == "BUY" else
                gross - close_value - commission - transaction_tax
            )
            pnl = self._costs.money(pnl)
            return_rate = float(pnl / order.notional_twd) if order.notional_twd else 0.0
            benchmark = float(
                Decimal(next_bar.close) / order.reference_price - Decimal("1")
            )
            self._repository.update(replace(
                order, status=ShadowOrderStatus.EVALUATED,
                execution_time=next_bar.event_time, fill_price=fill_price,
                gross_twd=gross, commission_twd=commission,
                transaction_tax_twd=transaction_tax, pnl_twd=pnl,
                return_rate=return_rate, benchmark_return=benchmark,
            ))
            evaluated += 1
        return ShadowProcessResult(
            evaluated=evaluated, pending=len(self._repository.list_pending())
        )

    def overview(self) -> ShadowTradingOverview:
        orders = tuple(self._repository.list_orders())
        evaluated = [item for item in orders if item.status == ShadowOrderStatus.EVALUATED]
        returns = [item.return_rate for item in evaluated if item.return_rate is not None]
        return ShadowTradingOverview(
            orders=orders,
            pending_count=sum(item.status == ShadowOrderStatus.PENDING for item in orders),
            evaluated_count=len(evaluated),
            no_action_count=sum(item.status == ShadowOrderStatus.NO_ACTION for item in orders),
            research_only_count=sum(item.promotion_gate != "CANDIDATE" for item in orders),
            attributed_pnl_twd=sum(
                (item.pnl_twd or Decimal("0") for item in evaluated), Decimal("0")
            ),
            average_return_rate=(sum(returns) / len(returns) if returns else None),
            broker_capability=self._broker.capability(),
            assumptions=self._assumptions,
        )

    def run_daily(self) -> ShadowDailyResult:
        processed = self.process_pending()
        generated = reused = skipped = 0
        for asset in self._universe.list_active("TW"):
            for algorithm in self.algorithms:
                try:
                    result = self.generate(asset.symbol, algorithm)
                    if result.reused:
                        reused += 1
                    else:
                        generated += 1
                except (ValueError, RuntimeError):
                    skipped += 1
        return ShadowDailyResult(
            generated=generated, reused=reused, skipped=skipped,
            evaluated=processed.evaluated, pending=processed.pending,
        )
