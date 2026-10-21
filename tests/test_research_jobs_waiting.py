"""2026-10-21 (使用者 2026-10-16：隨時看得到研究有沒有在跑): a queued pipeline shows as 等待中 with its reason."""
import os

from quant_platform.research.jobs import JobLog


def test_a_waiting_pipeline_is_listed_with_the_running_ones_until_it_ends(tmp_path):
    log = JobLog(tmp_path)
    job_id = log.waiting("bigscan2", "等大規模搜尋第一輪跑完", os.getpid())
    found = log.jobs()[0]
    assert found["status"] == "waiting" and found["status_label"] == "等待中" and found["current"] == "等大規模搜尋第一輪跑完"
    log.end_wait(job_id)
    assert log.jobs()[0]["status"] == "done" and "等完了" in log.jobs()[0]["summary"]
    log.waiting("x", "等顯示卡", 999_999_999)                                    # its process gone: 中斷
    assert [job["status"] for job in log.jobs() if "x" in job["name"]] == ["stopped"]
