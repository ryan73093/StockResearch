"""2026-10-15 (借券賣出餘額低是強因子，沿著借券這條線): the cost and the amount of securities lending."""
import gzip
import json
from datetime import date, timedelta

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_platform.research.chips import LENDING, LENDING_FACTORS, ChipStore, build


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def write(folder, name, rows):
    columns = {key: [row[key] for row in rows] for key in rows[0]}
    pq.write_table(pa.table(columns), folder / f"{name}.parquet")


def test_the_build_sums_each_days_deals_into_lots_and_lots_times_fee(tmp_path):
    raw = tmp_path / "raw" / "finmind"
    folder = raw / LENDING
    folder.mkdir(parents=True)
    (folder / "1101.json.gz").write_bytes(gzip.compress(json.dumps({"data": [
        {"date": "2024-01-02", "stock_id": "1101", "transaction_type": "議借", "volume": 10, "fee_rate": 1.0},
        {"date": "2024-01-02", "stock_id": "1101", "transaction_type": "競價", "volume": 30, "fee_rate": 3.0},
        {"date": "2024-01-02", "stock_id": "1101", "transaction_type": "定價", "volume": 5, "fee_rate": None},  # no fee: left out
        {"date": "2024-01-03", "stock_id": "1101", "transaction_type": "議借", "volume": 0, "fee_rate": 9.0},     # nothing lent
    ]}).encode()))
    build(tmp_path)
    rows = pq.read_table(tmp_path / "chips" / f"{LENDING}.parquet").to_pylist()
    assert rows == [{"date": date(2024, 1, 2), "code": "1101", "LendingVolume": 40.0, "FeeVolume": 100.0}]   # 2.5% on average


def test_fee_level_new_borrowing_and_fee_change_use_the_day_before(tmp_path):
    days = weekdays(date(2024, 1, 1), date(2024, 6, 28))
    folder = tmp_path / "chips"
    folder.mkdir()
    write(folder, "TaiwanStockShareholding", [{"date": day, "code": code, "ForeignInvestmentSharesRatio": 10.0,
                                               "NumberOfSharesIssued": 1_000_000.0} for day in days for code in ("1101", "2330")])
    # 1101: 10 lots lent every session, at 1% for the first 80 sessions and 2% after; 2330 is never borrowed
    write(folder, LENDING, [{"date": day, "code": "1101", "LendingVolume": 10.0, "FeeVolume": 10.0 * (1.0 if n < 80 else 2.0)}
                            for n, day in enumerate(days)])
    chips = ChipStore(tmp_path, days, ["1101.TW", "2330.TW"], np.full((2, len(days)), 50.0))
    assert chips.lending_available()
    fee = chips.lending_matrix("low_lending_fee_60")
    assert np.isnan(fee[0, 59]) and fee[0, 60] == pytest.approx(-1.0)           # sessions 0-59, known on session 60
    assert fee[0, 129] == pytest.approx(-(11 * 1.0 + 49 * 2.0) / 60) and np.isnan(fee[1, 100])   # 69-79 at 1%, 80-128 at 2%
    lent = chips.lending_matrix("low_lending_20")
    assert lent[0, 100] == pytest.approx(-20 * 10 * 1000 / 1_000_000)           # 200 lots = 200,000 of 1,000,000 shares
    assert lent[1, 100] == 0.0 and np.isnan(lent[0, 0])                          # none borrowed; nothing known before
    rising = chips.lending_matrix("lending_fee_not_rising")
    assert rising[0, 100] == pytest.approx(-(2.0 - 1.0))                         # sessions 80-99 at 2% against 20-79 at 1%
    assert np.isnan(rising[0, 79]) and rising[0, 80] == pytest.approx(0.0) and np.isnan(rising[1, 100])   # 80 sessions needed
    cut = ChipStore(tmp_path, days, ["1101.TW", "2330.TW"], np.full((2, len(days)), 50.0), until=days[100])
    assert cut.lending_matrix("lending_fee_not_rising")[0, 100] == pytest.approx(rising[0, 100])   # nothing after the cut


def test_lending_factors_are_rule_factors_but_not_model_features():
    from quant_platform.research import daily, model, rl_timing, scan

    assert set(LENDING_FACTORS) <= set(daily.FACTOR_LABELS) and set(LENDING_FACTORS) <= set(scan.signals())
    assert not set(LENDING_FACTORS) & set(model.features())
    assert not set(LENDING_FACTORS) & set(rl_timing.factor_names(rl_timing.ALL_VERSION))
