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
