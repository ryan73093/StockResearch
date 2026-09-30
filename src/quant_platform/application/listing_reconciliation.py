from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from quant_platform.application.ports import (
    MarketBarRepository,
    ResearchUniverseRepository,
    SchedulerJobRunRepository,
)
from quant_platform.domain.entities import MarketBar
from quant_platform.market_calendar import MarketCalendarStore, default_market_calendar

logger = logging.getLogger(__name__)

TAIPEI = ZoneInfo("Asia/Taipei")


class CompanyRosterProvider(Protocol):
    def fetch_company_profiles(self) -> tuple[object, ...]: ...


@dataclass(frozen=True, slots=True)
class ListingReconciliationResult:
    checked: int
    official_count: int
    deactivated: tuple[str, ...] = ()
    reasons: dict[str, str] = field(default_factory=dict)
    skipped_reason: str | None = None


class TaiwanListingReconciliationService:
    """Retire active Taiwan equities that left the official TWSE/TPEx rosters.

    A security is retired only with two independent signals: it is absent
    from both current company rosters, and it has not traded (volume > 0) for
    ``stale_sessions`` exchange sessions. History is kept; every open universe
    interval is closed at the last traded day so expansion cannot re-add it.
    """

    def __init__(
        self,
        universe: ResearchUniverseRepository,
        bars: MarketBarRepository,
        roster: CompanyRosterProvider,
        runs: SchedulerJobRunRepository | None = None,
        calendar_store: MarketCalendarStore | None = None,
        minimum_roster_size: int = 1500,
        maximum_retirements: int = 10,
        stale_sessions: int = 3,
    ) -> None:
        self._universe = universe
        self._bars = bars
        self._roster = roster
        self._runs = runs
        self._calendar_store = calendar_store
        self._minimum_roster_size = minimum_roster_size
        self._maximum_retirements = maximum_retirements
        self._stale_sessions = stale_sessions

    def _last_traded_day(self, symbol: str, now: datetime) -> date | None:
        recent = getattr(self._bars, "list_recent_bars", None)
        rows: list[MarketBar] = (
            recent(symbol, now - timedelta(days=400), now)
            if callable(recent)
            else self._bars.list_bars(symbol, interval="1d", as_of=now)
        )
        traded = [bar.event_time for bar in rows if bar.volume > 0]
        if not traded:
            return None
        latest = max(traded)
        latest = latest if latest.tzinfo else latest.replace(tzinfo=UTC)
        return latest.astimezone(TAIPEI).date()

    def run(self, now: datetime | None = None) -> ListingReconciliationResult:
        checked_at = (now or datetime.now(UTC)).astimezone(UTC)
        run_id = (
            self._runs.start("tw_listing_reconciliation", "TW", checked_at)
            if self._runs else None
        )
        try:
            result = self._reconcile(checked_at)
        except Exception as exc:
            if run_id is not None:
                self._runs.finish(run_id, "failed", datetime.now(UTC), "{}", str(exc))
            raise
        if run_id is not None:
            self._runs.finish(
                run_id,
                "succeeded",
                datetime.now(UTC),
                json.dumps(asdict(result), ensure_ascii=False),
                result.skipped_reason,
            )
        return result

    def _reconcile(self, now: datetime) -> ListingReconciliationResult:
        official = {
            str(getattr(item, "symbol", "")).upper()
            for item in self._roster.fetch_company_profiles()
        }
        official.discard("")
        equities = [
            asset for asset in self._universe.list_active("TW")
            if asset.asset_type.upper() == "EQUITY"
        ]
        if len(official) < self._minimum_roster_size:
            return ListingReconciliationResult(
                checked=len(equities), official_count=len(official),
                skipped_reason=(
                    f"官方名冊只有 {len(official)} 家，低於 {self._minimum_roster_size} 家，"
                    "視為下載不完整，本次不停用任何標的"
                ),
            )
        calendar = (self._calendar_store or default_market_calendar()).calendar("TW")
        today = now.astimezone(TAIPEI).date()
        latest_session = calendar.previous_trading_day(today)
        candidates: dict[str, tuple[date | None, str]] = {}
        for asset in equities:
            if asset.symbol.upper() in official:
                continue
            last_traded = self._last_traded_day(asset.symbol, now)
            idle = (
                calendar.sessions_after(last_traded, latest_session)
                if last_traded is not None else None
            )
            if idle is not None and idle < self._stale_sessions:
                continue
            candidates[asset.symbol] = (
                last_traded,
                "不在證交所／櫃買現行公司名冊，且"
                + (f"已 {idle} 個交易日無成交（最後成交 {last_traded}）"
                   if last_traded is not None else "查無任何成交紀錄"),
            )
        if len(candidates) > self._maximum_retirements:
            return ListingReconciliationResult(
                checked=len(equities), official_count=len(official),
                reasons={symbol: reason for symbol, (_, reason) in candidates.items()},
                skipped_reason=(
                    f"{len(candidates)} 檔同時符合下市條件，超過單次上限 "
                    f"{self._maximum_retirements} 檔；請人工確認名冊來源後再處理"
                ),
            )
        memberships = self._universe.list_memberships("TW")
        recorded_at = datetime.now(UTC)
        for symbol, (last_traded, reason) in candidates.items():
            end = last_traded or latest_session
            closed = [
                replace(
                    item,
                    valid_to=max(end, item.valid_from),
                    end_is_exact=False,
                    reason=f"{item.reason}；{reason}",
                    recorded_at=recorded_at,
                )
                for item in memberships
                if item.symbol.upper() == symbol.upper() and item.valid_to is None
            ]
            if closed:
                self._universe.upsert_memberships(closed)
            self._universe.set_active(symbol, False)
            logger.warning("Retired %s from the active universe: %s", symbol, reason)
        return ListingReconciliationResult(
            checked=len(equities),
            official_count=len(official),
            deactivated=tuple(sorted(candidates)),
            reasons={symbol: reason for symbol, (_, reason) in candidates.items()},
        )
