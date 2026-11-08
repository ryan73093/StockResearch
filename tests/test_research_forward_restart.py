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
