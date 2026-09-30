from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from quant_platform.application.ports import PointInTimeDataRepository
from quant_platform.domain.entities import PointInTimeObservation

DATASET_KEY = "google_trends"
SOURCE = "google_trends_csv"
_HEADER_ALIASES = {
    "day": "day",
    "date": "day",
    "week": "week",
    "month": "month",
    "year": "year",
    "日": "day",
    "日期": "day",
    "週": "week",
    "周": "week",
    "月": "month",
    "年": "year",
}
_PARTIAL_HEADERS = {"ispartial", "部分資料", "資料不完整"}
_TRUE_VALUES = {"1", "true", "yes", "y"}


@dataclass(frozen=True, slots=True)
class GoogleTrendsImportResult:
    series_count: int
    received: int
    inserted: int
    partial_count: int
    downloaded_at: datetime


@dataclass(frozen=True, slots=True)
class GoogleTrendsObservationPreview:
    keyword: str
    event_time: datetime
    available_time: datetime
    interest: int | float
    interest_label: str
    geography: str
    granularity: str
    query_context: str
    is_partial: bool
    source_uri: str


@dataclass(frozen=True, slots=True)
class GoogleTrendsOverview:
    keyword: str
    as_of: datetime | None
    observations: tuple[GoogleTrendsObservationPreview, ...]
    keyword_count: int
    observation_count: int
    revision_count: int
    latest_available_time: datetime | None
    official_api_status: str


