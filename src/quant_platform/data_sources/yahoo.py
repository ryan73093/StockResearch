from __future__ import annotations

import json
import logging
import os
import threading
import time as clock
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from math import isfinite
from pathlib import Path
from tempfile import gettempdir
from typing import ClassVar
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from quant_platform.domain.entities import MarketBar

logger = logging.getLogger(__name__)


def _ascii(path: Path) -> bool:
    return str(path).isascii()


def ascii_ca_bundle(source: str | Path | None = None, target_dir: str | Path | None = None) -> str | None:
    """A copy of certifi's CA bundle at an ASCII-only path for curl (yfinance).

    The services run with PYTHONUTF8=1 (UTF-8 logs). curl_cffi then hands libcurl the bundle path
    as UTF-8 bytes while BoringSSL opens it with the ANSI code page, so under a non-ASCII Windows
    profile ("C:\\Users\\皮咪\\...") every yfinance request failed with curl error 77 and Yahoo
    data only arrived through the slower chart fallback (found 2026-10-01). ``None`` when no
    usable copy can be made; curl then keeps its default.
    """
    if source is None:
        import certifi

        source = certifi.where()
    source = Path(source)
    if _ascii(source):
        return str(source)
    folder = Path(target_dir or Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / "StockResearch")
    target = folder / "cacert.pem"
    if not _ascii(target):
        return None
    try:
        data = source.read_bytes()
        if not target.is_file() or target.read_bytes() != data:
            folder.mkdir(parents=True, exist_ok=True)
            partial = target.with_suffix(".pem.partial")
            partial.write_bytes(data)
            partial.replace(target)
    except OSError as exc:
        logger.warning("CA bundle copy for curl failed: %s", exc)
        return None
    return str(target)


