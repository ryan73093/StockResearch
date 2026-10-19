"""2026-10-20 (使用者：持續優化每一個策略；資料先用免費的): directors' and supervisors' pledged shares as a share of
what they hold, every company each month (公開資訊觀測站 IRB180, 董監事質權設定佔董事及監察人實際持有股數), listed (sii)
and OTC (otc), since 2015 — a static page a month and market (``siis.twse.com.tw/publish/<market>/<ROC year>IRB180_<MM>.HTM``).

Heavily pledged insiders face margin calls when the price falls, which deepens falls (share-pledging and crash risk,
e.g. Dou, Masulis & Zein 2019). The page lists each company with a pledge by its percentage; a company not listed has
none. Point in time: a month's holdings are filed by the 15th of the next month, so a month's ratio is used from the
first session after the 15th of the following month (``usable_from``).
"""

from __future__ import annotations

import gzip
import re
import time as clock
import urllib.request
from datetime import date
from pathlib import Path

URL = "https://siis.twse.com.tw/publish/{market}/{roc}IRB180_{month:02d}.HTM"
MARKETS = {"sii": "上市", "otc": "上櫃"}
FIRST = (2015, 1)
PAUSE = 2.0
_ENTRY = re.compile(r"(?<![\d.])(\d{4})\s+(\S+?)\s+(\d{1,3}\.\d{2})(?![\d.])")


def raw_path(base: Path, market: str, year: int, month: int) -> Path:
    return Path(base) / "raw" / "mops" / "pledge" / market / f"{year}{month:02d}.htm.gz"


def _get(market: str, year: int, month: int) -> bytes:
    url = URL.format(market=market, roc=year - 1911, month=month)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 StockResearch/1.0 (research)"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def usable_from(year: int, month: int) -> date:
    """The day after the filing deadline: the 16th of the month after."""
    return date(year + (month == 12), month % 12 + 1, 16)


def parse(raw: bytes) -> list[tuple[str, str, float]]:
    """(code, name, percent) for each company on the page (text in cp950, the exchange's static pages)."""
    text = raw.decode("cp950", "replace")
    text = re.sub(r"<[^>]+>|&nbsp;", " ", text)
    found, seen = [], set()
    for code, name, percent in _ENTRY.findall(text):
        if code in seen:
            continue
        value = float(percent)
        if 0.0 <= value <= 100.0:
            seen.add(code)
            found.append((code, name, value))
    return found


def months(today: date) -> list[tuple[int, int]]:
    """Every month from FIRST to the month before ``today``'s."""
    output, (year, month) = [], FIRST
    while (year, month) < (today.year, today.month):
        output.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return output


def fetch(base: str | Path, today: date, get=_get, sleep=clock.sleep) -> dict[str, int]:
    """The months not stored yet (and the last two again: a late filing changes them), both markets."""
    base = Path(base)
    wanted = months(today)
    recent = set(wanted[-2:])
    asked = failed = 0
    for year, month in wanted:
        for market in MARKETS:
            path = raw_path(base, market, year, month)
            if path.is_file() and (year, month) not in recent:
                continue
            try:
                raw = get(market, year, month)
            except OSError:
                failed += 1
                continue
            asked += 1
            if parse(raw):                              # a month not published yet answers without companies
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(gzip.compress(raw))
            sleep(PAUSE)
    return {"asked": asked, "failed": failed}


def build(base: str | Path) -> dict[str, int]:
    """Every stored month into ``pledge/ratios.parquet``: month, usable from, market, code, name, percent."""
    import pandas as pd

    base = Path(base)
    rows = []
    for path in sorted((base / "raw" / "mops" / "pledge").glob("*/*.htm.gz")):
        market, stamp = path.parts[-2], path.name[:6]
        year, month = int(stamp[:4]), int(stamp[4:])
        for code, name, percent in parse(gzip.decompress(path.read_bytes())):
            rows.append({"year": year, "month": month, "usable_from": usable_from(year, month), "market": market,
                         "code": code, "name": name, "percent": percent})
    frame = pd.DataFrame(rows, columns=["year", "month", "usable_from", "market", "code", "name", "percent"])
    target = base / "pledge" / "ratios.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target, index=False)
    return {"rows": len(frame), "months": frame[["year", "month"]].drop_duplicates().shape[0] if len(frame) else 0}
