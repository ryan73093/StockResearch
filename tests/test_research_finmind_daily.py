"""2026-10-22: the government banks' and the branches' days, kept small, stored once."""
import gzip
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from quant_platform.research.history import finmind_daily


def test_the_banks_and_the_branches_are_summed_by_hand():
    banks = [{"stock_id": "2330", "buy": 1000, "sell": 400, "buy_amount": 2.4e6, "sell_amount": 9.6e5, "bank_name": "臺銀"},
             {"stock_id": "2330", "buy": 0, "sell": 600, "buy_amount": 0, "sell_amount": 1.44e6, "bank_name": "土銀"}]
    assert finmind_daily.govbank_rows(banks) == [["2330", 1000.0, 1000.0, 2.4e6, 2.4e6]]
    branches = [{"securities_trader_id": "1440", "price": 100.0, "buy": 2000, "sell": 0},
                {"securities_trader_id": "1440", "price": 101.0, "buy": 1000, "sell": 500},
                {"securities_trader_id": "9800", "price": 100.5, "buy": 0, "sell": 3000}]
    assert finmind_daily.branch_rows(branches) == [["1440", 3000.0, 500.0, 301000.0, 50500.0],
                                                   ["9800", 0.0, 3000.0, 0.0, 301500.0]]


def test_a_day_is_asked_once_and_kept(tmp_path):
    asked = []

    def get(token, params):
        asked.append(params)
        return [{"date": "2026-10-21", "stock_id": "2330", "securities_trader_id": "1440", "price": 10.0, "buy": 5, "sell": 0},
                {"date": "2026-10-20", "stock_id": "2330", "securities_trader_id": "1440", "price": 10.0, "buy": 9, "sell": 0}]

    tasks = [("2330", date(2026, 10, 21))]
    found = finmind_daily.download(tmp_path, "t", tasks, "branches", 3600, get=get, sleep=lambda seconds: None,
                                   now=lambda: datetime(2026, 10, 22, tzinfo=ZoneInfo("Asia/Taipei")))
    assert found == {"asked": 1, "skipped": 0, "errors": 0, "waiting": 0, "tasks": 1}
    stored = json.loads(gzip.decompress((tmp_path / "raw" / "finmind" / "branches" / "2330" / "2026-10-21.json.gz")
                                        .read_bytes()))
    assert stored["rows"] == [["1440", 5.0, 0.0, 50.0, 0.0]]            # only that day's rows
    again = finmind_daily.download(tmp_path, "t", tasks, "branches", 3600, get=get, sleep=lambda seconds: None)
    assert again["skipped"] == 1 and len(asked) == 1


def test_the_paid_tables_come_a_day_at_a_time_and_a_fresh_empty_day_is_asked_again(tmp_path):
    """2026-11-02: the four per-stock paid tables stopped on 10-21; from 10-22 a session a request for every stock
    (raw rows kept); a day of the last two sessions that comes back empty is not stored — FinMind may not have it yet
    and a stored day is never asked again."""
    from quant_platform.research.history import paid

    served = {"2026-10-22": [{"date": "2026-10-22", "stock_id": "2330", "trading_money": 30_000_000.0, "volume": 10000.0,
                              "price": 3000.0, "trade_type": "配對交易"},
                             {"date": "2026-10-22", "stock_id": "2330", "trading_money": 3_000_000.0, "volume": 1000.0,
                              "price": 3000.0, "trade_type": "逐筆交易"}],
              "2026-10-23": [], "2026-10-26": []}
    asked = []

    def get(token, params):
        asked.append(params)
        assert params["start_date"] == params["end_date"] and "data_id" not in params
        return served[params["start_date"]]

    days = [date(2026, 10, 22), date(2026, 10, 23), date(2026, 10, 26)]
    tasks = [("", day) for day in days]
    found = finmind_daily.download(tmp_path, "t", tasks, "TaiwanStockBlockTrade", 3600, get=get,
                                   sleep=lambda seconds: None, fresh=date(2026, 10, 26))
    assert found == {"asked": 2, "skipped": 0, "errors": 0, "waiting": 1, "tasks": 3}
    folder = tmp_path / "raw" / "finmind" / "TaiwanStockBlockTrade" / "days"
    assert sorted(path.name for path in folder.iterdir()) == ["2026-10-22.json.gz", "2026-10-23.json.gz"]
    # the per-stock history (through 10-21, and a stale 10-22 row the day's file replaces) and the days, one table
    stock = tmp_path / "raw" / "finmind" / "TaiwanStockBlockTrade" / "2330.json.gz"
    stock.write_bytes(gzip.compress(json.dumps({"data": [
        {"date": "2026-10-21", "stock_id": "2330", "trading_money": 1_000_000.0, "volume": 400.0},
        {"date": "2026-10-22", "stock_id": "2330", "trading_money": 30_000_000.0, "volume": 10000.0}]}).encode("utf-8")))
    paid.build(tmp_path, ("TaiwanStockBlockTrade",))
    import pandas as pd

    table = pd.read_parquet(tmp_path / "paid" / "TaiwanStockBlockTrade.parquet")
    assert table["volume"].tolist() == [400.0, 11000.0]                       # 10-22 counted once, both trades
    through = json.loads((tmp_path / "paid" / "through.json").read_text(encoding="utf-8"))
    assert through == {"TaiwanStockBlockTrade": "2026-10-23"}                 # the empty 10-23 is covered, 10-26 not yet
    again = finmind_daily.download(tmp_path, "t", tasks, "TaiwanStockBlockTrade", 3600, get=get,
                                   sleep=lambda seconds: None, fresh=date(2026, 10, 26))
    assert again["skipped"] == 2 and again["waiting"] == 1 and len(asked) == 4      # only 10-26 asked again


