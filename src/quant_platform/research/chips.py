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
import warnings
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
# 2026-10-09: an event factor, fresh revenue news only. Quantified fundamental news drifts for weeks after
# it is public while the 70-day revenue_yoy holds stale months (AI-methods review); this one exists only in
# the 20 sessions after a monthly revenue is announced. Kept apart so the 2026-10-04 chip batch stays as run.
EVENT_FACTORS = {"revenue_accel": "月營收加速（公布後 20 個交易日內：最新年增率減前 3 個月平均）"}
ACCEL_SESSIONS = 20
# 2026-10-14 (資料缺口：借券賣出餘額、當沖比例): short selling through securities lending, and day trading, from
# FinMind's daily tables (published after the close: used from the next session). Kept apart from the 2026-10-04
# chip batch. Higher = what the factor favours: little lent-and-sold stock, shorts covering, little day trading.
SHORT_FACTORS = {
    "low_sbl_short": "借券賣出餘額低（佔發行股數，反向）",
    "sbl_cover_20": "借券賣出回補（20 日餘額減少，佔發行股數）",
    "low_day_trade_20": "當沖比例低（20 日當沖買進金額佔成交值，反向）",
}
FIELDS = {
    "TaiwanStockShareholding": ("ForeignInvestmentSharesRatio", "NumberOfSharesIssued"),
    "TaiwanStockPER": ("PER", "PBR"),
    "TaiwanStockMarginPurchaseShortSale": ("MarginPurchaseTodayBalance", "ShortSaleTodayBalance"),
}
SHORT_FIELDS = {
    "TaiwanDailyShortSaleBalances": ("SBLShortSalesCurrentDayBalance", "MarginShortSalesCurrentDayBalance"),
    "TaiwanStockDayTrading": ("BuyAmount", "Volume"),
}


def _rows(raw: Path, dataset: str):
    for path in sorted((raw / dataset).glob("*.json.gz")):
        body = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        yield path.name.split(".")[0], body.get("data") or []


OFFICIAL = {"TaiwanStockShareholding": "holding", "TaiwanStockPER": "per",
            "TaiwanStockMarginPurchaseShortSale": "margin", "TaiwanStockInstitutionalInvestorsBuySell": "insti"}


