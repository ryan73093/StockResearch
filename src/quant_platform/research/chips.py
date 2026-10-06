"""Chip and fundamental factors from FinMind's free tier (research roadmap R13, 2026-10-04).

``build`` turns the downloaded files (raw/finmind/<dataset>/<code>.json.gz) into one compact Parquet per
dataset under history/chips/ (date, code and the numbers used). ``ChipStore`` pivots them onto the factor
panel's sessions and stocks and computes the factors, point in time:

- shareholding, institutional trading, margin and P/E are published in the evening of their date, after
  the after-hours odd-lot auction the rules trade in, so a value dated day t is used from day t+1;
- a monthly revenue is used from the session after its announcement (``create_time``; the 11th of the
  following month when the record has none: the legal deadline is the 10th).

Factors (higher = what the factor favours; the strength analysis tells which direction pays):
外資持股比率、外資持股增加（20 日）、外資買超（20 日，佔發行股數）、投信買超（20 日）、融資增加（20 日）、
券資比、本益比倒數（盈餘殖利率）、淨值比倒數、月營收年增、近 3 個月營收年增、市值（大型股）.
"""

from __future__ import annotations

import gzip
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

CHIP_FACTORS = {
    "foreign_holding": "外資持股比率",
    "foreign_holding_change": "外資持股比率增加（20 日）",
    "foreign_buy_20": "外資買超（20 日，佔發行股數）",
    "trust_buy_20": "投信買超（20 日，佔發行股數）",
    "margin_growth_20": "融資餘額增加（20 日）",
    "short_margin_ratio": "券資比",
    "earnings_yield": "本益比倒數（盈餘殖利率）",
    "book_to_price": "淨值比倒數",
    "revenue_yoy": "月營收年增率",
    "revenue_yoy_3m": "近 3 個月營收年增率",
    "market_cap": "市值（大型股）",
}
FIELDS = {
    "TaiwanStockShareholding": ("ForeignInvestmentSharesRatio", "NumberOfSharesIssued"),
    "TaiwanStockPER": ("PER", "PBR"),
    "TaiwanStockMarginPurchaseShortSale": ("MarginPurchaseTodayBalance", "ShortSaleTodayBalance"),
}


def _rows(raw: Path, dataset: str):
    for path in sorted((raw / dataset).glob("*.json.gz")):
        body = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        yield path.name.split(".")[0], body.get("data") or []


OFFICIAL = {"TaiwanStockShareholding": "holding", "TaiwanStockPER": "per",
            "TaiwanStockMarginPurchaseShortSale": "margin", "TaiwanStockInstitutionalInvestorsBuySell": "insti"}


def _add_official(base: Path, dataset: str, columns: dict[str, list]) -> int:
    """S9-W04 (2026-10-06): the exchanges' daily reports for the (day, code) pairs FinMind's files lack."""
    from quant_platform.research.history.chips_daily import cached_rows

    if dataset not in OFFICIAL:
        return 0
    seen = set(zip(columns["date"], columns["code"], strict=True))
    added = 0
    for row in cached_rows(base, OFFICIAL[dataset]):
        if (row["date"], row["code"]) in seen:
            continue
        seen.add((row["date"], row["code"]))
        for name in columns:
            columns[name].append(row.get(name))
        added += 1
    return added