def test_adding_the_new_days_to_a_table_is_the_same_as_building_it_again(tmp_path):
    """2026-11-02: paiddaily adds the days since the tables were built (the margin table alone is 7 million rows) —
    the same table as building it whole: a whole-market day replaces the stocks' rows of that day."""
    import pandas as pd

    from quant_platform.research.history import paid

    raw = tmp_path / "raw" / "finmind"
    (raw / "TaiwanStockBlockTrade" / "days").mkdir(parents=True)
    (raw / "govbank").mkdir(parents=True)

    def put(path, payload):
        path.write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))
    put(raw / "TaiwanStockBlockTrade" / "2330.json.gz", {"data": [
        {"date": "2026-10-20", "stock_id": "2330", "trading_money": 5.0, "volume": 1.0},
        {"date": "2026-10-21", "stock_id": "2330", "trading_money": 7.0, "volume": 2.0},
        {"date": "2026-10-21", "stock_id": "2330", "trading_money": 1.0, "volume": 1.0}]})
    put(raw / "govbank" / "2026-10-21.json.gz", {"date": "2026-10-21", "rows": [["2330", 1, 2, 3, 4]]})
    paid.build(tmp_path)
    put(raw / "TaiwanStockBlockTrade" / "days" / "2026-10-22.json.gz", {"data": [
        {"date": "2026-10-22", "stock_id": "2317", "trading_money": 9.0, "volume": 3.0},
        {"date": "2026-10-22", "stock_id": "2317", "trading_money": 1.0, "volume": 1.0}]})
    put(raw / "TaiwanStockBlockTrade" / "days" / "2026-10-23.json.gz", {"data": []})
    put(raw / "govbank" / "2026-10-22.json.gz", {"date": "2026-10-22", "rows": [["2317", 5, 6, 7, 8]]})
    paid.update(tmp_path)
    added = {name: pd.read_parquet(tmp_path / "paid" / f"{name}.parquet") for name in ("TaiwanStockBlockTrade", paid.GOVBANK)}
    through = json.loads((tmp_path / "paid" / "through.json").read_text(encoding="utf-8"))
    paid.build(tmp_path)
    for name, frame in added.items():
        whole = pd.read_parquet(tmp_path / "paid" / f"{name}.parquet")
        pd.testing.assert_frame_equal(frame.reset_index(drop=True), whole.reset_index(drop=True))
    assert added["TaiwanStockBlockTrade"]["volume"].tolist() == [1.0, 3.0, 4.0]
    assert through == json.loads((tmp_path / "paid" / "through.json").read_text(encoding="utf-8"))
    assert through["TaiwanStockBlockTrade"] == "2026-10-23" and through[paid.GOVBANK] == "2026-10-22"
