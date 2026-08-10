from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from quant_platform.domain.entities import (
    BacktestArtifact,
    BacktestEquityPoint,
    BacktestRun,
    RegimeState,
)
from quant_platform.ensemble import DynamicStrategyEnsembleEngine


NAMES = ("trend_momentum", "mean_reversion", "regime_aware")


def _components(days: int = 80) -> list[BacktestArtifact]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    patterns = {
        "trend_momentum": [0.003 if index % 5 else -0.001 for index in range(days)],
        "mean_reversion": [0.001 if index % 2 else -0.0005 for index in range(days)],
        "regime_aware": [0.0015 if index % 4 else 0.0 for index in range(days)],
    }
    output = []
    for name in NAMES:
        returns = patterns[name]
        equity = 1.0
        points = []
        for index, value in enumerate(returns):
            equity *= 1 + value
            points.append(
                BacktestEquityPoint(
                    event_time=start + timedelta(days=index),
                    equity=equity,
                    benchmark_equity=1.0,
                    drawdown=0.0,
                    daily_return=value,
                    position=1.0,
                )
            )
        run = BacktestRun(
            id=None,
            symbol="SPY",
            market="US",
            strategy_name=name,
            strategy_version="1.0.0",
            research_version="1.0.0",
            promotion_gate="CANDIDATE",
            data_start=points[0].event_time,
            data_end=points[-1].event_time,
            fold_count=4,
            observation_count=days,
            total_return=equity - 1,
            annual_return=0.1,
            benchmark_return=0.05,
            excess_return=0.05,
            sharpe=1.0,
            sortino=1.0,
            calmar=1.0,
            max_drawdown=-0.05,
            win_rate=0.6,
            profit_factor=1.2,
            turnover=1.0,
            exposure=1.0,
            alpha=0.01,
            beta=0.5,
            information_ratio=0.5,
            trade_count=10,
            positive_fold_rate=0.75,
            commission_bps=2.0,
            slippage_bps=3.0,
            parameters_json="{}",
            limitations_json="[]",
            computed_at=start,
        )
        output.append(BacktestArtifact(run=run, folds=[], trades=[], equity=points))
    return output


def _regimes(days: int = 80, trend: str = "BULL") -> list[RegimeState]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return [
        RegimeState(
            symbol="SPY",
            event_time=start + timedelta(days=index),
            available_time=start + timedelta(days=index, hours=1),
            regime_version="1.0.0",
            trend_regime=trend,
            volatility_regime="LOW_VOL",
            composite_regime=f"{trend}_LOW_VOL",
            trend_score=0.1,
            volatility_score=0.0,
            confidence=0.8,
            computed_at=start,
        )
        for index in range(days)
    ]


def test_current_return_never_changes_current_weight() -> None:
    components = _components()
    engine = DynamicStrategyEnsembleEngine()
    original = engine.run(components, _regimes())
    mutated_points = list(components[0].equity)
    target = 35
    mutated_points[target] = replace(mutated_points[target], daily_return=-0.80)
    mutated = [replace(components[0], equity=mutated_points), *components[1:]]
    changed = engine.run(mutated, _regimes())
    assert original is not None and changed is not None
    assert original.weights[target].weights_json == changed.weights[target].weights_json
    assert original.weights[target + 1].weights_json != changed.weights[target + 1].weights_json


def test_regime_prior_changes_next_session_allocation() -> None:
    engine = DynamicStrategyEnsembleEngine(min_history=999)
    bull = engine.run(_components(), _regimes(trend="BULL"))
    bear = engine.run(_components(), _regimes(trend="BEAR"))
    assert bull is not None and bear is not None
    bull_weights = json.loads(bull.weights[1].weights_json)
    bear_weights = json.loads(bear.weights[1].weights_json)
    assert bull_weights["trend_momentum"] > bear_weights["trend_momentum"]
    assert bear_weights["regime_aware"] > bull_weights["regime_aware"]


def test_allocation_cost_is_deducted() -> None:
    components = _components()
    regimes = _regimes()
    free = DynamicStrategyEnsembleEngine(allocation_cost_bps=0).run(components, regimes)
    costly = DynamicStrategyEnsembleEngine(allocation_cost_bps=50).run(components, regimes)
    assert free is not None and costly is not None
    assert costly.run.total_return < free.run.total_return
    assert all(0.10 <= value <= 0.65 for value in json.loads(costly.run.latest_weights_json).values())
