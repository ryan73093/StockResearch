"""2026-11-12 (problem register P5; the memory answer of 11-11): the worker ran its heavy jobs — the forward record, the
after-close orders, the model refit, the stock snapshot, the research data refresh — in its own process and never gave
their memory back: 11.8 GB private two days after a restart, with nothing running. They run in a child process; its
memory goes back to the system when the job ends."""
import subprocess
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from quant_platform.config.settings import Settings
from quant_platform.container import build_container
from quant_platform.scheduler import child, runner

ZONE = ZoneInfo("Asia/Taipei")


def test_a_heavy_job_is_started_in_a_child_process_and_its_failure_is_the_jobs_failure(monkeypatch, tmp_path):
    calls, codes = [], [0, 1]

    def run(command, **options):
        calls.append((command, options))
        return subprocess.CompletedProcess(command, codes[len(calls) - 1])
    monkeypatch.setattr(child.subprocess, "run", run)
    monkeypatch.setattr(child, "_log_path", lambda container: tmp_path / "worker-jobs.log")
    job = child.in_child(runner.retrain_models)
    assert job.__name__ == "retrain_models" and job.__wrapped__ is runner.retrain_models and job.in_child
    assert job(object()) is None
    command, options = calls[0]
    assert command == [sys.executable, "-m", "quant_platform.scheduler.child", "retrain_models"]
    assert options["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)       # no window (AGENTS.md)
    with pytest.raises(child.ChildJobFailed, match="retrain_models 在子程序結束碼 1"):
        job(object())
    with pytest.raises(ValueError):                 # a caller's own time cannot be passed to the child
        job(object(), object())
    text = (tmp_path / "worker-jobs.log").read_text(encoding="utf-8")
    assert text.count("retrain_models 開始") == 2 and "結束（結束碼 1）" in text
    with pytest.raises(ValueError):                 # only the jobs listed as heavy
        child.in_child(runner.start_rolling)


def test_a_job_asked_every_minute_starts_a_process_only_when_it_has_work(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(child.subprocess, "run", lambda command, **options: calls.append(command)
                        or subprocess.CompletedProcess(command, 0))
    monkeypatch.setattr(child, "_log_path", lambda container: tmp_path / "worker-jobs.log")
    due = []
    job = child.in_child(runner.prepare_after_close_orders, when=lambda container: bool(due))
    job(object())
    assert calls == [] and not (tmp_path / "worker-jobs.log").exists()
    due.append(1)
    job(object())
    assert len(calls) == 1


def test_the_child_runs_the_job_with_its_own_container(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(child, "build_container", lambda: "container")
    monkeypatch.setattr(runner, "retrain_models", lambda container: seen.append(container) or ["dl-2.6.0"])
    assert child.main(["retrain_models"]) == 0 and seen == ["container"]

    def fails(container):
        raise KeyError("2026-10-29")
    monkeypatch.setattr(runner, "retrain_models", fails)
    assert child.main(["retrain_models"]) == 1
    assert "KeyError" in capsys.readouterr().err
    assert child.main(["start_rolling"]) == 2 and child.main([]) == 2          # only the jobs listed as heavy


def test_the_heavy_jobs_are_registered_through_the_child(tmp_path):
    from apscheduler.schedulers.background import BackgroundScheduler

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'jobs.db'}"))
    scheduler = BackgroundScheduler(timezone="Asia/Taipei")
    runner._add_maintenance_jobs(scheduler, container)
    wrapped = {job.id: job.func.__name__ for job in scheduler.get_jobs() if getattr(job.func, "in_child", False)}
    assert wrapped == {"research_history_refresh": "refresh_research_history", "after_close_orders": "prepare_after_close_orders",
                       "forward_simulation": "record_forward_simulation", "model_retrain": "retrain_models",
                       "stock_snapshot": "build_stock_snapshot"}
    assert sorted(wrapped.values()) == sorted(child.HEAVY)


def test_the_after_close_orders_are_due_only_in_their_window_with_todays_table_and_no_run_yet(tmp_path):
    import json

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'due.db'}"))
    day = datetime(2026, 11, 12, 13, 55, tzinfo=ZONE)                      # a Thursday
    raw = tmp_path / "research" / "history" / "raw" / "twse_stock_all" / "2026" / "20261112.json"
    assert runner.after_close_due(container, day) is False                 # the table is not in yet
    raw.parent.mkdir(parents=True)
    raw.write_text(json.dumps({"payload": {}}), encoding="utf-8")
    assert runner.after_close_due(container, day) is True
    assert runner.after_close_due(container, day.replace(hour=13, minute=49)) is False
    assert runner.after_close_due(container, day.replace(hour=14, minute=26)) is False
    assert runner.after_close_due(container, datetime(2026, 11, 14, 13, 55, tzinfo=ZONE)) is False     # a Saturday
