"""2026-10-21 (使用者付了 FinMind Sponsor：請繼續研究): the paid tables' factors by hand, each day's table used from the
next session; the tables built from FinMind's files; their digest in a rule's input."""
import gzip
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_platform.research.chips import ChipStore
from quant_platform.research.history import paid


def weekdays(start, count):
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


@pytest.fixture
def store(tmp_path):
    sessions = weekdays(date(2024, 1, 1), 30)
    (tmp_path / "paid").mkdir()
    (tmp_path / "chips").mkdir()
    pd.DataFrame({"date": sessions, "code": "1101", "NumberOfSharesIssued": 1_000_000.0}).to_parquet(
        tmp_path / "chips" / "TaiwanStockShareholding.parquet", index=False)
    pd.DataFrame({"date": sessions, "code": "1101", "margin_cost": 50.0, "margin_maintenance": 160.0,
                  "margin_balance": 10.0}).to_parquet(tmp_path / "paid" / "TaiwanStockMarginMaintenance.parquet", index=False)
    pd.DataFrame({"date": sessions, "code": "1101", "UnrestrictedLoanCurrentDayBalance": [float(n) for n in range(30)]}
                 ).to_parquet(tmp_path / "paid" / "TaiwanStockLoanCollateralBalance.parquet", index=False)
    # one block trade on the third session: 1,000 shares at 44 against a close of 40
    pd.DataFrame({"date": [sessions[2]], "code": ["1101"], "volume": [1000.0], "trading_money": [44_000.0]}).to_parquet(
        tmp_path / "paid" / "TaiwanStockBlockTrade.parquet", index=False)
    pd.DataFrame({"date": sessions, "code": "1101", "top_buy_volume": 300.0, "top_sell_volume": 100.0}).to_parquet(
        tmp_path / "paid" / "TaiwanStockBrokerDailyConcentration.parquet", index=False)
    close = np.full((2, len(sessions)), 40.0)
    return ChipStore(tmp_path, sessions, ["1101.TW", "9999.TW"], close), sessions


def test_the_paid_factors_by_hand(store):
    chips, sessions = store
    assert chips.paid_available("margin_safe")
    safe = chips.paid_matrix("margin_safe")
    assert np.isnan(safe[0, 0]) and safe[0, 1] == pytest.approx(160.0) and np.isnan(safe[1]).all()   # next session
    assert chips.paid_matrix("margin_in_profit")[0, 1] == pytest.approx(40 / 50 - 1)
    # the balance grows a lot a day: 20 lots in 20 sessions against 1,000,000 shares, reversed
    assert chips.paid_matrix("low_pledge_loan_20")[0, 21] == pytest.approx(-20 * 1000 / 1_000_000)
    premium = chips.paid_matrix("block_premium_20")
    volume = chips.paid_matrix("block_volume_20")
    # the table starts on the third session (used from the fourth): 20 sessions known from the 23rd on
    assert np.isnan(premium[0, 21]) and premium[0, 22] == pytest.approx(44_000 / 40_000 - 1)
    assert volume[0, 22] == pytest.approx(1000 / 1_000_000) and volume[0, 23] == pytest.approx(0.0)  # out of the window
    assert chips.paid_matrix("broker_concentration_5")[0, 5] == pytest.approx((300 - 100) / 400)


def test_the_tables_are_built_from_finmind_files_and_digested(tmp_path):
    source = tmp_path / "raw" / "finmind" / "TaiwanStockBlockTrade"
    source.mkdir(parents=True)
    data = [{"date": "2026-09-01", "stock_id": "2330", "trade_type": "配對交易", "price": 2433.81, "volume": 10000,
             "trading_money": 24338100},
            {"date": "2026-09-01", "stock_id": "2330", "trade_type": "逐筆交易", "price": 2440.0, "volume": 1000,
             "trading_money": 2440000}]
    (source / "2330.json.gz").write_bytes(gzip.compress(json.dumps({"data": data}).encode("utf-8")))
    assert paid.build(tmp_path, ("TaiwanStockBlockTrade",)) == {"TaiwanStockBlockTrade": 1}
    table = pd.read_parquet(tmp_path / "paid" / "TaiwanStockBlockTrade.parquet")
    assert table["volume"].tolist() == [11000.0] and table["trading_money"].tolist() == [26778100.0]
    assert paid.digest(tmp_path, date(2026, 8, 31), ["TaiwanStockBlockTrade"]) != paid.digest(
        tmp_path, date(2026, 9, 1), ["TaiwanStockBlockTrade"])


def test_only_what_was_public_by_the_close_is_read(store):
    chips, sessions = store
    chips.until = sessions[3]                                    # tables dated before it: the first three sessions'
    safe = chips.paid_matrix("margin_safe")
    assert safe[0, 3] == pytest.approx(160.0) and safe[0, 4] == pytest.approx(160.0)       # carried, not newer rows
    assert np.isnan(safe[0, 15])                                                           # carried at most 10 sessions


def test_the_new_data_rules_pair_the_strong_new_factors_with_the_strong_old_ones():
    # 2026-10-21 (research_method §7, written before the strength results)
    from quant_platform.research import daily

    def item(verdict, before_t=0.0, regime=None, since=None):
        return {"verdict": verdict, "periods": {"before_regime": {"t": before_t}, "regime": {"top_excess_year": regime},
                                                "since_2024": {"top_excess_year": since}}}

    report = {"factors": {"big_holder_up_4w": item("強"), "margin_safe": item("反向"), "retail_down_4w": item("弱"),
                          "broker_concentration_5": item("弱", regime=0.05, since=0.03),        # short: 2020-10, 2024
                          "foreign_buy_20": item("強", 3.0), "trust_buy_20": item("強", 5.0), "rsi_14": item("弱", 9.0),
                          "revenue_accel": item("強", 9.0), "high_52w": item("強", 1.0)}}
    signs, partners = daily.new_data_signs(report)
    assert signs == {"big_holder_up_4w": 1.0, "margin_safe": -1.0, "broker_concentration_5": 1.0}
    # no monthly revenue (lagging, the owner's rule) and one a family: foreign buying is the same family as the trusts'
    assert partners == ["trust_buy_20", "high_52w"]


def test_the_holder_filter_vetoes_the_fifth_whose_chips_disperse_most():
    # 2026-10-22 (集保因子第三版): exclude "h1"/"h2" — no new buys of the lowest fifth of the factor that day
    from types import SimpleNamespace

    from quant_platform.research import daily

    values = np.arange(100, dtype=float)[:, None].repeat(2, axis=1)
    values[0, 1] = np.nan
    panel = SimpleNamespace(matrix=lambda factor: values, symbols=[f"{1000 + n}.TW" for n in range(100)])
    vetoed = daily.FactorPanel.holder_vetoed(panel, 0, "retail_down_4w")
    assert vetoed == {f"{1000 + n}.TW" for n in range(20)}            # values 0..19.8: the lowest fifth
    few = SimpleNamespace(matrix=lambda factor: values[:40], symbols=panel.symbols[:40])
    assert daily.FactorPanel.holder_vetoed(few, 0, "retail_down_4w") == set()        # under 50 known: none
    rule = daily.DailyRule(name="x", factors={"ml_deep2_top": 1.0}, exclude="h1")
    assert rule.canonical()["exclude"] == "h1" and "exclude" not in daily.DailyRule(name="x", factors={"ml_deep2_top": 1.0}).canonical()
