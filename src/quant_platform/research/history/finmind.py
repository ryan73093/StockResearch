"""Chip and fundamental data per listed stock from FinMind's free tier (factor research, 2026-10-04).

The owner asked whether institutional trading and other information become factors. FinMind's free
tier (600 requests an hour with the project's token) has, per stock and including delisted ones:

| dataset | what | from |
|---|---|---|
| TaiwanStockShareholding | foreign holding ratio | 2004-02 |
| TaiwanStockMonthRevenue | monthly revenue | — |
| TaiwanStockPER | P/E, P/B, dividend yield | — |
| TaiwanStockMarginPurchaseShortSale | margin purchase and short sale balances | 2001 |
| TaiwanStockInstitutionalInvestorsBuySell | three institutional investors' net buying | 2012-05 |

One request per stock and dataset for the whole history, stored gzip-compressed under
``raw/finmind/<dataset>/<code>.json.gz``; a stored file is not requested again, so the download
resumes after any interruption. It paces itself under the hourly limit, waits an hour when the
limit is hit, and leaves 13:30–15:30 on weekdays to the daily pipeline that uses the same quota.
Point in time: each row is used only from the day after its date (monthly revenue from the 11th of
the following month, the legal deadline is the 10th).
"""

from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from datetime import date, datetime
from datetime import time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
URL = "https://api.finmindtrade.com/api/v4/data"
DATASETS = {
    "TaiwanStockShareholding": "外資持股比率",
    "TaiwanStockMonthRevenue": "月營收",
    "TaiwanStockPER": "本益比、淨值比、殖利率",
    "TaiwanStockMarginPurchaseShortSale": "融資融券",
    "TaiwanStockInstitutionalInvestorsBuySell": "三大法人買賣超",
}
STATEMENTS = ("TaiwanStockFinancialStatements", "TaiwanStockBalanceSheet", "TaiwanStockCashFlowsStatement")
PRICES = "TaiwanStockPrice"          # raw (unadjusted) daily prices; used for TPEx stocks (R6)
LISTS = {                            # one request each: dataset -> data ids ("" = the whole list)
    "TaiwanStockDelisting": ("",),
    "TaiwanStockInfo": ("",),
    "TaiwanFuturesInstitutionalInvestors": ("TX", "MTX", "TE", "TF"),
}
PER_HOUR = 560                     # under the free tier's 600 an hour, with room for other users of the token
QUIET = (clock_time(13, 30), clock_time(15, 30))


class QuotaReached(Exception):
    pass


def path_for(base: Path, dataset: str, code: str) -> Path:
    return base / "raw" / "finmind" / dataset / f"{code}.json.gz"


def read_rows(base: Path, dataset: str, code: str) -> list[dict[str, object]]:
    path = path_for(base, dataset, code)
    if not path.is_file():
        return []
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8")).get("data") or []


def http_get(dataset: str, code: str, token: str, end: date) -> dict[str, object]:
    query = urllib.parse.urlencode({"dataset": dataset, "data_id": code, "start_date": "2000-01-01",
                                    "end_date": end.isoformat()})
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
    return payload


def tpex_codes(base: Path) -> list[str]:
    """TPEx common stocks: today's (TaiwanStockInfo type tpex) and delisted codes TWSE never quoted
    since 2004 (the delisting list does not say which market; FinMind answers empty for a code it lacks)."""
    info_path = base / "raw" / "finmind" / "TaiwanStockInfo.json"
    rows = (json.loads(info_path.read_text(encoding="utf-8")).get("data") or []) if info_path.is_file() else []
    current = {str(row["stock_id"]) for row in rows if row.get("type") == "tpex"}
    delisted = {str(row.get("stock_id")) for row in read_rows(base, "TaiwanStockDelisting", "all")}
    listed = set(stock_codes(base))

    def common(code: str) -> bool:
        return len(code) == 4 and code.isdigit() and code[0] not in "09"

    return sorted(code for code in current | (delisted - listed) if common(code))


