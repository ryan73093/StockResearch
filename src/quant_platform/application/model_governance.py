from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime

import numpy as np

from quant_platform.application.ports import (
    FeatureLabelStoreRepository, ModelGovernanceRepository,
    ModelResearchRepository, ResearchUniverseRepository,
)
from quant_platform.domain.entities import (
    DriftStatus, ModelDriftSnapshot, ModelExperiment, ModelRegistryEntry,
    ModelRegistryStatus,
)


@dataclass(frozen=True, slots=True)
class ModelGovernancePolicy:
    version: str = "model-governance-v1"
    baseline_sessions: int = 252
    recent_sessions: int = 63
    minimum_feature_observations: int = 30
    minimum_monitored_features: int = 3
    minimum_observations: int = 500
    minimum_folds: int = 3
    minimum_directional_accuracy: float = 0.52
    minimum_rank_ic: float = 0.02
    minimum_r2: float = 0.0
    psi_warning: float = 0.10
    psi_critical: float = 0.25
    psi_feature_max_critical: float = 0.50
    directional_drop_warning: float = -0.02
    directional_drop_critical: float = -0.05
    rank_ic_drop_warning: float = -0.01
    rank_ic_drop_critical: float = -0.03


@dataclass(frozen=True, slots=True)
class GovernanceCheck:
    key: str
    label: str
    passed: bool
    value: float | int | str | bool | None
    threshold: float | int | str | bool
    unit: str
    explanation: str


@dataclass(frozen=True, slots=True)
class RegistryEntryView:
    entry: ModelRegistryEntry
    experiment: ModelExperiment
    drift: ModelDriftSnapshot | None
    checks: tuple[GovernanceCheck, ...]
    drift_checks: tuple[GovernanceCheck, ...]
    eligible: bool
    recommended: bool


@dataclass(frozen=True, slots=True)
class ModelGovernanceOverview:
    entries: tuple[RegistryEntryView, ...]
    policy: ModelGovernancePolicy
    champion_count: int
    challenger_count: int
    critical_count: int
    warning_count: int
    stable_count: int


@dataclass(frozen=True, slots=True)
class GovernanceRefreshResult:
    registered: int
    drift_snapshots: int
    automatically_demoted: int
    skipped: int


