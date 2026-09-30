from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from quant_platform.research.history.actions import (
    CorporateAction,
    build_actions,
    nice_ratio,
    splits_from_markers,
    total_return,
)
from quant_platform.research.history.catalog import HistorySeries
from quant_platform.research.history.crosscheck import compare_total_return
from quant_platform.research.history.dataset import read_series, write_parquet
from quant_platform.research.history.official import (
    DailyRow,
    OfficialHistoryClient,
    parse_twse_ex_rights,
    roc_long_date,
)

TAIPEI = ZoneInfo("Asia/Taipei")
EX_RIGHTS_ROW = [
    "94年05月19日", "0050", "元大台灣50", "46.69", "44.84", "0.0", "1.85", "1.850000", "息",
    "47.97", "41.71", "44.84", "44.84", "0050,20050519", "", "111.66", "N/A",
]


def test_odd_lot_rows_and_premium_summary():
    from quant_platform.research.history.odd_lot import parse_odd_lot, summarize

    rows = parse_odd_lot({"stat": "OK", "data": [
        ["0050", "台灣50", "5,854", "48", "331,026", "56.55", "56.55", "5,912", "56.60", "2,468"],
        ["0056", "元大高股息", "0", "0", "0", "--", "20.00", "10", "20.05", "5"],
    ]}, {"0050", "0056"})
    assert rows["0050"]["price"] == 56.55 and rows["0050"]["shares"] == 5_854
    assert rows["0056"]["price"] is None

    report = summarize([0.0, 0.001, -0.002, 0.0005], sampled=5)
    assert report["traded_sessions"] == 4 and report["median_bps"] == 2.5
    assert report["at_or_below_close"] == 0.5 and report["within_10bps"] == 0.75


def test_ex_rights_rows_become_cash_dividends():
    events = parse_twse_ex_rights({"stat": "OK", "data": [EX_RIGHTS_ROW]}, {"0050"})

    assert roc_long_date("94年05月19日") == date(2005, 5, 19)
    assert events[0].day == date(2005, 5, 19) and events[0].cash == 1.85 and events[0].kind == "息"
    assert parse_twse_ex_rights({"stat": "OK", "data": [EX_RIGHTS_ROW]}, {"0056"}) == []


def test_total_return_reinvests_cash_and_multiplies_units_on_splits():
    rows = [
        {"date": date(2025, 6, 10), "close": 100.0},
        {"date": date(2025, 6, 11), "close": 98.0},
        {"date": date(2025, 6, 18), "close": 25.0},
    ]
    actions = [
        CorporateAction(date(2025, 6, 11), "0050", "cash_dividend", cash=3.0),
        CorporateAction(date(2025, 6, 18), "0050", "split", ratio=4.0),
    ]

    series = total_return(rows, actions)

    assert series[0]["total_return_index"] == 100.0
    assert series[1]["total_return_index"] == pytest.approx(101.0)  # (98 + 3) / 100
    assert series[2]["total_return_index"] == pytest.approx(101.0 * 25 * 4 / 98)
    assert series[2]["unit_ratio"] == 4.0


def test_split_markers_give_whole_ratios():
    rows = [
        {"date": date(2025, 6, 10), "close": 188.65, "open": 188.0, "note": ""},
        {"date": date(2025, 6, 18), "close": 47.6, "open": 47.3, "note": "**"},
        {"date": date(2025, 6, 19), "close": 47.9, "open": 47.6, "note": ""},
    ]

    actions = splits_from_markers("0050", rows)

    assert [(item.day, item.ratio) for item in actions] == [(date(2025, 6, 18), 4.0)]
    assert nice_ratio(0.49) == 0.5 and nice_ratio(3.97) == 4.0


def test_total_return_matches_an_adjusted_close_with_a_flat_ratio():
    days = [date(2020, 1, day) for day in (2, 3, 6)]
    tr = dict(zip(days, (100.0, 101.0, 103.02)))
    adjusted = dict(zip(days, (50.0, 50.5, 51.51)))

    report = compare_total_return(tr, adjusted)

    assert report["common_days"] == 3
    assert report["end_drift"] == 0 and report["max_abs_deviation"] == 0


def test_build_actions_writes_total_return_files(tmp_path):
    catalog = (
        HistorySeries("0050", "元大台灣50", "twse_etf", date(2005, 5, 1), "0050.TW"),
        HistorySeries("00679B", "元大美債20年", "tpex_etf", date(2005, 5, 1), "00679B.TWO"),
    )
    for key in ("0050", "00679B"):
        write_parquet(
            [
                DailyRow(date(2005, 5, 18), 46.5, 46.8, 46.4, 46.69, source="test"),
                DailyRow(date(2005, 5, 19), 44.9, 45.1, 44.7, 45.00, source="test"),
            ],
            tmp_path / "daily" / f"{key}.parquet",
        )

    tpex_row = ["94/05/19", "00679B", "元大美債20年", "46.69", "46.19", "0.000000", "0.500000",
                "0.500000", "除息", "", "", "", "", "0.5", "0", "0", "0", "0", "0", "0", "0"]

    def exchange(url, data=None):
        if "TWT49U" in url:
            return {"stat": "OK", "data": [EX_RIGHTS_ROW] if "2005" in url else []}
        assert url.endswith("/bulletin/exDailyQ") and b"startDate=2005" in data or b"2006" in data
        return {"stat": "ok", "tables": [{"data": [tpex_row] if b"2005" in data else []}]}

    def yahoo(url):
        stamp = int(datetime(2005, 5, 19, 9, 0, tzinfo=TAIPEI).timestamp())
        return {"chart": {"result": [{"events": {"dividends": {str(stamp): {"date": stamp, "amount": 0.5}}}}]}}

    client = OfficialHistoryClient(
        tmp_path / "raw", fetch_json=exchange, min_interval=0, today=lambda: date(2006, 1, 10),
        sleep=lambda _seconds: None,
    )
    report = build_actions(tmp_path, client, catalog, yahoo_fetch=yahoo, today=lambda: date(2006, 1, 10))

    assert report["series"]["0050"]["cash_dividends"] == 1
    assert report["series"]["0050"]["sources"] == ["twse_ex_rights"]
    assert report["series"]["00679B"]["sources"] == ["tpex_ex_rights"]
    assert report["series"]["00679B"]["yahoo_dividend_check"]["amount_mismatches"] == []
    assert report["series"]["0050"]["yahoo_dividend_check"]["amount_mismatches"] == ["2005-05-19"]
    tr = read_series(tmp_path / "total_return" / "0050.parquet")
    assert tr[1]["total_return_index"] == pytest.approx(100 * (45.00 + 1.85) / 46.69)
    assert (tmp_path / "actions.json").is_file()
