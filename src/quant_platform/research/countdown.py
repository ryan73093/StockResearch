"""How long until the first T0 judgment, from the numbers the status and the overview already read.

2026-11-22 (使用者 D-059：十一月二十二，距三十號，我又不知道要多久，只剩八天，沒有一個可以上線的): the pages said "第一次可能
在 11/30 收盤後" and "54／60 個交易日" but never how long that is — how many sessions are left and which days they are, what is
judged then, and what "can go live" still waits for. The date is the roadmap's (``invariants.FIRST_T0``), the sessions are the
exchange's calendar, the counts are the chained forward record's (``dashboard.chain_summary``); nothing here is estimated.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

WEEKDAYS = "一二三四五六日"


def day_label(day: date) -> str:
    """"11/30（一）"."""
    return f"{day.month}/{day.day}（{WEEKDAYS[day.weekday()]}）"


def countdown(today: date, first: date, through: date | None, sessions: int, needed: int,
              is_trading_day: Callable[[date], bool], not_behind_both: int | None = None,
              t0_rules: int = 0) -> dict[str, object]:
    """The state of the wait for the first T0, as the pages say it.

    ``first``: the day the chained record reaches ``needed`` sessions (the roadmap's date table); ``through``: the last
    session the forward records have; ``sessions``: how many the chained record has; ``not_behind_both``: how many rules
    with the longest run are not behind 0050 by both moneys today; ``t0_rules``: rules that are T0 now."""
    left: list[date] = []
    day = through or today
    while day < first and len(left) < 400:
        day += timedelta(days=1)
        if is_trading_day(day):
            left.append(day)
    return {
        "first": first.isoformat(), "first_label": day_label(first), "today": today.isoformat(), "today_label": day_label(today),
        "days": (first - today).days, "sessions_left": len(left), "dates": [day_label(item) for item in left],
        "sessions": sessions, "needed": needed, "through_label": day_label(through) if through else "",
        "not_behind_both": not_behind_both, "t0": t0_rules,
        # the day has come: the record has its sessions (or the date is past), whatever the rules do
        "reached": not left and (sessions >= needed or today > first),
    }
