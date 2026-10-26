"""2026-10-26 (使用者：x 軸是最後的錢、y 軸是數量，投入的金額在中間，每個策略在 x 軸的哪一點): the end money's spread
(research/distribution.py)."""
import json
import math

from quant_platform.research import distribution


def _rules(research, values, bench=3_000_000):
    (research / "reports").mkdir(parents=True)
    rows = []
    for number, value in enumerate(values):
        name = f"r{number}.json"
        (research / "reports" / name).write_text(json.dumps(
            {"strategy": {"contributed": 1_660_000}, "since_2020": {"strategy": {"contributed": 1_020_000}}}),
            encoding="utf-8")
        rows.append({"trial_id": number, "name": f"規則{number}", "tier": "T3", "spec_hash": f"h{number}", "report_file": name,
                     "start": "2015-06-01", "final_value": value * 2, "benchmark_final_value": bench * 2,
                     "since_2020_final_value": value, "since_2020_benchmark_final_value": bench,
                     "category": {"family": "趨勢動能"}})
    rows.append({"trial_id": 99, "name": "一半 0050", "uses_0050": True, "final_value": 9e9, "report_file": "x"})
    return rows


def test_each_rule_sits_on_its_side_and_the_money_put_in_is_the_middle(tmp_path):
    rows = _rules(tmp_path, [500_000, 2_000_000, 2_500_000, 5_000_000, 12_000_000])
    found = distribution.view(tmp_path, rows)
    recent = found["recent"]
    assert recent["invested"] == 1_020_000 and recent["bench"] == 3_000_000 and found["full"]["invested"] == 1_660_000
    assert recent["counts"] == {"lost": 1, "behind": 2, "ahead": 2}                   # the half-0050 rule is not in it
    assert [item["value"] for item in recent["items"]][0] == 12_000_000
    edges = [item["low"] for item in recent["bins"]] + [recent["bins"][-1]["high"]]
    middle = len(edges) // 2
    assert math.isclose(edges[middle], 102.0)                                         # 萬元, in the middle
    assert math.isclose(edges[middle] / edges[0], edges[-1] / edges[middle])           # as far left as right
    assert sum(item["count"] for item in recent["bins"]) == 5
    assert any("規則4" in item["title"] for item in recent["bins"])
    svg = distribution.charts(found)["recent"]
    assert "投入 102 萬" in svg and "0050 300 萬" in svg and "<circle" not in svg     # the dashboard's: bars only
    assert distribution.charts(found, with_dots=True)["recent"].count("<circle") == 5 * 2   # mid and wide


def test_the_window_lists_both_sides(tmp_path):
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app
    from quant_platform.research import pool

    research = tmp_path / "research"
    rows = _rules(research, [500_000, 5_000_000])
    pool_rows = {"daily": rows}
    original = pool.pool_view
    pool.pool_view = lambda *args, **kwargs: pool_rows
    try:
        client = create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}",
                                                     scheduler_in_web=False))).test_client()
        body = client.get("/research/distribution?track=recent").get_data(as_text=True)
    finally:
        pool.pool_view = original
    assert "錢最多的 40 個" in body and "賠錢的 1 個" in body and "規則1" in body and "趨勢動能" in body
