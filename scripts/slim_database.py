"""One-off SQLite slimming for S1-W03 (run with all services stopped).

Steps: exclusive lock check -> WAL checkpoint -> full online backup ->
drop unused single-column indexes -> archive superseded model predictions to
verified Parquet -> VACUUM INTO a new file -> quick_check and per-table row
counts -> swap files (the pre-vacuum file is moved to instance/backups, never
deleted).

    .\\.venv\\Scripts\\python.exe scripts\\slim_database.py            # dry run
    .\\.venv\\Scripts\\python.exe scripts\\slim_database.py --execute
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

from quant_platform.application.prediction_archive import (
    PredictionArchiveService,
    active_registry_experiment_ids,
)
from quant_platform.database.engine import Database
from quant_platform.database.repositories import (
    SqlAlchemyModelGovernanceRepository,
    SqlAlchemyModelResearchRepository,
)

UNUSED_INDEXES = (
    "ix_model_predictions_experiment_id",  # leading column of uq_model_prediction_snapshot
    "ix_model_predictions_market",
    "ix_model_predictions_model_name",
    "ix_model_predictions_label_name",
    "ix_model_predictions_event_time",
    "ix_model_predictions_available_time",
    "ix_model_predictions_computed_at",
    "ix_feature_values_symbol",  # leading column of uq_feature_value_versioned
    "ix_feature_values_event_time",
    "ix_feature_values_available_time",
    "ix_feature_values_computed_at",
)


def log(message: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {message}", flush=True)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = [
        row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    return {
        name: connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        for name in names
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="instance/quant_platform.db")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    database_path = Path(args.db).resolve()
    instance = database_path.parent
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = instance / "backups"
    archive_dir = instance / "research" / "predictions"
    report = {"database": str(database_path), "started": stamp, "executed": args.execute}

    database = Database(f"sqlite:///{database_path.as_posix()}")
    models = SqlAlchemyModelResearchRepository(database.session_factory)
    governance = SqlAlchemyModelGovernanceRepository(database.session_factory)
    archive = PredictionArchiveService(
        models, archive_dir,
        registry_ids=lambda: active_registry_experiment_ids(governance.list_entries()),
    )
    keep, to_archive = archive.plan()
    log(f"experiments kept in database: {sorted(keep)}")
    log(f"experiments to archive: {len(to_archive)}")
    report.update(keep=sorted(keep), archive_count=len(to_archive))
    if not args.execute:
        log("dry run only; pass --execute to change the database")
        return 0

    connection = sqlite3.connect(database_path, timeout=5, isolation_level=None)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        connection.execute("COMMIT")
    except sqlite3.OperationalError as exc:
        log(f"database is in use ({exc}); stop services first")
        return 2
    log("checkpointing WAL")
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"quant_platform-pre-s1w03-{stamp}.db"
    log(f"full backup -> {backup_path}")
    started = time.time()
    with closing(sqlite3.connect(backup_path)) as target:
        connection.backup(target, pages=65536)
    log(f"backup done in {time.time() - started:.0f}s; hashing")
    report["backup"] = {"path": str(backup_path), "bytes": backup_path.stat().st_size,
                        "sha256": file_sha256(backup_path)}
    with closing(sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)) as check:
        report["backup"]["quick_check"] = check.execute("PRAGMA quick_check").fetchone()[0]
    log(f"backup quick_check: {report['backup']['quick_check']}")
    if report["backup"]["quick_check"] != "ok":
        log("backup failed verification; stopping")
        return 3

    for name in UNUSED_INDEXES:
        connection.execute(f'DROP INDEX IF EXISTS "{name}"')
    log(f"dropped {len(UNUSED_INDEXES)} unused indexes")
    connection.close()

    started = time.time()
    archived = archive.apply()
    log(f"archived {len(archived)} experiments, "
        f"{sum(item.rows for item in archived):,} rows in {time.time() - started:.0f}s")
    report["archived_rows"] = sum(item.rows for item in archived)
    database.engine.dispose()

    connection = sqlite3.connect(database_path, isolation_level=None)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = table_counts(connection)
    slim_path = instance / f"quant_platform.slim-{stamp}.db"
    log(f"VACUUM INTO {slim_path}")
    started = time.time()
    connection.execute("VACUUM INTO ?", (str(slim_path),))
    connection.close()
    log(f"vacuum done in {time.time() - started:.0f}s")
    # sqlite3 context managers only commit; closing() releases the file for the swap.
    with closing(sqlite3.connect(slim_path)) as slim:
        quick = slim.execute("PRAGMA quick_check").fetchone()[0]
        after = table_counts(slim)
    mismatched = {
        name: (before[name], after.get(name))
        for name in before if before[name] != after.get(name)
    }
    report.update(slim_quick_check=quick, mismatched_tables=mismatched)
    log(f"slim quick_check: {quick}; mismatched tables: {mismatched or 'none'}")
    if quick != "ok" or mismatched:
        log("slim copy failed verification; original database left in place")
        return 4

    retired = backup_dir / f"quant_platform-pre-vacuum-{stamp}.db"
    database_path.replace(retired)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{database_path}{suffix}")
        if sidecar.exists():
            sidecar.replace(Path(f"{retired}{suffix}"))
    slim_path.replace(database_path)
    report.update(
        pre_vacuum_moved_to=str(retired),
        bytes_before=retired.stat().st_size,
        bytes_after=database_path.stat().st_size,
    )
    log(f"swapped: {report['bytes_before'] / 1024**3:.2f} GiB -> "
        f"{report['bytes_after'] / 1024**3:.2f} GiB")
    report_path = backup_dir / f"slim-report-{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"report -> {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
