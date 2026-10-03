import json
from datetime import UTC, date, datetime, timedelta

import pytest

from quant_platform.research import promotion
from quant_platform.research.forward import STANDARD_PLAN
from quant_platform.research.market import MarketData
from quant_platform.research.periods import ResearchGateError, period_basis
from quant_platform.research.promotion import (
    PromotionLedger,
    PromotionPipeline,
    drawdown_gate,
    drawdown_track,
    required_tolerance,
    statistics_gate,
    tolerance_gate,
    strategy_catalog,
    window_gate,
)
from quant_platform.research.registry import TrialRegistry
from quant_platform.research.spec import Allocation, Sizing, StrategySpec

NOW = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


SESSIONS = weekdays(date(2004, 2, 11), date(2026, 9, 30))
# A steady rise: never a drawdown, so the candidate keeps 20% of new money as cash and lags DCA.
MARKET = MarketData(sessions=SESSIONS, closes={"0050": {day: 50.0 + index * 0.01 for index, day in enumerate(SESSIONS)}},
                    tax_kind={"0050": "stock_etf"})
SPEC = StrategySpec(
    name="回撤：跌 8% 加倍", allocation=Allocation(weights={"0050": 1.0}),
    sizing=Sizing(type="drawdown", drawdown_threshold=0.08, weak_multiplier=2.0, strong_multiplier=0.8),
)
GOOD_METRICS = {
    "windows": {"3y": {"count": 100, "win_ratio": 0.7, "median_excess": 0.01, "worst_excess": -0.02},
                "5y": {"count": 80, "win_ratio": 0.65, "median_excess": 0.012, "worst_excess": -0.01}},
    "max_drawdown": -0.50, "benchmark_max_drawdown": -0.52,
}


def test_gates_by_hand():
    assert window_gate(GOOD_METRICS) == []
    weak_five = {"windows": {**GOOD_METRICS["windows"], "5y": {"count": 80, "win_ratio": 0.55, "median_excess": 0.01}}}
    assert window_gate(weak_five) == ["5 年勝率 55% < 60%"]
    flat = {"windows": {"3y": {"count": 10, "win_ratio": 0.6, "median_excess": 0.0}}}
    assert window_gate(flat) == ["3 年中位超額 +0.00% ≤ 0"]
    assert window_gate({"windows": {"3y": {"count": 10, "win_ratio": 0.5, "median_excess": -0.1}}}, 0.5, ("3y",)) == []
    assert window_gate({"windows": {}}) == ["沒有 3 年滾動視窗"]
    # Deeper than the benchmark by more than 5 points fails; by 4 points passes.
    assert drawdown_gate({"max_drawdown": -0.60, "benchmark_max_drawdown": -0.54}) != []
    assert drawdown_gate({"max_drawdown": -0.58, "benchmark_max_drawdown": -0.54}) == []
    # Tracks: within 5 points of the benchmark is standard whatever the tolerance says.
    near, deep = ({"max_drawdown": value, "benchmark_max_drawdown": -0.5654} for value in (-0.58, -0.6306))
    assert drawdown_track(near, None) == drawdown_track(near, 0.30) == ("standard", [])
    # 63.06% is 6.5 points deeper: aggressive only when the plan tolerates it.
    track, reasons = drawdown_track(deep, None)
    assert track is None and "尚未建立投資計畫，進攻型賽道未啟用" in reasons
    track, reasons = drawdown_track(deep, 0.60)
    assert track is None and reasons[-1] == "最大回撤 -63.1% 超過計畫可承受回撤 60%"
    assert drawdown_track(deep, 0.65) == ("aggressive", [])
    assert drawdown_track({"max_drawdown": -0.65, "benchmark_max_drawdown": -0.5654}, 0.65) == ("aggressive", [])
    assert drawdown_track({"max_drawdown": -0.9}, 0.8) == (None, ["缺少最大回撤資料"])
    assert tolerance_gate({"max_drawdown": -0.40}, 0.65) == [] and tolerance_gate({"max_drawdown": -0.66}, 0.65) != []


