"""2026-10-28 (research_method §7「重現性」): a sample of the pool's latest results run again by the engine; other money
than the registered is a finding, the overview and the 16:00 check read the latest."""
import json
from datetime import datetime

from quant_platform.research import checks, daily, reproduce_check
from quant_platform.research.registry import TrialRegistry


def _register(research, rules):
    registry = TrialRegistry(research / "trials.jsonl")
    (research / "reports").mkdir(parents=True, exist_ok=True)
    for number, (rule, final, engine) in enumerate(rules, start=1):
        (research / "reports" / f"{number}.json").write_text(json.dumps({"spec": rule.canonical()}), encoding="utf-8")
        registry.register(kind="candidate", period="recent", spec_hash=rule.rule_hash, spec_name=rule.name,
                          input_hash=f"i{number}", data_fingerprint="f", report_file=f"{number}.json",
                          metrics={"engine": engine, "end": "2026-09-30", "final_value": final})


def test_the_sample_is_run_again_on_its_own_universe_and_other_money_is_found(tmp_path, monkeypatch):
    listed = daily.DailyRule(name="上市", factors={"trend_200": 1.0})
    wide = daily.DailyRule(name="上市＋上櫃", factors={"trend_200": 1.0}, universe="all")
    old = daily.DailyRule(name="舊引擎", factors={"high_52w": 1.0})
    half = daily.DailyRule(name="一半 0050", factors={"trend_200": 1.0}, core=0.5)
    _register(tmp_path, [(listed, 1000.0, daily.ENGINE_VERSION), (wide, 2000.0, daily.ENGINE_VERSION),
                         (old, 5.0, "daily-1.2.0"), (half, 7.0, daily.ENGINE_VERSION)])
    assert [rule.name for _record, rule in reproduce_check.pool(tmp_path)] == ["上市", "上市＋上櫃"]

    loaded = []
    monkeypatch.setattr(daily, "load", lambda base, universe="twse": loaded.append(universe) or (universe, universe))

    def evaluate(data, fp, rule, costs, cache, end=None):
        assert data == rule.universe and end.isoformat() == "2026-09-30"
        return {"strategy": {"final_value": 1000.0 if rule.universe == "twse" else 2100.0}}
    monkeypatch.setattr(daily, "evaluate", evaluate)
    path = reproduce_check.run(tmp_path / "history", tmp_path, sample=5, seed=1)
    found = json.loads(path.read_text(encoding="utf-8"))
    assert found["pool"] == 2 and found["compared"] == 2 and found["differing"] == 1 and sorted(loaded) == ["all", "twse"]
    rows = {row["name"]: row for row in found["rows"]}
    assert rows["上市"]["same"] and not rows["上市＋上櫃"]["same"] and rows["上市＋上櫃"]["now"] == 2100.0
    assert reproduce_check.latest(tmp_path)[1]["differing"] == 1
    shown = {row["key"]: row for row in checks.correctness(tmp_path, now=datetime.now())}["reproduce"]
    assert shown["ok"] is False and shown["headline"] == "1 個和登錄的不一樣" and not shown["stale"]
