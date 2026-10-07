"""R4 (2026-10-07): the daily rules' stock orders against the after-hours odd-lot auction."""

import json
from datetime import date

import pytest

from quant_platform.research import execution


def auction(price, shares=5_000):
    return {"price": price, "shares": shares, "trades": 10, "bid": None, "bid_shares": 0, "ask": None, "ask_shares": 0}


def test_orders_judged_by_hand():
    # close 100: today's limit is 101 for a buy (tick 0.5) and 99 for a sell (tick 0.1 below 100)
    buy = execution.judge("BUY", 300, 100.0, 99.0, auction(100.5))
    assert buy["limit"] == 101.0 and buy["outcome"] == "filled" and buy["cost_bps"] == pytest.approx(50.0)
    assert buy["odd"] == 300 and buy["size_share"] == pytest.approx(300 / 5_000)
    sell = execution.judge("SELL", 1_200, 100.0, 99.0, auction(99.5))
    assert sell["limit"] == 99.0 and sell["outcome"] == "filled" and sell["cost_bps"] == pytest.approx(50.0)
    assert sell["odd"] == 200                                   # the whole lot would go to the fixed-price session
    assert execution.judge("BUY", 300, 100.0, 99.0, auction(101.5))["outcome"] == "not_filled"
    assert execution.judge("BUY", 300, 100.0, 99.0, None)["outcome"] == "no_trade"
    # a limit-up close: the buy limit cannot pass the close, and the auction clears at it
    locked = execution.judge("BUY", 300, 110.0, 100.0, auction(110.0))
    assert locked["locked"] and locked["limit"] == 110.0 and locked["outcome"] == "at_limit"


def test_the_report_skips_0050_tpex_and_unsampled_days():
    sampled, other = date(2021, 3, 3), date(2021, 3, 4)
    closes = {"2330.TW": {date(2021, 3, 2): 99.0, sampled: 100.0, other: 100.0},
              "1101.TW": {date(2021, 3, 2): 50.0, sampled: 50.0}}
    auctions = {sampled: {"2330": auction(100.5), "1101": auction(50.2)}}
    ledger = [
        {"day": sampled, "symbol": "0050.TW", "side": "BUY", "shares": 100, "price": 130.0},
        {"day": sampled, "symbol": "6488.TWO", "side": "BUY", "shares": 10, "price": 500.0},
        {"day": other, "symbol": "2330.TW", "side": "SELL", "shares": 100, "price": 99.8},
        {"day": sampled, "symbol": "2330.TW", "side": "BUY", "shares": 100, "price": 100.2},
        {"day": sampled, "symbol": "1101.TW", "side": "BUY", "shares": 100, "price": 50.1},
        {"day": sampled, "symbol": "1101.TW", "side": "ADJUST", "factor": 1.1},
    ]
    report = execution.check_orders(ledger, closes, auctions, date(2020, 10, 26))
    assert report["all"]["orders"] == 2 and report["tpex_orders"] == 1 and report["all"]["filled"] == 1.0
    costs = report["all"]["cost_bps"]
    assert costs["mean"] == pytest.approx((50 + 40) / 2)          # 2330 +0.5%, 1101 +0.4%
    # traded amount: the 6488, 2330 (both days) and 1101 orders; not 0050
    amount = 10 * 500.0 + 100 * 99.8 + 100 * 100.2 + 100 * 50.1
    assert report["traded_amount"] == round(amount)
    assert report["extra_cost"] == round(amount * (45 - 20) / 10_000)
    assert report["since_2020_10"]["orders"] == 2 and report["before_2020_10"] == {"orders": 0}


def test_cached_auctions_keep_every_security(tmp_path):
    folder = tmp_path / "raw" / "twse_odd_lot" / "2021"
    folder.mkdir(parents=True)
    payload = {"stat": "OK", "data": [
        ["2330", "台積電", "12,345", "300", "7,400,000", "600.00", "599.00", "1,200", "600.00", "3,400"],
        ["0050", "元大台灣50", "25,475", "422", "3,380,377", "132.70", "132.65", "67,553", "132.70", "45,552"]]}
    (folder / "20210303.json").write_text(json.dumps(payload), encoding="utf-8")
    (folder / "20140101.json").write_text(json.dumps(payload), encoding="utf-8")       # before ``since``
    auctions = execution.load_auctions(tmp_path, since=date(2015, 6, 1))
    assert list(auctions) == [date(2021, 3, 3)]
    row = auctions[date(2021, 3, 3)]["2330"]
    assert row["shares"] == 12_345 and row["price"] == 600.0 and row["bid_shares"] == 1_200 and row["ask_shares"] == 3_400