def development_setup(tmp_path, dsr=0.99, pbo=0.1):
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    record = registry.register(
        kind="candidate", period="development", spec_hash=SPEC.spec_hash, spec_name=SPEC.name, input_hash="dev-1",
        data_fingerprint=period_basis(MARKET, "development"), metrics=GOOD_METRICS, report_file="dev.json",
    )
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    (tmp_path / "reports" / "dev.json").write_text(json.dumps({"spec": SPEC.model_dump(mode="json")}), encoding="utf-8")
    (tmp_path / "stats").mkdir(exist_ok=True)
    (tmp_path / "stats" / "development-20261001-000000.json").write_text(json.dumps({
        "basis": record.data_fingerprint, "trials": 1,
        "candidates": [{"trial_id": record.trial_id, "name": SPEC.name, "dsr": {"deflated_sharpe": dsr, "trials": 1},
                        "bootstrap": {"mean": 0.0, "low": 0.0, "high": 0.0}}],
        "pbo": {"pbo": pbo},
    }), encoding="utf-8")
    return record


def pipeline(tmp_path, **limits):
    return PromotionPipeline(tmp_path, STANDARD_PLAN, clock=lambda: NOW, **limits)


def test_statistics_gate_needs_the_current_basis_and_both_thresholds(tmp_path):
    record = development_setup(tmp_path, dsr=0.90, pbo=0.3)
    stats = json.loads(next((tmp_path / "stats").glob("*.json")).read_text(encoding="utf-8"))

    assert statistics_gate(record, stats) == ["DSR 0.90 < 0.95", "PBO 30% > 20%"]
    assert statistics_gate(record, {**stats, "basis": "old"}) == ["統計檢定尚未以目前資料版本完成"]
    assert pipeline(tmp_path).advance(MARKET) == []          # not eligible: nothing recorded


def test_a_candidate_that_fails_validation_stops_there(tmp_path):
    development_setup(tmp_path)

    events = pipeline(tmp_path).advance(MARKET)

    assert [(event.stage, event.outcome) for event in events] == [("development", "passed"), ("validation", "failed")]
    # Rising prices: the cash the candidate holds back never catches up, so it loses every window.
    assert any("勝率 0%" in reason for reason in events[1].evidence["reasons"])
    validation = [record for record in TrialRegistry(tmp_path / "trials.jsonl").records() if record.period == "validation"]
    assert len(validation) == 3                                 # main, costs doubled, one session late
    assert {record.metrics["execution_lag"] for record in validation} == {0, 1}
    assert {record.metrics["cost_scale"] for record in validation} == {1.0, 2.0}
    assert pipeline(tmp_path).advance(MARKET) == []             # a candidate is reviewed once


def test_full_path_to_approval_and_revocation(tmp_path, monkeypatch):
    from quant_platform.application.investment_plan import parse_plan_form

    development_setup(tmp_path)
    monkeypatch.setattr(promotion, "window_gate", lambda *args, **kwargs: [])
    monkeypatch.setattr(promotion, "drawdown_gate", lambda metrics: [])
    flow = pipeline(tmp_path)

    events = flow.advance(MARKET)

    assert [(event.stage, event.outcome) for event in events] == [
        ("development", "passed"), ("validation", "passed"), ("holdout", "passed"), ("forward", "started"),
    ]
    assert (tmp_path / "forward" / "tracked" / f"{SPEC.spec_hash[:16]}.json").is_file()
    holdout = [record for record in flow.registry.records() if record.period == "holdout"]
    assert len(holdout) == 1
    with pytest.raises(ResearchGateError):
        flow.approve(SPEC.spec_hash)                            # forward simulation not finished

    log = tmp_path / "forward" / "log.jsonl"
    days = [day for day in SESSIONS if day >= date(2026, 7, 1)][:40]
    log.write_text("".join(
        json.dumps({"date": day.isoformat(), "spec_hash": SPEC.spec_hash, "name": SPEC.name}) + "\n" for day in days
    ), encoding="utf-8")
    assert flow.advance(MARKET) == []                           # sessions before the forward start do not count
    later = [day.isoformat() for day in weekdays(date(2026, 10, 1), date(2026, 12, 31))][:40]
    log.write_text("".join(
        json.dumps({"date": day, "spec_hash": SPEC.spec_hash, "name": SPEC.name}) + "\n" for day in later
    ), encoding="utf-8")
    assert [(event.stage, event.outcome) for event in flow.advance(MARKET)] == [("forward", "passed")]

    assert strategy_catalog(tmp_path).keys() == {"benchmark_dca", "fixed_day_dca", "ma_value", "rebalance_80_20"}
    approved = flow.approve(SPEC.spec_hash, "看過證據")
    assert approved.actor == "user" and approved.evidence["note"] == "看過證據"
    catalog = strategy_catalog(tmp_path)
    key = f"approved:{SPEC.spec_hash[:16]}"
    assert catalog[key].spec_hash == SPEC.spec_hash
    form = {"monthly_amount": "10000", "salary_day": "5", "strategy_key": key, "max_drawdown_tolerance": "30"}
    assert parse_plan_form(form, catalog)["strategy_key"] == key
    assert flow.ledger.track(SPEC.spec_hash) == "standard" and required_tolerance(tmp_path) == {}
    flow.revoke(SPEC.spec_hash)
    assert key not in strategy_catalog(tmp_path)
    assert flow.ledger.verify() == []


