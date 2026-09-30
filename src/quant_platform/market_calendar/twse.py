from __future__ import annotations

import json
import re
import time as clock
from datetime import date
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from quant_platform.market_calendar.core import MarketClosure

TWSE_HOLIDAY_SCHEDULE_URL = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule"

# The official list mixes closures with informational rows for the first and
# last sessions around long holidays; those rows are trading days.
_TRADING_DAY_MARKERS = ("開始交易", "最後交易")
_MARKUP = re.compile(r"<br\s*/?>", re.IGNORECASE)


def _clean(value: object) -> str:
    return " ".join(_MARKUP.sub(" ", str(value or "")).split())


def parse_twse_holiday_schedule(payload: object, year: int) -> list[MarketClosure] | None:
    """Return the year's closures, or ``None`` when TWSE has not published it.

    TWSE answers ``stat=ok`` with zero rows for unpublished or archived
    years, so an empty list must not be mistaken for "no holidays".
    """
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
        raise ValueError("證交所開休市日期回應格式不符")
    rows = [row for row in payload.get("data") or [] if isinstance(row, list) and len(row) >= 2]
    if not rows:
        return None
    closures: dict[date, MarketClosure] = {}
    for row in rows:
        day = date.fromisoformat(str(row[0]).strip())
        if day.year != year:
            continue
        name = _clean(row[1])
        if any(marker in name for marker in _TRADING_DAY_MARKERS):
            continue
        description = _clean(row[2]) if len(row) > 2 else ""
        closures[day] = MarketClosure(day, name, description, "twse")
    return sorted(closures.values(), key=lambda item: item.day)


class TwseHolidayScheduleClient:
    """Keyless client for the TWSE market open/close schedule (2021 onward)."""

    def __init__(self, timeout: int = 20, url: str = TWSE_HOLIDAY_SCHEDULE_URL) -> None:
        self._timeout = timeout
        self._url = url

    def source_url(self, year: int) -> str:
        return f"{self._url}?{urlencode({'date': f'{year}0101', 'response': 'json'})}"

    def fetch_year(self, year: int) -> list[MarketClosure] | None:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request = Request(
                    self.source_url(year),
                    headers={"Accept": "application/json", "User-Agent": "StockResearch/1.0"},
                )
                with urlopen(request, timeout=self._timeout) as response:
                    payload = json.loads(response.read().decode("utf-8-sig"))
                return parse_twse_holiday_schedule(payload, year)
            except Exception as exc:  # noqa: BLE001 - bounded provider retry
                last_error = exc
                if attempt < 2:
                    clock.sleep(1.5 * (2**attempt))
        raise RuntimeError(f"證交所 {year} 年開休市日期下載失敗：{last_error}")
