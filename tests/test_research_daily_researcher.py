"""S9-W07 (2026-10-09): the AI researcher on the new design."""

import json
from types import SimpleNamespace

from quant_platform.research.agent import daily_researcher as dr
from quant_platform.research.daily import DailyRule
from quant_platform.research.registry import TrialRegistry


def tried_rule(base, rule, months, trial="t1"):
    reports = base / "reports"
    reports.mkdir(exist_ok=True)
    name = f"{trial}.json"
    (reports / name).write_text(json.dumps({"spec": rule.canonical(), "monthly_active_returns": months}, ensure_ascii=False),
                                encoding="utf-8")
    TrialRegistry(base / "trials.jsonl").register(
        kind="candidate", period="recent", spec_hash=rule.rule_hash, spec_name=rule.name, input_hash=trial,
        data_fingerprint="daily:x", report_file=name,
        metrics={"full_period_excess": 3.0, "since_2020_excess": 1.0, "max_drawdown": -0.2, "benchmark_max_drawdown": -0.3,
                 "windows": {"1y": {"count": 9, "win_ratio": 0.9}, "3y": {"count": 9, "win_ratio": 0.9, "median_excess": 0.2}}})


TREND = DailyRule(name="每天 站上 200 日均線：前 20 名", factors={"trend_200": 1.0})


def test_the_model_sees_nothing_after_september_2020(tmp_path):
    months = {"2019-01": 0.01, "2020-09": 0.02, "2020-10": 0.50, "2023-05": 0.90}
    tried_rule(tmp_path, TREND, months)
    researcher = dr.DailyResearcher(tmp_path, None)
    (row,) = researcher.tried()
    assert row["early_gap"] == (0.01 + 0.02) / (2 / 12)             # two visible months, per year
    instructions, prompt = researcher.build_prompt()
    assert "2023" not in prompt and "T0" not in prompt and "T1" not in prompt and "50.0" not in prompt
    assert "trend_200" in prompt and "ml_gbm_statements" in prompt and "ml_gbm：" not in prompt
    assert f"最多 {dr.MAX_PROPOSALS} 個" in instructions


def test_proposals_are_checked_and_near_repeats_refused(tmp_path):
    tried_rule(tmp_path, TREND, {"2019-01": 0.01})
    researcher = dr.DailyResearcher(tmp_path, None)

    def proposal(name, factors, **settings):
        return {"family": "x", "hypothesis": "h", "rationale": "r", "failure": "f",
                "rule": {"name": name, "factors": factors, **settings}}

    payload = {"proposals": [
        proposal("同一組因子", {"trend_200": 1.0}, top=30),                       # same factors and 0050 share
        proposal("沒有的因子", {"magic": 1.0}),
        proposal("舊模型", {"ml_gbm": 1.0}),
        proposal("前 12 名", {"revenue_accel": 1.0}, top=12),
        proposal("營收加速＋外資", {"revenue_accel": 1.0, "foreign_buy_20": 0.5}, check="weekly"),
        proposal("一半放 0050", {"eps_growth": 1.0}, core=0.5),                  # the owner holds 0050 apart
        proposal("低波動反轉", {"low_volatility_60": 1.0, "reversal_5d": -1.0}),
        proposal("第三個", {"eps_growth": 1.0}),
    ]}
    accepted, rejected = researcher.validate(payload, researcher.tried())
    assert [rule.name for rule, _notes in accepted] == ["AI：營收加速＋外資", "AI：低波動反轉"]
    reasons = {item["name"]: item["reason"] for item in rejected}
    assert "相同" in reasons["同一組因子"] and "因子" in reasons["沒有的因子"] and "因子" in reasons["舊模型"]
    assert "選項" in reasons["前 12 名"] and "超過" in reasons["第三個"] and "0050" in reasons["一半放 0050"]
    assert len(rejected) == 6                                               # the last valid one is past the cap of 2


class FakeClient:
    model = "fake"

    def json_call(self, instructions, user_input, operation, context=""):
        assert operation == dr.OPERATION
        return {"analysis": "a", "proposals": [{"family": "營收財報", "hypothesis": "營收加速後幾週會延續",
                                                "rationale": "r", "failure": "f",
                                                "rule": {"name": "營收加速", "factors": {"revenue_accel": 1.0},
                                                         "check": "weekly"}}]}, {"input_tokens": 1}

    def budget_status(self):
        return {"spent_usd": 0.01}


def test_a_round_is_registered_before_it_runs_and_shows_in_the_pool(tmp_path, monkeypatch):
    from quant_platform.research import daily

    order = []

    def fake_trial(rule, history, registry, reports, costs, data, fp, fingerprint):
        order.append([entry.get("status") for entry in dr.Journal(tmp_path / "journal.jsonl").entries()])
        return SimpleNamespace(trial_id=99), {"monthly_active_returns": {"2018-03": 0.012, "2024-01": 0.5}}

    monkeypatch.setattr(daily, "run_trial", fake_trial)
    monkeypatch.setattr(daily, "fingerprint", lambda history, universe="twse": "daily:x")
    entry = dr.DailyResearcher(tmp_path, FakeClient()).run_round(tmp_path / "history", load=lambda history: (None, None))
    assert order == [["registered"]]                                       # on record before it ran
    (item,) = entry["accepted"]
    assert entry["status"] == "ok" and item["trial_id"] == 99 and item["name"] == "AI：營收加速"
    assert item["early_gap"] == 0.012 * 12                                  # 2024 is not shown back to it
    assert dr.proposals(tmp_path)[0]["spec_hash"] == item["spec_hash"]
    from quant_platform.research.pool import _sources

    assert _sources(tmp_path)[item["spec_hash"]].startswith("AI 研究員")


def test_the_researcher_runs_once_a_week(tmp_path):
    from datetime import UTC, datetime

    from quant_platform.research.agent.researcher import Journal

    now = datetime(2026, 10, 10, 14, tzinfo=UTC)
    assert not dr.ran_within(tmp_path, 6, now)
    Journal(tmp_path / "journal.jsonl").append({"version": dr.VERSION, "status": "ok", "started_at": "2026-10-09T14:00:00+00:00"})
    assert dr.ran_within(tmp_path, 6, now) and not dr.ran_within(tmp_path, 6, datetime(2026, 10, 17, 14, tzinfo=UTC))


def test_factors_written_as_a_list_are_read():
    """2026-10-09: the first real round wrote the factors as a list of objects."""
    assert dr._factors({"trend_200": 1}) == {"trend_200": 1.0}
    assert dr._factors([{"name": "trend_200", "weight": 1}, {"factor": "revenue_yoy", "weight": 0.5}]) == {
        "trend_200": 1.0, "revenue_yoy": 0.5}
    assert dr._factors([["eps_growth", -1]]) == {"eps_growth": -1.0} and dr._factors(["rsi_14"]) == {"rsi_14": 1.0}
    assert dr._factors("trend_200") is None and dr._factors([{"name": "x", "weight": "強"}]) is None