def test_ledger_is_tamper_evident(tmp_path):
    ledger = PromotionLedger(tmp_path / "promotions.jsonl")
    ledger.append(SPEC, "development", "passed", {"trial_id": 1}, now=NOW)
    ledger.append(SPEC, "validation", "failed", {"reasons": ["x"]}, now=NOW)
    lines = ledger.path.read_text(encoding="utf-8").splitlines()
    edited = json.loads(lines[1])
    edited["outcome"] = "passed"
    ledger.path.write_text(lines[0] + "\n" + json.dumps(edited, ensure_ascii=False) + "\n", encoding="utf-8")

    assert ledger.verify()


def test_research_page_explains_why_nothing_is_promoted_and_guards_approval(tmp_path):
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    development_setup(tmp_path / "research", dsr=0.42)
    client = create_app(container).test_client()

    body = client.get("/research?tab=promotion").get_data(as_text=True)
    assert "晉級流程" in body and "目前沒有候選進入晉級流程" in body and "最佳 DSR 0.42" in body

    url = f"/research/promotions/{SPEC.spec_hash}/approve"
    assert "請先勾選" in client.post(url, data={}, follow_redirects=True).get_data(as_text=True)
    refused = client.post(url, data={"confirm": "yes"}, follow_redirects=True).get_data(as_text=True)
    assert "只有完成前向模擬" in refused
    assert client.post("/research/promotions/not-a-hash/approve", data={"confirm": "yes"}).status_code == 404


DEEP_METRICS = {**GOOD_METRICS, "max_drawdown": -0.6306, "benchmark_max_drawdown": -0.5654}


def deep_setup(tmp_path):
    record = development_setup(tmp_path)
    registry_path = tmp_path / "trials.jsonl"
    line = json.loads(registry_path.read_text(encoding="utf-8"))
    assert line["metrics"]["max_drawdown"] == -0.50
    registry_path.unlink()
    registry = TrialRegistry(registry_path)
    return registry.register(
        kind="candidate", period="development", spec_hash=SPEC.spec_hash, spec_name=SPEC.name, input_hash="dev-1",
        data_fingerprint=record.data_fingerprint, metrics=DEEP_METRICS, report_file="dev.json",
    )


def test_aggressive_track_needs_a_plan_tolerance(tmp_path):
    deep_setup(tmp_path)

    # No plan, or a tolerance below the 63.06% drawdown: nothing moves and nothing is written.
    assert pipeline(tmp_path).advance(MARKET) == []
    assert pipeline(tmp_path, drawdown_tolerance=0.60).advance(MARKET) == []
    assert not (tmp_path / "promotions.jsonl").exists()
    closed = pipeline(tmp_path).overview()
    assert closed["gates"]["aggressive_drawdown"] is None
    assert closed["deep_drawdown"] == [{"name": SPEC.name, "max_drawdown": -0.6306, "needed": 64, "within": False}]

    flow = pipeline(tmp_path, drawdown_tolerance=0.65, plan_version=3)
    assert flow.overview()["deep_drawdown"][0]["within"] is True
    events = flow.advance(MARKET)
    assert [(event.stage, event.outcome) for event in events] == [("development", "passed"), ("validation", "failed")]
    first = events[0].evidence
    assert (first["track"], first["drawdown_tolerance"], first["plan_version"], first["max_drawdown"]) == (
        "aggressive", 0.65, 3, -0.6306)
    assert events[1].evidence["track"] == "aggressive" and flow.ledger.track(SPEC.spec_hash) == "aggressive"
    # A later, higher tolerance does not reopen a candidate that already has a record.
    assert pipeline(tmp_path, drawdown_tolerance=0.80).advance(MARKET) == []
    assert flow.ledger.verify() == []
    with pytest.raises(ValueError):
        pipeline(tmp_path, drawdown_tolerance=65)


