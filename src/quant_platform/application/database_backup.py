"""Nightly online SQLite backup, rotation and restore drill (S2-W05).

The backup copies the live database with the SQLite backup API in a single
step, so the copy is one consistent snapshot while other connections keep
writing to the WAL. Every copy is switched to a standalone rollback journal,
checked with ``PRAGMA quick_check`` and its table row counts are written to
``manifest.jsonl``; the restore drill re-opens a copy of the file elsewhere and
compares those counts.
"""

from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import time
from collections.abc import Iterable
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

TAIPEI = ZoneInfo("Asia/Taipei")
JOB_NAME = "database_backup"
JOB_MARKET = "SYSTEM"
BACKUP_PREFIX = "quant_platform-"
BACKUP_SUFFIX = ".db"
PARTIAL_SUFFIX = ".partial"
MANIFEST_NAME = "manifest.jsonl"
# A nightly backup older than this is reported as overdue on the system page.
OVERDUE_AFTER = timedelta(hours=26)


@dataclass(frozen=True, slots=True)
class BackupRecord:
    file: str
    created_at: str
    size_bytes: int
    seconds: float
    quick_check: str
    table_counts: dict[str, int] = field(default_factory=dict)

    @property
    def created(self) -> datetime:
        return datetime.fromisoformat(self.created_at)


@dataclass(frozen=True, slots=True)
class BackupStatus:
    directory: str
    keep: int
    latest: BackupRecord | None
    count: int
    total_bytes: int
    overdue: bool


@dataclass(frozen=True, slots=True)
class RestoreDrillResult:
    backup_file: str
    restored_to: str
    copy_seconds: float
    quick_check: str
    tables_checked: int
    mismatches: dict[str, tuple[int | None, int | None]]
    latest_tw_bar: str | None
    kept_copy: bool

    @property
    def passed(self) -> bool:
        return self.quick_check == "ok" and not self.mismatches


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {
        name: int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
        for name in names
    }


