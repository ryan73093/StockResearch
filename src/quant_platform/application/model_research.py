from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

import numpy as np

from quant_platform.application.macro_data import MACRO_FEATURES
from quant_platform.application.ports import (
    FeatureLabelStoreRepository,
    ModelResearchRepository,
    ResearchUniverseRepository,
    SchedulerJobRunRepository,
)
from quant_platform.domain.entities import (
    JobRunStatus,
    ModelExperiment,
    ModelExplanation,
    ModelPrediction,
)
from quant_platform.feature_engineering.cross_asset import CROSS_ASSET_SYMBOLS
from quant_platform.machine_learning import MODEL_CATALOG, MODEL_VERSION, AutoMLSearch, build_model

EXPERIMENT_VERSION = "1.5.0"
DEFAULT_LABEL = "future_return_5d"
SUPPORTED_LABELS = (DEFAULT_LABEL, "excess_return_5d")
BASE_FEATURES = (
    "return_1d",
    "return_5d",
    "momentum_20d",
    "momentum_60d",
    "close_to_sma_20",
    "rsi_14",
    "atr_14_pct",
    "volatility_20d",
    "volume_zscore_20",
    "drawdown_252d",
    "day_of_week",
)
TW_RESEARCH_FEATURES = (
    "institutional_net_buy",
    "foreign_net_buy",
    "investment_trust_net_buy",
    "dealer_net_buy",
    "margin_purchase_balance",
    "short_sale_balance",
    "securities_lending_quantity",
    "pe_ratio",
    "pb_ratio",
    "dividend_yield",
    "monthly_revenue",
    "quarterly_eps",
    "roe_annualized",
    "roa_annualized",
    "gross_margin",
    "free_cash_flow",
    "free_cash_flow_margin",
    "large_holder_ratio_1000_lots",
    "retail_holder_ratio_under_1_lot",
    "large_holder_count_1000_lots",
    "shareholder_count",
)
CROSS_ASSET_RESEARCH_FEATURES = tuple(CROSS_ASSET_SYMBOLS)
MACRO_RESEARCH_FEATURES = tuple(MACRO_FEATURES.values())
DEFAULT_FEATURES = BASE_FEATURES
EXPLANATION_VERSION = "1.0.0"
AVAILABLE_MODELS = tuple(item.name for item in MODEL_CATALOG if item.available)


@dataclass(frozen=True, slots=True)
class ModelPipelineResult:
    run_id: int
    market: str
    status: str
    experiment_count: int
    candidates: int
    observations: int
    failed: int
    automl_trials: int
    reused: int
    failures: dict[str, str]
    started_at: str
    completed_at: str


@dataclass(frozen=True, slots=True)
class ModelExperimentView:
    experiment: ModelExperiment
    failed_gates: tuple[str, ...]
    fold_metrics: tuple[dict[str, object], ...]
    feature_importance: tuple[tuple[str, float], ...]
    parameters: dict[str, object]


@dataclass(frozen=True, slots=True)
class ModelResearchOverview:
    runs: tuple[ModelExperimentView, ...]
    selected: ModelExperimentView | None
    catalog: tuple[object, ...]
    run_count: int
    candidate_count: int
    market_count: int
    available_model_count: int
    automl_run_count: int
    archived_run_count: int
    history: tuple[ModelExperimentView, ...]


class ModelResearchOverviewService:
    def __init__(self, repository: ModelResearchRepository) -> None:
        self._repository = repository

    def get_overview(self, selected_id: int | None = None) -> ModelResearchOverview:
        experiments = self._repository.list_runs()
        latest_by_model: dict[tuple[str, str], ModelExperiment] = {}
        for item in experiments:
            key = (item.market, item.model_name)
            current = latest_by_model.get(key)
            if current is None or self._timestamp(item.computed_at) > self._timestamp(current.computed_at):
                latest_by_model[key] = item
        current_experiments = sorted(
            latest_by_model.values(),
            key=lambda item: (item.market, item.rank_ic or -1),
            reverse=True,
        )
        views = tuple(self._view(item) for item in current_experiments)
        selected_experiment = self._repository.get(selected_id) if selected_id else None
        selected = self._view(selected_experiment) if selected_experiment else (views[0] if views else None)
        return ModelResearchOverview(
            runs=views,
            selected=selected,
            catalog=MODEL_CATALOG,
            run_count=len(experiments),
            candidate_count=sum(item.experiment.promotion_gate == "CANDIDATE" for item in views),
            market_count=len({item.experiment.market for item in views}),
            available_model_count=sum(item.available for item in MODEL_CATALOG),
            automl_run_count=sum(
                json.loads(item.experiment.parameters_json).get("automl_trials", 0) > 0
                for item in views
            ),
            archived_run_count=len(experiments) - len(views),
            history=tuple(
                self._view(item)
                for item in sorted(
                    experiments, key=lambda value: self._timestamp(value.computed_at),
                    reverse=True,
                )
            ),
        )

    @staticmethod
    def _view(experiment: ModelExperiment) -> ModelExperimentView:
        limitations = tuple(json.loads(experiment.limitations_json))
        importance = json.loads(experiment.feature_importance_json)
        return ModelExperimentView(
            experiment=experiment,
            failed_gates=limitations,
            fold_metrics=tuple(json.loads(experiment.fold_metrics_json)),
            feature_importance=tuple(sorted(importance.items(), key=lambda item: item[1], reverse=True)),
            parameters=json.loads(experiment.parameters_json),
        )

    @staticmethod
    def _timestamp(value: datetime) -> datetime:
        return value.replace(tzinfo=None) if value.tzinfo else value


