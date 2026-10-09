"""2026-10-06: quarterly statement factors dated by the filing deadline."""

import gzip
import json
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from quant_platform.research.fundamentals import FundamentalStore, build, deadline, factor_table

QUARTERS = [date(2024, 3, 31), date(2024, 6, 30), date(2024, 9, 30), date(2024, 12, 31), date(2025, 3, 31),
            date(2025, 6, 30)]


def test_quarters_are_usable_the_day_after_the_filing_deadline():
    assert [deadline(day) for day in QUARTERS[:4]] == [date(2024, 5, 16), date(2024, 9, 1), date(2024, 11, 15),
                                                       date(2025, 4, 1)]


def frame(gap=False):
    rows = []
    for number, quarter in enumerate(QUARTERS):
        if gap and number == 2:
            continue
        rows.append({"code": "1101", "quarter": quarter, "available": deadline(quarter), "revenue": 100.0 + 10 * number,
                     "gross_profit": 30.0 + 5 * number, "operating_income": 10.0 + 4 * number, "net_income": 8.0 + number,
                     "eps": 1.0 + 0.5 * number, "assets": 1000.0, "liabilities": 400.0, "equity": 200.0})
    return pd.DataFrame(rows)


def test_the_five_factors_by_hand():
    table = factor_table(frame()).reset_index(drop=True)
    fourth, fifth = table.iloc[3], table.iloc[4]
    assert np.isnan(table.iloc[2]["roe_ttm"])                               # three quarters only
    assert fourth["roe_ttm"] == pytest.approx((8 + 9 + 10 + 11) / 200)
    assert fourth["gross_margin"] == pytest.approx((30 + 35 + 40 + 45) / (100 + 110 + 120 + 130))
    # the 2025 Q1 operating margin (26/140) against 2024 Q1 (10/100); EPS 3.0 against 1.0
    assert fifth["operating_margin_change"] == pytest.approx(26 / 140 - 10 / 100)
    assert fifth["eps_growth"] == pytest.approx((3.0 - 1.0) / 1.0)
    assert fifth["low_debt"] == pytest.approx(-0.4) and fifth["available"] == date(2025, 5, 16)
    gapped = factor_table(frame(gap=True)).set_index("available")
    assert np.isnan(gapped.loc[date(2025, 4, 1), "roe_ttm"])                # a missing quarter breaks the four


def test_the_store_shows_a_quarter_only_from_its_deadline(tmp_path):
    raw = tmp_path / "raw" / "finmind"
    income, balance = raw / "TaiwanStockFinancialStatements", raw / "TaiwanStockBalanceSheet"
    income.mkdir(parents=True)
    balance.mkdir(parents=True)
    rows = frame()
    income_rows, balance_rows = [], []
    for row in rows.to_dict("records"):
        day = row["quarter"].isoformat()
        for name, item in (("Revenue", "revenue"), ("GrossProfit", "gross_profit"), ("OperatingIncome", "operating_income"),
                           ("EquityAttributableToOwnersOfParent", "net_income"), ("EPS", "eps")):
            income_rows.append({"date": day, "type": name, "value": row[item]})
        for name, item in (("TotalAssets", "assets"), ("Liabilities", "liabilities"),
                           ("EquityAttributableToOwnersOfParent", "equity")):
            balance_rows.append({"date": day, "type": name, "value": row[item]})
    (income / "1101.json.gz").write_bytes(gzip.compress(json.dumps({"data": income_rows}).encode()))
    (balance / "1101.json.gz").write_bytes(gzip.compress(json.dumps({"data": balance_rows}).encode()))
    assert build(tmp_path) == {"rows": 6, "codes": 1}
    sessions = [date(2025, 3, 31), date(2025, 4, 1), date(2025, 5, 15), date(2025, 5, 16)]
    store = FundamentalStore(tmp_path, sessions, ["1101.TW", "2330.TW"])
    roe = store.matrix("roe_ttm")
    # 2024 Q4 (8+9+10+11)/200 from April 1st; 2025 Q1 (9+10+11+12)/200 from May 16th; nothing before
    assert np.isnan(roe[0, 0]) and roe[0, 1] == pytest.approx(0.19) and roe[0, 2] == pytest.approx(0.19)
    assert roe[0, 3] == pytest.approx(0.21) and np.isnan(roe[1]).all()


def test_a_trained_version_keeps_its_features(tmp_path):
    from quant_platform.research import model

    (tmp_path / "meta.json").write_text(json.dumps({"features": ["trend_200", "momentum_3"]}), encoding="utf-8")
    assert model.trained_features(tmp_path) == ("trend_200", "momentum_3")
    assert len(model.trained_features(tmp_path / "missing")) == 36            # 2026-10-09: + revenue_accel


def test_the_due_quarter_follows_the_filing_deadlines():
    from quant_platform.research.fundamentals import due_quarter

    assert due_quarter(date(2026, 10, 7)) == date(2026, 6, 30)
    assert due_quarter(date(2026, 11, 14)) == date(2026, 6, 30) and due_quarter(date(2026, 11, 15)) == date(2026, 9, 30)
    assert due_quarter(date(2027, 3, 31)) == date(2026, 9, 30) and due_quarter(date(2027, 4, 1)) == date(2026, 12, 31)


def test_the_refresh_requests_only_files_without_the_due_quarter(tmp_path):
    from quant_platform.research.history.finmind import fetch_all, path_for

    for code, newest in (("1101", "2026-03-31"), ("2330", "2026-06-30")):
        path = path_for(tmp_path, "TaiwanStockFinancialStatements", code)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps({"data": [{"date": newest, "type": "EPS", "value": 1.0}]}).encode()))
    asked = []

    def get(dataset, code, token, end):
        asked.append(code)
        return {"data": [{"date": "2026-06-30", "type": "EPS", "value": 2.0}]}

    result = fetch_all(tmp_path, "t", ["1101", "2330"], ["TaiwanStockFinancialStatements"], get=get,
                       sleep=lambda _s: None, refresh_before=date(2026, 6, 30),
                       now=lambda: datetime(2026, 10, 7, 20, 0))      # outside the 13:30-15:30 pause
    assert asked == ["1101"] and result["requested"] == 1 and result["skipped"] == 1
    refreshed = json.loads(gzip.decompress(path_for(tmp_path, "TaiwanStockFinancialStatements", "1101").read_bytes()))
    assert refreshed["data"][0]["date"] == "2026-06-30"


def test_the_worker_starts_the_refresh_once_a_quarter(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.scheduler.runner import start_statements_refresh

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'r.db'}"))
    folder = tmp_path / "research" / "history" / "fundamentals"
    folder.mkdir(parents=True)
    (folder / "quarterly.parquet").write_bytes(b"x")
    started = []
    moment = datetime(2026, 11, 15, 22, 15, tzinfo=ZoneInfo("Asia/Taipei"))
    assert start_statements_refresh(container, moment, launch=started.append) == date(2026, 9, 30)
    assert start_statements_refresh(container, moment, launch=started.append) is None        # once a quarter
    assert started == [date(2026, 9, 30)] and (folder / "refresh-2026-09-30.started").is_file()
