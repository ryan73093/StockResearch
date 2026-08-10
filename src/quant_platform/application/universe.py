from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from quant_platform.application.analytics import DEFAULT_UNIVERSE
from quant_platform.application.ports import (
    MarketBarRepository,
    ResearchUniverseRepository,
    SchedulerJobRunRepository,
)
from quant_platform.application.services import MarketDataIngestionService
from quant_platform.domain.entities import JobRunStatus, ResearchAsset, SchedulerJobRun

logger = logging.getLogger(__name__)


DEFAULT_ASSET_METADATA: dict[str, tuple[str, str, str | None, str]] = {
    "SPY": ("US", "ETF", "Broad Market", "SPY"),
    "QQQ": ("US", "ETF", "Technology Growth", "SPY"),
    "IWM": ("US", "ETF", "Small Cap", "SPY"),
    "XLK": ("US", "ETF", "Technology", "SPY"),
    "XLF": ("US", "ETF", "Financials", "SPY"),
    "XLE": ("US", "ETF", "Energy", "SPY"),
    "XLV": ("US", "ETF", "Health Care", "SPY"),
    "XLI": ("US", "ETF", "Industrials", "SPY"),
    "XLP": ("US", "ETF", "Consumer Staples", "SPY"),
    "XLY": ("US", "ETF", "Consumer Discretionary", "SPY"),
    "TLT": ("US", "ETF", "Treasury Bond", "SPY"),
    "GLD": ("US", "ETF", "Gold", "SPY"),
    "^IXIC": ("US", "INDEX", "NASDAQ Composite", "^GSPC"),
    "^GSPC": ("US", "INDEX", "S&P 500", "^GSPC"),
    "^SOX": ("US", "INDEX", "Philadelphia Semiconductor", "^GSPC"),
    "GC=F": ("US", "COMMODITY", "Gold Futures", "^GSPC"),
    "TWD=X": ("US", "FX", "USD/TWD", "^GSPC"),
    "^TWII": ("TW", "INDEX", "Taiwan Weighted Index", "0050.TW"),
    "JPY=X": ("US", "FX", "USD/JPY", "^GSPC"),
    "^VIX": ("US", "VOLATILITY", "CBOE VIX", "^GSPC"),
    "BZ=F": ("US", "COMMODITY", "Brent Crude", "^GSPC"),
    "0050.TW": ("TW", "ETF", "Broad Market", "0050.TW"),
    "0056.TW": ("TW", "ETF", "Dividend", "0050.TW"),
    "006208.TW": ("TW", "ETF", "Broad Market", "0050.TW"),
    "00878.TW": ("TW", "ETF", "Dividend ESG", "0050.TW"),
}

DEFAULT_ASSET_NAMES = {
    "0050.TW": "元大台灣50",
    "0056.TW": "元大高股息",
    "006208.TW": "富邦台50",
    "00878.TW": "國泰永續高股息",
    "^TWII": "臺灣加權股價指數",
}


class UniverseValidationError(ValueError):
    pass


