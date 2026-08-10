from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from quant_platform.application.factor_research import MARKET_BENCHMARKS
from quant_platform.application.universe_history import UniverseHistoryService
from quant_platform.application.ports import (
    BacktestResearchRepository,
    MarketBarRepository,
    RegimeFactorResearchRepository,
    ResearchUniverseRepository,
    SchedulerJobRunRepository,
)
from quant_platform.backtest import BiasSafeBacktestEngine
from quant_platform.domain.entities import BacktestArtifact, BacktestRun, JobRunStatus
from quant_platform.strategy.base import SignalStrategy

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BacktestResearchPipelineResult:
    run_id: int
    market: str
    status: str
    asset_count: int
    strategy_count: int
    backtest_runs: int
    candidates: int
    failed: int
    failures: dict[str, str]
    started_at: str
    completed_at: str


@dataclass(frozen=True, slots=True)
class BacktestRunView:
    run: BacktestRun
    failed_gates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BacktestResearchOverview:
    run_count: int
    candidate_count: int
    fold_count: int
    trade_count: int
    runs: list[BacktestRunView]
    selected: BacktestArtifact | None
    generated_at: datetime


class BacktestResearchOverviewService:
    def __init__(self, repository: BacktestResearchRepository) -> None:
        self._repository = repository

    def get_overview(self, selected_id: int | None = None) -> BacktestResearchOverview:
        runs = self._repository.list_runs()
        views: list[BacktestRunView] = []
        for run in runs:
            views.append(
                BacktestRunView(
                    run=run,
                    failed_gates=self._failed_gates(run),
                )
            )
        views.sort(
            key=lambda item: (
                item.run.promotion_gate == "CANDIDATE",
                item.run.sharpe,
                item.run.excess_return,
            ),
            reverse=True,
        )
        selected = self._repository.get(selected_id) if selected_id is not None else None
        if selected is None and views and views[0].run.id is not None:
            selected = self._repository.get(int(views[0].run.id))
        return BacktestResearchOverview(
            run_count=len(runs),
            candidate_count=sum(item.promotion_gate == "CANDIDATE" for item in runs),
            fold_count=sum(item.fold_count for item in runs),
            trade_count=sum(item.trade_count for item in runs),
            runs=views,
            selected=selected,
            generated_at=datetime.now(UTC),
        )

    def get_artifact(self, run_id: int) -> BacktestArtifact | None:
        return self._repository.get(run_id)

    @staticmethod
    def _failed_gates(run: BacktestRun) -> tuple[str, ...]:
        gates = []
        if run.fold_count < 4:
            gates.append("FOLDS<4")
        if run.observation_count < 252:
            gates.append("OOS<252")
        if run.excess_return <= 0:
            gates.append("NO_ALPHA")
        if run.sharpe < 0.75:
            gates.append("SHARPE<0.75")
        if run.max_drawdown < -0.25:
            gates.append("DD>25%")
        if (run.profit_factor or 0) < 1.10:
            gates.append("PF<1.10")
        if run.positive_fold_rate < 2 / 3:
            gates.append("FOLD_STABILITY")
        if "not survivorship-safe" in run.limitations_json:
            gates.append("SURVIVORSHIP")
        return tuple(gates)


class BacktestResearchPipeline:
    """Runs all registered strategies over one market and persists complete artifacts."""

    def __init__(
        self,
        universe_repository: ResearchUniverseRepository,
        market_repository: MarketBarRepository,
        regime_repository: RegimeFactorResearchRepository,
        backtest_repository: BacktestResearchRepository,
        run_repository: SchedulerJobRunRepository,
        universe_history: UniverseHistoryService,
        engine: BiasSafeBacktestEngine,
        strategies: tuple[SignalStrategy, ...],
    ) -> None:
        self._universe_repository = universe_repository
        self._market_repository = market_repository
        self._regime_repository = regime_repository
        self._backtest_repository = backtest_repository
        self._run_repository = run_repository
        self._universe_history = universe_history
        self._engine = engine
        self._strategies = strategies

    def run(
        self,
        market: str,
        now: datetime | None = None,
        symbols: list[str] | None = None,
    ) -> BacktestResearchPipelineResult:
        normalized_market = market.upper()
        if normalized_market not in MARKET_BENCHMARKS:
            raise ValueError("market must be US or TW")
        started = now or datetime.now(UTC)
        if started.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        audit_id = self._run_repository.start("walk_forward_backtest", normalized_market, started)
        assets = [
            asset
            for asset in self._universe_repository.list_active(normalized_market)
            if asset.asset_type in {"EQUITY", "ETF"}
            and (symbols is None or asset.symbol in symbols)
        ]
        regimes = self._regime_repository.list_regimes(MARKET_BENCHMARKS[normalized_market])
        history_audit = self._universe_history.audit(started.date(), normalized_market)
        backtest_runs = 0
        candidates = 0
        failures: dict[str, str] = {}
        for asset in assets:
            bars = self._market_repository.list_bars(asset.symbol, as_of=started)
            for strategy in self._strategies:
                key = f"{asset.symbol}/{strategy.name}"
                try:
                    artifact = self._engine.run(
                        bars, strategy, regimes, computed_at=started,
                        survivorship_safe=history_audit.survivorship_safe,
                    )
                    if artifact is None:
                        raise ValueError("insufficient history for configured walk-forward folds")
                    self._backtest_repository.save(artifact)
                    backtest_runs += 1
                    candidates += artifact.run.promotion_gate == "CANDIDATE"
                except Exception as exc:
                    logger.exception("Backtest failed for %s", key)
                    failures[key] = str(exc)
        completed = datetime.now(UTC)
        failed = len(failures)
        status = (
            JobRunStatus.SUCCEEDED
            if failed == 0
            else JobRunStatus.FAILED
            if backtest_runs == 0
            else JobRunStatus.PARTIAL
        )
        result = BacktestResearchPipelineResult(
            run_id=audit_id,
            market=normalized_market,
            status=status.value,
            asset_count=len(assets),
            strategy_count=len(self._strategies),
            backtest_runs=backtest_runs,
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
