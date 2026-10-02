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
    assert "目前資料版本 2 個候選試驗" in body and "回撤 5%" in body and "開發期" in body


def test_fingerprint_covers_only_data_up_to_the_end():
    from quant_platform.research.periods import period_basis

    later = SESSIONS[-1] + timedelta(days=3)
    appended = MarketData(
        sessions=[*SESSIONS, later],
        closes={"0050": {**MARKET.closes["0050"], later: 99.0}},
        tax_kind={"0050": "stock_etf"},
    )
    corrected = MarketData(
        sessions=SESSIONS,
        closes={"0050": {**MARKET.closes["0050"], date(2016, 3, 1): 1.0}},
        tax_kind={"0050": "stock_etf"},
    )
    end = date(2016, 12, 30)

    assert appended.fingerprint_until(end) == MARKET.fingerprint_until(end)       # a new day after the end
    assert corrected.fingerprint_until(end) != MARKET.fingerprint_until(end)      # a corrected old close
    assert appended.fingerprint_until(later) != MARKET.fingerprint_until(later)
    assert period_basis(MARKET, "development") == MARKET.fingerprint_until(end)


def test_trials_on_corrected_data_replace_old_ones_and_each_rule_counts_once(tmp_path):
    from quant_platform.research.periods import period_basis
    from quant_platform.research.reports import trial_ranking
    from quant_platform.research.significance import significance

    registry = TrialRegistry(tmp_path / "trials.jsonl")
    corrected = MarketData(
        sessions=SESSIONS,
        closes={"0050": {day: value * 1.01 for day, value in MARKET.closes["0050"].items()}},
        tax_kind={"0050": "stock_etf"},
    )
    for market in (MARKET, corrected):
        for threshold in (0.05, 0.2):
            spec = StrategySpec(
                name=f"回撤 {threshold:.0%}", allocation=Allocation(weights={"0050": 1.0}),
                sizing=Sizing(type="drawdown", drawdown_threshold=threshold),
            )
            outcome = run_trial(kind="candidate", spec=spec, period="development", market=market, plan=PLAN,
                                registry=registry, reports_dir=tmp_path / "reports", window_months=(3,))
            assert outcome.record.data_fingerprint == period_basis(market, "development")

    ranking = trial_ranking(registry.path, "development")
    stats = significance(registry, tmp_path / "reports", "development",
                         fingerprint=period_basis(corrected, "development"))

    assert registry.count("candidate") == 4
    assert ranking["total"] == 2 and ranking["older"] == 2
    assert {row["trial_id"] for row in ranking["rows"]} == {3, 4}
    # Two rules, each run on two data bases: four records, two attempts (owner's decision 2026-10-02).
    assert stats["current_trials"] == 2 and stats["older_trials"] == 2 and stats["records"] == 4
    assert stats["trials"] == 2 and stats["counting"] == "spec_hash"
    assert {item["trial_id"] for item in stats["candidates"]} == {3, 4}
    assert all(item["dsr"]["trials"] == 2 for item in stats["candidates"])


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
    weak = round_summary(registry.path, "development", {
        "basis": "d", "trials": 3, "candidates": [{"dsr": {"deflated_sharpe": 0.4}}], "pbo": {"pbo": 0.6},
    })
    strong = round_summary(registry.path, "development", {
        "basis": "d", "trials": 3, "candidates": [{"dsr": {"deflated_sharpe": 0.99}}], "pbo": {"pbo": 0.1},
    })
    stale = round_summary(registry.path, "development", {
        "basis": "old", "trials": 3, "candidates": [{"dsr": {"deflated_sharpe": 0.99}}], "pbo": {"pbo": 0.1},
    })

    assert pending["verdict"].startswith("統計檢定尚未完成")
    assert stale["verdict"].startswith("統計檢定尚未完成")  # stats from another data basis are not used
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


def test_directions_group_the_batch_names():
    from quant_platform.research.batches import second_batch
    from quant_platform.research.summary import _direction

    assert {_direction(spec.name) for spec in second_batch()} == {"趨勢控制", "ETF 輪動", "核心＋衛星"}
    assert _direction("均線 60 日：弱勢 ×2、強勢 ×0.5") == "均線" and _direction("時點：每月 6 日全數買進 0050") == "時點"


