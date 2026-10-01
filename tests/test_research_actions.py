from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from quant_platform.research.history.actions import (
    CorporateAction,
    actions_from_ex_rights,
    build_actions,
    nice_ratio,
    reference_mismatches,
    splits_from_markers,
    total_return,
)
from quant_platform.research.history.catalog import HistorySeries
from quant_platform.research.history.crosscheck import compare_total_return
from quant_platform.research.history.dataset import read_series, write_parquet
from quant_platform.research.history.official import (
    DailyRow,
    ExRightsLayoutError,
    OfficialHistoryClient,
    parse_tpex_ex_rights,
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


# TWT49U from 2009 on: 權值 and 息值 are gone, only 權值+息值 and the 權/息 flag remain.
SHORT_FIELDS = [
    "資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價", "權值+息值", "權/息", "漲停價格",
    "跌停價格", "開盤競價基準", "減除股利參考價", "詳細資料", "最近一次申報資料 季別/日期",
    "最近一次申報每股 (單位)淨值", "最近一次申報每股 (單位)盈餘",
]
SHORT_ROW = [
    "99年10月25日", "0050", "元大台灣50", "57.10", "54.90", "2.200000", "息", "58.70", "51.10",
    "54.90", "54.90", "0050,20101025", "", "", "",
]


def test_short_twt49u_layout_is_read_by_header():
    """The 2010 0050 dividend of 2.20 is cash, not a 4% stock dividend (the bug before this fix)."""
    events = parse_twse_ex_rights({"stat": "OK", "fields": SHORT_FIELDS, "data": [SHORT_ROW]}, {"0050"})

    assert len(events) == 1
    event = events[0]
    assert (event.day, event.cash, event.rights_value) == (date(2010, 10, 25), 2.2, 0.0)
    actions = actions_from_ex_rights(events)
    assert [(item.kind, item.cash) for item in actions] == [("cash_dividend", 2.2)]
    assert reference_mismatches(actions) == []
    # A cached payload without "fields" is recognised by its row length.
    assert parse_twse_ex_rights({"stat": "OK", "data": [SHORT_ROW]}, {"0050"})[0].cash == 2.2


def test_short_layout_stock_only_and_combined_rows():
    stock_row = [*SHORT_ROW[:5], "1.000000", "權", *SHORT_ROW[7:]]
    events = parse_twse_ex_rights({"stat": "OK", "fields": SHORT_FIELDS, "data": [stock_row]}, {"0050"})
    assert (events[0].rights_value, events[0].cash) == (1.0, 0.0)

    combined = [*SHORT_ROW[:5], "3.200000", "權息", *SHORT_ROW[7:]]
    with pytest.raises(ExRightsLayoutError, match="同時除權與除息"):
        parse_twse_ex_rights({"stat": "OK", "fields": SHORT_FIELDS, "data": [combined]}, {"0050"})
    with pytest.raises(ExRightsLayoutError, match="表頭"):
        parse_twse_ex_rights({"stat": "OK", "fields": ["日期", "代號"], "data": [SHORT_ROW]}, {"0050"})


def test_tpex_rounding_residue_in_rights_column_is_ignored():
    fields = ["除權息日期", "代號", "名稱", "除權息前收盤價", "除權息參考價", "權值", "息值", "權值+息值", "權/息",
              "漲停價", "跌停價", "開始交易基準價", "減除股利參考價", "現金股利"]
    row = ["107/11/22", "00679B", "元大美債20年", "36.70", "36.46", "-0.01", "0.24500000", "0.24", "除息",
           "9999.95", "0.01", "36.46", "36.46", "0.24500000"]

    events = parse_tpex_ex_rights({"stat": "ok", "tables": [{"fields": fields, "data": [row]}]}, {"00679B"})

    assert (events[0].day, events[0].cash, events[0].rights_value) == (date(2018, 11, 22), 0.245, 0.0)
    assert [item.kind for item in actions_from_ex_rights(events)] == ["cash_dividend"]


def test_reference_mismatch_flags_a_misread_column():
    good = CorporateAction(date(2010, 10, 25), "0050", "cash_dividend", cash=2.2, pre_close=57.10, reference=54.90)
    wrong = CorporateAction(date(2011, 7, 26), "0050", "cash_dividend", cash=0.0, pre_close=59.0, reference=57.05)

    assert [item["date"] for item in reference_mismatches([good, wrong])] == ["2011-07-26"]


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


def test_yahoo_dividend_check_divides_pre_split_dividends_by_the_split(tmp_path):
    """Yahoo shows 0050's 2025-01-17 dividend of 2.70 as 0.675 after the 1→4 split."""
    catalog = (HistorySeries("0050", "元大台灣50", "twse_etf", date(2025, 1, 1), "0050.TW"),)
    write_parquet(
        [
            DailyRow(date(2025, 1, 16), 200.0, 200.0, 199.0, 199.4, source="test"),
            DailyRow(date(2025, 1, 17), 197.0, 198.0, 196.0, 197.0, source="test"),
            DailyRow(date(2025, 6, 10), 188.0, 189.0, 187.0, 188.0, source="test"),
            DailyRow(date(2025, 6, 18), 47.0, 47.8, 46.9, 47.6, note="**", source="test"),
        ],
        tmp_path / "daily" / "0050.parquet",
    )
    row = [*SHORT_ROW[:5], "2.700000", "息", *SHORT_ROW[7:]]
    row[0], row[3], row[4] = "114年01月17日", "199.40", "196.70"

    def exchange(url, data=None):
        return {"stat": "OK", "fields": SHORT_FIELDS, "data": [row] if "2025" in url else []}

    def yahoo(url):
        stamp = int(datetime(2025, 1, 17, 9, 0, tzinfo=TAIPEI).timestamp())
        return {"chart": {"result": [{"events": {"dividends": {str(stamp): {"date": stamp, "amount": 0.675}}}}]}}

    client = OfficialHistoryClient(
        tmp_path / "raw", fetch_json=exchange, min_interval=0, today=lambda: date(2025, 12, 31),
        sleep=lambda _seconds: None,
    )
    report = build_actions(tmp_path, client, catalog, yahoo_fetch=yahoo, today=lambda: date(2025, 12, 31))

    entry = report["series"]["0050"]
    assert entry["splits"] == [{"date": "2025-06-18", "ratio": 4.0}]
    assert entry["yahoo_dividend_check"]["amount_mismatches"] == []
    assert entry["reference_mismatches"] == []
