"""Daily news collection for the future LLM analyst (research roadmap R15/R16, 2026-10-05).

Historical news is not available point in time (FinMind answers one stock-day per request, and an LLM has
already read the past), so news is collected from now on: every trading day at 14:40, after the
after-hours auction, the previous trading day's news for the stocks that matter — the 150 most traded
listed stocks of the last 20 sessions and everything the forward-observed rules hold — is stored as
``raw/finmind/TaiwanStockNews/<YYYYMMDD>/<code>.json.gz`` (title, source, link, time). Nothing decides on it
yet: once months have accumulated, the analyst turns it into dated features and the factor analysis
says whether they help (forward only).
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pyarrow.parquet as pq

from quant_platform.research.history.finmind import URL, QuotaReached

TOP = 150


def candidates(history: str | Path, research_dir: str | Path, day: date, top: int = TOP) -> list[str]:
    """The most traded listed stocks over the last 20 sessions up to ``day`` and every forward holding."""
    base = Path(history)
    codes: set[str] = set()
    frames = []
    for year in (day.year - 1, day.year):
        path = base / "stocks" / "twse" / f"{year}.parquet"
        if path.is_file():
            frames.append(pq.read_table(path, columns=["date", "code", "turnover"]))
    if frames:
        import pyarrow as pa

        table = pa.concat_tables(frames).to_pandas()
        table = table[table["date"] <= day]
        recent = sorted(table["date"].unique())[-20:]
        table = table[table["date"].isin(recent)]
        ranked = table.groupby("code")["turnover"].mean().sort_values(ascending=False)
        codes.update(ranked.index[:top].tolist())
    log = Path(research_dir) / "forward" / "stocks" / "log.jsonl"
    if log.is_file():
        latest: dict[str, dict] = {}
        for line in log.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                latest[record["rule_hash"]] = record
        for record in latest.values():
            codes.update(item["code"] for item in record.get("holdings") or [])
    return sorted(codes)


def path_for(history: str | Path, day: date, code: str) -> Path:
    return Path(history) / "raw" / "finmind" / "TaiwanStockNews" / f"{day:%Y%m%d}" / f"{code}.json.gz"


def http_news(token: str, code: str, day: date) -> list[dict]:
    query = urllib.parse.urlencode({"dataset": "TaiwanStockNews", "data_id": code, "start_date": day.isoformat()})
    request = urllib.request.Request(f"{URL}?{query}", headers={"Authorization": f"Bearer {token}",
                                                                 "User-Agent": "StockResearch/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (402, 429):
            raise QuotaReached(str(exc)) from exc
        raise
    if payload.get("status") in (402, 429):
        raise QuotaReached(str(payload.get("msg")))
    return [row for row in payload.get("data") or [] if str(row.get("date", ""))[:10] == day.isoformat()]


def collect(history: str | Path, day: date, codes: list[str], token: str, job=None,
            get: Callable[[str, str, date], list[dict]] = http_news, sleep: Callable[[float], None] = time.sleep,
            pause: float = 3.0) -> dict[str, object]:
    """Store each code's news of ``day`` once; stops (and resumes next time) at the hourly limit."""
    stored = skipped = articles = 0
    if job:
        job.update(total=len(codes), done=0, force=True)
    for index, code in enumerate(codes):
        path = path_for(history, day, code)
        if path.is_file():
            skipped += 1
            continue
        if job:
            job.update(done=index, current=code)
        try:
            rows = get(token, code, day)
        except QuotaReached:
            break
        except (OSError, ValueError):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps({"code": code, "day": day.isoformat(), "data": rows},
                                                  ensure_ascii=False).encode("utf-8")))
        stored += 1
        articles += len(rows)
        sleep(pause)
    if job:
        job.update(done=len(codes), force=True)
        job.payload["summary"] = f"{day}：{stored} 檔、{articles} 則（已有 {skipped} 檔）"
    return {"stored": stored, "skipped": skipped, "articles": articles}


def read(history: str | Path, day: date) -> dict[str, list[dict]]:
    folder = Path(history) / "raw" / "finmind" / "TaiwanStockNews" / f"{day:%Y%m%d}"
    output = {}
    for path in sorted(folder.glob("*.json.gz")) if folder.is_dir() else []:
        output[path.name.split(".")[0]] = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))["data"]
    return output

