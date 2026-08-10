from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from threading import Event
from time import monotonic, sleep

import numpy as np

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import (
    SqlAlchemyFeatureLabelStoreRepository,
    SqlAlchemyModelResearchRepository,
)
from quant_platform.domain.entities import FeatureValue, ModelExperiment, ModelExplanation
from quant_platform.application.model_research import BASE_FEATURES, TW_RESEARCH_FEATURES
from quant_platform.application.model_training import ModelTrainingCoordinator
from quant_platform.machine_learning import build_model
from quant_platform.machine_learning.automl import AutoMLSearch


def test_local_model_zoo_models_are_deterministic_and_finite() -> None:
    rng = np.random.default_rng(7)
    features = rng.normal(size=(240, 5))
    target = features[:, 0] * 0.04 - features[:, 2] * 0.02 + rng.normal(0, 0.01, 240)
    for name in ("historical_mean", "ridge_linear", "bagged_stumps", "gradient_boosted_stumps", "random_forest", "svm_rbf"):
        first = build_model(name).fit(features[:180], target[:180])
        second = build_model(name).fit(features[:180], target[:180])
        first_predictions = first.predict(features[180:])
        second_predictions = second.predict(features[180:])
        assert np.isfinite(first_predictions).all()
        assert np.allclose(first_predictions, second_predictions)
        assert len(first.feature_importance_) == features.shape[1]


def test_automl_search_is_reproducible_and_records_trials() -> None:
    rng = np.random.default_rng(11)
    features = rng.normal(size=(260, 4))
    target = features[:, 0] * 0.03 - features[:, 1] * 0.01 + rng.normal(0, 0.01, 260)
    events = np.array([datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index) for index in range(260)], dtype=object)
    available = np.array([value + timedelta(days=5) for value in events], dtype=object)
    search = AutoMLSearch(trials=3, seed=17)
    first = search.search("ridge_linear", features, target, events, available)
    second = search.search("ridge_linear", features, target, events, available)
    assert first.engine == "optuna_tpe_nested_time_split"
    assert first.trial_count == 3
    assert first.parameters == second.parameters
    assert first.best_score == second.best_score


def test_dataset_fingerprint_detects_historical_revision(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'fingerprint.db'}"))
    events = np.array([datetime(2025, 1, 1, tzinfo=UTC)], dtype=object)
    dataset = (
        np.array([[1.0, 2.0]]), np.array([0.01]), events,
        np.array(["2330.TW"], dtype=object), events,
    )
    revised = (
        np.array([[1.0, 2.1]]), np.array([0.01]), events,
        np.array(["2330.TW"], dtype=object), events,
    )
    assert container.model_research_pipeline._dataset_fingerprint(dataset) != container.model_research_pipeline._dataset_fingerprint(revised)


def test_model_evaluation_keeps_each_out_of_sample_prediction(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'oos.db'}"))
    rng = np.random.default_rng(23)
    dates = [datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index) for index in range(220)]
    event_times = np.array([day for day in dates for _ in range(5)], dtype=object)
    symbols = np.array([f"{2300 + index}.TW" for _ in dates for index in range(5)], dtype=object)
    features = rng.normal(size=(len(event_times), 2))
    target = features[:, 0] * 0.02 - features[:, 1] * 0.01 + rng.normal(
        0, 0.005, len(event_times)
    )
    label_available = np.array(
        [event_time + timedelta(days=5) for event_time in event_times], dtype=object
    )
    historical: list[tuple[datetime, str, float, float]] = []

    experiment = container.model_research_pipeline._evaluate(
        "TW",
        "ridge_linear",
        (features, target, event_times, symbols, label_available),
        asset_count=5,
        computed_at=datetime(2026, 1, 1, tzinfo=UTC),
        feature_names=("f1", "f2"),
        historical_predictions=historical,
    )

    assert experiment.fold_count == 3
    assert len(historical) == experiment.observation_count
    assert len({item[0] for item in historical}) > 20
    assert {item[1] for item in historical} == set(symbols)
    assert min(item[3] for item in historical) == 0.0
    assert max(item[3] for item in historical) == 1.0


def test_model_training_coordinator_returns_immediately_and_reports_completion() -> None:
    release = Event()

    class Pipeline:
        def run(self, market: str):
            release.wait(1)
            return type("Result", (), {"experiment_count": 6, "automl_trials": 40, "reused": 0})()

    coordinator = ModelTrainingCoordinator(Pipeline())
    started = monotonic()
    assert coordinator.start("TW") is True
    assert monotonic() - started < 0.2
    assert coordinator.start("TW") is False
    release.set()
    deadline = monotonic() + 2
    while coordinator.get("TW")["state"] not in {"succeeded", "failed"} and monotonic() < deadline:
        sleep(0.01)
    assert coordinator.get("TW")["state"] == "succeeded"
    assert "40" in str(coordinator.get("TW")["message"])


