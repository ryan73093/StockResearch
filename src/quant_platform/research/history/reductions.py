"""Capital reductions, TWSE and TPEx (2026-10-16 audit: the research had no capital-reduction data at all).

A capital reduction stops a stock's trading for a few weeks; it resumes at a reference price — the last close
before the stop, less the money returned per share (退還股款), divided by the new shares per old share (彌補虧損
returns nothing). Without these, only an overnight move beyond 0.6× or 1/0.6× was taken for a change in the share
count, so a 20% reduction resumed as a "25% gain" with the shares unchanged — in the accounts and in every factor.

Sources (each kept as fetched under ``raw/``):

* TWSE 減資恢復買賣參考價格 (TWTAUU), by year: ``twse_reductions/<year>.json``; each row's details
  (TWTAVUDetail: new shares per 1,000, money back per share) ``twse_reduction_details/<code>_<date>.json``;
* TPEx 減資恢復買賣參考價格 (bulletin/revivt), by year: ``tpex_reductions/<year>.json`` (details inline).

``events`` gives symbol → resume day → ("reduction", close before the stop, reference price): the unit factor
before ÷ reference (research/stock_rules.py ``unit_factors_all``) keeps the adjusted price continuous for every
factor. ``exchanges`` gives symbol → resume day → (new shares per old share, NT$ back per old share) where the
details are known: the engine (1.5.0) exchanges a holding's shares (whole shares, the fraction lost) and pays the
money back on the resume day. ``limits`` gives the day's limit prices.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

TWSE_URL = "https://www.twse.com.tw/rwd/zh/reducation/TWTAUU?startDate={start}&endDate={end}&response=json"
TWSE_DETAIL = "https://www.twse.com.tw/rwd/zh/reducation/TWTAVUDetail?STK_NO={code}&FILE_DATE={filed}&response=json"
TPEX_URL = "https://www.tpex.org.tw/www/zh-tw/bulletin/revivt?startDate={start}&endDate={end}&response=json"
FIRST_YEAR = 2005


def _roc(text: object) -> date | None:
    """'113/01/22' or '1130122' → 2024-01-22."""
    digits = re.findall(r"\d+", str(text or ""))
    if len(digits) == 1 and len(digits[0]) == 7:
        digits = [digits[0][:3], digits[0][3:5], digits[0][5:]]
    try:
        return date(int(digits[0]) + 1911, int(digits[1]), int(digits[2])) if len(digits) >= 3 else None
    except ValueError:
        return None


def _number(text: object) -> float | None:
    found = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(text or ""))
    try:
        return float(found.group(0).replace(",", "")) if found else None
    except ValueError:
        return None


def fetch(client, today: date, first_year: int = FIRST_YEAR) -> int:
    """Both exchanges' tables for every year (a closed year from the cache) and the details of every TWSE row
    (kept for good once fetched); returns the reductions listed."""
    count = 0
    for year in range(first_year, today.year + 1):
        end = min(today, date(year, 12, 31))
        twse = client._cached(f"twse_reductions/{year}", TWSE_URL.format(start=f"{year}0101", end=f"{end:%Y%m%d}"),
                              final=end < today, period_end=end)
        tpex = client._cached(f"tpex_reductions/{year}",
                              TPEX_URL.format(start=f"{year}/01/01", end=f"{end:%Y/%m/%d}"),
                              final=end < today, period_end=end)
        rows = parse(twse)
        count += len(rows) + len(parse_tpex(tpex))
        for row in rows:
            if row.get("filed"):
                client._cached(f"twse_reduction_details/{row['code']}_{row['filed']}",
                               TWSE_DETAIL.format(code=row["code"], filed=row["filed"]), final=True)
    return count


def parse(payload: object) -> list[dict[str, object]]:
    """TWSE TWTAUU rows: code, resume day, close before, reference, reason, limits, the details' file date."""
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
        return []
    fields = [str(field).strip() for field in payload.get("fields") or []]
    rows = []
    for item in payload.get("data") or []:
        record = dict(zip(fields, item))
        day, code = _roc(record.get("恢復買賣日期")), str(record.get("股票代號", "")).strip()
        before, reference = _number(record.get("停止買賣前收盤價格")), _number(record.get("恢復買賣參考價"))
        if day and code and before and reference and before > 0 and reference > 0:
            filed = re.findall(r"\d{8}", str(record.get("詳細資料", "")))
            rows.append({"code": code, "day": day, "before": before, "reference": reference,
                         "reason": str(record.get("減資原因", "")).strip(), "filed": filed[0] if filed else None,
                         "high": _number(record.get("漲停價格")), "low": _number(record.get("跌停價格"))})
    return rows


