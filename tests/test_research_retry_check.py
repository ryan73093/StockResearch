"""2026-11-13: the diagnostic that measures what trying a refused top-up again would change (research/retry_check.py)."""
import json

import pytest

from quant_platform.research import retry_check


def _item(number, guarded, since="2026-10-01"):
    rule = {"name": f"r{number}", "factors": {"momentum_3": 1.0}, "top": 20, "vol_scale": "taiex_half" if guarded else "none"}
    return {"rule_hash": f"{number:04d}", "name": f"規則{number}", "rule": rule, "kind": "daily", "since": since}


def test_the_rules_are_picked_evenly_from_each_group_and_the_same_every_time():
    items = [_item(number, number % 3 == 0) for number in range(30)]
    first = retry_check.pick(items, 4)
    assert first == retry_check.pick(list(reversed(items)), 4)
    guarded = [item for item in first if item["rule"]["vol_scale"] == "taiex_half"]
    assert len(guarded) == 4 and len(first) == 8
    assert [item["rule_hash"] for item in guarded] == ["0000", "0006", "0015", "0021"]   # 10 guarded, every 2.5th
    assert len(retry_check.pick(items[:5], 40)) == 5                                      # fewer than asked: all


def test_only_the_months_accounts_of_single_rules_are_candidates(tmp_path):
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    tracked = [_item(1, True), _item(1, True), _item(2, False), _item(3, False, since="2026-09-01"),
               {**_item(4, True), "stopped": "2026-11-02"}, {**_item(5, False), "kind": "blend"}]
    (folder / "tracked.json").write_text(json.dumps(tracked, ensure_ascii=False), encoding="utf-8")
    assert [item["rule_hash"] for item in retry_check.candidates(tmp_path)] == ["0001", "0002"]


def test_the_summary_compares_the_money_counted_and_the_tiers():
    def row(guarded, before, after, tier_before="T1", tier_after="T1", registered=None):
        return {"name": "x", "guarded": guarded, "registered_counted": registered,
                "without": {"counted_value": before, "max_drawdown": -0.40, "tier": tier_before},
                "with": {"counted_value": after, "max_drawdown": -0.41, "tier": tier_after}}

    found = retry_check.summarize([row(True, 100.0, 110.0, registered=100.0), row(True, 100.0, 102.0, "T1", "T0 候選"),
                                   row(False, 100.0, 99.0, registered=99.5)])
    assert found["guarded"]["rules"] == 2 and found["guarded"]["median_change"] == pytest.approx(0.06)
    assert found["guarded"]["gained"] == 2 and found["guarded"]["tier_changed"] == [("x", "T1", "T0 候選")]
    assert found["guarded"]["median_drawdown_change"] == pytest.approx(-0.01)
    assert found["plain"]["lost"] == 1 and found["plain"]["median_change"] == pytest.approx(-0.01)
    assert (found["checked"], found["reproduced"]) == (2, 2)


def test_each_rule_runs_without_and_with_the_option_on_the_engines_path(tmp_path, monkeypatch):
    from quant_platform.research import daily

    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    (folder / "tracked.json").write_text(json.dumps([_item(1, True), _item(2, False)], ensure_ascii=False),
                                         encoding="utf-8")
    calls = []

    class Panel:
        pass

    monkeypatch.setattr(daily, "load", lambda base: (object(), Panel()))

    def evaluate(data, fp, rule, costs, cache, retry_topups=False):
        calls.append((rule.name, retry_topups, fp._memo == {} or True))
        return {"value": 110.0 if retry_topups else 100.0}

    monkeypatch.setattr(daily, "evaluate", evaluate)
    monkeypatch.setattr(daily, "report_metrics", lambda report, engine, plan: {
        "counted_value": report["value"], "max_drawdown": -0.3, "trades": 1, "sold_value": report["value"]})
    monkeypatch.setattr(daily, "tier", lambda metrics: ("T1", []))
    found = retry_check.run(tmp_path, tmp_path, costs=None, per_group=5)
    assert [(name, retry) for name, retry, _memo in calls] == [("r1", False), ("r1", True), ("r2", False), ("r2", True)]
    assert found["summary"]["guarded"]["median_change"] == pytest.approx(0.10)
    assert len(list((tmp_path / "diagnostics").glob("retry-topups-*.json"))) == 1
