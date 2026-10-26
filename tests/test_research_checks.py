"""2026-10-26: the search funnel and the correctness checks on the research overview (research/checks.py)."""
import json
from datetime import datetime

from quant_platform.research import checks


def _scan(research, name, candidates, passed, results, tiers, minutes=10.0):
    folder = research / "scans" / name
    folder.mkdir(parents=True)
    (folder / "summary.json").write_text(json.dumps({"candidates": candidates, "passed_screen": passed, "results": results,
                                                     "tiers": tiers, "minutes": minutes}, ensure_ascii=False), encoding="utf-8")


def test_funnel_adds_the_rounds_and_takes_the_biggest_blend(tmp_path):
    _scan(tmp_path, "scan-1.0.0-20261010-015056", 1000, 100,
          [{"full_excess": 0.02}, {"full_excess": -0.01}], {"T0 候選": 1, "T3": 1})
    _scan(tmp_path, "scan-large-1.0.0-20261020-105730", 5000, 400, [{"full_excess": 0.05}], {"T2": 1}, minutes=50.0)
    (tmp_path / "blend_scan").mkdir()
    for number, (count, passed) in enumerate(((300, 30), (900, 60))):
        (tmp_path / "blend_scan" / f"finalists-{number}.json").write_text(json.dumps(
            {"pool": 10 + number, "candidates": count, "passed": passed, "stamp": f"2026101{number}-120000",
             "finalists": [{}] * 3}), encoding="utf-8")
    found = checks.funnel(tmp_path)
    assert found["total"] == {"candidates": 6000, "passed": 500, "finalists": 3, "passing": 1, "beat_full": 2}
    assert found["biggest_blend"]["candidates"] == 900 and found["blend_finalists"] == 6 and found["minutes"] == 60
    assert [row["large"] for row in found["rounds"]] == [False, True]
    assert found["rounds"][0]["at"] == datetime(2026, 10, 10, 1, 50, 56)


def test_correctness_reads_each_checks_latest_result_and_marks_old_ones(tmp_path):
    (tmp_path / "leakcheck").mkdir()
    (tmp_path / "leakcheck" / "leakcheck-20261020-051718.json").write_text(json.dumps(
        {"days": ["2018-06-15"], "factors": ["a", "b"], "leaks": []}), encoding="utf-8")
    (tmp_path / "audit").mkdir()
    (tmp_path / "audit" / "trade-audit-20261016-164037.json").write_text(json.dumps(
        {"rules": [{}], "orders": 10, "violation_counts": {"tick": 2}, "checks": {"tick": "升降單位"}}), encoding="utf-8")
    (tmp_path / "audit" / "trade-audit-20261024-223752.json").write_text(json.dumps(
        {"rules": [{}, {}], "orders": 20, "violation_counts": {}, "checks": {"tick": "升降單位"}}), encoding="utf-8")
    (tmp_path / "forward" / "stocks").mkdir(parents=True)
    (tmp_path / "forward" / "stocks" / "audit-20261026-012556.json").write_text(json.dumps(
        {"accounts": 3, "checked_days": 30, "differing_accounts": 1}), encoding="utf-8")
    (tmp_path / "invariants.json").write_text(json.dumps(
        {"at": "2026-10-22T18:24:26+08:00", "checks": [{"name": "a", "ok": True}, {"name": "b", "ok": False, "detail": "x"}]},
        ensure_ascii=False), encoding="utf-8")
    found = {row["key"]: row for row in checks.correctness(tmp_path, now=datetime(2026, 10, 26, 21, 0))}
    assert found["leak"]["ok"] and not found["leak"]["stale"]
    assert found["trades"]["ok"] and found["trades"]["headline"] == "0 筆違規"          # the newest audit, not the older one
    assert not found["forward"]["ok"] and found["forward"]["headline"] == "1 個帳戶不一樣"
    assert not found["decisions"]["ok"] and found["decisions"]["headline"] == "1／2 項通過"
    assert found["screen"]["ok"] is False and found["screen"]["headline"] == "還沒跑過"
    later = {row["key"]: row for row in checks.correctness(tmp_path, now=datetime(2026, 11, 2, 12, 0))}
    assert later["leak"]["stale"] and later["decisions"]["stale"] and not later["forward"]["stale"]


def test_the_dashboard_windows_render(tmp_path):
    """The two windows the overview opens (a field named like a dict method once broke the checks window)."""
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    research = tmp_path / "research"
    _scan(research, "scan-1.0.0-20261010-015056", 1000, 100, [{"full_excess": 0.02}], {"T0 候選": 1})
    (research / "audit").mkdir()
    (research / "audit" / "trade-audit-20261024-223752.json").write_text(json.dumps(
        {"rules": [{}], "orders": 20, "violation_counts": {}, "checks": {"tick": "成交價不是升降單位的整數倍"}},
        ensure_ascii=False), encoding="utf-8")
    (research / "invariants.json").write_text(json.dumps(
        {"at": "2026-10-22T18:24:26+08:00", "checks": [{"name": "前向帳戶沒有 0050", "ok": True}]}, ensure_ascii=False),
        encoding="utf-8")
    client = create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}",
                                                 scheduler_in_web=False))).test_client()
    found = client.get("/research/checks")
    body = found.get_data(as_text=True)
    assert found.status_code == 200 and "成交價不是升降單位的整數倍" in body and "通過・前向帳戶沒有 0050" in body
    assert "還沒跑過" in body                                    # no leak check, forward audit or screen check here
    found = client.get("/research/searches")
    body = found.get_data(as_text=True)
    assert found.status_code == 200 and "1,000" in body and "兩階段" in body