class ResearchUniverseService:
    def __init__(
        self,
        repository: ResearchUniverseRepository,
        company_provider: object | None = None,
    ) -> None:
        self._repository = repository
        self._company_provider = company_provider
        self._company_profile_cache: dict[str, object] = {}

    def ensure_default_universe(self) -> None:
        now = datetime.now(UTC)
        for symbol in DEFAULT_UNIVERSE:
            if self._repository.get(symbol) is not None:
                if symbol in DEFAULT_ASSET_NAMES:
                    self._repository.update_metadata(
                        symbol,
                        company_name=DEFAULT_ASSET_NAMES[symbol],
                        company_abbreviation=DEFAULT_ASSET_NAMES[symbol],
                        metadata_source="built-in-index-etf-identity",
                        metadata_updated_at=now,
                    )
                continue
            market, asset_type, sector, benchmark = DEFAULT_ASSET_METADATA[symbol]
            self._repository.add(
                ResearchAsset(
                    id=None,
                    symbol=symbol,
                    market=market,
                    asset_type=asset_type,
                    sector=sector,
                    benchmark_symbol=benchmark,
                    active=True,
                    data_start=date(2020, 1, 1),
                    created_at=now,
                    updated_at=now,
                    company_name=DEFAULT_ASSET_NAMES.get(symbol),
                    company_abbreviation=DEFAULT_ASSET_NAMES.get(symbol),
                    metadata_source=(
                        "built-in-index-etf-identity"
                        if symbol in DEFAULT_ASSET_NAMES else None
                    ),
                    metadata_updated_at=now if symbol in DEFAULT_ASSET_NAMES else None,
                )
            )

    def list_all(self) -> list[ResearchAsset]:
        return self._repository.list_all()

    def active_symbols(self, market: str | None = None) -> list[str]:
        return [asset.symbol for asset in self._repository.list_active(market)]

    def add_asset(
        self,
        symbol: str,
        market: str,
        asset_type: str = "EQUITY",
        sector: str | None = None,
        benchmark_symbol: str | None = None,
        data_start: date | None = None,
    ) -> ResearchAsset:
        normalized_symbol = symbol.strip().upper()
        normalized_market = market.strip().upper()
        if not normalized_symbol:
            raise UniverseValidationError("symbol is required")
        if normalized_market not in {"US", "TW"}:
            raise UniverseValidationError("market must be US or TW")
        existing = self._repository.get(normalized_symbol)
        if existing is not None:
            if not existing.active:
                self._repository.set_active(normalized_symbol, True)
            return self._repository.get(normalized_symbol) or existing
        now = datetime.now(UTC)
        return self._repository.add(
            ResearchAsset(
                id=None,
                symbol=normalized_symbol,
                market=normalized_market,
                asset_type=asset_type.strip().upper() or "EQUITY",
                sector=sector.strip() if sector else None,
                benchmark_symbol=benchmark_symbol.strip().upper() if benchmark_symbol else None,
                active=True,
                data_start=data_start or date(2020, 1, 1),
                created_at=now,
                updated_at=now,
            )
        )

    def set_active(self, symbol: str, active: bool) -> bool:
        return self._repository.set_active(symbol.strip().upper(), active)

    def sync_taiwan_company_metadata(self) -> int:
        if self._company_provider is None:
            return 0
        profiles = self._company_provider.fetch_company_profiles()
        self._company_profile_cache = {
            profile.symbol: profile for profile in profiles
        }
        now = datetime.now(UTC)
        changed = 0
        for profile in profiles:
            if self._repository.update_metadata(
                profile.symbol,
                company_name=profile.company_name,
                company_abbreviation=profile.company_abbreviation,
                industry_code=profile.industry_code,
                sector=profile.sector,
                paid_in_capital=profile.paid_in_capital,
                issued_shares=profile.issued_shares,
                metadata_source=profile.source,
                metadata_updated_at=now,
            ):
                changed += 1
        return changed

    def resolve_company_profiles(self, symbols: Iterable[str]) -> dict[str, object]:
        requested = {str(symbol).strip().upper() for symbol in symbols}
        output: dict[str, object] = {}
        for symbol in requested:
            asset = self._repository.get(symbol)
            if asset is not None and asset.company_name:
                output[symbol] = asset
        missing = requested - output.keys()
        if missing and self._company_provider is not None:
            if not self._company_profile_cache:
                try:
                    profiles = self._company_provider.fetch_company_profiles()
                    self._company_profile_cache = {
                        profile.symbol: profile for profile in profiles
                    }
                except Exception:
                    return output
            output.update({
                symbol: self._company_profile_cache[symbol]
                for symbol in missing
                if symbol in self._company_profile_cache
            })
        return output

    def update_taiwan_market_ranks(self, rows: object) -> int:
        now = datetime.now(UTC)
        changed = 0
        for rank, item in enumerate(rows, start=1):
            if self._repository.update_metadata(
                item.symbol,
                market_value_twd=int(item.market_value_twd),
                market_rank=rank,
                metadata_source=item.source,
                metadata_updated_at=now,
            ):
                changed += 1
        return changed


