from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quant_platform.reinforcement_learning import (
    DqnAgentAdapter, PpoAgentAdapter, TorchAgentConfig, torch_capability,
)
from quant_platform.reinforcement_learning.agents import policy_from_neural_research
from quant_platform.reinforcement_learning.environment import RlEnvironmentService
from quant_platform.domain.entities import MarketBar
from quant_platform.reinforcement_learning.environment import (
    ExpandingWalkForwardSplitter, OfflineTradingEnvironment, WalkForwardConfig,
    latest_market_observation,
)


class _Experiments:
    def __init__(self):
        self.values = []

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

    def save(self, experiment):
        self.values.append(experiment)
        return len(self.values)


def test_torch_capability_and_agent_adapters_load_without_eager_dependency():
    capability = torch_capability()
    assert isinstance(capability.installed, bool)
    assert capability.device_name
    assert capability.reason
    assert PpoAgentAdapter.algorithm == "ppo"
    assert DqnAgentAdapter.algorithm == "dqn"


def test_invalid_common_torch_agent_configuration_is_rejected():
    with pytest.raises(ValueError, match="Hidden size"):
        PpoAgentAdapter(TorchAgentConfig(hidden_size=4))
    with pytest.raises(ValueError, match="episodes"):
        DqnAgentAdapter(TorchAgentConfig(training_episodes=0))


def test_missing_torch_returns_clear_training_error():
    capability = torch_capability()
    if capability.installed:
        pytest.skip("PyTorch is installed in this environment")
    service = RlEnvironmentService(
        SimpleNamespace(list_bars=lambda symbol: []),
        SimpleNamespace(get=lambda symbol: SimpleNamespace(asset_type="EQUITY")),
        _Experiments(),
    )
    with pytest.raises(RuntimeError, match="pip install"):
        service.run_neural_agent("ppo", "2330")


def _bars(count: int = 115) -> list[MarketBar]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    output = []
    for index in range(count):
        close = Decimal("100") + Decimal(str(index % 17)) + Decimal(index) / 10
        event = start + timedelta(days=index)
        output.append(MarketBar(
            symbol="2330.TW", market="TW", interval="1d", event_time=event,
            available_time=event + timedelta(hours=6),
            ingested_at=event + timedelta(hours=6), open=close - Decimal("0.2"),
            high=close + 1, low=close - 1, close=close,
            adjusted_close=close, volume=1_000_000, source="test",
        ))
    return output


@pytest.mark.parametrize("adapter_type", [PpoAgentAdapter, DqnAgentAdapter])
def test_pytorch_agents_complete_train_validation_and_unseen_test(adapter_type):
    if not torch_capability().installed:
        pytest.skip("PyTorch optional dependency is not installed")
    bars = _bars()
    split = ExpandingWalkForwardSplitter(WalkForwardConfig(
        minimum_train_bars=60, validation_bars=20, test_bars=20,
        embargo_bars=2, minimum_folds=1,
    )).split(bars)[0]
    config = TorchAgentConfig(
        hidden_size=8, training_episodes=1, ppo_update_epochs=1,
        dqn_batch_size=16, dqn_replay_capacity=200,
        dqn_target_update_steps=20, requested_device="cpu", random_seed=123,
    )
    result = adapter_type(config).train_fold(OfflineTradingEnvironment(bars), split)
    assert result.fold_number == 1
    assert result.device == "cpu"
    assert result.parameter_count > 0
    assert result.checkpoint
    assert result.test_metrics.steps == split.test_end - split.test_start
    assert result.test_metrics.total_cost_twd >= 0


def test_neural_agent_service_saves_checkpoint_and_reuses_snapshot():
    if not torch_capability().installed:
        pytest.skip("PyTorch optional dependency is not installed")
    bars = _bars()
    experiments = _Experiments()
    service = RlEnvironmentService(
        SimpleNamespace(list_bars=lambda symbol: bars),
        SimpleNamespace(get=lambda symbol: SimpleNamespace(asset_type="EQUITY")),
        experiments,
        walk_forward_config=WalkForwardConfig(
            minimum_train_bars=60, validation_bars=20, test_bars=20,
            embargo_bars=2, minimum_folds=1,
        ),
        torch_agent_config=TorchAgentConfig(
            hidden_size=8, training_episodes=1, ppo_update_epochs=1,
            dqn_batch_size=16, requested_device="cpu", random_seed=456,
        ),
    )
    first = service.run_neural_agent("ppo", "2330")
    second = service.run_neural_agent("ppo", "2330")
    assert first.algorithm == "ppo"
    assert first.fold_count == 1
    assert first.folds[0].checkpoint
    inference_policy = policy_from_neural_research(first)
    target_weight = inference_policy(latest_market_observation(bars))
    assert 0 <= target_weight <= 0.8
    assert second.reused is True
    assert second.experiment_id == first.experiment_id
    assert len(experiments.values) == 1