class GoogleTrendsCsvImportService:
    """Import an official Google Trends UI export without inventing history.

    Google Trends website values are normalized per request and can change on a
    later export. Every import is therefore an immutable source revision whose
    availability begins at the actual download/import timestamp.
    """

    def __init__(self, repository: PointInTimeDataRepository) -> None:
        self._repository = repository

    def overview(
        self,
        keyword: str = "",
        *,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> GoogleTrendsOverview:
        normalized_keyword = keyword.strip().upper()
        selected_as_of = self._aware(as_of) if as_of else None
        rows = self._repository.list_observations(
            DATASET_KEY,
            entity_id=normalized_keyword or None,
            as_of=selected_as_of,
            limit=max(1, min(limit, 1000)),
        )
        previews = []
        for row in rows:
            payload = json.loads(row.payload_json)
            previews.append(
                GoogleTrendsObservationPreview(
                    keyword=row.entity_id,
                    event_time=row.event_time,
                    available_time=row.available_time,
                    interest=payload["interest"],
                    interest_label=str(payload["interest_label"]),
                    geography=str(payload.get("geography") or "GLOBAL"),
                    granularity=str(payload["granularity"]),
                    query_context="／".join(
                        f"{key}: {value}" for key, value in payload.get("query_context", {}).items()
                    )
                    or "來源未標示",
                    is_partial=bool(payload.get("is_partial", False)),
                    source_uri=row.source_uri,
                )
            )
        coverage = [
            item
            for item in self._repository.list_coverage()
            if item.dataset_key == DATASET_KEY
            and (not normalized_keyword or item.entity_id == normalized_keyword)
        ]
        return GoogleTrendsOverview(
            keyword=normalized_keyword,
            as_of=selected_as_of,
            observations=tuple(previews),
            keyword_count=len({item.entity_id for item in coverage}),
            observation_count=sum(item.row_count for item in coverage),
            revision_count=sum(item.revision_count for item in coverage),
            latest_available_time=max(
                (item.last_available_time for item in coverage), default=None
            ),
            official_api_status="限量 Alpha；目前使用官方網頁 CSV",
        )

    def import_csv(
        self,
        content: str | bytes,
        *,
        downloaded_at: datetime | None = None,
        source_uri: str = "https://trends.google.com/trends/",
        entity_ids: tuple[str, ...] | None = None,
    ) -> GoogleTrendsImportResult:
        acquired = self._aware(downloaded_at or datetime.now(UTC))
        text = (
            content.decode("utf-8-sig") if isinstance(content, bytes) else content.lstrip("\ufeff")
        )
        if len(text.encode("utf-8")) > 5_000_000:
            raise ValueError("Google Trends CSV 不可超過 5 MB")
        rows = list(csv.reader(io.StringIO(text)))
        header_index, granularity = self._find_header(rows)
        query_context = self._query_context(rows[:header_index])
        context_json = json.dumps(
            query_context, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        context_hash = hashlib.sha256(context_json.encode("utf-8")).hexdigest()[:12]
        header = [cell.strip() for cell in rows[header_index]]
        partial_index = next(
            (index for index, value in enumerate(header) if value.lower() in _PARTIAL_HEADERS),
            None,
        )
        value_indexes = [
            index for index in range(1, len(header)) if index != partial_index and header[index]
        ]
        if not value_indexes:
            raise ValueError("Google Trends CSV 沒有搜尋趨勢欄位")
        if entity_ids is not None and len(entity_ids) != len(value_indexes):
            raise ValueError("entity_ids 數量必須與 CSV 趨勢欄位數量相同")

        series = []
        for offset, index in enumerate(value_indexes):
            inferred, geography = self._series_metadata(header[index])
            entity = (entity_ids[offset] if entity_ids else inferred).strip().upper()
            if not entity:
                raise ValueError(f"第 {index + 1} 欄缺少可識別的關鍵字")
            series.append((index, entity, geography))

        observations: list[PointInTimeObservation] = []
        partial_count = 0
        for row_number, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
            if not row or not any(cell.strip() for cell in row):
                continue
            event_time = self._event_time(row[0], granularity, row_number)
            if event_time > acquired:
                raise ValueError(f"第 {row_number} 列的期間晚於資料取得時間")
            is_partial = (
                partial_index is not None
                and partial_index < len(row)
                and row[partial_index].strip().lower() in _TRUE_VALUES
            )
            for index, entity, geography in series:
                if index >= len(row) or not row[index].strip():
                    continue
                label = row[index].strip()
                interest = self._interest(label, row_number)
                payload = {
                    "geography": geography,
                    "granularity": granularity,
                    "interest": interest,
                    "interest_label": label,
                    "is_partial": is_partial,
                    "keyword": entity,
                    "period_start": event_time.date().isoformat(),
                    "query_context": query_context,
                }
                canonical = json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                observations.append(
                    PointInTimeObservation(
                        id=None,
                        dataset_key=DATASET_KEY,
                        entity_id=entity,
                        event_time=event_time,
                        available_time=acquired,
                        ingested_at=acquired,
                        revision_key=(f"{granularity}:{geography or 'GLOBAL'}:{context_hash}"),
                        content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                        payload_json=canonical,
                        source=SOURCE,
                        source_uri=source_uri,
                    )
                )
                partial_count += int(is_partial)

        if not observations:
            raise ValueError("Google Trends CSV 沒有可匯入的資料列")
        inserted = self._repository.add_revisions(observations)
        return GoogleTrendsImportResult(
            series_count=len(series),
            received=len(observations),
            inserted=inserted,
            partial_count=partial_count,
            downloaded_at=acquired,
        )

    @staticmethod
    def _find_header(rows: list[list[str]]) -> tuple[int, str]:
        for index, row in enumerate(rows):
            if not row:
                continue
            value = row[0].strip().lower()
            if value in {"hour", "time", "小時", "時間"}:
                raise ValueError("小時資料依瀏覽器時區顯示；請先轉成含時區的標準格式再匯入")
            granularity = _HEADER_ALIASES.get(value)
            if granularity:
                return index, granularity
        raise ValueError("找不到 Google Trends 日期／週／月／年標題列")

    @staticmethod
    def _series_metadata(label: str) -> tuple[str, str]:
        match = re.match(r"^(.*?):\s*\(([^()]*)\)\s*$", label.strip())
        if match:
            return match.group(1).strip(), match.group(2).strip().upper()
        return label.strip(), ""

    @staticmethod
    def _query_context(rows: list[list[str]]) -> dict[str, str]:
        context: dict[str, str] = {}
        for row in rows:
            cells = [cell.strip() for cell in row if cell.strip()]
            if not cells:
                continue
            if len(cells) >= 2:
                key, value = cells[0], cells[1]
            elif ":" in cells[0]:
                key, _, value = cells[0].partition(":")
            else:
                continue
            if key.strip() and value.strip():
                context[key.strip().lower()] = value.strip()
        return dict(sorted(context.items()))

    @staticmethod
    def _event_time(raw: str, granularity: str, row_number: int) -> datetime:
        value = raw.strip()
        match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", value)
        if match:
            year, month, day = (int(part) for part in match.groups())
        else:
            month_match = re.fullmatch(r"(\d{4})[-/](\d{1,2})", value)
            year_match = re.fullmatch(r"(\d{4})", value)
            if granularity == "month" and month_match:
                year, month = (int(part) for part in month_match.groups())
                day = 1
            elif granularity == "year" and year_match:
                year, month, day = int(year_match.group(1)), 1, 1
            else:
                raise ValueError(f"第 {row_number} 列無法解析期間：{raw}")
        try:
            return datetime(year, month, day, tzinfo=UTC)
        except ValueError as exc:
            raise ValueError(f"第 {row_number} 列期間無效：{raw}") from exc

    @staticmethod
    def _interest(raw: str, row_number: int) -> int | float:
        if raw == "<1":
            return 0.5
        try:
            value = float(raw.replace(",", ""))
        except ValueError as exc:
            raise ValueError(f"第 {row_number} 列趨勢值無效：{raw}") from exc
        if not 0 <= value <= 100:
            raise ValueError(f"第 {row_number} 列趨勢值必須介於 0 與 100")
        return int(value) if value.is_integer() else value

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("downloaded_at 與 as_of 必須包含時區")
        return value.astimezone(UTC)