def fetch_lists(base: Path, token: str, get: Callable[[str], dict] | None = None,
                sleep: Callable[[float], None] = time.sleep) -> dict[str, int]:
    """The list datasets (delisting, stock info, futures positioning by contract): one request each."""
    def default(query: str) -> dict:
        request = urllib.request.Request(f"{URL}?{query}", headers={"Authorization": f"Bearer {token}",
                                                                     "User-Agent": "StockResearch/1.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    get = get or default
    counts = {}
    for dataset, ids in LISTS.items():
        for data_id in ids:
            query = urllib.parse.urlencode({"dataset": dataset, **({"data_id": data_id, "start_date": "2000-01-01"}
                                                                   if data_id else {})})
            payload = get(query)
            rows = payload.get("data") or []
            path = path_for(base, dataset, data_id or "all")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(json.dumps({"dataset": dataset, "code": data_id, "data": rows},
                                                      ensure_ascii=False).encode("utf-8")))
            if dataset == "TaiwanStockInfo":       # the industry map reads the plain JSON
                (base / "raw" / "finmind" / "TaiwanStockInfo.json").write_text(
                    json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            counts[f"{dataset}:{data_id or 'all'}"] = len(rows)
            sleep(3)
    return counts


def stock_codes(base: Path) -> list[str]:
    """Every common-stock code that traded on TWSE in the research years (delisted included)."""
    import pyarrow.parquet as pq

    codes: set[str] = set()
    for path in sorted((base / "stocks" / "twse").glob("*.parquet")):
        codes.update(pq.read_table(path, columns=["code"])["code"].to_pylist())
    return sorted(codes)


def _quiet(now: datetime) -> bool:
    return now.weekday() < 5 and QUIET[0] <= now.time() <= QUIET[1]


def fetch_all(base: Path, token: str, codes: Iterable[str], datasets: Iterable[str] = tuple(DATASETS), job=None,
              get: Callable[[str, str, str, date], dict] = http_get, sleep: Callable[[float], None] = time.sleep,
              now: Callable[[], datetime] = lambda: datetime.now(TAIPEI), per_hour: int = PER_HOUR) -> dict[str, object]:
    codes, datasets = list(codes), list(datasets)
    total = len(codes) * len(datasets)
    interval = 3600.0 / per_hour
    done = requested = skipped = 0
    errors: list[str] = []
    if job:
        job.update(total=total, done=0, current="", force=True)
    for dataset in datasets:
        for code in codes:
            path = path_for(base, dataset, code)
            if path.is_file():
                done += 1
                skipped += 1
                continue
            while _quiet(now()):
                if job:
                    job.update(current="13:30–15:30 讓額度給每日流程，暫停中", force=True)
                sleep(300)
            if job:
                job.update(done=done, current=f"{DATASETS.get(dataset, dataset)} {code}")
            for attempt in range(3):
                try:
                    payload = get(dataset, code, token, now().date())
                    break
                except QuotaReached:
                    if job:
                        job.update(current="已達每小時上限，等一小時", force=True)
                    sleep(3600)
                except (urllib.error.URLError, TimeoutError, ValueError) as exc:
                    payload = None
                    if attempt == 2:
                        errors.append(f"{dataset} {code}：{exc}")
                    sleep(30)
            requested += 1
            if payload is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                body = {"dataset": dataset, "code": code, "fetched_at": now().isoformat(timespec="seconds"),
                        "data": payload.get("data") or []}
                partial = path.with_suffix(".partial")
                partial.write_bytes(gzip.compress(json.dumps(body, ensure_ascii=False).encode("utf-8")))
                partial.replace(path)
            done += 1
            sleep(interval)
    if job:
        job.update(done=done, force=True)
        job.payload["summary"] = f"請求 {requested} 次、已有 {skipped} 個、錯誤 {len(errors)} 個"
    return {"requested": requested, "skipped": skipped, "errors": errors, "total": total}
