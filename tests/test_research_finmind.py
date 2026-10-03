from datetime import datetime

from quant_platform.research.history.finmind import TAIPEI, QuotaReached, fetch_all, read_rows
from quant_platform.research.jobs import JobLog


def test_download_resumes_respects_the_quiet_window_and_waits_on_the_quota(tmp_path):
    calls, sleeps = [], []
    moments = iter([datetime(2026, 10, 5, 14, 0, tzinfo=TAIPEI)] + [datetime(2026, 10, 5, 16, 0, tzinfo=TAIPEI)] * 50)
    state = {"quota": True}

    def get(dataset, code, token, end):
        calls.append((dataset, code))
        if code == "2317" and state["quota"]:
            state["quota"] = False
            raise QuotaReached("upper limit")
        return {"status": 200, "data": [{"date": "2026-10-02", "stock_id": code, "value": 1}]}

    job = JobLog(tmp_path).start("FinMind", "x")
    result = fetch_all(tmp_path, "token", ["2330", "2317"], ["TaiwanStockPER"], job=job, get=get,
                       sleep=sleeps.append, now=lambda: next(moments), per_hour=3600)
    assert result == {"requested": 2, "skipped": 0, "errors": [], "total": 2}
    assert calls == [("TaiwanStockPER", "2330"), ("TaiwanStockPER", "2317"), ("TaiwanStockPER", "2317")]
    assert 300 in sleeps and 3600 in sleeps              # the 14:00 quiet window, then the hourly limit
    assert read_rows(tmp_path, "TaiwanStockPER", "2317")[0]["stock_id"] == "2317"
    calls.clear()
    again = fetch_all(tmp_path, "token", ["2330", "2317"], ["TaiwanStockPER"], get=get, sleep=sleeps.append,
                      now=lambda: datetime(2026, 10, 5, 16, 0, tzinfo=TAIPEI))
    assert calls == [] and again["skipped"] == 2        # stored files are not requested again
