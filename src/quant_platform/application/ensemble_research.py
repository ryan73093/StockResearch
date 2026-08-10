from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from quant_platform.application.factor_research import MARKET_BENCHMARKS
from quant_platform.application.ports import (
    BacktestResearchRepository,
    EnsembleResearchRepository,
    RegimeFactorResearchRepository,
    SchedulerJobRunRepository,
)
from quant_platform.domain.entities import EnsembleArtifact, EnsembleRun, JobRunStatus
from quant_platform.ensemble import DynamicStrategyEnsembleEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EnsemblePipelineResult:
    run_id: int
    market: str
    status: str
    asset_count: int
    ensemble_runs: int
    candidates: int
    failed: int
    failures: dict[str, str]
    started_at: str
    completed_at: str


@dataclass(frozen=True, slots=True)
class EnsembleRunView:
    run: EnsembleRun
    latest_weights: dict[str, float]
    average_weights: dict[str, float]
    failed_gates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EnsembleOverview:
    run_count: int
    candidate_count: int
    average_excess: float
    runs: list[EnsembleRunView]
    selected: EnsembleArtifact | None
    selected_weights: list[dict[str, object]]
    generated_at: datetime


class EnsembleOverviewService:
    def __init__(self, repository: EnsembleResearchRepository) -> None:
        self._repository = repository

    def get_overview(self, selected_id: int | None = None) -> EnsembleOverview:
        runs = self._repository.list_runs()
        views = [
            EnsembleRunView(
                run=run,
                latest_weights=json.loads(run.latest_weights_json),
                average_weights=json.loads(run.average_weights_json),
                failed_gates=self._failed_gates(run),
            )
            for run in runs
        ]
        views.sort(
            key=lambda item: (
                item.run.promotion_gate == "CANDIDATE",
                item.run.sharpe,
                item.run.excess_to_equal,
            ),
            reverse=True,
        )
        selected = self._repository.get(selected_id) if selected_id is not None else None
        if selected is None and views and views[0].run.id is not None:
            selected = self._repository.get(int(views[0].run.id))
        selected_weights = []
        if selected:
            selected_weights = [
                {
                    "time": point.event_time.isoformat(),
                    "regime": point.regime,
                    "weights": json.loads(point.weights_json),
                    "scores": json.loads(point.scores_json),
                    "contributions": json.loads(point.contributions_json),
                    "turnover": point.turnover,
                    "cost": point.cost,
                    "return": point.daily_return,
                }
                for point in selected.weights
            ]
        return EnsembleOverview(
            run_count=len(runs),
            candidate_count=sum(run.promotion_gate == "CANDIDATE" for run in runs),
            average_excess=(sum(run.excess_to_equal for run in runs) / len(runs) if runs else 0.0),
            runs=views,
            selected=selected,
            selected_weights=selected_weights,
            generated_at=datetime.now(UTC),
        )

    def get_artifact(self, run_id: int) -> EnsembleArtifact | None:
        return self._repository.get(run_id)

    @staticmethod
    def _failed_gates(run: EnsembleRun) -> tuple[str, ...]:
        gates: list[str] = []
        if run.observation_count < 252:
            gates.append("OOS<252")
        if run.excess_to_equal <= 0:
            gates.append("NO_EDGE_VS_EQUAL")
        if run.sharpe <= run.equal_weight_sharpe:
            gates.append("SHARPE<=EQUAL")
        if run.max_drawdown < -0.25:
            gates.append("DD>25%")
        limitations = run.limitations_json.lower()
        if "not a promotion candidate" in limitations:
            gates.append("COMPONENT_GATE")
        if "not survivorship-safe" in limitations:
            gates.append("SURVIVORSHIP")
        return tuple(gates)


class EnsembleResearchPipeline:
    """Builds one adaptive ensemble per asset from persisted OOS backtest artifacts."""

    def __init__(
        self,
        backtest_repository: BacktestResearchRepository,
        regime_repository: RegimeFactorResearchRepository,
        ensemble_repository: EnsembleResearchRepository,
        run_repository: SchedulerJobRunRepository,
        engine: DynamicStrategyEnsembleEngine,
    ) -> None:
        self._backtest_repository = backtest_repository
        self._regime_repository = regime_repository
        self._ensemble_repository = ensemble_repository
        self._run_repository = run_repository
        self._engine = engine

    def run(self, market: str, now: datetime | None = None) -> EnsemblePipelineResult:
        normalized_market = market.upper()
        if normalized_market not in MARKET_BENCHMARKS:
            raise ValueError("market must be US or TW")
        started = now or datetime.now(UTC)
        if started.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        audit_id = self._run_repository.start("strategy_ensemble", normalized_market, started)
        grouped: dict[str, list[int]] = {}
        for run in self._backtest_repository.list_runs(normalized_market):
            if run.id is not None:
                grouped.setdefault(run.symbol, []).append(int(run.id))
        regimes = self._regime_repository.list_regimes(MARKET_BENCHMARKS[normalized_market])
        ensemble_runs = candidates = 0
        failures: dict[str, str] = {}
        for symbol, run_ids in grouped.items():
            try:
                artifacts = [
                    artifact
                    for run_id in run_ids
                    if (artifact := self._backtest_repository.get(run_id)) is not None
                ]
                artifact = self._engine.run(artifacts, regimes, computed_at=started)
                if artifact is None:
                    raise ValueError("three aligned strategy artifacts are required")
                self._ensemble_repository.save(artifact)
                ensemble_runs += 1
                candidates += artifact.run.promotion_gate == "CANDIDATE"
            except Exception as exc:
                logger.exception("Ensemble failed for %s", symbol)
                failures[symbol] = str(exc)
        completed = datetime.now(UTC)
        failed = len(failures)
        status = (
            JobRunStatus.SUCCEEDED
            if failed == 0
            else JobRunStatus.FAILED
            if ensemble_runs == 0
            else JobRunStatus.PARTIAL
        )
        result = EnsemblePipelineResult(
            run_id=audit_id,
            market=normalized_market,
            status=status.value,
            asset_count=len(grouped),
            ensemble_runs=ensemble_runs,
            candidates=candidates,
            failed=failed,
            failures=failures,
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
        )
        self._run_repository.finish(
            audit_id,
            status.value,
            completed,
            json.dumps(asdict(result), ensure_ascii=False),
            "; ".join(f"{key}: {value}" for key, value in failures.items()) or None,
        )
        return result
