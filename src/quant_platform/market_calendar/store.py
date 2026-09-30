from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, date, datetime, timedelta
from importlib import resources
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from quant_platform.market_calendar.core import MarketClosure, TradingCalendar, weekday_calendar

logger = logging.getLogger(__name__)

TAIPEI = ZoneInfo("Asia/Taipei")
_FAILED_REFRESH_RETRY = timedelta(hours=6)


class HolidayScheduleClient(Protocol):
    def source_url(self, year: int) -> str: ...

    def fetch_year(self, year: int) -> list[MarketClosure] | None: ...


def closure_records(closures: list[MarketClosure]) -> list[dict[str, str]]:
    return [
        {"date": item.day.isoformat(), "name": item.name, "description": item.description}
        for item in closures
    ]


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError):
        logger.exception("Market calendar file %s is unreadable; ignoring it", path)
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _years_from(payload: dict, source: str) -> dict[int, list[MarketClosure]]:
    years: dict[int, list[MarketClosure]] = {}
    for year_text, entry in (payload.get("years") or {}).items():
        try:
            year = int(year_text)
            years[year] = [
                MarketClosure(
                    date.fromisoformat(item["date"]),
                    str(item.get("name", "")),
                    str(item.get("description", "")),
                    source,
                )
                for item in entry.get("closures") or []
            ]
        except (KeyError, TypeError, ValueError):
            logger.warning("Skipping malformed market calendar year %r", year_text)
    return years


