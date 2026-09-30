"""Contribution plans: when the investor's money arrives (S3-W04)."""

from __future__ import annotations

import bisect
import calendar
from dataclasses import asdict, dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class ContributionPlan:
    monthly_amount: float = 10_000.0
    day_of_month: int = 5          # salary day; next session when it is closed
    annual_growth: float = 0.0     # stepwise raise every 12 months

    def schedule(self, sessions: list[date], start: date, end: date) -> list[tuple[date, float]]:
        """Contribution dates (sessions) and amounts between ``start`` and ``end``."""
        output: list[tuple[date, float]] = []
        year, month = start.year, start.month
        index = 0
        while (year, month) <= (end.year, end.month):
            day = min(self.day_of_month, calendar.monthrange(year, month)[1])
            target = date(year, month, day)
            position = bisect.bisect_left(sessions, target)
            if position < len(sessions):
                session = sessions[position]
                if start <= session <= end and (not output or output[-1][0] != session):
                    amount = self.monthly_amount * (1 + self.annual_growth) ** (index // 12)
                    output.append((session, round(amount, 2)))
            index += 1
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return output

    def as_dict(self) -> dict[str, object]:
        return asdict(self)
