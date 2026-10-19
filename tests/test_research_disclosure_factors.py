"""2026-10-19 (使用者：還有什麼方法可以找到更好的策略): insiders' notices to sell on the market and buyback programs as
factors — from the session after the date, summed over 60 sessions, against the shares issued."""
from datetime import date, timedelta

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_platform.research.chips import DISCLOSURE_SESSIONS, ChipStore


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def test_insider_sales_and_buybacks_count_for_60_sessions_from_the_next_session(tmp_path):
    days = weekdays(date(2024, 1, 1), date(2024, 12, 31))
    folder = tmp_path / "chips"
    folder.mkdir()
    pq.write_table(pa.table({"date": days * 2, "code": ["1101"] * len(days) + ["2330"] * len(days),
                             "NumberOfSharesIssued": [1_000_000.0] * (2 * len(days))}),
                   folder / "TaiwanStockShareholding.parquet")
    (tmp_path / "insider").mkdir()
    pq.write_table(pa.table({
        "notice_date": [date(2024, 3, 1), date(2024, 6, 3), date(2024, 6, 3)],
        "code": ["2330", "1101", "1101"],
        "method": ["贈與", "一般交易(每日得轉讓股數限制)", "贈與"],                 # a gift is not a sale
        "total_own": [5_000.0, 10_000.0, 50_000.0], "total_trust": [0.0, 0.0, 0.0]}),
        tmp_path / "insider" / "transfers.parquet")
    (tmp_path / "buyback").mkdir()
    pq.write_table(pa.table({"board_date": [date(2024, 6, 3)], "code": ["2330"], "planned_shares": [20_000.0]}),
                   tmp_path / "buyback" / "programs.parquet")
    chips = ChipStore(tmp_path, days, ["1101.TW", "2330.TW"], np.full((2, len(days)), 50.0))
    assert chips.disclosure_available("low_insider_sell_60") and chips.disclosure_available("buyback_60")
    sell = chips.disclosure_matrix("low_insider_sell_60")
    start = days.index(date(2024, 6, 4))                                     # filed Monday: used from Tuesday
    assert sell[0, start - 1] == 0.0 and sell[0, start] == pytest.approx(-0.01)
    assert sell[0, start + DISCLOSURE_SESSIONS - 1] == pytest.approx(-0.01) and sell[0, start + DISCLOSURE_SESSIONS] == 0.0
    assert np.isnan(sell[0, days.index(date(2024, 2, 1))])                   # before the data starts
    assert sell[1, start] == 0.0                                             # 2330's notice was a gift
    buy = chips.disclosure_matrix("buyback_60")
    assert buy[1, start] == pytest.approx(0.02) and buy[0, start] == 0.0
    cut = ChipStore(tmp_path, days, ["1101.TW", "2330.TW"], np.full((2, len(days)), 50.0), until=date(2024, 6, 3))
    assert np.isnan(cut.disclosure_matrix("buyback_60")[1, start])           # nothing announced yet at the cut


def test_an_event_week_compares_the_stocks_with_an_event_to_every_eligible_one():
    from quant_platform.research.factors import event_weeks

    matrix = np.zeros(60)
    matrix[:3] = [0.01, 0.02, np.nan]                                          # two events, one unknown
    forward = np.full(60, 0.01)
    forward[:2] = [0.05, 0.03]
    eligible = np.ones(60, dtype=bool)
    excess, stocks = event_weeks(matrix, forward, eligible)
    assert stocks == 2 and excess == pytest.approx(0.04 - (0.08 + 0.01 * 57) / 59)
    assert event_weeks(np.zeros(60), forward, eligible) is None                # no event that week
    assert event_weeks(matrix, forward, np.zeros(60, dtype=bool)) is None     # too few eligible
