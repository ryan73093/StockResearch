from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

# Longest real closure (Lunar New Year plus settlement-only days) is under two
# weeks; a longer walk means corrupt calendar data rather than a holiday.
_MAX_CONSECUTIVE_CLOSED_DAYS = 31


@dataclass(frozen=True, slots=True)
class MarketClosure:
    day: date
    name: str
    description: str = ""
    source: str = "twse"


class TradingCalendar:
    """Exchange sessions for one market.

    Weekends are always closed. A holiday-aware calendar also closes the
    listed exchange holidays; years outside ``covered_years`` fall back to the
    weekday rule and are reported through ``covers`` so callers can warn.
    """

    def __init__(
        self,
        market: str,
        closures: Iterable[MarketClosure] = (),
        covered_years: Iterable[int] = (),
        holiday_aware: bool = True,
    ) -> None:
        self.market = market.upper()
        self.holiday_aware = holiday_aware
        self._closures = {item.day: item for item in closures}
        self._covered_years = frozenset(covered_years)

    @property
    def covered_years(self) -> frozenset[int]:
        return self._covered_years

    def covers(self, day: date) -> bool:
        return not self.holiday_aware or day.year in self._covered_years

    def closure(self, day: date) -> MarketClosure | None:
        listed = self._closures.get(day)
        if listed is not None:
            return listed
        if day.weekday() >= 5:
            return MarketClosure(day, "週末", source="weekday")
        return None

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self._closures

    def previous_trading_day(self, day: date, inclusive: bool = True) -> date:
        candidate = day if inclusive else day - timedelta(days=1)
        for _ in range(_MAX_CONSECUTIVE_CLOSED_DAYS):
            if self.is_trading_day(candidate):
                return candidate
            candidate -= timedelta(days=1)
        raise ValueError(f"{self.market} 交易日曆在 {day} 之前連續超過 31 天休市，資料可能有誤")

    def next_trading_day(self, day: date, inclusive: bool = False) -> date:
        candidate = day if inclusive else day + timedelta(days=1)
        for _ in range(_MAX_CONSECUTIVE_CLOSED_DAYS):
            if self.is_trading_day(candidate):
                return candidate
            candidate += timedelta(days=1)
        raise ValueError(f"{self.market} 交易日曆在 {day} 之後連續超過 31 天休市，資料可能有誤")

    def sessions_after(self, start: date, end: date) -> int:
        """Count trading sessions in the half-open range ``(start, end]``."""
        if end <= start:
            return 0
        count = 0
        cursor = start + timedelta(days=1)
        while cursor <= end:
            if self.is_trading_day(cursor):
                count += 1
            cursor += timedelta(days=1)
        return count


def weekday_calendar(market: str) -> TradingCalendar:
    """Calendar without exchange holidays; used for markets not yet modelled."""
    return TradingCalendar(market, holiday_aware=False)
