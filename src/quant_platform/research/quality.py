"""Data quality of the research store: what the system page checks (roadmap S9-W05, 2026-10-06).

Since S9-W03 Today, holdings and the plan read their prices from the research history, so its quality is
what matters: is every dataset up to the latest session that should be there, did the last session's
quotes arrive for the whole market, and which stocks moved beyond the 10% daily limit with no ex-rights
event to explain it (a data error, a new listing's first days, or a split the tables do not mark).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

LIMIT = 0.105          # the daily price limit is 10%; a tick of rounding above it
STALE_CHIPS = 5        # sessions the chip data may lag before it is a warning (no nightly update yet)


def _last_two(path: Path) -> tuple[date | None, dict[str, dict], dict[str, dict]]:
    """The year file's last session and the rows of its last two sessions by code."""
    if not path.is_file():
        return None, {}, {}
    table = pq.read_table(path, columns=["date", "code", "name", "close"])
    if table.num_rows == 0:
        return None, {}, {}
    days = sorted(set(pc.unique(table["date"]).to_pylist()))
    last = days[-1]
    previous = days[-2] if len(days) > 1 else None

    def rows(day):
        if day is None:
            return {}
        part = table.filter(pc.equal(table["date"], day)).to_pylist()
        return {row["code"]: row for row in part}

    return last, rows(last), rows(previous)


def _status(last: date | None, expected: date, sessions_behind: int | None = None, allowed: int = 0) -> str:
    if last is None:
        return "bad"
    if last >= expected:
        return "ok"
    return "warn" if sessions_behind is not None and sessions_behind <= allowed else "bad"


def check(history: str | Path, research_dir: str | Path, expected: date, sessions: list[date] | None = None,
          events: dict[str, dict] | None = None) -> dict[str, object]:
    """``expected``: the latest session whose close should be in the store by now. ``sessions``: the
    trading days (to count how far a dataset lags). ``events``: symbol -> day -> ex-rights event."""
    history, research_dir = Path(history), Path(research_dir)
    sessions = sorted(sessions or [])

    def behind(last: date | None) -> int | None:
        if last is None:
            return None
        return sum(1 for day in sessions if last < day <= expected)

    rows: list[dict[str, object]] = []
    moves: list[dict[str, object]] = []
    gone = 0
    for exchange, label, suffix in (("twse", "上市個股日行情", ".TW"), ("tpex", "上櫃個股日行情", ".TWO")):
        path = history / "stocks" / exchange / f"{expected.year}.parquet"
        last, today, before = _last_two(path)
        if last is None and expected.month == 1:                  # early January: last year's file
            last, today, before = _last_two(history / "stocks" / exchange / f"{expected.year - 1}.parquet")
        rows.append({"name": label, "last": last.isoformat() if last else None, "status": _status(last, expected),
                     "detail": f"{len(today):,} 檔；前一天 {len(before):,} 檔" if last else "沒有資料"})
        gone += len(set(before) - set(today)) if len(before) > 50 else 0
        for code, row in today.items():
            earlier = before.get(code)
            if not earlier or not earlier["close"] or not row["close"]:
                continue
            change = row["close"] / earlier["close"] - 1
            if abs(change) > LIMIT:
                marked = (events or {}).get(f"{code}{suffix}", {}).get(last) or \
                    (events or {}).get(f"{code}.TW", {}).get(last)
                moves.append({"code": code, "name": row["name"], "change": change, "day": last.isoformat(),
                              "explained": "除權息" if marked else None})
    daily = sorted((history / "daily").glob("*.parquet")) if (history / "daily").is_dir() else []
    lasts = {}
    for path in daily:
        table = pq.read_table(path, columns=["date"])
        lasts[path.stem] = pc.max(table["date"]).as_py() if table.num_rows else None
    lagging = sorted(name for name, last in lasts.items() if last is None or last < expected)
    oldest = min((last for last in lasts.values() if last), default=None)
    rows.append({"name": "ETF 與指數日線", "last": max((last for last in lasts.values() if last), default=None),
                 "status": "ok" if lasts and not lagging else ("warn" if lasts else "bad"),
                 "detail": f"{len(lasts)} 個序列" + (f"；落後：{'、'.join(lagging)}（最舊 {oldest}）" if lagging else "")})
    chips = history / "chips" / "TaiwanStockShareholding.parquet"
    chips_last = pc.max(pq.read_table(chips, columns=["date"])["date"]).as_py() if chips.is_file() else None
    rows.append({"name": "籌碼與基本面", "last": chips_last,
                 "status": _status(chips_last, expected, behind(chips_last), STALE_CHIPS),
                 "detail": "交易日 21:30 由證交所與櫃買日報更新；月營收每晚抓最新一個月的彙總表"})
    news = history / "raw" / "finmind" / "TaiwanStockNews"
    folders = sorted(path for path in news.iterdir() if path.is_dir()) if news.is_dir() else []
    news_last = date.fromisoformat(f"{folders[-1].name[:4]}-{folders[-1].name[4:6]}-{folders[-1].name[6:]}") if folders else None
    rows.append({"name": "新聞（成交值前 150 檔與前向持股）", "last": news_last,
                 "status": "ok" if news_last and (behind(news_last) or 0) <= 1 else "warn",
                 "detail": f"{len(list(folders[-1].glob('*.json.gz')))} 檔" if folders else "還沒有（交易日 14:40 收前一個交易日）"})
    snapshot = research_dir / "snapshots" / "stocks-latest.json"
    snapshot_last = None
    if snapshot.is_file():
        try:
            snapshot_last = date.fromisoformat(json.loads(snapshot.read_text(encoding="utf-8"))["date"])
        except (OSError, ValueError, KeyError):
            snapshot_last = None
    rows.append({"name": "個股因子快照（個股頁、市場總覽）", "last": snapshot_last,
                 "status": _status(snapshot_last, expected), "detail": "交易日 15:45 建立"})
    for row in rows:
        if isinstance(row["last"], date):
            row["last"] = row["last"].isoformat()
    worst = "bad" if any(row["status"] == "bad" for row in rows[:2]) else (
        "warn" if any(row["status"] != "ok" for row in rows) or [move for move in moves if not move["explained"]] else "ok")
    moves.sort(key=lambda move: -abs(move["change"]))
    return {"expected": expected.isoformat(), "rows": rows, "moves": moves, "gone": gone, "status": worst}


def ex_rights_events(history: str | Path, year: int) -> dict[str, dict]:
    """The year's official ex-rights events by symbol and day (TWSE and TPEx)."""
    from quant_platform.research.legacy_challenger import exchange_events

    return exchange_events(Path(history) / "raw", year)

