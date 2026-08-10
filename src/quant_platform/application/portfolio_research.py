from __future__ import annotations

import json
import logging
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from quant_platform.application.factor_research import MARKET_BENCHMARKS
from quant_platform.application.ports import (
    EnsembleResearchRepository,
    MarketBarRepository,
    PortfolioResearchRepository,
    ResearchUniverseRepository,
    SchedulerJobRunRepository,
)
from quant_platform.domain.entities import EnsembleRun, JobRunStatus, PortfolioArtifact, PortfolioRun
from quant_platform.portfolio import PORTFOLIO_METHODS, PortfolioRiskEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PortfolioPipelineResult:
    run_id: int
    market: str
    status: str
    asset_count: int
    method_count: int
    portfolio_runs: int
    candidates: int
    failed: int
    failures: dict[str, str]
    started_at: str
    completed_at: str


@dataclass(frozen=True, slots=True)
class PortfolioRunView:
    run: PortfolioRun
    latest_weights: dict[str, float]
    sector_exposure: dict[str, float]
    factor_exposure: dict[str, float]
    stress_tests: dict[str, float]
    failed_gates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PortfolioOverview:
    run_count: int
    candidate_count: int
    market_count: int
    best_sharpe: float
    runs: list[PortfolioRunView]
    selected: PortfolioArtifact | None
    selected_view: PortfolioRunView | None
    selected_allocations: list[dict[str, object]]
    generated_at: datetime


class PortfolioOverviewService:
    def __init__(self, repository: PortfolioResearchRepository) -> None:
        self._repository = repository

    def get_overview(self, selected_id: int | None = None) -> PortfolioOverview:
        runs = self._repository.list_runs()
        views = [self._view(run) for run in runs]
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
        allocations: list[dict[str, object]] = []
        if selected:
            allocations = [
                {
                    "time": item.event_time.isoformat(),
                    "weights": json.loads(item.weights_json),
                    "cash": item.cash_weight,
                    "turnover": item.turnover,
                    "cost": item.cost,
                    "flags": json.loads(item.constraint_flags_json),
                }
                for item in selected.allocations
            ]
        return PortfolioOverview(
            run_count=len(runs),
            candidate_count=sum(run.promotion_gate == "CANDIDATE" for run in runs),
            market_count=len({run.market for run in runs}),
            best_sharpe=max((run.sharpe for run in runs), default=0.0),
            runs=views,
            selected=selected,
            selected_view=self._view(selected.run) if selected else None,
            selected_allocations=allocations,
            generated_at=datetime.now(UTC),
        )

    def get_artifact(self, run_id: int) -> PortfolioArtifact | None:
        return self._repository.get(run_id)

    @classmethod
    def _view(cls, run: PortfolioRun) -> PortfolioRunView:
        return PortfolioRunView(
            run=run,
            latest_weights=json.loads(run.latest_weights_json),
            sector_exposure=json.loads(run.sector_exposure_json),
            factor_exposure=json.loads(run.factor_exposure_json),
            stress_tests=json.loads(run.stress_tests_json),
            failed_gates=cls._failed_gates(run),
        )

    @staticmethod
    def _failed_gates(run: PortfolioRun) -> tuple[str, ...]:
        gates: list[str] = []
        if run.observation_count < 252:
            gates.append("OOS<252")
        if run.excess_to_equal <= 0:
            gates.append("NO_EDGE_VS_EQUAL")
        if run.excess_to_benchmark <= 0:
            gates.append("NO_EDGE_VS_BENCHMARK")
        if run.sharpe < 0.75:
            gates.append("SHARPE<0.75")
        if run.max_drawdown < -0.20:
            gates.append("DD>20%")
        if run.cvar_95 < -0.05:
            gates.append("CVAR>5%")
        limitations = run.limitations_json.lower()
        if "not promotion candidates" in limitations:
            gates.append("ENSEMBLE_GATE")
        if "not survivorship-safe" in limitations:
            gates.append("SURVIVORSHIP")
        return tuple(gates)