class YahooFinanceProvider:
    """Yahoo daily-bar adapter.

    Yahoo provides session dates rather than authoritative publication timestamps.
    We conservatively mark a daily bar available 15 minutes after the regular close.
    Exchange calendars will replace this small market mapping in the scheduler phase.
    """

    name = "yahoo_finance"
    _market_closes: ClassVar[dict[str, tuple[str, time]]] = {
        "US": ("America/New_York", time(16, 0)),
        "TW": ("Asia/Taipei", time(13, 30)),
    }
    _request_lock = threading.Lock()
    _last_request_at = 0.0
    # yfinance can return empty frames for every symbol (cookie/crumb trouble);
    # each one then costs three tries and 3 s of back-off before the chart
    # route (2026-10-01: 536 Taiwan symbols took over half an hour). After
    # three empty symbols in a row the chart route is used directly for 30 minutes.
    _EMPTY_STREAK_LIMIT: ClassVar[int] = 3
    _SKIP_SECONDS: ClassVar[float] = 1800.0
    _yfinance_empty_streak = 0
    _yfinance_skip_until = 0.0
    _curl_session: ClassVar[object | None] = None

    @classmethod
    def _yfinance_session(cls) -> object | None:
        """One curl_cffi session for yfinance, verifying with an ASCII-path CA bundle."""
        if cls._curl_session is None:
            try:
                from curl_cffi import requests as curl_requests
            except ImportError:  # older yfinance on requests: keep its own session
                return None
            cls._curl_session = curl_requests.Session(impersonate="chrome", verify=ascii_ca_bundle() or True)
        return cls._curl_session

    def fetch_daily_bars(
        self, symbol: str, market: str, start: datetime, end: datetime
    ) -> list[MarketBar]:
        provider = type(self)
        if clock.monotonic() < provider._yfinance_skip_until:
            return self._fetch_chart_bars(symbol, market, start, end)
        try:
            import yfinance as yf
        except ImportError as exc:
            raise RuntimeError(
                "Yahoo provider requires the data extra: pip install '.[data]'"
            ) from exc

        # yfinance's SQLite timezone cache cannot open paths containing some
        # non-ASCII Windows profile segments. Keep its disposable cache in the
        # OS temp directory; research data still persists in our own database.
        cache_path = Path(gettempdir()) / "quant-yfinance-cache"
        cache_path.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(cache_path))

        last_error: Exception | None = None
        history = None
        for attempt in range(3):
            try:
                with self._request_lock:
                    wait = 0.45 - (clock.monotonic() - self._last_request_at)
                    if wait > 0:
                        clock.sleep(wait)
                    history = yf.Ticker(symbol, session=self._yfinance_session()).history(
                        start=start.date().isoformat(),
                        end=end.date().isoformat(),
                        interval="1d",
                        auto_adjust=False,
                        actions=False,
                        repair=False,
                    )
                    type(self)._last_request_at = clock.monotonic()
                if not history.empty:
                    break
            except Exception as exc:  # noqa: BLE001 -- retry all vendor-library failures
                last_error = exc
            if attempt < 2:
                clock.sleep(2**attempt)
        if history is None or history.empty:
            # yfinance's cookie/crumb route can temporarily return an empty
            # frame while Yahoo's public chart route remains available. Use a
            # single range request as a bounded fallback; it avoids month-by-
            # month retry storms and still returns adjusted close and splits.
            provider._yfinance_empty_streak += 1
            if provider._yfinance_empty_streak >= provider._EMPTY_STREAK_LIMIT:
                provider._yfinance_skip_until = clock.monotonic() + provider._SKIP_SECONDS
                provider._yfinance_empty_streak = 0
            return self._fetch_chart_bars(symbol, market, start, end, last_error)

        provider._yfinance_empty_streak = 0
        return self._normalize_history(history, symbol, market)

    def _normalize_history(
        self,
        history,
        symbol: str,
        market: str,
        ingested_at: datetime | None = None,
    ) -> list[MarketBar]:
        timezone_name, close_time = self._market_closes.get(market.upper(), ("UTC", time(23, 59)))
        timezone = ZoneInfo(timezone_name)
        ingested_at = ingested_at or datetime.now(UTC)
        bars: list[MarketBar] = []
        for index, row in history.iterrows():
            session_date = index.to_pydatetime().date()
            event_time = datetime.combine(session_date, close_time, tzinfo=timezone)
            available_time = event_time + timedelta(minutes=15)
            if available_time > ingested_at:
                # Yahoo can expose a still-forming daily candle before the session closes.
                continue
            open_price = self._decimal_or_none(row.get("Open"))
            high_price = self._decimal_or_none(row.get("High"))
            low_price = self._decimal_or_none(row.get("Low"))
            close_price = self._decimal_or_none(row.get("Close"))
            if None in {open_price, high_price, low_price, close_price}:
                # Yahoo sometimes includes an all-NaN session row. One bad
                # candle must not discard the rest of a valid historical batch.
                continue
            assert open_price is not None
            assert high_price is not None
            assert low_price is not None
            assert close_price is not None
            adjusted_close = self._decimal_or_none(row.get("Adj Close"))
            volume_value = self._finite_float(row.get("Volume"))
            # Yahoo FX candles occasionally publish a rounded high/low a few
            # ticks inside open/close. Preserve the observed endpoints while
            # restoring the OHLC invariant required by the quality gate.
            high_price = max(high_price, open_price, close_price)
            low_price = min(low_price, open_price, close_price)
            bars.append(
                MarketBar(
                    symbol=symbol,
                    market=market.upper(),
                    interval="1d",
                    event_time=event_time,
                    available_time=available_time,
                    ingested_at=ingested_at,
                    open=open_price,
                    high=high_price,
                    low=low_price,
                    close=close_price,
                    adjusted_close=adjusted_close,
                    volume=max(0, int(volume_value or 0)),
                    source=self.name,
                )
            )
        return bars

    @staticmethod
    def _finite_float(value: object) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if isfinite(number) else None

    @classmethod
    def _decimal_or_none(cls, value: object) -> Decimal | None:
        number = cls._finite_float(value)
        return Decimal(str(value)) if number is not None else None

    def _fetch_chart_bars(
        self,
        symbol: str,
        market: str,
        start: datetime,
        end: datetime,
        previous_error: Exception | None = None,
    ) -> list[MarketBar]:
        query = urlencode(
            {
                "period1": int(start.timestamp()),
                "period2": int(end.timestamp()),
                "interval": "1d",
                "events": "div,splits",
                "includeAdjustedClose": "true",
            }
        )
        request = Request(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?{query}",
            headers={"User-Agent": "Mozilla/5.0 StockResearch/1.0"},
        )
        last_error = previous_error
        payload: dict[str, object] | None = None
        for attempt in range(3):
            try:
                with self._request_lock:
                    wait = 0.45 - (clock.monotonic() - self._last_request_at)
                    if wait > 0:
                        clock.sleep(wait)
                    with urlopen(request, timeout=30) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    type(self)._last_request_at = clock.monotonic()
                break
            except Exception as exc:  # noqa: BLE001 -- retry all network/provider failures
                last_error = exc
                if attempt < 2:
                    clock.sleep(2**attempt)
        if payload is None:
            raise RuntimeError(f"Yahoo chart download failed after retry: {last_error}")
        chart = payload.get("chart", {})
        if not isinstance(chart, dict) or chart.get("error"):
            raise RuntimeError(f"Yahoo chart returned an error: {chart.get('error')}")
        results = chart.get("result") or []
        if not results:
            return []
        result = results[0]
        timestamps = result.get("timestamp") or []
        indicators = result.get("indicators") or {}
        quotes = indicators.get("quote") or []
        if not timestamps or not quotes:
            return []
        quote = quotes[0]
        adjusted_rows = indicators.get("adjclose") or []
        adjusted_values = adjusted_rows[0].get("adjclose", []) if adjusted_rows else []
        timezone_name, close_time = self._market_closes.get(market.upper(), ("UTC", time(23, 59)))
        timezone = ZoneInfo(timezone_name)
        ingested_at = datetime.now(UTC)
        bars: list[MarketBar] = []
        for index, timestamp in enumerate(timestamps):
            values = {
                name: (quote.get(name) or [None] * len(timestamps))[index]
                for name in ("open", "high", "low", "close", "volume")
            }
            prices = {
                name: self._decimal_or_none(values[name])
                for name in ("open", "high", "low", "close")
            }
            if any(value is None for value in prices.values()):
                continue
            assert prices["open"] is not None
            assert prices["high"] is not None
            assert prices["low"] is not None
            assert prices["close"] is not None
            session_date = datetime.fromtimestamp(timestamp, UTC).astimezone(timezone).date()
            event_time = datetime.combine(session_date, close_time, tzinfo=timezone)
            available_time = event_time + timedelta(minutes=15)
            if available_time > ingested_at:
                continue
            open_price = prices["open"]
            high_price = prices["high"]
            low_price = prices["low"]
            close_price = prices["close"]
            adjusted = adjusted_values[index] if index < len(adjusted_values) else None
            bars.append(
                MarketBar(
                    symbol=symbol,
                    market=market.upper(),
                    interval="1d",
                    event_time=event_time,
                    available_time=available_time,
                    ingested_at=ingested_at,
                    open=open_price,
                    high=max(high_price, open_price, close_price),
                    low=min(low_price, open_price, close_price),
                    close=close_price,
                    adjusted_close=self._decimal_or_none(adjusted),
                    volume=max(0, int(self._finite_float(values["volume"]) or 0)),
                    source=self.name,
                )
            )
        return bars
