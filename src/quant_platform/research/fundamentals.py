"""Quarterly statement factors (research roadmap R5, 2026-10-06).

FinMind's statements (income statement single quarters since 2000, balance sheet since 2012, listed and
TPEx) are cut down to one row per company and quarter with the items the factors need, and dated by the
legal filing deadline — usable from the day after it, later than most companies actually file, so a
backtest never sees a quarter early:

| quarter ending | deadline |
|---|---|
| March | May 15 |
| June | August 31 (the latest, financial companies) |
| September | November 14 |
| December | March 31 of the next year |

Factors (higher = better on the factor's own terms), each from the latest quarter usable that day:
roe_ttm (the last four quarters' net income to owners ÷ equity), gross_margin (four quarters' gross profit
÷ revenue), operating_margin_change (the quarter's operating margin minus the same quarter a year before),
eps_growth (EPS against the same quarter a year before, ÷ its absolute value, clipped to ±5) and low_debt
(−liabilities ÷ assets). Banks and insurers have no gross profit: those stay empty.

2026-10-28 (research_method §7「季報盈餘驚喜」): the earnings-news family, EARNINGS_FACTORS — eps_surprise, the
standardized unexpected earnings of a seasonal random walk with drift (Foster, Olsen & Shevlin 1984; Bernard & Thomas
1989): the EPS change against the same quarter a year before, less the average of the SURPRISE_QUARTERS changes before
it, ÷ their standard deviation; eps_accel, that change less the previous quarter's, ÷ the same spread (He &
Narayanamoorthy 2020: earnings acceleration predicts returns beyond the surprise); and eps_streak, how many quarters in a
row the EPS has beaten the same quarter a year before (Loh & Warachka 2012: streaks of surprises drift further). Kept out of STATEMENT_FACTORS, so the factor lists
the models, robots and searches build from daily.FACTOR_LABELS do not change under them.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

STATEMENT_FACTORS = {
    "roe_ttm": "股東權益報酬率（近四季）",
    "gross_margin": "毛利率（近四季）",
    "operating_margin_change": "營業利益率比去年同季增加",
    "eps_growth": "每股盈餘比去年同季成長",
    "low_debt": "負債比低",
}
EARNINGS_FACTORS = {
    "eps_surprise": "季報盈餘驚喜（每股盈餘比去年同季的增減，扣掉過去 8 季增減的平均，÷ 它們的標準差）",
    "eps_accel": "盈餘加速（這季比去年同季的增減，減上一季的增減，÷ 過去 8 季增減的標準差）",
    "eps_streak": "盈餘連續成長（每股盈餘連續幾季比去年同季高，最多 8 季）",
}
SURPRISE_QUARTERS = 8        # eps_surprise: the earlier seasonal changes its expectation and spread come from
SURPRISE_LEAST = 4           # at least this many of them
STREAK_MOST = 8
INCOME = {"Revenue": "revenue", "GrossProfit": "gross_profit", "OperatingIncome": "operating_income",
          "EquityAttributableToOwnersOfParent": "net_income", "EPS": "eps"}
BALANCE = {"TotalAssets": "assets", "Liabilities": "liabilities", "EquityAttributableToOwnersOfParent": "equity"}
FILE = Path("fundamentals") / "quarterly.parquet"
STALE = 260                  # sessions a quarter's values are carried before they count as missing


def deadline(quarter: date) -> date:
    """The day a quarter's statements are usable: the day after the legal filing deadline."""
    if quarter.month == 3:
        last = date(quarter.year, 5, 15)
    elif quarter.month == 6:
        last = date(quarter.year, 8, 31)
    elif quarter.month == 9:
        last = date(quarter.year, 11, 14)
    else:
        last = date(quarter.year + 1, 3, 31)
    return last + timedelta(days=1)


def due_quarter(today: date) -> date:
    """The latest quarter whose filing deadline has passed by ``today`` (its statements are usable)."""
    ends = [date(year, month, day) for year in (today.year - 1, today.year)
            for month, day in ((3, 31), (6, 30), (9, 30), (12, 31))]
    return max(quarter for quarter in ends if deadline(quarter) <= today)


def _read(path: Path) -> list[dict]:
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8")).get("data") or []


def build(history: str | Path) -> dict[str, int]:
    """history/fundamentals/quarterly.parquet: code, quarter, available and the statement items."""
    base = Path(history)
    raw = base / "raw" / "finmind"
    rows: dict[tuple[str, date], dict[str, object]] = {}
    for dataset, items in (("TaiwanStockFinancialStatements", INCOME), ("TaiwanStockBalanceSheet", BALANCE)):
        for path in sorted((raw / dataset).glob("*.json.gz")):
            code = path.name.split(".")[0]
            for item in _read(path):
                name = items.get(item.get("type"))
                value = item.get("value")
                if name is None or not isinstance(value, (int, float)):
                    continue
                quarter = date.fromisoformat(item["date"])
                if quarter.month not in (3, 6, 9, 12):
                    continue
                row = rows.setdefault((code, quarter), {"code": code, "quarter": quarter, "available": deadline(quarter)})
                row[name] = float(value)
    names = list(INCOME.values()) + list(BALANCE.values())
    ordered = sorted(rows.values(), key=lambda row: (row["code"], row["quarter"]))
    table = pa.table({"code": [row["code"] for row in ordered], "quarter": [row["quarter"] for row in ordered],
                      "available": [row["available"] for row in ordered],
                      **{name: [row.get(name) for row in ordered] for name in names}})
    out = base / FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_suffix(".parquet.partial")
    pq.write_table(table, partial, compression="zstd")
    partial.replace(out)
    return {"rows": table.num_rows, "codes": len({row["code"] for row in ordered})}


