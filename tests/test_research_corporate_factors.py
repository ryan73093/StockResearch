"""2026-10-15 (公司財務政策): a raised or cut cash dividend, and new shares from a cash capital increase."""
import gzip
import json
from datetime import date, timedelta

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_platform.research.chips import CORPORATE, CORPORATE_FACTORS, ChipStore, build, corporate_events


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def policy(announced, cash=0.0, shares=0.0):
    return {"AnnouncementDate": announced, "CashEarningsDistribution": cash, "CashStatutorySurplus": 0.0,
            "TotalNumberOfCashCapitalIncrease": shares}


def test_each_payout_is_compared_with_the_one_a_year_earlier():
    rows = [policy("2023-06-01", 2.0), policy("2024-06-03", 2.5), policy("2024-09-01", shares=1_000_000.0),
            policy("2025-06-02", 2.0),
            # a quarterly payer's quarters: each against the same quarter, not the one three months before
            policy("2024-03-01", 1.0), policy("2025-03-03", 1.2), policy("2025-11-30", 0.0)]
    events = corporate_events(rows)
    assert (date(2024, 6, 3), pytest.approx(0.5), None) in events                     # 2.5 against 2.0
    assert (date(2025, 6, 2), pytest.approx(-0.5), None) in events                    # a cut
    assert (date(2025, 3, 3), pytest.approx(0.2), None) in events
    assert (date(2024, 9, 1), None, 1_000_000.0) in events
    assert not any(day in (date(2023, 6, 1), date(2024, 3, 1)) for day, *_ in events)   # nothing a year before


def test_the_build_writes_one_row_per_announcement(tmp_path):
    folder = tmp_path / "raw" / "finmind" / "TaiwanStockDividend"
    folder.mkdir(parents=True)
    (folder / "1101.json.gz").write_bytes(gzip.compress(json.dumps({"data": [
        policy("2023-06-01", 2.0), policy("2024-06-03", 2.5), policy("2024-09-01", shares=5_000.0)]}).encode()))
    build(tmp_path)
    rows = pq.read_table(tmp_path / "chips" / f"{CORPORATE}.parquet").to_pylist()
    assert [(row["date"], row["dividend_change"], row["cash_issue_shares"]) for row in rows] == [
        (date(2024, 6, 3), pytest.approx(0.5), None), (date(2024, 9, 1), None, 5_000.0)]


def test_a_value_holds_250_sessions_from_the_session_after_the_announcement(tmp_path):
    days = weekdays(date(2023, 1, 2), date(2025, 6, 30))
    folder = tmp_path / "chips"
    folder.mkdir()
    pq.write_table(pa.table({"date": days * 2, "code": ["1101"] * len(days) + ["2330"] * len(days),
                             "ForeignInvestmentSharesRatio": [10.0] * (2 * len(days)),
                             "NumberOfSharesIssued": [1_000_000.0] * (2 * len(days))}),
                   folder / "TaiwanStockShareholding.parquet")
    announced = date(2024, 6, 1)                                     # a Saturday: used from Monday 06-03
    pq.write_table(pa.table({"date": [date(2023, 3, 1), announced, announced], "code": ["2330", "1101", "1101"],
                             "dividend_change": [None, 0.5, None], "cash_issue_shares": [None, None, 50_000.0]}),
                   folder / f"{CORPORATE}.parquet")
    close = np.full((2, len(days)), 25.0)
    chips = ChipStore(tmp_path, days, ["1101.TW", "2330.TW"], close)
    assert chips.corporate_available()
    raise_ = chips.corporate_matrix("dividend_raise")
    start = days.index(date(2024, 6, 3))
    assert raise_[0, start - 1] == 0.0 and raise_[0, start] == pytest.approx(0.5 / 25)    # 2% more of the price
    assert raise_[0, start + 249] == pytest.approx(0.02) and raise_[0, start + 250] == 0.0                  # 250 sessions
    assert np.isnan(raise_[0, days.index(date(2023, 3, 1))]) and raise_[1, start] == 0.0  # before the data; no event
    issue = chips.corporate_matrix("no_cash_issue")
    assert issue[0, start] == pytest.approx(-0.05) and issue[1, start] == 0.0              # 50,000 new of 1,000,000
    cut = ChipStore(tmp_path, days, ["1101.TW", "2330.TW"], close, until=date(2024, 6, 3))
    assert cut.corporate_matrix("no_cash_issue")[0, start] == pytest.approx(-0.05)        # announced before the cut
    early = ChipStore(tmp_path, days, ["1101.TW", "2330.TW"], close, until=date(2024, 5, 31))
    assert early.corporate_matrix("no_cash_issue")[0, start] == 0.0                         # not yet announced
    # 2026-10-18: on the listed-and-TPEx panel a stock that moved markets is two symbols with one code
    both = ChipStore(tmp_path, days, ["1101.TW", "2330.TW", "1101.TWO"], np.full((3, len(days)), 25.0))
    matrix = both.corporate_matrix("dividend_raise")
    assert matrix.shape == (3, len(days)) and matrix[0, start] == matrix[2, start] == pytest.approx(0.02)
    assert both.corporate_matrix("no_cash_issue")[2, start] == pytest.approx(-0.05)


def test_corporate_factors_are_rule_factors_but_not_model_features():
    from quant_platform.research import daily, model, rl_timing, scan

    assert set(CORPORATE_FACTORS) <= set(daily.FACTOR_LABELS) and set(CORPORATE_FACTORS) <= set(scan.signals())
    assert not set(CORPORATE_FACTORS) & set(model.features())
    assert not set(CORPORATE_FACTORS) & set(rl_timing.factor_names(rl_timing.ALL_VERSION))
