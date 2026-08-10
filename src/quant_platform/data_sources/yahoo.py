from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
import json
from math import isnan
from pathlib import Path
from tempfile import gettempdir
import threading
import time as clock
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from quant_platform.domain.entities import MarketBar


class YahooFinanceProvider:
    """Yahoo daily-bar adapter.

    Yahoo provides session dates rather than authoritative publication timestamps.
    We conservatively mark a daily bar available 15 minutes after the regular close.
    Exchange calendars will replace this small market mapping in the scheduler phase.
    """

    name = "yahoo_finance"
    _market_closes = {
        "US": ("America/New_York", time(16, 0)),
        "TW": ("Asia/Taipei", time(13, 30)),
    }
    _request_lock = threading.Lock()
    _last_request_at = 0.0

    def fetch_daily_bars(
        self, symbol: str, market: str, start: datetime, end: datetime
    ) -> list[MarketBar]:
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
                    history = yf.Ticker(symbol).history(
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
            except Exception as exc:
                last_error = exc
            if attempt < 2:
                clock.sleep(2 ** attempt)
        if history is None or history.empty:
            # yfinance's cookie/crumb route can temporarily return an empty
            # frame while Yahoo's public chart route remains available. Use a
            # single range request as a bounded fallback; it avoids month-by-
            # month retry storms and still returns adjusted close and splits.
            return self._fetch_chart_bars(symbol, market, start, end, last_error)

        timezone_name, close_time = self._market_closes.get(
            market.upper(), ("UTC", time(23, 59))
        )
        timezone = ZoneInfo(timezone_name)
        ingested_at = datetime.now(UTC)
        bars: list[MarketBar] = []
        for index, row in history.iterrows():
            session_date = index.to_pydatetime().date()
            event_time = datetime.combine(session_date, close_time, tzinfo=timezone)
            available_time = event_time + timedelta(minutes=15)
            if available_time > ingested_at:
                # Yahoo can expose a still-forming daily candle before the session closes.
                continue
            adjusted = row.get("Adj Close")
            adjusted_close = (
                None if adjusted is None or isnan(float(adjusted)) else Decimal(str(adjusted))
            )
            open_price = Decimal(str(row["Open"]))
            close_price = Decimal(str(row["Close"]))
            # Yahoo FX candles occasionally publish a rounded high/low a few
            # ticks inside open/close. Preserve the observed endpoints while
            # restoring the OHLC invariant required by the quality gate.
            high_price = max(Decimal(str(row["High"])), open_price, close_price)
            low_price = min(Decimal(str(row["Low"])), open_price, close_price)
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
                    volume=int(row["Volume"]),
                    source=self.name,
                )
            )
        return bars

    def _fetch_chart_bars(
        self,
        symbol: str,
        market: str,
        start: datetime,
        end: datetime,
        previous_error: Exception | None = None,
    ) -> list[MarketBar]:
        query = urlencode({
            "period1": int(start.timestamp()),
            "period2": int(end.timestamp()),
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "true",
        })
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
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    clock.sleep(2 ** attempt)
        if payload is None:
            raise RuntimeError(
                f"Yahoo chart download failed after retry: {last_error}"
            )
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
        timezone_name, close_time = self._market_closes.get(
            market.upper(), ("UTC", time(23, 59))
        )
        timezone = ZoneInfo(timezone_name)
        ingested_at = datetime.now(UTC)
        bars: list[MarketBar] = []
        for index, timestamp in enumerate(timestamps):
            values = {
                name: (quote.get(name) or [None] * len(timestamps))[index]
                for name in ("open", "high", "low", "close", "volume")
            }
            if any(values[name] is None for name in ("open", "high", "low", "close")):
                continue
            session_date = datetime.fromtimestamp(timestamp, UTC).astimezone(timezone).date()
            event_time = datetime.combine(session_date, close_time, tzinfo=timezone)
            available_time = event_time + timedelta(minutes=15)
            if available_time > ingested_at:
                continue
            open_price = Decimal(str(values["open"]))
            close_price = Decimal(str(values["close"]))
            adjusted = adjusted_values[index] if index < len(adjusted_values) else None
            bars.append(MarketBar(
                symbol=symbol,
                market=market.upper(),
                interval="1d",
                event_time=event_time,
                available_time=available_time,
                ingested_at=ingested_at,
                open=open_price,
                high=max(Decimal(str(values["high"])), open_price, close_price),
                low=min(Decimal(str(values["low"])), open_price, close_price),
                close=close_price,
                adjusted_close=(Decimal(str(adjusted)) if adjusted is not None else None),
                volume=int(values["volume"] or 0),
                source=self.name,
            ))
        return bars
