"""2026-11-11 (problem register「D 的設計」3：狀態由程式印出): ``python -m quant_platform.research status`` (research/status_report.py)."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from quant_platform.research import jobs
from quant_platform.research import status_report as status

ZONE = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 11, 30, 17, 0, tzinfo=ZONE)


def _research(tmp_path, forward_last="2026-11-30", close_last=date(2026, 11, 27)):
    research = tmp_path / "research"
    (research / "history" / "daily").mkdir(parents=True)
    pd.DataFrame({"date": [date(2026, 11, 26), close_last], "close": 1.0}).to_parquet(
        research / "history" / "daily" / "TAIEX.parquet", index=False)
    (research / "forward" / "stocks").mkdir(parents=True)
    (research / "forward" / "stocks" / "log.jsonl").write_text(
        "".join(json.dumps({"rule_hash": "a", "date": day, "holdings": [{"code": "2330"}] * 20}) + "\n"
                for day in ("2026-11-27", forward_last)), encoding="utf-8")
    (tmp_path / "market_calendar").mkdir()                           # the typhoon day, as the operator records it
    (tmp_path / "market_calendar" / "manual_closures.json").write_text(json.dumps(
        {"closures": [{"date": "2026-10-23", "name": "颱風休市"}]}), encoding="utf-8")
    (research / "rolling").mkdir()
    for cutoff, passed, usable in (("2026-08-31", {"a", "b"}, 0), ("2026-09-30", {"a", "c"}, 1)):
        (research / "rolling" / f"{cutoff}.json").write_text(json.dumps(
            {"cutoff": cutoff, "data_through": "2026-11-27",
             "graded": [{"rule_hash": rule, "passed": rule in passed} for rule in ("a", "b", "c", "d")],
             "holdout": [{"rule_hash": "a", "usable": True, "start": "2026-10-01"}][:usable]}), encoding="utf-8")
    (research / "rolling" / "2026-08-31.before-gate2020-20261027.json").write_text(json.dumps(   # a copy, not a list
        {"cutoff": "2026-08-31", "graded": [{"rule_hash": "z", "passed": True}]}), encoding="utf-8")
    return research


def test_the_dates_are_set_against_the_exchanges_session(tmp_path):
    research = _research(tmp_path)
    state = status.collect(research, NOW)
    assert state["due"] == "2026-11-30"                       # 17:00, the 15:15 and 15:30 jobs have come
    assert state["close"] == "2026-11-27" and state["forward"] == "2026-11-30"
    text = status.render(state)
    assert "研究資料（收盤）：2026-11-27（應有 2026-11-30）✗" in text
    assert "前向紀錄：2026-11-30（應有 2026-11-30）✓" in text


def test_the_forward_log_is_read_from_its_end(tmp_path):
    research = _research(tmp_path)
    assert status.last_forward(research, tail=300) == date(2026, 11, 30)   # the first piece read is cut and skipped
    assert status.last_forward(tmp_path / "nothing") is None


def test_the_month_list_and_the_chain(tmp_path):
    """9/1 to 11/30 is the 60th session (roadmap date table: 10/23 typhoon, 10/26 closed); rule "a" is on both lists."""
    research = _research(tmp_path)
    state = status.collect(research, NOW)
    assert state["list"]["wanted"] == "2026-09-30"
    assert state["list"]["latest"] == {"cutoff": "2026-09-30", "graded": 4, "passed": 2, "usable": 1,
                                       "data_through": "2026-11-27", "released": True}
    assert state["chain"]["start"] == "2026-09-01" and state["chain"]["sessions"] == 60
    assert state["chain"]["rules"] == 1 and state["chain"]["lists"] == 2 and state["chain"]["needed"] == 60
    text = status.render(state)
    assert "最新名單截止 2026-09-30 ✓：評 4 個、過門檻 2 個、當月可用 1 個" in text
    assert "從 2026-09-01（第一份名單截止日的隔天）到 2026-11-30：60 個交易日／要 60 個" in text
    december = status.collect(research, datetime(2026, 12, 1, 9, 0, tzinfo=ZONE))
    assert "應為截止 2026-10-31；最新名單截止 2026-09-30 ✗" in status.render(december)


def test_the_flows_running_waiting_and_broken(tmp_path, monkeypatch):
    research = _research(tmp_path)
    monkeypatch.setattr(jobs, "_alive", lambda pid, started=None: pid == os.getpid())
    (research / "jobs").mkdir()
    rows = [("1-wait", "研究流程 branches：等待中", "waiting", os.getpid(), "等分點下載完"),
            ("2", "全段大搜尋", "running", os.getpid(), "b 段"),
            ("3", "已經不在的程式", "running", 999999, "")]
    for name, label, state, pid, current in rows:
        (research / "jobs" / f"{name}.json").write_text(json.dumps(
            {"id": name, "name": label, "command": "x", "pid": pid, "status": state, "started_at": "2026-11-30T08:00:00+08:00",
             "done": 3, "total": 10, "current": current}), encoding="utf-8")
    (research / "pipelines").mkdir()
    (research / "pipelines" / "rebuild.json").write_text(json.dumps(
        {"name": "rebuild", "pid": 1, "status": "interrupted", "started_at": "2026-11-29T22:00:00"}), encoding="utf-8")
    (research / "pipelines" / "done.json").write_text(json.dumps(
        {"name": "done", "pid": 1, "status": "done", "started_at": "2026-11-29T22:00:00"}), encoding="utf-8")
    flows = status.collect(research, NOW)["flows"]
    assert [row["name"] for row in flows["running"]] == ["全段大搜尋"]
    assert [row["reason"] for row in flows["waiting"]] == ["等分點下載完"]
    assert flows["broken"] == [{"name": "rebuild", "status": "interrupted", "since": "2026-11-29T22:00:00"}]
    text = status.render(status.collect(research, NOW))
    assert "流程：在跑 1 個、等待中 1 個、中斷或失敗沒恢復的 1 個" in text
    assert "等待：研究流程 branches：等待中（2026-11-30 08:00 起）：等分點下載完" in text


def test_the_red_checks_and_a_missing_run_today(tmp_path):
    research = _research(tmp_path)
    checks = [{"name": "前向帳戶對帳 0 問題", "ok": True, "detail": "0"},
              {"name": "文件守門", "ok": False, "detail": "AGENTS.md 3,100 字，上限 3,000"}]
    (research / "invariants.json").write_text(json.dumps({"at": "2026-11-30T16:00:00+08:00", "checks": checks}),
                                              encoding="utf-8")
    text = status.render(status.collect(research, NOW))
    assert "16:00 需求檢查（2026-11-30 16:00，2 項）：紅燈 1 項\n  ✗ 文件守門：AGENTS.md 3,100 字，上限 3,000" in text
    (research / "invariants.json").write_text(json.dumps({"at": "2026-11-27T16:00:00+08:00", "checks": checks}),
                                              encoding="utf-8")
    assert "✗ 今天 16:00 的還沒有" in status.render(status.collect(research, NOW))
    assert "✗ 今天 16:00 的還沒有" not in status.render(status.collect(research, NOW.replace(hour=15)))


def test_the_report_prints_where_the_console_is_not_utf8():
    """2026-11-11: run from a tool (stdout a pipe, code page 950), the command died on the first ✓ with a
    UnicodeEncodeError — the takeover step of AGENTS.md could not be run at all. The report goes out as UTF-8."""
    import io

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp950")
    status.emit("研究資料到 2026-11-11 ✓\n紅燈 ✗", stream)
    stream.flush()
    assert raw.getvalue().decode("utf-8").splitlines() == ["研究資料到 2026-11-11 ✓", "紅燈 ✗"]


def test_a_job_that_failed_in_the_last_day_is_listed_until_the_same_job_succeeds(tmp_path, monkeypatch):
    """2026-11-11: the nightly model refit failed nine nights (KeyError) and the report said 中斷或失敗沒恢復的 0 個 —
    it looked at the flows only, not at the worker's jobs."""
    research = _research(tmp_path)
    monkeypatch.setattr(jobs, "_alive", lambda pid, started=None: False)
    (research / "jobs").mkdir()
    rows = [("a", "重訓機器學習模型（dl-2.6.0，2026 年 11 月）", "failed", "2026-11-29T22:45:00+08:00", "KeyError: '2026-10-29'"),
            ("b", "新聞收集", "failed", "2026-11-30T14:40:00+08:00", "TimeoutError"),
            ("c", "新聞收集", "done", "2026-11-30T15:40:00+08:00", "12 則"),               # the same job, later, worked
            ("d", "很久以前失敗的", "failed", "2026-11-20T10:00:00+08:00", "x")]
    for name, label, state, started, summary in rows:
        (research / "jobs" / f"{name}.json").write_text(json.dumps(
            {"id": name, "name": label, "command": "x", "pid": 1, "status": state, "started_at": started,
             "finished_at": started, "summary": summary}), encoding="utf-8")
    state = status.collect(research, NOW)
    assert state["flows"]["failed"] == [{"name": "重訓機器學習模型（dl-2.6.0，2026 年 11 月）",
                                         "since": "2026-11-29T22:45:00+08:00", "summary": "KeyError: '2026-10-29'"}]
    text = status.render(state)
    assert "最近 24 小時失敗、之後沒有成功過的工作 1 個" in text
    assert "失敗：重訓機器學習模型（dl-2.6.0，2026 年 11 月）（2026-11-29 22:45）：KeyError: '2026-10-29'" in text


def test_a_red_check_shows_how_long_it_has_been_red(tmp_path):
    research = _research(tmp_path)
    (research / "invariants.json").write_text(json.dumps({"at": "2026-11-30T16:00:00+08:00", "checks": [
        {"name": "前向帳戶照規則交易", "ok": False, "detail": "69 個", "red_days": 4, "red_since": "2026-11-25"},
        {"name": "備份", "ok": True, "detail": "有"}]}), encoding="utf-8")
    text = status.render(status.collect(research, NOW))
    assert "✗ 前向帳戶照規則交易（連續 4 天，2026-11-25 起）：69 個" in text


def test_a_list_held_for_its_models_is_said_to_be_not_released(tmp_path):
    research = _research(tmp_path)
    path = research / "rolling" / "2026-09-30.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**payload, "released": False}), encoding="utf-8")
    assert "名單已評、還沒發布：等模型重訓到截止日" in status.render(status.collect(research, NOW))
