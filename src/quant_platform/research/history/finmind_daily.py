"""2026-10-22 (使用者：還有什麼要加入分析的; FinMind Sponsor until 2026-11-21): the two paid tables FinMind serves only
one day at a time, downloaded while the plan lasts and kept small.

| table | request | kept |
|---|---|---|
| TaiwanStockGovernmentBankBuySell (八大行庫) | one session, every stock (about 15,000 rows) | per stock and session: the eight banks' shares and money bought and sold, summed |
| TaiwanStockTradingDailyReport (分點) | one stock and session (thousands of rows: branch × price) | per stock and session: each branch's shares bought and sold and money, summed over prices |

Both from 2021-06-30 (FinMind's first day). The branches only for the listed stocks among the 300 largest by
turnover in any calendar year from 2021 (``branch_codes``: not today's 300, which would keep only the survivors).
Files: ``raw/finmind/govbank/<date>.json.gz`` and ``raw/finmind/branches/<code>/<date>.json.gz``; a stored day is not
asked again (resumable). Paced under ``per_hour``; at FinMind's hourly limit it waits until the next hour
(finmind.quota_wait)."""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from quant_platform.research import files  # 2026-10-29: replace, tried again while read

from quant_platform.research.history.finmind import TAIPEI, URL, QuotaReached, quota_wait

FIRST = date(2021, 6, 30)
TOP = 300


def sessions(base: str | Path, start: date = FIRST, end: date | None = None) -> list[date]:
    """The listed market's sessions from the stored prices."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    days: set[date] = set()
    for path in sorted((Path(base) / "stocks" / "twse").glob("*.parquet")):
        if int(path.stem) < start.year:
            continue
        days.update(pc.unique(pq.read_table(path, columns=["date"])["date"]).to_pylist())
    return sorted(day for day in days if day >= start and (end is None or day <= end))


def branch_codes(base: str | Path, start: date = FIRST, top: int = TOP) -> list[str]:
    """The listed common stocks among the ``top`` by turnover in any calendar year from ``start``'s."""
    import pandas as pd
    import pyarrow.parquet as pq

    codes: set[str] = set()
    for path in sorted((Path(base) / "stocks" / "twse").glob("*.parquet")):
        if int(path.stem) < start.year:
            continue
        frame = pq.read_table(path, columns=["code", "turnover"]).to_pandas()
        frame = frame[frame["code"].str.len().eq(4) & frame["code"].str[0].ne("0") & ~frame["code"].str.startswith("91")]
        codes.update(frame.groupby("code")["turnover"].sum().nlargest(top).index)
    return sorted(codes)


def _get(token: str, params: dict) -> list[dict]:
    request = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(params)}",
                                     headers={"Authorization": f"Bearer {token}", "User-Agent": "StockResearch/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (402, 429):
            raise QuotaReached(str(exc)) from exc
        raise
    if payload.get("status") in (402, 429):
        raise QuotaReached(str(payload.get("msg")))
    return payload.get("data") or []


def govbank_rows(rows: list[dict]) -> list[list]:
    """[code, shares bought, shares sold, money bought, money sold] per stock, the banks summed."""
    total: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for row in rows:
        entry = total[str(row.get("stock_id"))]
        for slot, key in enumerate(("buy", "sell", "buy_amount", "sell_amount")):
            entry[slot] += float(row.get(key) or 0)
    return [[code, *values] for code, values in sorted(total.items())]


def branch_rows(rows: list[dict]) -> list[list]:
    """[branch id, shares bought, shares sold, money bought, money sold] per branch, the price levels summed."""
    total: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    for row in rows:
        entry = total[str(row.get("securities_trader_id"))]
        price, bought, sold = float(row.get("price") or 0), float(row.get("buy") or 0), float(row.get("sell") or 0)
        entry[0] += bought
        entry[1] += sold
        entry[2] += bought * price
        entry[3] += sold * price
    return [[branch, *[round(value, 2) for value in values]] for branch, values in sorted(total.items())]


def _store(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_bytes(gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8")))
    files.replace(partial, path, wait=120.0)


def download(base: str | Path, token: str, tasks: list[tuple[str, date]], kind: str, per_hour: int, job=None,
             get: Callable[[str, dict], list[dict]] = _get, sleep: Callable[[float], None] = time.sleep,
             now: Callable[[], datetime] = lambda: datetime.now(TAIPEI)) -> dict[str, int]:
    """``tasks``: (code, day) — the code "" for the government banks' whole-market day. Each stored once."""
    base = Path(base)
    asked = skipped = errors = 0
    interval = 3600.0 / per_hour
    for number, (code, day) in enumerate(tasks):
        path = (base / "raw" / "finmind" / "govbank" / f"{day.isoformat()}.json.gz" if kind == "govbank"
                else base / "raw" / "finmind" / "branches" / code / f"{day.isoformat()}.json.gz")
        if path.is_file():
            skipped += 1
            continue
        if job and number % 20 == 0:
            job.update(done=number, total=len(tasks), current=f"{code or '全市場'} {day}")
        params = ({"dataset": "TaiwanStockGovernmentBankBuySell", "start_date": day.isoformat()} if kind == "govbank"
                  else {"dataset": "TaiwanStockTradingDailyReport", "data_id": code, "start_date": day.isoformat()})
        for attempt in range(3):
            try:
                rows = get(token, params)
                rows = [row for row in rows if str(row.get("date") or "")[:10] == day.isoformat()]
                _store(path, {"date": day.isoformat(), "code": code,
                              "rows": govbank_rows(rows) if kind == "govbank" else branch_rows(rows)})
                asked += 1
                break
            except QuotaReached:
                wait = quota_wait(token, now())
                if job:
                    job.update(current=f"已達每小時上限，等 {wait / 60:.0f} 分鐘", force=True)
                sleep(wait)
            except (urllib.error.URLError, TimeoutError, ValueError):
                if attempt == 2:
                    errors += 1
                sleep(30)
        sleep(interval)
    if job:
        job.update(done=len(tasks), total=len(tasks), force=True)
    return {"asked": asked, "skipped": skipped, "errors": errors, "tasks": len(tasks)}
