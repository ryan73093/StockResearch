"""Nightly chip and fundamental data from the exchanges' all-market daily reports (roadmap S9-W04, 2026-10-06).

FinMind's free tier answers one stock per request (about 600 an hour), so the whole market's chip data
cannot be refreshed every night from it. TWSE and TPEx publish each dataset for every stock in one daily
report, and their numbers are the ones FinMind serves (checked on 2026-10-02: TSMC's foreign holding
69.17% and 25,932,370,067 shares, margin 30,939 / short 18 lots, foreign 7,044,515 bought / 12,958,489
sold, trust 539,805 / 286,127 — the same). So the history built from the FinMind download is extended
day by day from these reports, four requests per exchange and session:

| dataset (chips parquet) | TWSE | TPEx |
|---|---|---|
| TaiwanStockShareholding | fund/MI_QFIIS | insti/qfii |
| TaiwanStockPER | afterTrading/BWIBBU_d | afterTrading/peQryDate |
| TaiwanStockMarginPurchaseShortSale | marginTrading/MI_MARGN | margin/balance |
| TaiwanStockInstitutionalInvestorsBuySell | fund/T86 | insti/dailyTrade |

The raw replies are cached under ``raw/official_chips/`` (a session's copy is requested again once after
it closed); ``research/chips.py`` adds their rows for the days the FinMind files do not have. Monthly
revenue is not here (it comes once a month).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path

from quant_platform.research.history.official import TPEX_BULLETIN, OfficialHistoryClient, number

TWSE = "https://www.twse.com.tw/rwd/zh"
TPEX = TPEX_BULLETIN.rsplit("/", 1)[0]
STOCK = re.compile(r"^(?!91)[1-9]\d{3}$")
DATASETS = ("holding", "per", "margin", "insti")


def url(exchange: str, dataset: str, day: date) -> str:
    if exchange == "twse":
        compact = f"{day:%Y%m%d}"
        return {
            "holding": f"{TWSE}/fund/MI_QFIIS?date={compact}&selectType=ALLBUT0999&response=json",
            "per": f"{TWSE}/afterTrading/BWIBBU_d?date={compact}&selectType=ALL&response=json",
            "margin": f"{TWSE}/marginTrading/MI_MARGN?date={compact}&selectType=ALL&response=json",
            "insti": f"{TWSE}/fund/T86?date={compact}&selectType=ALLBUT0999&response=json",
        }[dataset]
    slash = f"{day:%Y/%m/%d}"
    return {
        "holding": f"{TPEX}/insti/qfii?date={slash}&response=json",
        "per": f"{TPEX}/afterTrading/peQryDate?date={slash}&response=json",
        "margin": f"{TPEX}/margin/balance?date={slash}&response=json",
        "insti": f"{TPEX}/insti/dailyTrade?type=Daily&sect=EW&date={slash}&response=json",
    }[dataset]


def key(exchange: str, dataset: str, day: date) -> str:
    return f"official_chips/{exchange}_{dataset}/{day:%Y}/{day:%Y%m%d}"


def _tables(payload: object) -> list[dict]:
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
        return []
    return [table for table in (payload.get("tables") or [payload]) if isinstance(table, dict) and table.get("data")]


def _value(text: object) -> float | None:
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = str(text or "").strip().rstrip("%")
    return number(cleaned) if cleaned not in ("", "-", "--", "N/A") else None


def _column(fields: list[str], *names: str, start: int = 0) -> int:
    """The first field at or after ``start`` that equals one of ``names`` (spaces ignored)."""
    clean = [str(field).replace(" ", "") for field in fields]
    for index in range(start, len(clean)):
        if clean[index] in names:
            return index
    raise ValueError(f"欄位不見了：{names}（{fields}）")


def parse(exchange: str, dataset: str, payload: object, day: date) -> list[dict[str, object]]:
    """Rows in the chips parquet's columns: date, code and the dataset's values."""
    rows: list[dict[str, object]] = []
    for table in _tables(payload):
        fields = table.get("fields") or []
        try:
            if dataset == "holding":
                code_at = _column(fields, "證券代號", "代號")
                shares_at = _column(fields, "發行股數", "發行股數(A)")
                ratio_at = _column(fields, "全體外資及陸資持股比率", "僑外資及陸資持股比率(E=C/A)")
            elif dataset == "per":
                code_at = _column(fields, "證券代號", "股票代號")
                per_at = _column(fields, "本益比")
                pbr_at = _column(fields, "股價淨值比")
            elif dataset == "margin":
                code_at = _column(fields, "代號", "股票代號")
                if exchange == "twse":       # 融資 then 融券, each with its own 今日餘額
                    margin_at = _column(fields, "今日餘額")
                    short_at = _column(fields, "今日餘額", start=margin_at + 1)
                else:
                    margin_at = _column(fields, "資餘額")
                    short_at = _column(fields, "券餘額")
            else:
                code_at = _column(fields, "證券代號", "代號")
        except ValueError:
            continue
        for item in table["data"]:
            code = str(item[code_at]).strip()
            if not STOCK.match(code):
                continue
            row: dict[str, object] = {"date": day, "code": code}
            if dataset == "holding":
                row.update(ForeignInvestmentSharesRatio=_value(item[ratio_at]), NumberOfSharesIssued=_value(item[shares_at]))
            elif dataset == "per":
                row.update(PER=_value(item[per_at]), PBR=_value(item[pbr_at]))
            elif dataset == "margin":
                row.update(MarginPurchaseTodayBalance=_value(item[margin_at]), ShortSaleTodayBalance=_value(item[short_at]))
            elif exchange == "twse":
                # T86: foreign excluding their dealers (net at 4), foreign dealers (7), investment trust (10)
                row.update(foreign_net=(_value(item[4]) or 0.0) + (_value(item[7]) or 0.0), trust_net=_value(item[10]) or 0.0)
            else:
                # dailyTrade: foreign excl. dealers 2-4, foreign dealers 5-7, foreign total 8-10, trust 11-13
                row.update(foreign_net=_value(item[10]) or 0.0, trust_net=_value(item[13]) or 0.0)
            rows.append(row)
        if rows:
            break
    return rows


def fetch(base: Path, client: OfficialHistoryClient, sessions: Iterable[date], today: date,
          exchanges: tuple[str, ...] = ("twse", "tpex")) -> dict[str, object]:
    """Request (or read from the cache) every dataset of every session; returns rows per dataset."""
    counts: dict[str, int] = {}
    days = sorted(sessions)
    for day in days:
        for exchange in exchanges:
            for dataset in DATASETS:
                payload = client._cached(key(exchange, dataset, day), url(exchange, dataset, day),
                                         final=day < today, period_end=day)
                counts[f"{exchange}_{dataset}"] = counts.get(f"{exchange}_{dataset}", 0) + len(
                    parse(exchange, dataset, payload, day))
    return {"sessions": len(days), "first": days[0].isoformat() if days else None,
            "last": days[-1].isoformat() if days else None, "rows": counts, "requests": client.requests}


def cached_rows(base: Path, dataset: str) -> Iterator[dict[str, object]]:
    """Every cached report's rows for one dataset (both exchanges), oldest first."""
    import json

    raw = Path(base) / "raw" / "official_chips"
    for exchange in ("twse", "tpex"):
        folder = raw / f"{exchange}_{dataset}"
        for path in sorted(folder.rglob("*.json")) if folder.is_dir() else []:
            stem = path.stem
            day = date(int(stem[:4]), int(stem[4:6]), int(stem[6:8]))
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            yield from parse(exchange, dataset, payload, day)