@dataclass(frozen=True, slots=True)
class DailyPipelineResult:
    run_id: int
    market: str
    status: str
    asset_count: int
    succeeded: int
    failed: int
    received: int
    inserted: int
    failures: dict[str, str]
    started_at: str
    completed_at: str


class DailyMarketDataPipeline:
    """Auditable incremental market-data pipeline for one market."""

    def __init__(
        self,
        universe_repository: ResearchUniverseRepository,
        market_bar_repository: MarketBarRepository,
        ingestion_service: MarketDataIngestionService,
        run_repository: SchedulerJobRunRepository,
    ) -> None:
        self._universe_repository = universe_repository
        self._market_bar_repository = market_bar_repository
        self._ingestion_service = ingestion_service
        self._run_repository = run_repository

    def run(
        self,
        market: str,
        now: datetime | None = None,
        full_refresh: bool = False,
        symbols: list[str] | None = None,
    ) -> DailyPipelineResult:
        normalized_market = market.upper()
        if normalized_market not in {"US", "TW"}:
            raise ValueError("market must be US or TW")
        started = now or datetime.now(UTC)
        if started.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        selected_symbols = {item.strip().upper() for item in symbols} if symbols else None
        assets = [
            asset
            for asset in self._universe_repository.list_active(normalized_market)
            if selected_symbols is None or asset.symbol in selected_symbols
        ]
        run_id = self._run_repository.start("daily_market_data", normalized_market, started)
        succeeded = 0
        received = 0
        inserted = 0
        failures: dict[str, str] = {}

        for asset in assets:
            try:
                latest = self._market_bar_repository.latest_event_time(
                    asset.symbol, "1d", "yahoo_finance"
                )
                if latest is not None and normalized_market == "TW":
                    if latest.tzinfo is None:
                        latest = latest.replace(tzinfo=UTC)
                    taipei = ZoneInfo("Asia/Taipei")
                    if (
                        latest.astimezone(taipei).date()
                        >= started.astimezone(taipei).date()
                    ):
                        succeeded += 1
                        continue
                if full_refresh or latest is None:
                    start = datetime.combine(asset.data_start, time.min, tzinfo=UTC)
                else:
                    if latest.tzinfo is None:
                        latest = latest.replace(tzinfo=UTC)
                    start = latest - timedelta(days=7)
                result = self._ingestion_service.ingest_daily(
                    symbol=asset.symbol,
                    market=asset.market,
                    start=start,
                    end=started + timedelta(days=1),
                )
                succeeded += 1
                received += result.received
                inserted += result.inserted
            except Exception as exc:  # Each asset is isolated; the run remains auditable.
                logger.exception("Daily ingestion failed for %s", asset.symbol)
                failures[asset.symbol] = str(exc)

        completed = datetime.now(UTC)
        failed = len(failures)
        if failed == 0:
            status = JobRunStatus.SUCCEEDED
        elif succeeded == 0:
            status = JobRunStatus.FAILED
        else:
            status = JobRunStatus.PARTIAL
        result = DailyPipelineResult(
            run_id=run_id,
            market=normalized_market,
            status=status.value,
            asset_count=len(assets),
            succeeded=succeeded,
            failed=failed,
            received=received,
            inserted=inserted,
            failures=failures,
            started_at=started.isoformat(),
            completed_at=completed.isoformat(),
        )
        self._run_repository.finish(
            run_id=run_id,
            status=status.value,
            completed_at=completed,
            metrics_json=json.dumps(asdict(result), ensure_ascii=False),
            error="; ".join(f"{symbol}: {error}" for symbol, error in failures.items()) or None,
        )
        return result

    def list_recent_runs(self, limit: int = 20) -> list[SchedulerJobRun]:
        return self._run_repository.list_recent(limit)
