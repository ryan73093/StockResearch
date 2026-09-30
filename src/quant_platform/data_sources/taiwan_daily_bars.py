from __future__ import annotations

import json
import threading
import time as clock
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from quant_platform.domain.entities import MarketBar

TAIPEI = ZoneInfo("Asia/Taipei")


class TaiwanOfficialDailyBarProvider:
    """Official TWSE/TPEx close snapshot used when Yahoo has not rolled over.

    TWSE's bulk OpenAPI can lag one session, so the dated MI_INDEX report is
    used for listed securities. TPEx's official daily-close OpenAPI already
    exposes the latest completed session. Both are keyless and market-wide.
    """

    name = "twse_tpex_official"
    _twse_url = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
    _tpex_latest_url = (
        "https://www.tpex.org.tw/openapi/v1/"
        "tpex_mainboard_daily_close_quotes"
    )
    _tpex_historical_url = (
        "https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/"
        "stk_quote_result.php"
    )

    def __init__(self, timeout: int = 30, cache_minutes: int = 15) -> None:
        self._timeout = timeout
        self._cache_for = cache_minutes * 60
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._cached_date: date | None = None
        self._cached: dict[str, MarketBar] = {}

    @staticmethod
    def expected_session_date(now: datetime) -> date:
        if now.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        local_now = now.astimezone(TAIPEI)
        candidate = local_now.date()
        if local_now.time() < time(13, 45):
            candidate -= timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate -= timedelta(days=1)
        return candidate

    def fetch_snapshot(
        self, symbols: list[str], now: datetime | None = None
    ) -> list[MarketBar]:
        ingested_at = (now or datetime.now(UTC)).astimezone(UTC)
        target = self.expected_session_date(ingested_at)
        requested = {symbol.upper() for symbol in symbols}
        with self._lock:
            if (
                self._cached_date != target
                or clock.monotonic() - self._cached_at >= self._cache_for
            ):
                twse = self._fetch_twse(target, ingested_at)
                tpex = self._fetch_tpex(target, ingested_at)
                self._cached = {item.symbol: item for item in (*twse, *tpex)}
                self._cached_date = target
                self._cached_at = clock.monotonic()
            return [
                self._cached[symbol]
                for symbol in sorted(requested)
                if symbol in self._cached
            ]

    def fetch_range(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
        now: datetime | None = None,
    ) -> list[MarketBar]:
        """Fetch a short missing-session range with two bulk requests per day."""
        if end_date < start_date:
            return []
        ingested_at = (now or datetime.now(UTC)).astimezone(UTC)
        requested = {symbol.upper() for symbol in symbols}
        bars: dict[tuple[str, datetime], MarketBar] = {}
        target = start_date
        while target <= end_date:
            if target.weekday() < 5:
                daily = (*self._fetch_twse(target, ingested_at), *self._fetch_tpex(target, ingested_at))
                for item in daily:
                    if item.symbol in requested:
                        bars[(item.symbol, item.event_time)] = item
            target += timedelta(days=1)
        return sorted(bars.values(), key=lambda item: (item.event_time, item.symbol))

    def _request_json(self, url: str) -> object:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request = Request(
                    url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "StockResearch/1.0",
                    },
                )
                with urlopen(request, timeout=self._timeout) as response:
                    return json.loads(response.read().decode("utf-8-sig"))
            except Exception as exc:  # noqa: BLE001 - bounded provider retry
                last_error = exc
                if attempt < 2:
                    clock.sleep(1.5 * (2**attempt))
        raise RuntimeError(f"官方台股收盤資料下載失敗：{last_error}")

    def _fetch_twse(
        self, target: date, ingested_at: datetime
    ) -> list[MarketBar]:
        query = urlencode({
            "response": "json",
            "date": target.strftime("%Y%m%d"),
            "type": "ALLBUT0999",
        })
        payload = self._request_json(f"{self._twse_url}?{query}")
        if not isinstance(payload, dict) or payload.get("stat") != "OK":
            return []
        tables = payload.get("tables") or []
        quote_table = next(
            (
                item for item in tables
                if isinstance(item, dict)
                and (item.get("fields") or [])[:2] == ["證券代號", "證券名稱"]
            ),
            None,
        )
        if quote_table is None:
            return []
        event_date = self._date(payload.get("date"))
        return [
            bar
            for row in quote_table.get("data") or []
            if (bar := self._twse_bar(row, event_date, ingested_at)) is not None
        ]

    def _fetch_tpex(
        self, target: date, ingested_at: datetime
    ) -> list[MarketBar]:
        today = ingested_at.astimezone(TAIPEI).date()
        if target == today:
            payload = self._request_json(self._tpex_latest_url)
            if not isinstance(payload, list):
                return []
            return [
                bar
                for row in payload
                if isinstance(row, dict)
                and (bar := self._tpex_bar(row, ingested_at)) is not None
            ]

        roc_year = target.year - 1911
        query = urlencode({
            "l": "zh-tw",
            "o": "json",
            "d": f"{roc_year:03d}/{target:%m/%d}",
            "s": "0,asc,0",
        })
        payload = self._request_json(f"{self._tpex_historical_url}?{query}")
        if not isinstance(payload, dict) or payload.get("stat") != "ok":
            return []
        tables = payload.get("tables") or []
        quote_table = tables[0] if tables and isinstance(tables[0], dict) else None
        if quote_table is None:
            return []
        event_date = self._date(payload.get("date"))
        return [
            bar
            for row in quote_table.get("data") or []
            if (bar := self._tpex_historical_bar(row, event_date, ingested_at)) is not None
        ]

    def _fetch_tpex_latest(self, ingested_at: datetime) -> list[MarketBar]:
        payload = self._request_json(self._tpex_latest_url)
        if not isinstance(payload, list):
            return []
        return [
            bar
            for row in payload
            if isinstance(row, dict)
            and (bar := self._tpex_bar(row, ingested_at)) is not None
        ]

    @classmethod
    def _tpex_historical_bar(
        cls, row: object, event_date: date, ingested_at: datetime
    ) -> MarketBar | None:
        if not isinstance(row, list) or len(row) < 9:
            return None
        code = str(row[0]).strip()
        return cls._bar(
            symbol=f"{code}.TWO",
            event_date=event_date,
            open_value=row[4],
            high_value=row[5],
            low_value=row[6],
            close_value=row[2],
            volume_value=row[8],
            ingested_at=ingested_at,
        )

    @classmethod
    def _twse_bar(
        cls, row: object, event_date: date, ingested_at: datetime
    ) -> MarketBar | None:
        if not isinstance(row, list) or len(row) < 9:
            return None
        code = str(row[0]).strip()
        return cls._bar(
            symbol=f"{code}.TW",
            event_date=event_date,
            open_value=row[5],
            high_value=row[6],
            low_value=row[7],
            close_value=row[8],
            volume_value=row[2],
            ingested_at=ingested_at,
        )

    @classmethod
    def _tpex_bar(
        cls, row: dict[str, object], ingested_at: datetime
    ) -> MarketBar | None:
        code = str(row.get("SecuritiesCompanyCode") or "").strip()
        try:
            event_date = cls._date(row.get("Date"))
        except ValueError:
            return None
        return cls._bar(
            symbol=f"{code}.TWO",
            event_date=event_date,
            open_value=row.get("Open"),
            high_value=row.get("High"),
            low_value=row.get("Low"),
            close_value=row.get("Close"),
            volume_value=row.get("TradingShares"),
            ingested_at=ingested_at,
        )

    @classmethod
    def _bar(
        cls,
        *,
        symbol: str,
        event_date: date,
        open_value: object,
        high_value: object,
        low_value: object,
        close_value: object,
        volume_value: object,
        ingested_at: datetime,
    ) -> MarketBar | None:
        prices = [cls._decimal(value) for value in (
            open_value, high_value, low_value, close_value
        )]
        if any(value is None for value in prices):
            return None
        open_price, high_price, low_price, close_price = prices
        assert open_price is not None
        assert high_price is not None
        assert low_price is not None
        assert close_price is not None
        event_time = datetime.combine(event_date, time(13, 30), tzinfo=TAIPEI)
        available_time = event_time + timedelta(minutes=15)
        if available_time > ingested_at:
            return None
        return MarketBar(
            symbol=symbol,
            market="TW",
            interval="1d",
            event_time=event_time,
            available_time=available_time,
            ingested_at=ingested_at,
            open=open_price,
            high=max(high_price, open_price, close_price),
            low=min(low_price, open_price, close_price),
            close=close_price,
            adjusted_close=close_price,
            volume=cls._integer(volume_value),
            source=cls.name,
        )

    @staticmethod
    def _decimal(value: object) -> Decimal | None:
        normalized = str(value or "").replace(",", "").strip()
        if normalized in {"", "--", "---"}:
            return None
        try:
            return Decimal(normalized)
        except InvalidOperation:
            return None

    @staticmethod
    def _integer(value: object) -> int:
        try:
            return max(0, int(str(value or "0").replace(",", "").strip()))
        except ValueError:
            return 0

    @staticmethod
    def _date(value: object) -> date:
        digits = str(value or "").replace("/", "").replace("-", "")
        if len(digits) == 7:
            return date(int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:7]))
        if len(digits) == 8:
            return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        raise ValueError(f"unsupported official market date: {value!r}")
