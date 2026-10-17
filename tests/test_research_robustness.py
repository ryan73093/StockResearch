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