def test_second_batch_covers_trend_rotation_and_core_satellite_with_new_rules():
    from quant_platform.research.batches import first_batch, second_batch

    specs = second_batch()

    assert len(specs) == 28 and len({spec.spec_hash for spec in specs}) == 28 and len({spec.name for spec in specs}) == 28
    assert all(set(spec.assets) <= {"0050", "0056"} for spec in specs)   # what the development period trades
    assert sum(spec.allocation.defensive is not None for spec in specs) == 12
    assert sum(spec.allocation.rotation is not None and not spec.allocation.rotation.core for spec in specs) == 12
    assert sum(bool(spec.allocation.rotation and spec.allocation.rotation.core) for spec in specs) == 4
    assert not {spec.spec_hash for spec in specs} & {spec.spec_hash for spec in first_batch()}


def test_third_batch_rotates_sectors_and_tests_the_leveraged_etf():
    from quant_platform.research.batches import SECTORS, first_batch, second_batch, third_batch
    from quant_platform.research.summary import _direction

    specs = third_batch()
    assert len(specs) == 35 and len({spec.spec_hash for spec in specs}) == 35 and len({spec.name for spec in specs}) == 35
    assert {_direction(spec.name) for spec in specs} == {"板塊輪動", "核心＋衛星", "槓桿型"}
    rotating = [spec for spec in specs if spec.allocation.rotation is not None]
    assert len(rotating) == 28 and all(set(spec.allocation.rotation.candidates) <= {"0050", *SECTORS} for spec in rotating)
    leveraged = [spec for spec in specs if "00631L" in spec.assets]
    assert len(leveraged) == 7
    assert all(spec.allocation.defensive is None or spec.allocation.defensive.signal_asset == "0050" for spec in leveraged)
    earlier = {spec.spec_hash for spec in first_batch()} | {spec.spec_hash for spec in second_batch()}
    assert not earlier & {spec.spec_hash for spec in specs}


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


def test_trial_count_is_distinct_rules_and_variants_are_not_candidates(tmp_path):
    import json

    from quant_platform.research.registry import distinct_rules, one_per_rule
    from quant_platform.research.significance import significance
    from quant_platform.research.statistics import expected_max_sharpe

    registry = TrialRegistry(tmp_path / "trials.jsonl")
    (tmp_path / "reports").mkdir()

    def add(spec_hash, basis, months, **metrics):
        index = registry.count("candidate") + 1
        (tmp_path / "reports" / f"{index}.json").write_text(json.dumps({
            "monthly_active_returns": {f"2010-{month:02d}": value for month, value in enumerate(months, 1)},
        }), encoding="utf-8")
        return registry.register(
            kind="candidate", period="development", spec_hash=spec_hash, spec_name=spec_hash, input_hash=f"in-{index}",
            data_fingerprint=basis, metrics=metrics, report_file=f"{index}.json",
        )

    add("a", "old", [0.0, 0.0])                                   # 1: rule a on the old basis
    add("c", "old", [0.0, 0.0])                                   # 2: rule c only ever ran on the old basis
    add("a", "new", [0.5, 0.5, 0.5, 0.5], cost_scale=2.0)         # 3: a robustness variant of a
    add("a", "new", [0.03, 0.01, 0.03, 0.01], cost_scale=1.0, execution_lag=0)   # 4: a's main run
    add("b", "new", [0.01, -0.01, 0.01, -0.01])                   # 5
    records = registry.records()

    assert distinct_rules(records) == 3                            # a, b, c: five records, three rules
    assert [record.trial_id for record in one_per_rule(records[2:])] == [4, 5]   # the main run, not the variant
    stats = significance(registry, tmp_path / "reports", "development", fingerprint="new")
    assert (stats["trials"], stats["records"], stats["current_trials"], stats["older_trials"]) == (3, 5, 3, 2)
    assert {item["trial_id"] for item in stats["candidates"]} == {4, 5}
    # Monthly Sharpe of trial 4: mean 0.02 / sample stdev 0.011547 = √3; of trial 5: 0. Variance of (√3, 0) = 1.5.
    dsr = next(item["dsr"] for item in stats["candidates"] if item["trial_id"] == 4)
    assert dsr["trials"] == 3 and dsr["expected_max_sharpe"] == pytest.approx(expected_max_sharpe(3, 1.5))
    # Fewer trials lower the bar a Sharpe ratio has to clear.
    assert expected_max_sharpe(2, 0.01) < expected_max_sharpe(4, 0.01)
