from __future__ import annotations

import math
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Callable

import numpy as np

from quant_platform.application.ports import (
    MarketBarRepository, ResearchExperimentRepository, ResearchUniverseRepository,
)
from quant_platform.domain.entities import ExperimentStatus, MarketBar, ResearchExperiment
from quant_platform.execution import TaiwanExecutionCostModel

if TYPE_CHECKING:
    from quant_platform.reinforcement_learning.agents import (
        NeuralAgentResearch, TorchCapability,
    )


@dataclass(frozen=True, slots=True)
class TradingObservation:
    event_time: datetime
    return_1d: float
    momentum_5d: float
    momentum_20d: float
    volatility_20d: float
    drawdown: float
    cash_weight: float
    position_weight: float

    def vector(self) -> np.ndarray:
        return np.array((
            self.return_1d, self.momentum_5d, self.momentum_20d,
            self.volatility_20d, self.drawdown, self.cash_weight,
            self.position_weight,
        ), dtype=np.float32)


def latest_market_observation(
    bars: list[MarketBar], cash_weight: float = 1.0, position_weight: float = 0.0
) -> TradingObservation:
    ordered = sorted(bars, key=lambda item: item.event_time)
    if len(ordered) < 21:
        raise ValueError("Latest RL observation requires at least 21 daily bars")
    closes = np.asarray([float(item.close) for item in ordered], dtype=float)
    returns = np.diff(np.log(closes[-21:]))
    return TradingObservation(
        event_time=ordered[-1].event_time,
        return_1d=float(closes[-1] / closes[-2] - 1),
        momentum_5d=float(closes[-1] / closes[-6] - 1),
        momentum_20d=float(closes[-1] / closes[-21] - 1),
        volatility_20d=float(np.std(returns, ddof=1) * math.sqrt(252)),
        drawdown=float(closes[-1] / np.max(closes) - 1),
        cash_weight=max(0.0, min(1.0, cash_weight)),
        position_weight=max(0.0, min(1.0, position_weight)),
    )


@dataclass(frozen=True, slots=True)
class EpisodeMetrics:
    total_return: float
    benchmark_return: float
    excess_return: float
    sharpe: float
    max_drawdown: float
    turnover: float
    total_cost_twd: float
    trade_count: int
    steps: int


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    policy_name: str
    label: str
    metrics: EpisodeMetrics


@dataclass(frozen=True, slots=True)
class RlLabOverview:
    symbol: str
    asset_found: bool
    bar_count: int
    first_date: datetime | None
    last_date: datetime | None
    observation_size: int
    observation_names: tuple[str, ...]
    action_description: str
    execution_description: str
    initial_cash_twd: float
    max_position_weight: float
    commission_bps: float
    stock_sell_tax_bps: float
    etf_sell_tax_bps: float
    slippage_bps: float
    evaluations: tuple[PolicyEvaluation, ...]
    ready_for_training: bool
    readiness_notes: tuple[str, ...]
    walk_forward: RlWalkForwardResearch | None
    cpu_agent: CpuAgentResearch | None
    torch_capability: TorchCapability
    neural_agents: tuple[NeuralAgentResearch, ...]
    current_data_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    minimum_train_bars: int = 504
    validation_bars: int = 63
    test_bars: int = 63
    embargo_bars: int = 5
    minimum_folds: int = 3

    @property
    def minimum_required_bars(self) -> int:
        return (
            self.minimum_train_bars + self.validation_bars + self.test_bars
            + 2 * self.embargo_bars
        )


@dataclass(frozen=True, slots=True)
class WalkForwardSplit:
    fold_number: int
    train_start: int
    train_end: int
    validation_start: int
    validation_end: int
    test_start: int
    test_end: int
    train_start_time: datetime
    train_end_time: datetime
    validation_start_time: datetime
    validation_end_time: datetime
    test_start_time: datetime
    test_end_time: datetime


@dataclass(frozen=True, slots=True)
class WalkForwardPolicySummary:
    policy_name: str
    label: str
    fold_count: int
    compounded_return: float
    compounded_benchmark_return: float
    compounded_excess_return: float
    positive_excess_fold_ratio: float
    median_sharpe: float
    worst_max_drawdown: float
    total_cost_twd: float
    total_trade_count: int
    promotion_gate: str
    failed_gates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RlWalkForwardResearch:
    experiment_id: int
    symbol: str
    experiment_version: str
    data_fingerprint: str
    data_is_current: bool
    bar_count: int
    fold_count: int
    config: WalkForwardConfig
    splits: tuple[WalkForwardSplit, ...]
    policies: tuple[WalkForwardPolicySummary, ...]
    computed_at: datetime
    reused: bool = False


@dataclass(frozen=True, slots=True)
class CpuAgentConfig:
    population_size: int = 16
    generations: int = 6
    elite_fraction: float = 0.25
    initial_parameter_std: float = 1.0
    minimum_parameter_std: float = 0.05
    random_seed: int = 20260719