def parse_tpex(payload: object) -> list[dict[str, object]]:
    """TPEx revivt rows, with the new shares per old share and the money back from the inline details."""
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
        return []
    rows = []
    for table in payload.get("tables") or []:
        fields = [str(field).strip() for field in table.get("fields") or []]
        for item in table.get("data") or []:
            record = dict(zip(fields, item))
            day, code = _roc(record.get("恢復買賣日期")), str(record.get("股票代號", "")).strip()
            before = _number(record.get("最後交易日之收盤價格"))
            reference = _number(record.get("減資恢復買賣開始日參考價格"))
            if not (day and code and before and reference and before > 0 and reference > 0):
                continue
            detail = str(record.get("詳細資料", ""))
            kept = re.search(r"每壹仟股換發新股票[:：]</th><td>\s*([\d.,]+)", detail)
            back = re.search(r"每股退還股款[:：]</th><td>\s*([\d.,]+)", detail)
            per_share = _number(kept.group(1)) if kept else None
            rows.append({"code": code, "day": day, "before": before, "reference": reference,
                         "reason": str(record.get("減資原因", "")).strip(),
                         "kept": per_share / 1000 if per_share else None,
                         "refund": _number(back.group(1)) if back else None,
                         "high": _number(record.get("漲停價格")), "low": _number(record.get("跌停價格"))})
    return rows


def parse_detail(payload: object) -> tuple[float, float] | None:
    """TWSE TWTAVUDetail: (new shares per old share, NT$ back per old share)."""
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok" or not payload.get("data"):
        return None
    record = {str(field).strip().rstrip("：:"): value
              for field, value in zip(payload.get("fields") or [], payload["data"][0])}
    kept, back = _number(record.get("每壹仟股換發新股票")), _number(record.get("每股退還股款"))
    if kept is None or kept <= 0:
        return None
    return kept / 1000, back or 0.0


def _rows(raw_dir: Path, first_year: int) -> list[tuple[str, dict]]:
    """(symbol, row) for both exchanges' tables from ``first_year``."""
    output = []
    for exchange, suffix, parser in (("twse", ".TW", parse), ("tpex", ".TWO", parse_tpex)):
        folder = Path(raw_dir) / f"{exchange}_reductions"
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            if path.stem.isdigit() and int(path.stem) >= first_year:
                output += [(row["code"] + suffix, row) for row in parser(json.loads(path.read_text(encoding="utf-8")))]
    return output


def events(raw_dir: Path, first_year: int) -> dict[str, dict[date, tuple[str, float, float]]]:
    """symbol → resume day → ("reduction", close before the stop, reference price)."""
    output: dict[str, dict[date, tuple[str, float, float]]] = {}
    for symbol, row in _rows(raw_dir, first_year):
        output.setdefault(symbol, {})[row["day"]] = ("reduction", row["before"], row["reference"])
    return output


def exchanges(raw_dir: Path, first_year: int) -> dict[str, dict[date, tuple[float, float]]]:
    """symbol → resume day → (new shares per old share, NT$ back per old share), where the details are known."""
    output: dict[str, dict[date, tuple[float, float]]] = {}
    details = Path(raw_dir) / "twse_reduction_details"
    for symbol, row in _rows(raw_dir, first_year):
        terms = None
        if row.get("kept"):
            terms = (row["kept"], row.get("refund") or 0.0)
        elif row.get("filed"):
            path = details / f"{row['code']}_{row['filed']}.json"
            if path.is_file():
                terms = parse_detail(json.loads(path.read_text(encoding="utf-8")))
        if terms is not None:
            output.setdefault(symbol, {})[row["day"]] = terms
    return output


def limits(raw_dir: Path, first_year: int) -> dict[str, dict[date, tuple[float, float]]]:
    """symbol → resume day → the exchange's (limit-down, limit-up) that day."""
    output: dict[str, dict[date, tuple[float, float]]] = {}
    for symbol, row in _rows(raw_dir, first_year):
        if row["low"] and row["high"]:
            output.setdefault(symbol, {})[row["day"]] = (row["low"], row["high"])
    return output
