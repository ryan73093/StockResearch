"""2026-11-08: the accounts opened before the models' refit, recorded again; the GPU given back after a worker job."""
import json
import sys
from types import SimpleNamespace

from quant_platform.research import gpu
from quant_platform.research.stock_forward import StockForwardTracker, unexplained


def test_only_differences_without_a_13_50_reason_are_unexplained():
    rows = [{"rule_hash": "a", "differed": 3, "first": {"day": "2026-10-02"}},
            {"rule_hash": "b", "differed": 1, "first": {"day": "2026-11-03", "table": {"4958": {}}}},
            {"rule_hash": "c", "differed": 0, "first": None}]
    assert unexplained(rows) == {"a"}


def test_an_account_restarted_leaves_the_log_and_goes_to_the_archive_line_by_line(tmp_path):
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    rows = [{"rule_hash": "a", "date": "2026-10-01", "text": "中文"}, {"rule_hash": "b", "date": "2026-10-01", "text": "留下"},
            {"rule_hash": "a", "date": "2026-10-02", "text": "中文"}]
    (folder / "log.jsonl").write_text(chr(10).join(json.dumps(row, ensure_ascii=False) for row in rows) + chr(10), encoding="utf-8")
    tracker = StockForwardTracker(tmp_path)
    assert tracker._archive_records({"a"}, "2026-11-08T23:00:00") == 2
    assert [row["rule_hash"] for row in tracker.records()] == ["b"]
    assert [row["date"] for row in tracker.archived()] == ["2026-10-01", "2026-10-02"]
    assert not (folder / "log.partial").exists()


def test_the_gpu_is_given_back_only_when_a_job_loaded_pytorch(monkeypatch):
    calls = []
    fake = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True, empty_cache=lambda: calls.append("emptied")))
    monkeypatch.setitem(sys.modules, "torch", fake)
    gpu.release()
    assert calls == ["emptied"]
    calls.clear()
    monkeypatch.delitem(sys.modules, "torch")
    gpu.release()
    assert calls == []


def test_a_forward_only_experiment_has_a_fixed_number(tmp_path):
    """2026-11-08: 8 forward accounts (the news veto, the LLM trader, their controls) had no number."""
    from quant_platform.research import numbers
    from quant_platform.research.stock_forward import forward_only

    rules = [rule for rule, _reason in forward_only()]
    assert len(rules) >= 8 and len({rule.rule_hash for rule in rules}) == len(rules)
    assert numbers.label(tmp_path, rules[0].rule_hash) == "#E1" and numbers.label(tmp_path, rules[3].rule_hash) == "#E4"
    assert numbers.experiment_spec("#e4") == rules[3].rule_hash and numbers.experiment_spec("E99") is None
    assert numbers.label(tmp_path, "no-such-rule") == ""


def test_the_overview_summarises_the_longest_run_on_the_lists():
    """2026-11-08: the rules with the most sessions on the lists: how many ahead of 0050, the middle one, the best first."""
    from quant_platform.research.dashboard import chain_summary

    part = {"from": "2026-09-01", "to": "2026-11-06"}
    chained = {"a": {"sessions": 44, "excess": 0.10, "return": 0.02, "benchmark_return": -0.08, "parts": [part]},
               "b": {"sessions": 44, "excess": -0.02, "return": -0.10, "benchmark_return": -0.08, "parts": [part]},
               "c": {"sessions": 44, "excess": -0.05, "return": -0.13, "benchmark_return": -0.08, "parts": [part]},
               "d": {"sessions": 5, "excess": 0.30, "return": 0.20, "benchmark_return": -0.10, "parts": [part]}}
    found = chain_summary(chained, {"a": "甲"}, top=2)
    assert (found["rules"], found["sessions"], found["ahead"], found["median"]) == (3, 44, 1, -0.02)
    assert (found["start"], found["end"], found["benchmark"]) == ("2026-09-01", "2026-11-06", -0.08)
    assert [row["rule_hash"] for row in found["top"]] == ["a", "b"] and found["top"][0]["name"] == "甲"
    # 2026-11-12: the other money (both sold that day) beside it; a record made before it reads as the counted one
    assert found["ahead_sold"] == found["ahead"] and found["median_sold"] == found["median"]
    both = chain_summary({key: {**row, "sold_excess": 0.5} for key, row in chained.items()}, {"a": "甲"}, top=2)
    assert both["ahead_sold"] == both["rules"] and both["median_sold"] == 0.5
    assert chain_summary({}, {}) is None