def _add_official(base: Path, dataset: str, columns: dict[str, list]) -> int:
    """S9-W04 (2026-10-06): the exchanges' daily reports for the (day, code) pairs FinMind's files lack."""
    from quant_platform.research.history.chips_daily import cached_revenue_rows, cached_rows

    if dataset == "TaiwanStockMonthRevenue":
        rows = cached_revenue_rows(base)          # the month's first table that lists the company
    elif dataset in OFFICIAL:
        rows = cached_rows(base, OFFICIAL[dataset])
    else:
        return 0
    seen = set(zip(columns["date"], columns["code"], strict=True))
    added = 0
    for row in rows:
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
    steps += [dataset for dataset in SHORT_FIELDS if (raw / dataset).is_dir()]      # once downloaded
    if job:
        job.update(total=len(steps), done=0, force=True)
    for index, dataset in enumerate(steps):
        if job:
            job.update(done=index, current=dataset)
        columns: dict[str, list] = {"date": [], "code": []}
        fields = FIELDS.get(dataset) or SHORT_FIELDS.get(dataset)
        if fields:
            for name in fields:
                columns[name] = []
            for code, rows in _rows(raw, dataset):
                for row in rows:
                    columns["date"].append(date.fromisoformat(row["date"]))
                    columns["code"].append(code)
                    for name in fields:
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
        if dataset not in SHORT_FIELDS:
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

    def __init__(self, history: str | Path, sessions: list[date], symbols: list[str], close: np.ndarray,
                 until: date | None = None) -> None:
        self._folder = Path(history) / "chips"
        # 2026-10-11 (research/leakcheck.py): only what was public by the close of ``until`` — daily tables
        # dated before it, monthly revenue available before it; None reads everything
        self.until = until
        self.sessions, self.symbols = sessions, symbols
        self._codes = [symbol.split(".")[0] for symbol in symbols]
        self._close = close                                     # symbols × sessions, unadjusted
        self._frames: dict[str, pd.DataFrame] = {}

    def available(self) -> bool:
        return (self._folder / "TaiwanStockShareholding.parquet").is_file()

    def short_available(self) -> bool:
        return all((self._folder / f"{dataset}.parquet").is_file() for dataset in SHORT_FIELDS)

    def short_matrix(self, factor: str, turnover: np.ndarray) -> np.ndarray:
        """symbols × sessions, SHORT_FACTORS; ``turnover`` (symbols × sessions, NT$) for the day-trading share."""
        if factor in ("low_sbl_short", "sbl_cover_20"):
            shares = self._daily("TaiwanStockShareholding", "NumberOfSharesIssued")
            balance = self._daily("TaiwanDailyShortSaleBalances", "SBLShortSalesCurrentDayBalance")
            ratio = balance / shares.where(shares > 0)
            frame = -ratio if factor == "low_sbl_short" else -(ratio - ratio.shift(20))
        elif factor == "low_day_trade_20":
            bought = self._daily("TaiwanStockDayTrading", "BuyAmount")             # dated the day before
            value = pd.DataFrame(turnover.T, index=bought.index, columns=bought.columns).shift(1)
            frame = -(bought.rolling(20, min_periods=10).sum() / value.rolling(20, min_periods=10).sum().where(lambda v: v > 0))
        else:
            raise ValueError(factor)
        return np.ascontiguousarray(frame.to_numpy(dtype=np.float32).T)

    def _daily(self, dataset: str, field: str) -> pd.DataFrame:
        """sessions × codes, the value known on each session (dated the day before), carried forward
        up to 10 sessions over gaps."""
        key = f"{dataset}:{field}"
        if key not in self._frames:
            table = pq.read_table(self._folder / f"{dataset}.parquet", columns=["date", "code", field]).to_pandas()
            if self.until is not None:
                table = table[pd.to_datetime(table["date"]) < pd.Timestamp(self.until)]
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
        if self.until is not None:
            table = table[pd.to_datetime(table["date"]) < pd.Timestamp(self.until)]
        frame = table.pivot_table(index="date", columns="code", values=field, aggfunc="sum")
        frame.index = pd.to_datetime(frame.index)
        sessions = pd.to_datetime(pd.Index(self.sessions))
        frame = frame.reindex(sessions).reindex(columns=self._codes)
        # a session without a record is zero net buying once the data starts (2012-05)
        started = frame.notna().cumsum() > 0
        frame = frame.fillna(0).where(started)
        return frame.rolling(window, min_periods=window).sum().shift(1)

    def _revenue(self, accel: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Latest month's revenue year-on-year growth and the 3-month sum's, as known on each session; with
        ``accel`` the latest growth minus the previous three months' average, for ACCEL_SESSIONS after it
        is announced (first element)."""
        table = pq.read_table(self._folder / "TaiwanStockMonthRevenue.parquet").to_pandas()
        if self.until is not None:
            table = table[pd.to_datetime(table["available"]) < pd.Timestamp(self.until)]
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
        columns = [("yoy", 70), ("yoy3", 70)]
        if accel:
            yoy_by_key = table.set_index(["code", "key"])["yoy"]
            earlier = np.column_stack([yoy_by_key.reindex(list(zip(table["code"], table["key"] - lag))).to_numpy()
                                       for lag in (1, 2, 3)])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)       # no earlier month known: unknown
                table["accel"] = table["yoy"].to_numpy() - np.nanmean(earlier, axis=1)
            columns = [("accel", ACCEL_SESSIONS)]
        outputs = []
        for column, limit in columns:
            frame = table.pivot_table(index="available", columns="code", values=column, aggfunc="last")
            frame.index = pd.to_datetime(frame.index) + pd.Timedelta(days=1)   # usable the day after
            frame = frame.reindex(sessions.union(frame.index)).ffill(limit=limit).reindex(sessions)
            outputs.append(frame.reindex(columns=self._codes))
        return (outputs[0], outputs[0]) if accel else (outputs[0], outputs[1])

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
        elif factor == "revenue_accel":
            frame = self._revenue(accel=True)[0]
        elif factor == "market_cap":
            shares = self._daily("TaiwanStockShareholding", "NumberOfSharesIssued")
            close = pd.DataFrame(self._close.T, index=shares.index, columns=shares.columns)
            frame = np.log((shares * close.shift(1)).where(shares > 0))
        else:
            raise ValueError(factor)
        return np.ascontiguousarray(frame.to_numpy(dtype=np.float32).T)

