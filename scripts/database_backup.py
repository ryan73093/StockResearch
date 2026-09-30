"""Manual database backup, backup status and restore drill (S2-W05).

The worker already backs up every day at 03:00 (instance/backups/daily, keep 7).

    .\\.venv\\Scripts\\python.exe scripts\\database_backup.py status
    .\\.venv\\Scripts\\python.exe scripts\\database_backup.py run
    .\\.venv\\Scripts\\python.exe scripts\\database_backup.py drill [--backup FILE] [--keep-copy]

`drill` copies a backup to instance/backups/drill, opens the copy read-only,
runs quick_check and compares every table's row count with the manifest; the
live database is never touched. Reports go to instance/backups/drill/*.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from quant_platform.application.database_backup import TAIPEI, DatabaseBackupService
from quant_platform.config.settings import Settings


def _service() -> DatabaseBackupService:
    settings = Settings.from_env()
    prefix = "sqlite:///"
    if not settings.database_url.startswith(prefix):
        raise SystemExit("DATABASE_URL 不是 SQLite 檔案，無法備份")
    database = Path(settings.database_url[len(prefix):].split("?", 1)[0])
    return DatabaseBackupService(database, database.parent / "backups" / "daily", keep=7)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="最近備份與保留份數")
    commands.add_parser("run", help="立即建立一份備份（與 03:00 排程相同流程）")
    drill = commands.add_parser("drill", help="還原演練：還原到暫存路徑並核對")
    drill.add_argument("--backup", help="備份檔名（預設最新一份）")
    drill.add_argument("--keep-copy", action="store_true", help="保留還原出的檔案")
    args = parser.parse_args()
    service = _service()

    if args.command == "status":
        status = service.status()
        print(json.dumps(asdict(status), ensure_ascii=False, indent=2, default=str))
        return 0
    if args.command == "run":
        record = service.run()
        print(json.dumps(
            {key: value for key, value in asdict(record).items() if key != "table_counts"}
            | {"tables": len(record.table_counts)},
            ensure_ascii=False, indent=2,
        ))
        return 0

    work_dir = service.backup_dir.parent / "drill"
    result = service.restore_drill(work_dir, backup_file=args.backup, keep_copy=args.keep_copy)
    report = asdict(result) | {
        "passed": result.passed,
        "checked_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
    }
    stamp = datetime.now(TAIPEI).strftime("%Y%m%d-%H%M%S")
    report_path = work_dir / f"drill-{stamp}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"報告：{report_path}")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
