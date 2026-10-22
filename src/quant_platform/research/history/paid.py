"""2026-10-21 (使用者付了 FinMind Sponsor：請繼續研究; 路線圖 S9-W22): the paid tables FinMind gave per stock
(raw/finmind/<dataset>/<code>.json.gz, research/history/finmind.py PAID) as one Parquet file each under
``history/paid/`` — not ``history/chips/``, whose every file is part of every rule's data fingerprint: a rule reads
these only through the factors that use them, and its trial input then holds their digest (``digest``).

| dataset | kept | from |
|---|---|---|
| TaiwanStockMarginMaintenance | margin buyers' average cost, the maintenance ratio (%) | 2001 |
| TaiwanStockLoanCollateralBalance | the unrestricted-purpose loans' balance (lots) | 2006-10 |
| TaiwanStockBlockTrade | each day's block trades: shares, money | 2005 (2007 for most) |
| TaiwanStockBrokerDailyConcentration | the top 15 branches' buying and selling (shares) | 2021-06-28 |
| TaiwanStockGovernmentBankBuySell | the eight government banks' shares and money bought and sold, summed (from the per-day files of history/finmind_daily.py) | 2021-06-30 |

Each table is the day's, published after the close: used from the next session (research/chips.py)."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

TABLES = {
    "TaiwanStockMarginMaintenance": ("margin_cost", "margin_maintenance", "margin_balance"),
    "TaiwanStockLoanCollateralBalance": ("UnrestrictedLoanCurrentDayBalance",),
    "TaiwanStockBlockTrade": ("volume", "trading_money"),
    "TaiwanStockBrokerDailyConcentration": ("top_buy_volume", "top_sell_volume"),
}


GOVBANK = "TaiwanStockGovernmentBankBuySell"


def folder(base: str | Path) -> Path:
    return Path(base) / "paid"


def build_govbank(base: str | Path) -> int:
    """2026-10-22: the government banks' per-day files (raw/finmind/govbank/<date>.json.gz) into one table."""
    import gzip
    import json

    import pandas as pd

    base = Path(base)
    rows = []
    for path in sorted((base / "raw" / "finmind" / "govbank").glob("*.json.gz")):
        try:
            payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
            day = date.fromisoformat(str(payload["date"])[:10])
        except (OSError, ValueError, KeyError):
            continue
        for code, bought, sold, buy_money, sell_money in payload.get("rows") or []:
            rows.append({"date": day, "code": str(code), "buy": float(bought), "sell": float(sold),
                         "buy_amount": float(buy_money), "sell_amount": float(sell_money)})
    frame = pd.DataFrame(rows, columns=["date", "code", "buy", "sell", "buy_amount", "sell_amount"])
    frame = frame.drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"])
    target = folder(base) / f"{GOVBANK}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target, index=False)
    return len(frame)


def build(base: str | Path, datasets: tuple[str, ...] = (*TABLES, GOVBANK)) -> dict[str, int]:
    """Every stored stock file of each dataset into ``paid/<dataset>.parquet`` (date, code, the kept fields; block
    trades summed by day). Rows without a date or code, or with fields that are not numbers, are left out."""
    import pandas as pd

    from quant_platform.research.history.finmind import read_rows

    base = Path(base)
    counts = {}
    for dataset in datasets:
        if dataset == GOVBANK:
            counts[dataset] = build_govbank(base)
            continue
        fields = TABLES[dataset]
        source = base / "raw" / "finmind" / dataset
        rows = []
        for path in sorted(source.glob("*.json.gz")) if source.is_dir() else []:
            for row in read_rows(base, dataset, path.name.split(".")[0]):
                try:
                    rows.append({"date": date.fromisoformat(str(row["date"])[:10]), "code": str(row["stock_id"]),
                                 **{field: float(row[field]) for field in fields}})
                except (KeyError, TypeError, ValueError):
                    continue
        frame = pd.DataFrame(rows, columns=["date", "code", *fields])
        if dataset == "TaiwanStockBlockTrade" and len(frame):
            frame = frame.groupby(["date", "code"], as_index=False)[list(fields)].sum()
        frame = frame.drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"])
        target = folder(base) / f"{dataset}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target, index=False)
        counts[dataset] = len(frame)
    return counts


def digest(base: str | Path, until: date, datasets: list[str]) -> str:
    """The rows of ``datasets`` dated by ``until`` (a trial's input)."""
    import pandas as pd

    found = hashlib.sha256()
    for dataset in sorted(datasets):
        path = folder(base) / f"{dataset}.parquet"
        if not path.is_file():
            found.update(f"{dataset}:none".encode())
            continue
        frame = pd.read_parquet(path)
        frame = frame[pd.to_datetime(frame["date"]) <= pd.Timestamp(until)]
        found.update(dataset.encode())
        found.update(pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes())
    return found.hexdigest()
