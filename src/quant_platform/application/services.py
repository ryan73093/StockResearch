from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from quant_platform.application.ports import (
    DatabaseProbe,
    ExecutionLockManager,
    MarketBarRepository,
    MarketDataProvider,
    ResearchExperimentRepository,
)
from quant_platform.domain.entities import MarketBar


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    status: str
    database: str
    checked_at: str
    version: str
    redis: str
    lock_backend: str


class HealthService:
    def __init__(
        self, database: DatabaseProbe, version: str,
        lock_manager: ExecutionLockManager, redis_enabled: bool, redis_required: bool,
    ) -> None:
        self._database = database
        self._version = version
        self._locks = lock_manager
        self._redis_enabled = redis_enabled
        self._redis_required = redis_required

    def check(self) -> HealthSnapshot:
        database_ok = self._database.ping()
        redis_ok = self._locks.backend == "redis" and self._locks.ping()
        infrastructure_ok = database_ok and (redis_ok or not self._redis_required)
        return HealthSnapshot(
            status="healthy" if infrastructure_ok else "degraded",
            database="connected" if database_ok else "unavailable",
            checked_at=datetime.now(UTC).isoformat(),
            version=self._version,
            redis=("connected" if redis_ok else "unavailable") if self._redis_enabled else "disabled",
            lock_backend=self._locks.backend,
        )


class ResearchOverviewService:
    def __init__(self, repository: ResearchExperimentRepository) -> None:
        self._repository = repository

    def get_overview(self) -> dict[str, object]:
        recent = self._repository.list_recent(limit=5)
        return {
            "experiment_count": self._repository.count(),
            "recent_experiments": recent,
        }


class DataQualityError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IngestionResult:
    symbol: str
    source: str
    received: int
    inserted: int
    duplicates: int
    started_at: str
    completed_at: str


class MarketBarValidator:
    def validate(self, bars: list[MarketBar]) -> None:
        natural_keys: set[tuple[object, ...]] = set()
        for bar in bars:
            if bar.event_time.tzinfo is None or bar.available_time.tzinfo is None:
                raise DataQualityError("event_time and available_time must be timezone-aware")
            if bar.available_time < bar.event_time:
                raise DataQualityError("available_time cannot precede event_time")
            if bar.available_time > bar.ingested_at:
                raise DataQualityError("a bar cannot be ingested before its available_time")
            if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
                raise DataQualityError(f"invalid OHLC range for {bar.symbol} at {bar.event_time}")
            if bar.high < bar.low:
                raise DataQualityError(f"high is below low for {bar.symbol} at {bar.event_time}")
            if bar.volume < 0:
                raise DataQualityError(f"negative volume for {bar.symbol} at {bar.event_time}")
            key = (bar.symbol, bar.interval, bar.source, bar.event_time, bar.available_time)
            if key in natural_keys:
                raise DataQualityError(f"duplicate provider row for {bar.symbol} at {bar.event_time}")
            natural_keys.add(key)


class MarketDataIngestionService:
    def __init__(
        self,
        provider: MarketDataProvider,
        repository: MarketBarRepository,
        validator: MarketBarValidator | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._validator = validator or MarketBarValidator()

    def ingest_daily(
        self, symbol: str, market: str, start: datetime, end: datetime
    ) -> IngestionResult:
        started = datetime.now(UTC)
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start and end must be timezone-aware")
        if start >= end:
            raise ValueError("start must be earlier than end")
        normalized_symbol = symbol.strip().upper()
        bars = self._provider.fetch_daily_bars(normalized_symbol, market, start, end)
        if not bars:
            raise DataQualityError(
                f"{self._provider.name} returned no daily bars for "
                f"{normalized_symbol} between {start.date()} and {end.date()}"
            )
        if any(bar.symbol != normalized_symbol for bar in bars):
            raise DataQualityError("provider returned a bar for the wrong symbol")
        if any(bar.source != self._provider.name for bar in bars):
            raise DataQualityError("provider source does not match the configured adapter")
        self._validator.validate(bars)
        inserted = self._repository.add_missing(bars)
        completed = datetime.now(UTC)
        return IngestionResult(
            symbol=normalized_symbol,
            source=self._provider.name,
            received=len(bars),
            inserted=inserted,
            duplicates=len(bars) - inserted,
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
        )


class MarketDataOverviewService:
    def __init__(self, repository: MarketBarRepository) -> None:
        self._repository = repository

    def get_overview(self) -> dict[str, object]:
        coverage = self._repository.list_coverage()
        return {
            "dataset_count": len(coverage),
            "row_count": sum(item.row_count for item in coverage),
            "coverage": coverage,
        }