def build(history: str | Path, job=None) -> dict[str, int]:
    """One Parquet per dataset with the columns the factors use; returns rows written. FinMind's download
    first, then the exchanges' daily reports for the days it does not have (research/history/chips_daily.py)."""
    base = Path(history)
    raw, out = base / "raw" / "finmind", base / "chips"
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    steps = list(FIELDS) + ["TaiwanStockInstitutionalInvestorsBuySell", "TaiwanStockMonthRevenue"]
    if job:
        job.update(total=len(steps), done=0, force=True)
    for index, dataset in enumerate(steps):
        if job:
            job.update(done=index, current=dataset)
        columns: dict[str, list] = {"date": [], "code": []}
        if dataset in FIELDS:
            for name in FIELDS[dataset]:
                columns[name] = []
            for code, rows in _rows(raw, dataset):
                for row in rows:
                    columns["date"].append(date.fromisoformat(row["date"]))
                    columns["code"].append(code)
                    for name in FIELDS[dataset]:
                        value = row.get(name)
                        columns[name].append(float(value) if isinstance(value, (int, float)) else None)
        elif dataset == "TaiwanStockInstitutionalInvestorsBuySell":
            columns.update({"foreign_net": [], "trust_net": []})
            for code, rows in _rows(raw, dataset):
                net: dict[str, list[float]] = {}
                for row in rows:
                    who = row.get("name") or ""
                    slot = net.setdefault(row["date"], [0.0, 0.0])
                    amount = float(row.get("buy") or 0) - float(row.get("sell") or 0)
                    if who in ("Foreign_Investor", "Foreign_Dealer_Self"):
                        slot[0] += amount
                    elif who == "Investment_Trust":
                        slot[1] += amount
                for day, (foreign, trust) in net.items():
                    columns["date"].append(date.fromisoformat(day))
                    columns["code"].append(code)
                    columns["foreign_net"].append(foreign)
                    columns["trust_net"].append(trust)
        else:  # monthly revenue: the month it is for and the day it became public
            columns.update({"available": [], "revenue": [], "year": [], "month": []})
            for code, rows in _rows(raw, dataset):
                for row in rows:
                    year, month = int(row.get("revenue_year") or 0), int(row.get("revenue_month") or 0)
                    if not year or not month or row.get("revenue") is None:
                        continue
                    created = row.get("create_time")
                    following = date(year + (month == 12), month % 12 + 1, 10)
                    try:
                        public = date.fromisoformat(str(created)[:10]) if created else following
                    except ValueError:
                        public = following
                    columns["date"].append(date(year, month, 1))
                    columns["code"].append(code)
                    columns["available"].append(max(public, date(year, month, 28)))
                    columns["revenue"].append(float(row["revenue"]))
                    columns["year"].append(year)
                    columns["month"].append(month)
        _add_official(base, dataset, columns)
        table = pa.table(columns)
        pq.write_table(table, out / f"{dataset}.parquet", compression="zstd")
        written[dataset] = table.num_rows
    if job:
        job.update(done=len(steps), force=True)
        job.payload["summary"] = "、".join(f"{name.removeprefix('TaiwanStock')} {rows:,} 列" for name, rows in written.items())
    return written


