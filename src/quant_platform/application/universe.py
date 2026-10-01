from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from time import sleep as _sleep
from zoneinfo import ZoneInfo

from quant_platform.application.analytics import DEFAULT_UNIVERSE
from quant_platform.application.ports import (
    MarketBarRepository,
    ResearchUniverseRepository,
    SchedulerJobRunRepository,
)
from quant_platform.application.services import MarketBarValidator, MarketDataIngestionService
from quant_platform.domain.entities import JobRunStatus, ResearchAsset, SchedulerJobRun
from quant_platform.market_calendar import (
    MarketCalendarStore,
    TradingCalendar,
    default_market_calendar,
)

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
    "00713.TW": ("TW", "ETF", "Dividend Low Volatility", "0050.TW"),
    "00919.TW": ("TW", "ETF", "Dividend", "0050.TW"),
    "00679B.TWO": ("TW", "ETF", "Treasury Bond", "0050.TW"),
    "00687B.TWO": ("TW", "ETF", "Treasury Bond", "0050.TW"),
}

DEFAULT_ASSET_NAMES = {
    "0050.TW": "元大台灣50",
    "0056.TW": "元大高股息",
    "006208.TW": "富邦台50",
    "00878.TW": "國泰永續高股息",
    "00713.TW": "元大台灣高息低波",
    "00919.TW": "群益台灣精選高息",
    "00679B.TWO": "元大美債20年",
    "00687B.TWO": "國泰20年美債",
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
            existing = self._repository.get(symbol)
            if existing is not None:
                if symbol in DEFAULT_ASSET_NAMES:
                    default_name = DEFAULT_ASSET_NAMES[symbol]
                    if (
                        existing.company_name != default_name
                        or existing.company_abbreviation != default_name
                        or existing.metadata_source != "built-in-index-etf-identity"
                    ):
                        self._repository.update_metadata(
                            symbol,
                            company_name=default_name,
                            company_abbreviation=default_name,
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
    data_date: str | None = None
    expected_date: str | None = None
    fresh: bool = True


class DailyMarketDataPipeline:
    """Auditable incremental market-data pipeline for one market."""

    def __init__(
        self,
        universe_repository: ResearchUniverseRepository,
        market_bar_repository: MarketBarRepository,
        ingestion_service: MarketDataIngestionService,
        run_repository: SchedulerJobRunRepository,
        official_tw_provider: object | None = None,
        calendar_store: MarketCalendarStore | None = None,
        official_wait: timedelta = timedelta(0),
        official_poll_seconds: float = 30.0,
        sleep=None,
        clock=None,
    ) -> None:
        self._universe_repository = universe_repository
        self._market_bar_repository = market_bar_repository
        self._ingestion_service = ingestion_service
        self._run_repository = run_repository
        self._official_tw_provider = official_tw_provider
        self._calendar_store = calendar_store
        self._validator = MarketBarValidator()
        # How long a Taiwan run waits for today's official close before asking
        # Yahoo symbol by symbol (S1-W05: the official table appeared at 13:51
        # on 2026-10-01; one Yahoo request per symbol took over half an hour).
        self._official_wait = official_wait
        self._official_poll_seconds = official_poll_seconds
        self._sleep = sleep or _sleep
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _market_clock(market: str) -> tuple[ZoneInfo, time]:
        if market.upper() == "TW":
            return ZoneInfo("Asia/Taipei"), time(13, 45)
        return ZoneInfo("America/New_York"), time(16, 15)

    def _calendar(self, market: str) -> TradingCalendar:
        return (self._calendar_store or default_market_calendar()).calendar(market)

    @classmethod
    def expected_session_date(
        cls, market: str, now: datetime, calendar: TradingCalendar | None = None
    ) -> date:
        """Return the latest exchange session whose daily candle should be available.

        The cutoff matches the Yahoo adapter's close-plus-15-minute
        ``available_time``. Weekends and listed exchange holidays roll back to
        the previous session, so a holiday is not reported as missing data.
        """
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        zone, available_after = cls._market_clock(market)
        local_now = now.astimezone(zone)
        candidate = local_now.date()
        if local_now.time() < available_after:
            candidate -= timedelta(days=1)
        sessions = calendar or default_market_calendar().calendar(market)
        return sessions.previous_trading_day(candidate)

    def latest_market_date(
        self, market: str, now: datetime | None = None
    ) -> date | None:
        normalized_market = market.upper()
        benchmark = "0050.TW" if normalized_market == "TW" else "SPY"
        latest = max(
            (
                value
                for source in ("yahoo_finance", "twse_tpex_official")
                if (
                    value := self._market_bar_repository.latest_event_time(
                        benchmark, "1d", source
                    )
                ) is not None
            ),
            default=None,
        )
        if latest is None:
            latest = max(
                (
                    value
                    for asset in self._universe_repository.list_active(
                        normalized_market
                    )
                    if (
                        value := self._market_bar_repository.latest_event_time(
                            asset.symbol, "1d", "yahoo_finance"
                        )
                    ) is not None
                ),
                default=None,
            )
        if latest is None:
            return None
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=UTC)
        zone, _ = self._market_clock(normalized_market)
        return latest.astimezone(zone).date()

    def _official_close_stored(self, symbols: list[str], expected_date: date) -> bool:
        """Whether the official table for ``expected_date`` is already in the database
        (a rerun must not wait for it again). Indices are not in that table."""
        listed = [symbol for symbol in symbols if symbol.endswith((".TW", ".TWO"))][:20]
        for symbol in sorted(listed, key=lambda item: item != "0050.TW"):
            latest = self._market_bar_repository.latest_event_time(symbol, "1d", "twse_tpex_official")
            if latest is None:
                continue
            if latest.tzinfo is None:
                latest = latest.replace(tzinfo=UTC)
            return latest.astimezone(ZoneInfo("Asia/Taipei")).date() >= expected_date
        return False

    def _wait_for_official_close(self, symbols: list[str], expected_date: date, started: datetime) -> list:
        """Poll the official close table until it has ``expected_date`` or the wait ends."""
        deadline = started + self._official_wait
        while self._clock() + timedelta(seconds=self._official_poll_seconds) <= deadline:
            self._sleep(self._official_poll_seconds)
            fetch_range = getattr(self._official_tw_provider, "fetch_range", None)
            try:
                if callable(fetch_range):  # no cache, unlike fetch_snapshot
                    bars = fetch_range(symbols, expected_date, expected_date, self._clock())
                else:
                    bars = self._official_tw_provider.fetch_snapshot(symbols, self._clock())
                bars = [
                    item for item in bars
                    if item.event_time.astimezone(ZoneInfo("Asia/Taipei")).date() == expected_date
                ]
                self._validator.validate(bars)
            except Exception:  # noqa: BLE001 -- keep polling; Yahoo is the fallback
                logger.exception("Official TW close poll failed")
                continue
            if bars:
                logger.info("Official TW close for %s appeared after %s", expected_date, self._clock() - started)
                return bars
        logger.warning("Official TW close for %s not published within %s; using Yahoo", expected_date,
                       self._official_wait)
        return []

    def is_fresh(self, market: str, now: datetime | None = None) -> bool:
        checked_at = now or datetime.now(UTC)
        return self.latest_market_date(market, checked_at) == self.expected_session_date(
            market, checked_at, self._calendar(market)
        )

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
        expected_date = self.expected_session_date(
            normalized_market, started, self._calendar(normalized_market)
        )
        latest_before = self.latest_market_date(normalized_market, started)
        official_target_symbols: set[str] = set()

        if normalized_market == "TW" and self._official_tw_provider is not None:
            try:
                active_symbols = [asset.symbol for asset in assets]
                fetch_range = getattr(self._official_tw_provider, "fetch_range", None)
                if latest_before is not None and callable(fetch_range):
                    official_start = max(
                        latest_before + timedelta(days=1),
                        expected_date - timedelta(days=31),
                    )
                    official_bars = fetch_range(
                        active_symbols, official_start, expected_date, started
                    )
                elif latest_before != expected_date:
                    official_bars = self._official_tw_provider.fetch_snapshot(
                        active_symbols, started
                    )
                else:
                    official_bars = []
                self._validator.validate(official_bars)
                received += len(official_bars)
                inserted += self._market_bar_repository.add_missing(official_bars)
                official_target_symbols = {
                    item.symbol
                    for item in official_bars
                    if item.event_time.astimezone(ZoneInfo("Asia/Taipei")).date()
                    == expected_date
                }
            except Exception as exc:
                logger.exception("Official TW close snapshot ingestion failed")
                failures["__official_tw_snapshot__"] = str(exc)
            if (
                not official_target_symbols
                and "__official_tw_snapshot__" not in failures
                and self._official_wait > timedelta(0)
                and expected_date == started.astimezone(ZoneInfo("Asia/Taipei")).date()
                and not self._official_close_stored(active_symbols, expected_date)
            ):
                bars = self._wait_for_official_close(active_symbols, expected_date, started)
                received += len(bars)
                inserted += self._market_bar_repository.add_missing(bars)
                official_target_symbols = {
                    item.symbol
                    for item in bars
                    if item.event_time.astimezone(ZoneInfo("Asia/Taipei")).date() == expected_date
                }

        for asset in assets:
            try:
                if not full_refresh and normalized_market == "TW":
                    official_latest = self._market_bar_repository.latest_event_time(
                        asset.symbol, "1d", "twse_tpex_official"
                    )
                    if official_latest is not None:
                        if official_latest.tzinfo is None:
                            official_latest = official_latest.replace(tzinfo=UTC)
                        if (
                            official_latest.astimezone(ZoneInfo("Asia/Taipei")).date()
                            >= expected_date
                        ):
                            official_target_symbols.add(asset.symbol)
                    if asset.symbol in official_target_symbols:
                        succeeded += 1
                        continue
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
        data_date = self.latest_market_date(normalized_market, started)
        fresh = data_date == expected_date
        if not fresh:
            failures["__market_freshness__"] = (
                f"{normalized_market} 最新行情日 {data_date or '尚無'}，"
                f"預期 {expected_date}；禁止用舊行情產生新決策"
            )
            if normalized_market == "TW":
                failures["__market_freshness__"] += (
                    "。若當日為颱風等臨時休市，執行 "
                    "`python -m quant_platform.market_calendar add-closure "
                    f"{expected_date} 颱風休市` 後重跑"
                )
        failed = len(failures)
        if not fresh:
            status = JobRunStatus.FAILED
        elif failed == 0:
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
            data_date=data_date.isoformat() if data_date else None,
            expected_date=expected_date.isoformat(),
            fresh=fresh,
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
