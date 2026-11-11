"""2026-11-12 (problem register P6): a scheduled job that ends on an error is written down, and the status command, the
16:00 check and the Today page say so — the nightly model refit failed nine nights and nothing read by anyone did."""
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from quant_platform.research import invariants
from quant_platform.research import status_report as status
from quant_platform.scheduler import failures, runner

ZONE = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 11, 12, 16, 0, tzinfo=ZONE)


def test_a_failure_is_one_line_and_the_last_day_is_read_newest_first(tmp_path):
    failures.record(tmp_path, "old", "很久以前", KeyError("x"), now=datetime(2026, 11, 10, 22, 45, tzinfo=ZONE))
    failures.record(tmp_path, "model_retrain", "模型重訓（交易日 22:45）", KeyError("2026-10-29"),
                    now=datetime(2026, 11, 11, 22, 51, tzinfo=ZONE))
    failures.record(tmp_path, "after_close_orders", "盤後單", RuntimeError("子程序結束碼 1"),
                    now=datetime(2026, 11, 12, 13, 51, tzinfo=ZONE))
    failures.record(tmp_path, "after_close_orders", "盤後單", RuntimeError("子程序結束碼 1"),
                    now=datetime(2026, 11, 12, 13, 52, tzinfo=ZONE))
    found = failures.recent(tmp_path, now=NOW)
    assert [(row["job"], row["times"]) for row in found] == [("after_close_orders", 2), ("model_retrain", 1)]
    assert found[1]["error"] == "KeyError: '2026-10-29'" and found[0]["at"] == "2026-11-12T13:52:00+08:00"
    assert failures.recent(tmp_path / "nothing", now=NOW) == []
    failures.record(tmp_path / "file-in-the-way" / "x", "a", "b", "c")          # never raises


def test_the_workers_listener_writes_the_failure_down_and_sends_it(tmp_path):
    sent = []
    container = SimpleNamespace(
        settings=SimpleNamespace(database_url=f"sqlite:///{tmp_path / 'instance' / 'quant_platform.db'}", public_url=""),
        notification_service=SimpleNamespace(send=lambda key, title, text: sent.append((key, title)) or "disabled"))
    job = SimpleNamespace(id="model_retrain", name="模型重訓（交易日 22:45）")
    scheduler = SimpleNamespace(get_job=lambda job_id: job if job_id == "model_retrain" else None)
    listener = runner.failure_listener(container, scheduler)
    listener(SimpleNamespace(job_id="model_retrain", exception=KeyError("2026-10-29")))
    listener(SimpleNamespace(job_id="gone", exception=RuntimeError("x")))        # a job no longer listed: its id
    found = failures.recent(tmp_path / "instance")
    assert {row["job"]: row["name"] for row in found} == {"model_retrain": "模型重訓（交易日 22:45）", "gone": "gone"}
    assert [title for _key, title in sent] == ["模型重訓（交易日 22:45）失敗", "gone失敗"]


def test_the_status_and_the_daily_check_show_the_failed_jobs(tmp_path):
    research = tmp_path / "research"
    research.mkdir()
    name = "過去 24 小時排程工作沒有失敗"
    assert {item["name"]: item for item in invariants.run(research, date(2026, 11, 12), windows=[], memory=[], now=NOW)}[name]["ok"]
    failures.record(tmp_path, "model_retrain", "模型重訓（交易日 22:45）", KeyError("2026-10-29"),
                    now=datetime(2026, 11, 11, 22, 51, tzinfo=ZONE))
    found = {item["name"]: item for item in invariants.run(research, date(2026, 11, 12), windows=[], memory=[], now=NOW)}[name]
    assert not found["ok"] and "模型重訓（交易日 22:45）（11/11 22:51）：KeyError: '2026-10-29'" in found["detail"]
    text = status.render(status.collect(research, NOW))
    assert "排程工作最近 24 小時失敗 1 個" in text and "模型重訓（交易日 22:45）（11-11 22:51）：KeyError: '2026-10-29'" in text


def test_no_channel_for_the_failures_is_red_and_the_status_says_so(tmp_path, monkeypatch):
    """2026-11-12: with neither LINE nor email set, every failure notification ended as "disabled" and nothing said so."""
    research = tmp_path / "research"
    research.mkdir()
    name = "失敗通知有管道可以送出"
    monkeypatch.setattr(invariants, "notification_channel", lambda: None)
    found = {item["name"]: item for item in invariants.run(research, date(2026, 11, 12), windows=[], memory=[], now=NOW)}[name]
    assert not found["ok"] and "LINE 與信箱都沒有設定" in found["detail"]
    assert "失敗通知：沒有管道" in status.render(status.collect(research, NOW))
    monkeypatch.setattr(invariants, "notification_channel", lambda: "email")
    found = {item["name"]: item for item in invariants.run(research, date(2026, 11, 12), windows=[], memory=[], now=NOW)}[name]
    assert found["ok"] and found["detail"] == "信箱"
    assert "失敗通知：信箱" in status.render(status.collect(research, NOW))


def test_the_channel_is_read_from_the_settings(monkeypatch):
    from types import SimpleNamespace

    from quant_platform.config import settings as settings_module

    base = dict(line_enabled=False, line_channel_access_token="", line_to="", email_enabled=False, smtp_host="smtp.x",
                smtp_from="", report_email_to="")
    monkeypatch.setattr(settings_module, "get_settings", lambda: SimpleNamespace(**base))
    assert invariants.notification_channel() is None                   # a host alone is not a channel
    monkeypatch.setattr(settings_module, "get_settings", lambda: SimpleNamespace(
        **{**base, "email_enabled": True, "smtp_from": "a@x", "report_email_to": "b@x"}))
    assert invariants.notification_channel() == "email"
    monkeypatch.setattr(settings_module, "get_settings", lambda: SimpleNamespace(
        **{**base, "line_enabled": True, "line_channel_access_token": "t", "line_to": "u"}))
    assert invariants.notification_channel() == "line"
