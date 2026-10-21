"""2026-10-20 (使用者：資料先用免費的；持續優化每一個策略): the depository's weekly holder distribution of every security
(集保戶股權分散表, TDCC open data 1-5), kept from now on.

The open data has only the latest week (the Friday's), and its history is not free, so each week's file is stored as it
comes; a year from now it is a history to test the big holders' signals (the share held in lots of 1,000 shares and
more, its change) on, forward only. Fetched every day (a new Friday's file appears over the weekend); a date already
stored is skipped. Levels 1–15 are holding brackets (15: more than 1,000,000 shares), 16 an adjustment, 17 the total.
"""

from __future__ import annotations

import csv
import gzip
import io
import urllib.request
from pathlib import Path

URL = "https://opendata.tdcc.com.tw/getOD.ashx?id=1-5"
BIG = 15                        # the bracket of more than 1,000,000 shares (1,000 lots)


def raw_path(base: str | Path, day: str) -> Path:
    return Path(base) / "raw" / "tdcc" / "holding" / f"{day}.csv.gz"


def _get() -> bytes:
    request = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 StockResearch/1.0 (research)"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def parse(raw: bytes) -> list[dict[str, object]]:
    """Rows of (date, code, level, holders, shares, percent); the header and malformed lines skipped."""
    text = raw.decode("utf-8-sig", "replace")
    rows = []
    for line in csv.reader(io.StringIO(text)):
        if len(line) < 6 or not line[0].strip().isdigit():
            continue
        try:
            rows.append({"date": line[0].strip(), "code": line[1].strip(), "level": int(line[2]),
                         "holders": int(line[3]), "shares": int(line[4]), "percent": float(line[5])})
        except ValueError:
            continue
    return rows


def fetch(base: str | Path, get=_get) -> dict[str, object]:
    """Today's file stored under its data date if that date is new; {"date", "stored", "rows"}."""
    raw = get()
    rows = parse(raw)
    if not rows:
        return {"date": None, "stored": False, "rows": 0}
    day = str(rows[0]["date"])
    path = raw_path(base, day)
    if path.is_file():
        return {"date": day, "stored": False, "rows": len(rows)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(raw))
    return {"date": day, "stored": True, "rows": len(rows)}


# 2026-10-21 (FinMind Sponsor 計畫): FinMind's TaiwanStockHoldingSharesPer (from 2010, Backer and above) names a bracket
# by its range of shares; the depository's numbers 1–15 by the range's lower end, 16 the adjustment, 17 the total
FINMIND_DATASET = "TaiwanStockHoldingSharesPer"
LOWER_ENDS = {1: 1, 1000: 2, 1001: 2, 5001: 3, 10001: 4, 15001: 5, 20001: 6, 30001: 7, 40001: 8, 50001: 9, 100001: 10,
              200001: 11, 400001: 12, 600001: 13, 800001: 14, 1000001: 15}


def finmind_level(text: object) -> int:
    """The depository's bracket number for FinMind's HoldingSharesLevel ("1-999", "1,000-5,000", …, "more than
    1,000,001", "total"); 16 for anything else (the adjustment), which the table leaves out."""
    words = str(text).strip().lower()
    if words.startswith("total") or words == "合計":
        return 17
    if "more" in words or "以上" in words:
        return 15
    head = words.split("-")[0].replace(",", "").strip()
    return LOWER_ENDS.get(int(head), 16) if head.isdigit() else 16


def finmind_rows(base: str | Path) -> list[dict[str, object]]:
    """Every stored FinMind file's weeks as the depository's rows (date as YYYYMMDD)."""
    from quant_platform.research.history.finmind import read_rows

    base = Path(base)
    folder = base / "raw" / "finmind" / FINMIND_DATASET
    rows = []
    for path in sorted(folder.glob("*.json.gz")) if folder.is_dir() else []:
        for row in read_rows(base, FINMIND_DATASET, path.name.split(".")[0]):
            level = finmind_level(row.get("HoldingSharesLevel"))
            if level == 16:
                continue
            try:
                rows.append({"date": str(row["date"])[:10].replace("-", ""), "code": str(row["stock_id"]), "level": level,
                             "holders": int(row.get("people") or 0), "shares": int(row.get("unit") or 0),
                             "percent": float(row.get("percent") or 0.0)})
            except (KeyError, TypeError, ValueError):
                continue
    return rows


def build(base: str | Path) -> dict[str, int]:
    """Every stored week into ``tdcc/holding.parquet`` (brackets 1–15 and the total, 17): FinMind's history first,
    the depository's own weekly files over it where both have a week (2026-10-21)."""
    import pandas as pd

    base = Path(base)
    rows = finmind_rows(base)
    for path in sorted((base / "raw" / "tdcc" / "holding").glob("*.csv.gz")):
        rows.extend(row for row in parse(gzip.decompress(path.read_bytes())) if row["level"] != 16)
    frame = pd.DataFrame(rows, columns=["date", "code", "level", "holders", "shares", "percent"])
    frame = frame.drop_duplicates(["date", "code", "level"], keep="last").sort_values(["date", "code", "level"])
    target = base / "tdcc" / "holding.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target, index=False)
    return {"weeks": int(frame["date"].nunique()) if len(frame) else 0, "rows": len(frame),
            "first": str(frame["date"].min()) if len(frame) else None}


def digest(base: str | Path, until) -> str:
    """The weeks a run could read up to ``until`` (a trial's input)."""
    import hashlib

    import pandas as pd

    path = Path(base) / "tdcc" / "holding.parquet"
    if not path.is_file():
        return "none"
    frame = pd.read_parquet(path)
    frame = frame[frame["date"].astype(str) <= until.strftime("%Y%m%d")]
    return hashlib.sha256(pd.util.hash_pandas_object(frame, index=False).values.tobytes()).hexdigest()
