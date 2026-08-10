from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import numpy as np

from quant_platform.domain.entities import MarketBar
from quant_platform.reinforcement_learning.environment import (
    CpuAgentConfig, CpuPolicySearchTrainer, ExpandingWalkForwardSplitter,
    OfflineTradingEnvironment, RlEnvironmentService, WalkForwardConfig,
    evaluate_policy,
)


def _bars(count: int = 45, future_open_multiplier: Decimal = Decimal("1")) -> list[MarketBar]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    output = []
    for index in range(count):
        close = Decimal("100") + Decimal(index)
        open_price = close - Decimal("0.5")
        if index == 21:
            open_price *= future_open_multiplier
        event = start + timedelta(days=index)
        output.append(MarketBar(
            symbol="2330.TW", market="TW", interval="1d", event_time=event,
            available_time=event + timedelta(hours=6),
            ingested_at=event + timedelta(hours=6), open=open_price,
            high=max(open_price, close) + 1, low=min(open_price, close) - 1,
            close=close, adjusted_close=close, volume=1_000_000, source="test",
        ))
    return output


def test_observation_cannot_see_next_open_and_execution_uses_it():
    normal = OfflineTradingEnvironment(_bars())
    gapped = OfflineTradingEnvironment(_bars(future_open_multiplier=Decimal("2")))
    normal_observation = normal.reset()
    gapped_observation = gapped.reset()
    np.testing.assert_allclose(normal_observation.vector(), gapped_observation.vector())
    _, _, _, normal_info = normal.step(0.8)
    _, _, _, gap_info = gapped.step(0.8)
    assert gap_info["execution_open_twd_per_share"] == 2 * normal_info["execution_open_twd_per_share"]
    assert gap_info["executed_share_change"] < normal_info["executed_share_change"]


def test_buy_and_hold_pays_shared_costs_while_cash_policy_does_not():
    environment = OfflineTradingEnvironment(_bars())
    cash = evaluate_policy(environment, lambda _: 0.0)
    invested = evaluate_policy(environment, lambda _: 0.8)
    assert cash.total_return == 0
    assert cash.total_cost_twd == 0
    assert invested.trade_count >= 1
    assert invested.total_cost_twd > 0
    assert invested.turnover > 0


def test_action_is_clipped_and_environment_terminates_without_shorting():
    environment = OfflineTradingEnvironment(_bars(count=24), max_position_weight=0.8)
    observation = environment.reset()
    terminated = False
    while not terminated:
        observation, reward, terminated, info = environment.step(5.0)
        assert observation.position_weight <= 0.81
        assert environment.quantity >= 0
        assert np.isfinite(reward)
        assert info["commission_twd"] >= 0
    metrics = environment.metrics()
    assert metrics.steps == 3
    assert metrics.max_drawdown <= 0


def test_expanding_walk_forward_has_embargo_and_non_overlapping_tests():
    bars = _bars(1050)
    config = WalkForwardConfig(
        minimum_train_bars=504, validation_bars=63, test_bars=63, embargo_bars=5
    )
    splits = ExpandingWalkForwardSplitter(config).split(bars)
    assert len(splits) == 4
    assert splits[0].train_end == 503
    assert splits[0].validation_start - splits[0].train_end - 1 == 5
    assert splits[0].test_start - splits[0].validation_end - 1 == 5
    for previous, current in zip(splits, splits[1:]):
        assert current.train_end == previous.test_end
        assert current.test_start > previous.test_end


class _ExperimentMemory:
    def __init__(self) -> None:
        self.values = []

    def save(self, experiment):
        self.values.append(experiment)
        return len(self.values)

    def get_latest(self, name, experiment_type):
        matches = [
            item for item in self.values
            if item.name == name and item.experiment_type == experiment_type
        ]
        if not matches:
            return None
        item = matches[-1]
        return type(item)(
            id=len(self.values), name=item.name, experiment_type=item.experiment_type,
            status=item.status, parameters_json=item.parameters_json,
            metrics_json=item.metrics_json, created_at=item.created_at,
        )


def test_walk_forward_research_is_saved_and_same_snapshot_is_reused():
    bars = _bars(1050)
    bar_repository = SimpleNamespace(list_bars=lambda symbol: bars)
    universe = SimpleNamespace(get=lambda symbol: SimpleNamespace(asset_type="EQUITY"))
    experiments = _ExperimentMemory()
    service = RlEnvironmentService(bar_repository, universe, experiments)

    first = service.run_walk_forward("2330")
    second = service.run_walk_forward("2330")

    assert first.fold_count == 4
    assert first.experiment_id == 1
    assert second.experiment_id == 1
    assert second.reused is True
    assert len(experiments.values) == 1
    assert all(item.fold_count == 4 for item in first.policies)
    assert all(item.promotion_gate in {"CANDIDATE", "RESEARCH_ONLY"} for item in first.policies)
    assert len(first.data_fingerprint) == 64


def test_cpu_agent_is_deterministic_and_future_test_prices_do_not_change_training():
    bars = _bars(130)
    config = WalkForwardConfig(
        minimum_train_bars=60, validation_bars=20, test_bars=20,
        embargo_bars=2, minimum_folds=1,
    )
    split = ExpandingWalkForwardSplitter(config).split(bars)[0]
    agent_config = CpuAgentConfig(
        population_size=6, generations=2, elite_fraction=0.34,
        random_seed=77,
    )
    first = CpuPolicySearchTrainer(agent_config).train_fold(
        OfflineTradingEnvironment(bars), split
    )
    changed = list(bars)
    for index in range(split.test_start, split.test_end + 1):
        changed[index] = replace(
            changed[index], open=changed[index].open * Decimal("1.5"),
            high=changed[index].high * Decimal("1.5"),
            low=changed[index].low * Decimal("1.5"),
            close=changed[index].close * Decimal("1.5"),
        )
    second = CpuPolicySearchTrainer(agent_config).train_fold(
        OfflineTradingEnvironment(changed), split
    )
    np.testing.assert_allclose(first.policy_weights, second.policy_weights)
    np.testing.assert_allclose(first.feature_mean, second.feature_mean)
    assert first.validation_objective == second.validation_objective
    assert first.test_metrics.total_return != second.test_metrics.total_return


def test_cpu_agent_experiment_is_saved_and_reused():
    bars = _bars(180)
    bar_repository = SimpleNamespace(list_bars=lambda symbol: bars)
    universe = SimpleNamespace(get=lambda symbol: SimpleNamespace(asset_type="EQUITY"))
    experiments = _ExperimentMemory()
    service = RlEnvironmentService(
        bar_repository, universe, experiments,
        walk_forward_config=WalkForwardConfig(
            minimum_train_bars=60, validation_bars=20, test_bars=20,
            embargo_bars=2, minimum_folds=2,
        ),
        cpu_agent_config=CpuAgentConfig(
            population_size=6, generations=2, elite_fraction=0.34,
            random_seed=91,
        ),
    )
    first = service.run_cpu_agent("2330")
    second = service.run_cpu_agent("2330")
    assert first.fold_count >= 2
    assert len(first.folds[0].policy_weights) == 8
    assert len(first.folds[0].feature_mean) == 5
    assert second.reused is True
    assert second.experiment_id == first.experiment_id
    assert len(experiments.values) == 1
