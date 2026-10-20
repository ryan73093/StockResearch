"""2026-10-20: the holding styles side by side (research/holding.py)."""
from quant_platform.research.daily import DailyRule, holding_key
from quant_platform.research.holding import style_of


def test_the_styles_and_the_strategy_they_share():
    base = DailyRule(name="a", factors={"trend_200": 1.0}, top=20)
    fixed = base.canonical()
    trend = DailyRule(name="b", factors={"trend_200": 1.0}, top=20, hold="trend", min_hold=0).canonical()
    free = DailyRule(name="c", factors={"trend_200": 1.0}, top=20, min_hold=0).canonical()
    assert [style_of(spec) for spec in (fixed, trend, free)] == ["固定持有 20 天", "月線上續抱", "不設最短持有"]
    assert holding_key(fixed) == holding_key(trend) == holding_key(free)              # the same strategy
    assert holding_key(DailyRule(name="a", factors={"trend_200": 1.0}, top=30).canonical()) != holding_key(fixed)
    assert style_of(DailyRule(name="d", factors={"trend_200": 1.0}, min_hold=10).canonical()) is None


def test_a_sleeve_is_shown_with_the_style_it_holds_not_the_one_its_name_came_from():
    from quant_platform.research.blend import BlendRule, Sleeve
    from quant_platform.research.holding import named
    from quant_platform.research.pool import describe_rule

    free = DailyRule(name="掃描：技術模型、每週決策、月線上續抱", factors={"trend_200": 1.0}, top=20, min_hold=0)
    trend = DailyRule(name="趨勢", factors={"trend_200": 1.0}, top=20, hold="trend", min_hold=0)
    assert named(free.canonical()) == "掃描：技術模型、每週決策｜持有：不設最短持有"
    assert named(trend.canonical()) == "趨勢｜持有：月線上續抱"
    blend = BlendRule(name="組合", core=0.0, sleeves=(Sleeve(rule=free, share=0.6), Sleeve(rule=trend, share=0.4)))
    lines = describe_rule(blend.canonical())
    assert lines[1:] == ["60%：掃描：技術模型、每週決策｜持有：不設最短持有", "40%：趨勢｜持有：月線上續抱"]


def test_every_passing_strategy_gets_the_holding_styles_it_has_not_run(tmp_path, monkeypatch):
    import json

    from quant_platform.research import daily
    from quant_platform.research.registry import TrialRegistry

    monkeypatch.chdir(tmp_path)
    research = tmp_path / "instance" / "research"
    (research / "reports").mkdir(parents=True)
    registry = TrialRegistry(research / "trials.jsonl")
    monkeypatch.setattr(daily, "tier", lambda metrics: ("T0 候選" if metrics.get("ok") else "T3", []))

    def register(rule, ok):
        name = f"{rule.rule_hash[:12]}.json"
        (research / "reports" / name).write_text(json.dumps({"spec": rule.canonical()}), encoding="utf-8")
        registry.register(kind="candidate", period="recent", spec_hash=rule.rule_hash, spec_name=rule.name,
                          input_hash=rule.rule_hash, data_fingerprint="x", report_file=name,
                          metrics={"engine": daily.ENGINE_VERSION, "ok": ok, "final_value": 1.0})

    passing = DailyRule(name="趨勢、月線上續抱", factors={"trend_200": 1.0}, top=20, hold="trend", min_hold=0)
    register(passing, True)
    register(DailyRule(name="趨勢", factors={"trend_200": 1.0}, top=20), False)      # its fixed style: already run
    register(DailyRule(name="輸的", factors={"momentum_6": 1.0}, top=20), False)     # not passing: not studied
    rules = daily.holding_all_batch()
    assert [(rule.name, rule.min_hold, rule.hold) for rule in rules] == [("趨勢｜持有：不設最短持有", 0, "days")]
