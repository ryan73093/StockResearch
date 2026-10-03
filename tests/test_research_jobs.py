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
