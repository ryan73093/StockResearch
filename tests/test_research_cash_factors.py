"""2026-11-20 (使用者：繼續，還有什麼): the cash flow statement — year-to-date filings split into quarters, accruals and
cash profit, its own table and versions."""
import gzip
import json
from datetime import date

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_platform.research import fundamentals
from quant_platform.research.fundamentals import (
    CASH_FACTORS, CASH_FILE, CASH_VERSIONS, FILE, build_cash, cash_factor_table, deadline, quarter_cash,
)

QUARTERS = [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31), date(2025, 3, 31),
            date(2025, 6, 30)]
YEAR_TO_DATE = [10.0, 25.0, 45.0, 70.0, 20.0, 50.0]            # quarters of 10, 15, 20, 25, then 20, 30


def write_raw(history, code, values, item="CashFlowsFromOperatingActivities"):
    folder = history / "raw" / "finmind" / "TaiwanStockCashFlowsStatement"
    folder.mkdir(parents=True, exist_ok=True)
    rows = [{"date": quarter.isoformat(), "type": item, "value": value} for quarter, value in zip(QUARTERS, values)]
    (folder / f"{code}.json.gz").write_bytes(gzip.compress(json.dumps({"data": rows}).encode("utf-8")))


def write_statements(history, codes):
    rows = [{"code": code, "quarter": quarter, "available": deadline(quarter), "net_income": 12.0,
             "assets": 1000.0} for code in codes for quarter in QUARTERS]
    frame = pd.DataFrame(rows)
    path = history / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(frame, preserve_index=False), path)


def test_the_year_to_date_filings_are_split_into_quarters(tmp_path):
    write_raw(tmp_path, "1101", YEAR_TO_DATE)
    write_raw(tmp_path, "1102", [10.0, 25.0, None, 70.0, 20.0, 50.0])          # September missing
    cash = quarter_cash(tmp_path / "raw" / "finmind")
    assert [cash[("1101", quarter)] for quarter in QUARTERS] == [10.0, 15.0, 20.0, 25.0, 20.0, 30.0]
    assert ("1102", QUARTERS[2]) not in cash and ("1102", QUARTERS[3]) not in cash     # no quarter before December
    assert cash[("1102", QUARTERS[1])] == 15.0


def test_accruals_and_cash_profit_by_hand_and_financials_left_out(tmp_path):
    write_raw(tmp_path, "1101", YEAR_TO_DATE)
    write_raw(tmp_path, "2882", YEAR_TO_DATE)
    write_statements(tmp_path, ["1101", "2882"])
    info = {"data": [{"stock_id": "1101", "industry_category": "水泥工業"},
                     {"stock_id": "2882", "industry_category": "金融保險"}]}
    (tmp_path / "raw" / "finmind" / "TaiwanStockInfo.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    result = build_cash(tmp_path, date(2026, 11, 20))
    table = pq.read_table(tmp_path / CASH_FILE).to_pandas()
    assert result == {"rows": 6, "versions_added": 0} and set(table["code"]) == {"1101"}
    factors = cash_factor_table(table).reset_index(drop=True)
    assert np.isnan(factors.loc[2, "cash_profit"])                              # three quarters only
    assert factors.loc[3, "cash_profit"] == pytest.approx(70 / 1000)
    assert factors.loc[3, "low_accruals"] == pytest.approx(-(48 - 70) / 1000)
    assert factors.loc[5, "cash_profit"] == pytest.approx((20 + 25 + 20 + 30) / 1000)
    assert set(CASH_FACTORS) <= set(factors.columns)


def test_a_restated_quarter_is_a_new_version_and_reaches_only_forward(tmp_path):
    write_raw(tmp_path, "1101", YEAR_TO_DATE)
    write_statements(tmp_path, ["1101"])
    build_cash(tmp_path, date(2026, 11, 20))
    write_raw(tmp_path, "1101", [10.0, 25.0, 45.0, 70.0, 20.0, 80.0])          # June 2025 restated
    assert build_cash(tmp_path, date(2026, 11, 25))["versions_added"] == 1
    kept = pq.read_table(tmp_path / CASH_VERSIONS).to_pandas()
    assert len(kept) == 7 and str(kept["seen"].max())[:10] == "2026-11-25"
    store = fundamentals.CashStore(tmp_path, [date(2026, 11, 24), date(2026, 11, 25)], ["1101.TW"])
    values = store.matrix("cash_profit")[0]
    assert values[0] == pytest.approx(95 / 1000) and values[1] == pytest.approx(125 / 1000)


def test_cash_factors_are_rule_factors_with_their_own_input():
    from quant_platform.research import categories, daily, model, scan

    for name in CASH_FACTORS:
        assert name in daily.RULE_FACTORS and name not in daily.FACTOR_LABELS and name in scan.SHORT
        assert name not in scan.signals() and categories.FACTOR_FAMILY[name] == "營收財報"
    assert not set(CASH_FACTORS) & set(model.features())
    digests = daily.input_digests(fundamentals.Path("nowhere"), ["cash_profit"], False)
    assert "cash" in digests and "statements" not in digests