class ModelResearchPipeline:
    """Trains registered models with expanding, label-availability-safe time folds."""

    def __init__(
        self,
        universe_repository: ResearchUniverseRepository,
        feature_store_repository: FeatureLabelStoreRepository,
        model_repository: ModelResearchRepository,
        run_repository: SchedulerJobRunRepository,
    ) -> None:
        self._universe_repository = universe_repository
        self._feature_store_repository = feature_store_repository
        self._model_repository = model_repository
        self._run_repository = run_repository
        self._automl = AutoMLSearch(trials=4, seed=42)

    def run(
        self,
        market: str,
        now: datetime | None = None,
        model_names: tuple[str, ...] | None = None,
        feature_profile: str = "auto",
        max_assets: int | None = None,
        label_name: str = DEFAULT_LABEL,
    ) -> ModelPipelineResult:
        normalized_market = market.upper()
        if normalized_market not in {"US", "TW"}:
            raise ValueError("market must be US or TW")
        if label_name not in SUPPORTED_LABELS:
            raise ValueError(
                "label_name must be future_return_5d or excess_return_5d"
            )
        started = now or datetime.now(UTC)
        assets = sorted(
            self._universe_repository.list_active(normalized_market),
            key=lambda item: (
                item.market_rank is None,
                item.market_rank if item.market_rank is not None else 10**9,
                -(item.market_value_twd or 0),
                item.symbol,
            ),
        )
        if max_assets is not None:
            if max_assets < 30:
                raise ValueError("max_assets must be at least 30")
            assets = assets[:max_assets]
        symbols = [item.symbol for item in assets if item.asset_type in {"EQUITY", "ETF"}]
        run_id = self._run_repository.start("model_zoo_research", normalized_market, started)
        failures: dict[str, str] = {}
        experiments: list[ModelExperiment] = []
        observations = 0
        automl_trials = 0
        reused = 0
        requested_models = model_names or AVAILABLE_MODELS

        def progress(stage: str, percent: int, **metrics: object) -> None:
            updater = getattr(self._run_repository, "update_progress", None)
            if callable(updater):
                updater(run_id, json.dumps({
                    "stage": stage,
                    "progress": percent,
                    "updated_at": datetime.now(UTC).isoformat(),
                    "requested_models": list(requested_models),
                    "label_name": label_name,
                    "feature_profile": feature_profile,
                    "max_assets": max_assets,
                    **metrics,
                }, ensure_ascii=False))

        try:
            if feature_profile not in {"auto", "price_core", "comprehensive"}:
                raise ValueError(
                    "feature_profile must be auto, price_core or comprehensive"
                )
            unknown_models = sorted(set(requested_models) - set(AVAILABLE_MODELS))
            if unknown_models:
                raise ValueError(f"unavailable models: {', '.join(unknown_models)}")
            progress("載入時間點正確的歷史特徵", 5)
            # The GPU technical model deliberately uses the longest consistent
            # price-derived history. Auxiliary/macro features are evaluated in
            # separate challengers because their shorter coverage must not cut
            # the core regime test down to only the recent market.
            price_core = feature_profile == "price_core" or (
                feature_profile == "auto"
                and set(requested_models) == {"torch_cuda_mlp"}
            )
            feature_names = BASE_FEATURES if price_core else self._select_research_features(
                symbols,
                CROSS_ASSET_RESEARCH_FEATURES
                + MACRO_RESEARCH_FEATURES
                + (TW_RESEARCH_FEATURES if normalized_market == "TW" else ()),
            )
            feature_rows = self._point_in_time_feature_rows(symbols, feature_names)
            progress(
                "歷史特徵載入完成，建立訓練矩陣",
                20,
                assets=len(symbols),
                feature_rows=len(feature_rows),
                features=len(feature_names),
            )
            latest_rows = self._latest_feature_rows_from_rows(feature_rows)
            dataset = self._dataset(
                symbols, feature_names, feature_rows, label_name=label_name
            )
            feature_available = {
                (symbol, event_time): available_time
                for symbol, event_time, available_time, _ in feature_rows
            }
            observations = len(dataset[0])
            unique_dates = len(set(dataset[2].tolist()))
            progress(
                "訓練矩陣完成，開始逐模型時間序列驗證",
                30,
                assets=len(symbols),
                observations=observations,
                dates=unique_dates,
                features=len(feature_names),
            )
            expected_end = self._expected_oos_end(dataset[2])
            dataset_fingerprint = self._dataset_fingerprint(dataset)
            for model_index, model_name in enumerate(requested_models):
                try:
                    progress(
                        f"訓練與驗證 {model_name}",
                        30 + int(60 * model_index / max(len(requested_models), 1)),
                        current_model=model_name,
                        completed_models=model_index,
                        total_models=len(requested_models),
                        observations=observations,
                        dates=unique_dates,
                    )
                    existing = self._reusable_experiment(
                        normalized_market, model_name, label_name, expected_end,
                        dataset_fingerprint,
                    )
                    if existing is not None:
                        experiments.append(existing)
                        reused += 1
                        continue
                    search = self._automl.search(model_name, dataset[0], dataset[1], dataset[2], dataset[4])
                    automl_trials += search.trial_count
                    historical_drafts: list[tuple[datetime, str, float, float]] = []
                    experiment = self._evaluate(
                        normalized_market,
                        model_name,
                        dataset,
                        len(set(dataset[3].tolist())),
                        started,
                        feature_names,
                        search.parameters, search.engine, search.trial_count, search.best_score,
                        dataset_fingerprint, label_name,
                        historical_drafts,
                    )
                    experiment_id = self._model_repository.save(experiment)
                    # A full TW history can produce hundreds of thousands of
                    # OOS rows per model. Persist bounded batches so memory and
                    # SQLite lock time stay predictable.
                    for batch_start in range(0, len(historical_drafts), 1000):
                        predictions = [ModelPrediction(
                            id=None,
                            experiment_id=experiment_id,
                            market=normalized_market,
                            symbol=symbol,
                            model_name=model_name,
                            label_name=label_name,
                            horizon=5,
                            event_time=event_time,
                            available_time=feature_available.get(
                                (symbol, event_time), event_time
                            ),
                            predicted_value=predicted_value,
                            rank_score=rank_score,
                            computed_at=started,
                        )
                            for event_time, symbol, predicted_value, rank_score
                            in historical_drafts[batch_start:batch_start + 1000]
                        ]
                        self._model_repository.save_predictions(predictions)
                    latest_predictions = self._latest_predictions(
                        experiment_id, normalized_market, model_name, dataset, latest_rows, started,
                        search.parameters, label_name,
                    )
                    self._model_repository.save_predictions(latest_predictions)
                    self._model_repository.save_explanations(
                        self._latest_explanations(
                            experiment_id,
                            normalized_market,
                            model_name,
                            dataset,
                            latest_rows,
                            feature_names,
                            started,
                            search.parameters,
                        )
                    )
                    experiments.append(experiment)
                    progress(
                        f"{model_name} 已保存，準備下一個模型",
                        30 + int(
                            60 * (model_index + 1) / max(len(requested_models), 1)
                        ),
                        current_model=model_name,
                        completed_models=model_index + 1,
                        total_models=len(requested_models),
                        observations=observations,
                        dates=unique_dates,
                        saved_oos_predictions=len(historical_drafts),
                    )
                except Exception as exc:  # noqa: BLE001 - preserve other model results
                    failures[model_name] = str(exc)
        except Exception as exc:  # noqa: BLE001 - persist dataset-stage audit failure
            failures["dataset"] = str(exc)

        completed = datetime.now(UTC)
        status = (
            JobRunStatus.SUCCEEDED
            if not failures
            else JobRunStatus.FAILED
            if not experiments
            else JobRunStatus.PARTIAL
        )
        result = ModelPipelineResult(
            run_id=run_id,
            market=normalized_market,
            status=status.value,
            experiment_count=len(experiments),
            candidates=sum(item.promotion_gate == "CANDIDATE" for item in experiments),
            observations=observations,
            failed=len(failures),
            automl_trials=automl_trials,
            reused=reused,
            failures=failures,
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
        )
        self._run_repository.finish(
            run_id,
            status.value,
            completed,
            json.dumps(asdict(result), ensure_ascii=False),
            "; ".join(f"{key}: {value}" for key, value in failures.items()) or None,
        )
        return result

    def _reusable_experiment(
        self, market: str, model_name: str, label_name: str,
        expected_end: datetime, dataset_fingerprint: str
    ) -> ModelExperiment | None:
        for item in self._model_repository.list_runs(market):
            if (
                item.model_name == model_name
                and item.label_name == label_name
                and item.model_version == MODEL_VERSION
                and item.experiment_version == EXPERIMENT_VERSION
                and self._timestamp(item.data_end) == self._timestamp(expected_end)
                and json.loads(item.parameters_json).get("dataset_fingerprint") == dataset_fingerprint
            ):
                coverage = getattr(self._model_repository, "prediction_coverage", None)
                if not callable(coverage) or item.id is None:
                    return item
                rows, dates = coverage(int(item.id))
                if rows >= item.observation_count and dates >= 20:
                    return item
        return None

    @staticmethod
    def _expected_oos_end(event_times: np.ndarray) -> datetime:
        unique_dates = sorted(set(event_times.tolist()))
        if len(unique_dates) >= 504:
            return unique_dates[-1]
        start_index = int(len(unique_dates) * 0.85)
        end_index = min(start_index + max(int(len(unique_dates) * 0.12), 20), len(unique_dates))
        return unique_dates[end_index - 1]

    @staticmethod
    def _dataset_fingerprint(
        dataset: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]
    ) -> str:
        features, target, event_times, symbols, label_available = dataset
        digest = hashlib.sha256()
        digest.update(np.ascontiguousarray(features).tobytes())
        digest.update(np.ascontiguousarray(target).tobytes())
        for values in (event_times, symbols, label_available):
            digest.update("\x1f".join(str(value) for value in values.tolist()).encode("utf-8"))
        return digest.hexdigest()[:24]

    def _dataset(
        self,
        symbols: list[str],
        feature_names: tuple[str, ...] = DEFAULT_FEATURES,
        feature_rows: list[tuple[str, datetime, datetime, np.ndarray]] | None = None,
        label_name: str = DEFAULT_LABEL,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        if not symbols:
            raise ValueError("no active research assets")
        label_values = []
        for start in range(0, len(symbols), 25):
            label_values.extend(
                self._feature_store_repository.list_labels(
                    symbols[start:start + 25], [label_name]
                )
            )
        labels_by_key = {(item.symbol, item.event_time): item for item in label_values}
        rows: list[tuple[datetime, str, datetime, list[float], float]] = []
        rows_source = (
            feature_rows
            if feature_rows is not None
            else self._point_in_time_feature_rows(symbols, feature_names)
        )
        for symbol, event_time, _, vector in rows_source:
            label = labels_by_key.get((symbol, event_time))
            if label is None:
                continue
            values = vector.tolist()
            if all(math.isfinite(value) for value in values) and math.isfinite(label.value):
                rows.append((event_time, symbol, label.available_time, values, label.value))
        rows.sort(key=lambda item: (item[0], item[1]))
        if len(rows) < 120:
            raise ValueError(f"insufficient complete feature/label rows: {len(rows)}")
        return (
            np.array([item[3] for item in rows], dtype=float),
            np.array([item[4] for item in rows], dtype=float),
            np.array([item[0] for item in rows], dtype=object),
            np.array([item[1] for item in rows], dtype=object),
            np.array([item[2] for item in rows], dtype=object),
        )

    def _latest_feature_rows(
        self, symbols: list[str], feature_names: tuple[str, ...] = DEFAULT_FEATURES
    ) -> list[tuple[str, datetime, datetime, np.ndarray]]:
        return self._latest_feature_rows_from_rows(
            self._point_in_time_feature_rows(symbols, feature_names)
        )

    @staticmethod
    def _latest_feature_rows_from_rows(
        rows: list[tuple[str, datetime, datetime, np.ndarray]],
    ) -> list[tuple[str, datetime, datetime, np.ndarray]]:
        latest: dict[str, tuple[str, datetime, datetime, np.ndarray]] = {}
        for symbol, event_time, available_time, vector in rows:
            candidate = (symbol, event_time, available_time, vector)
            if symbol not in latest or event_time > latest[symbol][1]:
                latest[symbol] = candidate
        return sorted(latest.values(), key=lambda item: item[0])

    def _point_in_time_feature_rows(
        self, symbols: list[str], feature_names: tuple[str, ...]
    ) -> list[tuple[str, datetime, datetime, np.ndarray]]:
        # Loading every feature-value ORM row for the full TW universe at once can
        # consume tens of gigabytes before the compact numeric matrix is built.
        # Work in symbol batches so raw database objects are released promptly.
        if len(symbols) > 25:
            output: list[tuple[str, datetime, datetime, np.ndarray]] = []
            for start in range(0, len(symbols), 25):
                output.extend(
                    self._point_in_time_feature_rows(
                        symbols[start:start + 25], feature_names
                    )
                )
            output.sort(key=lambda item: (item[1], item[0]))
            return output
        values = self._feature_store_repository.list_features(symbols, list(feature_names))
        base_names = tuple(name for name in feature_names if name in BASE_FEATURES)
        slow_names = tuple(name for name in feature_names if name not in BASE_FEATURES)
        base: dict[tuple[str, datetime], dict[str, object]] = {}
        slow: dict[tuple[str, str], list[object]] = {}
        for item in values:
            if item.feature_name in base_names:
                row = base.setdefault(
                    (item.symbol, item.event_time),
                    {"values": {}, "available_time": item.available_time},
                )
                row["values"][item.feature_name] = item.value
                if self._timestamp(item.available_time) > self._timestamp(row["available_time"]):
                    row["available_time"] = item.available_time
            elif item.feature_name in slow_names:
                slow.setdefault((item.symbol, item.feature_name), []).append(item)
        slow_index: dict[tuple[str, str], tuple[list[datetime], list[object]]] = {}
        for key, items in slow.items():
            items.sort(key=lambda item: self._timestamp(item.available_time))
            slow_index[key] = (
                [self._timestamp(item.available_time) for item in items],
                items,
            )
        output: list[tuple[str, datetime, datetime, np.ndarray]] = []
        for (symbol, event_time), row in sorted(base.items(), key=lambda item: (item[0][1], item[0][0])):
            feature_map = row["values"]
            if any(name not in feature_map for name in base_names):
                continue
            cutoff = row["available_time"]
            complete = True
            for name in slow_names:
                times, items = slow_index.get((symbol, name), ([], []))
                selected_index = bisect_right(times, self._timestamp(cutoff)) - 1
                if selected_index >= 0:
                    selected = items[selected_index]
                    feature_map[name] = selected.value
                else:
                    complete = False
                    break
            if not complete:
                continue
            vector = np.array([feature_map[name] for name in feature_names], dtype=float)
            if np.isfinite(vector).all():
                output.append((symbol, event_time, cutoff, vector))
        return output

    def _select_research_features(
        self,
        symbols: list[str],
        candidates: tuple[str, ...],
    ) -> tuple[str, ...]:
        """Choose a causally joinable slow-feature set with honest coverage.

        Requiring every ETF and equity to have every financial statement field
        makes a populated comprehensive store collapse to zero rows.  Features
        are added only while their common symbol coverage remains large enough
        for a research challenger; unsupported fields are omitted instead of
        being rewritten as economic zeros.
        """
        if not symbols:
            return BASE_FEATURES
        values = self._feature_store_repository.list_features(
            symbols, list(BASE_FEATURES + candidates)
        )
        latest_base_cutoff: dict[str, datetime] = {}
        for item in values:
            if item.feature_name not in BASE_FEATURES:
                continue
            current = latest_base_cutoff.get(item.symbol)
            if current is None or self._timestamp(item.available_time) > self._timestamp(
                current
            ):
                latest_base_cutoff[item.symbol] = item.available_time
        support: dict[str, set[str]] = {}
        release_history: dict[str, dict[str, set[datetime]]] = {}
        for item in values:
            cutoff = latest_base_cutoff.get(item.symbol)
            if (
                item.feature_name in candidates
                and cutoff is not None
                and self._timestamp(item.available_time) <= self._timestamp(cutoff)
                and math.isfinite(item.value)
            ):
                support.setdefault(item.feature_name, set()).add(item.symbol)
                release_history.setdefault(item.feature_name, {}).setdefault(
                    item.symbol, set()
                ).add(item.event_time)
        minimum_assets = min(len(symbols), max(5, math.ceil(len(symbols) * 0.4)))
        common_symbols = set(symbols)
        selected: list[str] = []
        for name in candidates:
            # A broad current snapshot is useful for today's decision, but it is
            # not a historical training feature. Require repeated point-in-time
            # releases so one fresh valuation/TDCC download cannot collapse a
            # five-year training matrix to one or two dates.
            repeated_symbols = {
                symbol
                for symbol, releases in release_history.get(name, {}).items()
                if len(releases) >= 12
            }
            narrowed = common_symbols & support.get(name, set()) & repeated_symbols
            if len(narrowed) < minimum_assets:
                continue
            selected.append(name)
            common_symbols = narrowed
        return BASE_FEATURES + tuple(selected)

    @staticmethod
    def _timestamp(value: datetime) -> datetime:
        return value.replace(tzinfo=None) if value.tzinfo else value

    @staticmethod
    def _latest_predictions(
        experiment_id: int,
        market: str,
        model_name: str,
        dataset: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray],
        latest_rows: list[tuple[str, datetime, datetime, np.ndarray]],
        computed_at: datetime,
        parameters: dict[str, object] | None = None,
        label_name: str = DEFAULT_LABEL,
    ) -> list[ModelPrediction]:
        if not latest_rows:
            return []
        features, target, _, _, label_available = dataset
        def is_known(available: datetime) -> bool:
            reference = computed_at
            if available.tzinfo is None and reference.tzinfo is not None:
                reference = reference.replace(tzinfo=None)
            elif available.tzinfo is not None and reference.tzinfo is None:
                reference = reference.replace(tzinfo=available.tzinfo)
            return available <= reference

        known_mask = np.array([is_known(available) for available in label_available])
        if known_mask.sum() < 80:
            return []
        model = build_model(model_name, parameters).fit(features[known_mask], target[known_mask])
        matrix = np.vstack([item[3] for item in latest_rows])
        predicted = model.predict(matrix)
        order = np.argsort(np.argsort(predicted)).astype(float)
        ranks = order / max(len(order) - 1, 1)
        return [
            ModelPrediction(
                id=None,
                experiment_id=experiment_id,
                market=market,
                symbol=item[0],
                model_name=model_name,
                label_name=label_name,
                horizon=5,
                event_time=item[1],
                available_time=item[2],
                predicted_value=float(prediction),
                rank_score=float(rank),
                computed_at=computed_at,
            )
            for item, prediction, rank in zip(latest_rows, predicted, ranks)
        ]

    @staticmethod
    def _latest_explanations(
        experiment_id: int,
        market: str,
        model_name: str,
        dataset: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray],
        latest_rows: list[tuple[str, datetime, datetime, np.ndarray]],
        feature_names: tuple[str, ...],
        computed_at: datetime,
        parameters: dict[str, object] | None = None,
    ) -> list[ModelExplanation]:
        if not latest_rows:
            return []
        features, target, _, _, label_available = dataset

        def is_known(available: datetime) -> bool:
            reference = computed_at
            if available.tzinfo is None and reference.tzinfo is not None:
                reference = reference.replace(tzinfo=None)
            elif available.tzinfo is not None and reference.tzinfo is None:
                reference = reference.replace(tzinfo=available.tzinfo)
            return available <= reference

        known_mask = np.array([is_known(value) for value in label_available])
        if known_mask.sum() < 80:
            return []
        training = features[known_mask]
        model = build_model(model_name, parameters).fit(training, target[known_mask])
        reference = np.median(training, axis=0)
        baseline = float(model.predict(reference.reshape(1, -1))[0])
        output: list[ModelExplanation] = []
        for symbol, event_time, available_time, vector in latest_rows:
            prediction = float(model.predict(vector.reshape(1, -1))[0])
            contributions: dict[str, float] = {}
            for index, name in enumerate(feature_names):
                counterfactual = vector.copy()
                counterfactual[index] = reference[index]
                without_feature = float(model.predict(counterfactual.reshape(1, -1))[0])
                contributions[name] = round(prediction - without_feature, 8)
            output.append(
                ModelExplanation(
                    id=None,
                    experiment_id=experiment_id,
                    market=market,
                    symbol=symbol,
                    model_name=model_name,
                    event_time=event_time,
                    data_available_time=available_time,
                    predicted_value=prediction,
                    baseline_value=baseline,
                    contributions_json=json.dumps(contributions, ensure_ascii=False, sort_keys=True),
                    explanation_method="median_counterfactual",
                    explanation_version=EXPLANATION_VERSION,
                    computed_at=computed_at,
                )
            )
        return output

    def _evaluate(
        self,
        market: str,
        model_name: str,
        dataset: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray],
        asset_count: int,
        computed_at: datetime,
        feature_names: tuple[str, ...] = DEFAULT_FEATURES,
        parameters: dict[str, object] | None = None,
        automl_engine: str = "disabled",
        automl_trials: int = 0,
        automl_best_score: float | None = None,
        dataset_fingerprint: str = "",
        label_name: str = DEFAULT_LABEL,
        historical_predictions: list[tuple[datetime, str, float, float]] | None = None,
    ) -> ModelExperiment:
        features, target, event_times, symbols, label_available = dataset
        unique_dates = sorted(set(event_times.tolist()))
        if len(unique_dates) < 120:
            raise ValueError("insufficient unique dates for expanding time split")
        if len(unique_dates) >= 504:
            fold_ranges = tuple(
                (start, min(start + 63, len(unique_dates)))
                for start in range(252, len(unique_dates), 63)
                if min(start + 63, len(unique_dates)) - start >= 20
            )
            split_description: object = {
                "minimum_training_sessions": 252,
                "test_sessions": 63,
                "coverage": "continuous_after_warmup",
            }
        else:
            boundaries = (0.55, 0.70, 0.85)
            fold_ranges = tuple(
                (
                    int(len(unique_dates) * fraction),
                    min(
                        int(len(unique_dates) * fraction)
                        + max(int(len(unique_dates) * 0.12), 20),
                        len(unique_dates),
                    ),
                )
                for fraction in boundaries
            )
            split_description = boundaries
        predictions: list[float] = []
        actuals: list[float] = []
        prediction_dates: list[datetime] = []
        prediction_symbols: list[str] = []
        folds: list[dict[str, object]] = []
        importances: list[np.ndarray] = []
        for sequence, (start_index, end_index) in enumerate(fold_ranges, 1):
            if end_index <= start_index:
                continue
            test_start = unique_dates[start_index]
            test_end = unique_dates[end_index - 1]
            train_mask = (event_times < test_start) & (label_available <= test_start)
            test_mask = (event_times >= test_start) & (event_times <= test_end)
            if train_mask.sum() < 80 or test_mask.sum() < 20:
                continue
            model = build_model(model_name, parameters).fit(features[train_mask], target[train_mask])
            test_features = features[test_mask]
            fold_predictions = model.predict(test_features)
            fold_actuals = target[test_mask]
            metrics = self._metrics(fold_actuals, fold_predictions)
            folds.append(
                {
                    "sequence": sequence,
                    "train_end": test_start.isoformat(),
                    "test_start": test_start.isoformat(),
                    "test_end": test_end.isoformat(),
                    "train_observations": int(train_mask.sum()),
                    "test_observations": int(test_mask.sum()),
                    **metrics,
                }
            )
            predictions.extend(fold_predictions.tolist())
            actuals.extend(fold_actuals.tolist())
            prediction_dates.extend(event_times[test_mask].tolist())
            prediction_symbols.extend(symbols[test_mask].tolist())
            baseline_mse = float(np.mean((fold_actuals - fold_predictions) ** 2))
            permutation = np.zeros(features.shape[1], dtype=float)
            rng = np.random.default_rng(1000 + sequence)
            for feature_index in range(features.shape[1]):
                permuted = test_features.copy()
                rng.shuffle(permuted[:, feature_index])
                shuffled_mse = float(np.mean((fold_actuals - model.predict(permuted)) ** 2))
                permutation[feature_index] = max(shuffled_mse - baseline_mse, 0.0)
            total = float(permutation.sum())
            importances.append(permutation / total if total else permutation)
        if len(folds) < 2:
            raise ValueError("fewer than two valid time folds")
        predicted = np.array(predictions)
        observed = np.array(actuals)
        if historical_predictions is not None:
            indexes_by_date: dict[datetime, list[int]] = {}
            for index, event_time in enumerate(prediction_dates):
                indexes_by_date.setdefault(event_time, []).append(index)
            for event_time, indexes in sorted(indexes_by_date.items()):
                day_predictions = predicted[indexes]
                order = np.argsort(np.argsort(day_predictions)).astype(float)
                ranks = order / max(len(order) - 1, 1)
                historical_predictions.extend(
                    (
                        prediction_dates[index],
                        prediction_symbols[index],
                        float(predicted[index]),
                        float(rank),
                    )
                    for index, rank in zip(indexes, ranks)
                )
        metrics = self._metrics(observed, predicted)
        rank_ic = self._rank_ic(observed, predicted)
        spread = self._long_short_spread(observed, predicted, prediction_dates)
        importance = np.mean(importances, axis=0)
        importance_json = {
            name: round(float(value), 6)
            for name, value in zip(feature_names, importance)
        }
        limitations: list[str] = []
        if len(observed) < 1000:
            limitations.append("OOS<1000")
        if len(folds) < 3:
            limitations.append("FOLDS<3")
        if rank_ic is None or rank_ic < 0.02:
            limitations.append("RANK_IC<0.02")
        if metrics["directional_accuracy"] < 0.52:
            limitations.append("DIRECTION<52%")
        if metrics["r2"] < -0.10:
            limitations.append("R2<-0.10")
        if asset_count < 30:
            limitations.append("UNIVERSE<30")
        promotion_gate = "CANDIDATE" if not limitations else "RESEARCH"
        return ModelExperiment(
            id=None,
            market=market,
            model_name=model_name,
            model_version=MODEL_VERSION,
            label_name=label_name,
            experiment_version=EXPERIMENT_VERSION,
            promotion_gate=promotion_gate,
            data_start=min(prediction_dates),
            data_end=max(prediction_dates),
            observation_count=len(observed),
            fold_count=len(folds),
            feature_count=len(feature_names),
            rmse=metrics["rmse"],
            mae=metrics["mae"],
            r2=metrics["r2"],
            directional_accuracy=metrics["directional_accuracy"],
            rank_ic=rank_ic,
            long_short_spread=spread,
            feature_names_json=json.dumps(feature_names),
            parameters_json=json.dumps(
                {
                    "split": "expanding",
                    "folds": split_description,
                    "importance_method": "out_of_sample_permutation",
                    "slow_feature_join": "available_time_asof",
                    "missing_slow_feature": "exclude_incomplete_row_v2",
                    "model_parameters": parameters or {},
                    "automl_engine": automl_engine,
                    "automl_trials": automl_trials,
                    "automl_best_validation_rmse": automl_best_score,
                    "tuning_scope": "pre_oos_nested_time_split",
                    "dataset_fingerprint": dataset_fingerprint,
                    "feature_profile": (
                        "price_core"
                        if tuple(feature_names) == BASE_FEATURES
                        else "comprehensive"
                    ),
                },
                sort_keys=True,
            ),
            fold_metrics_json=json.dumps(folds, sort_keys=True),
            feature_importance_json=json.dumps(importance_json, sort_keys=True),
            limitations_json=json.dumps(limitations),
            computed_at=computed_at,
        )

    @staticmethod
    def _metrics(actual: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
        residual = actual - predicted
        denominator = float(np.sum((actual - actual.mean()) ** 2))
        return {
            "rmse": float(np.sqrt(np.mean(residual**2))),
            "mae": float(np.mean(np.abs(residual))),
            "r2": float(1 - np.sum(residual**2) / denominator) if denominator else 0.0,
            "directional_accuracy": float(np.mean((actual >= 0) == (predicted >= 0))),
        }

    @staticmethod
    def _rank_ic(actual: np.ndarray, predicted: np.ndarray) -> float | None:
        if len(actual) < 3 or np.std(actual) == 0 or np.std(predicted) == 0:
            return None
        actual_rank = np.argsort(np.argsort(actual)).astype(float)
        predicted_rank = np.argsort(np.argsort(predicted)).astype(float)
        value = float(np.corrcoef(actual_rank, predicted_rank)[0, 1])
        return value if math.isfinite(value) else None

    @staticmethod
    def _long_short_spread(
        actual: np.ndarray, predicted: np.ndarray, dates: list[datetime]
    ) -> float | None:
        spreads: list[float] = []
        indexes_by_date: dict[datetime, list[int]] = {}
        for index, event_time in enumerate(dates):
            indexes_by_date.setdefault(event_time, []).append(index)
        for indexes in indexes_by_date.values():
            if len(indexes) < 4:
                continue
            ordered = sorted(indexes, key=lambda index: predicted[index])
            bucket = max(1, len(ordered) // 5)
            spreads.append(float(actual[ordered[-bucket:]].mean() - actual[ordered[:bucket]].mean()))
        return float(np.mean(spreads)) if spreads else None
