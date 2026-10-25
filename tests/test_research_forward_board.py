"""2026-10-13: the common-start forward board (research/forward_board.py)."""

import json

import pytest

from quant_platform.research import forward_board


def test_the_state_against_the_backtest_band_by_hand():
    expected = {"trial": 1, "mean": 0.01, "sd": 0.04}            # +1% a month, 4% monthly spread
    # two months: centre +2%, 90% band ±1.645 × 4% × √2 = ±9.3%, alert line 2% − 2.326 × 5.66% = −11.2%
    label, centre, low, high = forward_board.state(0.05, 2, expected)
    assert label == "符合預期" and centre == pytest.approx(0.02) and low == pytest.approx(-0.0731, abs=1e-4)
    assert forward_board.state(0.12, 2, expected)[0] == "強於預期"
    assert forward_board.state(-0.10, 2, expected)[0] == "弱於預期"
    assert forward_board.state(-0.13, 2, expected)[0] == "警示"
    assert forward_board.state(0.0, 2, None)[0] == "沒有回測可比"


def test_the_expectation_reads_the_rule_s_backtest(tmp_path):
    from quant_platform.research.registry import TrialRegistry

    research = tmp_path / "research"
    (research / "reports").mkdir(parents=True)
    months = {f"2020-{month:02d}": value for month, value in zip(range(1, 13), [0.02, -0.01] * 6)}
    (research / "reports" / "r.json").write_text(json.dumps({"monthly_active_returns": months}), encoding="utf-8")
    TrialRegistry(research / "trials.jsonl").register(kind="candidate", period="recent", spec_hash="abc", spec_name="x",
                                                      input_hash="i", data_fingerprint="d", metrics={}, report_file="r.json")
    found = forward_board.expectation(research, "abc")
    assert found["mean"] == pytest.approx(0.005) and found["sd"] > 0


def test_the_forward_tab_shows_the_board(tmp_path):
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    from datetime import datetime
    from zoneinfo import ZoneInfo

    from quant_platform.research.forward_board import common_start

    folder = tmp_path / "research" / "forward" / "stocks"
    folder.mkdir(parents=True)
    start = common_start(datetime.now(ZoneInfo("Asia/Taipei")).date()).isoformat()
    board = {"common_start": start, "date": "2026-10-12", "sessions": 8, "months": 0.38,
             "counts": {"警示": 0, "弱於預期": 0, "符合預期": 1, "強於預期": 0, "沒有回測可比": 0},
             "rows": [{"rule_hash": "h", "name": "T0 共識清單測試", "trial": 1, "since": "2026-10-12", "rank": 1,
                       "rank_change": None, "return": 0.03, "excess": 0.01, "band_low": -0.05, "band_high": 0.07,
                       "state": "符合預期", "advice": "照規則繼續。", "max_drawdown": -0.02, "buys": 20, "sells": 0}]}
    (folder / "board.json").write_text(json.dumps(board, ensure_ascii=False), encoding="utf-8")
    client = create_app(build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))).test_client()
    body = client.get("/research?tab=forward").get_data(as_text=True)
    assert "共同起跑線" in body and "T0 共識清單測試" in body and "在預期內" in body and "/research/rules/h" in body
    # 2026-10-17: last month's start line is not shown (the page says it is being rebuilt from this month's start)
    (folder / "board.json").write_text(json.dumps({**board, "common_start": "2020-01-01"}, ensure_ascii=False), encoding="utf-8")
    body = client.get("/research?tab=forward").get_data(as_text=True)
    assert "重算中" in body and "T0 共識清單測試" not in body and "2020-01-01 起的舊起跑線" in body


def test_the_recent_page_lists_every_order_and_both_periods(tmp_path):
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    folder = tmp_path / "research" / "forward" / "stocks" / "recent"
    folder.mkdir(parents=True)
    replay = {"rule_hash": "abcdef12", "name": "T0 共識清單測試", "start": "2026-07-13", "end": "2026-10-12",
              "research_end": "2026-09-30", "cash": 1200, "costs": 3456,
              "periods": {"all": {"return": 0.08, "benchmark_return": 0.05, "excess": 0.028, "max_drawdown": -0.06},
                          "research": {"return": 0.05, "benchmark_return": 0.03, "excess": 0.019, "max_drawdown": -0.06},
                          "unseen": {"return": 0.03, "benchmark_return": 0.02, "excess": 0.009, "max_drawdown": -0.02}},
              "curve": [{"date": "2026-07-13", "value": 300000, "benchmark": 300000, "contributed": 300000},
                        {"date": "2026-10-12", "value": 334000, "benchmark": 325000, "contributed": 330000}],
              "trades": [{"date": "2026-10-12", "code": "2330", "name": "台積電", "side": "BUY", "refused": "漲停買不到"},
                         {"date": "2026-10-12", "code": "2317", "name": "鴻海", "side": "SELL", "shares": 100, "price": 200.0,
                          "fee": 28, "tax": 60, "amount": 20000}],
              "holdings": [{"code": "2454", "name": "聯發科", "value": 15000}],
              "counts": {"buys": 0, "sells": 1, "refused": 1}}
    (folder / "abcdef12.json").write_text(json.dumps(replay, ensure_ascii=False), encoding="utf-8")
    client = create_app(build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))).test_client()
    body = client.get("/research/recent/abcdef12").get_data(as_text=True)
    assert "近 3 個月模擬操作" in body and "漲停買不到" in body and "鴻海" in body and "沒有任何資料挑過規則" in body
    assert client.get("/research/recent/zzzzzzzz").status_code == 404


def test_the_common_start_rolls_with_the_month():
    """2026-10-17 (使用者：前向觀察的起點照現在的月份往前推兩個月): the day after the month's cutoff."""
    from datetime import date

    from quant_platform.research.forward_board import common_start

    assert common_start(date(2026, 10, 17)) == date(2026, 9, 1)
    assert common_start(date(2026, 11, 3)) == date(2026, 10, 1)
    assert common_start(date(2027, 1, 6)) == date(2026, 12, 1)


def test_accounts_with_the_same_trades_are_one_row():
    """2026-10-25 (使用者：可悲 — four names, one set of picks): twins share a row, places run over distinct results."""
    from quant_platform.research.forward_board import group_twins

    def row(rank, name, value, sells=2, excess=0.17):
        return {"rule_hash": name, "name": name, "rank": rank, "value": value, "buys": 21, "sells": sells,
                "max_drawdown": -0.062, "excess": excess}

    board = group_twins({"rows": [row(1, "a", 371000.2), row(2, "b", 371000.4), row(3, "c", 370900.0),
                                  row(4, "d", 300000.0, excess=-0.01)]})
    first = board["rows"][0]
    assert [twin["name"] for twin in first["twins"]] == ["b"] and board["rows"][1]["twin_of"] == "a"
    assert first["place"] == 1 and board["rows"][2]["place"] == 2 and board["rows"][3]["place"] == 3
    assert board["distinct"] == 3 and board["ahead"] == 2
