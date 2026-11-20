"""2026-11-20 (使用者：繼續，還有什麼): the asset growth and gross profit over assets from the quarterly statements."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from quant_platform.research.fundamentals import BALANCE_FACTORS, EARNINGS_FACTORS, deadline, factor_table

QUARTERS = [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31), date(2025, 3, 31),
            date(2025, 6, 30)]


def frame():
    rows = []
    for number, quarter in enumerate(QUARTERS):
        rows.append({"code": "1101", "quarter": quarter, "available": deadline(quarter), "revenue": 100.0,
                     "gross_profit": 30.0 + 5 * number, "operating_income": 10.0, "net_income": 8.0, "eps": 1.0,
                     "assets": 1000.0 + 100 * number, "liabilities": 400.0, "equity": 200.0})
    return pd.DataFrame(rows)


def test_asset_growth_and_gross_profit_over_assets_by_hand():
    table = factor_table(frame()).reset_index(drop=True)
    assert np.isnan(table.loc[3, "low_asset_growth"])                    # no quarter a year before
    assert table.loc[4, "low_asset_growth"] == pytest.approx(-(1400 / 1000 - 1))
    assert table.loc[5, "low_asset_growth"] == pytest.approx(-(1500 / 1100 - 1))
    assert np.isnan(table.loc[2, "gp_assets"])                           # three quarters of gross profit only
    assert table.loc[3, "gp_assets"] == pytest.approx((30 + 35 + 40 + 45) / 1300)
    assert table.loc[5, "gp_assets"] == pytest.approx((40 + 45 + 50 + 55) / 1500)


def test_balance_factors_are_rule_factors_outside_the_model_lists():
    from quant_platform.research import categories, daily, model, scan
    from quant_platform.research.dashboard import source_groups

    assert not set(BALANCE_FACTORS) & set(EARNINGS_FACTORS)             # model O's features read that family
    for name in BALANCE_FACTORS:
        assert name in daily.RULE_FACTORS and name not in daily.FACTOR_LABELS
        assert name not in scan.signals() and name in scan.SHORT
        assert categories.FACTOR_FAMILY[name] == "營收財報"
    assert not set(BALANCE_FACTORS) & set(model.features())
    statements = next(group for group in source_groups() if group[0] == "statements")
    assert set(BALANCE_FACTORS) <= statements[3]


def test_a_rule_reading_them_hashes_the_statements(tmp_path):
    from quant_platform.research import daily

    digests = daily.input_digests(tmp_path, ["low_asset_growth"], False)
    assert "statements" in digests
