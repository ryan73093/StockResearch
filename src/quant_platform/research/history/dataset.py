"""Build the long-history research dataset from the official sources (S3-W01).

Layout under ``instance/research/history/``::

    raw/…                  cached official responses (see official.py)
    daily/<key>.parquet    one file per series, sorted by date, unadjusted prices
    manifest.json          rows, dates, sources, SHA-256 and quality per series

The TAIEX monthly history doubles as the trading calendar: 2004–2009 ETF days
come from the daily ETF report for each TAIEX session, and gaps are measured
against it.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Iterable
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from quant_platform.research.history.catalog import (
    SERIES,
    STRESS_PERIODS,
    TAIEX_START,
    TWSE_ETF_DAILY_START,
    TWSE_STOCK_DAY_START,
    HistorySeries,
)
from quant_platform.research.history.official import (
    DailyRow,
    OfficialHistoryClient,
    parse_taiex_month,
    parse_taiex_total_return_month,
    parse_tpex_stock_month,
    parse_twse_etf_day,
    parse_twse_stock_month,
)

TAIPEI = ZoneInfo("Asia/Taipei")
SCHEMA = pa.schema([
    ("date", pa.date32()),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("volume", pa.int64()),
    ("turnover", pa.int64()),
    ("trades", pa.int64()),
    ("note", pa.string()),
    ("source", pa.string()),
])


@dataclass(slots=True)
class SeriesQuality:
    rows: int = 0
    first: str | None = None
    last: str | None = None
    duplicates: int = 0
    no_trade_days: int = 0
    ohlc_inconsistent: list[str] = field(default_factory=list)
    missing_sessions: int = 0
    missing_examples: list[str] = field(default_factory=list)
    split_markers: list[str] = field(default_factory=list)
    stress_covered: dict[str, bool] = field(default_factory=dict)


def months(first: date, last: date) -> Iterable[date]:
    current = date(first.year, first.month, 1)
    while (current.year, current.month) <= (last.year, last.month):
        yield current
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)


class HistoryDataset:
    def __init__(
        self,
        base_dir: str | Path,
        client: OfficialHistoryClient | None = None,
        catalog: tuple[HistorySeries, ...] = SERIES,
        today: Callable[[], date] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.base = Path(base_dir)
        self._today = today or (lambda: datetime.now(TAIPEI).date())
        self.client = client or OfficialHistoryClient(self.base / "raw", today=self._today)
        self.catalog = catalog
        self._progress = progress or (lambda _message: None)

    @property
    def manifest_path(self) -> Path:
        return self.base / "manifest.json"

    def parquet_path(self, key: str) -> Path:
        return self.base / "daily" / f"{key}.parquet"

    # --- collection ------------------------------------------------------
    def _taiex(self) -> list[DailyRow]:
        rows: list[DailyRow] = []
        for month in months(TAIEX_START, self._today()):
            rows.extend(parse_taiex_month(self.client.taiex_month(month)))
        return rows

    def collect(self, keys: Iterable[str] | None = None) -> dict[str, list[DailyRow]]:
        wanted = [item for item in self.catalog if keys is None or item.key in set(keys)]
        today = self._today()
        self._progress("加權指數（交易日曆）")
        taiex = self._taiex()
        sessions = sorted({row.day for row in taiex})
        output: dict[str, list[DailyRow]] = {}
        if any(item.key == "TAIEX" for item in wanted):
            output["TAIEX"] = taiex

        pre_2010 = [
            item for item in wanted
            if item.kind == "twse_etf" and item.first_month < TWSE_STOCK_DAY_START
        ]
        if pre_2010:
            codes = {item.key for item in pre_2010}
            start = max(TWSE_ETF_DAILY_START, min(item.first_month for item in pre_2010))
            days = [day for day in sessions if start <= day < TWSE_STOCK_DAY_START]
            self._progress(f"2004–2009 ETF 每日收盤 {len(days)} 個交易日")
            for index, day in enumerate(days, 1):
                for code, row in parse_twse_etf_day(self.client.twse_etf_day(day), codes).items():
                    output.setdefault(code, []).append(row)
                if index % 250 == 0:
                    self._progress(f"  {day} ({index}/{len(days)})")

        for item in wanted:
            if item.kind == "twse_etf":
                self._progress(f"{item.key} {item.name}")
                first = max(item.first_month, date(2010, 1, 1))
                for month in months(first, today):
                    output.setdefault(item.key, []).extend(
                        parse_twse_stock_month(self.client.twse_stock_month(item.key, month))
                    )
            elif item.kind == "tpex_etf":
                self._progress(f"{item.key} {item.name}")
                for month in months(item.first_month, today):
                    output.setdefault(item.key, []).extend(
                        parse_tpex_stock_month(self.client.tpex_stock_month(item.key, month))
                    )
            elif item.kind == "taiex_tr":
                self._progress(f"{item.key} {item.name}")
                for month in months(max(item.first_month, TAIEX_START), today):
                    output.setdefault(item.key, []).extend(
                        parse_taiex_total_return_month(self.client.taiex_total_return_month(month))
                    )
        output.setdefault("__sessions__", [DailyRow(day, None, None, None, None) for day in sessions])
        return output

    # --- build -----------------------------------------------------------
    def build(self, keys: Iterable[str] | None = None) -> dict[str, object]:
        collected = self.collect(keys)
        sessions = [row.day for row in collected.pop("__sessions__")]
        manifest = self._load_manifest()
        entries = manifest.setdefault("series", {})
        for item in self.catalog:
            if item.key not in collected:
                continue
            rows, quality = clean(collected[item.key], sessions, with_ohlc=item.kind != "taiex_tr")
            if item.backfill is not None:
                source_path = self.base / "total_return" / f"{item.backfill.source}.parquet"
                if source_path.is_file():
                    synthetic, info = synthesize_backfill(item.backfill, rows, read_series(source_path))
                    rows = synthetic + rows
                    quality.update(info)
            path = self.parquet_path(item.key)
            write_parquet(rows, path)
            entries[item.key] = {
                "name": item.name,
                "kind": item.kind,
                "file": str(path.relative_to(self.base)).replace("\\", "/"),
                "sha256": sha256(path),
                "sources": dict(Counter(row.source for row in rows)),
                "note": item.note,
                "quality": quality,
            }
        manifest["generated_at"] = datetime.now(TAIPEI).isoformat(timespec="seconds")
        manifest["requests"] = {"network": self.client.requests, "cache": self.client.cache_hits}
        self.base.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return manifest

    def _load_manifest(self) -> dict[str, object]:
        if self.manifest_path.is_file():
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        return {}


def _quality_dict(quality: SeriesQuality) -> dict[str, object]:
    return {name: getattr(quality, name) for name in SeriesQuality.__slots__}


def clean(
    rows: list[DailyRow], sessions: list[date], with_ohlc: bool = True
) -> tuple[list[DailyRow], dict[str, object]]:
    """Sort, drop duplicates and no-trade days, and measure quality."""
    quality = SeriesQuality()
    by_day: dict[date, DailyRow] = {}
    for row in sorted(rows, key=lambda item: item.day):
        if row.close is None:
            quality.no_trade_days += 1
            continue
        if row.day in by_day:
            quality.duplicates += 1
            continue
        by_day[row.day] = row
    output = [by_day[day] for day in sorted(by_day)]
    quality.rows = len(output)
    if output:
        quality.first = output[0].day.isoformat()
        quality.last = output[-1].day.isoformat()
        span = [day for day in sessions if output[0].day <= day <= output[-1].day]
        missing = [day for day in span if day not in by_day]
        quality.missing_sessions = len(missing)
        quality.missing_examples = [day.isoformat() for day in missing[:10]]
        for name, (start, end) in STRESS_PERIODS.items():
            quality.stress_covered[name] = output[0].day <= start and output[-1].day >= end
    for row in output:
        if "*" in row.note:
            quality.split_markers.append(row.day.isoformat())
        if with_ohlc and None not in (row.open, row.high, row.low, row.close):
            if row.low > min(row.open, row.close) + 1e-9 or row.high < max(row.open, row.close) - 1e-9:
                quality.ohlc_inconsistent.append(row.day.isoformat())
    return output, _quality_dict(quality)


def synthesize_backfill(
    backfill: "Backfill", official: list[DailyRow], source_total_return: list[dict[str, object]],
) -> tuple[list[DailyRow], dict[str, object]]:
    """Closes before the first official row, chained backward from it with the backfill rule, and
    how the rule tracks the official closes where both exist (annualised mean residual = the drag
    the real series implies; stdev = tracking error)."""
    index = {row["date"]: float(row["total_return_index"]) for row in source_total_return
             if row.get("total_return_index")}
    days = sorted(index)
    info: dict[str, object] = {
        "synthetic_days": 0, "synthetic_from": backfill.source, "synthetic_rule": backfill.rule,
        "synthetic_until": None, "calibration": None,
    }
    if not official or len(days) < 2:
        return [], info
    first = official[0]
    daily_drag = backfill.annual_drag / 252
    growth = {day: index[day] / index[previous] - 1 for previous, day in zip(days, days[1:])}
    residuals = []
    previous_close = None
    for row in official:
        if previous_close and row.day in growth and row.close:
            residuals.append(row.close / previous_close - 1 - backfill.leverage * growth[row.day])
        previous_close = row.close
    if len(residuals) > 20:
        info["calibration"] = {
            "overlap_days": len(residuals),
            "implied_annual_drag": round(-statistics.fmean(residuals) * 252, 5),
            "tracking_error_annual": round(statistics.pstdev(residuals) * 252 ** 0.5, 5),
        }
    synthetic: list[DailyRow] = []
    close = first.close
    for previous, day in reversed(list(zip(days, days[1:]))):
        if day > first.day:
            continue
        if day == first.day:
            close = first.close
            continue
        # close[previous] from close[day]: close[day] = close[previous] × (1 + L·g[day] − drag)
        close = close / (1 + backfill.leverage * growth[day] - daily_drag)
        synthetic.append(DailyRow(previous, close, close, close, close, source="synthetic"))
    synthetic.reverse()
    synthetic = [DailyRow(row.day, round(row.open, 4), round(row.high, 4), round(row.low, 4), round(row.close, 4),
                          source="synthetic") for row in synthetic]
    info["synthetic_days"] = len(synthetic)
    info["synthetic_until"] = synthetic[-1].day.isoformat() if synthetic else None
    return synthetic, info


def write_parquet(rows: list[DailyRow], path: Path) -> None:
    table = pa.Table.from_pydict(
        {
            "date": [row.day for row in rows],
            "open": [row.open for row in rows],
            "high": [row.high for row in rows],
            "low": [row.low for row in rows],
            "close": [row.close for row in rows],
            "volume": [row.volume for row in rows],
            "turnover": [row.turnover for row in rows],
            "trades": [row.trades for row in rows],
            "note": [row.note for row in rows],
            "source": [row.source for row in rows],
        },
        schema=SCHEMA,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".parquet.partial")
    pq.write_table(table, partial, compression="zstd")
    partial.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_series(path: Path) -> list[dict[str, object]]:
    return pq.read_table(path).to_pylist()
