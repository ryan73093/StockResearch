import json

import pytest

from quant_platform.research.jobs import JobLog


def test_a_job_reports_progress_and_its_end(tmp_path):
    log = JobLog(tmp_path)
    with log.start("個股規則快篩", "stocks --screen", total=4) as job:
        job.update(done=2, current="第 3 個", force=True)
        running = log.jobs()[0]
        assert running["status"] == "running" and running["percent"] == 50 and running["current"] == "第 3 個"
        assert running["eta"] is not None
        job.update(done=4, force=True)
        job.payload["summary"] = "4 個規則"
    finished = log.jobs()[0]
    assert finished["status"] == "done" and finished["status_label"] == "完成" and finished["summary"] == "4 個規則"
    assert log.running() == 0


def test_a_failed_job_and_a_vanished_process(tmp_path):
    log = JobLog(tmp_path)
    with pytest.raises(ValueError), log.start("壞掉的程式", "x"):
        raise ValueError("資料不見了")
    failed = log.jobs()[0]
    assert failed["status"] == "failed" and "資料不見了" in failed["summary"]
    # a job file left "running" by a process that no longer exists shows as 中斷
    ghost = {"id": "20260101-000000-1", "name": "舊程式", "command": "x", "pid": 999_999_999, "status": "running",
             "started_at": "2026-01-01T00:00:00+08:00", "done": 1, "total": 10}
    (tmp_path / "jobs" / "20260101-000000-1.json").write_text(json.dumps(ghost), encoding="utf-8")
    rows = {row["name"]: row for row in log.jobs()}
    assert rows["舊程式"]["status"] == "stopped" and rows["舊程式"]["status_label"] == "中斷"


def test_a_locked_progress_file_never_stops_the_program(tmp_path, monkeypatch):
    """2026-10-05: Windows refused to replace the progress file while the website read it; the download died."""
    import pathlib

    from quant_platform.research import jobs

    monkeypatch.setattr(jobs, "RETRY_PAUSE", 0)
    original, refusals = pathlib.Path.replace, {"left": 0}

    def replace(self, target):
        if refusals["left"] > 0:
            refusals["left"] -= 1
            raise PermissionError(5, "存取被拒")
        return original(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", replace)
    with JobLog(tmp_path).start("下載", "cmd", total=10) as job:
        refusals["left"] = 2                                 # refused twice, the third try works
        job.update(done=3, force=True)
        assert json.loads(next((tmp_path / "jobs").glob("*.json")).read_text(encoding="utf-8"))["done"] == 3
        refusals["left"] = 100                               # refused every time: skipped, no error
        job.update(done=5, force=True)
        refusals["left"] = 0
    assert JobLog(tmp_path).jobs()[0]["status"] == "done"


def test_a_pid_given_to_a_later_program_is_not_the_job(tmp_path):
    """2026-10-07: a job stopped by a deploy kept 執行中 because Windows gave its PID to a new process."""
    import os

    log = JobLog(tmp_path)
    job = log.start("新聞", "scheduler collect_news", total=10)
    job.payload["pid"] = os.getpid()                    # alive, but created long after the job "started"
    job.payload["started_at"] = "2020-01-01T09:00:00+08:00"
    job._write(force=True)
    fresh = log.start("現在的工作", "scheduler update_chips", total=1)
    rows = {row["command"]: row["status"] for row in log.jobs()}
    assert rows["scheduler collect_news"] == "stopped" and rows["scheduler update_chips"] == "running"
    fresh.finish()
