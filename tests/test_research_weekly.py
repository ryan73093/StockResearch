import json
from datetime import date

from quant_platform.research.registry import TrialRegistry
from quant_platform.research.weekly import save_weekly_report, week_bounds, weekly_report

GOOD = {"windows": {"3y": {"count": 30, "win_ratio": 0.7, "median_excess": 0.01}}}
WEAK = {"windows": {"3y": {"count": 30, "win_ratio": 0.4, "median_excess": -0.01}}}


def test_week_bounds_are_monday_to_sunday():
    assert week_bounds(date(2026, 10, 1)) == (date(2026, 9, 28), date(2026, 10, 4))


def test_weekly_report_counts_only_this_week(tmp_path):
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    for index, metrics in enumerate((GOOD, WEAK), 1):
        registry.register(kind="candidate", period="development", spec_hash=f"{index}" * 64,
                          spec_name=f"設定 {index}", input_hash=f"i{index}", data_fingerprint="basis", metrics=metrics)
    # Pin the creation time inside the week under test (the registry stamps "now").
    lines = [json.loads(line) for line in registry.path.read_text(encoding="utf-8").splitlines()]
    registry.path.write_text("".join(
        json.dumps({**line, "created_at": "2026-09-30T22:00:00+08:00"}, ensure_ascii=False) + "\n" for line in lines
    ), encoding="utf-8")
    (tmp_path / "journal.jsonl").write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in (
        {"started_at": "2026-09-21T14:00:00+00:00", "hypothesis": "上週的假設", "accepted": [], "rejected": []},
        {"started_at": "2026-09-30T14:00:00+00:00", "status": "ok", "hypothesis": "回撤時多買",
         "accepted": [{"name": "設定 1"}], "rejected": [{"name": "x", "reason": "與已測規則相同"},
                                                      {"name": "y", "reason": "不符合 StrategySpec：weights"}],
         "extension_ideas": ["依月營收調整"]},
    )), encoding="utf-8")
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "usage.jsonl").write_text(
        json.dumps({"at": "2026-09-30T14:00:00+00:00", "cost_usd": 0.0021}) + "\n"
        + json.dumps({"at": "2026-09-20T14:00:00+00:00", "cost_usd": 0.5}) + "\n", encoding="utf-8")

    report = weekly_report(tmp_path, date(2026, 10, 1))

    assert (report["week"], report["start"], report["end"]) == ("2026-W40", "2026-09-28", "2026-10-04")
    assert report["rounds"] == 1 and report["hypotheses"] == ["回撤時多買"]
    assert report["ai_trials"] == 1 and report["rejected"] == 2
    assert dict(report["top_rejections"]) == {"與已測規則相同": 1, "不符合 StrategySpec": 1}
    assert report["new_trials"] == 2 and report["passing_new"] == 1
    assert report["best_new"]["name"] == "設定 1" and report["cost_usd"] == 0.0021
    assert report["next_ideas"] == ["依月營收調整"] and "1 個新設定通過開發期門檻" in report["verdict"]
    assert save_weekly_report(tmp_path, date(2026, 10, 1)).name == "2026-W40.json"
    assert weekly_report(tmp_path, date(2026, 10, 12))["verdict"] == "本週沒有新的研究輪次或試驗。"