def test_model_experiment_registry_is_idempotent(tmp_path) -> None:
    start = datetime(2024, 1, 1, tzinfo=UTC)
    experiment = ModelExperiment(
        id=None,
        market="US",
        model_name="ridge_linear",
        model_version="1.0.0",
        label_name="future_return_5d",
        experiment_version="1.0.0",
        promotion_gate="RESEARCH",
        data_start=start,
        data_end=start + timedelta(days=200),
        observation_count=300,
        fold_count=3,
        feature_count=5,
        rmse=0.02,
        mae=0.01,
        r2=-0.01,
        directional_accuracy=0.51,
        rank_ic=0.03,
        long_short_spread=0.002,
        feature_names_json='["return_1d"]',
        parameters_json="{}",
        fold_metrics_json="[]",
        feature_importance_json='{"return_1d": 1.0}',
        limitations_json='["UNIVERSE<30"]',
        computed_at=start,
    )
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'models.db'}"))
    repository = SqlAlchemyModelResearchRepository(container.database.session_factory)
    first_id = repository.save(experiment)
    second_id = repository.save(experiment)
    assert first_id == second_id
    assert len(repository.list_runs()) == 1
    assert repository.get(first_id) == repository.list_runs()[0]


def test_taiwan_slow_features_are_joined_only_after_available_time(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'asof.db'}"))
    repository = SqlAlchemyFeatureLabelStoreRepository(container.database.session_factory)
    first_event = datetime(2026, 1, 2, 13, 30, tzinfo=UTC)
    second_event = first_event + timedelta(days=1)
    values: list[FeatureValue] = []
    for name in BASE_FEATURES:
        for event_time in (first_event, second_event):
            values.append(
                FeatureValue(
                    symbol="2330.TW",
                    feature_name=name,
                    feature_version="1.0.0",
                    event_time=event_time,
                    available_time=event_time + timedelta(minutes=15),
                    computed_at=second_event,
                    value=1.0,
                )
            )
    for name in TW_RESEARCH_FEATURES:
        values.append(
            FeatureValue(
                symbol="2330.TW",
                feature_name=name,
                feature_version="1.0.0",
                event_time=first_event - timedelta(days=1),
                available_time=first_event + timedelta(hours=1),
                computed_at=second_event,
                value=2.0,
            )
        )
    repository.upsert_features(values)
    rows = container.model_research_pipeline._point_in_time_feature_rows(
        ["2330.TW"], BASE_FEATURES + TW_RESEARCH_FEATURES
    )
    # Unknown point-in-time values must not be rewritten as economic zeros.
    assert len(rows) == 1
    assert rows[0][1] == second_event.replace(tzinfo=None)
    assert rows[0][3][-1] == 2.0


def test_model_explanation_registry_is_idempotent(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'explanations.db'}"))
    repository = SqlAlchemyModelResearchRepository(container.database.session_factory)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    experiment_id = repository.save(
        ModelExperiment(
            id=None, market="TW", model_name="ridge_linear", model_version="1.0.0",
            label_name="future_return_5d", experiment_version="1.0.0", promotion_gate="RESEARCH",
            data_start=start, data_end=start + timedelta(days=200), observation_count=300,
            fold_count=3, feature_count=1, rmse=.02, mae=.01, r2=0, directional_accuracy=.51,
            rank_ic=.02, long_short_spread=.001, feature_names_json='["return_1d"]',
            parameters_json="{}", fold_metrics_json="[]", feature_importance_json='{"return_1d":1}',
            limitations_json='["UNIVERSE<30"]', computed_at=start,
        )
    )
    explanation = ModelExplanation(
        id=None, experiment_id=experiment_id, market="TW", symbol="2330.TW",
        model_name="ridge_linear", event_time=start, data_available_time=start,
        predicted_value=.01, baseline_value=0, contributions_json='{"return_1d":.01}',
        explanation_method="median_counterfactual", explanation_version="1.0.0", computed_at=start,
    )
    assert repository.save_explanations([explanation, explanation]) == 2
    assert len(repository.list_explanations(symbol="2330.TW")) == 1


def test_explainability_service_aggregates_positive_and_negative_evidence(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'explain-view.db'}"))
    container.research_universe_service.add_asset(
        "2330.TW", "TW", "EQUITY", "半導體", "0050.TW", date(2024, 1, 1)
    )
    repository = SqlAlchemyModelResearchRepository(container.database.session_factory)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    experiment_id = repository.save(
        ModelExperiment(
            id=None, market="TW", model_name="ridge_linear", model_version="1.0.0",
            label_name="future_return_5d", experiment_version="1.0.0", promotion_gate="RESEARCH",
            data_start=start, data_end=start + timedelta(days=200), observation_count=300,
            fold_count=3, feature_count=2, rmse=.02, mae=.01, r2=0, directional_accuracy=.51,
            rank_ic=.02, long_short_spread=.001, feature_names_json='["return_1d","pe_ratio"]',
            parameters_json='{"importance_method":"out_of_sample_permutation"}', fold_metrics_json="[]",
            feature_importance_json='{"return_1d":0.6,"pe_ratio":0.4}',
            limitations_json='["UNIVERSE<30"]', computed_at=start,
        )
    )
    repository.save_explanations(
        [ModelExplanation(
            id=None, experiment_id=experiment_id, market="TW", symbol="2330.TW",
            model_name="ridge_linear", event_time=start, data_available_time=start,
            predicted_value=.01, baseline_value=0,
            contributions_json='{"return_1d":0.012,"pe_ratio":-0.002}',
            explanation_method="median_counterfactual", explanation_version="1.0.0", computed_at=start,
        )]
    )
    overview = container.explainability_service.get("2330")
    assert overview.models[0].permutation_importance[0] == ("return_1d", 0.6)
    assert overview.positive_evidence[0].feature_name == "return_1d"
    assert overview.negative_evidence[0].feature_name == "pe_ratio"
    assert overview.warning_count == 1
