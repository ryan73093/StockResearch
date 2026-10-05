"""One price store for the app (roadmap S9-W03, owner 2026-10-04: 股價不要存兩份).

The research history is the source of daily closes: the ETF series under history/daily (official, built
nightly) and every listed stock under history/stocks/twse and tpex (official all-market quotes, appended nightly).
Today's close comes from TWSE's all-market quote table (MI_INDEX), which the close job fetches from
13:49 (first seen 19–21 minutes after the close, S1-W05) into the same raw cache the nightly append reads.

``ResearchPrices`` answers the two questions the app asks — a symbol's daily closes (Today's orders, the
holdings history) and the latest closes of some symbols (holdings value) — with the interface of the
legacy market-bar repository, so the services keep working. ``FallbackPrices`` uses it first and the
legacy SQLite bars only when the research store has nothing newer for a symbol (TPEx stocks, or a day
the close job missed) while the legacy price step still runs; once verified, the legacy step stops.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow.compute as pc
import pyarrow.parquet as pq

from quant_platform.research.history.official import number

TAIPEI = ZoneInfo("Asia/Taipei")
CLOSE = time(13, 30)
PUBLISHED = time(13, 50)


@dataclass(frozen=True)
class Bar:
    symbol: str
    event_time: datetime          # the session's close, 13:30 Taipei
    available_time: datetime      # when the official close is public
    close: float
    source: str = "twse_official"
    open: float | None = None     # S9-W05: the paper account fills at the next session's open
    volume: float = 0.0           # shares traded

    def __post_init__(self) -> None:
        if self.open is None:
            object.__setattr__(self, "open", self.close)


def rows_from_payload(payload: object) -> dict[str, tuple[float | None, float, float]]:
    """Every code's (open, close, shares traded) in a TWSE MI_INDEX all-market payload."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return {}
    output: dict[str, tuple[float | None, float, float]] = {}
    for table in payload.get("tables") or []:
        fields = (table or {}).get("fields") or []
        if fields[:2] != ["證券代號", "證券名稱"]:
            continue
        for item in table.get("data") or []:
            if len(item) < 9:
                continue
            close = number(item[8])
            if close is not None and close > 0:
                output[str(item[0]).strip()] = (number(item[5]), close, number(item[2]) or 0.0)
    return output


