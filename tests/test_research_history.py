from datetime import date

import pytest

from quant_platform.research.history.catalog import HistorySeries
from quant_platform.research.history.crosscheck import compare, split_factor, yahoo_history
from quant_platform.research.history.dataset import HistoryDataset, clean, read_series
from quant_platform.research.history.official import (
    DailyRow,
    OfficialHistoryClient,
    SourceRefused,
    parse_tpex_stock_month,
    parse_twse_stock_month,
    roc_date,
)

CATALOG = (
    HistorySeries("0050", "元大台灣50", "twse_etf", date(2009, 12, 1), "0050.TW"),
    HistorySeries("TAIEX", "加權指數", "taiex", date(2009, 12, 1), "^TWII"),
    HistorySeries("TAIEX_TR", "報酬指數", "taiex_tr", date(2009, 12, 1)),
)
TODAY = date(2010, 2, 3)
SESSIONS = {
    "200912": ["98/12/30", "98/12/31"],
    "201001": ["99/01/04", "99/01/05"],
    "201002": ["99/02/01", "99/02/02"],
}


class FakeExchange:
    def __init__(self):
        self.calls = []
        self.refuse = False

    def __call__(self, url):
        self.calls.append(url)
        if self.refuse:
            raise SourceRefused("HTTP 403")
        month = url.split("date=")[1][:6]
        if "MI_5MINS_HIST" in url:
            days = SESSIONS.get(month)
            if not days:
                return {"stat": "很抱歉，沒有符合條件的資料!"}
            return {"stat": "OK", "data": [[f" {day}", "7,000.00", "7,100.00", "6,950.00", "7,050.00"] for day in days]}
        if "MFI94U" in url:
            days = SESSIONS.get(month)
            if not days:
                return {"stat": "很抱歉，沒有符合條件的資料!"}
            return {"stat": "OK", "data": [[f" {day}", "9,000.00"] for day in days]}
        if "MI_INDEX" in url:
            day = url.split("date=")[1][:8]
            return {
                "stat": "OK", "date": day,
                "tables": [{}, {"fields": ["證券代號", "證券名稱", "成交股數"], "data": [
                    ["0050", "元大台灣50", "8,000,000", "800", "400,000,000", "50.00", "50.50", "49.80", "50.20"],
                    ["0051", "元大中型100", "1,000", "1", "20,000", "20.00", "20.00", "20.00", "20.00"],
                ]}],
            }
        if "STOCK_DAY" in url:
            days = SESSIONS.get(month, [])
            return {"stat": "OK", "data": [
                [day, "1,000", "50,000", "50.00", "51.00", "49.00", "50.50", "+0.30", "10", "**" if day == "99/02/01" else ""]
                for day in days
            ]}
        raise AssertionError(url)


def _dataset(tmp_path, exchange, offline=False):
    client = OfficialHistoryClient(
        tmp_path / "raw", fetch_json=exchange, min_interval=0, today=lambda: TODAY,
        sleep=lambda _seconds: None, offline=offline,
    )
    return HistoryDataset(tmp_path, client=client, catalog=CATALOG, today=lambda: TODAY)


def test_build_joins_the_daily_etf_report_with_monthly_history(tmp_path):
    exchange = FakeExchange()
    manifest = _dataset(tmp_path, exchange).build()

    entry = manifest["series"]["0050"]
    quality = entry["quality"]
    assert (quality["rows"], quality["first"], quality["last"]) == (6, "2009-12-30", "2010-02-02")
    assert entry["sources"] == {"twse_etf_daily": 2, "twse_stock_day": 4}
    assert quality["missing_sessions"] == 0 and quality["split_markers"] == ["2010-02-01"]
    rows = read_series(tmp_path / "daily" / "0050.parquet")
    assert rows[0]["date"] == date(2009, 12, 30) and rows[0]["close"] == 50.2 and rows[0]["volume"] == 8_000_000
    assert manifest["series"]["TAIEX_TR"]["quality"]["rows"] == 6
    assert manifest["series"]["TAIEX"]["quality"]["rows"] == 6


