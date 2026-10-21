"""2026-10-21: every passing strategy in the fill timings it has not run (daily.execution_all_batch)."""
import json

from quant_platform.research import daily
from quant_platform.research.daily import DailyRule
from quant_platform.research.registry import TrialRegistry


def test_a_passing_strategy_gets_the_other_fill_timings_with_its_holding_style_whole(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(daily, "EXECUTION_BEFORE", "9999")
    monkeypatch.setattr(daily, "tier", lambda metrics: ("T0 候選" if metrics.get("ok") else "T3", []))
    research = tmp_path / "instance" / "research"
    (research / "reports").mkdir(parents=True)
    registry = TrialRegistry(research / "trials.jsonl")

    def register(rule, ok):
        name = f"{rule.rule_hash[:12]}.json"
        (research / "reports" / name).write_text(json.dumps({"spec": rule.canonical()}), encoding="utf-8")
        registry.register(kind="candidate", period="recent", spec_hash=rule.rule_hash, spec_name=rule.name,
                          input_hash=rule.rule_hash, data_fingerprint="x", report_file=name,
                          metrics={"engine": daily.ENGINE_VERSION, "ok": ok, "final_value": 1.0})

    register(DailyRule(name="趨勢｜持有：不設最短持有", factors={"trend_200": 1.0}, min_hold=0), True)
    register(DailyRule(name="趨勢＋隔天開盤成交｜持有：不設最短持有", factors={"trend_200": 1.0}, min_hold=0,
                       execution="open"), False)
    register(DailyRule(name="輸的", factors={"momentum_6": 1.0}), False)               # not passing: not studied
    rules = daily.execution_all_batch()
    assert [(rule.name, rule.execution) for rule in rules] == [("趨勢＋隔天收盤成交｜持有：不設最短持有", "next")]
    assert daily.execution_key(rules[0].canonical()) == daily.execution_key(
        DailyRule(name="x", factors={"trend_200": 1.0}, min_hold=0).canonical())
