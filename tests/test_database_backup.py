import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from quant_platform.application.database_backup import (
    MANIFEST_NAME,
    DatabaseBackupService,
)
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app


class FakeRuns:
    def __init__(self):
        self.started = []
        self.finished = []

    def start(self, job_name, market, started_at):
        self.started.append((job_name, market, started_at))
        return len(self.started)

    def finish(self, run_id, status, finished_at, summary, error):
        self.finished.append((run_id, status, json.loads(summary), error))


@pytest.fixture
def live_database(tmp_path):
    path = tmp_path / "live.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE market_bars (market TEXT, symbol TEXT, event_time TEXT)")
        db.execute("CREATE TABLE paper_orders (id INTEGER PRIMARY KEY)")
        db.executemany(
            "INSERT INTO market_bars VALUES ('TW', ?, ?)",
            [(f"{code}.TW", f"2026-09-{day:02d}") for code in range(2000, 2010) for day in (28, 29, 30)],
        )
        db.execute("INSERT INTO paper_orders DEFAULT VALUES")
        db.commit()
    return path


def at(day: int, hour: int = 19) -> datetime:
    # 19:00 UTC = 03:00 Taipei on the next day.
    return datetime(2026, 9, day, hour, 0, tzinfo=UTC)


def test_backup_is_a_verified_standalone_copy_with_counts(live_database, tmp_path):
    runs = FakeRuns()
    service = DatabaseBackupService(live_database, tmp_path / "daily", keep=7, runs=runs)

    record = service.run(now=at(30))

    backup = tmp_path / "daily" / record.file
    assert record.file == "quant_platform-20261001-030000.db"
    assert record.quick_check == "ok"
    assert record.table_counts == {"market_bars": 30, "paper_orders": 1}
    assert backup.is_file() and not backup.with_name(backup.name + "-wal").exists()
    with closing(sqlite3.connect(backup)) as copy:
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    manifest = (tmp_path / "daily" / MANIFEST_NAME).read_text(encoding="utf-8").splitlines()
    assert json.loads(manifest[0])["file"] == record.file
    assert runs.started[0][:2] == ("database_backup", "SYSTEM")
    assert runs.finished[0][1] == "succeeded" and runs.finished[0][2]["tables"] == 2


def test_backup_sees_committed_rows_while_another_connection_holds_the_database(
    live_database, tmp_path
):
    service = DatabaseBackupService(live_database, tmp_path / "daily")
    with closing(sqlite3.connect(live_database)) as writer:
        writer.execute("INSERT INTO paper_orders DEFAULT VALUES")
        writer.commit()
        writer.execute("BEGIN")
        writer.execute("INSERT INTO paper_orders DEFAULT VALUES")  # not committed

        record = service.run(now=at(30))

        writer.rollback()
    assert record.table_counts["paper_orders"] == 2


def test_rotation_keeps_the_newest_copies(live_database, tmp_path):
    runs = FakeRuns()
    service = DatabaseBackupService(live_database, tmp_path / "daily", keep=2, runs=runs)

    for day in (26, 27, 28, 29):
        service.run(now=at(day))

    files = [path.name for path in service.backup_files()]
    assert files == ["quant_platform-20260930-030000.db", "quant_platform-20260929-030000.db"]
    assert runs.finished[-1][2]["removed"] == ["quant_platform-20260928-030000.db"]
    assert [record.file for record in service.records()] == files


def test_status_reports_missing_and_overdue_backups(live_database, tmp_path):
    service = DatabaseBackupService(live_database, tmp_path / "daily")
    empty = service.status(now=at(30))
    assert empty.latest is None and empty.overdue and empty.count == 0

    service.run(now=at(30))

    fresh = service.status(now=at(30) + timedelta(hours=20))
    stale = service.status(now=at(30) + timedelta(hours=27))
    assert not fresh.overdue and fresh.count == 1 and fresh.total_bytes > 0
    assert stale.overdue


def test_failed_backup_is_recorded_and_leaves_no_partial_file(tmp_path):
    runs = FakeRuns()
    service = DatabaseBackupService(tmp_path / "missing.db", tmp_path / "daily", runs=runs)

    with pytest.raises(FileNotFoundError):
        service.run(now=at(30))

    assert runs.finished[0][1] == "failed"
    assert not (tmp_path / "daily").exists()


def test_restore_drill_restores_elsewhere_and_matches_the_manifest(live_database, tmp_path):
    service = DatabaseBackupService(live_database, tmp_path / "daily")
    service.run(now=at(29))
    latest = service.run(now=at(30))

    result = service.restore_drill(tmp_path / "drill")

    assert result.passed
    assert result.backup_file == latest.file
    assert result.tables_checked == 2
    assert result.latest_tw_bar == "2026-09-30"
    assert not list((tmp_path / "drill").glob("restore-drill-*"))  # copy removed by default


def test_restore_drill_reports_count_mismatches(live_database, tmp_path):
    service = DatabaseBackupService(live_database, tmp_path / "daily")
    record = service.run(now=at(30))
    manifest = tmp_path / "daily" / MANIFEST_NAME
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["table_counts"]["market_bars"] = 31
    manifest.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    result = service.restore_drill(tmp_path / "drill", backup_file=record.file)

    assert not result.passed
    assert result.mismatches == {"market_bars": (31, 30)}


def test_system_page_shows_backup_status(tmp_path):
    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False)
    )
    client = create_app(container).test_client()

    assert "尚無備份" in client.get("/system").get_data(as_text=True)

    container.database_backup.run()
    body = client.get("/system").get_data(as_text=True)

    assert "資料庫備份" in body and "共 1 份（上限 7）" in body
    assert "03:00" in body  # nightly schedule listed
