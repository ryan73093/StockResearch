import json
from datetime import date, timedelta

import pytest

from quant_platform.research.cashflow import ContributionPlan
from quant_platform.research.market import MarketData
from quant_platform.research.periods import ResearchGateError, check_gate, run_trial
from quant_platform.research.registry import TrialRegistry
from quant_platform.research.spec import BASELINES, Allocation, Sizing, StrategySpec


def _weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


SESSIONS = _weekdays(date(2016, 1, 1), date(2022, 6, 30))
MARKET = MarketData(
    sessions=SESSIONS,
    closes={"0050": {day: 50.0 + (index % 40) for index, day in enumerate(SESSIONS)}},
    tax_kind={"0050": "stock_etf"},
    fingerprint="test",
)
PLAN = ContributionPlan(monthly_amount=10_000, day_of_month=5)
CANDIDATE = StrategySpec(
    name="候選：回撤加碼", allocation=Allocation(weights={"0050": 1.0}),
    sizing=Sizing(type="drawdown", drawdown_threshold=0.1),
)


def _register(registry, **overrides):
    values = dict(kind="candidate", period="development", spec_hash="a" * 64, spec_name="x",
                  input_hash="i1", data_fingerprint="d", metrics={"xirr": 0.1})
    values.update(overrides)
    return registry.register(**values)


def test_registry_is_append_only_idempotent_and_tamper_evident(tmp_path):
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    first = _register(registry)
    again = _register(registry)
    second = _register(registry, input_hash="i2")

    assert again == first and registry.count() == 2
    assert second.prev_hash == first.record_hash and registry.verify() == []

    lines = registry.path.read_text(encoding="utf-8").splitlines()
    edited = json.loads(lines[0])
    edited["metrics"]["xirr"] = 0.5
    registry.path.write_text(json.dumps(edited) + "\n" + lines[1] + "\n", encoding="utf-8")
    assert any("被修改" in problem for problem in registry.verify())

    registry.path.write_text(lines[1] + "\n", encoding="utf-8")
    assert registry.verify()  # deleting the first record breaks the chain


def test_holdout_needs_validation_and_runs_once(tmp_path):
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    kwargs = dict(kind="candidate", spec=CANDIDATE, market=MARKET, plan=PLAN, registry=registry,
                  reports_dir=tmp_path / "reports", window_months=(12,))

    with pytest.raises(ResearchGateError):
        run_trial(period="holdout", **kwargs)
    with pytest.raises(ResearchGateError):
        check_gate(registry, "candidate", CANDIDATE, "full")

    validation = run_trial(period="validation", **kwargs)
    holdout = run_trial(period="holdout", **kwargs)
    assert validation.record.period == "validation" and holdout.record.period == "holdout"
    assert holdout.report["strategy"]["start"] >= "2022-01-01"
    with pytest.raises(ResearchGateError):
        run_trial(period="holdout", **kwargs)
    assert registry.count("candidate") == 2 and registry.verify() == []


def test_execution_lag_fills_on_the_next_session_and_changes_the_trial():
    from quant_platform.research.engine import simulate

    on_time = simulate(BASELINES["benchmark_dca"], MARKET, PLAN, end=date(2016, 3, 31))
    late = simulate(BASELINES["benchmark_dca"], MARKET, PLAN, end=date(2016, 3, 31), execution_lag=1)

    assert [trade.day for trade in on_time.trades] == [date(2016, 1, 5), date(2016, 2, 5), date(2016, 3, 7)]
    assert [trade.day for trade in late.trades] == [date(2016, 1, 6), date(2016, 2, 8), date(2016, 3, 8)]
    assert late.input_hash != on_time.input_hash


