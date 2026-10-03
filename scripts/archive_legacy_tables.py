"""Archive the legacy research tables of the SQLite store to Parquet (roadmap S9-W06, owner 2026-10-04).

Read-only: the services keep running. Each table is read in rowid order in chunks and written to
``instance/archive/legacy/<table>.parquet`` (zstd); ``manifest.json`` records the rows written, the
table's row count and each file's SHA-256, so the drop that follows (after the legacy pipeline is
stopped, with a full backup first) can be checked against it. The run shows on the website
(研究 › 執行中的程式). Usage (project root):

    $env:PYTHONPATH="src"; .venv\\Scripts\\python.exe scripts\\archive_legacy_tables.py
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from quant_platform.research.jobs import JobLog

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "instance" / "quant_platform.db"
OUT = ROOT / "instance" / "archive" / "legacy"
TAIPEI = ZoneInfo("Asia/Taipei")
CHUNK = 200_000
# Legacy research outputs and the legacy pipeline's data (路線圖 S9-W05/W06). App records (plans,
# trades, cash flows, notifications, scheduler runs, paper trading) are not archived here.
TABLES = (
    "backtest_equity_points", "ensemble_weight_points", "ensemble_equity_points", "model_predictions",
    "feature_values", "backtest_trades", "label_values", "backtest_folds", "taiwan_data_records", "market_bars",
    "portfolio_equity_points", "point_in_time_observations", "knowledge_chunks", "portfolio_allocation_points",
    "model_explanations", "daily_decisions", "macro_observations", "feature_revisions", "knowledge_documents",
    "backtest_runs", "regime_states", "ensemble_runs", "model_drift_snapshots", "model_experiments",
    "model_registry_entries", "factor_research_results", "feature_definitions", "daily_research_reports",
    "portfolio_runs", "research_universe", "universe_memberships", "data_quality_snapshots",
)
TYPES = {"INTEGER": pa.int64(), "INT": pa.int64(), "BIGINT": pa.int64(), "REAL": pa.float64(), "FLOAT": pa.float64(),
         "NUMERIC": pa.float64(), "BOOLEAN": pa.bool_(), "BLOB": pa.binary()}


def _type(declared: str) -> pa.DataType:
    name = (declared or "").upper().split("(")[0].strip()
    return TYPES.get(name, pa.string())


def _column(values: list, kind: pa.DataType) -> pa.Array:
    try:
        return pa.array(values, type=kind)
    except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError):
        return pa.array([None if value is None else str(value) for value in values], type=pa.string())


def _fit(values: list, kind: pa.DataType) -> pa.Array:
    """Values in the file's type: text as text, numbers as numbers (a value that is not one becomes null)."""
    try:
        return pa.array(values, type=kind)
    except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError):
        if pa.types.is_string(kind):
            return pa.array([None if value is None else str(value) for value in values], type=kind)
        cast = float if pa.types.is_floating(kind) else int

        def convert(value):
            try:
                return None if value is None else cast(value)
            except (TypeError, ValueError, OverflowError):
                return None

        return pa.array([convert(value) for value in values], type=kind)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def archive(job) -> dict[str, object]:
    connection = sqlite3.connect(f"file:{DATABASE.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=1")
    OUT.mkdir(parents=True, exist_ok=True)
    present = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    tables = [name for name in TABLES if name in present]
    manifest: dict[str, object] = {"database": str(DATABASE), "started_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
                                   "tables": {}}
    job.update(total=len(tables), done=0, force=True)
    for index, table in enumerate(tables):
        info = list(connection.execute(f'PRAGMA table_info("{table}")'))
        names = [row[1] for row in info]
        kinds = [_type(row[2]) for row in info]
        schema = pa.schema([("_rowid", pa.int64())] + list(zip(names, kinds)))
        path = OUT / f"{table}.parquet"
        partial = path.with_suffix(".parquet.partial")
        writer = None
        written, last = 0, -1
        select = ", ".join(f'"{name}"' for name in names)
        while True:
            rows = connection.execute(f'SELECT rowid, {select} FROM "{table}" WHERE rowid > ? ORDER BY rowid LIMIT ?',
                                      (last, CHUNK)).fetchall()
            if not rows:
                break
            columns = list(zip(*rows))
            if writer is None:
                # the first chunk fixes each column's type: the declared one, or text when it does not fit
                arrays = [pa.array(columns[0], type=pa.int64())] + [_column(list(values), kind)
                                                                       for values, kind in zip(columns[1:], kinds)]
                schema = pa.schema([pa.field(name, array.type if not pa.types.is_null(array.type) else pa.string())
                                    for name, array in zip(["_rowid"] + names, arrays)])
                writer = pq.ParquetWriter(partial, schema, compression="zstd")
            arrays = [_fit(list(values), field.type) for values, field in zip(columns, schema)]
            writer.write_table(pa.Table.from_arrays(arrays, schema=schema))
            written += len(rows)
            last = rows[-1][0]
            job.update(done=index, current=f"{table}：{written:,} 列")
        if writer is not None:
            writer.close()
            partial.replace(path)
        count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        manifest["tables"][table] = {"rows": written, "count_at_end": count, "file": path.name if written else None,
                                     "sha256": sha256(path) if written else None,
                                     "bytes": path.stat().st_size if written else 0}
        (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    manifest["finished_at"] = datetime.now(TAIPEI).isoformat(timespec="seconds")
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    job.update(done=len(tables), force=True)
    total = sum(item["rows"] for item in manifest["tables"].values())
    size = sum(item["bytes"] for item in manifest["tables"].values())
    job.payload["summary"] = f"{len(tables)} 張表、{total:,} 列、{size / 2**30:.1f} GB（{OUT}）"
    return manifest


if __name__ == "__main__":
    with JobLog(ROOT / "instance" / "research").start("封存舊研究資料表（SQLite → Parquet，只讀）",
                                                       "scripts/archive_legacy_tables.py " + " ".join(sys.argv[1:])) as job:
        archive(job)
