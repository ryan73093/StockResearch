from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime

from quant_platform.application.ports import (
    MarketBarRepository,
    ResearchUniverseRepository,
)

logger = logging.getLogger(__name__)


# Liquid, cross-industry names are attempted first. Membership history still
# decides whether each code is currently eligible; this list is not treated as
# an index-constituent claim.
TW_PRIORITY_CODES = (
    "1101", "1216", "1301", "1303", "1402", "2002", "2105", "2207",
    "2301", "2303", "2308", "2317", "2327", "2330", "2345", "2357",
    "2379", "2382", "2395", "2408", "2412", "2454", "2603", "2609",
    "2615", "2801", "2880", "2881", "2882", "2883", "2884", "2885",
    "2886", "2887", "2890", "2891", "2892", "3008", "3034", "3045",
    "3231", "3711", "4904", "5871", "5876", "5880", "6505", "6669",
)


@dataclass(frozen=True, slots=True)
class UniverseExpansionOverview:
    eligible_common_stocks: int
    registered_assets: int
    data_ready_assets: int
    short_history_assets: int
    new_listing_assets: int
    decision_covered_assets: int
    remaining_assets: int
    next_symbols: tuple[str, ...]
    daily_batch_size: int
    target_ready_assets: int
    status: str
    selection_basis: str


@dataclass(frozen=True, slots=True)
class UniverseExpansionResult:
    attempted: int
    data_ready: int
    kept_active: int
    deactivated: int
    received_bars: int
    inserted_bars: int
    symbols: tuple[str, ...]
    failures: tuple[str, ...]
    skipped_reason: str | None = None