@dataclass(frozen=True, slots=True)
class CpuAgentFoldResult:
    fold_number: int
    random_seed: int
    train_objective: float
    validation_objective: float
    train_metrics: EpisodeMetrics
    validation_metrics: EpisodeMetrics
    test_metrics: EpisodeMetrics
    policy_weights: tuple[float, ...]
    feature_mean: tuple[float, ...]
    feature_std: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class CpuAgentResearch:
    experiment_id: int
    symbol: str
    experiment_version: str
    data_fingerprint: str
    data_is_current: bool
    fold_count: int
    config: CpuAgentConfig
    summary: WalkForwardPolicySummary
    folds: tuple[CpuAgentFoldResult, ...]
    computed_at: datetime
    reused: bool = False


class OfflineTradingEnvironment:
    """Gym-like, dependency-free environment with t-close decision and t+1-open execution."""

    observation_names = (
        "一日報酬", "五日動能", "二十日動能", "二十日年化波動",
        "歷史回撤", "現金權重", "持倉權重",
    )

    def __init__(
        self,
        bars: list[MarketBar],
        initial_cash: Decimal = Decimal("1000000"),
        max_position_weight: float = 0.8,
        cost_model: TaiwanExecutionCostModel | None = None,
        asset_type: str = "EQUITY",
        lookback: int = 20,
    ) -> None:
        self.bars = sorted(bars, key=lambda item: item.event_time)
        self.initial_cash = initial_cash
        self.max_position_weight = max(0.0, min(1.0, max_position_weight))
        self.costs = cost_model or TaiwanExecutionCostModel()
        self.asset_type = asset_type
        self.lookback = lookback
        if len(self.bars) < lookback + 2:
            raise ValueError(f"RL environment needs at least {lookback + 2} daily bars")
        if any(Decimal(item.open) <= 0 or Decimal(item.close) <= 0 for item in self.bars):
            raise ValueError("RL environment requires positive open and close prices")
        for previous, current in zip(self.bars, self.bars[1:]):
            if current.event_time <= previous.event_time:
                raise ValueError("Market bars must have strictly increasing event_time")
        self._start_index = lookback
        self._end_index = len(self.bars) - 1
        self.reset()

    def reset(
        self, start_index: int | None = None, end_index: int | None = None
    ) -> TradingObservation:
        self._start_index = max(self.lookback, start_index or self.lookback)
        self._end_index = min(len(self.bars) - 1, end_index or len(self.bars) - 1)
        if self._end_index <= self._start_index:
            raise ValueError("Episode end must be after its start")
        self.index = self._start_index
        self.cash = self.initial_cash
        self.quantity = 0
        self.peak_equity = self.initial_cash
        self.equity_history = [float(self.initial_cash)]
        self.reward_history: list[float] = []
        self.total_turnover = Decimal("0")
        self.total_cost = Decimal("0")
        self.trade_count = 0
        self.start_close = Decimal(self.bars[self.index].close)
        return self.observation()

    @property
    def equity(self) -> Decimal:
        return self.cash + Decimal(self.bars[self.index].close) * self.quantity

    def observation(self) -> TradingObservation:
        closes = np.array(
            [float(item.close) for item in self.bars[: self.index + 1]], dtype=float
        )
        returns = np.diff(np.log(closes[-21:]))
        equity = self.equity
        position_value = Decimal(self.bars[self.index].close) * self.quantity
        return TradingObservation(
            event_time=self.bars[self.index].event_time,
            return_1d=float(closes[-1] / closes[-2] - 1),
            momentum_5d=float(closes[-1] / closes[-6] - 1),
            momentum_20d=float(closes[-1] / closes[-21] - 1),
            volatility_20d=float(np.std(returns, ddof=1) * math.sqrt(252)),
            drawdown=float(closes[-1] / np.max(closes) - 1),
            cash_weight=float(self.cash / equity) if equity > 0 else 0.0,
            position_weight=float(position_value / equity) if equity > 0 else 0.0,
        )

    def step(self, target_weight: float) -> tuple[TradingObservation, float, bool, dict[str, float]]:
        if self.index >= self._end_index:
            raise RuntimeError("Episode is already terminated")
        action = max(0.0, min(self.max_position_weight, float(target_weight)))
        decision_equity = self.equity
        decision_close = Decimal(self.bars[self.index].close)
        target_quantity = int(
            decision_equity * Decimal(str(action)) / decision_close
        )
        delta = target_quantity - self.quantity
        next_bar = self.bars[self.index + 1]
        commission = transaction_tax = Decimal("0")
        gross = Decimal("0")
        if delta > 0:
            fill_price = self.costs.slipped_price(Decimal(next_bar.open), "BUY")
            executable = min(delta, int(self.cash / fill_price))
            while executable > 0:
                gross = self.costs.money(fill_price * executable)
                commission = self.costs.commission(gross, executable)
                if gross + commission <= self.cash:
                    break
                executable -= 1
            if executable > 0:
                self.cash -= gross + commission
                self.quantity += executable
                delta = executable
            else:
                delta = 0
        elif delta < 0:
            executable = min(-delta, self.quantity)
            fill_price = self.costs.slipped_price(Decimal(next_bar.open), "SELL")
            gross = self.costs.money(fill_price * executable)
            commission = self.costs.commission(gross, executable) if executable else Decimal("0")
            transaction_tax = self.costs.sell_tax(gross, self.asset_type) if executable else Decimal("0")
            self.cash += gross - commission - transaction_tax
            self.quantity -= executable
            delta = -executable
        trade_cost = commission + transaction_tax
        if delta:
            self.trade_count += 1
            self.total_turnover += gross
            self.total_cost += trade_cost
        self.index += 1
        ending_equity = self.equity
        reward = float(ending_equity / decision_equity - Decimal("1"))
        self.peak_equity = max(self.peak_equity, ending_equity)
        drawdown = float(ending_equity / self.peak_equity - Decimal("1"))
        self.equity_history.append(float(ending_equity))
        self.reward_history.append(reward)
        terminated = self.index >= self._end_index
        info = {
            "equity_twd": float(ending_equity),
            "trade_gross_twd": float(gross),
            "commission_twd": float(commission),
            "transaction_tax_twd": float(transaction_tax),
            "drawdown": drawdown,
            "executed_share_change": float(delta),
            "execution_open_twd_per_share": float(next_bar.open),
        }
        return self.observation(), reward, terminated, info

    def metrics(self) -> EpisodeMetrics:
        equity = np.array(self.equity_history, dtype=float)
        returns = np.array(self.reward_history, dtype=float)
        running_max = np.maximum.accumulate(equity)
        drawdowns = equity / running_max - 1
        total_return = equity[-1] / equity[0] - 1
        benchmark = float(
            Decimal(self.bars[self.index].close) / self.start_close - Decimal("1")
        )
        sharpe = (
            float(np.mean(returns) / np.std(returns, ddof=1) * math.sqrt(252))
            if len(returns) > 1 and np.std(returns, ddof=1) > 0 else 0.0
        )
        return EpisodeMetrics(
            total_return=float(total_return), benchmark_return=benchmark,
            excess_return=float(total_return - benchmark), sharpe=sharpe,
            max_drawdown=float(np.min(drawdowns)),
            turnover=float(self.total_turnover / self.initial_cash),
            total_cost_twd=float(self.total_cost), trade_count=self.trade_count,
            steps=len(returns),
        )


