"""2026-10-16 (股本膨脹): new shares over 250 sessions, a stock dividend's listing taken out, falls left out."""
from datetime import date, timedelta

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_platform.research.chips import ISSUANCE_FACTORS, STOCK_DIVIDENDS, ChipStore


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def store(tmp_path, days, dividends, until=None):
    folder = tmp_path / "chips"
    folder.mkdir(exist_ok=True)
    shares = []
    for n, day in enumerate(days):
        # 1101: a 10% stock dividend (ex-date session 100) listed on session 110, a 10% cash capital increase on
        # session 200, halved by a capital reduction on session 300; 2330 never changes
        count = 1_000_000.0 * (1.1 if n >= 110 else 1.0) * (1.1 if n >= 200 else 1.0) * (0.5 if n >= 300 else 1.0)
        shares += [{"date": day, "code": "1101", "ForeignInvestmentSharesRatio": 1.0, "NumberOfSharesIssued": count},
                   {"date": day, "code": "2330", "ForeignInvestmentSharesRatio": 1.0, "NumberOfSharesIssued": 5e6}]
    pq.write_table(pa.table({key: [row[key] for row in shares] for key in shares[0]}), folder / "TaiwanStockShareholding.parquet")
    pq.write_table(pa.table({"date": [day for day, _ratio in dividends], "code": ["1101"] * len(dividends),
                             "ratio": [ratio for _day, ratio in dividends]},
                            schema=pa.schema([("date", pa.date32()), ("code", pa.string()), ("ratio", pa.float64())])),
                   folder / f"{STOCK_DIVIDENDS}.parquet")
    return ChipStore(tmp_path, days, ["1101.TW", "2330.TW"], np.full((2, len(days)), 20.0), until=until)



def test_new_shares_count_a_stock_dividend_and_a_reduction_do_not(tmp_path):
    days = weekdays(date(2022, 1, 3), date(2024, 12, 31))
    chips = store(tmp_path, days, [(days[100], 0.1)])
    assert chips.issuance_available()
    values = chips.issuance_matrix()
    assert np.isnan(values[0, 247]) and values[0, 260] == pytest.approx(-np.log(1.1))   # 250 sessions needed
    assert values[0, 300] == pytest.approx(-np.log(1.1))                            # the capital increase only
    assert values[0, 460] == pytest.approx(0.0) and values[1, 300] == 0.0           # it left the window; the fall is ignored
    plain = store(tmp_path, days, [])                                                # without the dividend's record
    assert plain.issuance_matrix()[0, 300] == pytest.approx(-2 * np.log(1.1))
    early = store(tmp_path, days, [(days[100], 0.1)], until=days[90])               # the dividend not yet known
    assert np.isnan(early.issuance_matrix()[0, 300]) or early.issuance_matrix()[0, 300] != values[0, 300]


def test_issuance_is_a_rule_factor_but_not_a_model_feature():
    from quant_platform.research import daily, model, rl_timing, scan

    assert set(ISSUANCE_FACTORS) <= set(daily.FACTOR_LABELS) and set(ISSUANCE_FACTORS) <= set(scan.signals())
    assert not set(ISSUANCE_FACTORS) & set(model.features())
    assert not set(ISSUANCE_FACTORS) & set(rl_timing.factor_names(rl_timing.ALL_VERSION))