def test_significance_counts_every_candidate_trial(tmp_path):
    from quant_platform.research.significance import significance

    registry = TrialRegistry(tmp_path / "trials.jsonl")
    for threshold in (0.05, 0.1, 0.2):
        spec = StrategySpec(
            name=f"回撤 {threshold:.0%}", allocation=Allocation(weights={"0050": 1.0}),
            sizing=Sizing(type="drawdown", drawdown_threshold=threshold),
        )
        for period in ("development", "validation"):
            run_trial(kind="candidate", spec=spec, period=period, market=MARKET, plan=PLAN,
                      registry=registry, reports_dir=tmp_path / "reports", window_months=(12,))

    short = significance(registry, tmp_path / "reports", "development")
    report = significance(registry, tmp_path / "reports", "validation")

    assert "pbo" not in short  # 12 shared months: too short to split into 16 blocks
    assert report["trials"] == 3 and len(report["candidates"]) == 3
    assert all(item["dsr"]["trials"] == 3 for item in report["candidates"])
    assert report["pbo"]["months"] == 60 and 0 <= report["pbo"]["pbo"] <= 1


def test_research_page_ranks_candidate_trials(tmp_path):
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    client = create_app(build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False)
    )).test_client()
    assert "個候選試驗" not in client.get("/research").get_data(as_text=True)

    registry = TrialRegistry(tmp_path / "research" / "trials.jsonl")
    for threshold in (0.05, 0.2):
        spec = StrategySpec(
            name=f"回撤 {threshold:.0%}", allocation=Allocation(weights={"0050": 1.0}),
            sizing=Sizing(type="drawdown", drawdown_threshold=threshold),
        )
        run_trial(kind="candidate", spec=spec, period="development", market=MARKET, plan=PLAN,
                  registry=registry, reports_dir=tmp_path / "research" / "reports", window_months=(3,))

    body = client.get("/research").get_data(as_text=True)
    assert "已登錄 2 個候選試驗" in body and "回撤 5%" in body and "開發期" in body


def test_round_summary_states_an_honest_verdict(tmp_path):
    from quant_platform.research.summary import round_summary

    registry = TrialRegistry(tmp_path / "trials.jsonl")
    for index, (name, win, median) in enumerate((
        ("均線 60 日：弱勢 ×2、強勢 ×0.5", 0.7, 0.01), ("均線 200 日：弱勢 ×2、強勢 ×1", 0.4, -0.002),
        ("時點：每月 16 日全數買進 0050", 0.3, -0.001),
    )):
        _register(registry, spec_name=name, input_hash=f"i{index}", metrics={
            "windows": {"3y": {"win_ratio": win, "median_excess": median, "worst_excess": -0.05}},
        })

    pending = round_summary(registry.path, "development", None)
    weak = round_summary(registry.path, "development", {"candidates": [{"dsr": {"deflated_sharpe": 0.4}}], "pbo": {"pbo": 0.6}})
    strong = round_summary(registry.path, "development", {"candidates": [{"dsr": {"deflated_sharpe": 0.99}}], "pbo": {"pbo": 0.1}})

    assert pending["verdict"].startswith("統計檢定尚未完成")
    assert [row["direction"] for row in weak["rows"]] == ["均線", "時點"]
    assert weak["rows"][0]["trials"] == 2 and weak["rows"][0]["passing"] == 1
    assert "沒有設定能證明勝過定期定額" in weak["verdict"] and "DSR 0.40" in weak["verdict"]
    assert "1 個設定通過" in strong["verdict"]


def test_first_batch_is_a_fixed_list_of_distinct_valid_specs():
    from quant_platform.research.batches import first_batch

    specs = first_batch()

    assert len(specs) == 68
    assert len({spec.spec_hash for spec in specs}) == 68 and len({spec.name for spec in specs}) == 68
    assert {spec.spec_hash for spec in specs} == {spec.spec_hash for spec in first_batch()}
    assert all(set(spec.assets) <= {"0050", "0056", "006208"} for spec in specs)


def test_rerunning_a_trial_reuses_the_record_and_baselines_may_use_full(tmp_path):
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    kwargs = dict(kind="baseline", spec=BASELINES["ma_value"], period="full", market=MARKET, plan=PLAN,
                  registry=registry, reports_dir=tmp_path / "reports", window_months=(12,))

    first = run_trial(**kwargs)
    second = run_trial(**kwargs)

    assert not first.reused and second.reused and registry.count() == 1
    assert first.report_path.is_file()
    with pytest.raises(ResearchGateError):
        run_trial(**{**kwargs, "spec": CANDIDATE})  # a non-built-in spec is not a baseline