Policy = Callable[[TradingObservation], float]


def evaluate_policy(
    environment: OfflineTradingEnvironment, policy: Policy,
    start_index: int | None = None, end_index: int | None = None,
) -> EpisodeMetrics:
    observation = environment.reset(start_index, end_index)
    terminated = False
    while not terminated:
        observation, _, terminated, _ = environment.step(policy(observation))
    return environment.metrics()


class ExpandingWalkForwardSplitter:
    """Non-overlapping test windows with expanding training history and embargo gaps."""

    def __init__(self, config: WalkForwardConfig | None = None) -> None:
        self.config = config or WalkForwardConfig()
        if self.config.minimum_train_bars < 22:
            raise ValueError("Training window must contain at least 22 daily bars")
        if self.config.validation_bars < 1 or self.config.test_bars < 2:
            raise ValueError("Validation and test windows must be positive")
        if self.config.embargo_bars < 0:
            raise ValueError("Embargo cannot be negative")

    def split(self, bars: list[MarketBar]) -> tuple[WalkForwardSplit, ...]:
        if len(bars) < self.config.minimum_required_bars:
            return ()
        ordered = sorted(bars, key=lambda item: item.event_time)
        splits: list[WalkForwardSplit] = []
        train_end = self.config.minimum_train_bars - 1
        fold_number = 1
        while True:
            validation_start = train_end + 1 + self.config.embargo_bars
            validation_end = validation_start + self.config.validation_bars - 1
            test_start = validation_end + 1 + self.config.embargo_bars
            test_end = test_start + self.config.test_bars - 1
            if test_end >= len(ordered):
                break
            splits.append(WalkForwardSplit(
                fold_number=fold_number,
                train_start=0, train_end=train_end,
                validation_start=validation_start, validation_end=validation_end,
                test_start=test_start, test_end=test_end,
                train_start_time=ordered[0].event_time,
                train_end_time=ordered[train_end].event_time,
                validation_start_time=ordered[validation_start].event_time,
                validation_end_time=ordered[validation_end].event_time,
                test_start_time=ordered[test_start].event_time,
                test_end_time=ordered[test_end].event_time,
            ))
            train_end = test_end
            fold_number += 1
        return tuple(splits)


class LinearAllocationPolicy:
    """Small auditable policy: standardized market state -> continuous allocation."""

    def __init__(
        self,
        weights: np.ndarray,
        feature_mean: np.ndarray,
        feature_std: np.ndarray,
        max_position_weight: float = 0.8,
    ) -> None:
        if weights.shape != (8,):
            raise ValueError("Linear allocation policy requires 8 parameters")
        if feature_mean.shape != (5,) or feature_std.shape != (5,):
            raise ValueError("Market feature scaler requires 5 values")
        self.weights = weights.astype(float, copy=True)
        self.feature_mean = feature_mean.astype(float, copy=True)
        self.feature_std = np.maximum(feature_std.astype(float, copy=True), 1e-8)
        self.max_position_weight = max_position_weight

    def __call__(self, observation: TradingObservation) -> float:
        market = np.array((
            observation.return_1d, observation.momentum_5d,
            observation.momentum_20d, observation.volatility_20d,
            observation.drawdown,
        ), dtype=float)
        state = np.concatenate((
            (market - self.feature_mean) / self.feature_std,
            np.array((observation.cash_weight, observation.position_weight, 1.0)),
        ))
        score = float(np.clip(np.dot(self.weights, state), -30.0, 30.0))
        return self.max_position_weight / (1.0 + math.exp(-score))


