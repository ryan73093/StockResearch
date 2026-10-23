"""2026-10-23 (使用者：績效的成長幅度有沒有個線型圖、到底進步了多少；研究總覽策略數怎麼會只有這麼少): research/progress.py."""
import json

from quant_platform.research import progress


def test_the_search_scale_adds_the_rounds_and_counts_the_biggest_blend_round_once(tmp_path):
    for name, count in (("scan-1.0.0-20261010-015056", 1000), ("scan-large-1.0.0-20261020-105730", 5000)):
        (tmp_path / "scans" / name).mkdir(parents=True)
        (tmp_path / "scans" / name / "summary.json").write_text(json.dumps({"candidates": count}), encoding="utf-8")
    (tmp_path / "blend_scan").mkdir()
    for name, count, pool in (("finalists-a.json", 300, 9), ("finalists-b.json", 100, 5), ("finalists-c.json", 300, 9)):
        (tmp_path / "blend_scan" / name).write_text(json.dumps({"candidates": count, "pool": pool}), encoding="utf-8")
    scale = progress.search_scale(tmp_path)
    assert scale["scans"] == 6000 and scale["scan_rounds"] == 2
    assert scale["blends"] == 300 and scale["blend_rounds"] == 2 and scale["total"] == 6300   # the same round twice: one
    rows = progress.searches(tmp_path)
    assert rows[-1]["total"] == 6300 and [row["kind"] for row in rows[:2]] == ["scan", "scan"]


def test_the_best_is_each_rules_newest_run_so_an_engine_correction_shows(tmp_path, monkeypatch):
    from quant_platform.research import daily
    from quant_platform.research.registry import TrialRegistry

    monkeypatch.setattr(daily, "tier", lambda metrics, forward=None: (metrics["grade"], ""))
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    count = iter(range(100))

    def run(spec, value, grade, engine):
        registry.register(kind="candidate", period="recent", spec_hash=spec, spec_name=spec,
                          input_hash=f"{spec}{next(count)}", data_fingerprint="x",
                          metrics={"final_value": value, "benchmark_final_value": 100.0, "engine": engine, "grade": grade})
    run("a", 300.0, "T0 候選", "daily-1.4.0")
    run("b", 500.0, "T1", "daily-1.4.0")
    run("c", 900.0, "T3", "daily-1.4.0")                  # loses to 0050: never the best
    run("d", 800.0, "T0 候選", "daily-1.4.0")             # holds 0050: left out
    run("a", 250.0, "T0 候選", "daily-1.5.0")             # the newer engine corrects a
    run("a", 999.0, "T0 候選", "daily-1.4.0")             # an older engine's rerun registered later does not count
    rows = {key: {"uses_0050": key == "d"} for key in "abcd"}
    found = progress.timeline(tmp_path, rows=rows)
    last = found["points"][-1]
    assert last["t0"] == 250.0 and last["any"] == 500.0 and last["risky"] is None
    assert last["passing"] == 1 and last["rules"] == 3 and found["benchmark"] == 100.0
    assert [item["engine"] for item in found["engines"]] == ["daily-1.4.0", "daily-1.5.0"]


def test_the_chart_draws_each_line_with_a_hover_value_and_the_guides():
    from datetime import datetime

    points = [{"at": datetime(2026, 10, 4, 2), "a": 100.0}, {"at": datetime(2026, 10, 5, 3), "a": 150.0},
              {"at": datetime(2026, 10, 6, 4), "a": None}]
    svg = progress.chart(points, "at", [{"key": "a", "label": "最好", "css": "s1"}], lambda value: f"{value:.0f}",
                         guides=[(120.0, "0050")], marks=[(datetime(2026, 10, 5), "引擎 1.5")], step=True, label="測試")
    assert svg.startswith('<svg class="progress-chart"') and svg.count("<polyline") == 1
    assert "最好：150" in svg and "0050" in svg and "引擎 1.5" in svg and svg.count("<circle") == 2
    assert progress.chart([], "at", [], str) == ""
