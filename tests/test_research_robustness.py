"""2026-10-18: the robustness check doubles every cost but the tax and picks the best T0 candidates."""
import json

from quant_platform.research import robustness
from quant_platform.research.costs import broker_costs
from quant_platform.research.registry import TrialRegistry


def test_doubled_costs_keep_the_tax():
    costs = broker_costs("cathay")
    dearer = costs.scaled(robustness.COST_FACTOR)
    assert dearer.fee_rate == costs.fee_rate * 2 and dearer.slippage_bps == costs.slippage_bps * 2
    assert dearer.minimum_fee == costs.minimum_fee * 2 and dearer.tax_rates == costs.tax_rates


def test_the_candidates_are_the_best_single_listed_t0_rules(tmp_path, monkeypatch):
    from quant_platform.research import daily

    registry = TrialRegistry(tmp_path / "trials.jsonl")
    (tmp_path / "reports").mkdir()
    specs = {"a": {"name": "a", "factors": {"x": 1.0}}, "b": {"name": "b", "factors": {"x": 1.0}, "core": 0.5},
             "c": {"name": "c", "factors": {"x": 1.0}}, "d": {"kind": "blend", "name": "d"}}
    for number, (key, spec) in enumerate(specs.items()):
        (tmp_path / "reports" / f"{key}.json").write_text(json.dumps({"spec": spec}), encoding="utf-8")
        registry.register(kind="candidate", period="recent", spec_hash=key, spec_name=key, input_hash=key,
                          data_fingerprint="x", report_file=f"{key}.json",
                          metrics={"engine": daily.ENGINE_VERSION, "final_value": 1_000 * (number + 1)})
    monkeypatch.setattr(daily, "tier", lambda metrics: ("T0 候選", ""))
    chosen = robustness.candidates(tmp_path, 5)
    assert [record.spec_name for record, _spec in chosen] == ["c", "a"]       # the 0050 core and the blend left out


def test_named_trials_are_checked_whatever_their_tier_blends_too(tmp_path):
    import json

    from quant_platform.research import robustness
    from quant_platform.research.blend import BlendRule, Sleeve
    from quant_platform.research.daily import DailyRule
    from quant_platform.research.registry import TrialRegistry

    (tmp_path / "reports").mkdir()
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    single = DailyRule(name="a", factors={"trend_200": 1.0})
    blend = BlendRule(name="b", core=0.0, sleeves=(Sleeve(rule=single, share=0.5),
                                                   Sleeve(rule=DailyRule(name="c", factors={"momentum_6": 1.0}), share=0.5)))
    for rule in (single, blend):
        name = f"{rule.rule_hash[:8]}.json"
        (tmp_path / "reports" / name).write_text(json.dumps({"spec": rule.canonical()}), encoding="utf-8")
        registry.register(kind="candidate", period="recent", spec_hash=rule.rule_hash, spec_name=rule.name,
                          input_hash=rule.rule_hash, data_fingerprint="x", report_file=name, metrics={})
    chosen = robustness.chosen_trials(tmp_path, [2, 1, 99])
    assert [record.trial_id for record, _spec in chosen] == [2, 1]
    assert chosen[0][1]["kind"] == "blend"


def test_open_fills_are_found_and_earlier_checks_stay_on_the_page(tmp_path):
    """2026-10-22 (開盤賣做不做得到): a rule or a blend's sleeve filling in the opening auction gets the tightened
    run; the page keeps the rows of earlier checks on the same engine that the newest did not run again."""
    import json

    from quant_platform.research import robustness
    from quant_platform.research.blend import BlendRule, Sleeve
    from quant_platform.research.daily import DailyRule

    split = DailyRule(name="a", factors={"trend_200": 1.0}, top=3, execution="split")
    plain = DailyRule(name="b", factors={"trend_200": 1.0}, top=3)
    assert robustness.opens_auction(split) and not robustness.opens_auction(plain)
    assert robustness.opens_auction(BlendRule(name="c", sleeves=(Sleeve(rule=plain, share=0.5), Sleeve(rule=split, share=0.5))))
    folder = tmp_path / "robustness"
    folder.mkdir()
    row = lambda trial, money: {"trial": trial, "base": {"final_value": money}}
    (folder / "20261021-000000.json").write_text(json.dumps({"engine": "e1", "rows": [row(1, 10), row(2, 30)]}), encoding="utf-8")
    (folder / "20261020-000000.json").write_text(json.dumps({"engine": "e0", "rows": [row(9, 99)]}), encoding="utf-8")
    (folder / "20261022-000000.json").write_text(json.dumps({"engine": "e1", "rows": [row(2, 31), row(3, 20)]}), encoding="utf-8")
    found = robustness.latest(tmp_path)
    assert [(item["trial"], item["base"]["final_value"]) for item in found["rows"]] == [(2, 31), (3, 20), (1, 10)]
