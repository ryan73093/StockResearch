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