class MarketCalendarStore:
    """Taiwan exchange calendar assembled from three layers.

    1. Packaged TWSE schedule committed with the code.
    2. ``instance/market_calendar/twse_cache.json``: newer official fetches.
    3. ``instance/market_calendar/manual_closures.json``: unscheduled closures
       such as typhoon days, recorded by the operator.

    Other markets use the weekday rule until their holidays are modelled.
    """

    def __init__(
        self,
        instance_dir: str | Path | None = "instance",
        client: HolidayScheduleClient | None = None,
        packaged_path: Path | None = None,
    ) -> None:
        base = Path(instance_dir) / "market_calendar" if instance_dir is not None else None
        self._cache_path = base / "twse_cache.json" if base else None
        self._manual_path = base / "manual_closures.json" if base else None
        self._packaged_path = packaged_path
        self._client = client
        self._lock = threading.Lock()
        self._signature: tuple[float, float] | None = None
        self._taiwan: TradingCalendar | None = None
        self._last_refresh_attempt: datetime | None = None
        self._last_refresh_ok: date | None = None

    def _packaged_payload(self) -> dict:
        if self._packaged_path is not None:
            return _read_json(self._packaged_path)
        resource = resources.files("quant_platform.market_calendar").joinpath(
            "data", "twse_closures.json"
        )
        try:
            return json.loads(resource.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            logger.exception("Packaged TWSE calendar is missing or unreadable")
            return {}

    @staticmethod
    def _mtime(path: Path | None) -> float:
        try:
            return path.stat().st_mtime if path else 0.0
        except OSError:
            return 0.0

    def _build_taiwan(self) -> TradingCalendar:
        years = _years_from(self._packaged_payload(), "twse")
        if self._cache_path is not None:
            years.update(_years_from(_read_json(self._cache_path), "twse"))
        closures = [item for values in years.values() for item in values]
        for item in self.manual_closures():
            closures.append(item)
        return TradingCalendar("TW", closures, covered_years=years.keys())

    def calendar(self, market: str) -> TradingCalendar:
        if market.upper() != "TW":
            return weekday_calendar(market)
        signature = (self._mtime(self._cache_path), self._mtime(self._manual_path))
        with self._lock:
            if self._taiwan is None or signature != self._signature:
                self._taiwan = self._build_taiwan()
                self._signature = signature
            return self._taiwan

    def is_trading_day(self, market: str, day: date) -> bool:
        return self.calendar(market).is_trading_day(day)

    def manual_closures(self) -> list[MarketClosure]:
        if self._manual_path is None:
            return []
        closures = []
        for item in _read_json(self._manual_path).get("closures") or []:
            try:
                closures.append(MarketClosure(
                    date.fromisoformat(item["date"]),
                    str(item.get("name") or "臨時休市"),
                    str(item.get("note") or ""),
                    "manual",
                ))
            except (KeyError, TypeError, ValueError):
                logger.warning("Skipping malformed manual closure %r", item)
        return sorted(closures, key=lambda item: item.day)

    def add_manual_closure(self, day: date, name: str, note: str = "") -> MarketClosure:
        if self._manual_path is None:
            raise RuntimeError("未設定 instance 目錄，無法保存人工休市")
        if not name.strip():
            raise ValueError("請填寫休市原因")
        payload = _read_json(self._manual_path)
        records = [
            item for item in payload.get("closures") or []
            if item.get("date") != day.isoformat()
        ]
        records.append({
            "date": day.isoformat(),
            "name": name.strip(),
            "note": note.strip(),
            "recorded_at": datetime.now(UTC).isoformat(),
        })
        _write_json(self._manual_path, {"closures": sorted(records, key=lambda item: item["date"])})
        return MarketClosure(day, name.strip(), note.strip(), "manual")

    def remove_manual_closure(self, day: date) -> bool:
        if self._manual_path is None:
            return False
        payload = _read_json(self._manual_path)
        records = payload.get("closures") or []
        kept = [item for item in records if item.get("date") != day.isoformat()]
        if len(kept) == len(records):
            return False
        _write_json(self._manual_path, {"closures": kept})
        return True

    def refresh(self, years: list[int], now: datetime | None = None) -> dict[int, int | None]:
        """Fetch official schedules and store published years in the cache.

        Returns closure counts per year; ``None`` marks a year TWSE has not
        published yet. Unpublished years never overwrite cached data.
        """
        if self._client is None or self._cache_path is None:
            raise RuntimeError("未設定證交所日曆來源或 instance 目錄")
        fetched_at = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        payload = _read_json(self._cache_path)
        stored = payload.setdefault("years", {})
        results: dict[int, int | None] = {}
        for year in sorted(set(years)):
            closures = self._client.fetch_year(year)
            results[year] = None if closures is None else len(closures)
            if closures is not None:
                stored[str(year)] = {
                    "fetched_at": fetched_at,
                    "source_url": self._client.source_url(year),
                    "closures": closure_records(closures),
                }
        _write_json(self._cache_path, payload)
        return results

    def refresh_if_due(self, now: datetime | None = None) -> dict[int, int | None] | None:
        """Refresh at most once per Taipei day; failed attempts back off six hours.

        Fetches the current year, plus next year from October when TWSE
        usually publishes it. Errors are logged and never raised so a
        calendar outage cannot stop the daily workflow.
        """
        if self._client is None or self._cache_path is None:
            return None
        current = (now or datetime.now(UTC)).astimezone(TAIPEI)
        today = current.date()
        if self._last_refresh_ok == today:
            return None
        if (
            self._last_refresh_attempt is not None
            and current - self._last_refresh_attempt < _FAILED_REFRESH_RETRY
        ):
            return None
        self._last_refresh_attempt = current
        years = [today.year]
        if today.month >= 10:
            years.append(today.year + 1)
        try:
            results = self.refresh(years, current)
        except Exception:
            logger.exception("TWSE market calendar refresh failed; keeping existing calendar")
            return None
        self._last_refresh_ok = today
        self._last_refresh_attempt = None
        return results

    def status(self, today: date) -> dict[str, object]:
        calendar = self.calendar("TW")
        cache = _read_json(self._cache_path) if self._cache_path else {}
        return {
            "covered_years": sorted(calendar.covered_years),
            "current_year_covered": calendar.covers(today),
            "manual_closures": [
                {"date": item.day.isoformat(), "name": item.name, "note": item.description}
                for item in self.manual_closures()
            ],
            "cache_fetched_at": {
                year: entry.get("fetched_at")
                for year, entry in (cache.get("years") or {}).items()
            },
        }


_default_store: MarketCalendarStore | None = None
_default_lock = threading.Lock()


def default_market_calendar() -> MarketCalendarStore:
    """Process-wide store for callers constructed without the container."""
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = MarketCalendarStore("instance")
        return _default_store
