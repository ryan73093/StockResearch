from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from quant_platform.domain.entities import PointInTimeDataset, PointInTimeObservation


TAIPEI = ZoneInfo("Asia/Taipei")


class TwsePointInTimeProvider:
    """Official TWSE OpenAPI snapshot adapter.

    TWT53U is a latest-market report rather than a historical archive.  The
    platform records exactly when the snapshot was downloaded and never assigns
    it to an earlier as-of date.
    """

    name = "twse_openapi"
    endpoint = "https://openapi.twse.com.tw/v1/exchangeReport/TWT53U"

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout

    def fetch(
        self,
        definition: PointInTimeDataset,
        entity_id: str,
        start: datetime,
        end: datetime,
        ingested_at: datetime,
    ) -> list[PointInTimeObservation]:
        local_day = ingested_at.astimezone(TAIPEI).date()
        if not start.astimezone(TAIPEI).date() <= local_day <= end.astimezone(TAIPEI).date():
            raise ValueError("證交所零股 OpenAPI 只提供目前快照，不能直接回補歷史日期")
        request = Request(self.endpoint, headers={"Accept": "application/json"})
        with urlopen(request, timeout=self._timeout) as response:
            rows = json.loads(response.read().decode("utf-8-sig"))
        if not isinstance(rows, list):
            raise RuntimeError("證交所零股 API 回傳格式不是清單")
        return self._normalize(definition, entity_id, rows, ingested_at)

    def _normalize(
        self,
        definition: PointInTimeDataset,
        requested_entity: str,
        rows: list[dict[str, Any]],
        ingested_at: datetime,
    ) -> list[PointInTimeObservation]:
        output = []
        normalized_request = requested_entity.upper().removesuffix(".TW")
        for raw in rows:
            entity_id = str(
                raw.get("Code") or raw.get("證券代號") or raw.get("stock_id") or ""
            ).strip().upper()
            if not entity_id:
                continue
            if normalized_request not in {"", "ALL"} and entity_id != normalized_request:
                continue
            raw = self._typed_payload(raw)
            canonical = json.dumps(
                raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            event_time = ingested_at.astimezone(UTC)
            local_date = ingested_at.astimezone(TAIPEI).date().isoformat()
            output.append(PointInTimeObservation(
                id=None, dataset_key=definition.dataset_key, entity_id=entity_id,
                event_time=event_time, available_time=event_time, ingested_at=event_time,
                revision_key=f"{entity_id}:{local_date}:snapshot",
                content_hash=content_hash, payload_json=canonical,
                source=self.name, source_uri=self.endpoint,
            ))
        return output

    @staticmethod
    def _typed_payload(raw: dict[str, Any]) -> dict[str, Any]:
        integer_fields = {
            "TradeVolume", "Transaction", "TradeValue", "BestBidVolume", "BestAskVolume",
            "成交股數", "成交筆數", "成交金額",
        }
        decimal_fields = {
            "TradePrice", "BestBidPrice", "BestAskPrice", "成交價",
        }
        output = dict(raw)
        for key in integer_fields:
            value = output.get(key)
            if value not in {None, "", "-", "--"}:
                try:
                    output[key] = int(float(str(value).replace(",", "")))
                except ValueError:
                    pass
        for key in decimal_fields:
            value = output.get(key)
            if value not in {None, "", "-", "--"}:
                try:
                    output[key] = float(str(value).replace(",", ""))
                except ValueError:
                    pass
        return output


class PointInTimeProviderRouter:
    """Routes catalog-defined datasets without coupling the use case to vendors."""

    name = "router"

    def __init__(self, providers: list[object]) -> None:
        self._providers = {str(item.name): item for item in providers}

    @property
    def has_token(self) -> bool:
        provider = self._providers.get("finmind")
        return bool(getattr(provider, "has_token", False))

    def fetch(self, definition, entity_id, start, end, ingested_at):
        provider = self._providers.get(definition.source)
        if provider is None:
            raise ValueError(f"尚未安裝資料來源介接器：{definition.source}")
        return provider.fetch(definition, entity_id, start, end, ingested_at)
