import json
from datetime import date

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.research.pool import pool_view, rule_rows
from quant_platform.research.registry import TrialRegistry

GOOD = {"windows": {"3y": {"count": 100, "win_ratio": 0.7, "median_excess": 0.01, "worst_excess": -0.02},
                    "5y": {"count": 80, "win_ratio": 0.65, "median_excess": 0.012, "worst_excess": -0.01}},
        "max_drawdown": -0.50, "benchmark_max_drawdown": -0.52, "full_period_excess": 0.2, "xirr": 0.09, "benchmark_xirr": 0.07}
BAD = {**GOOD, "windows": {"3y": {"count": 100, "win_ratio": 0.3, "median_excess": -0.02, "worst_excess": -0.1},
                           "5y": {"count": 80, "win_ratio": 0.2, "median_excess": -0.03, "worst_excess": -0.2}},
       "full_period_excess": -0.3}


def seed(research):
    registry = TrialRegistry(research / "trials.jsonl")
    add = lambda **kw: registry.register(kind="candidate", report_file="r.json", **kw)  # noqa: E731
    add(period="development", spec_hash="etf-win", spec_name="時點：每月 26 日", input_hash="1", data_fingerprint="etf", metrics=GOOD)
    add(period="development", spec_hash="etf-lose", spec_name="均線 60 日", input_hash="2", data_fingerprint="etf", metrics=BAD)
    add(period="development", spec_hash="stock-both", spec_name="個股 52 週高點", input_hash="3", data_fingerprint="stocks:a",
        metrics={**GOOD, "plan": {"kind": "ContributionPlan"}})
    add(period="validation", spec_hash="stock-both", spec_name="個股 52 週高點", input_hash="4", data_fingerprint="stocks:v",
        metrics={**GOOD, "plan": {"kind": "ContributionPlan"}})
    add(period="development", spec_hash="stock-fail", spec_name="個股 動能", input_hash="5", data_fingerprint="stocks:a",
        metrics={**GOOD, "plan": {"kind": "ContributionPlan"}})
    add(period="validation", spec_hash="stock-fail", spec_name="個股 動能", input_hash="6", data_fingerprint="stocks:v",
        metrics={**BAD, "plan": {"kind": "ContributionPlan"}})
    add(period="development", spec_hash="stock-lump", spec_name="個股 一次投入", input_hash="7", data_fingerprint="stocks:a",
        metrics={**GOOD, "plan": {"kind": "LumpSumPlan"}})           # an illustration, not a trial of the standard plan
    return registry


def test_pool_classifies_every_rule(tmp_path):
    seed(tmp_path)
    rows = {row["spec_hash"]: row for row in rule_rows(tmp_path)}

    assert rows["etf-win"]["status"] == "window_ok" and rows["etf-win"]["family"] == "ETF 規則"
    assert rows["etf-lose"]["status"] == "eliminated" and "3 年勝率 30% < 60%" in rows["etf-lose"]["development"]["reasons"]
    assert rows["stock-both"]["status"] == "validation_passed" and rows["stock-both"]["validation"]["excess"] == 0.2
    assert rows["stock-fail"]["status"] == "validation_failed" and rows["stock-fail"]["validation"]["reasons"]
    assert "stock-lump" not in rows
    view = pool_view(tmp_path)
    assert view["total"] == 4 and view["attempts"] == 5 and view["families"] == {"ETF 規則": 2, "個股規則": 2}
    assert [row["spec_hash"] for row in view["both_periods"]] == ["stock-both"]
    assert view["counts"]["validation_failed"] == 1 and view["counts"]["eliminated"] == 1
    assert view["high_win"][0]["development"]["win_3y"] == 0.7


def test_pool_page_and_json(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    seed(tmp_path / "research")
    client = create_app(container).test_client()
    body = client.get("/research/pool").get_data(as_text=True)
    assert "研究選手池" in body and "個股 52 週高點" in body and "驗證期未通過" in body and "兩段獨立期間都贏" in body
    payload = client.get("/research/pool.json").get_json()
    assert payload["total"] == 4 and payload["status_labels"]["eliminated"] == "開發期淘汰"
    assert client.get("/system/docs/research_method").status_code in (200, 404)
