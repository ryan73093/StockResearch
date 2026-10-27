"""2026-10-27 (使用者 10/26：2015 到 2020 和 2020 到現在交易的風氣差很多，不要再用 2015～2020 作為篩選；短歷史賽道加重;
10/27：問我幹嘛): the gate on the 2020-10 track — its windows, the registry's backfill beside it, the month's list."""
import json
from datetime import date, timedelta
from types import SimpleNamespace

from quant_platform.research import daily, windows2020


def _sessions(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def test_the_windows_from_2020_11_are_kept_beside_all_of_them(monkeypatch):
    """Behind 0050 in every window starting before 2020-11, ahead in every one from it: 1-year windows from 2019-01."""
    data = SimpleNamespace(sessions=_sessions(date(2019, 1, 1), date(2023, 12, 31)))
    monkeypatch.setattr(daily, "simulate_daily", lambda *args, **kwargs: SimpleNamespace(final_value=100.0, contributed=100.0))

    def account(first, last):
        return SimpleNamespace(final_value=110.0 if first >= date(2020, 11, 1) else 95.0, contributed=100.0)
    found = daily.account_windows(data, account, None, 12, date(2019, 1, 2), date(2023, 12, 31), {})
    # starts 2019-01 .. 2023-01 (49); from 2020-11: 2020-11 .. 2023-01 (27)
    assert found["count"] == 49 and found["win_ratio"] == round(27 / 49, 4)
    assert found["since_2020"] == {"count": 27, "win_ratio": 1.0, "median_excess": 0.1, "worst_excess": 0.1, "best_excess": 0.1}


def test_2015_to_2020_is_no_gate_and_the_2020_windows_decide():
    windows = {"1y": {"count": 120, "win_ratio": 0.3, "since_2020": {"count": 59, "win_ratio": 0.7}},
               "3y": {"count": 100, "win_ratio": 0.2, "median_excess": -0.1,
                      "since_2020": {"count": 35, "win_ratio": 0.8, "median_excess": 0.3}}}
    metrics = {"start": "2015-06-01", "full_period_excess": -0.4, "since_2020_excess": 0.6, "windows": windows,
               "max_drawdown": -0.36, "benchmark_max_drawdown": -0.34}
    assert daily.gate(metrics) == [] and daily.tier(metrics)[0] == "T0 候選"     # behind from 2015-06: reported only
    weak = {**windows, "1y": {**windows["1y"], "since_2020": {"count": 59, "win_ratio": 0.45}}}
    assert daily.gate({**metrics, "windows": weak}) == ["2020-10 以後的 1 年視窗勝率 45% < 50%"]
    assert daily.tier({**metrics, "since_2020_excess": -0.01})[0] == "T3"
    # a result kept before the windows had their 2020 part: its windows from 2015-06 until the backfill
    assert daily.regime_windows({"windows": {"1y": {"count": 9, "win_ratio": 0.2}}})["1y"] == {"count": 9, "win_ratio": 0.2}


def test_the_registry_reads_the_backfilled_windows_and_its_chain_stays_whole(tmp_path):
    from quant_platform.research.registry import SUPPLEMENT, TrialRegistry

    registry = TrialRegistry(tmp_path / "trials.jsonl")
    metrics = {"engine": daily.ENGINE_VERSION, "end": "2026-09-30", "final_value": 5.0,
               "windows": {"1y": {"count": 120, "win_ratio": 0.4}, "3y": {"count": 100, "win_ratio": 0.5}}}
    record = registry.register(kind="candidate", period="recent", spec_hash="s", spec_name="x", input_hash="i",
                               data_fingerprint="f", metrics=metrics)
    assert "since_2020" not in registry.records()[0].metrics["windows"]["1y"]
    (tmp_path / SUPPLEMENT).write_text(json.dumps({"input_hash": "i", "trial": record.trial_id, "windows": {
        "1y": {"count": 60, "win_ratio": 0.7}, "3y": {"count": 36, "win_ratio": 0.9, "median_excess": 0.2}}}) + "\n",
        encoding="utf-8")
    merged = registry.records()[0].metrics["windows"]
    assert merged["1y"]["since_2020"]["win_ratio"] == 0.7 and merged["1y"]["win_ratio"] == 0.4
    assert registry.verify() == [] and "since_2020" not in registry.written()[0].metrics["windows"]["1y"]
    registry.register(kind="candidate", period="recent", spec_hash="t", spec_name="y", input_hash="j",
                      data_fingerprint="f", metrics=metrics)
    assert registry.verify() == [] and len(registry.records()) == 2


def test_the_months_list_is_graded_again_on_the_2020_windows(tmp_path, monkeypatch):
    from quant_platform.research import rolling

    windows = {"1y": {"count": 120, "win_ratio": 0.3}, "3y": {"count": 100, "win_ratio": 0.2, "median_excess": -0.1}}
    rule = daily.DailyRule(name="a", factors={"trend_200": 1.0})
    base = {"start": "2015-06-01", "full_period_excess": -0.4, "since_2020_excess": 0.6, "windows": windows,
            "max_drawdown": -0.36, "benchmark_max_drawdown": -0.34, "final_value": 900.0}
    rows = [{"trial": 1, "rule_hash": "a", "name": "a", "spec": rule.canonical(), "metrics": base, "tier": "T3",
             "passed": False},
            {"trial": 2, "rule_hash": "b", "name": "b", "spec": rule.canonical(), "metrics": base, "tier": "T3",
             "passed": False},                                                   # the engine no longer reproduces it
            {"trial": 3, "rule_hash": "c", "name": "c", "spec": rule.canonical(), "metrics": base, "tier": "T3",
             "passed": False, "carried_from": "2026-07-31"}]
    rolling.save(tmp_path, date(2026, 8, 31), {"cutoff": "2026-08-31", "graded": rows})
    items = windows2020.list_items(json.loads((tmp_path / "rolling" / "2026-08-31.json").read_text(encoding="utf-8")))
    assert [(item["key"], bool(item.get("full"))) for item in items] == [("a", False), ("b", False), ("c", True)]
    good = {"1y": {"count": 59, "win_ratio": 0.7}, "3y": {"count": 35, "win_ratio": 0.8, "median_excess": 0.3}}
    full = {**base, "since_2020_excess": -0.2, "windows": {key: {**value, "since_2020": value} for key, value in windows.items()}}
    ran = []

    def run(items, base_, broker, workers, progress=None):
        ran.append([(item["key"], bool(item.get("full"))) for item in items])
        for item in items:
            row = {"key": item["key"], "final": 900.0, "windows": good, "since_2020_final_value": 380.0,
                   "since_2020_benchmark_final_value": 300.0}
            if item.get("full"):
                yield {**row, "same": None, "metrics": {**full, "since_2020_final_value": 1.0}}
            else:
                yield {**row, "same": item["key"] != "b"}
    monkeypatch.setattr(windows2020, "_run", run)
    counts = windows2020.backfill_list(tmp_path, tmp_path, date(2026, 8, 31))
    assert ran == [[("a", False), ("b", False), ("c", True)], [("b", True)]]
    assert counts == {"rows": 3, "full": 2, "errors": 0, "changed": 1}
    saved = json.loads((tmp_path / "rolling" / "2026-08-31.json").read_text(encoding="utf-8"))
    graded = {row["rule_hash"]: row for row in saved["graded"]}
    assert graded["a"]["tier"] == "T0 候選" and graded["a"]["passed"] and graded["a"]["regraded"]["before"] == "T3"
    assert graded["a"]["metrics"]["windows"]["1y"]["since_2020"] == good["1y"]
    assert graded["a"]["metrics"]["since_2020_final_value"] == 380.0
    assert graded["b"]["tier"] == "T3" and graded["c"]["tier"] == "T3" and "carried_from" not in graded["c"]
    assert saved["gate"] == "2020-10" and windows2020.list_items(saved) == []
