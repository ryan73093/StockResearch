"""2026-11-27: the New York exchange's holidays, from its published rules.

The US calendar was weekdays only, so the 06:30 Taipei run on the day after Thanksgiving expected a session that never
took place and failed (「US 行情尚未更新到 2026-11-26」), and would have after every US holiday. Early closes (13:00) are
ordinary sessions here. A closure the exchange announces for one day (a day of mourning) is listed in ``ONE_OFF``;
until it is, that day's run fails as before.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

from quant_platform.market_calendar.core import MarketClosure, TradingCalendar

YEARS = range(2000, 2101)
ONE_OFF = (
    (date(2012, 10, 29), "颶風 Sandy"),
    (date(2012, 10, 30), "颶風 Sandy"),
    (date(2018, 12, 5), "美國前總統老布希國殤日"),
    (date(2025, 1, 9), "美國前總統卡特國殤日"),
)


def _nth_weekday(year: int, month: int, weekday: int, number: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (number - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Easter Sunday (the anonymous Gregorian computus)."""
    golden, century, rest = year % 19, year // 100, year % 100
    leap_centuries, century_rest = divmod(century, 4)
    correction = (century - (century + 8) // 25 + 1) // 3
    epact = (19 * golden + century - leap_centuries - correction + 15) % 30
    leaps, years_rest = divmod(rest, 4)
    weekday = (32 + 2 * century_rest + 2 * leaps - epact - years_rest) % 7
    shift = (golden + 11 * epact + 22 * weekday) // 451
    month, day = divmod(epact + weekday - 7 * shift + 114, 31)
    return date(year, month, day + 1)


def _observed(day: date) -> date:
    """A holiday on a Saturday is taken the Friday before, one on a Sunday the Monday after."""
    return day - timedelta(days=1) if day.weekday() == 5 else day + timedelta(days=1) if day.weekday() == 6 else day


def holidays(year: int) -> list[tuple[date, str]]:
    found: list[tuple[date, str]] = []
    if date(year, 1, 1).weekday() != 5:      # on a Saturday the exchange stays open on 31 December
        found.append((_observed(date(year, 1, 1)), "美國元旦"))
    found += [(_nth_weekday(year, 1, 0, 3), "金恩紀念日"), (_nth_weekday(year, 2, 0, 3), "美國總統日"),
              (_easter(year) - timedelta(days=2), "耶穌受難日"), (_last_weekday(year, 5, 0), "美國陣亡將士紀念日")]
    if year >= 2022:
        found.append((_observed(date(year, 6, 19)), "美國六月節"))
    found += [(_observed(date(year, 7, 4)), "美國獨立紀念日"), (_nth_weekday(year, 9, 0, 1), "美國勞動節"),
              (_nth_weekday(year, 11, 3, 4), "感恩節"), (_observed(date(year, 12, 25)), "耶誕節")]
    return found


@lru_cache(maxsize=1)
def nyse_calendar() -> TradingCalendar:
    closures = [MarketClosure(day, name, source="nyse") for year in YEARS for day, name in holidays(year)]
    closures += [MarketClosure(day, name, source="nyse") for day, name in ONE_OFF]
    return TradingCalendar("US", closures, covered_years=YEARS)
