from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from quant_platform.domain.entities import PointInTimeDataset, PointInTimeObservation


TAIPEI = ZoneInfo("Asia/Taipei")


class FinMindPointInTimeProvider:
    """FinMind v4 adapter for intraday and derivative research datasets.

    Historical API downloads are end-of-day research data, not a live feed.  The
    adapter therefore uses the documented dataset publication time instead of
    pretending every minute/tick was observable in real time.
    """

    name = "finmind"

    def __init__(self, base_url: str, token: str = "", timeout: int = 45) -> None:
        self._base_url = base_url
        self._token = token
        self._timeout = timeout

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    def fetch(
        self,
        definition: PointInTimeDataset,
        entity_id: str,
        start: datetime,
        end: datetime,
        ingested_at: datetime,
    ) -> list[PointInTimeObservation]:
        if definition.source != self.name:
            raise ValueError(f"不支援的資料來源：{definition.source}")
        if definition.access_tier in {"sponsor", "sponsorpro", "backer_or_sponsor"} and not self._token:
            raise ValueError(
                f"{definition.display_name} 需要 FinMind {definition.access_tier} 權限；"
                "請先在 .env 設定 FINMIND_TOKEN。"
            )
        payloads: list[dict[str, Any]] = []
        # High-volume datasets are officially restricted to one trading day per
        # request.  Keep the loop explicit so rate/cost behavior remains visible.
        if definition.frequency in {"1m", "tick"}:
            current = start.astimezone(TAIPEI).date()
            final = end.astimezone(TAIPEI).date()
            while current <= final:
                payloads.extend(self._request(definition, entity_id, current, current))
                current += timedelta(days=1)
        else:
            payloads.extend(self._request(
                definition,
                entity_id,
                start.astimezone(TAIPEI).date(),
                end.astimezone(TAIPEI).date(),
            ))
        return self._normalize(definition, entity_id, payloads, ingested_at)

    def _request(
        self,
        definition: PointInTimeDataset,
        entity_id: str,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        params = {
            "dataset": definition.source_dataset,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }
        if entity_id and entity_id != "ALL":
            params["data_id"] = entity_id.upper().removesuffix(".TW").removesuffix(".TWO")
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        request = Request(f"{self._base_url}?{urlencode(params)}", headers=headers)
        with urlopen(request, timeout=self._timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("status") not in {None, 200}:
            raise RuntimeError(payload.get("msg") or f"FinMind status {payload.get('status')}")
        return [dict(item) for item in payload.get("data", [])]

    def _normalize(
        self,
        definition: PointInTimeDataset,
        requested_entity: str,
        rows: list[dict[str, Any]],
        ingested_at: datetime,
    ) -> list[PointInTimeObservation]:
        occurrence: defaultdict[str, int] = defaultdict(int)
        output: list[PointInTimeObservation] = []
        for raw in rows:
            event_time = self._event_time(raw)
            available_time = self._available_time(definition, event_time)
            if available_time > ingested_at:
                continue
            entity_id = self._entity_id(raw, requested_entity)
            canonical = json.dumps(
                raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            base_key = self._revision_key(raw, entity_id)
            occurrence[base_key] += 1
            revision_key = f"{base_key}#{occurrence[base_key]}" if occurrence[base_key] > 1 else base_key
            output.append(PointInTimeObservation(
                id=None,
                dataset_key=definition.dataset_key,
                entity_id=entity_id,
                event_time=event_time,
                available_time=available_time,
                ingested_at=ingested_at,
                revision_key=revision_key[:240],
                content_hash=content_hash,
                payload_json=canonical,
                source=self.name,
                source_uri=f"{self._base_url}?dataset={definition.source_dataset}",
            ))
        return output

    @staticmethod
    def _event_time(raw: dict[str, Any]) -> datetime:
        raw_date = str(raw.get("date") or raw.get("datetime") or "")
        if not raw_date:
            raise ValueError("來源資料缺少 date/datetime")
        raw_clock = str(raw.get("minute") or raw.get("time") or "").strip()
        combined = raw_date[:10]
        if len(raw_date) >= 16:
            combined = raw_date[:19]
        elif raw_clock:
            combined = f"{raw_date[:10]} {raw_clock[:8]}"
        formats = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")
        for fmt in formats:
            try:
                parsed = datetime.strptime(combined, fmt)
                return parsed.replace(tzinfo=TAIPEI).astimezone(UTC)
            except ValueError:
                continue
        raise ValueError(f"無法解析來源時間：{combined}")

    @staticmethod
    def _available_time(definition: PointInTimeDataset, event_time: datetime) -> datetime:
        local_date = event_time.astimezone(TAIPEI).date()
        publication = time(15, 50) if definition.frequency == "1m" else time(18, 0)
        return datetime.combine(local_date, publication, tzinfo=TAIPEI).astimezone(UTC)

    @staticmethod
    def _entity_id(raw: dict[str, Any], fallback: str) -> str:
        for key in ("stock_id", "futures_id", "option_id", "data_id", "code"):
            if raw.get(key) not in {None, ""}:
                return str(raw[key]).upper()
        return fallback.upper() or "ALL"

    @staticmethod
    def _revision_key(raw: dict[str, Any], entity_id: str) -> str:
        keys = (
            "contract_date", "contract_month", "trading_session", "PutCall", "callput",
            "ExercisePrice", "strike_price", "type", "minute", "time", "sequence",
        )
        dimensions = {key: raw[key] for key in keys if raw.get(key) not in {None, ""}}
        return f"{entity_id}:{json.dumps(dimensions, ensure_ascii=False, sort_keys=True)}"