def quotes_from_payload(payload: object) -> dict[str, float]:
    """Every code's close in a TWSE MI_INDEX all-market payload (stocks and ETFs alike)."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return {}
    output: dict[str, float] = {}
    for table in payload.get("tables") or []:
        fields = (table or {}).get("fields") or []
        if fields[:2] != ["證券代號", "證券名稱"]:
            continue
        for item in table.get("data") or []:
            if len(item) < 9:
                continue
            close = number(item[8])
            if close is not None and close > 0:
                output[str(item[0]).strip()] = close
    return output


def _at(day: date, moment: time) -> datetime:
    return datetime.combine(day, moment, TAIPEI)


class ResearchPrices:
    def __init__(self, history: str | Path) -> None:
        self._history = Path(history)
        self._cache: dict[tuple, dict[date, float]] = {}

    # --- sources ----------------------------------------------------------------------------
    def _etf(self, code: str) -> dict[date, float]:
        path = self._history / "daily" / f"{code}.parquet"
        if not path.is_file():
            return {}
        key = ("etf", code, path.stat().st_mtime)
        if key not in self._cache:
            table = pq.read_table(path, columns=["date", "close"])
            self._cache[key] = {day: float(close) for day, close in zip(table["date"].to_pylist(), table["close"].to_pylist())
                                if close}
        return self._cache[key]

    def _stock(self, code: str, exchange: str = "twse") -> dict[date, float]:
        folder = self._history / "stocks" / exchange
        files = sorted(folder.glob("*.parquet")) if folder.is_dir() else []
        key = ("stock", exchange, code, tuple(path.stat().st_mtime for path in files))
        if key not in self._cache:
            series: dict[date, float] = {}
            for path in files:
                table = pq.read_table(path, columns=["date", "code", "close"], filters=[("code", "=", code)])
                if table.num_rows:
                    table = table.filter(pc.is_valid(table["close"]))
                    series.update(zip(table["date"].to_pylist(), map(float, table["close"].to_pylist())))
            self._cache[key] = series
        return self._cache[key]

    def today_quotes(self, day: date) -> dict[str, float]:
        """The close job's cached MI_INDEX payload for ``day`` (empty before it is published)."""
        return {code: row[1] for code, row in self.today_rows(day).items()}

    def today_rows(self, day: date) -> dict[str, tuple[float | None, float, float]]:
        path = self._history / "raw" / "twse_stock_all" / f"{day:%Y}" / f"{day:%Y%m%d}.json"
        if not path.is_file():
            return {}
        key = ("raw", day, path.stat().st_mtime)
        if key not in self._cache:
            try:
                self._cache[key] = rows_from_payload(json.loads(path.read_text(encoding="utf-8")))
            except ValueError:
                self._cache[key] = {}
        return self._cache[key]

    def _ohlcv(self, code: str, exchange: str) -> dict[date, tuple[float | None, float]]:
        """(open, shares traded) by session: the ETF series, else the stock year files."""
        path = self._history / "daily" / f"{code}.parquet"
        files = [path] if path.is_file() else sorted((self._history / "stocks" / exchange).glob("*.parquet"))[-2:]
        key = ("ohlcv", exchange, code, tuple(item.stat().st_mtime for item in files))
        if key not in self._cache:
            rows: dict[date, tuple[float | None, float]] = {}
            for item in files:
                filters = None if item == path else [("code", "=", code)]
                table = pq.read_table(item, columns=["date", "open", "volume"], filters=filters)
                for day, open_, volume in zip(table["date"].to_pylist(), table["open"].to_pylist(),
                                              table["volume"].to_pylist(), strict=True):
                    rows[day] = (float(open_) if open_ else None, float(volume or 0))
            self._cache[key] = rows
        return self._cache[key]

    # --- the repository interface -------------------------------------------------------------
    def history(self, symbol: str, as_of: datetime | None = None) -> list[tuple[date, float]]:
        code, _, suffix = symbol.upper().partition(".")
        series = dict(self._etf(code) or self._stock(code, "tpex" if suffix == "TWO" else "twse"))
        moment = (as_of or datetime.now(TAIPEI)).astimezone(TAIPEI)
        today = moment.date()
        if today not in series and suffix != "TWO" and moment.time() >= PUBLISHED:
            close = self.today_quotes(today).get(code)
            if close and series:
                series[today] = close
        return sorted((day, close) for day, close in series.items()
                      if day < today or (day == today and moment.time() >= PUBLISHED))

    def list_bars(self, symbol: str, interval: str = "1d", source: str | None = None,
                  as_of: datetime | None = None) -> list[Bar]:
        code, _, suffix = symbol.upper().partition(".")
        closes = self.history(symbol, as_of)
        if not closes:
            return []
        rows = self._ohlcv(code, "tpex" if suffix == "TWO" else "twse")
        bars = []
        for day, close in closes:
            open_, volume = rows.get(day) or (self.today_rows(day).get(code) or (None, close, 0.0))[::2]
            bars.append(Bar(symbol.upper(), _at(day, CLOSE), _at(day, PUBLISHED), close, open=open_, volume=volume))
        return bars

    def latest_closes(self, symbols: list[str], as_of: datetime | None = None) -> dict[str, float]:
        output = {}
        for symbol in symbols:
            rows = self.history(symbol, as_of)
            if rows:
                output[symbol.upper()] = rows[-1][1]
        return output

    def latest_market_date(self, as_of: datetime | None = None) -> date | None:
        rows = self.history("0050.TW", as_of)
        return rows[-1][0] if rows else None


class FallbackPrices:
    """The research store first; the legacy SQLite bars only where it has nothing as recent."""

    def __init__(self, primary: ResearchPrices, secondary) -> None:
        self.primary, self.secondary = primary, secondary

    def list_bars(self, symbol: str, interval: str = "1d", source: str | None = None, as_of: datetime | None = None):
        bars = self.primary.list_bars(symbol, as_of=as_of)
        if self.secondary is None:
            return bars
        legacy = self.secondary.list_bars(symbol, as_of=as_of)
        if not bars:
            return legacy
        if legacy and legacy[-1].event_time.astimezone(TAIPEI).date() > bars[-1].event_time.date():
            return legacy
        return bars

    def latest_closes(self, symbols: list[str], as_of: datetime | None = None) -> dict[str, float]:
        output = {}
        for symbol in symbols:
            bars = self.list_bars(symbol, as_of=as_of)
            if bars:
                output[symbol.upper()] = float(bars[-1].close)
        return output

    def history(self, symbol: str) -> list[tuple[date, float]]:
        return [(bar.event_time.astimezone(TAIPEI).date(), float(bar.close)) for bar in self.list_bars(symbol)]

    def latest_market_date(self, as_of: datetime | None = None) -> date | None:
        bars = self.list_bars("0050.TW", as_of=as_of)
        return bars[-1].event_time.astimezone(TAIPEI).date() if bars else None


def fetch_today_close(history: str | Path, today: date, client=None) -> int:
    """The close job (trading days, every minute from 13:49 until it has the table): fetch TWSE's
    all-market quotes for today into the raw cache. Returns the number of codes with a close (0 while
    TWSE has not published)."""
    from quant_platform.research.history.official import OfficialHistoryClient
    from quant_platform.research.history.stocks import twse_all_day

    base = Path(history)
    prices = ResearchPrices(base)
    if len(prices.today_quotes(today)) > 500:
        return len(prices.today_quotes(today))
    client = client or OfficialHistoryClient(base / "raw")
    payload = twse_all_day(client, today)
    return len(quotes_from_payload(payload))
