from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from quant_platform.application.ports import ResearchUniverseRepository
from quant_platform.data_sources.finmind import FinMindProvider
from quant_platform.data_sources.twse import TwseCompanyProvider
from quant_platform.domain.entities import UniverseMembership


@dataclass(frozen=True, slots=True)
class UniverseHistoryAudit:
    query_date: date
    interval_count: int
    symbol_count: int
    active_count: int
    delisted_count: int
    exact_start_count: int
    coverage_start: date | None
    survivorship_safe: bool
    quality_notes: tuple[str, ...]
    memberships: tuple[UniverseMembership, ...]


@dataclass(frozen=True, slots=True)
class UniverseSyncResult:
    current_received: int
    delisted_received: int
    official_listing_dates: int
    changed: int


class UniverseHistoryService:
    """Builds and audits point-in-time universe intervals without overstating completeness."""

    def __init__(
        self,
        repository: ResearchUniverseRepository,
        finmind: FinMindProvider,
        twse: TwseCompanyProvider,
    ) -> None:
        self._repository = repository
        self._finmind = finmind
        self._twse = twse

    def ensure_seed_memberships(self) -> int:
        existing = {(item.symbol, item.source) for item in self._repository.list_memberships()}
        now = datetime.now(UTC)
        seeds = [
            UniverseMembership(
                id=None, symbol=asset.symbol, market=asset.market,
                valid_from=asset.data_start, valid_to=None,
                start_is_exact=False, end_is_exact=False,
                source="research-config", reason="研究設定起日，不等同正式上市日",
                recorded_at=now,
            )
            for asset in self._repository.list_all()
            if (asset.symbol, "research-config") not in existing
        ]
        return self._repository.upsert_memberships(seeds)

    def sync_taiwan(self) -> UniverseSyncResult:
        current = self._finmind.fetch_dataset("TaiwanStockInfo")
        delisted = self._finmind.fetch_dataset("TaiwanStockDelisting")
        official_rows = self._twse.fetch_listed_companies()
        official_dates = {
            self._twse.company_code(row): self._twse.listing_date(row)
            for row in official_rows
            if self._twse.company_code(row)
        }
        asset_starts = {
            asset.symbol.split(".")[0]: asset.data_start
            for asset in self._repository.list_all() if asset.market == "TW"
        }
        delisted_dates = {
            str(row.get("stock_id") or "").strip(): self._parse_date(row.get("date"))
            for row in delisted
            if row.get("stock_id") and self._parse_date(row.get("date"))
        }
        now = datetime.now(UTC)
        values: list[UniverseMembership] = []
        for row in current:
            code = str(row.get("stock_id") or "").strip()
            if not code:
                continue
            market_type = str(row.get("type") or "twse").lower()
            suffix = ".TWO" if market_type == "tpex" else ".TW"
            listing_date = official_dates.get(code)
            inferred_start = asset_starts.get(code, date(2001, 1, 1))
            values.append(UniverseMembership(
                id=None, symbol=f"{code}{suffix}", market="TW",
                valid_from=listing_date or inferred_start,
                valid_to=delisted_dates.get(code),
                start_is_exact=listing_date is not None,
                end_is_exact=True,
                source="twse-finmind-lifecycle",
                reason="證交所上市日＋FinMind 現行股票清單／下市櫃日期",
                recorded_at=now,
            ))
        current_codes = {str(row.get("stock_id") or "").strip() for row in current}
        for row in delisted:
            code = str(row.get("stock_id") or "").strip()
            end = self._parse_date(row.get("date"))
            if not code or end is None or code in current_codes:
                continue
            values.append(UniverseMembership(
                id=None, symbol=f"{code}.TW", market="TW",
                valid_from=asset_starts.get(code, date(2001, 1, 1)), valid_to=end,
                start_is_exact=False, end_is_exact=True,
                source="finmind-delisting", reason="下市櫃日精確；上市起日仍待歷史名冊補齊",
                recorded_at=now,
            ))
        changed = self._repository.upsert_memberships(values)
        return UniverseSyncResult(
            current_received=len(current), delisted_received=len(delisted),
            official_listing_dates=sum(value is not None for value in official_dates.values()),
            changed=changed,
        )

    def audit(self, effective_date: date | None = None, market: str = "TW") -> UniverseHistoryAudit:
        query_date = effective_date or date.today()
        intervals = self._repository.list_memberships(market)
        selected = self.members_on(query_date, market)
        symbols = {item.symbol for item in intervals}
        exact_starts = {item.symbol for item in intervals if item.start_is_exact}
        delisted_symbols = {item.symbol for item in intervals if item.valid_to is not None and item.end_is_exact}
        notes: list[str] = []
        if not intervals:
            notes.append("尚未建立任何歷史股票池有效期間。")
        if len(exact_starts) < len(symbols):
            notes.append(f"仍有 {len(symbols)-len(exact_starts)} 檔缺少精確上市起日。")
        if not delisted_symbols:
            notes.append("尚未匯入下市櫃公司，無法排除存活者偏差。")
        notes.append("通過門檻前，跨股票選股與組合回測仍維持研究狀態。")
        safe = bool(intervals) and len(exact_starts) == len(symbols) and bool(delisted_symbols)
        return UniverseHistoryAudit(
            query_date=query_date, interval_count=len(intervals), symbol_count=len(symbols),
            active_count=len(selected), delisted_count=len(delisted_symbols),
            exact_start_count=len(exact_starts),
            coverage_start=min((item.valid_from for item in intervals), default=None),
            survivorship_safe=safe, quality_notes=tuple(notes),
            memberships=tuple(sorted(intervals, key=lambda item: (item.valid_from, item.symbol), reverse=True)[:200]),
        )

    def members_on(self, effective_date: date, market: str | None = None) -> list[UniverseMembership]:
        rows = self._repository.list_members_on(effective_date, market)
        selected: dict[str, UniverseMembership] = {}
        for row in rows:
            previous = selected.get(row.symbol)
            if previous is None or (row.start_is_exact, row.end_is_exact) > (
                previous.start_is_exact, previous.end_is_exact
            ):
                selected[row.symbol] = row
        return sorted(selected.values(), key=lambda item: item.symbol)

    @staticmethod
    def _parse_date(value: object) -> date | None:
        try:
            return date.fromisoformat(str(value)[:10])
        except (TypeError, ValueError):
            return None
