from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from quant_platform.domain.entities import (
    EnsembleArtifact,
    EnsembleEquityPoint,
    EnsembleRun,
    EnsembleWeightPoint,
)
from quant_platform.portfolio import PORTFOLIO_METHODS, PortfolioRiskEngine
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import SqlAlchemyPortfolioResearchRepository


def _ensembles(days: int = 220) -> list[EnsembleArtifact]:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    symbols = ["AAA", "BBB", "CCC", "DDD"]
    artifacts = []
    for asset_index, symbol in enumerate(symbols):
        equity = 1.0
        weights = []
        curve = []
        for index in range(days):
            value = (
                0.0002 * (asset_index + 1)
                + 0.003 * (((index * (asset_index + 2)) % 11) - 5) / 5
            )
            event_time = start + timedelta(days=index)
            equity *= 1 + value
            weights.append(
                EnsembleWeightPoint(
                    event_time=event_time,
                    regime="BULL_LOW_VOL",
                    weights_json='{"mean_reversion": 0.3, "regime_aware": 0.3, "trend_momentum": 0.4}',
                    scores_json="{}",
                    contributions_json="{}",
                    turnover=0.0,
                    cost=0.0,
                    daily_return=value,
                )
            )
            curve.append(
                EnsembleEquityPoint(
                    event_time=event_time,
                    equity=equity,
                    equal_weight_equity=equity,
                    drawdown=0.0,
                )
            )
        run = EnsembleRun(
            id=asset_index + 1,
            symbol=symbol,
            market="US",
            ensemble_version="1.0.0",
            promotion_gate="CANDIDATE",
            data_start=weights[0].event_time,
            data_end=weights[-1].event_time,
            observation_count=days,
            component_count=3,
            total_return=equity - 1,
            annual_return=0.1,
            equal_weight_return=0.05,
            excess_to_equal=0.05,
            sharpe=1.0,
            equal_weight_sharpe=0.5,
            max_drawdown=-0.1,
            turnover=1.0,
            allocation_cost_bps=2.0,
            average_weights_json="{}",
            latest_weights_json="{}",
            regime_metrics_json="{}",
            limitations_json="[]",
            computed_at=start,
        )
        artifacts.append(EnsembleArtifact(run=run, weights=weights, equity=curve))
    return artifacts


def _context(days: int = 220):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    benchmark = {
        (start + timedelta(days=index)).date(): 0.0003 + 0.002 * ((index % 7) - 3) / 3
        for index in range(days)
    }
    sectors = {"AAA": "A", "BBB": "B", "CCC": "C", "DDD": "D"}
    liquidity = {"AAA": 10, "BBB": 20, "CCC": 30, "DDD": 40}
    return benchmark, sectors, liquidity


def test_all_portfolio_methods_are_constrained_and_auditable() -> None:
    ensembles = _ensembles()
    benchmark, sectors, liquidity = _context()
    engine = PortfolioRiskEngine(max_asset_weight=0.35, max_sector_weight=0.50)
    for method in PORTFOLIO_METHODS:
        artifact = engine.run(method, ensembles, benchmark, sectors, liquidity)
        assert artifact is not None, method
        weights = json.loads(artifact.run.latest_weights_json)
        assert set(weights) == {"AAA", "BBB", "CCC", "DDD"}
        assert sum(weights.values()) <= 1.0000001
        assert max(weights.values()) <= 0.3500001
        assert artifact.run.observation_count == 160
        assert artifact.allocations
        assert artifact.equity
        assert json.loads(artifact.run.stress_tests_json)["equity_shock_20pct"] <= 0
        assert 0 <= artifact.run.liquidity_risk <= 1


def test_rebalance_at_t_never_uses_return_at_t() -> None:
    ensembles = _ensembles()
    benchmark, sectors, liquidity = _context()
    engine = PortfolioRiskEngine()
    original = engine.run("inverse_volatility", ensembles, benchmark, sectors, liquidity)
    target = 60
    mutated_points = list(ensembles[0].weights)
    mutated_points[target] = replace(mutated_points[target], daily_return=-0.90)
    mutated = [replace(ensembles[0], weights=mutated_points), *ensembles[1:]]
    changed = engine.run("inverse_volatility", mutated, benchmark, sectors, liquidity)
    assert original is not None and changed is not None
    assert original.allocations[0].weights_json == changed.allocations[0].weights_json
    assert original.equity[0].daily_return != changed.equity[0].daily_return
    assert original.allocations[1].weights_json != changed.allocations[1].weights_json


def test_turnover_cost_reduces_portfolio_return() -> None:
    ensembles = _ensembles()
    benchmark, sectors, liquidity = _context()
    free = PortfolioRiskEngine(transaction_cost_bps=0).run(
        "mean_variance", ensembles, benchmark, sectors, liquidity
    )
    costly = PortfolioRiskEngine(transaction_cost_bps=50).run(
        "mean_variance", ensembles, benchmark, sectors, liquidity
    )
    assert free is not None and costly is not None
    assert costly.run.total_return < free.run.total_return


def test_portfolio_repository_replaces_same_snapshot_atomically(tmp_path) -> None:
    ensembles = _ensembles()
    benchmark, sectors, liquidity = _context()
    artifact = PortfolioRiskEngine().run(
        "risk_parity", ensembles, benchmark, sectors, liquidity
    )
    assert artifact is not None
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'portfolio.db'}"))
    repository = SqlAlchemyPortfolioResearchRepository(container.database.session_factory)
    first_id = repository.save(artifact)
    second_id = repository.save(artifact)
    assert first_id == second_id
    assert len(repository.list_runs()) == 1
    restored = repository.get(first_id)
    assert restored is not None
    assert len(restored.allocations) == len(artifact.allocations)
    assert len(restored.equity) == len(artifact.equity)
