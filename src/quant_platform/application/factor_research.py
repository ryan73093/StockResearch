from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from quant_platform.application.ports import (
    FeatureLabelStoreRepository,
    MarketBarRepository,
    RegimeFactorResearchRepository,
    ResearchUniverseRepository,
    SchedulerJobRunRepository,
)
from quant_platform.domain.entities import FactorResearchResult, JobRunStatus, RegimeState
from quant_platform.factor import FactorResearchEngine
from quant_platform.regime import RegimeDetectionEngine

logger = logging.getLogger(__name__)


MARKET_BENCHMARKS = {"US": "SPY", "TW": "0050.TW"}


@dataclass(frozen=True, slots=True)
class FactorResearchPipelineResult:
    run_id: int
    market: str
    status: str
    symbol_count: int
    regime_values: int
    factor_results: int
    started_at: str
    completed_at: str
    error: str | None


@dataclass(frozen=True, slots=True)
class FactorResultView:
    result: FactorResearchResult
    quality_gate: str
    direction: str
    average_assets: float
    ic_curve: list[float]


@dataclass(frozen=True, slots=True)
class FactorResearchOverview:
    result_count: int
    market_count: int
    latest_regimes: list[RegimeState]
    results: list[FactorResultView]
    candidate_count: int
    generated_at: datetime


class FactorResearchOverviewService:
    def __init__(self, repository: RegimeFactorResearchRepository) -> None:
        self._repository = repository

    def get_overview(self) -> FactorResearchOverview:
        results = self._repository.list_factor_results()
        regimes = self._repository.list_regimes()
        latest_by_symbol: dict[str, RegimeState] = {}
        for regime in regimes:
            current = latest_by_symbol.get(regime.symbol)
            if current is None or regime.event_time > current.event_time:
                latest_by_symbol[regime.symbol] = regime
        views = []
        for item in results:
            daily = json.loads(item.daily_metrics_json).get("5d", [])
            average_assets = (
                sum(entry.get("n", 0) for entry in daily) / len(daily) if daily else 0.0
            )
            views.append(
                FactorResultView(
                    result=item,
                    quality_gate=self._quality_gate(item, average_assets),
                    direction="HIGH" if (item.rank_ic_5d or 0) >= 0 else "LOW",
                    average_assets=average_assets,
                    ic_curve=[float(entry["rank_ic"]) for entry in daily[-60:]],
                )
            )
        views.sort(key=lambda item: abs(item.result.rank_ic_5d or 0), reverse=True)
        return FactorResearchOverview(
            result_count=len(results),
            market_count=len({item.market for item in results}),
            latest_regimes=sorted(latest_by_symbol.values(), key=lambda item: item.symbol),
            results=views,
            candidate_count=sum(item.quality_gate == "CANDIDATE" for item in views),
            generated_at=datetime.now(UTC),
        )

    @staticmethod
    def _quality_gate(result: FactorResearchResult, average_assets: float) -> str:
        if average_assets < 30:
            return "SMALL_UNIVERSE"
        if result.cross_sections_5d < 100:
            return "INSUFFICIENT"
        rank_ic = abs(result.rank_ic_5d or 0)
        positive_rate = max(result.positive_ic_rate_5d or 0, 1 - (result.positive_ic_rate_5d or 0))
        if rank_ic >= 0.03 and positive_rate >= 0.52 and result.cross_sections_20d >= 80:
            return "CANDIDATE"
        if rank_ic < 0.01:
            return "WEAK"
        return "EXPLORATORY"


class FactorResearchPipeline:
    """Builds market regimes and persisted factor evidence for one market."""

    def __init__(
        self,
        universe_repository: ResearchUniverseRepository,
        market_repository: MarketBarRepository,
        feature_store_repository: FeatureLabelStoreRepository,
        research_repository: RegimeFactorResearchRepository,
        run_repository: SchedulerJobRunRepository,
        regime_engine: RegimeDetectionEngine,
        factor_engine: FactorResearchEngine,
    ) -> None:
        self._universe_repository = universe_repository
        self._market_repository = market_repository
        self._feature_store_repository = feature_store_repository
        self._research_repository = research_repository
        self._run_repository = run_repository
        self._regime_engine = regime_engine
        self._factor_engine = factor_engine

    def run(self, market: str, now: datetime | None = None) -> FactorResearchPipelineResult:
        normalized_market = market.upper()
        if normalized_market not in MARKET_BENCHMARKS:
            raise ValueError("market must be US or TW")
        started = now or datetime.now(UTC)
        if started.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        run_id = self._run_repository.start("regime_factor_research", normalized_market, started)
        error: str | None = None
        regime_count = 0
        result_count = 0
        assets = [
            asset
            for asset in self._universe_repository.list_active(normalized_market)
            if asset.asset_type in {"EQUITY", "ETF"}
        ]
        try:
            benchmark = MARKET_BENCHMARKS[normalized_market]
            bars = self._market_repository.list_bars(benchmark)
            regimes = self._regime_engine.compute(bars, computed_at=started)
            regime_count = self._research_repository.upsert_regimes(regimes)
            symbols = [asset.symbol for asset in assets]
            definitions = self._feature_store_repository.list_definitions()
            features = self._feature_store_repository.list_features(symbols)
            labels = self._feature_store_repository.list_labels(
                symbols, ["future_return_5d", "future_return_20d", "excess_return_5d"]
            )
            factor_results = self._factor_engine.evaluate(
                normalized_market, definitions, features, labels, regimes, as_of=started
            )
            result_count = self._research_repository.upsert_factor_results(factor_results)
            status = JobRunStatus.SUCCEEDED
        except Exception as exc:
            logger.exception("Regime/factor research failed for %s", normalized_market)
            error = str(exc)
            status = JobRunStatus.FAILED
        completed = datetime.now(UTC)
        result = FactorResearchPipelineResult(
            run_id=run_id,
            market=normalized_market,
            status=status.value,
            symbol_count=len(assets),
            regime_values=regime_count,
            factor_results=result_count,
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
            error=error,
        )
        self._run_repository.finish(
            run_id,
            status.value,
            completed,
            json.dumps(asdict(result), ensure_ascii=False),
            error,
        )
        return result
