"""2026-10-21 (FinMind Sponsor 計畫，路線圖 S9-W22): the depository's holder brackets as factors, FinMind's history
merged with the weekly files, the paid download's plan detection and its nightly start."""
import gzip
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from quant_platform.research.chips import HOLDER_DELAY_DAYS, ChipStore
from quant_platform.research.history import finmind, tdcc


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def week_rows(day, code, big, retail, holders):
    rows = [{"date": day, "code": code, "level": level, "holders": 10, "shares": 100, "percent": retail / 3}
            for level in (1, 2, 3)]
    rows.append({"date": day, "code": code, "level": 15, "holders": 5, "shares": 900, "percent": big})
    rows.append({"date": day, "code": code, "level": 17, "holders": holders, "shares": 1000, "percent": 100.0})
    return rows


def test_the_holder_factors_by_hand_from_the_monday_after_each_friday(tmp_path):
    fridays = [date(2024, 1, 5) + timedelta(weeks=n) for n in range(6)]
    rows = []
    for number, friday in enumerate(fridays):
        rows += week_rows(friday.strftime("%Y%m%d"), "1101", 50.0 + number, 30.0 - number, 1000 - 10 * number)
    (tmp_path / "tdcc").mkdir()
    pd.DataFrame(rows).to_parquet(tmp_path / "tdcc" / "holding.parquet", index=False)
    sessions = weekdays(date(2024, 1, 1), date(2024, 2, 23))
    store = ChipStore(tmp_path, sessions, ["1101.TW", "9999.TW"], np.ones((2, len(sessions))))
    assert store.holder_available()
    big = store.holder_matrix("big_holder_pct")
    monday = sessions.index(date(2024, 1, 8))                    # the first Friday's table: from Monday on
    assert np.isnan(big[0, :monday]).all() and big[0, monday] == pytest.approx(0.50)
    assert big[0, monday + 4] == pytest.approx(0.50) and big[0, monday + 5] == pytest.approx(0.51)
    assert np.isnan(big[1]).all()                                # no brackets for this code
    up = store.holder_matrix("big_holder_up_4w")
    fifth = sessions.index(date(2024, 2, 5))                     # the fifth Friday (2/2) against the first (1/5)
    assert np.isnan(up[0, fifth - 1]) and up[0, fifth] == pytest.approx(0.04)
    holders = store.holder_matrix("holders_down_4w")
    assert holders[0, fifth] == pytest.approx(-(960 / 1000 - 1))
    retail = store.holder_matrix("retail_down_4w")
    assert retail[0, fifth] == pytest.approx(0.04)
    store.until = date(2024, 1, 8)                               # what was public by Monday 1/8's close
    seen = store.holder_matrix("big_holder_pct")
    assert seen[0, monday] == pytest.approx(0.50) and seen[0, monday + 5] == pytest.approx(0.50)
    assert HOLDER_DELAY_DAYS == 3


def test_finmind_brackets_become_the_depositorys_numbers_and_the_weekly_files_win(tmp_path):
    assert [tdcc.finmind_level(text) for text in ("1-999", "1,000-5,000", "5,001-10,000", "800,001-1,000,000",
                                                  "more than 1,000,001", "total", "差異數調整（說明4）")] == [1, 2, 3, 14, 15, 17, 16]
    folder = tmp_path / "raw" / "finmind" / tdcc.FINMIND_DATASET
    folder.mkdir(parents=True)
    data = [{"date": "2026-10-16", "stock_id": "2330", "HoldingSharesLevel": "more than 1,000,001", "people": 1470,
             "percent": 84.0, "unit": 21_000_000_000},
            {"date": "2026-10-09", "stock_id": "2330", "HoldingSharesLevel": "more than 1,000,001", "people": 1460,
             "percent": 83.9, "unit": 20_900_000_000},
            {"date": "2026-10-09", "stock_id": "2330", "HoldingSharesLevel": "差異數調整（說明4）", "people": 0,
             "percent": 0.0, "unit": 0}]
    (folder / "2330.json.gz").write_bytes(gzip.compress(json.dumps({"data": data}).encode("utf-8")))
    weekly = tmp_path / "raw" / "tdcc" / "holding"
    weekly.mkdir(parents=True)
    (weekly / "20261016.csv.gz").write_bytes(gzip.compress(
        "資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n20261016,2330,15,1476,21928634563,84.56\n".encode("utf-8")))
    found = tdcc.build(tmp_path)
    table = pd.read_parquet(tmp_path / "tdcc" / "holding.parquet")
    assert found == {"weeks": 2, "rows": 2, "first": "20261009"}
    assert table[table["date"] == "20261016"]["percent"].tolist() == [84.56]       # the depository's own file wins
    assert tdcc.digest(tmp_path, date(2026, 10, 10)) != tdcc.digest(tmp_path, date(2026, 10, 20))


def test_the_plan_is_read_from_the_tokens_hourly_limit():
    assert finmind.request_limit("x", get=lambda: {"api_request_limit": 6000, "user_count": 3}) == 6000
    assert finmind.request_limit("x", get=lambda: {"msg": "no"}) is None
    assert int(6000 * finmind.PAID_SHARE) == 5580
    assert "TaiwanStockHoldingSharesPer" in finmind.PAID


def test_the_paid_download_starts_once_when_the_plan_is_paid(tmp_path):
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.scheduler.runner import start_finmind_paid

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'paid.db'}", finmind_token="t"))
    started = []
    now = datetime(2026, 10, 22, 22, 20, tzinfo=ZoneInfo("Asia/Taipei"))
    assert start_finmind_paid(container, now, launch=started.append, limit=lambda token: 600) is None
    assert start_finmind_paid(container, now, launch=started.append, limit=lambda token: 6000) == "finmind-paid"
    assert start_finmind_paid(container, now, launch=started.append, limit=lambda token: 6000) is None   # once
    assert started == ["finmind-paid"]
    assert json.loads((tmp_path / "research" / "history" / "raw" / "finmind" / "plan.json").read_text(encoding="utf-8"))["limit"] == 6000
    assert finmind.plan("x", get=lambda: {"api_request_limit": 600, "user_count": 0, "level_title": "Free",
                                          "SponsorInfo": {"subscription_expired_date": ""}}) == {
        "title": "Free", "limit": 600, "used": 0, "expires": None, "next": None}


def test_the_token_is_read_from_the_env_file_as_it_is_now(tmp_path):
    (tmp_path / ".env").write_text("A=1\nFINMIND_TOKEN='new'\n", encoding="utf-8")
    assert finmind.env_token(tmp_path) == "new" and finmind.env_token(tmp_path / "none") == ""


def test_a_reached_limit_waits_until_finmind_frees_it():
    moment = datetime(2026, 10, 22, 0, 0, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    answer = {"api_request_limit": 6000, "next_available_time": "2026-10-22 00:00:56"}
    assert finmind.quota_wait("x", moment, get=lambda: answer) == 71.0
    assert finmind.quota_wait("x", moment, get=lambda: {"api_request_limit": 6000, "next_available_time": ""}) == 900.0
    late = {"api_request_limit": 6000, "next_available_time": "2026-10-21 23:59:00"}
    assert finmind.quota_wait("x", moment, get=lambda: late) == 60.0
