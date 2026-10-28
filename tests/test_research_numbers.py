"""2026-10-28 (使用者：麻煩每個策略給個編號好嗎): a strategy's number is its first trial and never changes."""
import json

from quant_platform.research import numbers


def test_a_strategy_keeps_its_first_trial_as_its_number(tmp_path):
    rows = [{"trial_id": 12, "kind": "candidate", "period": "recent", "spec_hash": "a" * 64},
            {"trial_id": 15, "kind": "candidate", "period": "recent", "spec_hash": "b" * 64},
            {"trial_id": 40, "kind": "candidate", "period": "recent", "spec_hash": "a" * 64}]   # run again: same number
    (tmp_path / "trials.jsonl").write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    assert numbers.label(tmp_path, "a" * 64) == "#12" and numbers.label(tmp_path, "b" * 64) == "#15"
    assert numbers.spec_of(tmp_path, 12) == "a" * 64 and numbers.spec_of(tmp_path, 40) is None
    assert numbers.label(tmp_path, "c" * 64) == "" and numbers.label(tmp_path, None) == ""
