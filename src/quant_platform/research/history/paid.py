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

Each table is the day's, published after the close: used from the next session (research/chips.py).

2026-11-02: the tables stopped on 10-21 (the per-stock downloads of 10-21/22, nothing daily since); from then on the
four per-stock tables come a session at a time for every stock (``raw/finmind/<dataset>/days/<date>.json.gz``,
history/finmind_daily.py), used for every day that has such a file. ``paid/through.json``: the last day each table
covers — read by research/chips.py, after which a factor is unknown, not 0."""

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
DAYS = "days"                 # raw/finmind/<dataset>/days/<date>.json.gz: a session, every stock (2026-11-02)
THROUGH = "through.json"


def day_files(base: str | Path, dataset: str) -> dict[date, Path]:
    found = {}
    for path in sorted((Path(base) / "raw" / "finmind" / dataset / DAYS).glob("*.json.gz")):
        try:
            found[date.fromisoformat(path.name[:10])] = path
        except ValueError:
            continue
    return found


def write_through(base: str | Path, covered: dict[str, date]) -> None:
    """``paid/through.json``: the last day each table covers (merged with what is there — the branch signals')."""
    import json

    from quant_platform.research import files

    target = folder(base) / THROUGH
    known = {}
    if target.is_file():
        try:
            known = json.loads(target.read_text(encoding="utf-8"))
        except ValueError:
            known = {}
    known.update({name: day.isoformat() for name, day in covered.items() if day is not None})
    target.parent.mkdir(parents=True, exist_ok=True)
    files.write_text(target, json.dumps(known, ensure_ascii=False, indent=1, sort_keys=True))


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
    days = [path.name[:10] for path in (base / "raw" / "finmind" / "govbank").glob("*.json.gz")]
    write_through(base, {GOVBANK: date.fromisoformat(max(days)) if days else None})   # a file a day, rows or none
    return len(frame)


def build(base: str | Path, datasets: tuple[str, ...] = (*TABLES, GOVBANK)) -> dict[str, int]:
    """Every stored stock file of each dataset into ``paid/<dataset>.parquet`` (date, code, the kept fields; block
    trades summed by day). Rows without a date or code, or with fields that are not numbers, are left out."""
    import gzip
    import json

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
        by_day = day_files(base, dataset)
        rows = []
        newest = None

        def take(row: dict) -> None:
            nonlocal newest
            try:
                day = date.fromisoformat(str(row["date"])[:10])
                found = {"date": day, "code": str(row["stock_id"]), **{field: float(row[field]) for field in fields}}
            except (KeyError, TypeError, ValueError):
                return
            rows.append(found)
            newest = day if newest is None or day > newest else newest

        replaced = {day.isoformat() for day in by_day}
        for path in sorted(source.glob("*.json.gz")) if source.is_dir() else []:
            for row in read_rows(base, dataset, path.name.split(".")[0]):
                if str(row.get("date") or "")[:10] not in replaced:
                    take(row)
        for path in by_day.values():                  # the day's file has every stock: it replaces the stocks' rows
            for row in json.loads(gzip.decompress(path.read_bytes()).decode("utf-8")).get("data") or []:
                take(row)
        frame = pd.DataFrame(rows, columns=["date", "code", *fields])
        if dataset == "TaiwanStockBlockTrade" and len(frame):
            frame = frame.groupby(["date", "code"], as_index=False)[list(fields)].sum()
        frame = frame.drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"])
        target = folder(base) / f"{dataset}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target, index=False)
        write_through(base, {dataset: max([day for day in (newest, max(by_day) if by_day else None) if day], default=None)})
        counts[dataset] = len(frame)
    return counts


def update(base: str | Path) -> dict[str, int]:
    """2026-11-02 (paiddaily, twice a day): the days downloaded since a table was built, added to it — not every stock
    file again (the margin table alone is 7 million rows from 2001). The same table as ``build``: a day with a
    whole-market file replaces the stocks' rows of that day; the government banks' days not in the table are read.
    A table not built yet is built whole."""
    import gzip
    import json

    import pandas as pd

    base = Path(base)
    counts = {}
    for dataset in (*TABLES, GOVBANK):
        target = folder(base) / f"{dataset}.parquet"
        if not target.is_file():
            counts.update(build(base, (dataset,)))
            continue
        table = pd.read_parquet(target)
        known = set(pd.to_datetime(table["date"]).dt.date)
        if dataset == GOVBANK:
            source = base / "raw" / "finmind" / "govbank"
            days = {date.fromisoformat(path.name[:10]): path for path in sorted(source.glob("*.json.gz"))}
            rows = []
            for day, path in days.items():
                if day in known:
                    continue
                payload = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
                rows += [{"date": day, "code": str(code), "buy": float(bought), "sell": float(sold),
                          "buy_amount": float(buy_money), "sell_amount": float(sell_money)}
                         for code, bought, sold, buy_money, sell_money in payload.get("rows") or []]
            covered = max(days) if days else None
        else:
            fields = TABLES[dataset]
            days = day_files(base, dataset)
            table = table[~pd.to_datetime(table["date"]).dt.date.isin(set(days))]
            rows = []
            for path in days.values():
                for row in json.loads(gzip.decompress(path.read_bytes()).decode("utf-8")).get("data") or []:
                    try:
                        rows.append({"date": date.fromisoformat(str(row["date"])[:10]), "code": str(row["stock_id"]),
                                     **{field: float(row[field]) for field in fields}})
                    except (KeyError, TypeError, ValueError):
                        continue
            covered = max([day for day in (max(known) if known else None, max(days) if days else None) if day], default=None)
        added = pd.DataFrame(rows, columns=list(table.columns))
        if dataset == "TaiwanStockBlockTrade" and len(added):
            added = added.groupby(["date", "code"], as_index=False)[list(TABLES[dataset])].sum()
        frame = pd.concat([table.assign(date=pd.to_datetime(table["date"]).dt.date), added], ignore_index=True)
        frame = frame.drop_duplicates(["date", "code"], keep="last").sort_values(["date", "code"])
        partial = target.with_suffix(".partial")
        frame.to_parquet(partial, index=False)
        partial.replace(target)
        write_through(base, {dataset: covered})
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
