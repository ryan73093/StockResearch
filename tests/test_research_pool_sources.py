"""2026-10-21: the pool's rule sources — every batch built once, then the batch runs' own record (research/pool.py)."""
from types import SimpleNamespace

from quant_platform.research import pool


def test_every_batch_is_built_once_and_batch_runs_add_their_rules(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pool, "_every_batch", lambda: calls.append(1) or {"a": "每天決策批次 old"})
    assert pool._build_sources(tmp_path) == {"a": "每天決策批次 old"} and len(calls) == 1
    pool.record_sources(tmp_path, [SimpleNamespace(rule_hash="b"), SimpleNamespace(rule_hash="a")], "每天決策批次 new")
    assert pool._build_sources(tmp_path) == {"a": "每天決策批次 old", "b": "每天決策批次 new"}
    assert len(calls) == 1                                                     # the snapshot is kept
