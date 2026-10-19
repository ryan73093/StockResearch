"""2026-10-19 (使用者：繼續研究，還有什麼方法可以找到更好的策略；資料先用免費的): companies' buyback programs, from the
Market Observation Post System (庫藏股統計彙總表, t35sc09), listed (sii) and OTC (otc), by board-resolution date.

One request per market and year returns every program whose board resolved a buyback that year: the date, the
purpose (1 transfer to employees, 2 for convertibles or warrants, 3 to protect the company's credit and shareholders'
equity), the cap, the planned shares, the price range and window — and, once the window is over, what was bought.
Each answer is kept as it came (``raw/mops/buyback/<market>/<year>.html.gz``); the current and previous years are
asked again on every run (their results fill in later), the earlier ones once.

Point in time: a program is known from the session after its board date (the announcement is made that evening).
Only what the announcement says may be used before the window ends — the bought shares, amount and average price are
filled in afterwards (``KNOWN_AT_ANNOUNCEMENT``).
"""

from __future__ import annotations

import gzip
import re
import time as clock
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

URL = "https://mopsov.twse.com.tw/mops/web/ajax_t35sc09"
MARKETS = {"sii": "上市", "otc": "上櫃"}
FIRST_YEAR = 2015
PAUSE = 3.0
FIELDS = ("code", "name", "board_date", "purpose", "cap_amount", "planned_shares", "price_low", "price_high",
          "window_start", "window_end", "finished", "executed_shares", "cancelled_shares", "executed_ratio",
          "executed_amount", "average_price", "executed_share_of_issued", "unfinished_reason")
KNOWN_AT_ANNOUNCEMENT = ("code", "name", "board_date", "purpose", "cap_amount", "planned_shares", "price_low",
                         "price_high", "window_start", "window_end")
_ROC = re.compile(r"^(\d{2,3})/(\d{2})/(\d{2})$")


def raw_path(base: Path, market: str, year: int) -> Path:
    return Path(base) / "raw" / "mops" / "buyback" / market / f"{year}.html.gz"


def _post(market: str, year: int, today: date) -> str:
    roc = year - 1911
    last = today if year == today.year else date(year, 12, 31)
    body = urllib.parse.urlencode({"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "TYPEK": market,
                                   "d1": f"{roc}0101", "d2": f"{last.year - 1911}{last:%m%d}", "RD": 1}).encode()
    request = urllib.request.Request(URL, data=body, headers={"User-Agent": "Mozilla/5.0 StockResearch/1.0 (research)",
                                                              "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", "replace")


def roc(text: str) -> date | None:
    found = _ROC.match(text.strip())
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


def answered(html: str) -> bool:
    return "彙總統計表" in html or "查無" in html


def parse(html: str) -> list[dict[str, object]]:
    """Each program row: a sequence number, then the fields in FIELDS order (the standard-reached column skipped)."""
    rows = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>|&nbsp;", " ", cell)).strip()
                 for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if len(cells) < 20 or not cells[0].isdigit() or not roc(cells[3]):
            continue
        values = cells[1:12] + cells[13:20]               # cells[12]: 買回達一定標準資料 (a link)
        item = dict(zip(FIELDS, values, strict=True))
        for name in ("board_date", "window_start", "window_end"):
            item[name] = roc(str(item[name]))
        for name in ("cap_amount", "planned_shares", "price_low", "price_high", "executed_shares", "cancelled_shares",
                     "executed_ratio", "executed_amount", "average_price", "executed_share_of_issued"):
            item[name] = number(str(item[name]))
        rows.append(item)
    return rows


def fetch(base: str | Path, today: date, post=_post, sleep=clock.sleep) -> dict[str, int]:
    """Every market and year from FIRST_YEAR: the years not stored yet, and the current and previous ones again."""
    base = Path(base)
    asked = 0
    for year in range(FIRST_YEAR, today.year + 1):
        for market in MARKETS:
            path = raw_path(base, market, year)
            if path.is_file() and year < today.year - 1:
                continue
            html = post(market, year, today)
            if not answered(html):
                raise RuntimeError(f"{year} {market}：觀測站沒有正常回應")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(html.encode("utf-8")))
            asked += 1
            sleep(PAUSE)
    return {"asked": asked}


def build(base: str | Path) -> dict[str, int]:
    """Every stored year parsed into ``buyback/programs.parquet`` (one row a program, with its market)."""
    import pandas as pd

    base = Path(base)
    rows = []
    for path in sorted((base / "raw" / "mops" / "buyback").glob("*/*.html.gz")):
        for item in parse(gzip.decompress(path.read_bytes()).decode("utf-8")):
            rows.append({**item, "market": path.parts[-2]})
    frame = pd.DataFrame(rows, columns=[*FIELDS, "market"]).drop_duplicates(subset=["code", "board_date", "planned_shares"])
    target = base / "buyback" / "programs.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target, index=False)
    return {"programs": len(frame), "protect": int((frame["purpose"].astype(str) == "3").sum()) if len(frame) else 0,
            "built_at": int(datetime.now().timestamp())}
