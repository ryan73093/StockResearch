"""2026-10-20: the owner's decisions checked as code (research/invariants.py)."""
import json
from datetime import date

from quant_platform.research import invariants
from quant_platform.research.daily import DailyRule


def test_an_account_whose_rule_differs_from_the_months_list_is_caught(tmp_path):
    graded = DailyRule(name="a", factors={"trend_200": 1.0})
    converted = DailyRule(name="a、月線上續抱", factors={"trend_200": 1.0}, hold="trend")
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    accounts = [{"rule_hash": rule.rule_hash, "name": rule.name, "rule": rule.canonical(), "kind": "daily",
                 "since": "2026-09-01", "initial": 300_000, "monthly": 10_000} for rule in (graded, converted)]
    (folder / "tracked.json").write_text(json.dumps(accounts, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "rolling").mkdir()
    (tmp_path / "rolling" / "2026-08-31.json").write_text(json.dumps({"graded": [
        {"rule_hash": graded.rule_hash, "passed": True}]}), encoding="utf-8")
    checks = {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 20))}
    same = checks["前向帳戶的規則和每月名單上評過分的一模一樣（平台不轉換任何設定）"]
    assert not same["ok"] and "a、月線上續抱" in same["detail"]
    assert checks["前向帳戶沒有 0050"]["ok"] and checks["前向帳戶從當月截止日隔天起算"]["ok"]
    assert checks["前向帳戶現金流是 30 萬＋每月 1 萬"]["ok"]
    accounts[0]["since"] = "2026-10-01"
    (folder / "tracked.json").write_text(json.dumps(accounts, ensure_ascii=False), encoding="utf-8")
    assert not {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 20))}["前向帳戶從當月截止日隔天起算"]["ok"]
    tile = invariants.tile({"at": "2026-10-20T16:00:00", "checks": list(checks.values())})
    assert tile["badge"] == "badge--bad" and "不符" in tile["state"]


def test_an_account_paid_a_month_it_started_after_is_caught(tmp_path):
    from datetime import timedelta

    import pyarrow as pa
    import pyarrow.parquet as pq

    rule = DailyRule(name="LLM", factors={"llm_trader": 1.0})
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    (folder / "tracked.json").write_text(json.dumps([{"rule_hash": rule.rule_hash, "name": rule.name, "kind": "daily",
                                                      "rule": rule.canonical(), "since": "2026-10-20",
                                                      "initial": 300_000, "monthly": 10_000}]), encoding="utf-8")
    calendar = [date(2026, 9, 1) + timedelta(days) for days in range(50)]
    (tmp_path / "history" / "daily").mkdir(parents=True)
    pq.write_table(pa.table({"date": calendar}), tmp_path / "history" / "daily" / "0050.parquet")
    name = "前向帳戶實際投入＝啟動 30 萬＋每月 5 日 1 萬（照交易日曆）"

    def paid(amount):
        (folder / "log.jsonl").write_text(json.dumps({"date": "2026-10-20", "rule_hash": rule.rule_hash,
                                                      "contributed": amount, "cash": amount, "value": amount,
                                                      "holdings": []}) + "\n", encoding="utf-8")
        return {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 20))}[name]["ok"]

    assert not paid(310_000)                    # October's 1 萬 on the 20th: the 5th had passed
    assert paid(300_000)


def test_a_rule_graded_t0_before_the_first_possible_day_is_caught(tmp_path, monkeypatch):
    from quant_platform.research import daily
    from quant_platform.research.registry import TrialRegistry

    (tmp_path / "forward" / "stocks").mkdir(parents=True)
    (tmp_path / "forward" / "stocks" / "tracked.json").write_text("[]", encoding="utf-8")
    TrialRegistry(tmp_path / "trials.jsonl").register(kind="candidate", period="recent", spec_hash="h", spec_name="早到的",
                                                       input_hash="h", data_fingerprint="x", metrics={})
    name = "12/28 以前沒有規則被標成 T0"
    monkeypatch.setattr(daily, "tier", lambda metrics, forward=None: ("T0 候選", ""))
    assert {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 21))}[name]["ok"]
    monkeypatch.setattr(daily, "tier", lambda metrics, forward=None: ("T0", ""))
    found = {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 21))}[name]
    assert not found["ok"] and "1 個" in found["detail"]


def test_the_latest_trade_audit_must_be_clean(tmp_path):
    (tmp_path / "forward" / "stocks").mkdir(parents=True)
    (tmp_path / "forward" / "stocks" / "tracked.json").write_text("[]", encoding="utf-8")
    folder = tmp_path / "audit"
    folder.mkdir()
    name = "最新的交易規則稽核 0 違規（8 天內）"

    def checked():
        return {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 21))}[name]

    assert not checked()["ok"] and "沒有稽核紀錄" in checked()["detail"]
    (folder / "trade-audit-1.json").write_text(json.dumps({"rules": [{}], "orders": 10, "violation_counts":
                                                           {"turnover": 3}}), encoding="utf-8")
    assert not checked()["ok"]
    (folder / "trade-audit-2.json").write_text(json.dumps({"rules": [{}], "orders": 10, "violation_counts": {}}),
                                               encoding="utf-8")
    assert checked()["ok"]