class ChipStore:
    """The chip and fundamental factors on a factor panel's sessions × stocks (symbols × sessions)."""

    def __init__(self, history: str | Path, sessions: list[date], symbols: list[str], close: np.ndarray) -> None:
        self._folder = Path(history) / "chips"
        self.sessions, self.symbols = sessions, symbols
        self._codes = [symbol.split(".")[0] for symbol in symbols]
        self._close = close                                     # symbols × sessions, unadjusted
        self._frames: dict[str, pd.DataFrame] = {}

    def available(self) -> bool:
        return (self._folder / "TaiwanStockShareholding.parquet").is_file()

    def _daily(self, dataset: str, field: str) -> pd.DataFrame:
        """sessions × codes, the value known on each session (dated the day before), carried forward
        up to 10 sessions over gaps."""
        key = f"{dataset}:{field}"
        if key not in self._frames:
            table = pq.read_table(self._folder / f"{dataset}.parquet", columns=["date", "code", field]).to_pandas()
            frame = table.pivot_table(index="date", columns="code", values=field, aggfunc="last")
            frame.index = pd.to_datetime(frame.index)
            sessions = pd.to_datetime(pd.Index(self.sessions))
            frame = frame.reindex(sessions.union(frame.index)).ffill(limit=10).reindex(sessions)
            frame = frame.reindex(columns=self._codes)
            self._frames[key] = frame.shift(1)                  # published after the close: next session
        return self._frames[key]

    def _flow(self, field: str, window: int) -> pd.DataFrame:
        table = pq.read_table(self._folder / "TaiwanStockInstitutionalInvestorsBuySell.parquet",
                              columns=["date", "code", field]).to_pandas()
        frame = table.pivot_table(index="date", columns="code", values=field, aggfunc="sum")
        frame.index = pd.to_datetime(frame.index)
        sessions = pd.to_datetime(pd.Index(self.sessions))
        frame = frame.reindex(sessions).reindex(columns=self._codes)
        # a session without a record is zero net buying once the data starts (2012-05)
        started = frame.notna().cumsum() > 0
        frame = frame.fillna(0).where(started)
        return frame.rolling(window, min_periods=window).sum().shift(1)

    def _revenue(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Latest month's revenue year-on-year growth and the 3-month sum's, as known on each session."""
        table = pq.read_table(self._folder / "TaiwanStockMonthRevenue.parquet").to_pandas()
        table = table.sort_values(["code", "date"])
        table["key"] = table["year"] * 12 + table["month"]
        by_key = table.set_index(["code", "key"])["revenue"]
        previous = by_key.reindex(list(zip(table["code"], table["key"] - 12))).to_numpy()
        table["yoy"] = table["revenue"].to_numpy() / previous - 1
        three = table.groupby("code")["revenue"].transform(lambda values: values.rolling(3).sum())
        table["three"] = three
        three_by_key = table.set_index(["code", "key"])["three"]
        previous_three = three_by_key.reindex(list(zip(table["code"], table["key"] - 12))).to_numpy()
        table["yoy3"] = table["three"].to_numpy() / previous_three - 1
        sessions = pd.to_datetime(pd.Index(self.sessions))
        outputs = []
        for column in ("yoy", "yoy3"):
            frame = table.pivot_table(index="available", columns="code", values=column, aggfunc="last")
            frame.index = pd.to_datetime(frame.index) + pd.Timedelta(days=1)   # usable the day after
            frame = frame.reindex(sessions.union(frame.index)).ffill(limit=70).reindex(sessions)
            outputs.append(frame.reindex(columns=self._codes))
        return outputs[0], outputs[1]

    def matrix(self, factor: str) -> np.ndarray:
        """symbols × sessions."""
        if factor == "foreign_holding":
            frame = self._daily("TaiwanStockShareholding", "ForeignInvestmentSharesRatio")
        elif factor == "foreign_holding_change":
            level = self._daily("TaiwanStockShareholding", "ForeignInvestmentSharesRatio")
            frame = level - level.shift(20)
        elif factor in ("foreign_buy_20", "trust_buy_20"):
            shares = self._daily("TaiwanStockShareholding", "NumberOfSharesIssued")
            flow = self._flow("foreign_net" if factor == "foreign_buy_20" else "trust_net", 20)
            frame = flow / shares.where(shares > 0)
        elif factor == "margin_growth_20":
            balance = self._daily("TaiwanStockMarginPurchaseShortSale", "MarginPurchaseTodayBalance")
            frame = balance / balance.shift(20).where(balance.shift(20) > 0) - 1
        elif factor == "short_margin_ratio":
            margin = self._daily("TaiwanStockMarginPurchaseShortSale", "MarginPurchaseTodayBalance")
            short = self._daily("TaiwanStockMarginPurchaseShortSale", "ShortSaleTodayBalance")
            frame = short / margin.where(margin > 0)
        elif factor == "earnings_yield":
            per = self._daily("TaiwanStockPER", "PER")
            frame = 1 / per.where(per > 0)
        elif factor == "book_to_price":
            pbr = self._daily("TaiwanStockPER", "PBR")
            frame = 1 / pbr.where(pbr > 0)
        elif factor in ("revenue_yoy", "revenue_yoy_3m"):
            latest, three = self._revenue()
            frame = latest if factor == "revenue_yoy" else three
        elif factor == "market_cap":
            shares = self._daily("TaiwanStockShareholding", "NumberOfSharesIssued")
            close = pd.DataFrame(self._close.T, index=shares.index, columns=shares.columns)
            frame = np.log((shares * close.shift(1)).where(shares > 0))
        else:
            raise ValueError(factor)
        return np.ascontiguousarray(frame.to_numpy(dtype=np.float32).T)

