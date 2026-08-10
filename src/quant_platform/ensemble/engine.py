from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime

import numpy as np

from quant_platform.domain.entities import (
    BacktestArtifact,
    EnsembleArtifact,
    EnsembleEquityPoint,
    EnsembleRun,
    EnsembleWeightPoint,
    RegimeState,
)


ENSEMBLE_VERSION = "1.0.0"
EXPECTED_COMPONENTS = ("trend_momentum", "mean_reversion", "regime_aware")
REGIME_PRIORS = {
    "BULL": {"trend_momentum": 0.35, "mean_reversion": -0.20, "regime_aware": 0.25},
    "RANGE": {"trend_momentum": -0.15, "mean_reversion": 0.35, "regime_aware": 0.20},
    "BEAR": {"trend_momentum": -0.40, "mean_reversion": 0.10, "regime_aware": 0.40},
}


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    deviation = statistics.stdev(returns)
    return statistics.fmean(returns) / deviation * math.sqrt(252) if deviation else 0.0


def _drawdown(returns: list[float]) -> float:
    equity = peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    return worst


def _bounded_softmax(
    scores: dict[str, float], temperature: float, minimum: float, maximum: float
) -> dict[str, float]:
    names = list(scores)
    values = np.array([scores[name] for name in names], dtype=float) / temperature
    values -= values.max()
    raw = np.exp(values)
    raw /= raw.sum()
    weights = np.full(len(names), minimum, dtype=float)
    remaining = 1.0 - minimum * len(names)
    preference = raw.copy()
    active = set(range(len(names)))
    while active and remaining > 1e-12:
        total = sum(preference[index] for index in active)
        if total <= 0:
            addition = remaining / len(active)
            for index in active:
                weights[index] += addition
            break
        capped = []
        for index in active:
            proposed = remaining * preference[index] / total
            capacity = maximum - weights[index]
            if proposed >= capacity - 1e-12:
                weights[index] += capacity
                remaining -= capacity
                capped.append(index)
        if not capped:
            for index in active:
                weights[index] += remaining * preference[index] / total
            remaining = 0.0
        else:
            active.difference_update(capped)
    weights /= weights.sum()
    return {name: float(weights[index]) for index, name in enumerate(names)}


