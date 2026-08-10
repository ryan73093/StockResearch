import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from quant_platform.application.model_governance import (
    ModelGovernancePolicy, ModelGovernanceService,
)
from quant_platform.database import Database
from quant_platform.database.repositories import (
    SqlAlchemyModelGovernanceRepository,
    SqlAlchemyModelResearchRepository,
)
from quant_platform.domain.entities import (
    DriftStatus, FeatureValue, ModelExperiment, ModelRegistryStatus,
)


class _Models:
    def __init__(self, repository, runs):
        self.repository = repository
        self.runs = list(runs)

    def append(self, run):
        run_id = self.repository.save(run)
        self.runs.append(replace(run, id=run_id))

    def list_runs(self, market=None):
        return [item for item in self.runs if market is None or item.market == market]

    def get(self, run_id):
        return next((item for item in self.runs if item.id == run_id), None)

    def list_predictions(self, market=None, symbol=None):
        return []


def _experiment(run_id=1, candidate=True, computed_offset=0):
    now = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=computed_offset)
    return ModelExperiment(
        id=run_id, market="TW", model_name="ridge_linear", model_version="1",
        label_name="future_return_5d", experiment_version="test",
        promotion_gate="CANDIDATE" if candidate else "RESEARCH",
        data_start=now - timedelta(days=500), data_end=now,
        observation_count=1200, fold_count=4, feature_count=3,
        rmse=0.02, mae=0.01, r2=0.10, directional_accuracy=0.58,
        rank_ic=0.08, long_short_spread=0.03,
        feature_names_json=json.dumps(["f1", "f2", "f3"]),
        parameters_json="{}", fold_metrics_json="[]",
        feature_importance_json="{}", limitations_json="[]",
        computed_at=now,
    )


def _features():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    values = []
    for index in range(380):
        event = start + timedelta(days=index)
        for feature_index, name in enumerate(("f1", "f2", "f3")):
            values.append(FeatureValue(
                symbol="2330.TW", feature_name=name, feature_version="1",
                event_time=event, available_time=event + timedelta(hours=1),
                computed_at=event + timedelta(hours=1),
                value=float((index % 20) + feature_index),
            ))
    return values


def _service(tmp_path, runs):
    database = Database(f"sqlite:///{tmp_path / 'governance.db'}")
    database.create_schema()
    repository = SqlAlchemyModelGovernanceRepository(database.session_factory)
    model_repository = SqlAlchemyModelResearchRepository(database.session_factory)
    persisted = [replace(run, id=model_repository.save(run)) for run in runs]
    models = _Models(model_repository, persisted)
    features = SimpleNamespace(list_features=lambda symbols, names=None: _features())
    universe = SimpleNamespace(list_active=lambda market: [SimpleNamespace(symbol="2330.TW")])
    policy = ModelGovernancePolicy(
        psi_warning=999, psi_critical=999, psi_feature_max_critical=999,
    )
    return ModelGovernanceService(repository, models, features, universe, policy), models


def test_registry_requires_all_checks_then_supports_manual_champion(tmp_path):
    service, _ = _service(tmp_path, [_experiment()])
    refreshed = service.refresh()
    assert refreshed.registered == 1
    assert refreshed.drift_snapshots == 1
    view = service.overview().entries[0]
    assert view.entry.status == ModelRegistryStatus.CHALLENGER
    assert view.drift.status == DriftStatus.STABLE
    assert view.eligible is True
    promoted = service.promote(
        view.entry.id, "研究員甲", "所有樣本外品質與漂移檢核均通過，升級研究冠軍。",
    )
    assert promoted.entry.status == ModelRegistryStatus.CHAMPION
    assert service.overview().champion_count == 1


def test_new_non_candidate_snapshot_automatically_demotes_old_champion(tmp_path):
    service, models = _service(tmp_path, [_experiment()])
    service.refresh()
    entry = service.overview().entries[0]
    service.promote(
        entry.entry.id, "研究員甲", "先建立有效冠軍，之後驗證新版本失效會自動降級。",
    )
    models.append(_experiment(run_id=2, candidate=False, computed_offset=10))
    result = service.refresh()
    assert result.registered == 1
    assert result.automatically_demoted == 1
    states = {item.entry.experiment_id: item.entry.status for item in service.overview().entries}
    assert states[1] == ModelRegistryStatus.DEMOTED
    assert states[2] == ModelRegistryStatus.CHALLENGER


def test_non_candidate_model_cannot_be_manually_promoted(tmp_path):
    service, _ = _service(tmp_path, [_experiment(candidate=False)])
    service.refresh()
    view = service.overview().entries[0]
    assert view.eligible is False
    with pytest.raises(PermissionError, match="全部品質"):
        service.promote(
            view.entry.id, "研究員甲", "即使人工操作也必須由後端拒絕這次模型升級。",
        )


def test_population_stability_index_detects_distribution_shift():
    baseline = np.linspace(-1, 1, 1000)
    stable = np.linspace(-1, 1, 1000)
    shifted = np.linspace(5, 7, 1000)
    stable_psi = ModelGovernanceService.population_stability_index(baseline, stable)
    shifted_psi = ModelGovernanceService.population_stability_index(baseline, shifted)
    assert stable_psi == pytest.approx(0.0)
    assert shifted_psi is not None and shifted_psi > 0.25