def test_rebuild_uses_the_cache_and_keeps_the_same_hash(tmp_path):
    first = _dataset(tmp_path, FakeExchange()).build()

    again = FakeExchange()
    second = _dataset(tmp_path, again).build()
    # Only the current month (2010-02) is asked again: TAIEX, total return and 0050.
    assert len(again.calls) == 3 and all("20100201" in url or "2010%2F02" in url for url in again.calls)

    offline = FakeExchange()
    third = _dataset(tmp_path, offline, offline=True).build()
    assert offline.calls == []
    for key in ("0050", "TAIEX", "TAIEX_TR"):
        assert first["series"][key]["sha256"] == second["series"][key]["sha256"] == third["series"][key]["sha256"]


def test_refusal_stops_the_run(tmp_path):
    exchange = FakeExchange()
    exchange.refuse = True
    with pytest.raises(SourceRefused):
        _dataset(tmp_path, exchange).build()
    assert len(exchange.calls) == 1  # no retries against a refusing exchange


def test_clean_reports_duplicates_no_trade_days_and_bad_bars():
    sessions = [date(2020, 1, day) for day in (2, 3, 6, 7)]
    rows = [
        DailyRow(date(2020, 1, 2), 10, 11, 9, 10.5, source="a"),
        DailyRow(date(2020, 1, 2), 10, 11, 9, 10.5, source="b"),
        DailyRow(date(2020, 1, 3), None, None, None, None, source="a"),
        DailyRow(date(2020, 1, 7), 10, 10.2, 10.1, 10.0, source="a"),
    ]

    cleaned, quality = clean(rows, sessions)

    assert [row.day.day for row in cleaned] == [2, 7]
    assert quality["duplicates"] == 1 and quality["no_trade_days"] == 1
    assert quality["missing_sessions"] == 2  # 01-03 (no trade) and 01-06
    assert quality["ohlc_inconsistent"] == ["2020-01-07"]


def test_parsers_follow_the_official_layouts():
    assert roc_date(" 92/07/01") == date(2003, 7, 1)
    twse = parse_twse_stock_month({"stat": "OK", "data": [
        [" 99/01/04", "20,083,125", "1,132,155,005", "56.45", "56.65", "56.05", "56.50", "+0.05", "1,624", ""],
        [" 99/01/05", "0", "0", "--", "--", "--", "--", " 0.00", "0", ""],
    ]})
    assert twse[0].close == 56.5 and twse[0].volume == 20_083_125 and twse[1].close is None
    tpex = parse_tpex_stock_month({"stat": "ok", "tables": [{"data": [
        ["106/02/02", "639", "24,503", "38.36", "38.37", "38.28", "38.37", "-0.72", "54"],
    ]}]})
    assert tpex[0].day == date(2017, 2, 2) and tpex[0].volume == 639_000 and tpex[0].turnover == 24_503_000


def test_crosscheck_scales_official_prices_by_yahoo_splits():
    split_day = date(2025, 6, 18)
    splits = [(split_day, 4.0)]
    official = {date(2025, 6, 17): 190.0, split_day: 47.6}
    yahoo = {date(2025, 6, 17): 47.5, split_day: 47.6, date(2025, 6, 19): 48.0}

    report = compare(official, yahoo, splits)

    assert split_factor(date(2025, 6, 17), splits) == 4.0 and split_factor(split_day, splits) == 1.0
    assert report["common_days"] == 2 and report["over_0_5pct"] == 0
    assert report["only_yahoo"] == 1 and report["max_relative_difference"] == 0.0


def test_yahoo_history_reads_closes_and_split_events():
    payload = {"chart": {"result": [{
        "timestamp": [1750208400, 1750294800],  # 2025-06-18 / 06-19 09:00 Taipei
        "indicators": {"quote": [{"close": [47.6, None]}]},
        "events": {"splits": {"1750208400": {"date": 1750208400, "numerator": 4, "denominator": 1}}},
    }]}}

    history, splits = yahoo_history("0050.TW", date(2025, 6, 1), date(2025, 6, 30), lambda _url: payload)

    assert history == {date(2025, 6, 18): 47.6}
    assert splits == [(date(2025, 6, 18), 4.0)]