def test_aggressive_path_to_approval_rechecks_the_tolerance(tmp_path, monkeypatch):
    from quant_platform.application.investment_plan import InvestmentPlanError, parse_plan_form

    deep_setup(tmp_path)
    monkeypatch.setattr(promotion, "window_gate", lambda *args, **kwargs: [])
    # The benchmark-relative gate always fails: only the plan tolerance can let the candidate through.
    monkeypatch.setattr(promotion, "drawdown_gate", lambda metrics: ["比定期定額深"])
    flow = pipeline(tmp_path, drawdown_tolerance=0.65, plan_version=3)

    events = flow.advance(MARKET)
    assert [(event.stage, event.outcome) for event in events] == [
        ("development", "passed"), ("validation", "passed"), ("holdout", "passed"), ("forward", "started"),
    ]
    assert all(event.evidence["track"] == "aggressive" for event in events)
    log = tmp_path / "forward" / "log.jsonl"
    later = [day.isoformat() for day in weekdays(date(2026, 10, 1), date(2026, 12, 31))][:40]
    log.write_text("".join(
        json.dumps({"date": day, "spec_hash": SPEC.spec_hash, "name": SPEC.name}) + "\n" for day in later
    ), encoding="utf-8")
    assert [(event.stage, event.outcome) for event in flow.advance(MARKET)] == [("forward", "passed")]

    # Approval looks at the plan's tolerance at that moment: no plan or a lowered tolerance refuses.
    count = len(flow.ledger.events())
    for limits in ({}, {"drawdown_tolerance": 0.60}):
        with pytest.raises(ResearchGateError):
            pipeline(tmp_path, **limits).approve(SPEC.spec_hash)
        assert pipeline(tmp_path, **limits).overview()["pipeline"][0]["can_approve"] is False
    assert len(flow.ledger.events()) == count
    approved = pipeline(tmp_path, drawdown_tolerance=0.65, plan_version=4).approve(SPEC.spec_hash)
    assert (approved.evidence["track"], approved.evidence["plan_version"]) == ("aggressive", 4)

    # The plan may not adopt it with a tolerance below its deepest recorded drawdown.
    key = f"approved:{SPEC.spec_hash[:16]}"
    needs = required_tolerance(tmp_path)
    assert needs == {key: 0.6306}
    form = {"monthly_amount": "10000", "salary_day": "5", "strategy_key": key, "max_drawdown_tolerance": "63"}
    with pytest.raises(InvestmentPlanError, match="最深回撤是 63.1%"):
        parse_plan_form(form, strategy_catalog(tmp_path), needs)
    assert parse_plan_form({**form, "max_drawdown_tolerance": "64"}, strategy_catalog(tmp_path), needs)
    assert parse_plan_form({**form, "strategy_key": "benchmark_dca", "max_drawdown_tolerance": "30"},
                           strategy_catalog(tmp_path), needs)
    # Lowering the tolerance later flags the approved strategy; nothing is revoked automatically.
    row = pipeline(tmp_path, drawdown_tolerance=0.30).overview()["pipeline"][0]
    assert row["over_tolerance"] is True and row["can_revoke"] is True and row["stage"] == "approved"


def test_stale_statistics_and_variants_do_not_promote(tmp_path):
    record = development_setup(tmp_path)
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    registry.register(
        kind="candidate", period="development", spec_hash="another-rule", spec_name="另一個", input_hash="dev-2",
        data_fingerprint=record.data_fingerprint, metrics=GOOD_METRICS, report_file="dev.json",
    )
    registry.register(
        kind="candidate", period="development", spec_hash=SPEC.spec_hash, spec_name=SPEC.name, input_hash="dev-3",
        data_fingerprint=record.data_fingerprint, metrics={**GOOD_METRICS, "cost_scale": 2.0}, report_file="dev.json",
    )
    review = pipeline(tmp_path).development_review()
    # The saved statistics cover 1 rule, the registry now holds 2: recompute before anything moves.
    assert [row["record"].trial_id for row in review] == [1, 2]      # the cost variant is not a candidate
    assert all("統計檢定的設定數落後登錄檔，需重算" in row["reasons"] for row in review)
    assert pipeline(tmp_path).advance(MARKET) == []
