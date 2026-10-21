"""2026-10-22 (使用者 2026-10-20：我也要這個——盤中資金流向): the session's money by side, by hand."""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from quant_platform.research import live_flow

TAIPEI = ZoneInfo("Asia/Taipei")


def snap(code, amount, side, change=1.0):
    return {"stock_id": code, "total_amount": amount, "TickType": side, "close": 100.0, "change_rate": change,
            "date": "2026-10-22 09:00:15.000000"}


def test_the_money_since_the_last_snapshot_goes_to_the_last_trades_side():
    flow = live_flow.Flow()
    flow.update([snap("2330", 1000.0, 1), snap("0050", 99.0, 1), snap("2317", 500.0, 2, -1.0)])
    assert flow.stocks["2330"] == {"amount": 1000.0, "outer": 0.0, "inner": 0.0, "unknown": 1000.0, "close": 100.0,
                                   "change": 1.0, "at": "09:00:15"}            # before the first: unknown side
    assert "0050" not in flow.stocks                                             # ETFs left out
    flow.update([snap("2330", 1600.0, 1), snap("2317", 800.0, 2, -1.0)])
    flow.update([snap("2330", 1700.0, 2), snap("2317", 800.0, 1, -1.0)])        # no new money: nothing counted
    assert (flow.stocks["2330"]["outer"], flow.stocks["2330"]["inner"]) == (600.0, 100.0)
    assert (flow.stocks["2317"]["outer"], flow.stocks["2317"]["inner"]) == (0.0, 300.0)
    groups = {"2330": "半導體業", "2317": "其他電子業"}
    first, second = flow.industries(groups)
    assert first["industry"] == "半導體業" and first["net"] == 500.0 and first["bought"] == pytest.approx(600 / 700)
    assert first["share"] == pytest.approx(1700 / 2500) and second["net"] == -300.0 and second["down"] == 1
    assert first["leader"] == "2330"


def test_a_session_runs_to_the_close_and_compares_with_the_one_before(tmp_path):
    research = tmp_path / "research"
    old = live_flow.folder(research, date(2026, 10, 21))
    old.mkdir(parents=True)
    (old / "minutes.jsonl").write_text(json.dumps({"time": "09:00:00", "industries": [
        {"industry": "半導體業", "amount": 1.0, "share": 0.5, "net": 0.0}]}) + "\n", encoding="utf-8")
    moments = iter([datetime(2026, 10, 22, 8, 59, 0, tzinfo=TAIPEI)]                     # the day, read once
                   + [datetime(2026, 10, 22, 9, 0, 0, tzinfo=TAIPEI) + timedelta(seconds=15 * n) for n in range(3)]
                   + [datetime(2026, 10, 22, 13, 31, tzinfo=TAIPEI)] * 3)
    answers = iter([[snap("2330", 100.0, 1)], [snap("2330", 300.0, 1)], [snap("2330", 400.0, 2)]])
    history = tmp_path / "history"
    history.mkdir()
    (history / "raw" / "finmind").mkdir(parents=True)
    (history / "raw" / "finmind" / "TaiwanStockInfo.json").write_text(json.dumps({"data": [
        {"stock_id": "2330", "industry_category": "半導體業", "date": "2026-10-01"}]}), encoding="utf-8")
    found = live_flow.run(history, research, "t", now=lambda: next(moments), get=lambda token: next(answers),
                          sleep=lambda seconds: None)
    assert found == {"snapshots": 3, "errors": 0, "stocks": 1}
    page = live_flow.latest(research)
    entry = page["industries"][0]
    assert (entry["outer"], entry["inner"], entry["share"]) == (200.0, 100.0, 1.0)
    assert entry["share_change"] == pytest.approx(0.5) and "state" not in page
    lines = (live_flow.folder(research, date(2026, 10, 22)) / "minutes.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1                                                       # one row in the first 5 minutes


def test_the_page_shows_the_industries_and_stocks(tmp_path):
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'live.db'}"))
    client = create_app(container).test_client()
    assert "還沒有盤中資料" in client.get("/market/live").get_data(as_text=True)
    flow = live_flow.Flow()
    flow.update([snap("2330", 1e9, 1), snap("2317", 5e8, 2, -1.0)])
    flow.update([snap("2330", 3e9, 1), snap("2317", 9e8, 2, -1.0)])
    live_flow.save(tmp_path / "research", date(2026, 10, 22), flow, {"2330": "半導體業", "2317": "其他電子業"},
                   datetime(2026, 10, 22, 9, 30, tzinfo=TAIPEI), minute_row=True)
    body = client.get("/market/live").get_data(as_text=True)
    assert "半導體業" in body and "20.0 億" in body and "主動買超個股" in body and "欄位說明" in body
    assert client.get("/market/live.json").get_json()["industries"][0]["industry"] == "半導體業"


def test_one_industry_named_two_ways_is_one_row():
    flow = live_flow.Flow()
    flow.update([snap("2882", 100.0, 1), snap("5864", 50.0, 1)])
    assert [entry["industry"] for entry in flow.industries({"2882": "金融保險", "5864": "金融業"})] == ["金融保險"]
