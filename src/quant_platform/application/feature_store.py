from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from quant_platform.application.ports import (
    FeatureLabelStoreRepository,
    MarketBarRepository,
    ResearchUniverseRepository,
    SchedulerJobRunRepository,
)
from quant_platform.domain.entities import JobRunStatus, StoreCoverage
from quant_platform.feature_engineering.engine import FeatureEngine
from quant_platform.feature_engineering.cross_asset import CrossAssetFeatureEngine
from quant_platform.labels.engine import LABEL_DEFINITIONS, LabelEngine

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FeatureLabelPipelineResult:
    run_id: int
    market: str
    status: str
    asset_count: int
    succeeded: int
    failed: int
    feature_values: int
    label_values: int
    failures: dict[str, str]
    started_at: str
    completed_at: str


@dataclass(frozen=True, slots=True)
class FeatureStoreOverview:
    feature_definition_count: int
    label_definition_count: int
    feature_value_count: int
    label_value_count: int
    symbol_count: int
    feature_coverage: list[StoreCoverage]
    label_coverage: list[StoreCoverage]
    label_definitions: tuple[dict[str, object], ...]


class FeatureStoreOverviewService:
    def __init__(self, repository: FeatureLabelStoreRepository) -> None:
        self._repository = repository

    def get_overview(self) -> FeatureStoreOverview:
        definitions = self._repository.list_definitions()
        feature_coverage = self._repository.feature_coverage()
        label_coverage = self._repository.label_coverage()
        symbols = {item.symbol for item in feature_coverage + label_coverage}
        return FeatureStoreOverview(
            feature_definition_count=len(definitions),
            label_definition_count=len(LABEL_DEFINITIONS),
            feature_value_count=sum(item.row_count for item in feature_coverage),
            label_value_count=sum(item.row_count for item in label_coverage),
            symbol_count=len(symbols),
            feature_coverage=feature_coverage,
            label_coverage=label_coverage,
            label_definitions=LABEL_DEFINITIONS,
        )

    def list_feature_definitions(self):
        return self._repository.list_definitions()


class FeatureLabelPipeline:
    """Materializes the feature/label stores from point-in-time market bars."""

    def __init__(
        self,
        universe_repository: ResearchUniverseRepository,
        market_bar_repository: MarketBarRepository,
        store_repository: FeatureLabelStoreRepository,
        run_repository: SchedulerJobRunRepository,
        feature_engine: FeatureEngine,
        label_engine: LabelEngine,
        cross_asset_engine: CrossAssetFeatureEngine | None = None,
    ) -> None:
        self._universe_repository = universe_repository
        self._market_bar_repository = market_bar_repository
        self._store_repository = store_repository
        self._run_repository = run_repository
        self._feature_engine = feature_engine
        self._label_engine = label_engine
        self._cross_asset_engine = cross_asset_engine or CrossAssetFeatureEngine()
        self._store_repository.register_definitions(feature_engine.definitions)
        self._store_repository.register_definitions(self._cross_asset_engine.definitions)

    def run(
        self,
        market: str,
        now: datetime | None = None,
        symbols: list[str] | None = None,
    ) -> FeatureLabelPipelineResult:
        normalized_market = market.upper()
        if normalized_market not in {"US", "TW"}:
            raise ValueError("market must be US or TW")
        started = now or datetime.now(UTC)
        if started.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        requested = {item.strip().upper() for item in symbols or []}
        assets = [
            asset
            for asset in self._universe_repository.list_active(normalized_market)
            if not requested or asset.symbol in requested
        ]
        run_id = self._run_repository.start("feature_label_build", normalized_market, started)
        succeeded = 0
        feature_values = 0
        label_values = 0
        failures: dict[str, str] = {}
        bar_cache: dict[str, list] = {}

        def bars_for(symbol: str):
            if symbol not in bar_cache:
                bar_cache[symbol] = self._market_bar_repository.list_bars(symbol)
            return bar_cache[symbol]

        context_bars = {symbol: bars_for(symbol) for symbol in self._cross_asset_engine.symbols}

        for asset in assets:
            try:
                bars = bars_for(asset.symbol)
                if not bars:
                    raise ValueError("no daily market bars available")
                benchmark_bars = bars_for(asset.benchmark_symbol) if asset.benchmark_symbol else []
                features = self._feature_engine.compute(bars, computed_at=started)
                features.extend(self._cross_asset_engine.compute(bars, context_bars, computed_at=started))
                labels = self._label_engine.compute(bars, benchmark_bars, computed_at=started)
                feature_values += self._store_repository.upsert_features(features)
                label_values += self._store_repository.upsert_labels(labels)
                succeeded += 1
            except Exception as exc:  # A single symbol cannot invalidate the market run.
                logger.exception("Feature/label build failed for %s", asset.symbol)
                failures[asset.symbol] = str(exc)

        completed = datetime.now(UTC)
        failed = len(failures)
        status = (
            JobRunStatus.SUCCEEDED
            if failed == 0
            else JobRunStatus.FAILED
            if succeeded == 0
            else JobRunStatus.PARTIAL
        )
        result = FeatureLabelPipelineResult(
            run_id=run_id,
            market=normalized_market,
            status=status.value,
            asset_count=len(assets),
            succeeded=succeeded,
            failed=failed,
            feature_values=feature_values,
            label_values=label_values,
            failures=failures,
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
        )
        self._run_repository.finish(
            run_id,
            status.value,
            completed,
            json.dumps(asdict(result), ensure_ascii=False),
            "; ".join(f"{symbol}: {error}" for symbol, error in failures.items()) or None,
        )
        return result