class DynamicStrategyEnsembleEngine:
    """Combines strictly out-of-sample strategy returns using lagged adaptive weights."""

    version = ENSEMBLE_VERSION

    def __init__(
        self,
        lookback: int = 42,
        min_history: int = 20,
        temperature: float = 0.75,
        minimum_weight: float = 0.10,
        maximum_weight: float = 0.65,
        allocation_cost_bps: float = 2.0,
    ) -> None:
        self.lookback = lookback
        self.min_history = min_history
        self.temperature = temperature
        self.minimum_weight = minimum_weight
        self.maximum_weight = maximum_weight
        self.allocation_cost_bps = allocation_cost_bps

    def run(
        self,
        components: list[BacktestArtifact],
        regimes: list[RegimeState],
        computed_at: datetime | None = None,
    ) -> EnsembleArtifact | None:
        by_name = {item.run.strategy_name: item for item in components}
        if any(name not in by_name for name in EXPECTED_COMPONENTS):
            return None
        selected = {name: by_name[name] for name in EXPECTED_COMPONENTS}
        symbols = {artifact.run.symbol for artifact in selected.values()}
        markets = {artifact.run.market for artifact in selected.values()}
        if len(symbols) != 1 or len(markets) != 1:
            raise ValueError("ensemble components must represent one asset and market")

        return_maps = {
            name: {point.event_time: point.daily_return for point in artifact.equity}
            for name, artifact in selected.items()
        }
        common_dates = sorted(set.intersection(*(set(values) for values in return_maps.values())))
        if not common_dates:
            return None
        regime_by_date = {item.event_time.date(): item.composite_regime for item in regimes}
        histories: dict[str, list[float]] = {name: [] for name in EXPECTED_COMPONENTS}
        previous_weights = {name: 1 / len(EXPECTED_COMPONENTS) for name in EXPECTED_COMPONENTS}
        ensemble_returns: list[float] = []
        equal_returns: list[float] = []
        weight_points: list[EnsembleWeightPoint] = []
        equity_points: list[EnsembleEquityPoint] = []
        weight_history: dict[str, list[float]] = defaultdict(list)
        regime_returns: dict[str, list[float]] = defaultdict(list)
        ensemble_equity = equal_equity = peak = 1.0
        total_turnover = 0.0
        prior_regime = "UNKNOWN"

        for event_time in common_dates:
            scores: dict[str, float] = {}
            regime_class = prior_regime.split("_", 1)[0]
            prior = REGIME_PRIORS.get(regime_class, {})
            for name in EXPECTED_COMPONENTS:
                trailing = histories[name][-self.lookback :]
                performance = 0.0
                if len(trailing) >= self.min_history:
                    performance = float(np.clip(_sharpe(trailing), -2.0, 2.0))
                    performance += max(_drawdown(trailing), -0.35)
                scores[name] = performance + prior.get(name, 0.0)
            weights = _bounded_softmax(
                scores, self.temperature, self.minimum_weight, self.maximum_weight
            )
            turnover = sum(abs(weights[name] - previous_weights[name]) for name in weights)
            cost = turnover * self.allocation_cost_bps / 10_000
            current = {name: return_maps[name][event_time] for name in EXPECTED_COMPONENTS}
            contributions = {name: weights[name] * current[name] for name in EXPECTED_COMPONENTS}
            daily_return = sum(contributions.values()) - cost
            equal_return = statistics.fmean(current.values())
            ensemble_equity *= 1 + daily_return
            equal_equity *= 1 + equal_return
            peak = max(peak, ensemble_equity)
            drawdown = ensemble_equity / peak - 1
            ensemble_returns.append(daily_return)
            equal_returns.append(equal_return)
            total_turnover += turnover
            regime_returns[prior_regime].append(daily_return)
            for name, value in weights.items():
                weight_history[name].append(value)
            weight_points.append(
                EnsembleWeightPoint(
                    event_time=event_time,
                    regime=prior_regime,
                    weights_json=json.dumps(weights, sort_keys=True),
                    scores_json=json.dumps(scores, sort_keys=True),
                    contributions_json=json.dumps(contributions, sort_keys=True),
                    turnover=turnover,
                    cost=cost,
                    daily_return=daily_return,
                )
            )
            equity_points.append(
                EnsembleEquityPoint(
                    event_time=event_time,
                    equity=ensemble_equity,
                    equal_weight_equity=equal_equity,
                    drawdown=drawdown,
                )
            )
            for name in EXPECTED_COMPONENTS:
                histories[name].append(current[name])
            previous_weights = weights
            prior_regime = regime_by_date.get(event_time.date(), prior_regime)

        observations = len(common_dates)
        total_return = ensemble_equity - 1
        equal_return = equal_equity - 1
        annual_return = ensemble_equity ** (252 / observations) - 1 if observations else 0.0
        sharpe = _sharpe(ensemble_returns)
        equal_sharpe = _sharpe(equal_returns)
        max_drawdown = min(point.drawdown for point in equity_points)
        component_limitations = [
            f"{name}: component is not a promotion candidate"
            for name, artifact in selected.items()
            if artifact.run.promotion_gate != "CANDIDATE"
        ]
        if any("not survivorship-safe" in artifact.run.limitations_json for artifact in selected.values()):
            component_limitations.append("component universe is not survivorship-safe")
        gates_pass = (
            observations >= 252
            and total_return > equal_return
            and sharpe > equal_sharpe
            and max_drawdown >= -0.25
            and not component_limitations
        )
        limitations = [
            "Weights use trailing component OOS returns through t-1 only.",
            "Regime prior uses the most recently known state before allocation.",
            "Allocation turnover is charged but broker-specific market impact is not yet modeled.",
            *component_limitations,
        ]
        regime_metrics = {
            key: {
                "observations": len(values),
                "return": math.prod(1 + value for value in values) - 1,
                "sharpe": _sharpe(values),
            }
            for key, values in regime_returns.items()
        }
        calculated_at = computed_at or datetime.now(UTC)
        run = EnsembleRun(
            id=None,
            symbol=next(iter(symbols)),
            market=next(iter(markets)),
            ensemble_version=self.version,
            promotion_gate="CANDIDATE" if gates_pass else "RESEARCH",
            data_start=common_dates[0],
            data_end=common_dates[-1],
            observation_count=observations,
            component_count=len(EXPECTED_COMPONENTS),
            total_return=total_return,
            annual_return=annual_return,
            equal_weight_return=equal_return,
            excess_to_equal=total_return - equal_return,
            sharpe=sharpe,
            equal_weight_sharpe=equal_sharpe,
            max_drawdown=max_drawdown,
            turnover=total_turnover,
            allocation_cost_bps=self.allocation_cost_bps,
            average_weights_json=json.dumps(
                {name: statistics.fmean(values) for name, values in weight_history.items()},
                sort_keys=True,
            ),
            latest_weights_json=weight_points[-1].weights_json,
            regime_metrics_json=json.dumps(regime_metrics, sort_keys=True),
            limitations_json=json.dumps(limitations),
            computed_at=calculated_at,
        )
        return EnsembleArtifact(run=run, weights=weight_points, equity=equity_points)
