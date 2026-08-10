from __future__ import annotations

import hashlib
import json
import threading
import time as clock
from pathlib import Path
from datetime import UTC, datetime, time, timedelta
from typing import Any
from urllib.parse import urlencode
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from quant_platform.domain.entities import TaiwanDataRecord
from quant_platform.application.ports import ProviderAccessBlockedError


TAIPEI = ZoneInfo("Asia/Taipei")


class FinMindProvider:
    """Small FinMind v4 adapter that preserves raw records and publication timing."""

    name = "finmind"
    _publication_hours = {
        "TaiwanStockInstitutionalInvestorsBuySell": 16,
        "TaiwanStockMarginPurchaseShortSale": 16,
        "TaiwanStockSecuritiesLending": 18,
        "TaiwanStockPER": 18,
        "TaiwanStockMonthRevenue": 18,
        "TaiwanStockFinancialStatements": 18,
        "TaiwanStockBalanceSheet": 18,
        "TaiwanStockCashFlowsStatement": 18,
        "TaiwanStockNews": 18,
    }
    _quarterly_datasets = {
        "TaiwanStockFinancialStatements",
        "TaiwanStockBalanceSheet",
        "TaiwanStockCashFlowsStatement",
    }

    def __init__(
        self, base_url: str, token: str = "", timeout: int = 30,
        circuit_state_path: str | Path | None = None,
    ) -> None:
        self._base_url = base_url
        self._token = token
        self._timeout = timeout
        self._request_lock = threading.Lock()
        self._last_request_at = 0.0
        self._minimum_interval = 0.40 if token else 0.75
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._blocked_reason: str | None = None
        self._blocked_until: float | None = None
        self._blocked_code: int | None = None
        self._circuit_state_path = Path(circuit_state_path) if circuit_state_path else None
        self._load_circuit_state()

    def status(self) -> dict[str, Any]:
        """Return circuit state without consuming an API request."""
        remaining = None
        if self._blocked_until is not None:
            remaining = max(0, int(self._blocked_until - clock.time()))
        return {
            "available": self._blocked_reason is None or remaining == 0,
            "http_code": self._blocked_code,
            "reason": self._blocked_reason,
            "retry_after_seconds": remaining,
        }

    def fetch(
        self, dataset: str, symbol: str, start: datetime, end: datetime
    ) -> list[TaiwanDataRecord]:
        data_id = symbol.upper().removesuffix(".TW").removesuffix(".TWO")
        payloads: list[dict[str, Any]] = []
        if dataset == "TaiwanStockNews":
            # FinMind explicitly returns only one day per news request.
            first = max(start.date(), end.date() - timedelta(days=6))
            current = first
            while current <= end.date():
                payloads.append(self._request({
                    "dataset": dataset, "data_id": data_id,
                    "start_date": current.isoformat(),
                }))
                current += timedelta(days=1)
        else:
            payloads.append(self._request({
                "dataset": dataset, "data_id": data_id,
                "start_date": start.date().isoformat(), "end_date": end.date().isoformat(),
            }))
        ingested_at = datetime.now(UTC)
        output: list[TaiwanDataRecord] = []
        for payload in payloads:
            for raw in payload.get("data", []):
                record = self._record(dataset, symbol, raw, ingested_at)
                if record.available_time <= ingested_at:
                    output.append(record)
        return output

    def fetch_dataset(self, dataset: str) -> list[dict[str, Any]]:
        """Fetch a market-wide FinMind dataset without inventing a security id."""
        params: dict[str, str] = {"dataset": dataset}
        payload = self._request(params)
        return [dict(item) for item in payload.get("data", [])]

    def _request(self, params: dict[str, str]) -> dict[str, Any]:
        self._raise_if_blocked()
        values = dict(params)
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        url = f"{self._base_url}?{urlencode(values)}"
        cached = self._cache.get(url)
        if cached and clock.monotonic() - cached[0] < 900:
            return cached[1]
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                with self._request_lock:
                    wait = self._minimum_interval - (
                        clock.monotonic() - self._last_request_at
                    )
                    if wait > 0:
                        clock.sleep(wait)
                    request = Request(url, headers=headers)
                    with urlopen(request, timeout=self._timeout) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    self._last_request_at = clock.monotonic()
                if payload.get("status") not in {None, 200}:
                    raise RuntimeError(
                        payload.get("msg")
                        or f"FinMind status {payload.get('status')}"
                    )
                self._cache[url] = (clock.monotonic(), payload)
                return payload
            except HTTPError as exc:
                last_error = exc
                self._last_request_at = clock.monotonic()
                if exc.code == 401:
                    self._block(exc.code, None, "權杖無效；更新 Token 前不再呼叫")
                    raise ProviderAccessBlockedError(self._blocked_reason or "FinMind 401") from exc
                if exc.code == 402:
                    self._block(exc.code, 3600, "每小時額度已用完")
                    raise ProviderAccessBlockedError(self._blocked_reason or "FinMind 402") from exc
                if exc.code == 403:
                    self._block(exc.code, 1800, "IP 暫時被封鎖")
                    raise ProviderAccessBlockedError(self._blocked_reason or "FinMind 403") from exc
                if exc.code == 429:
                    retry_after = self._retry_after_seconds(exc)
                    self._block(exc.code, retry_after, "請求過快")
                    raise ProviderAccessBlockedError(self._blocked_reason or "FinMind 429") from exc
                if 400 <= exc.code < 500:
                    raise RuntimeError(
                        f"FinMind request rejected (HTTP {exc.code}); request will not be retried"
                    ) from exc
                if attempt < 3 and exc.code >= 500:
                    clock.sleep(1.5 * (2 ** attempt))
                else:
                    break
            except ProviderAccessBlockedError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    clock.sleep(1.5 * (2 ** attempt))
        raise RuntimeError(f"FinMind request failed after retry: {last_error}")

    def _raise_if_blocked(self) -> None:
        if self._blocked_reason is None:
            return
        if self._blocked_until is not None and clock.time() >= self._blocked_until:
            self._blocked_reason = None
            self._blocked_until = None
            self._blocked_code = None
            self._persist_circuit_state()
            return
        raise ProviderAccessBlockedError(self._blocked_reason)

    def _block(self, code: int, seconds: int | None, explanation: str) -> None:
        self._blocked_code = code
        self._blocked_until = None if seconds is None else clock.time() + seconds
        cooldown = "需更新設定" if seconds is None else f"冷卻 {seconds // 60 or 1} 分鐘後再試"
        self._blocked_reason = (
            f"FinMind HTTP {code}：{explanation}；{cooldown}。冷卻期間不送出 HTTP。"
        )
        self._persist_circuit_state()

    def _load_circuit_state(self) -> None:
        path = self._circuit_state_path
        if path is None or not path.exists():
            return
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            self._blocked_code = state.get("http_code")
            self._blocked_reason = state.get("reason")
            self._blocked_until = state.get("blocked_until")
            self._raise_if_blocked()
        except ProviderAccessBlockedError:
            pass
        except Exception:
            self._blocked_code = self._blocked_reason = self._blocked_until = None

    def _persist_circuit_state(self) -> None:
        path = self._circuit_state_path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "http_code": self._blocked_code,
            "reason": self._blocked_reason,
            "blocked_until": self._blocked_until,
        }, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _retry_after_seconds(exc: HTTPError) -> int:
        raw = exc.headers.get("Retry-After") if exc.headers else None
        try:
            return max(1, min(3600, int(raw)))
        except (TypeError, ValueError):
            return 60

    def _record(
        self,
        dataset: str,
        symbol: str,
        raw: dict[str, Any],
        ingested_at: datetime,
    ) -> TaiwanDataRecord:
        raw_date = str(raw["date"])
        event_date = datetime.strptime(raw_date[:10], "%Y-%m-%d").date()
        if dataset == "TaiwanStockNews" and len(raw_date) >= 19:
            local_event = datetime.strptime(raw_date[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIPEI)
            event_time = local_event.astimezone(UTC)
        else:
            event_time = datetime.combine(event_date, time.min, tzinfo=TAIPEI).astimezone(UTC)
        publication_date = event_date
        if raw.get("create_time"):
            publication_date = datetime.strptime(str(raw["create_time"])[:10], "%Y-%m-%d").date()
        elif dataset in self._quarterly_datasets:
            # FinMind statement history exposes period-end dates but not historical filing timestamps.
            # A 90-day lag is deliberately conservative across quarterly and annual reports.
            publication_date = event_date + timedelta(days=90)
        if dataset == "TaiwanStockNews":
            available_time = event_time
        else:
            hour = self._publication_hours.get(dataset, 18)
            available_time = datetime.combine(publication_date, time(hour), tzinfo=TAIPEI).astimezone(UTC)
        normalized_symbol = symbol.upper()
        canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        dimensions = {
            key: value
            for key, value in raw.items()
            if key not in {"date", "stock_id"} and not self._is_number(value)
        }
        record_key = json.dumps(dimensions, ensure_ascii=False, sort_keys=True) or "record"
        return TaiwanDataRecord(
            id=None,
            symbol=normalized_symbol,
            dataset=dataset,
            event_time=event_time,
            available_time=available_time,
            ingested_at=ingested_at,
            record_key=record_key[:160],
            content_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            fields_json=canonical,
            source=self.name,
        )

    @staticmethod
    def _is_number(value: Any) -> bool:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        try:
            float(str(value).replace(",", ""))
            return value not in {None, ""}
        except (TypeError, ValueError):
            return False
