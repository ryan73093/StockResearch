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
    rows = {row["spec_hash"]: row for row in rule_rows(tmp_path, "dca")}

    assert rows["etf-win"]["status"] == "window_ok" and rows["etf-win"]["family"] == "ETF 規則"
    assert rows["etf-lose"]["status"] == "eliminated" and "3 年勝率 30% < 60%" in rows["etf-lose"]["development"]["reasons"]
    assert rows["stock-both"]["status"] == "validation_passed" and rows["stock-both"]["validation"]["excess"] == 0.2
    assert rows["stock-fail"]["status"] == "validation_failed" and rows["stock-fail"]["validation"]["reasons"]
    assert "stock-lump" not in rows
    view = pool_view(tmp_path, basis="dca")
    assert view["total"] == 4 and view["attempts"] == 5 and view["families"] == {"ETF 規則": 2, "個股規則": 2}
    assert [row["spec_hash"] for row in view["both_periods"]] == ["stock-both"]
    assert view["counts"]["validation_failed"] == 1 and view["counts"]["eliminated"] == 1
    assert view["high_win"][0]["development"]["win_3y"] == 0.7


def test_pool_page_and_json(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    seed(tmp_path / "research")
    client = create_app(container).test_client()
    body = client.get("/research/pool?basis=dca").get_data(as_text=True)
    assert "研究選手池" in body and "個股 52 週高點" in body and "驗證期未通過" in body and "兩段獨立期間都贏" in body
    payload = client.get("/research/pool.json?basis=dca").get_json()
    assert payload["total"] == 4 and payload["status_labels"]["eliminated"] == "開發期淘汰"
    assert client.get("/system/docs/research_method").status_code in (200, 404)


def test_pool_and_research_page_show_costs_and_forward_tracking(tmp_path):
    from quant_platform.research.stock_rules import StockRule

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    research = tmp_path / "research"
    registry = seed(research)
    # a run from before cost shares were stored: 2005-02 to 2016-12 is 143 contributions of NT$10,000
    registry.register(kind="candidate", report_file="r.json", period="development", spec_hash="stock-old",
                      spec_name="個股 舊紀錄", input_hash="8", data_fingerprint="stocks:a",
                      metrics={**GOOD, "plan": {"kind": "ContributionPlan", "monthly_amount": 10_000},
                               "start": "2005-02-07", "end": "2016-12-30", "costs": 143_000})
    folder = research / "forward" / "stocks"
    folder.mkdir(parents=True)
    rule = StockRule(name="個股 52 週高點", factor="high_52w", top=30, buffer=3)
    (folder / "tracked.json").write_text(json.dumps([{
        "rule_hash": "stock-both", "name": rule.name, "rule": rule.model_dump(mode="json"), "since": "2026-10-05",
        "reason": "兩段期間都贏"}], ensure_ascii=False), encoding="utf-8")
    (folder / "log.jsonl").write_text(json.dumps({
        "date": "2026-10-05", "rule_hash": "stock-both", "name": rule.name, "since": "2026-10-05", "value": 9_990.0,
        "cash": 90.0, "contributed": 10_000.0, "benchmark_value": 9_995.0, "excess": -0.0005,
        "holdings": [{"code": "2330", "name": "台積電", "units": 9.0, "close": 1_100.0, "value": 9_900.0}],
        "trades_today": [{"code": "2330", "name": "台積電", "side": "BUY", "shares": 9, "price": 1_100.0, "fee": 14, "tax": 0}],
        "adjustments_today": [], "trades_total": 1, "fees_total": 14, "taxes_total": 0,
        "replay_check": {"days": 0, "mismatches": []}, "late": False}, ensure_ascii=False) + "\n", encoding="utf-8")

    rows = {row["spec_hash"]: row for row in rule_rows(research, "dca")}
    assert rows["stock-old"]["development"]["cost_share"] == 0.1
    assert rows["stock-both"]["forward_since"] == "2026-10-05" and rows["etf-win"]["forward_since"] is None
    client = create_app(container).test_client()
    page = client.get("/research").get_data(as_text=True)
    assert "個股規則前向模擬" in page and "台積電" in page and "正常" in page
    pool = client.get("/research/pool?basis=dca").get_data(as_text=True)
    assert "前向觀察中的個股規則" in pool and "費稅佔投入" in pool and "前向觀察 10-05 起" in pool
    payload = client.get("/research/pool.json?basis=dca").get_json()
    assert payload["forward_stocks"][0]["sessions"] == 1 and payload["forward_stocks"][0]["problems"] == []


def test_final_validation_result_shows_on_pool_and_forward_rows(tmp_path):
    from quant_platform.research.stock_forward import StockForwardTracker
    from quant_platform.research.stock_rules import StockRule

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    research = tmp_path / "research"
    registry = seed(research)
    registry.register(kind="candidate", report_file="r.json", period="holdout", spec_hash="stock-both",
                      spec_name="個股 52 週高點", input_hash="9", data_fingerprint="stocks:h",
                      metrics={**BAD, "full_period_excess": -0.71, "plan": {"kind": "ContributionPlan"}})
    folder = research / "forward" / "stocks"
    folder.mkdir(parents=True)
    rule = StockRule(name="個股 52 週高點", factor="high_52w", top=30, buffer=3)
    (folder / "tracked.json").write_text(json.dumps([{"rule_hash": "stock-both", "name": rule.name,
                                                       "rule": rule.model_dump(mode="json"), "since": "2026-10-03"}],
                                                     ensure_ascii=False), encoding="utf-8")
    rows = {row["spec_hash"]: row for row in rule_rows(research, "dca")}
    assert rows["stock-both"]["status"] == "holdout_failed" and rows["stock-both"]["holdout"]["excess"] == -0.71
    final = StockForwardTracker(research).summary()[0]["final"]
    assert final["passed"] is False and final["excess"] == -0.71
    client = create_app(container).test_client()
    assert "最終驗證未通過 -71%" in client.get("/research").get_data(as_text=True)
    assert "最終驗證比 0050 -71%" in client.get("/research/pool?basis=dca").get_data(as_text=True)


def test_pool_defaults_to_the_lump_sum_account(tmp_path):
    registry = seed(tmp_path)
    lump = {**GOOD, "plan": {"kind": "LumpSumPlan", "monthly_amount": 300_000}, "engine": "stocks-1.2.0",
            "final_value": 1_210_000.0, "benchmark_final_value": 679_000.0}
    registry.register(kind="candidate", report_file="r.json", period="development", spec_hash="stock-both",
                      spec_name="個股 52 週高點", input_hash="10", data_fingerprint="stocks:a", metrics=lump)
    rows = {row["spec_hash"]: row for row in rule_rows(tmp_path)}
    # only lump-sum runs of engine 1.2.0 count: the older lump-sum run (monthly windows) is left out
    assert set(rows) == {"stock-both"} and rows["stock-both"]["development"]["final_value"] == 1_210_000.0
    assert rows["stock-both"]["status"] == "window_ok"
    view = pool_view(tmp_path)
    assert view["basis"] == "lump_sum" and "30 萬" in view["basis_label"]
