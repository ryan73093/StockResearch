"""Every scheduled job that ended on an error, written down (2026-11-12; problem register P6).

The worker's nightly model refit raised nine nights in a row. APScheduler wrote the traceback into the worker's log,
the failure notification had no channel to go out on, and the status, the 16:00 check and the Today page read other
places — nobody saw it. The worker now appends one line a failure to ``instance/worker-failures.jsonl`` (the job, when,
the error's last line); the status command, the 16:00 check and the Today page read the last 24 hours of it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
FILE = "worker-failures.jsonl"


def record(instance: str | Path, job_id: str, name: str, error: object, now: datetime | None = None) -> dict[str, str]:
    """Append the failure; never raises (a failure to write it down must not hide the job's own error)."""
    when = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    text = f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else str(error)
    row = {"at": when.isoformat(timespec="seconds"), "job": str(job_id), "name": str(name), "error": text[:400]}
    try:
        path = Path(instance) / FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + chr(10))
    except OSError:
        pass
    return row


def recent(instance: str | Path, hours: int = 24, now: datetime | None = None) -> list[dict[str, str]]:
    """The failures of the last ``hours``, newest first; a job that failed several times is one row with ``times``."""
    path = Path(instance) / FILE
    if not path.is_file():
        return []
    since = (now or datetime.now(TAIPEI)).astimezone(TAIPEI) - timedelta(hours=hours)
    found: dict[str, dict[str, str]] = {}
    try:
        with path.open("rb") as handle:                 # the end of the file is the last days
            handle.seek(max(0, path.stat().st_size - (1 << 20)))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in reversed(lines):
        try:
            row = json.loads(line)
            when = datetime.fromisoformat(row["at"])
        except (ValueError, KeyError, TypeError):
            continue
        if when < since:
            break
        kept = found.setdefault(row["job"], {**row, "times": 0})
        kept["times"] = int(kept["times"]) + 1
    return list(found.values())