class CpuPolicySearchTrainer:
    """Deterministic cross-entropy policy search using train and validation only."""

    def __init__(self, config: CpuAgentConfig | None = None) -> None:
        self.config = config or CpuAgentConfig()
        if self.config.population_size < 4:
            raise ValueError("CPU agent population must be at least 4")
        if self.config.generations < 1:
            raise ValueError("CPU agent generations must be positive")
        if not 0 < self.config.elite_fraction <= 0.5:
            raise ValueError("Elite fraction must be in (0, 0.5]")

    @staticmethod
    def objective(metrics: EpisodeMetrics) -> float:
        """Risk- and cost-aware score; max_drawdown is negative."""
        return (
            metrics.sharpe + metrics.total_return
            + 1.5 * metrics.max_drawdown - 0.02 * metrics.turnover
        )

    @staticmethod
    def _fit_scaler(
        environment: OfflineTradingEnvironment, start_index: int, end_index: int
    ) -> tuple[np.ndarray, np.ndarray]:
        observation = environment.reset(start_index, end_index)
        values: list[list[float]] = []
        terminated = False
        while not terminated:
            values.append([
                observation.return_1d, observation.momentum_5d,
                observation.momentum_20d, observation.volatility_20d,
                observation.drawdown,
            ])
            observation, _, terminated, _ = environment.step(0.0)
        matrix = np.asarray(values, dtype=float)
        mean = np.mean(matrix, axis=0)
        std = np.std(matrix, axis=0, ddof=1)
        return mean, np.where(np.isfinite(std) & (std > 1e-8), std, 1.0)

    def train_fold(
        self, environment: OfflineTradingEnvironment, split: WalkForwardSplit
    ) -> CpuAgentFoldResult:
        fold_seed = self.config.random_seed + split.fold_number
        rng = np.random.default_rng(fold_seed)
        feature_mean, feature_std = self._fit_scaler(
            environment, split.train_start, split.train_end
        )
        parameter_mean = np.zeros(8, dtype=float)
        parameter_std = np.full(8, self.config.initial_parameter_std, dtype=float)
        elite_count = max(2, int(self.config.population_size * self.config.elite_fraction))
        final_elites = np.empty((0, 8), dtype=float)
        for _ in range(self.config.generations):
            population = rng.normal(
                parameter_mean, parameter_std,
                size=(self.config.population_size, parameter_mean.size),
            )
            population[0] = parameter_mean
            scores = np.array([
                self.objective(evaluate_policy(
                    environment,
                    LinearAllocationPolicy(weights, feature_mean, feature_std),
                    split.train_start, split.train_end,
                ))
                for weights in population
            ])
            elite_indices = np.argsort(scores)[-elite_count:]
            final_elites = population[elite_indices]
            parameter_mean = np.mean(final_elites, axis=0)
            parameter_std = np.maximum(
                np.std(final_elites, axis=0, ddof=1),
                self.config.minimum_parameter_std,
            )
        candidates = np.vstack((parameter_mean, final_elites))
        validation_metrics = [
            evaluate_policy(
                environment,
                LinearAllocationPolicy(weights, feature_mean, feature_std),
                split.validation_start, split.validation_end,
            )
            for weights in candidates
        ]
        validation_scores = np.array([self.objective(item) for item in validation_metrics])
        best_index = int(np.argmax(validation_scores))
        best_weights = candidates[best_index]
        policy = LinearAllocationPolicy(best_weights, feature_mean, feature_std)
        train_metrics = evaluate_policy(
            environment, policy, split.train_start, split.train_end
        )
        test_metrics = evaluate_policy(
            environment, policy, split.test_start, split.test_end
        )
        return CpuAgentFoldResult(
            fold_number=split.fold_number, random_seed=fold_seed,
            train_objective=self.objective(train_metrics),
            validation_objective=float(validation_scores[best_index]),
            train_metrics=train_metrics,
            validation_metrics=validation_metrics[best_index],
            test_metrics=test_metrics,
            policy_weights=tuple(float(value) for value in best_weights),
            feature_mean=tuple(float(value) for value in feature_mean),
            feature_std=tuple(float(value) for value in feature_std),
        )


