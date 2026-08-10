from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from quant_platform.application.point_in_time_data import (
    PointInTimeDataService,
    PointInTimeIngestionResult,
)
from quant_platform.application.ports import PointInTimeDataRepository


@dataclass(frozen=True, slots=True)
class EarningsCallPreview:
    symbol: str
    company_name: str
    market: str
    event_time: datetime
    available_time: datetime
    ingested_at: datetime
    subject: str
    location: str
    summary: str
    clause: str
    presentation_urls: tuple[str, ...]
    related_urls: tuple[str, ...]
    presentation_count: int
    related_url_count: int
    source_kind: str
    source_uri: str
    is_upcoming: bool


@dataclass(frozen=True, slots=True)
class EarningsCallOverview:
    event_count: int
    company_count: int
    upcoming_count: int
    presentation_count: int
    latest_available_time: datetime | None
    selected_symbol: str
    selected_scope: str
    events: tuple[EarningsCallPreview, ...]


class EarningsCallService:
    dataset_key = "tw_earnings_call"

    def __init__(
        self,
        point_in_time_service: PointInTimeDataService,
        repository: PointInTimeDataRepository,
    ) -> None:
        self._service = point_in_time_service
        self._repository = repository

    def refresh(
        self,
        symbol: str = "ALL",
        now: datetime | None = None,
    ) -> PointInTimeIngestionResult:
        current = self._aware(now or datetime.now(UTC))
        normalized = self._normalize_symbol(symbol) or "ALL"
        return self._service.ingest(
            self.dataset_key, normalized,
            current - timedelta(days=60), current + timedelta(days=365), now=current,
        )

    def overview(
        self,
        symbol: str | None = None,
        scope: str = "all",
        as_of: datetime | None = None,
        limit: int = 200,
    ) -> EarningsCallOverview:
        current = self._aware(as_of or datetime.now(UTC))
        normalized = self._normalize_symbol(symbol) if symbol else ""
        rows = self._repository.list_observations(
            self.dataset_key, entity_id=normalized or None, as_of=current, limit=None,
        )
        events: list[EarningsCallPreview] = []
        for row in rows:
            payload = json.loads(row.payload_json)
            is_upcoming = self._aware(row.event_time) >= current
            if scope == "upcoming" and not is_upcoming:
                continue
            if scope == "past" and is_upcoming:
                continue
            events.append(EarningsCallPreview(
                symbol=row.entity_id,
                company_name=str(payload.get("公司名稱", "")),
                market=str(payload.get("市場", "")),
                event_time=self._aware(row.event_time),
                available_time=self._aware(row.available_time),
                ingested_at=self._aware(row.ingested_at),
                subject=str(payload.get("主旨", "")),
                location=str(payload.get("地點", "")),
                summary=str(payload.get("擇要訊息", "")),
                clause=str(payload.get("符合條款", "")),
                presentation_urls=tuple(payload.get("簡報網址", []) or []),
                related_urls=tuple(payload.get("影音／相關網址", []) or []),
                presentation_count=int(payload.get("簡報文件數", 0) or 0),
                related_url_count=int(payload.get("影音／相關連結數", 0) or 0),
                source_kind=str(payload.get("來源類型", "")),
                source_uri=row.source_uri,
                is_upcoming=is_upcoming,
            ))
        events.sort(key=lambda item: item.event_time, reverse=scope != "upcoming")
        events = events[:max(1, min(limit, 1000))]
        return EarningsCallOverview(
            event_count=len(events),
            company_count=len({item.symbol for item in events}),
            upcoming_count=sum(item.is_upcoming for item in events),
            presentation_count=sum(item.presentation_count for item in events),
            latest_available_time=max((item.available_time for item in events), default=None),
            selected_symbol=normalized,
            selected_scope=scope if scope in {"all", "upcoming", "past"} else "all",
            events=tuple(events),
        )

    @staticmethod
    def _normalize_symbol(value: str | None) -> str:
        return (value or "").strip().upper().removesuffix(".TW").removesuffix(".TWO")

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
