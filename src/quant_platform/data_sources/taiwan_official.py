from __future__ import annotations

import hashlib
import json
import threading
import time as clock
from datetime import UTC, datetime, time
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from quant_platform.domain.entities import TaiwanDataRecord


TAIPEI = ZoneInfo("Asia/Taipei")


class TaiwanOfficialFallbackProvider:
    """Free TWSE/TPEx valuation snapshots with FinMind for other datasets."""

    name = "taiwan_official_router"
    continues_after_primary_block = True
    _urls = {
        ".TW": "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL",
        ".TWO": "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_peratio_analysis",
    }

    def __init__(self, fallback: object, timeout: int = 30) -> None:
        self._fallback = fallback
        self._timeout = timeout
        self._lock = threading.Lock()
        self._cache: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}

    def source_for(self, dataset: str) -> str:
        if dataset == "TaiwanStockPER":
            return "twse_tpex_openapi"
        return str(getattr(self._fallback, "name", "finmind"))

    def status(self) -> dict[str, Any]:
        fallback_status = getattr(self._fallback, "status", None)
        return {
            "official_market_api": "available",
            "finmind": fallback_status() if callable(fallback_status) else {},
        }

    def fetch(
        self, dataset: str, symbol: str, start: datetime, end: datetime
    ) -> list[TaiwanDataRecord]:
        if dataset != "TaiwanStockPER":
            return self._fallback.fetch(dataset, symbol, start, end)
        suffix = ".TWO" if symbol.upper().endswith(".TWO") else ".TW"
        code = symbol.upper().removesuffix(suffix)
        raw = self._market_snapshot(suffix).get(code)
        if raw is None:
            return []
        event_date = self._date(raw.get("Date"))
        event_time = datetime.combine(event_date, time.min, tzinfo=TAIPEI).astimezone(UTC)
        available_time = datetime.combine(event_date, time(18), tzinfo=TAIPEI).astimezone(UTC)
        ingested_at = datetime.now(UTC)
        if available_time > ingested_at:
            return []
        fields = self._normalized_fields(raw, suffix)
        canonical = json.dumps(
            fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return [TaiwanDataRecord(
            id=None,
            symbol=symbol.upper(),
            dataset=dataset,
            event_time=event_time,
            available_time=available_time,
            ingested_at=ingested_at,
            record_key="official_daily_valuation",
            content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            fields_json=canonical,
            source="twse_tpex_openapi",
        )]

    def fetch_many(
        self, dataset: str, symbols: list[str], start: datetime, end: datetime
    ) -> list[TaiwanDataRecord]:
        """Fetch market-wide official data once, then normalize all requested symbols."""
        if dataset != "TaiwanStockPER":
            raise ValueError(f"batch fetch is not supported for {dataset}")
        output: list[TaiwanDataRecord] = []
        for symbol in symbols:
            output.extend(self.fetch(dataset, symbol, start, end))
        return output

    def _market_snapshot(self, suffix: str) -> dict[str, dict[str, Any]]:
        cached = self._cache.get(suffix)
        if cached and clock.monotonic() - cached[0] < 900:
            return cached[1]
        with self._lock:
            cached = self._cache.get(suffix)
            if cached and clock.monotonic() - cached[0] < 900:
                return cached[1]
            request = Request(
                self._urls[suffix],
                headers={
                    "Accept": "application/json",
                    "User-Agent": "StockResearch/1.0",
                },
            )
            with urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8-sig"))
            code_key = "SecuritiesCompanyCode" if suffix == ".TWO" else "Code"
            indexed = {
                str(item.get(code_key, "")).strip(): dict(item)
                for item in payload
                if str(item.get(code_key, "")).strip()
            }
            self._cache[suffix] = (clock.monotonic(), indexed)
            return indexed

    @staticmethod
    def _date(value: object):
        digits = str(value or "").replace("/", "").replace("-", "")
        if len(digits) == 7:
            year = int(digits[:3]) + 1911
            month, day = int(digits[3:5]), int(digits[5:7])
        elif len(digits) == 8:
            year, month, day = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        else:
            raise ValueError(f"unsupported official market date: {value!r}")
        return datetime(year, month, day).date()

    @staticmethod
    def _normalized_fields(raw: dict[str, Any], suffix: str) -> dict[str, Any]:
        if suffix == ".TWO":
            return {
                "date": raw.get("Date"),
                "stock_id": raw.get("SecuritiesCompanyCode"),
                "PER": raw.get("PriceEarningRatio"),
                "PBR": raw.get("PriceBookRatio"),
                "dividend_yield": raw.get("YieldRatio"),
            }
        return {
            "date": raw.get("Date"),
            "stock_id": raw.get("Code"),
            "PER": raw.get("PEratio"),
            "PBR": raw.get("PBratio"),
            "dividend_yield": raw.get("DividendYield"),
        }
