"""Empty the archived legacy research tables and VACUUM the SQLite store (roadmap S9-W06, owner 2026-10-04).

Run only through scripts/slim-legacy-tables.ps1, which stops the services and copies the database first.
Every table emptied here was archived to instance/archive/legacy (scripts/archive_legacy_tables.py);
the run refuses to touch anything when a table's row count differs from the archived count (it
changed after the archive). The tables keep their schema, so the legacy pages show empty states.
Recovery: stop the services and copy the backup folder's files back (the path is in the log).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from quant_platform.research.jobs import JobLog

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "instance" / "quant_platform.db"
MANIFEST = ROOT / "instance" / "archive" / "legacy" / "manifest.json"
TABLES = (
    "backtest_equity_points", "ensemble_weight_points", "ensemble_equity_points", "model_predictions",
    "feature_values", "backtest_trades", "label_values", "backtest_folds", "portfolio_equity_points",
    "portfolio_allocation_points", "model_explanations", "feature_revisions",
)


def slim(job, database: Path = DATABASE, manifest_path: Path = MANIFEST, tables=TABLES) -> dict[str, object]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))["tables"]
    before = database.stat().st_size
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        counts = {}
        for table in tables:
            if table not in manifest or not manifest[table].get("sha256") and manifest[table]["rows"]:
                raise SystemExit(f"{table} 沒有封存紀錄，停止")
            current = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if current != manifest[table]["rows"]:
                raise SystemExit(f"{table} 現在 {current:,} 列、封存時 {manifest[table]['rows']:,} 列：封存後有變動，停止（沒有刪任何東西）")
            counts[table] = current
        job.update(total=len(tables) + 1, done=0, current="清空已封存的表", force=True)
        connection.execute("BEGIN")
        for index, table in enumerate(tables):
            connection.execute(f'DELETE FROM "{table}"')
            job.update(done=index + 1, current=f"清空 {table}")
        connection.execute("COMMIT")
        job.update(current="VACUUM（重整資料庫，需要幾分鐘）", force=True)
        connection.execute("VACUUM")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    after = database.stat().st_size
    job.update(done=len(tables) + 1, force=True)
    job.payload["summary"] = f"清空 {len(tables)} 張表、{sum(counts.values()):,} 列；資料庫 {before / 2**30:.1f} GB → {after / 2**30:.1f} GB"
    return {"before": before, "after": after, "rows": counts}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backup", required=True, help="slim-legacy-tables.ps1 的備份資料夾（必須存在）")
    parser.add_argument("--tables", default=",".join(TABLES),
                        help="要清空的已封存表，逗號分隔（2026-10-07 起可只清指定的表，例如 taiwan_data_records）")
    args = parser.parse_args()
    tables = tuple(name.strip() for name in args.tables.split(",") if name.strip())
    if not Path(args.backup, "quant_platform.db").is_file():
        raise SystemExit("找不到備份，停止")
    with JobLog(ROOT / "instance" / "research").start("資料庫瘦身（清空已封存的舊研究表、VACUUM）",
                                                       "scripts/slim_legacy_tables.py " + " ".join(sys.argv[1:])) as job:
        result = slim(job, tables=tables)
    print(json.dumps(result, ensure_ascii=False))
