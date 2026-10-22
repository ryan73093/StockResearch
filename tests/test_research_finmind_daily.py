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
    assert found == {"asked": 1, "skipped": 0, "errors": 0, "tasks": 1}
    stored = json.loads(gzip.decompress((tmp_path / "raw" / "finmind" / "branches" / "2330" / "2026-10-21.json.gz")
                                        .read_bytes()))
    assert stored["rows"] == [["1440", 5.0, 0.0, 50.0, 0.0]]            # only that day's rows
    again = finmind_daily.download(tmp_path, "t", tasks, "branches", 3600, get=get, sleep=lambda seconds: None)
    assert again["skipped"] == 1 and len(asked) == 1