class DatabaseBackupService:
    """Creates, rotates and reports the daily backups in ``backup_dir``."""

    def __init__(
        self,
        database_path: str | Path,
        backup_dir: str | Path,
        keep: int = 7,
        runs: object | None = None,
    ) -> None:
        if keep < 1:
            raise ValueError("keep must be at least 1")
        self._source = Path(database_path)
        self._dir = Path(backup_dir)
        self._keep = keep
        self._runs = runs

    @property
    def backup_dir(self) -> Path:
        return self._dir

    def run(self, now: datetime | None = None) -> BackupRecord:
        started = (now or datetime.now(UTC)).astimezone(UTC)
        run_id = self._runs.start(JOB_NAME, JOB_MARKET, started) if self._runs else None
        try:
            record = self._backup(started)
            removed = self._rotate()
        except Exception as exc:
            logger.exception("Database backup failed")
            if run_id is not None:
                self._runs.finish(run_id, "failed", datetime.now(UTC), "{}", str(exc))
            raise
        if run_id is not None:
            summary = {
                "file": record.file,
                "size_bytes": record.size_bytes,
                "seconds": record.seconds,
                "quick_check": record.quick_check,
                "tables": len(record.table_counts),
                "removed": removed,
            }
            self._runs.finish(
                run_id, "succeeded", datetime.now(UTC), json.dumps(summary, ensure_ascii=False), None
            )
        logger.info(
            "Database backup %s (%.1f GiB) in %.0fs; removed %s old copies",
            record.file, record.size_bytes / 2**30, record.seconds, len(removed),
        )
        return record

    def _backup(self, started: datetime) -> BackupRecord:
        if not self._source.is_file():
            raise FileNotFoundError(f"找不到資料庫 {self._source}")
        self._dir.mkdir(parents=True, exist_ok=True)
        stamp = started.astimezone(TAIPEI).strftime("%Y%m%d-%H%M%S")
        target = self._dir / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
        partial = target.with_name(target.name + PARTIAL_SUFFIX)
        partial.unlink(missing_ok=True)  # leftover of an interrupted run of this job
        clock = time.perf_counter()
        with closing(sqlite3.connect(self._source)) as source, closing(
            sqlite3.connect(partial)
        ) as copy:
            source.backup(copy)  # pages=-1: one step, one consistent snapshot
        with closing(sqlite3.connect(partial)) as copy:
            copy.execute("PRAGMA journal_mode=DELETE")
            check = str(copy.execute("PRAGMA quick_check").fetchone()[0])
            counts = table_counts(copy) if check == "ok" else {}
        if check != "ok":
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"備份檔 quick_check 未通過：{check}")
        partial.replace(target)
        record = BackupRecord(
            file=target.name,
            created_at=started.isoformat(),
            size_bytes=target.stat().st_size,
            seconds=round(time.perf_counter() - clock, 1),
            quick_check=check,
            table_counts=counts,
        )
        with (self._dir / MANIFEST_NAME).open("a", encoding="utf-8") as manifest:
            manifest.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        return record

    def backup_files(self) -> list[Path]:
        """Completed backups, newest first (timestamped names sort by time)."""
        if not self._dir.is_dir():
            return []
        return sorted(
            (
                path for path in self._dir.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}")
                if path.is_file()
            ),
            key=lambda path: path.name,
            reverse=True,
        )

    def _rotate(self) -> list[str]:
        removed: list[str] = []
        for path in self.backup_files()[self._keep:]:
            path.unlink()
            removed.append(path.name)
        return removed

    def records(self) -> list[BackupRecord]:
        """Manifest entries whose file still exists, newest first."""
        manifest = self._dir / MANIFEST_NAME
        if not manifest.is_file():
            return []
        present = {path.name for path in self.backup_files()}
        records: dict[str, BackupRecord] = {}
        for line in manifest.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                record = BackupRecord(**payload)
            except (TypeError, ValueError):
                continue
            if record.file in present:
                records[record.file] = record
        return sorted(records.values(), key=lambda item: item.file, reverse=True)

    def status(self, now: datetime | None = None) -> BackupStatus:
        files = self.backup_files()
        records = self.records()
        latest = records[0] if records else None
        current = (now or datetime.now(UTC)).astimezone(UTC)
        return BackupStatus(
            directory=str(self._dir),
            keep=self._keep,
            latest=latest,
            count=len(files),
            total_bytes=sum(path.stat().st_size for path in files),
            overdue=latest is None or current - latest.created > OVERDUE_AFTER,
        )

    def restore_drill(
        self,
        work_dir: str | Path,
        backup_file: str | None = None,
        keep_copy: bool = False,
    ) -> RestoreDrillResult:
        """Restore a backup to ``work_dir`` and verify it; the live database is untouched."""
        records = {record.file: record for record in self.records()}
        if backup_file is None:
            if not records:
                raise FileNotFoundError(f"{self._dir} 沒有可演練的備份紀錄")
            record = next(iter(sorted(records.values(), key=lambda r: r.file, reverse=True)))
        elif backup_file in records:
            record = records[backup_file]
        else:
            raise FileNotFoundError(f"備份紀錄中沒有 {backup_file}")
        source = self._dir / record.file
        work = Path(work_dir)
        work.mkdir(parents=True, exist_ok=True)
        restored = work / f"restore-drill-{record.file}"
        restored.unlink(missing_ok=True)
        clock = time.perf_counter()
        shutil.copyfile(source, restored)
        copy_seconds = round(time.perf_counter() - clock, 1)
        try:
            with closing(sqlite3.connect(f"file:{restored.as_posix()}?mode=ro", uri=True)) as copy:
                check = str(copy.execute("PRAGMA quick_check").fetchone()[0])
                counts = table_counts(copy)
                latest_bar = copy.execute(
                    "SELECT MAX(event_time) FROM market_bars WHERE market = 'TW'"
                ).fetchone()[0] if "market_bars" in counts else None
        finally:
            if not keep_copy:
                restored.unlink(missing_ok=True)
        mismatches = _count_mismatches(record.table_counts, counts)
        return RestoreDrillResult(
            backup_file=record.file,
            restored_to=str(restored),
            copy_seconds=copy_seconds,
            quick_check=check,
            tables_checked=len(counts),
            mismatches=mismatches,
            latest_tw_bar=str(latest_bar) if latest_bar is not None else None,
            kept_copy=keep_copy,
        )


def _count_mismatches(
    expected: dict[str, int], actual: dict[str, int]
) -> dict[str, tuple[int | None, int | None]]:
    names: Iterable[str] = sorted(set(expected) | set(actual))
    return {
        name: (expected.get(name), actual.get(name))
        for name in names
        if expected.get(name) != actual.get(name)
    }
