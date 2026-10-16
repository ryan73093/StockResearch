"""2026-10-16 (自營商): the dealers' own trading and hedging; model G learns every family but monthly revenue."""
import gzip
import json
from datetime import date, timedelta

import numpy as np
import pyarrow.parquet as pq
import pytest

from quant_platform.research.chips import DEALER_FACTORS, ChipStore, build


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def test_the_build_keeps_the_dealers_apart_and_their_flows_are_known_the_next_session(tmp_path):
    days = weekdays(date(2024, 1, 1), date(2024, 3, 29))
    raw = tmp_path / "raw" / "finmind"
    (raw / "TaiwanStockInstitutionalInvestorsBuySell").mkdir(parents=True)
    (raw / "TaiwanStockShareholding").mkdir(parents=True)
    flows = []
    for day in days:
        flows += [{"date": day.isoformat(), "name": "Dealer_self", "buy": 1_000, "sell": 0},
                  {"date": day.isoformat(), "name": "Dealer_Hedging", "buy": 0, "sell": 2_000},
                  {"date": day.isoformat(), "name": "Foreign_Investor", "buy": 5_000, "sell": 0}]
    (raw / "TaiwanStockInstitutionalInvestorsBuySell" / "1101.json.gz").write_bytes(gzip.compress(json.dumps({"data": flows}).encode()))
    (raw / "TaiwanStockShareholding" / "1101.json.gz").write_bytes(gzip.compress(json.dumps({"data": [
        {"date": day.isoformat(), "ForeignInvestmentSharesRatio": 10.0, "NumberOfSharesIssued": 1_000_000.0} for day in days]}).encode()))
    build(tmp_path)
    rows = pq.read_table(tmp_path / "chips" / "TaiwanStockInstitutionalInvestorsBuySell.parquet").to_pylist()
    assert rows[0]["dealer_self_net"] == 1_000 and rows[0]["dealer_hedge_net"] == -2_000 and rows[0]["foreign_net"] == 5_000
    chips = ChipStore(tmp_path, days, ["1101.TW"], np.full((1, len(days)), 20.0))
    assert chips.dealer_available()
    own, hedge = chips.matrix("dealer_buy_20")[0], chips.matrix("dealer_hedge_20")[0]
    assert np.isnan(own[19]) and own[20] == pytest.approx(20 * 1_000 / 1_000_000)        # sessions 0-19, known on 20
    assert hedge[30] == pytest.approx(-20 * 2_000 / 1_000_000)


def test_dealer_factors_are_rule_factors_and_only_model_g_learns_them():
    from quant_platform.research import daily, model, rl_timing, scan

    assert set(DEALER_FACTORS) <= set(daily.FACTOR_LABELS) and set(DEALER_FACTORS) <= set(scan.signals())
    assert not set(DEALER_FACTORS) & set(model.features())
    extended = set(model.version_features("gbm-2.2.0"))
    assert set(DEALER_FACTORS) <= extended and {"low_sbl_short", "dividend_raise", "low_lending_fee_60"} <= extended
    assert not set(model.MONTHLY_REVENUE) & extended                                       # revenue lags (使用者 10-10)
    assert "ml_gbm_ext" in daily.MODEL_FACTORS and "ml_gbm_ext" not in rl_timing.factor_names(rl_timing.ALL_VERSION)
