"""Long research programs the website lists (使用者 2026-10-04).

The owner does not want the assistant to sit waiting on long programs (every check spends tokens);
instead every long research program (data downloads, rule batches, factor analysis) registers itself
here and the website's 研究 › 執行中的程式 tab shows what runs, how far it is and when it should end.
One JSON file per run under ``instance/research/jobs/``: written atomically, updated at most every
few seconds, finished with a status and a one-line summary. A run whose process is gone without
finishing shows as 中斷.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
STATUS_LABELS = {"running": "執行中", "done": "完成", "failed": "失敗", "stopped": "中斷"}
WRITE_EVERY = 3.0   # seconds between progress writes
RETRIES, RETRIES_FORCED, RETRY_PAUSE = 3, 20, 0.25


def _alive(pid: int, started_at: datetime | None = None) -> bool:
    """Whether the job's process still runs. With ``started_at``, a process created more than two minutes
    after the job started is another program that got the same number (2026-10-07: the news job a deploy
    stopped on 10-06 still read 執行中 because its PID had been given to a new process)."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        created = None
        if started_at is not None:
            class FILETIME(ctypes.Structure):
                _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

            times = [FILETIME() for _ in range(4)]
            if kernel.GetProcessTimes(handle, *[ctypes.byref(item) for item in times]):
                ticks = (times[0].high << 32) | times[0].low          # 100 ns since 1601-01-01 UTC
                created = datetime(1601, 1, 1, tzinfo=ZoneInfo("UTC")) + timedelta(microseconds=ticks // 10)
        kernel.CloseHandle(handle)
        if not (bool(ok) and code.value == 259):            # STILL_ACTIVE
            return False
        return created is None or started_at is None or created <= started_at + timedelta(minutes=2)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _now() -> datetime:
    return datetime.now(TAIPEI)


class Job:
    def __init__(self, path: Path, payload: dict[str, object]) -> None:
        self._path = path
        self.payload = payload
        self._written = 0.0
        self._write(force=True)

    def _write(self, force: bool = False) -> None:
        moment = time.monotonic()
        if not force and moment - self._written < WRITE_EVERY:
            return
        self.payload["updated_at"] = _now().isoformat(timespec="seconds")
        partial = self._path.with_suffix(".partial")
        text = json.dumps(self.payload, ensure_ascii=False, indent=1)
        # Windows refuses to replace a file another process (the website listing the jobs) has open at
        # that instant: on 2026-10-05 that stopped a download. Retry briefly; a progress note never stops
        # the program (the next update writes it again).
        for _attempt in range(RETRIES_FORCED if force else RETRIES):
            try:
                partial.write_text(text, encoding="utf-8")
                partial.replace(self._path)
            except OSError:
                time.sleep(RETRY_PAUSE)
                continue
            self._written = moment
            return

    def update(self, done: int | None = None, current: str | None = None, total: int | None = None,
               force: bool = False) -> None:
        if total is not None:
            self.payload["total"] = total
        if done is not None:
            self.payload["done"] = done
        if current is not None:
            self.payload["current"] = current
        self._write(force=force)

    def finish(self, status: str = "done", summary: str = "") -> None:
        self.payload.update({"status": status, "summary": summary, "finished_at": _now().isoformat(timespec="seconds")})
        if status == "done" and self.payload.get("total"):     # loops report a step as it starts, not as it ends
            self.payload["done"] = self.payload["total"]
        self._write(force=True)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, kind, error, _trace) -> bool:
        if self.payload.get("status") == "running":
            if error is None:
                self.finish("done", str(self.payload.get("summary") or ""))
            else:
                self.finish("stopped" if kind is KeyboardInterrupt else "failed", f"{kind.__name__}: {error}"[:300])
        return False


class JobLog:
    def __init__(self, research_dir: str | Path) -> None:
        self.folder = Path(research_dir) / "jobs"

    def start(self, name: str, command: str, total: int | None = None, current: str = "") -> Job:
        self.folder.mkdir(parents=True, exist_ok=True)
        started = _now()
        job_id = f"{started:%Y%m%d-%H%M%S}-{os.getpid()}"
        suffix = 1
        while (self.folder / f"{job_id}.json").exists():     # two jobs of one process within a second
            suffix += 1
            job_id = f"{started:%Y%m%d-%H%M%S}-{os.getpid()}-{suffix}"
        return Job(self.folder / f"{job_id}.json", {
            "id": job_id, "name": name, "command": command, "pid": os.getpid(), "status": "running",
            "started_at": started.isoformat(timespec="seconds"), "done": 0, "total": total, "current": current,
            "summary": "", "finished_at": None,
        })

    def jobs(self, limit: int = 30) -> list[dict[str, object]]:
        """Newest first, with the status (a running job whose process is gone is 中斷), progress and
        an estimated end time."""
        if not self.folder.is_dir():
            return []
        rows = []
        for path in sorted(self.folder.glob("*.json"), reverse=True)[:limit]:
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            started = datetime.fromisoformat(item["started_at"]) if item.get("started_at") else None
            if item.get("status") == "running" and not _alive(int(item.get("pid") or 0), started):
                item["status"] = "stopped"
                item["summary"] = item.get("summary") or "程式已不在執行（可能被停止或電腦重開）"
            item["status_label"] = STATUS_LABELS.get(item["status"], item["status"])
            done, total = item.get("done") or 0, item.get("total")
            item["percent"] = round(100 * done / total) if total else None
            item["eta"] = None
            if item["status"] == "running" and total and done:
                started = datetime.fromisoformat(item["started_at"])
                elapsed = (_now() - started).total_seconds()
                remaining = elapsed / done * (total - done)
                item["eta"] = datetime.fromtimestamp(_now().timestamp() + remaining, TAIPEI).strftime("%m/%d %H:%M")
            rows.append(item)
        rows.sort(key=lambda row: (row["status"] != "running", row.get("started_at") or ""), reverse=False)
        running = [row for row in rows if row["status"] == "running"]
        others = sorted((row for row in rows if row["status"] != "running"), key=lambda row: row.get("started_at") or "",
                        reverse=True)
        return running + others

    def running(self) -> int:
        return sum(1 for row in self.jobs() if row["status"] == "running")