class UniverseExpansionService:
    """Stages current TW common stocks into research without a 2,000-call burst."""

    def __init__(
        self,
        universe_service: object,
        universe_repository: ResearchUniverseRepository,
        universe_history_service: object,
        market_data_pipeline: object,
        taiwan_data_pipeline: object,
        market_bars: MarketBarRepository,
        decision_repository: object,
        market_ranking_provider: object | None = None,
        daily_batch_size: int = 20,
        target_ready_assets: int = 500,
        run_repository: object | None = None,
    ) -> None:
        self._service = universe_service
        self._universe = universe_repository
        self._history = universe_history_service
        self._market_data = market_data_pipeline
        self._taiwan_data = taiwan_data_pipeline
        self._bars = market_bars
        self._decisions = decision_repository
        self._market_ranking = market_ranking_provider
        self._market_rank_cache: dict[str, int] = {}
        self._selection_basis = "執行擴充前將讀取官方市值排行"
        self._batch_size = daily_batch_size
        self._target = target_ready_assets
        self._runs = run_repository
        self._overview_cache: tuple[float, UniverseExpansionOverview] | None = None
        self._membership_refresh_attempted_at = 0.0
        self._overview_lock = threading.Lock()
        self._batch_lock = threading.Lock()

    def _eligible(self, effective_date: date | None = None) -> tuple[object, ...]:
        rows = self._history.members_on(effective_date or date.today(), "TW")
        return tuple(
            item for item in rows
            if re.fullmatch(r"\d{4}\.(?:TW|TWO)", item.symbol)
        )

    @staticmethod
    def _fallback_priority(symbol: str) -> tuple[int, int, str]:
        code = symbol.split(".", 1)[0]
        try:
            position = TW_PRIORITY_CODES.index(code)
            return 0, position, symbol
        except ValueError:
            digest = hashlib.sha256(symbol.encode("ascii")).hexdigest()
            return 1, int(digest[:12], 16), symbol

    def _ranking(self, refresh: bool = False) -> tuple[dict[str, int], str]:
        if self._market_rank_cache and not refresh:
            return self._market_rank_cache, self._selection_basis
        if not refresh:
            # A page view must never wait on TWSE/TPEx network calls. The daily
            # expansion worker refreshes the official ranking before selecting
            # its batch; until then the deterministic cross-industry order is
            # enough for a read-only progress screen.
            return {}, "等待每日排程更新官方市值排行；目前顯示備援順序"
        if self._market_ranking is None:
            return {}, "跨產業大型股優先清單"
        try:
            rows = self._market_ranking.fetch(force=refresh)
        except Exception:
            return {}, "官方市值暫時不可用；使用跨產業大型股備援順序"
        updater = getattr(self._service, "update_taiwan_market_ranks", None)
        if callable(updater):
            updater(rows)
        self._market_rank_cache = {
            item.symbol: index for index, item in enumerate(rows)
        }
        self._selection_basis = "證交所發行股數×收盤價＋櫃買中心官方市值"
        return self._market_rank_cache, self._selection_basis

    def _ready(self, symbol: str, now: datetime | None = None) -> bool:
        return len(self._bars.list_bars(symbol, as_of=now)) >= 252

    def refresh_market_metadata(self) -> int:
        ranking, _ = self._ranking(refresh=True)
        self.invalidate_overview_cache()
        return len(ranking)

    def invalidate_overview_cache(self) -> None:
        with self._overview_lock:
            self._overview_cache = None

    def _refresh_eligible_universe_if_needed(self) -> None:
        """Populate the current TWSE/TPEx candidate pool before expansion.

        The original worker only consumed persisted lifecycle rows.  A fresh
        installation therefore saw only the small built-in research seed and
        incorrectly concluded that there was nothing left to expand.
        """
        eligible_count = len(self._eligible())
        if eligible_count >= self._target:
            # Another worker or a manual sync may have populated memberships
            # after this process cached an empty overview.
            with self._overview_lock:
                cached_count = (
                    self._overview_cache[1].eligible_common_stocks
                    if self._overview_cache is not None
                    else eligible_count
                )
                if cached_count != eligible_count:
                    self._overview_cache = None
            return
        now = time.monotonic()
        if now - self._membership_refresh_attempted_at < 3600:
            return
        self._membership_refresh_attempted_at = now
        sync = getattr(self._history, "sync_taiwan", None)
        if not callable(sync):
            return
        try:
            sync()
        except Exception as exc:
            logger.warning("Taiwan universe membership refresh failed: %s", exc)
            return
        self.invalidate_overview_cache()

    def _history_tier(self, symbol: str, now: datetime | None = None) -> str:
        count = len(self._bars.list_bars(symbol, as_of=now))
        if count >= 252:
            return "SEASONED"
        if count >= 60:
            return "SHORT_HISTORY"
        if count >= 20:
            return "NEW_LISTING"
        return "OBSERVE"

    def _pending(self, refresh_ranking: bool = False) -> tuple[object, ...]:
        market_rank, _ = self._ranking(refresh=refresh_ranking)
        output = []
        for membership in self._eligible():
            asset = self._universe.get(membership.symbol)
            if (
                asset is not None
                and asset.active
                and self._history_tier(asset.symbol) != "OBSERVE"
            ):
                continue
            output.append(membership)
        return tuple(sorted(
            output,
            key=lambda item: (
                0, market_rank[item.symbol], item.symbol
            ) if item.symbol in market_rank else (
                1, *self._fallback_priority(item.symbol)
            ),
        ))

    def _compute_overview(self) -> UniverseExpansionOverview:
        eligible = self._eligible()
        active = tuple(
            item for item in self._universe.list_active("TW")
            if item.asset_type.upper() in {"EQUITY", "ETF"}
        )
        active_by_symbol = {item.symbol: item for item in active}
        counter = getattr(self._bars, "count_daily_sessions", None)
        counts = counter("TW") if callable(counter) else {
            item.symbol: len(self._bars.list_bars(item.symbol)) for item in active
        }

        def tier(symbol: str) -> str:
            count = counts.get(symbol, 0)
            if count >= 252:
                return "SEASONED"
            if count >= 60:
                return "SHORT_HISTORY"
            if count >= 20:
                return "NEW_LISTING"
            return "OBSERVE"

        tiers = [tier(item.symbol) for item in active]
        ready = tiers.count("SEASONED")
        covered = len({item.symbol for item in self._decisions.list_latest("TW")})
        market_rank, selection_basis = self._ranking()
        pending = [
            membership
            for membership in eligible
            if not (
                membership.symbol in active_by_symbol
                and tier(membership.symbol) != "OBSERVE"
            )
        ]
        pending.sort(
            key=lambda item: (
                (0, market_rank[item.symbol], item.symbol)
                if item.symbol in market_rank
                else (1, *self._fallback_priority(item.symbol))
            )
        )
        if ready >= self._target:
            status = "第一階段股票池目標已完成"
        elif pending:
            status = "分批補資料中"
        else:
            status = "沒有可擴充標的"
        return UniverseExpansionOverview(
            eligible_common_stocks=len({item.symbol for item in eligible}),
            registered_assets=len(active),
            data_ready_assets=ready,
            short_history_assets=tiers.count("SHORT_HISTORY"),
            new_listing_assets=tiers.count("NEW_LISTING"),
            decision_covered_assets=covered,
            remaining_assets=len(pending),
            next_symbols=tuple(item.symbol for item in pending[:10]),
            daily_batch_size=self._batch_size,
            target_ready_assets=self._target,
            status=status,
            selection_basis=selection_basis,
        )

    def overview(self) -> UniverseExpansionOverview:
        with self._overview_lock:
            if (
                self._overview_cache is not None
                and time.monotonic() - self._overview_cache[0] < 300
            ):
                return self._overview_cache[1]
            value = self._compute_overview()
            self._overview_cache = (time.monotonic(), value)
            return value

    def run_batch(
        self,
        batch_size: int | None = None,
        now: datetime | None = None,
        fetch_auxiliary: bool = True,
    ) -> UniverseExpansionResult:
        # The daily workflow, interval backfill and manual button share this
        # service instance. Serialize them so two jobs cannot select and fetch
        # the same pending symbols at the same time.
        with self._batch_lock:
            return self._run_batch_serial(
                batch_size=batch_size,
                now=now,
                fetch_auxiliary=fetch_auxiliary,
            )

    def _run_batch_serial(
        self,
        batch_size: int | None = None,
        now: datetime | None = None,
        fetch_auxiliary: bool = True,
    ) -> UniverseExpansionResult:
        started_at = now or datetime.now(UTC)
        self._refresh_eligible_universe_if_needed()
        overview = self.overview()
        if overview.data_ready_assets >= self._target:
            return UniverseExpansionResult(
                0, 0, 0, 0, 0, 0, (), (),
                skipped_reason=f"已達 {self._target} 檔成熟樣本目標",
            )
        if overview.remaining_assets <= 0:
            return UniverseExpansionResult(
                0, 0, 0, 0, 0, 0, (), (),
                skipped_reason="目前沒有可加入的合格股票",
            )
        audit_id = (
            self._runs.start("tw_universe_expansion", "TW", started_at)
            if self._runs is not None
            else None
        )
        try:
            result = self._run_batch_impl(
                batch_size=batch_size,
                now=started_at,
                fetch_auxiliary=fetch_auxiliary,
            )
        except Exception as exc:
            if audit_id is not None:
                self._runs.finish(
                    audit_id,
                    "failed",
                    datetime.now(UTC),
                    json.dumps(
                        {
                            "attempted": 0,
                            "inserted_bars": 0,
                            "failures": [str(exc)],
                        },
                        ensure_ascii=False,
                    ),
                    str(exc),
                )
            raise
        if audit_id is not None:
            status = (
                "succeeded"
                if result.attempted == 0 or not result.failures
                else "partial"
                if result.kept_active > 0 or result.inserted_bars > 0
                else "failed"
            )
            self._runs.finish(
                audit_id,
                status,
                datetime.now(UTC),
                json.dumps(
                    {
                        "attempted": result.attempted,
                        "data_ready": result.data_ready,
                        "kept_active": result.kept_active,
                        "deactivated": result.deactivated,
                        "received_bars": result.received_bars,
                        "inserted_bars": result.inserted_bars,
                        "symbols": result.symbols,
                        "failures": result.failures,
                        "skipped_reason": result.skipped_reason,
                    },
                    ensure_ascii=False,
                ),
                "\n".join(result.failures[:20]) if status == "failed" else None,
            )
        return result

    def _run_batch_impl(
        self,
        batch_size: int | None = None,
        now: datetime | None = None,
        fetch_auxiliary: bool = True,
    ) -> UniverseExpansionResult:
        started_at = now or datetime.now(UTC)
        size = max(1, min(batch_size or self._batch_size, 20))
        overview = self.overview()
        if overview.data_ready_assets >= self._target:
            return UniverseExpansionResult(
                0, 0, 0, 0, 0, 0, (), (),
                skipped_reason=f"已達 {self._target} 檔成熟樣本目標",
            )
        selected = self._pending(refresh_ranking=True)[:size]
        if not selected:
            return UniverseExpansionResult(
                0, 0, 0, 0, 0, 0, (), (),
                skipped_reason="目前沒有可加入的合格股票",
            )

        symbols = tuple(item.symbol for item in selected)
        for membership in selected:
            self._service.add_asset(
                membership.symbol,
                "TW",
                asset_type="EQUITY",
                benchmark_symbol="0050.TW",
                data_start=max(membership.valid_from, date(2020, 1, 1)),
            )
        metadata_sync = getattr(self._service, "sync_taiwan_company_metadata", None)
        if callable(metadata_sync):
            try:
                metadata_sync()
            except Exception:
                # Company identity is shown as pending until the next scheduled
                # metadata refresh; it must not cancel price-history ingestion.
                pass

        market_result = self._market_data.run(
            "TW", now=started_at, full_refresh=True, symbols=list(symbols)
        )
        # Taiwan datasets are useful but provider-plan failures must not erase
        # valid price history. The full daily research gate will decide readiness.
        auxiliary_failure: str | None = None
        if fetch_auxiliary:
            try:
                self._taiwan_data.run(
                    "TW", now=started_at, full_refresh=True, symbols=list(symbols)
                )
            except Exception as exc:
                auxiliary_failure = f"台股基本面／籌碼補資料：{exc}"

        ready = kept = deactivated = 0
        failures = [
            f"{symbol}：{message}"
            for symbol, message in market_result.failures.items()
        ]
        if auxiliary_failure:
            failures.append(auxiliary_failure)
        for symbol in symbols:
            tier = self._history_tier(symbol, started_at)
            if tier == "SEASONED":
                ready += 1
                kept += 1
            elif tier in {"SHORT_HISTORY", "NEW_LISTING"}:
                kept += 1
                description = (
                    "60～251 根日線，保留在短歷史研究層"
                    if tier == "SHORT_HISTORY"
                    else "20～59 根日線，保留在新上市事件研究層"
                )
                failures.append(f"{symbol}：{description}，不得假造一年期特徵")
            else:
                self._service.set_active(symbol, False)
                deactivated += 1
                if symbol not in market_result.failures:
                    failures.append(f"{symbol}：日線少於 20 根或無行情，先列觀察")
        result = UniverseExpansionResult(
            attempted=len(symbols), data_ready=ready, kept_active=kept,
            deactivated=deactivated, received_bars=market_result.received,
            inserted_bars=market_result.inserted, symbols=symbols,
            failures=tuple(failures),
        )
        self.invalidate_overview_cache()
        return result
