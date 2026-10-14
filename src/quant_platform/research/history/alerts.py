"""TWSE's attention and disposition announcements (2026-10-14; data gap 處置／注意股).

The exchange names a stock in 注意交易資訊 when it trades abnormally that day (a large rise over six sessions, a
volume burst, a big turnover share …), after the close; three such days in a row, or a few in a fortnight,
put it under 處置 (matching every 5 or 20 minutes, prepayment for large orders) for ten sessions. Both lists are
official and complete back to before 2014: the attention list by month, the disposition list by year.

``fetch`` keeps the raw answers (raw/twse_alerts/...) — a closed month or year is final — and ``build`` writes
chips/AlertNotice.parquet (date, code) and chips/AlertDisposition.parquet (date announced, code, first day,
last day, the n-th disposition). Common stocks only (four digits, not 00xx, not 91xx).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

FIRST_YEAR = 2014
STOCK = re.compile(r"^(?!91)[1-9]\d{3}$")


def _roc(text: str) -> date | None:
    parts = re.split(r"[./]", str(text).strip())
    try:
        year, month, day = (int(part) for part in parts[:3])
        return date(year + 1911, month, day)
    except (ValueError, TypeError):
        return None


def fetch(client, today: date) -> dict[str, int]:
    """Every month's attention list and every year's disposition list from FIRST_YEAR to ``today``."""
    from urllib.parse import urlencode

    from quant_platform.research.history.official import TWSE

    requested = {"notice": 0, "punish": 0}
    year, month = FIRST_YEAR, 1
    while (year, month) <= (today.year, today.month):
        first = date(year, month, 1)
        last = date(year + (month == 12), month % 12 + 1, 1)
        end = min(today, date.fromordinal(last.toordinal() - 1))
        query = urlencode({"startDate": f"{first:%Y%m%d}", "endDate": f"{end:%Y%m%d}", "response": "json"})
        client._cached(f"twse_alerts/notice/{first:%Y%m}", f"{TWSE}/announcement/notice?{query}",
                       final=end < today, period_end=end)
        requested["notice"] += 1
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    for year in range(FIRST_YEAR, today.year + 1):
        end = min(today, date(year, 12, 31))
        query = urlencode({"startDate": f"{year}0101", "endDate": f"{end:%Y%m%d}", "response": "json"})
        client._cached(f"twse_alerts/punish/{year}", f"{TWSE}/announcement/punish?{query}",
                       final=end < today, period_end=end)
        requested["punish"] += 1
    return requested


def parse_notice(payload: object) -> list[tuple[date, str]]:
    rows = []
    for item in (payload or {}).get("data") or []:
        code, day = str(item[1]).strip(), _roc(item[5])
        if day and STOCK.match(code):
            rows.append((day, code))
    return rows


def parse_punish(payload: object) -> list[tuple[date, str, date | None, date | None, int]]:
    rows = []
    for item in (payload or {}).get("data") or []:
        code, announced = str(item[2]).strip(), _roc(item[1])
        if not announced or not STOCK.match(code):
            continue
        period = re.split(r"[～~]", str(item[6]))
        start, end = (_roc(period[0]), _roc(period[1])) if len(period) == 2 else (None, None)
        count = re.search(r"第(.)次", str(item[7]))
        order = {"一": 1, "二": 2, "三": 3}.get(count.group(1), 1) if count else 1
        rows.append((announced, code, start, end, order))
    return rows


def build(base: Path) -> dict[str, int]:
    """chips/AlertNotice.parquet and chips/AlertDisposition.parquet from the raw answers."""
    import json

    raw = Path(base) / "raw" / "twse_alerts"
    notice, punish = set(), set()
    def load(path: Path) -> object:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("payload", payload) if isinstance(payload, dict) else payload

    for path in sorted((raw / "notice").glob("*.json")):
        notice.update(parse_notice(load(path)))
    for path in sorted((raw / "punish").glob("*.json")):
        punish.update(parse_punish(load(path)))
    out = Path(base) / "chips"
    out.mkdir(parents=True, exist_ok=True)
    notice_rows, punish_rows = sorted(notice), sorted(punish, key=lambda row: (row[0], row[1]))
    pq.write_table(pa.table({"date": [row[0] for row in notice_rows], "code": [row[1] for row in notice_rows]}),
                   out / "AlertNotice.parquet")
    pq.write_table(pa.table({"date": [row[0] for row in punish_rows], "code": [row[1] for row in punish_rows],
                             "start": [row[2] for row in punish_rows], "end": [row[3] for row in punish_rows],
                             "order": [row[4] for row in punish_rows]}), out / "AlertDisposition.parquet")
    return {"notice": len(notice_rows), "disposition": len(punish_rows)}
