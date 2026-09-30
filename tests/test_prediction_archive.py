from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pyarrow.parquet as pq

from quant_platform.application.prediction_archive import (
    PredictionArchiveService,
    active_registry_experiment_ids,
    experiments_in_use,
)
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.models import ModelExperimentModel, ModelPredictionModel
from quant_platform.database.repositories import SqlAlchemyModelResearchRepository

BASE = datetime(2026, 9, 1, tzinfo=UTC)


def _experiment(id_, *, model="ridge", label="future_return_5d", market="TW",
                rank_ic=0.1, gate="RESEARCH", days=0, spread=0.0):
    return SimpleNamespace(
        id=id_, market=market, model_name=model, label_name=label, promotion_gate=gate,
        rank_ic=rank_ic, long_short_spread=spread, computed_at=BASE + timedelta(days=days),
    )


def test_experiments_in_use_keeps_latest_positive_candidate_and_registry():
    experiments = [
        _experiment(1, rank_ic=0.2, days=1),
        _experiment(2, rank_ic=0.1, days=2),              # superseded positive
        _experiment(3, rank_ic=0.3, days=3),              # latest positive
        _experiment(4, rank_ic=-0.1, days=4),             # latest overall
        _experiment(5, model="mlp", rank_ic=0.5, gate="CANDIDATE", days=1),
        _experiment(6, model="mlp", rank_ic=0.2, gate="CANDIDATE", days=5),
        _experiment(7, model="svm", rank_ic=0.0, days=1),
    ]

    keep = experiments_in_use(experiments, registry_ids=[2])

    # 1 is archived: superseded, not a candidate, not in the registry.
    assert keep == {2, 3, 4, 5, 6, 7}


def test_registry_ids_skip_retired_entries():
    entries = [
        SimpleNamespace(experiment_id=10, status=SimpleNamespace(value="CHAMPION")),
        SimpleNamespace(experiment_id=11, status="retired"),
    ]
    assert active_registry_experiment_ids(entries) == {10}


def _insert_experiment(session, id_, rank_ic, days):
    session.add(ModelExperimentModel(
        id=id_, market="TW", model_name="ridge_linear", model_version="1",
        label_name="future_return_5d", experiment_version="1", promotion_gate="RESEARCH",
        data_start=BASE, data_end=BASE + timedelta(days=days),
        observation_count=1, fold_count=1, feature_count=1, rmse=0.1, mae=0.1, r2=0.0,
        directional_accuracy=0.5, rank_ic=rank_ic, long_short_spread=0.0,
        computed_at=BASE + timedelta(days=days),
    ))
    for index in range(5):
        event = BASE + timedelta(days=index)
        session.add(ModelPredictionModel(
            experiment_id=id_, market="TW", symbol=f"{2330 + index}.TW", model_name="ridge_linear",
            label_name="future_return_5d", horizon=5, event_time=event, available_time=event,
            predicted_value=0.01 * index, rank_score=0.1 * index, computed_at=event,
        ))


def test_archive_moves_superseded_predictions_to_verified_parquet(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'archive.db'}"))
    with container.database.session_factory() as session:
        _insert_experiment(session, 101, rank_ic=0.1, days=1)   # superseded
        _insert_experiment(session, 102, rank_ic=0.2, days=2)   # latest positive
        _insert_experiment(session, 103, rank_ic=-0.1, days=3)  # latest
        session.commit()
    repository = SqlAlchemyModelResearchRepository(container.database.session_factory)
    service = PredictionArchiveService(repository, tmp_path / "archive")

    archived = service.apply()

    assert [(item.experiment_id, item.rows) for item in archived] == [(101, 5)]
    table = pq.read_table(archived[0].path)
    assert table.num_rows == 5
    assert str(table.schema.field("event_time").type) == "timestamp[us, tz=UTC]"
    assert repository.prediction_coverage(101) == (0, 0)
    assert repository.prediction_coverage(102)[0] == 5
    assert repository.prediction_experiment_ids() == [102, 103]
    assert service.manifest_path.read_text(encoding="utf-8").count("\n") == 1
    assert service.apply() == []