class ModelGovernanceService:
    def __init__(
        self,
        repository: ModelGovernanceRepository,
        models: ModelResearchRepository,
        features: FeatureLabelStoreRepository,
        universe: ResearchUniverseRepository,
        policy: ModelGovernancePolicy | None = None,
    ) -> None:
        self._repository = repository
        self._models = models
        self._features = features
        self._universe = universe
        self._policy = policy or ModelGovernancePolicy()
        self._feature_drift_cache: dict[
            tuple[str, tuple[str, ...]], tuple[dict[str, float], int, list[datetime]]
        ] = {}

    @staticmethod
    def _timestamp(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @staticmethod
    def quality_score(experiment: ModelExperiment) -> float:
        rank_ic = max(-1.0, min(1.0, experiment.rank_ic or 0.0))
        r2 = max(-1.0, min(1.0, experiment.r2))
        return float(
            0.45 * experiment.directional_accuracy
            + 0.35 * rank_ic
            + 0.20 * r2
        )

    def sync_registry(self) -> int:
        now = datetime.now(UTC)
        all_runs = self._models.list_runs()
        latest: dict[tuple[str, str, str], ModelExperiment] = {}
        for experiment in all_runs:
            key = (experiment.market, experiment.model_name, experiment.label_name)
            current = latest.get(key)
            if current is None or self._timestamp(experiment.computed_at) > self._timestamp(current.computed_at):
                latest[key] = experiment
        registered = 0
        entries = self._repository.list_entries()
        for key, experiment in latest.items():
            if experiment.id is None:
                continue
            existing = self._repository.get_by_experiment(experiment.id)
            if existing is None:
                draft = ModelRegistryEntry(
                    id=None, experiment_id=experiment.id, market=experiment.market,
                    model_name=experiment.model_name, label_name=experiment.label_name,
                    status=ModelRegistryStatus.CHALLENGER,
                    quality_score=self.quality_score(experiment),
                    eligibility_checks_json="[]", registered_at=now,
                    promoted_at=None, demoted_at=None, reviewer=None,
                    decision_note=None, updated_at=now,
                )
                entry_id = self._repository.save_entry(draft)
                existing = replace(draft, id=entry_id)
                registered += 1
                entries.append(existing)
            for older in entries:
                if (
                    older.id != existing.id
                    and (older.market, older.model_name, older.label_name) == key
                    and older.status == ModelRegistryStatus.CHALLENGER
                ):
                    self._repository.update_entry(replace(
                        older, status=ModelRegistryStatus.RETIRED,
                        demoted_at=now, updated_at=now,
                        decision_note="已有相同模型的新資料快照，舊挑戰者自動封存。",
                    ))
        return registered

    def refresh(self) -> GovernanceRefreshResult:
        self._feature_drift_cache.clear()
        registered = self.sync_registry()
        drift_count = demoted = skipped = 0
        for entry in self._repository.list_entries():
            if entry.status not in {ModelRegistryStatus.CHALLENGER, ModelRegistryStatus.CHAMPION}:
                continue
            try:
                snapshot = self.compute_drift(entry.id)
                drift_count += 1
                if (
                    entry.status == ModelRegistryStatus.CHAMPION
                    and snapshot.status in {DriftStatus.CRITICAL, DriftStatus.INSUFFICIENT}
                ):
                    now = datetime.now(UTC)
                    self._repository.update_entry(replace(
                        entry, status=ModelRegistryStatus.DEMOTED,
                        demoted_at=now, updated_at=now,
                        decision_note=(
                            "漂移監控為嚴重或資料不足，系統自動降級冠軍模型。"
                        ),
                    ))
                    demoted += 1
            except (ValueError, RuntimeError):
                skipped += 1
        return GovernanceRefreshResult(
            registered=registered, drift_snapshots=drift_count,
            automatically_demoted=demoted, skipped=skipped,
        )

    def compute_drift(self, entry_id: int) -> ModelDriftSnapshot:
        entry = self._required_entry(entry_id)
        experiment = self._required_experiment(entry.experiment_id)
        feature_names = tuple(json.loads(experiment.feature_names_json))
        details, observation_count, all_recent_times = self._feature_drift_summary(
            entry.market, feature_names
        )
        latest_experiment = self._latest_experiment(
            entry.market, entry.model_name, entry.label_name
        )
        latest_score = self.quality_score(latest_experiment)
        directional_change = latest_experiment.directional_accuracy - experiment.directional_accuracy
        rank_change = (
            (latest_experiment.rank_ic or 0.0) - (experiment.rank_ic or 0.0)
            if latest_experiment.rank_ic is not None or experiment.rank_ic is not None
            else None
        )
        prediction_psi = self._prediction_psi(experiment, latest_experiment)
        psi_values = list(details.values())
        psi_median = float(np.median(psi_values)) if psi_values else None
        psi_max = max(psi_values) if psi_values else None
        enough = len(details) >= self._policy.minimum_monitored_features
        candidate_lost = (
            experiment.promotion_gate == "CANDIDATE"
            and latest_experiment.promotion_gate != "CANDIDATE"
        )
        critical = (
            candidate_lost
            or (psi_median is not None and psi_median >= self._policy.psi_critical)
            or (psi_max is not None and psi_max >= self._policy.psi_feature_max_critical)
            or directional_change <= self._policy.directional_drop_critical
            or (rank_change is not None and rank_change <= self._policy.rank_ic_drop_critical)
        )
        warning = (
            (psi_median is not None and psi_median >= self._policy.psi_warning)
            or (psi_max is not None and psi_max >= self._policy.psi_critical)
            or directional_change <= self._policy.directional_drop_warning
            or (rank_change is not None and rank_change <= self._policy.rank_ic_drop_warning)
        )
        status = (
            DriftStatus.INSUFFICIENT if not enough else
            DriftStatus.CRITICAL if critical else
            DriftStatus.WARNING if warning else DriftStatus.STABLE
        )
        drift_checks = self._drift_checks(
            len(details), psi_median, psi_max, directional_change,
            rank_change, candidate_lost,
        )
        now = datetime.now(UTC)
        recent_start = min(all_recent_times) if all_recent_times else experiment.data_end
        recent_end = max(all_recent_times) if all_recent_times else experiment.data_end
        snapshot = ModelDriftSnapshot(
            id=None, registry_entry_id=entry.id, policy_version=self._policy.version,
            snapshot_time=now, baseline_end=experiment.data_end,
            recent_start=recent_start, recent_end=recent_end,
            feature_count=len(details), observation_count=observation_count,
            feature_psi_median=psi_median, feature_psi_max=psi_max,
            prediction_psi=prediction_psi,
            directional_accuracy_change=directional_change,
            rank_ic_change=rank_change, quality_score_change=latest_score - entry.quality_score,
            status=status,
            feature_details_json=json.dumps(details, ensure_ascii=False, sort_keys=True),
            checks_json=json.dumps([asdict(item) for item in drift_checks], ensure_ascii=False),
            computed_at=now,
        )
        snapshot_id = self._repository.save_drift(snapshot)
        snapshot = replace(snapshot, id=snapshot_id)
        checks = self._eligibility_checks(experiment, snapshot)
        self._repository.update_entry(replace(
            entry, eligibility_checks_json=json.dumps(
                [asdict(item) for item in checks], ensure_ascii=False
            ), updated_at=now,
        ))
        return snapshot

    def _feature_drift_summary(
        self, market: str, feature_names: tuple[str, ...]
    ) -> tuple[dict[str, float], int, list[datetime]]:
        cache_key = (market, feature_names)
        cached = self._feature_drift_cache.get(cache_key)
        if cached is not None:
            details, observation_count, recent_times = cached
            return dict(details), observation_count, list(recent_times)
        active_symbols = [item.symbol for item in self._universe.list_active(market)]
        values = self._features.list_features(active_symbols, list(feature_names))
        grouped: dict[str, list[object]] = {}
        for value in values:
            if math.isfinite(value.value):
                grouped.setdefault(value.feature_name, []).append(value)
        details: dict[str, float] = {}
        all_recent_times: list[datetime] = []
        observation_count = 0
        for name in feature_names:
            items = sorted(grouped.get(name, []), key=lambda item: item.event_time)
            dates = sorted({item.event_time for item in items})
            if len(dates) < self._policy.recent_sessions + 2:
                continue
            recent_dates = set(dates[-self._policy.recent_sessions:])
            baseline_dates = set(dates[-(
                self._policy.baseline_sessions + self._policy.recent_sessions
            ):-self._policy.recent_sessions])
            baseline = np.asarray(
                [item.value for item in items if item.event_time in baseline_dates], dtype=float
            )
            recent = np.asarray(
                [item.value for item in items if item.event_time in recent_dates], dtype=float
            )
            if (
                baseline.size < self._policy.minimum_feature_observations
                or recent.size < self._policy.minimum_feature_observations
            ):
                continue
            psi = self.population_stability_index(baseline, recent)
            if psi is None:
                continue
            details[name] = psi
            observation_count += int(recent.size)
            all_recent_times.extend(item.event_time for item in items if item.event_time in recent_dates)
        cached_result = (dict(details), observation_count, list(all_recent_times))
        self._feature_drift_cache[cache_key] = cached_result
        return dict(details), observation_count, list(all_recent_times)

    @staticmethod
    def population_stability_index(
        baseline: np.ndarray, recent: np.ndarray, bins: int = 10
    ) -> float | None:
        baseline = baseline[np.isfinite(baseline)]
        recent = recent[np.isfinite(recent)]
        if baseline.size < 2 or recent.size < 2:
            return None
        quantiles = np.unique(np.quantile(baseline, np.linspace(0, 1, bins + 1)))
        if quantiles.size < 3:
            return 0.0 if np.allclose(np.median(baseline), np.median(recent)) else 1.0
        edges = quantiles.copy()
        edges[0], edges[-1] = -np.inf, np.inf
        expected = np.histogram(baseline, bins=edges)[0].astype(float)
        actual = np.histogram(recent, bins=edges)[0].astype(float)
        expected = np.clip(expected / expected.sum(), 1e-6, None)
        actual = np.clip(actual / actual.sum(), 1e-6, None)
        return float(np.sum((actual - expected) * np.log(actual / expected)))

    def _prediction_psi(
        self, baseline_experiment: ModelExperiment, latest_experiment: ModelExperiment
    ) -> float | None:
        predictions = self._models.list_predictions(market=baseline_experiment.market)
        baseline = np.asarray([
            item.predicted_value for item in predictions
            if item.experiment_id == baseline_experiment.id
        ], dtype=float)
        recent = np.asarray([
            item.predicted_value for item in predictions
            if item.experiment_id == latest_experiment.id
        ], dtype=float)
        if baseline.size < 20 or recent.size < 20:
            return None
        return self.population_stability_index(baseline, recent)

    def _latest_experiment(
        self, market: str, model_name: str, label_name: str
    ) -> ModelExperiment:
        matches = [
            item for item in self._models.list_runs(market)
            if item.model_name == model_name and item.label_name == label_name
        ]
        if not matches:
            raise LookupError("找不到相同模型的最新研究實驗")
        return max(matches, key=lambda item: self._timestamp(item.computed_at))

    def _eligibility_checks(
        self, experiment: ModelExperiment, drift: ModelDriftSnapshot | None
    ) -> tuple[GovernanceCheck, ...]:
        monitored = drift.feature_count if drift else 0
        monitor_ok = drift is not None and drift.status in {DriftStatus.STABLE, DriftStatus.WARNING}
        return (
            self._check("candidate", "模型研究門檻", experiment.promotion_gate == "CANDIDATE", experiment.promotion_gate, "CANDIDATE", "狀態", "底層模型必須先通過樣本外研究門檻。"),
            self._check("observations", "研究觀測數", experiment.observation_count >= self._policy.minimum_observations, experiment.observation_count, self._policy.minimum_observations, "筆", "避免以過少資料選出冠軍。"),
            self._check("folds", "樣本外折數", experiment.fold_count >= self._policy.minimum_folds, experiment.fold_count, self._policy.minimum_folds, "折", "至少三段互不重疊的樣本外期間。"),
            self._check("direction", "方向正確率", experiment.directional_accuracy >= self._policy.minimum_directional_accuracy, experiment.directional_accuracy, self._policy.minimum_directional_accuracy, "%", "方向預測需高於最低基準。"),
            self._check("rank_ic", "排序 IC", (experiment.rank_ic or 0.0) >= self._policy.minimum_rank_ic, experiment.rank_ic, self._policy.minimum_rank_ic, "無單位", "預測排名需與未來報酬呈穩定正相關。"),
            self._check("r2", "樣本外 R²", experiment.r2 > self._policy.minimum_r2, experiment.r2, self._policy.minimum_r2, "無單位", "必須優於只預測平均值的基準。"),
            self._check("monitor", "漂移監控可用", monitor_ok, drift.status.value if drift else "尚無", "stable 或 warning", "狀態", "嚴重漂移或資料不足時不得升級。"),
            self._check("features", "受監控特徵數", monitored >= self._policy.minimum_monitored_features, monitored, self._policy.minimum_monitored_features, "項", "至少監控三個模型輸入特徵。"),
        )

    def _drift_checks(
        self, feature_count: int, psi_median: float | None, psi_max: float | None,
        directional_change: float, rank_change: float | None, candidate_lost: bool,
    ) -> tuple[GovernanceCheck, ...]:
        return (
            self._check("features", "可監控特徵", feature_count >= self._policy.minimum_monitored_features, feature_count, self._policy.minimum_monitored_features, "項", "特徵不足時不允許宣稱模型穩定。"),
            self._check("psi_median", "特徵 PSI 中位數", psi_median is not None and psi_median < self._policy.psi_critical, psi_median, self._policy.psi_critical, "無單位", "PSI 越高代表整體輸入分布改變越大。"),
            self._check("psi_max", "最高單項 PSI", psi_max is not None and psi_max < self._policy.psi_feature_max_critical, psi_max, self._policy.psi_feature_max_critical, "無單位", "避免單一重要特徵劇烈漂移被平均值掩蓋。"),
            self._check("direction_change", "方向率變化", directional_change > self._policy.directional_drop_critical, directional_change, self._policy.directional_drop_critical, "%", "相對登錄基準的方向率下降幅度。"),
            self._check("rank_change", "Rank IC 變化", rank_change is None or rank_change > self._policy.rank_ic_drop_critical, rank_change, self._policy.rank_ic_drop_critical, "無單位", "相對登錄基準的排序能力下降幅度。"),
            self._check("candidate_retained", "候選資格未惡化", not candidate_lost, "已失去" if candidate_lost else "未失去", "不得失去", "狀態", "只有原本具候選資格、重訓後失去資格，才視為嚴重品質漂移。"),
        )

    @staticmethod
    def _check(
        key: str, label: str, passed: bool, value: object, threshold: object,
        unit: str, explanation: str,
    ) -> GovernanceCheck:
        return GovernanceCheck(
            key=key, label=label, passed=bool(passed), value=value,
            threshold=threshold, unit=unit, explanation=explanation,
        )

    def promote(self, entry_id: int, reviewer: str, note: str) -> RegistryEntryView:
        entry = self._required_entry(entry_id)
        if entry.status != ModelRegistryStatus.CHALLENGER:
            raise PermissionError("只有目前挑戰者可以升級為冠軍")
        reviewer, note = reviewer.strip(), note.strip()
        if len(reviewer) < 2 or len(note) < 10:
            raise ValueError("升級必須填寫審查人與至少 10 個字元的理由")
        experiment = self._required_experiment(entry.experiment_id)
        drift = self._repository.latest_drift(entry.id)
        checks = self._eligibility_checks(experiment, drift)
        if not all(item.passed for item in checks):
            raise PermissionError("模型未通過全部品質與漂移門檻，禁止升級冠軍")
        now = datetime.now(UTC)
        for current in self._repository.list_entries(entry.market):
            if (
                current.label_name == entry.label_name
                and current.status == ModelRegistryStatus.CHAMPION
            ):
                self._repository.update_entry(replace(
                    current, status=ModelRegistryStatus.DEMOTED,
                    demoted_at=now, reviewer=reviewer,
                    decision_note=f"由實驗 #{entry.experiment_id} 取代：{note}",
                    updated_at=now,
                ))
        promoted = replace(
            entry, status=ModelRegistryStatus.CHAMPION, promoted_at=now,
            demoted_at=None, reviewer=reviewer, decision_note=note, updated_at=now,
            eligibility_checks_json=json.dumps(
                [asdict(item) for item in checks], ensure_ascii=False
            ),
        )
        self._repository.update_entry(promoted)
        return self._view(promoted, recommended=True)

    def demote(self, entry_id: int, reviewer: str, note: str) -> RegistryEntryView:
        entry = self._required_entry(entry_id)
        if entry.status != ModelRegistryStatus.CHAMPION:
            raise PermissionError("只有冠軍模型可以降級")
        if len(reviewer.strip()) < 2 or len(note.strip()) < 10:
            raise ValueError("降級必須填寫審查人與至少 10 個字元的理由")
        now = datetime.now(UTC)
        updated = replace(
            entry, status=ModelRegistryStatus.DEMOTED, demoted_at=now,
            reviewer=reviewer.strip(), decision_note=note.strip(), updated_at=now,
        )
        self._repository.update_entry(updated)
        return self._view(updated, recommended=False)

    def overview(self) -> ModelGovernanceOverview:
        entries = self._repository.list_entries()
        recommendation: dict[tuple[str, str], int] = {}
        for entry in entries:
            if entry.status != ModelRegistryStatus.CHALLENGER:
                continue
            key = (entry.market, entry.label_name)
            current_id = recommendation.get(key)
            current = next((x for x in entries if x.id == current_id), None)
            if current is None or entry.quality_score > current.quality_score:
                recommendation[key] = entry.id
        views = tuple(
            self._view(entry, recommendation.get((entry.market, entry.label_name)) == entry.id)
            for entry in entries
            if entry.status != ModelRegistryStatus.RETIRED
        )
        return ModelGovernanceOverview(
            entries=views, policy=self._policy,
            champion_count=sum(item.entry.status == ModelRegistryStatus.CHAMPION for item in views),
            challenger_count=sum(item.entry.status == ModelRegistryStatus.CHALLENGER for item in views),
            critical_count=sum(item.drift and item.drift.status == DriftStatus.CRITICAL for item in views),
            warning_count=sum(item.drift and item.drift.status == DriftStatus.WARNING for item in views),
            stable_count=sum(item.drift and item.drift.status == DriftStatus.STABLE for item in views),
        )

    def _view(self, entry: ModelRegistryEntry, recommended: bool) -> RegistryEntryView:
        experiment = self._required_experiment(entry.experiment_id)
        drift = self._repository.latest_drift(entry.id) if entry.id else None
        checks = self._eligibility_checks(experiment, drift)
        drift_checks = tuple(
            GovernanceCheck(**item) for item in json.loads(drift.checks_json)
        ) if drift else ()
        return RegistryEntryView(
            entry=entry, experiment=experiment, drift=drift, checks=checks,
            drift_checks=drift_checks, eligible=all(item.passed for item in checks),
            recommended=recommended,
        )

    def _required_entry(self, entry_id: int) -> ModelRegistryEntry:
        entry = self._repository.get_entry(entry_id)
        if entry is None:
            raise LookupError("找不到模型登錄項目")
        return entry

    def _required_experiment(self, experiment_id: int) -> ModelExperiment:
        experiment = self._models.get(experiment_id)
        if experiment is None:
            raise LookupError("找不到模型實驗")
        return experiment
