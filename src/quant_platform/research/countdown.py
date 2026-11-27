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


def judged_count(chained: dict[str, dict] | None, pool_rows: list[dict], passing: tuple[str, ...] | set[str]) -> int:
    """How many rules the T0 judgment would look at today — the number the 判定清單 lists (``progress.t0_list``): the longest run
    on the monthly lists, a passing tier, no 0050 in the rule, not behind 0050 by either money, not reading paid tables that stopped (``pool.stale_data``). A count of its own because the
    home page's "兩種算法都不輸 0050" counts every rule with the longest run (``dashboard.chain_summary``), 0050 ones and
    failing ones included; the two are shown together so the pages do not say two numbers for one question (D-046 第 9 項)."""
    rows = {row["spec_hash"]: row for row in pool_rows}
    listed = {key: value for key, value in (chained or {}).items()
              if value.get("parts") and (rows.get(key) or {}).get("tier") in passing and not (rows.get(key) or {}).get("uses_0050")}
    longest = max((value["sessions"] for value in listed.values()), default=0)
    return sum(1 for key, value in listed.items()          # 2026-11-27: not the ones reading tables that stopped
               if value["sessions"] == longest and value["excess"] >= 0 and value.get("sold_excess", value["excess"]) >= 0
               and not rows[key].get("stale_data"))


def countdown(today: date, first: date, through: date | None, sessions: int, needed: int,
              is_trading_day: Callable[[date], bool], not_behind_both: int | None = None,
              t0_rules: int = 0, judged: int | None = None) -> dict[str, object]:
    """The state of the wait for the first T0, as the pages say it.

    ``first``: the day the chained record reaches ``needed`` sessions (the roadmap's date table); ``through``: the last
    session the forward records have; ``sessions``: how many the chained record has; ``not_behind_both``: how many rules
    with the longest run are not behind 0050 by both moneys today; ``judged``: how many of those the T0 judgment looks at
    (``judged_count``); ``t0_rules``: rules that are T0 now."""
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
        "not_behind_both": not_behind_both, "judged": judged, "t0": t0_rules,
        # the day has come: the record has its sessions (or the date is past), whatever the rules do
        "reached": not left and (sessions >= needed or today > first),
    }