def factor_table(frame: pd.DataFrame) -> pd.DataFrame:
    """One row per company and quarter with the five factors (from consecutive quarters only)."""
    frame = frame.sort_values(["code", "quarter"]).reset_index(drop=True)
    frame["index"] = frame["quarter"].map(lambda day: day.year * 4 + (day.month - 1) // 3)
    output = []
    for _code, group in frame.groupby("code", sort=False):
        group = group.set_index("index")
        span = range(group.index.min(), group.index.max() + 1)
        full = group.reindex(span)                                   # missing quarters stay as gaps
        four = lambda column, quarters=full: quarters[column].rolling(4, min_periods=4).sum()     # noqa: E731
        with np.errstate(divide="ignore", invalid="ignore"):
            equity = full["equity"].where(full["equity"] > 0)
            revenue4 = four("revenue").where(four("revenue") > 0)
            margin = full["operating_income"] / full["revenue"].where(full["revenue"] > 0)
            previous_eps = full["eps"].shift(4)
            change = full["eps"] - previous_eps                      # the seasonal change (NaN across a gap)
            earlier = change.shift(1).rolling(SURPRISE_QUARTERS, min_periods=SURPRISE_LEAST)
            spread = earlier.std()
            beat = (change > 0).astype(float).where(change.notna())
            run = beat.fillna(0).groupby((beat.fillna(0) == 0).cumsum()).cumsum()
            factors = pd.DataFrame({
                "roe_ttm": four("net_income") / equity,
                "gross_margin": four("gross_profit") / revenue4,
                "operating_margin_change": margin - margin.shift(4),
                "eps_growth": ((full["eps"] - previous_eps) / previous_eps.abs().where(previous_eps != 0)).clip(-5, 5),
                "low_debt": -(full["liabilities"] / full["assets"].where(full["assets"] > 0)),
                "eps_surprise": ((change - earlier.mean()) / spread.where(spread > 0)).clip(-10, 10),
                "eps_accel": ((change - change.shift(1)) / spread.where(spread > 0)).clip(-10, 10),
                "eps_streak": run.clip(upper=STREAK_MOST).where(change.notna()),
            }, index=full.index)
        factors["code"], factors["available"] = full["code"], full["available"]
        output.append(factors[full["code"].notna()])
    return (pd.concat(output) if output
            else pd.DataFrame(columns=["code", "available", *STATEMENT_FACTORS, *EARNINGS_FACTORS]))


class FundamentalStore:
    """The statement factors on a factor panel's sessions × stocks (symbols × sessions)."""

    def __init__(self, history: str | Path, sessions: list[date], symbols: list[str], until: date | None = None) -> None:
        self._path = Path(history) / FILE
        self.until = until                      # 2026-10-11 (leakcheck): only statements usable by ``until``
        self.sessions, self.symbols = sessions, symbols
        self._codes = [symbol.split(".")[0] for symbol in symbols]
        self._factors: pd.DataFrame | None = None

    def available(self) -> bool:
        return self._path.is_file()

    def _table(self) -> pd.DataFrame:
        if self._factors is None:
            self._factors = factor_table(pq.read_table(self._path).to_pandas())
        return self._factors

    def matrix(self, factor: str) -> np.ndarray:
        table = self._table().dropna(subset=["available"])
        if self.until is not None:
            table = table[pd.to_datetime(table["available"]) <= pd.Timestamp(self.until)]
        frame = table.sort_values("available").pivot_table(index="available", columns="code", values=factor,
                                                           aggfunc="last")
        frame.index = pd.to_datetime(frame.index)
        sessions = pd.to_datetime(pd.Index(self.sessions))
        frame = frame.reindex(sessions.union(frame.index)).ffill(limit=STALE).reindex(sessions)
        frame = frame.reindex(columns=self._codes)
        return np.ascontiguousarray(frame.to_numpy(dtype=np.float32).T)


def digest(history: str | Path, until: date) -> str:
    """The statements a period could see (usable by ``until``): part of a statement rule's trial input."""
    path = Path(history) / FILE
    if not path.is_file():
        return "missing"
    frame = pq.read_table(path).to_pandas()
    frame = frame[frame["available"] <= until].sort_values(["code", "quarter"]).reset_index(drop=True)
    return hashlib.sha256(pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes()).hexdigest()
