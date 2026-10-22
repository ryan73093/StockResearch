"""2026-10-22 (使用者：還有什麼要加入分析的): the eight government banks (八大行庫) — the table from the per-day files, the
two stock factors by hand (each day's table used from the next session) and the market-wide net buying."""
import gzip
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_platform.research import market_signals
from quant_platform.research.chips import ChipStore
from quant_platform.research.history import paid


def weekdays(start, count):
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _day_file(base, day, rows):
    folder = base / "raw" / "finmind" / "govbank"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day.isoformat()}.json.gz").write_bytes(
        gzip.compress(json.dumps({"date": day.isoformat(), "code": "", "rows": rows}).encode("utf-8")))


def test_the_table_is_built_from_the_day_files(tmp_path):
    days = weekdays(date(2024, 1, 1), 2)
    _day_file(tmp_path, days[0], [["1101", 1000, 0, 40_000, 0], ["2330", 0, 500, 0, 300_000]])
    _day_file(tmp_path, days[1], [])
    assert paid.build(tmp_path, (paid.GOVBANK,)) == {paid.GOVBANK: 2}
    table = pd.read_parquet(tmp_path / "paid" / f"{paid.GOVBANK}.parquet")
    assert table["code"].tolist() == ["1101", "2330"] and table["sell_amount"].tolist() == [0.0, 300_000.0]


@pytest.fixture
def store(tmp_path):
    sessions = weekdays(date(2024, 1, 1), 30)
    (tmp_path / "paid").mkdir()
    (tmp_path / "chips").mkdir()
    pd.DataFrame({"date": sessions, "code": "1101", "NumberOfSharesIssued": 1_000_000.0}).to_parquet(
        tmp_path / "chips" / "TaiwanStockShareholding.parquet", index=False)
    # the banks buy 40,000 of 1101 every session from the second; they sell 10,000 on the fifth
    rows = [{"date": day, "code": "1101", "buy": 1000.0, "sell": 0.0, "buy_amount": 40_000.0, "sell_amount": 0.0}
            for day in sessions[1:]]
    rows[3]["sell_amount"] = 10_000.0
    pd.DataFrame(rows).to_parquet(tmp_path / "paid" / f"{paid.GOVBANK}.parquet", index=False)
    close = np.full((2, len(sessions)), 40.0)
    close[0, 1::2] = 39.0                                     # 1101 falls on every second session (odd ones)
    return ChipStore(tmp_path, sessions, ["1101.TW", "9999.TW"], close), sessions


def test_the_government_bank_factors_by_hand(store):
    chips, sessions = store
    assert chips.paid_available("govbank_net_20")
    net = chips.paid_matrix("govbank_net_20")
    # the table starts on the second session (used from the third, the first session before it counts as 0 only
    # once the data starts): 20 sessions known from the 22nd (index 21) on, against the cap at the table's day's close
    assert np.isnan(net[0, 20])
    worth = 1_000_000 * close_on(sessions, 20)
    assert net[0, 21] == pytest.approx((20 * 40_000 - 10_000) / worth)
    dip = chips.paid_matrix("govbank_dip_buy_20")
    # in the 20 table days 1..20 the stock fell on the odd ones (10 of them); the fifth table (index 4) is even
    assert dip[0, 21] == pytest.approx(10 * 40_000 / worth)
    assert np.isnan(net[1]).all()


def close_on(sessions, index):
    return 40.0 if index % 2 == 0 else 39.0


def test_the_market_wide_net_buying_against_turnover(tmp_path):
    days = weekdays(date(2024, 1, 1), 8)
    (tmp_path / "paid").mkdir()
    pd.DataFrame([{"date": day, "code": "1101", "buy": 0.0, "sell": 0.0, "buy_amount": 300.0, "sell_amount": 100.0}
                  for day in days[2:]]).to_parquet(tmp_path / "paid" / f"{paid.GOVBANK}.parquet", index=False)
    (tmp_path / "stocks" / "twse").mkdir(parents=True)
    pd.DataFrame([{"date": day, "code": code, "turnover": 1_000.0} for day in days for code in ("1101", "2330")]
                 ).to_parquet(tmp_path / "stocks" / "twse" / "2024.parquet", index=False)
    found = market_signals.govbank(tmp_path, 5)
    assert min(found) == days[6] and found[days[6]] == pytest.approx(5 * 200 / (5 * 2_000))
    assert market_signals.govbank(tmp_path / "none", 5) == {}


def test_a_government_bank_signal_is_judged_on_the_short_periods():
    days = weekdays(date(2021, 7, 1), 1400)
    index = {day: 100.0 * (1.0003 ** number) for number, day in enumerate(days)}
    signal = {day: float(number % 7) for number, day in enumerate(days)}
    found = market_signals.study(index, {f"{market_signals.GOVBANK}（5 日，佔全市場成交值）": signal})
    item = next(iter(found.values()))
    assert set(item["periods"]) == set(market_signals.SHORT_PERIODS) and item["useful"] is False


def test_a_short_history_factor_is_judged_on_two_periods_that_do_not_overlap():
    from quant_platform.research.factors import short_verdict

    item = lambda value: {"top_excess_year": value}
    assert short_verdict({"short_first": item(0.03), "since_2024": item(0.05)}) == "強（短歷史）"
    assert short_verdict({"short_first": item(-0.01), "since_2024": item(0.09), "regime": item(0.04)}) == "弱（短歷史）"
    assert short_verdict({"short_first": item(-0.03), "since_2024": item(0.05)}) == "不穩（短歷史）"
    assert short_verdict({"short_first": item(-0.03), "since_2024": item(-0.02)}) == "反向（短歷史）"
    assert short_verdict({"since_2024": item(0.05)}) == "弱（短歷史）"


def test_the_margin_veto_reads_the_margin_table_and_names_its_trait():
    """2026-10-22 (否決第二版): exclude "m1" — no new buys of the fifth nearest a margin call; its table is an input."""
    from quant_platform.research import daily
    from quant_platform.research.categories import classify

    rule = daily.DailyRule(name="x", factors={"ml_deep2_top": 1.0}, top=10, exclude="m1")
    assert daily.HOLDER_EXCLUDES["m1"] == "margin_safe" and daily.rule_inputs(rule) == ["ml_deep2_top", "margin_safe"]
    assert daily.rule_inputs(daily.DailyRule(name="y", factors={"trend_200": 1.0})) == ["trend_200"]
    assert set(daily.VETO2_H2) <= set(daily.VETO2_RULES)
    assert "不新買融資快斷頭的股票" in classify(rule.canonical())["traits"]


def test_both_vetoes_together_read_both_tables():
    """2026-10-22 (否決第三版): exclude "hm" — a stock in either the holders' or the margin fifth is not newly bought."""
    from quant_platform.research import daily
    from quant_platform.research.categories import classify

    rule = daily.DailyRule(name="x", factors={"ml_deep2_top": 1.0}, top=20, exclude="hm")
    assert daily.VETO_FACTORS["hm"] == ("holders_down_4w", "margin_safe")
    assert daily.rule_inputs(rule) == ["ml_deep2_top", "holders_down_4w", "margin_safe"]
    assert "不新買股東大增或融資快斷頭的股票" in classify(rule.canonical())["traits"]
    assert len(daily.VETO3_H2) == 120 and len(set(daily.VETO3_H2)) == 120 and len(daily.VETO3_HM) == 23
