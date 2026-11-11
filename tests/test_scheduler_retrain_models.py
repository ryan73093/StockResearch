"""2026-10-21: the nightly retrain found a deep model's year by <year>.pkl only and retrained it every night."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from quant_platform.config.settings import Settings
from quant_platform.container import build_container
from quant_platform.research import model
from quant_platform.research.daily import DailyRule
from quant_platform.scheduler import runner


def test_a_deep_model_with_this_years_networks_is_not_retrained(tmp_path, monkeypatch):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'retrain.db'}"))
    research = tmp_path / "research"
    rule = DailyRule(name="深度", factors={"ml_deep2_top": 1.0})
    (research / "forward" / "stocks").mkdir(parents=True)
    (research / "forward" / "stocks" / "tracked.json").write_text(json.dumps([
        {"rule_hash": rule.rule_hash, "name": rule.name, "kind": "daily", "rule": rule.canonical()}]), encoding="utf-8")
    calls = []
    monkeypatch.setattr(model, "train_year", lambda base, version, year, job=None: calls.append(version) or {"years": {}})
    folder = research / "models" / model.MODELS["ml_deep2_top"]["version"]
    folder.mkdir(parents=True)
    now = datetime(2026, 10, 21, 22, 45, tzinfo=ZoneInfo("Asia/Taipei"))
    runner.retrain_models(container, now)
    assert calls == [model.MODELS["ml_deep2_top"]["version"]]          # no 2026 networks: trained
    (folder / "2026-0.pt").write_bytes(b"x")
    calls.clear()
    runner.retrain_models(container, now)
    assert calls == []                                                   # this year's networks are there: left alone


def test_the_monthly_deep_model_refits_only_the_new_month_once(tmp_path, monkeypatch):
    # dl-2.6.0 refits every month: the nightly check refitted all of the year's months every night (72 minutes)
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'retrain.db'}"))
    research = tmp_path / "research"
    rule = DailyRule(name="深度每月", factors={"ml_deep2_monthly": 1.0})
    (research / "forward" / "stocks").mkdir(parents=True)
    (research / "forward" / "stocks" / "tracked.json").write_text(json.dumps([
        {"rule_hash": rule.rule_hash, "name": rule.name, "kind": "daily", "rule": rule.canonical()}]), encoding="utf-8")
    calls = []
    monkeypatch.setattr(model, "train_year", lambda base, version, year, job=None, months=None:
                        calls.append((version, year, months)) or {"years": {}})
    version = model.MODELS["ml_deep2_monthly"]["version"]
    folder = research / "models" / version
    folder.mkdir(parents=True)
    (folder / "2026-0.pt").write_bytes(b"x")
    (folder / "meta.json").write_text(json.dumps({"trained_at": "2026-10-20T15:57:00+00:00"}), encoding="utf-8")
    october = datetime(2026, 10, 21, 22, 45, tzinfo=ZoneInfo("Asia/Taipei"))
    runner.retrain_models(container, october)
    assert calls == []                                                   # October's month already fitted
    runner.retrain_models(container, datetime(2026, 11, 2, 22, 45, tzinfo=ZoneInfo("Asia/Taipei")))
    assert calls == [(version, 2026, [11])]                              # November: that month only


def test_one_models_failure_does_not_stop_the_others_and_the_run_still_fails(tmp_path, monkeypatch):
    # 2026-11-11: dl-2.6.0's refit raised nine nights in a row; the models after it in the order were never looked at
    import pytest

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'retrain.db'}"))
    research = tmp_path / "research"
    rules = [DailyRule(name="深度每月", factors={"ml_deep2_monthly": 1.0}), DailyRule(name="深度", factors={"ml_deep2_top": 1.0})]
    (research / "forward" / "stocks").mkdir(parents=True)
    (research / "forward" / "stocks" / "tracked.json").write_text(json.dumps([
        {"rule_hash": rule.rule_hash, "name": rule.name, "kind": "daily", "rule": rule.canonical()} for rule in rules]),
        encoding="utf-8")
    monthly, yearly = model.MODELS["ml_deep2_monthly"]["version"], model.MODELS["ml_deep2_top"]["version"]
    calls = []

    def train_year(base, version, year, job=None, months=None):
        calls.append(version)
        if version == monthly:
            raise KeyError("2026-10-29")
        return {"years": {"2026": {"train_days": 2000}}}
    monkeypatch.setattr(model, "train_year", train_year)
    with pytest.raises(KeyError):
        runner.retrain_models(container, datetime(2026, 11, 3, 22, 45, tzinfo=ZoneInfo("Asia/Taipei")))
    assert calls == [monthly, yearly]                                    # the one after the failure was still trained
    from quant_platform.research.jobs import JobLog
    states = {item["name"].split("（")[1].split("，")[0]: (item["status"], item.get("summary")) for item in JobLog(research).jobs()}
    assert states[monthly][0] == "failed" and states[yearly] == ("done", "2026 年模型：訓練 2000 個交易日")
