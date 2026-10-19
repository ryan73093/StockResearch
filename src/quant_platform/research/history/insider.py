"""2026-10-19 (使用者：資料先用免費的): insiders' advance notices of share transfers, from the Market Observation Post
System (公開資訊觀測站, 持股轉讓日報表 — 股權轉讓事前申報), listed (SY) and OTC (OY), every weekday since 2015-06.

A director, supervisor, manager or 10% holder who will transfer shares must file beforehand: who, how (on the market —
一般交易, after-hours fixed price, block trades — or a gift, a trust, a transfer to a named person), how many, the
holding before and after, and the window (on the market: from the third day after the filing, for a month). The
exchange publishes the day's filings as one table per market; one request per weekday and market (a holiday answers
無持股轉讓之情形). Each answer is kept as it came (``raw/mops/insider/<market>/<year>/<yyyymmdd>.html.gz``), so the download
resumes and the parsing can be redone; requests are spaced PAUSE seconds apart and stop for 13:30–14:40.

Point in time: a filing is used from the session after its date (the table is published during and after the day).
"""

from __future__ import annotations

import gzip
import re
import time as clock
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from datetime import time as day_time
from pathlib import Path

URL = "https://mopsov.twse.com.tw/mops/web/ajax_t56sb12"
MARKETS = {"SY": "上市", "OY": "上櫃"}
FIRST = date(2015, 6, 1)
PAUSE = 3.0                                     # seconds between requests: a public site, asked politely
QUIET = (day_time(13, 30), day_time(14, 40))
BACKOFF = 600                                   # seconds to wait when the site refuses or answers oddly
FIELDS = ("change", "notice_date", "code", "name", "role", "person", "method", "shares", "daily_max", "transferee",
          "held_own", "held_trust", "total_own", "total_trust", "after_own", "after_trust", "period", "incomplete")
# ways that sell on the market (not a gift, a trust or a sale to a named person)
MARKET_METHODS = ("一般交易", "盤後定價", "鉅額")
_ROC_DATE = re.compile(r"^(\d{2,3})/(\d{2})/(\d{2})$")


class Refused(Exception):
    pass


def raw_path(base: Path, market: str, day: date) -> Path:
    return Path(base) / "raw" / "mops" / "insider" / market / str(day.year) / f"{day:%Y%m%d}.html.gz"


def _post(day: date, market: str) -> str:
    body = urllib.parse.urlencode({"run": "", "step": 2, "year": day.year - 1911, "month": f"{day.month:02d}",
                                   "day": f"{day.day:02d}", "report": market, "firstin": "true"}).encode()
    request = urllib.request.Request(URL, data=body, headers={"User-Agent": "Mozilla/5.0 StockResearch/1.0 (research)",
                                                              "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", "replace")


def answered(html: str) -> bool:
    """A real answer: the day's table (總計) or no filing that day (無持股轉讓之情形; 查無資料)."""
    return "總計" in html or "無持股轉讓" in html or "查無" in html


def roc(text: str) -> date | None:
    found = _ROC_DATE.match(text.strip())
    if not found:
        return None
    year, month, day = (int(part) for part in found.groups())
    return date(year + 1911, month, day)


def number(text: str) -> float | None:
    cleaned = text.replace(",", "").strip()
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def parse(html: str) -> list[dict[str, object]]:
    """The table's filings: each row's cells from its filing date on (the change mark, when there, just before)."""
    rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>|&nbsp;", " ", cell)).strip()
                 for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        at = next((index for index, cell in enumerate(cells) if roc(cell)), None)
        if at is None or len(cells) < at + 17:
            continue
        values = cells[at:at + 17]
        item: dict[str, object] = {"change": cells[at - 1] if at else ""}
        for name, value in zip(FIELDS[1:], values, strict=True):
            item[name] = value
        item["notice_date"] = roc(values[0])
        for name in ("shares", "daily_max", "held_own", "held_trust", "total_own", "total_trust", "after_own",
                     "after_trust"):
            item[name] = number(str(item[name]))
        rows.append(item)
    return rows


def weekdays(first: date, last: date) -> list[date]:
    days, day = [], first
    while day <= last:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def download(base: str | Path, today: date, job=None, post=_post, sleep=clock.sleep, now=datetime.now,
             first: date = FIRST) -> dict[str, int]:
    """Every weekday from ``first`` to the day before ``today`` (today's table is still filling) not stored yet, both
    markets; the answers stored as they came. Refusals wait BACKOFF and try again (three times, then stop)."""
    base = Path(base)
    wanted = [(day, market) for day in weekdays(first, today - timedelta(days=1)) for market in MARKETS
              if not raw_path(base, market, day).is_file()]
    done = refused = 0
    for number_, (day, market) in enumerate(wanted, start=1):
        while QUIET[0] <= now().time() < QUIET[1] and now().weekday() < 5:
            sleep(300)
        for attempt in range(3):
            try:
                html = post(day, market)
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                html = ""
            if answered(html):
                break
            refused += 1
            sleep(BACKOFF)
        else:
            raise Refused(f"{day} {market}：三次都沒有正常回應")
        path = raw_path(base, market, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(html.encode("utf-8")))
        done += 1
        if job:
            job.update(done=number_, total=len(wanted), current=f"{day} {MARKETS[market]}")
        sleep(PAUSE)
    return {"wanted": len(wanted), "stored": done, "refused": refused}


def build(base: str | Path) -> dict[str, int]:
    """Every stored day parsed into ``insider/transfers.parquet`` (one row a filing, with its market and query day)."""
    import pandas as pd

    base = Path(base)
    rows = []
    for path in sorted((base / "raw" / "mops" / "insider").glob("*/*/*.html.gz")):
        market, stamp = path.parts[-3], path.name[:8]
        for item in parse(gzip.decompress(path.read_bytes()).decode("utf-8")):
            rows.append({**item, "market": market, "query_day": datetime.strptime(stamp, "%Y%m%d").date()})
    frame = pd.DataFrame(rows, columns=[*FIELDS, "market", "query_day"])
    target = base / "insider" / "transfers.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target, index=False)
    on_market = frame["method"].astype(str).str.contains("|".join(MARKET_METHODS)) if len(frame) else []
    return {"filings": len(frame), "on_market": int(sum(on_market)) if len(frame) else 0,
            "days": len(list((base / "raw" / "mops" / "insider").glob("*/*/*.html.gz")))}