class PortfolioResearchPipeline:
    """Runs all portfolio methods on the latest market-level ensemble panel."""

    def __init__(
        self,
        universe_repository: ResearchUniverseRepository,
        market_repository: MarketBarRepository,
        ensemble_repository: EnsembleResearchRepository,
        portfolio_repository: PortfolioResearchRepository,
        run_repository: SchedulerJobRunRepository,
        engine: PortfolioRiskEngine,
        methods: tuple[str, ...] = PORTFOLIO_METHODS,
    ) -> None:
        self._universe_repository = universe_repository
        self._market_repository = market_repository
        self._ensemble_repository = ensemble_repository
        self._portfolio_repository = portfolio_repository
        self._run_repository = run_repository
        self._engine = engine
        self._methods = methods

    def run(self, market: str, now: datetime | None = None) -> PortfolioPipelineResult:
        normalized_market = market.upper()
        if normalized_market not in MARKET_BENCHMARKS:
            raise ValueError("market must be US or TW")
        started = now or datetime.now(UTC)
        if started.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        audit_id = self._run_repository.start("portfolio_risk_research", normalized_market, started)
        latest_runs: dict[str, EnsembleRun] = {}
        for run in self._ensemble_repository.list_runs(normalized_market):
            previous = latest_runs.get(run.symbol)
            if previous is None or self._run_order(run) > self._run_order(previous):
                latest_runs[run.symbol] = run
        ensembles = [
            artifact
            for run in latest_runs.values()
            if run.id is not None
            and (artifact := self._ensemble_repository.get(int(run.id))) is not None
        ]
        research_assets = [
            asset
            for asset in self._universe_repository.list_active(normalized_market)
            if asset.asset_type in {"EQUITY", "ETF"}
        ]
        sectors = {asset.symbol: asset.sector or "UNKNOWN" for asset in research_assets}
        liquidity = {
            asset.symbol: self._average_dollar_volume(asset.symbol, started)
            for asset in research_assets
        }
        benchmark = self._benchmark_returns(MARKET_BENCHMARKS[normalized_market], started)
        portfolio_runs = candidates = 0
        failures: dict[str, str] = {}
        for method in self._methods:
            try:
                artifact = self._engine.run(
                    method,
                    ensembles,
                    benchmark,
                    sectors,
                    liquidity,
                    computed_at=started,
                )
                if artifact is None:
                    raise ValueError("insufficient aligned ensemble history")
                self._portfolio_repository.save(artifact)
                portfolio_runs += 1
                candidates += artifact.run.promotion_gate == "CANDIDATE"
            except Exception as exc:
                logger.exception("Portfolio method failed for %s/%s", normalized_market, method)
                failures[method] = str(exc)
        completed = datetime.now(UTC)
        failed = len(failures)
        status = (
            JobRunStatus.SUCCEEDED
            if failed == 0
            else JobRunStatus.FAILED
            if portfolio_runs == 0
            else JobRunStatus.PARTIAL
        )
        result = PortfolioPipelineResult(
            run_id=audit_id,
            market=normalized_market,
            status=status.value,
            asset_count=len(ensembles),
            method_count=len(self._methods),
            portfolio_runs=portfolio_runs,
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

    @staticmethod
    def _run_order(run: EnsembleRun) -> tuple[datetime, datetime, int]:
        """Prefer the latest research batch, then its data window and persistent id."""
        return (
            run.computed_at.replace(tzinfo=None) if run.computed_at.tzinfo else run.computed_at,
            run.data_end.replace(tzinfo=None) if run.data_end.tzinfo else run.data_end,
            int(run.id or 0),
        )

    def _benchmark_returns(self, symbol: str, as_of: datetime) -> dict[date, float]:
        bars = self._market_repository.list_bars(symbol, as_of=as_of)
        ordered = sorted(bars, key=lambda item: item.event_time)
        output: dict[date, float] = {}
        for previous, current in zip(ordered, ordered[1:]):
            previous_close = float(previous.adjusted_close or previous.close)
            current_close = float(current.adjusted_close or current.close)
            if previous_close > 0:
                output[current.event_time.date()] = current_close / previous_close - 1
        return output

    def _average_dollar_volume(self, symbol: str, as_of: datetime) -> float:
        bars = self._market_repository.list_bars(symbol, as_of=as_of)
        values = [
            float(item.adjusted_close or item.close) * max(item.volume, 0)
            for item in bars[-60:]
        ]
        return statistics.fmean(values) if values else 0.0
