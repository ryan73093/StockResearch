"""2026-10-14 (資料缺口：借券賣出餘額、當沖比例): short selling through securities lending and day trading."""
from datetime import date, timedelta

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_platform.research.chips import SHORT_FACTORS, ChipStore


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


def store(tmp_path, days):
    folder = tmp_path / "chips"
    folder.mkdir()
    write(folder, "TaiwanStockShareholding", [{"date": day, "code": "1101", "ForeignInvestmentSharesRatio": 10.0,
                                               "NumberOfSharesIssued": 1_000_000.0} for day in days])
    write(folder, "TaiwanDailyShortSaleBalances", [{"date": day, "code": "1101", "SBLShortSalesCurrentDayBalance": 1_000.0 * (n + 1),
                                                    "MarginShortSalesCurrentDayBalance": 0.0} for n, day in enumerate(days)])
    write(folder, "TaiwanStockDayTrading", [{"date": day, "code": "1101", "BuyAmount": 2_000_000.0, "Volume": 10_000.0}
                                            for day in days])
    return ChipStore(tmp_path, days, ["1101.TW"], np.full((1, len(days)), 50.0))


def test_lending_short_and_day_trading_use_the_day_before(tmp_path):
    days = weekdays(date(2024, 1, 1), date(2024, 3, 29))
    chips = store(tmp_path, days)
    assert chips.short_available()
    turnover = np.full((1, len(days)), 10_000_000.0)
    low_short = chips.short_matrix("low_sbl_short", turnover)[0]
    assert np.isnan(low_short[0]) and low_short[5] == pytest.approx(-5_000 / 1_000_000)     # the 5th row: day 4's balance
    cover = chips.short_matrix("sbl_cover_20", turnover)[0]
    assert cover[30] == pytest.approx(-20_000 / 1_000_000)                                   # 20 more thousand in 20 sessions
    day_trade = chips.short_matrix("low_day_trade_20", turnover)[0]
    assert day_trade[30] == pytest.approx(-0.2)                                              # 2 of every 10 million
    assert set(SHORT_FACTORS) == {"low_sbl_short", "sbl_cover_20", "low_day_trade_20"}


def test_short_factors_are_rule_factors_but_not_model_features():
    from quant_platform.research import daily, model, scan

    assert set(SHORT_FACTORS) <= set(daily.FACTOR_LABELS) and set(SHORT_FACTORS) <= set(scan.signals())
    assert not set(SHORT_FACTORS) & set(model.features())
