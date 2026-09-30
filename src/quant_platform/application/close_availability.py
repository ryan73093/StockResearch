"""When does each close-data source publish the day's data? (S1-W05)

On Taiwan trading days the worker probes once a minute between 13:30 and
14:45 and records, per source, the first time the day's data is visible:

- ``twse_close``: TWSE MI_INDEX (ALLBUT0999) quote table for today, the
  official listed close used by the daily workflow.
- ``tpex_close``: TPEx OpenAPI daily close quotes dated today.
- ``yahoo_0050``: Yahoo's 0050.TW daily candle for today equals the official
  TWSE close (a candle exists all day; this measures when it is final).
- ``twse_odd_lot``: TWSE after-hours odd-lot report (TWT53U) for today, i.e.
  the 14:30 matching results.

Sightings are appended to ``instance/close_availability.jsonl``. Five trading
days of sightings decide how early the decision snapshot can start.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TAIPEI = ZoneInfo("Asia/Taipei")
MARKET_CLOSE = time(13, 30)
WINDOW_END = time(14, 45)
FILE_NAME = "close_availability.jsonl"
SOURCES = {
    "twse_close": "證交所收盤（MI_INDEX）",
    "tpex_close": "櫃買收盤（OpenAPI）",
    "yahoo_0050": "Yahoo 0050 收盤與官方一致",
    "twse_odd_lot": "證交所盤後零股成交（TWT53U）",
}
TWSE_CLOSE_URL = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
TPEX_CLOSE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/0050.TW"
TWSE_ODD_LOT_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/TWT53U"

FetchJson = Callable[[str], object]


@dataclass(frozen=True, slots=True)
class SourceSighting:
    day: str
    source: str
    first_seen: str
    minutes_after_close: float
    rows: int
    value: float | None = None


def _fetch_json(url: str) -> object:
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 StockResearch/1.0"},
    )
    with urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8-sig"))


def _number(value: object) -> float | None:
    text = str(value or "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


class CloseAvailabilityProbe:
    def __init__(
        self,
        instance_dir: str | Path,
        calendar_store: object,
        fetch_json: FetchJson | None = None,
    ) -> None:
        self._path = Path(instance_dir) / FILE_NAME
        self._calendar_store = calendar_store
        self._fetch = fetch_json or _fetch_json

    @property
    def path(self) -> Path:
        return self._path

    def run(self, now: datetime | None = None) -> list[SourceSighting]:
        """One probe tick; returns the sources first seen in this tick."""
        local = (now or datetime.now(UTC)).astimezone(TAIPEI)
        day = local.date()
        if not (MARKET_CLOSE <= local.time() <= WINDOW_END):
            return []
        if not self._calendar_store.calendar("TW").is_trading_day(day):
            return []
        seen = self.sightings().get(day.isoformat(), {})
        checks: dict[str, Callable[[date, dict[str, SourceSighting]], tuple[int, float | None] | None]] = {
            "twse_close": self._twse_close,
            "tpex_close": self._tpex_close,
            "yahoo_0050": self._yahoo_0050,
            "twse_odd_lot": self._twse_odd_lot,
        }
        found: list[SourceSighting] = []
        for source, check in checks.items():
            if source in seen:
                continue
            try:
                result = check(day, seen)
            except Exception as exc:  # noqa: BLE001 - a probe must never break the worker
                logger.info("Close availability probe %s failed: %s", source, exc)
                continue
            if result is None:
                continue
            rows, value = result
            close_at = datetime.combine(day, MARKET_CLOSE, tzinfo=TAIPEI)
            sighting = SourceSighting(
                day=day.isoformat(),
                source=source,
                first_seen=local.isoformat(timespec="seconds"),
                minutes_after_close=round((local - close_at).total_seconds() / 60, 1),
                rows=rows,
                value=value,
            )
            seen = {**seen, source: sighting}
            found.append(sighting)
        if found:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                for item in found:
                    handle.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
        return found

    def sightings(self) -> dict[str, dict[str, SourceSighting]]:
        """First sighting per day and source, oldest day first."""
        if not self._path.is_file():
            return {}
        result: dict[str, dict[str, SourceSighting]] = {}
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = SourceSighting(**json.loads(line))
            except (TypeError, ValueError):
                continue
            result.setdefault(item.day, {}).setdefault(item.source, item)
        return dict(sorted(result.items()))

    def _twse_close(self, day: date, _seen) -> tuple[int, float | None] | None:
        query = urlencode({"response": "json", "date": f"{day:%Y%m%d}", "type": "ALLBUT0999"})
        payload = self._fetch(f"{TWSE_CLOSE_URL}?{query}")
        if not isinstance(payload, dict) or payload.get("stat") != "OK":
            return None
        if str(payload.get("date")) != f"{day:%Y%m%d}":
            return None
        for table in payload.get("tables") or []:
            if isinstance(table, dict) and (table.get("fields") or [])[:2] == ["證券代號", "證券名稱"]:
                rows = [row for row in table.get("data") or [] if isinstance(row, list)]
                if not rows:
                    return None
                reference = next(
                    (_number(row[8]) for row in rows if len(row) > 8 and str(row[0]).strip() == "0050"),
                    None,
                )
                return len(rows), reference
        return None

    def _tpex_close(self, day: date, _seen) -> tuple[int, float | None] | None:
        payload = self._fetch(TPEX_CLOSE_URL)
        if not isinstance(payload, list):
            return None
        roc = f"{day.year - 1911:03d}{day:%m%d}"
        rows = [
            row for row in payload
            if isinstance(row, dict) and str(row.get("Date") or "").replace("/", "") == roc
        ]
        return (len(rows), None) if rows else None

    def _yahoo_0050(self, day: date, seen: dict[str, SourceSighting]) -> tuple[int, float | None] | None:
        official = seen.get("twse_close")
        if official is None or official.value is None:
            return None  # needs the official close to compare against
        payload = self._fetch(f"{YAHOO_CHART_URL}?{urlencode({'range': '5d', 'interval': '1d'})}")
        result = ((payload or {}).get("chart") or {}).get("result") or []
        if not result:
            return None
        stamps = result[0].get("timestamp") or []
        closes = (((result[0].get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
        for stamp, close in zip(stamps, closes):
            if close is None:
                continue
            if datetime.fromtimestamp(stamp, TAIPEI).date() == day and abs(close - official.value) < 0.005:
                return 1, round(float(close), 4)
        return None

    def _twse_odd_lot(self, day: date, _seen) -> tuple[int, float | None] | None:
        payload = self._fetch(f"{TWSE_ODD_LOT_URL}?{urlencode({'date': f'{day:%Y%m%d}', 'response': 'json'})}")
        if not isinstance(payload, dict) or payload.get("stat") != "OK":
            return None
        if str(payload.get("date")) != f"{day:%Y%m%d}":
            return None
        rows = [row for row in payload.get("data") or [] if isinstance(row, list)]
        return (len(rows), None) if rows else None


def recent_table(
    probe: CloseAvailabilityProbe, days: int = 5
) -> list[dict[str, object]]:
    """Rows for the system page: one per day, minutes after 13:30 per source."""
    history = probe.sightings()
    rows = []
    for day in list(history)[-days:][::-1]:
        seen = history[day]
        rows.append({
            "day": day,
            "sources": {
                source: (
                    f"{seen[source].first_seen[11:16]}（+{seen[source].minutes_after_close:g} 分）"
                    if source in seen else "—"
                )
                for source in SOURCES
            },
        })
    return rows
