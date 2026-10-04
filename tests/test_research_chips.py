import gzip
import json
from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research.chips import ChipStore, build


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def write_raw(base, dataset, code, rows):
    folder = base / "raw" / "finmind" / dataset
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{code}.json.gz").write_bytes(gzip.compress(json.dumps({"data": rows}).encode("utf-8")))


def test_chip_factors_use_only_what_was_public_at_each_session(tmp_path):
    days = weekdays(date(2023, 1, 2), date(2024, 3, 29))
    holding = [{"date": d.isoformat(), "ForeignInvestmentSharesRatio": 10.0 + i * 0.1, "NumberOfSharesIssued": 1_000_000}
               for i, d in enumerate(days)]
    write_raw(tmp_path, "TaiwanStockShareholding", "1101", holding)
    write_raw(tmp_path, "TaiwanStockPER", "1101", [{"date": d.isoformat(), "PER": 20.0, "PBR": 2.0} for d in days])
    write_raw(tmp_path, "TaiwanStockMarginPurchaseShortSale", "1101",
              [{"date": d.isoformat(), "MarginPurchaseTodayBalance": 100 + i, "ShortSaleTodayBalance": 10}
               for i, d in enumerate(days)])
    institutional = []
    for d in days:
        institutional += [{"date": d.isoformat(), "name": "Foreign_Investor", "buy": 1_000, "sell": 0},
                          {"date": d.isoformat(), "name": "Investment_Trust", "buy": 0, "sell": 500}]
    write_raw(tmp_path, "TaiwanStockInstitutionalInvestorsBuySell", "1101", institutional)
    revenue = []
    for year in (2023, 2024):
        for month in range(1, 13):
            if (year, month) > (2024, 2):
                break
            created = date(year + (month == 12), month % 12 + 1, 8)
            revenue.append({"revenue_year": year, "revenue_month": month, "revenue": 100.0 * (1.2 if year == 2024 else 1.0),
                            "create_time": created.isoformat()})
    write_raw(tmp_path, "TaiwanStockMonthRevenue", "1101", revenue)
    written = build(tmp_path)
    assert written["TaiwanStockShareholding"] == len(days) and written["TaiwanStockMonthRevenue"] == 14

    close = np.full((1, len(days)), 50.0)
    store = ChipStore(tmp_path, days, ["1101.TW"], close)
    t = days.index(date(2024, 1, 3))
    # published in the evening: the session uses the value dated the session before
    assert store.matrix("foreign_holding")[0, t] == pytest.approx(10.0 + (t - 1) * 0.1)
    assert store.matrix("foreign_holding_change")[0, t] == pytest.approx(2.0, abs=1e-4)          # 20 sessions × 0.1
    assert store.matrix("foreign_buy_20")[0, t] == pytest.approx(20 * 1_000 / 1_000_000)
    assert store.matrix("trust_buy_20")[0, t] == pytest.approx(-20 * 500 / 1_000_000)
    assert store.matrix("earnings_yield")[0, t] == pytest.approx(0.05)
    assert store.matrix("short_margin_ratio")[0, t] == pytest.approx(10 / (100 + t - 1))
    assert store.matrix("market_cap")[0, t] == pytest.approx(np.log(1_000_000 * 50.0))
    # January 2024 revenue is announced on 2024-02-08: usable from 02-09, 20% above January 2023
    before, after = days.index(date(2024, 2, 8)), days.index(date(2024, 2, 9))
    yoy = store.matrix("revenue_yoy")
    assert yoy[0, after] == pytest.approx(0.2) and (np.isnan(yoy[0, before]) or yoy[0, before] == pytest.approx(0.2))
    assert np.isnan(yoy[0, days.index(date(2024, 1, 9))]) or yoy[0, days.index(date(2024, 1, 9))] != pytest.approx(0.2)
