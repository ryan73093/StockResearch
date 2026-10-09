"""2026-10-09 (使用者：每一年的績效；前向觀察要看買了哪些股票、做了哪些操作)."""

import json

import pytest

from quant_platform.research.pool import yearly_matrix, yearly_returns
from quant_platform.research.stock_forward import StockForwardTracker


def test_yearly_returns_by_hand():
    curve = {
        "2020-01": [110.0, 105.0, 100.0],     # 100 put in at the start: +10% and +5%
        "2020-02": [131.8, 115.0, 110.0],     # 10 more: (131.8 − 110 − 10) ÷ (110 + 0.8 × 10) = +10%; 0050 flat
        "2021-01": [145.0, 126.5, 110.0],     # no new money: 145 ÷ 131.8 and 126.5 ÷ 115
    }
    years = yearly_returns(curve)
    assert years["2020"]["strategy"] == pytest.approx(1.1 * 1.1 - 1, abs=1e-4)
    assert years["2020"]["benchmark"] == pytest.approx(0.05, abs=1e-4)
    assert years["2020"]["months"] == 2
    assert years["2021"]["strategy"] == pytest.approx(145 / 131.8 - 1, abs=1e-4)
    assert years["2021"]["benchmark"] == pytest.approx(0.10, abs=1e-4)
    assert yearly_returns({}) == {}


def test_the_matrix_reads_each_rules_report(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    curve = {"2020-01": [110.0, 105.0, 100.0], "2020-02": [121.0, 105.0, 100.0]}
    (reports / "a.json").write_text(json.dumps({"curve": curve}), encoding="utf-8")
    rows = [{"name": "A", "spec_hash": "a" * 64, "tier": "T1", "report_file": "a.json"},
            {"name": "沒有報告", "spec_hash": "b" * 64, "tier": "T1", "report_file": "missing.json"}]
    matrix = yearly_matrix(tmp_path, rows)
    assert matrix["years"] == ["2020"] and [row["name"] for row in matrix["rows"]] == ["A"]
    assert matrix["benchmark"]["2020"] == pytest.approx(0.05) and matrix["months"]["2020"] == 2
    assert matrix["rows"][0]["years"]["2020"]["strategy"] == pytest.approx(0.21, abs=1e-4)


def record(day, trades, holdings, cash, contributed, value, benchmark):
    return {"date": day, "rule_hash": "r" * 64, "name": "規則", "since": "2026-10-08", "value": value, "cash": cash,
            "contributed": contributed, "benchmark_value": benchmark, "excess": (value - benchmark) / contributed,
            "holdings": holdings, "trades_today": trades, "adjustments_today": [], "trades_total": 0,
            "fees_total": 20, "taxes_total": 3}


def forward_folder(tmp_path):
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    (folder / "tracked.json").write_text(json.dumps([{"rule_hash": "r" * 64, "name": "規則", "since": "2026-10-08"}]),
                                         encoding="utf-8")
    buy = {"code": "2330", "name": "台積電", "side": "BUY", "shares": 10, "price": 1000.0, "fee": 10, "tax": 0}
    buy2 = {"code": "2317", "name": "鴻海", "side": "BUY", "shares": 20, "price": 200.0, "fee": 10, "tax": 0}
    sell = {"code": "2317", "name": "鴻海", "side": "SELL", "shares": 20, "price": 210.0, "fee": 10, "tax": 3}
    first = record("2026-10-08", [buy, buy2], [{"code": "2330", "name": "台積電", "units": 10, "close": 1000.0, "value": 10_000.0},
                                              {"code": "2317", "name": "鴻海", "units": 20, "close": 200.0, "value": 4_000.0}],
                   980.0, 15_000.0, 14_980.0, 15_000.0)
    stale = dict(first, value=1.0)              # the same day recorded twice: the later one counts
    second = record("2026-10-09", [sell], [{"code": "2330", "name": "台積電", "units": 10, "close": 1100.0, "value": 11_000.0}],
                    5_167.0, 15_000.0, 16_167.0, 15_100.0)
    lines = [stale, first, second]
    (folder / "log.jsonl").write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n", encoding="utf-8")
    return tmp_path


def test_the_forward_detail_lists_holdings_and_every_trade(tmp_path):
    detail = StockForwardTracker(forward_folder(tmp_path)).detail("r" * 64)
    assert detail["sessions"] == 2 and detail["latest"]["date"] == "2026-10-09"
    assert [(trade["date"], trade["side"], trade["code"]) for trade in detail["trades"]] == [
        ("2026-10-09", "SELL", "2317"), ("2026-10-08", "BUY", "2330"), ("2026-10-08", "BUY", "2317")]
    assert detail["trades"][0]["amount"] == pytest.approx(4_200.0) and (detail["buys"], detail["sells"]) == (2, 1)
    assert detail["holdings"][0]["code"] == "2330" and detail["holdings"][0]["weight"] == pytest.approx(11_000 / 16_167)
    assert [row["value"] for row in detail["series"]] == [14_980.0, 16_167.0]
    assert detail["problems"] == []
    assert StockForwardTracker(tmp_path).detail("x" * 64) is None


def test_the_forward_page(tmp_path):
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    forward_folder(tmp_path / "research")
    client = create_app(build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))).test_client()
    body = client.get("/research/forward/" + "r" * 64).get_data(as_text=True)
    assert "每一筆交易" in body and "買 2・賣 1" in body and "2317 鴻海" in body and "台積電" in body
    assert client.get("/research/forward/" + "x" * 64).status_code == 404