class RlEnvironmentService:
    experiment_version = "rl-walk-forward-v1"
    cpu_agent_version = "rl-cpu-linear-cem-v1"

    def __init__(
        self,
        bars: MarketBarRepository,
        universe: ResearchUniverseRepository,
        experiments: ResearchExperimentRepository,
        walk_forward_config: WalkForwardConfig | None = None,
        cpu_agent_config: CpuAgentConfig | None = None,
        torch_agent_config: object | None = None,
    ) -> None:
        self._bars = bars
        self._universe = universe
        self._experiments = experiments
        self._walk_forward_config = walk_forward_config or WalkForwardConfig()
        self._cpu_agent_config = cpu_agent_config or CpuAgentConfig()
        self._torch_agent_config = torch_agent_config

    @staticmethod
    def _normalize(raw_symbol: str) -> str:
        symbol = raw_symbol.strip().upper()
        return f"{symbol}.TW" if symbol.isdigit() else symbol

    @staticmethod
    def _policies() -> tuple[tuple[str, str, Policy], ...]:
        return (
            ("cash", "全現金基準", lambda _: 0.0),
            ("target_weight", "八成目標權重每日再平衡", lambda _: 0.8),
            (
                "momentum_rule", "二十日動能規則",
                lambda observation: 0.8 if observation.momentum_20d > 0 else 0.0,
            ),
        )

    @staticmethod
    def _fingerprint(symbol: str, bars: list[MarketBar]) -> str:
        digest = hashlib.sha256()
        digest.update(symbol.encode("utf-8"))
        for bar in bars:
            digest.update(
                "|".join((
                    bar.event_time.isoformat(), bar.available_time.isoformat(),
                    str(bar.open), str(bar.high), str(bar.low), str(bar.close),
                    str(bar.volume), bar.source,
                )).encode("utf-8")
            )
        return digest.hexdigest()

    @staticmethod
    def _summary(
        name: str, label: str, fold_metrics: list[EpisodeMetrics], minimum_folds: int
    ) -> WalkForwardPolicySummary:
        compounded_return = math.prod(1 + item.total_return for item in fold_metrics) - 1
        compounded_benchmark = (
            math.prod(1 + item.benchmark_return for item in fold_metrics) - 1
        )
        positive_ratio = (
            sum(item.excess_return > 0 for item in fold_metrics) / len(fold_metrics)
            if fold_metrics else 0.0
        )
        median_sharpe = float(np.median([item.sharpe for item in fold_metrics])) if fold_metrics else 0.0
        worst_drawdown = min((item.max_drawdown for item in fold_metrics), default=0.0)
        failed: list[str] = []
        if len(fold_metrics) < minimum_folds:
            failed.append(f"樣本外分段少於 {minimum_folds} 折")
        if compounded_return <= compounded_benchmark:
            failed.append("複利樣本外報酬未超越買入持有基準")
        if positive_ratio < 0.6:
            failed.append("超額報酬為正的分段少於 60%")
        if median_sharpe < 0.5:
            failed.append("樣本外 Sharpe 中位數低於 0.50")
        if worst_drawdown < -0.30:
            failed.append("最差樣本外最大回撤超過 30%")
        return WalkForwardPolicySummary(
            policy_name=name, label=label, fold_count=len(fold_metrics),
            compounded_return=compounded_return,
            compounded_benchmark_return=compounded_benchmark,
            compounded_excess_return=compounded_return - compounded_benchmark,
            positive_excess_fold_ratio=positive_ratio,
            median_sharpe=median_sharpe, worst_max_drawdown=worst_drawdown,
            total_cost_twd=sum(item.total_cost_twd for item in fold_metrics),
            total_trade_count=sum(item.trade_count for item in fold_metrics),
            promotion_gate="CANDIDATE" if not failed else "RESEARCH_ONLY",
            failed_gates=tuple(failed),
        )

    def _decode_experiment(
        self, experiment: ResearchExperiment, current_fingerprint: str, reused: bool = False
    ) -> RlWalkForwardResearch:
        parameters = json.loads(experiment.parameters_json)
        metrics = json.loads(experiment.metrics_json)
        config = WalkForwardConfig(**parameters["config"])
        splits = tuple(WalkForwardSplit(
            **{
                **item,
                **{
                    key: datetime.fromisoformat(item[key])
                    for key in (
                        "train_start_time", "train_end_time", "validation_start_time",
                        "validation_end_time", "test_start_time", "test_end_time",
                    )
                },
            }
        ) for item in metrics["splits"])
        policies = tuple(WalkForwardPolicySummary(
            **{**item, "failed_gates": tuple(item["failed_gates"])}
        ) for item in metrics["policies"])
        return RlWalkForwardResearch(
            experiment_id=int(experiment.id or 0), symbol=parameters["symbol"],
            experiment_version=parameters["experiment_version"],
            data_fingerprint=parameters["data_fingerprint"],
            data_is_current=parameters["data_fingerprint"] == current_fingerprint,
            bar_count=parameters["bar_count"], fold_count=len(splits), config=config,
            splits=splits, policies=policies, computed_at=experiment.created_at,
            reused=reused,
        )

    def run_walk_forward(self, raw_symbol: str = "2330") -> RlWalkForwardResearch:
        symbol = self._normalize(raw_symbol)
        asset = self._universe.get(symbol)
        if asset is None:
            raise ValueError("股票不在研究股票池")
        bars = self._bars.list_bars(symbol)
        splitter = ExpandingWalkForwardSplitter(self._walk_forward_config)
        splits = splitter.split(bars)
        if len(splits) < self._walk_forward_config.minimum_folds:
            raise ValueError(
                f"至少需要 {self._walk_forward_config.minimum_folds} 個完整樣本外分段；"
                f"目前只有 {len(splits)} 個"
            )
        fingerprint = self._fingerprint(symbol, bars)
        name = f"rl_walk_forward:{symbol}"
        latest = self._experiments.get_latest(name, "rl_walk_forward")
        if latest is not None:
            parameters = json.loads(latest.parameters_json)
            if (
                parameters.get("experiment_version") == self.experiment_version
                and parameters.get("data_fingerprint") == fingerprint
                and parameters.get("config") == asdict(self._walk_forward_config)
            ):
                return self._decode_experiment(latest, fingerprint, reused=True)

        environment = OfflineTradingEnvironment(
            bars, asset_type=asset.asset_type, cost_model=TaiwanExecutionCostModel()
        )
        metrics_by_policy: dict[str, list[EpisodeMetrics]] = {
            name: [] for name, _, _ in self._policies()
        }
        fold_payload: list[dict[str, object]] = []
        for split in splits:
            policy_payload: dict[str, object] = {}
            for policy_name, _, policy in self._policies():
                metrics = evaluate_policy(
                    environment, policy, split.test_start, split.test_end
                )
                metrics_by_policy[policy_name].append(metrics)
                policy_payload[policy_name] = asdict(metrics)
            fold_payload.append({
                "fold_number": split.fold_number,
                "test_start_time": split.test_start_time.isoformat(),
                "test_end_time": split.test_end_time.isoformat(),
                "policies": policy_payload,
            })
        summaries = tuple(
            self._summary(name, label, metrics_by_policy[name], self._walk_forward_config.minimum_folds)
            for name, label, _ in self._policies()
        )
        now = datetime.now(UTC)
        experiment = ResearchExperiment(
            id=None, name=name, experiment_type="rl_walk_forward",
            status=ExperimentStatus.SUCCEEDED,
            parameters_json=json.dumps({
                "symbol": symbol, "experiment_version": self.experiment_version,
                "data_fingerprint": fingerprint, "bar_count": len(bars),
                "data_start": bars[0].event_time.isoformat(),
                "data_end": bars[-1].event_time.isoformat(),
                "config": asdict(self._walk_forward_config),
            }, ensure_ascii=False, sort_keys=True),
            metrics_json=json.dumps({
                "splits": [
                    {key: value.isoformat() if isinstance(value, datetime) else value
                     for key, value in asdict(split).items()}
                    for split in splits
                ],
                "fold_metrics": fold_payload,
                "policies": [asdict(item) for item in summaries],
            }, ensure_ascii=False, sort_keys=True),
            created_at=now,
        )
        experiment_id = self._experiments.save(experiment)
        saved = ResearchExperiment(
            id=experiment_id, name=experiment.name,
            experiment_type=experiment.experiment_type, status=experiment.status,
            parameters_json=experiment.parameters_json, metrics_json=experiment.metrics_json,
            created_at=experiment.created_at,
        )
        return self._decode_experiment(saved, fingerprint)

    def _decode_cpu_agent(
        self, experiment: ResearchExperiment, current_fingerprint: str,
        reused: bool = False,
    ) -> CpuAgentResearch:
        parameters = json.loads(experiment.parameters_json)
        metrics = json.loads(experiment.metrics_json)
        folds = tuple(CpuAgentFoldResult(
            fold_number=item["fold_number"], random_seed=item["random_seed"],
            train_objective=item["train_objective"],
            validation_objective=item["validation_objective"],
            train_metrics=EpisodeMetrics(**item["train_metrics"]),
            validation_metrics=EpisodeMetrics(**item["validation_metrics"]),
            test_metrics=EpisodeMetrics(**item["test_metrics"]),
            policy_weights=tuple(item["policy_weights"]),
            feature_mean=tuple(item["feature_mean"]),
            feature_std=tuple(item["feature_std"]),
        ) for item in metrics["folds"])
        summary_item = metrics["summary"]
        summary = WalkForwardPolicySummary(
            **{**summary_item, "failed_gates": tuple(summary_item["failed_gates"])}
        )
        return CpuAgentResearch(
            experiment_id=int(experiment.id or 0), symbol=parameters["symbol"],
            experiment_version=parameters["experiment_version"],
            data_fingerprint=parameters["data_fingerprint"],
            data_is_current=parameters["data_fingerprint"] == current_fingerprint,
            fold_count=len(folds), config=CpuAgentConfig(**parameters["agent_config"]),
            summary=summary, folds=folds, computed_at=experiment.created_at,
            reused=reused,
        )

    def run_cpu_agent(self, raw_symbol: str = "2330") -> CpuAgentResearch:
        symbol = self._normalize(raw_symbol)
        asset = self._universe.get(symbol)
        if asset is None:
            raise ValueError("股票不在研究股票池")
        bars = self._bars.list_bars(symbol)
        splits = ExpandingWalkForwardSplitter(self._walk_forward_config).split(bars)
        if len(splits) < self._walk_forward_config.minimum_folds:
            raise ValueError(
                f"CPU 代理至少需要 {self._walk_forward_config.minimum_folds} 個完整樣本外分段"
            )
        fingerprint = self._fingerprint(symbol, bars)
        name = f"rl_cpu_agent:{symbol}"
        latest = self._experiments.get_latest(name, "rl_cpu_agent")
        if latest is not None:
            parameters = json.loads(latest.parameters_json)
            if (
                parameters.get("experiment_version") == self.cpu_agent_version
                and parameters.get("data_fingerprint") == fingerprint
                and parameters.get("walk_forward_config") == asdict(self._walk_forward_config)
                and parameters.get("agent_config") == asdict(self._cpu_agent_config)
            ):
                return self._decode_cpu_agent(latest, fingerprint, reused=True)

        environment = OfflineTradingEnvironment(
            bars, asset_type=asset.asset_type, cost_model=TaiwanExecutionCostModel()
        )
        trainer = CpuPolicySearchTrainer(self._cpu_agent_config)
        fold_results = tuple(trainer.train_fold(environment, split) for split in splits)
        summary = self._summary(
            "cpu_linear_cem", "CPU 線性策略搜尋代理",
            [item.test_metrics for item in fold_results],
            self._walk_forward_config.minimum_folds,
        )
        now = datetime.now(UTC)
        experiment = ResearchExperiment(
            id=None, name=name, experiment_type="rl_cpu_agent",
            status=ExperimentStatus.SUCCEEDED,
            parameters_json=json.dumps({
                "symbol": symbol, "experiment_version": self.cpu_agent_version,
                "data_fingerprint": fingerprint, "bar_count": len(bars),
                "walk_forward_config": asdict(self._walk_forward_config),
                "agent_config": asdict(self._cpu_agent_config),
                "objective": "sharpe + return + 1.5*max_drawdown - 0.02*turnover",
            }, ensure_ascii=False, sort_keys=True),
            metrics_json=json.dumps({
                "summary": asdict(summary),
                "folds": [asdict(item) for item in fold_results],
            }, ensure_ascii=False, sort_keys=True),
            created_at=now,
        )
        experiment_id = self._experiments.save(experiment)
        saved = ResearchExperiment(
            id=experiment_id, name=experiment.name,
            experiment_type=experiment.experiment_type, status=experiment.status,
            parameters_json=experiment.parameters_json, metrics_json=experiment.metrics_json,
            created_at=experiment.created_at,
        )
        return self._decode_cpu_agent(saved, fingerprint)

    def _resolved_torch_config(self):
        from quant_platform.reinforcement_learning.agents import TorchAgentConfig

        return self._torch_agent_config or TorchAgentConfig()

    def _decode_neural_agent(
        self, experiment: ResearchExperiment, current_fingerprint: str,
        reused: bool = False,
    ):
        from quant_platform.reinforcement_learning.agents import (
            NeuralAgentResearch, TorchAgentConfig, TorchAgentFoldResult,
        )

        parameters = json.loads(experiment.parameters_json)
        metrics = json.loads(experiment.metrics_json)
        folds = tuple(TorchAgentFoldResult(
            fold_number=item["fold_number"], random_seed=item["random_seed"],
            device=item["device"], parameter_count=item["parameter_count"],
            train_objective=item["train_objective"],
            validation_objective=item["validation_objective"],
            train_metrics=EpisodeMetrics(**item["train_metrics"]),
            validation_metrics=EpisodeMetrics(**item["validation_metrics"]),
            test_metrics=EpisodeMetrics(**item["test_metrics"]),
            feature_mean=tuple(item["feature_mean"]),
            feature_std=tuple(item["feature_std"]),
            checkpoint=item["checkpoint"],
        ) for item in metrics["folds"])
        summary_item = metrics["summary"]
        summary = WalkForwardPolicySummary(
            **{**summary_item, "failed_gates": tuple(summary_item["failed_gates"])}
        )
        return NeuralAgentResearch(
            experiment_id=int(experiment.id or 0), symbol=parameters["symbol"],
            algorithm=parameters["algorithm"], label=parameters["label"],
            experiment_version=parameters["experiment_version"],
            data_fingerprint=parameters["data_fingerprint"],
            data_is_current=parameters["data_fingerprint"] == current_fingerprint,
            fold_count=len(folds), config=TorchAgentConfig(**parameters["agent_config"]),
            summary=summary, folds=folds, computed_at=experiment.created_at,
            reused=reused,
        )

    def run_neural_agent(self, algorithm: str, raw_symbol: str = "2330"):
        from quant_platform.reinforcement_learning.agents import (
            DqnAgentAdapter, PpoAgentAdapter, torch_capability,
        )

        capability = torch_capability()
        if not capability.installed:
            raise RuntimeError(capability.reason)
        normalized_algorithm = algorithm.strip().lower()
        adapter_type = {"ppo": PpoAgentAdapter, "dqn": DqnAgentAdapter}.get(
            normalized_algorithm
        )
        if adapter_type is None:
            raise ValueError("只支援 PPO 或 DQN")
        symbol = self._normalize(raw_symbol)
        asset = self._universe.get(symbol)
        if asset is None:
            raise ValueError("股票不在研究股票池")
        bars = self._bars.list_bars(symbol)
        splits = ExpandingWalkForwardSplitter(self._walk_forward_config).split(bars)
        if len(splits) < self._walk_forward_config.minimum_folds:
            raise ValueError(
                f"{normalized_algorithm.upper()} 至少需要 "
                f"{self._walk_forward_config.minimum_folds} 個完整樣本外分段"
            )
        config = self._resolved_torch_config()
        fingerprint = self._fingerprint(symbol, bars)
        experiment_type = f"rl_{normalized_algorithm}_agent"
        name = f"{experiment_type}:{symbol}"
        version = f"{experiment_type}-v1"
        latest = self._experiments.get_latest(name, experiment_type)
        if latest is not None:
            parameters = json.loads(latest.parameters_json)
            if (
                parameters.get("experiment_version") == version
                and parameters.get("data_fingerprint") == fingerprint
                and parameters.get("walk_forward_config") == asdict(self._walk_forward_config)
                and parameters.get("agent_config") == asdict(config)
            ):
                return self._decode_neural_agent(latest, fingerprint, reused=True)

        environment = OfflineTradingEnvironment(
            bars, asset_type=asset.asset_type, cost_model=TaiwanExecutionCostModel()
        )
        adapter = adapter_type(config)
        fold_results = tuple(adapter.train_fold(environment, split) for split in splits)
        summary = self._summary(
            normalized_algorithm, adapter.label,
            [item.test_metrics for item in fold_results],
            self._walk_forward_config.minimum_folds,
        )
        now = datetime.now(UTC)
        experiment = ResearchExperiment(
            id=None, name=name, experiment_type=experiment_type,
            status=ExperimentStatus.SUCCEEDED,
            parameters_json=json.dumps({
                "symbol": symbol, "algorithm": normalized_algorithm,
                "label": adapter.label, "experiment_version": version,
                "data_fingerprint": fingerprint, "bar_count": len(bars),
                "walk_forward_config": asdict(self._walk_forward_config),
                "agent_config": asdict(config),
            }, ensure_ascii=False, sort_keys=True),
            metrics_json=json.dumps({
                "summary": asdict(summary),
                "folds": [asdict(item) for item in fold_results],
            }, ensure_ascii=False, sort_keys=True),
            created_at=now,
        )
        experiment_id = self._experiments.save(experiment)
        saved = ResearchExperiment(
            id=experiment_id, name=experiment.name,
            experiment_type=experiment.experiment_type, status=experiment.status,
            parameters_json=experiment.parameters_json, metrics_json=experiment.metrics_json,
            created_at=experiment.created_at,
        )
        return self._decode_neural_agent(saved, fingerprint)

    def overview(self, raw_symbol: str = "2330") -> RlLabOverview:
        from quant_platform.reinforcement_learning.agents import torch_capability

        symbol = self._normalize(raw_symbol)
        asset = self._universe.get(symbol)
        bars = self._bars.list_bars(symbol) if asset else []
        notes: list[str] = []
        evaluations: list[PolicyEvaluation] = []
        costs = TaiwanExecutionCostModel()
        fingerprint = self._fingerprint(symbol, bars) if bars else None
        latest = self._experiments.get_latest(
            f"rl_walk_forward:{symbol}", "rl_walk_forward"
        )
        walk_forward = (
            self._decode_experiment(latest, fingerprint or "") if latest else None
        )
        latest_cpu_agent = self._experiments.get_latest(
            f"rl_cpu_agent:{symbol}", "rl_cpu_agent"
        )
        cpu_agent = (
            self._decode_cpu_agent(latest_cpu_agent, fingerprint or "")
            if latest_cpu_agent else None
        )
        neural_agents = []
        for algorithm in ("ppo", "dqn"):
            experiment_type = f"rl_{algorithm}_agent"
            latest_neural = self._experiments.get_latest(
                f"{experiment_type}:{symbol}", experiment_type
            )
            if latest_neural:
                neural_agents.append(
                    self._decode_neural_agent(latest_neural, fingerprint or "")
                )
        if asset is None:
            notes.append("股票不在研究股票池。")
        if len(bars) < 252:
            notes.append("少於 252 個交易日，只能驗證環境，不能進行可信的年度樣本外訓練。")
        if len(bars) >= 22 and asset:
            environment = OfflineTradingEnvironment(
                bars, asset_type=asset.asset_type, cost_model=costs
            )
            for name, label, policy in self._policies():
                evaluations.append(PolicyEvaluation(
                    policy_name=name, label=label,
                    metrics=evaluate_policy(environment, policy),
                ))
        if neural_agents:
            notes.append(
                "PPO／DQN 已完成離線 walk-forward 訓練，但未通過門檻前仍不可進入模擬交易。"
            )
        else:
            notes.append("目前尚無已保存的 PPO／DQN 實驗。")
        notes.append(
            "所有 RL 代理必須使用相同的訓練／驗證／測試切分與基準比較。"
        )
        return RlLabOverview(
            symbol=symbol, asset_found=asset is not None, bar_count=len(bars),
            first_date=bars[0].event_time if bars else None,
            last_date=bars[-1].event_time if bars else None,
            observation_size=len(OfflineTradingEnvironment.observation_names),
            observation_names=OfflineTradingEnvironment.observation_names,
            action_description="連續目標持倉權重 0%～80%，不允許放空",
            execution_description="t 日收盤決策，t+1 日開盤加滑價成交",
            initial_cash_twd=1_000_000.0, max_position_weight=0.8,
            commission_bps=float(costs.commission_bps),
            stock_sell_tax_bps=float(costs.stock_sell_tax_bps),
            etf_sell_tax_bps=float(costs.etf_sell_tax_bps),
            slippage_bps=float(costs.slippage_bps),
            evaluations=tuple(evaluations), ready_for_training=len(bars) >= 756,
            readiness_notes=tuple(notes),
            walk_forward=walk_forward, cpu_agent=cpu_agent,
            torch_capability=torch_capability(),
            neural_agents=tuple(neural_agents),
            current_data_fingerprint=fingerprint,
        )
