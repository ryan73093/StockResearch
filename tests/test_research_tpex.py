"""R6 (2026-10-05): TPEx stocks in the research data, as an optional universe."""

import gzip
import json
from datetime import date, datetime, time, timedelta

import pyarrow.parquet as pq

from quant_platform.research.daily import DailyRule, risk_batch, tpex_batch
from quant_platform.research.history.dataset import write_parquet
from quant_platform.research.history.finmind import path_for, tpex_codes
from quant_platform.research.history.official import DailyRow
from quant_platform.research.history.stocks import build_tpex_from_finmind, write_year
from quant_platform.research.prices import ResearchPrices
from quant_platform.research.stock_forward import TAIPEI, StockForwardTracker
from quant_platform.research.stock_rules import load_stock_data, stock_fingerprint


def quote(day, code, close, name="x", turnover=60_000_000):
    return {"date": day, "code": code, "name": name, "open": close, "high": close, "low": close, "close": close,
            "volume": 1_000, "turnover": turnover, "trades": 10}


def finmind(base, code, rows):
    path = path_for(base, "TaiwanStockPrice", code)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps({"dataset": "TaiwanStockPrice", "code": code, "data": rows}).encode()))


def price_row(day, close, volume=1_000):
    return {"date": day, "Trading_Volume": volume, "Trading_money": int(close * volume), "open": close, "max": close,
            "min": close, "close": close, "Trading_turnover": 5}


def setup(base):
    """3092 traded on TPEx and moved to TWSE on 2021-05-13; 6488 is a TPEx stock; 9951 a 99xx TPEx stock."""
    info = {"data": [{"stock_id": code, "stock_name": name, "type": "tpex", "industry_category": "半導體業"}
                     for code, name in (("3092", "鴻碩"), ("6488", "環球晶"), ("9951", "皇田"), ("9105", "TDR"))]}
    (base / "raw" / "finmind").mkdir(parents=True)
    (base / "raw" / "finmind" / "TaiwanStockInfo.json").write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    write_year([quote(date(2021, 5, 13), "3092", 10.0), quote(date(2021, 5, 14), "3092", 10.2)],
               base / "stocks" / "twse" / "2021.parquet")
    finmind(base, "3092", [price_row("2021-05-11", 9.0), price_row("2021-05-12", 9.6),
                           price_row("2021-05-13", 10.0)])                       # FinMind also has the TWSE days
    finmind(base, "6488", [price_row("2021-05-11", 500.0), price_row("2021-05-12", 0.0),   # no trade
                           price_row("2021-05-13", 505.0, volume=0), price_row("2021-05-14", 510.0)])
    days = [date(2021, 5, 11), date(2021, 5, 12), date(2021, 5, 13), date(2021, 5, 14)]
    for name in ("TAIEX", "0050"):
        write_parquet([DailyRow(day, 1.0, 1.0, 1.0, 100.0, source="t") for day in days], base / "daily" / f"{name}.parquet")
    events = {"tables": [{"fields": ["除權息日期", "代號", "權/息", "除權息前收盤價", "除權息參考價"],
                          "data": [["110/05/12", "3092", "除息", "10.00", "9.50"]]}]}
    (base / "raw" / "tpex_ex_rights").mkdir(parents=True)
    (base / "raw" / "tpex_ex_rights" / "2021.json").write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")


def test_tpex_years_from_finmind_leave_out_no_trade_days_and_the_days_twse_has(tmp_path):
    setup(tmp_path)
    assert tpex_codes(tmp_path) == ["3092", "6488", "9951"]          # 99xx is a common stock, 91xx a TDR
    result = build_tpex_from_finmind(tmp_path)
    assert result["codes"] == 2 and result["years"] == [2021]
    rows = pq.read_table(tmp_path / "stocks" / "tpex" / "2021.parquet").to_pylist()
    assert [(row["code"], row["date"].isoformat(), row["close"]) for row in rows] == [
        ("3092", "2021-05-11", 9.0), ("6488", "2021-05-11", 500.0), ("3092", "2021-05-12", 9.6),
        ("6488", "2021-05-14", 510.0)]
    assert rows[1]["name"] == "環球晶" and rows[1]["turnover"] == 500_000


def test_universe_all_adds_tpex_and_joins_a_stock_that_moved_to_twse(tmp_path):
    setup(tmp_path)
    build_tpex_from_finmind(tmp_path)
    listed = load_stock_data(tmp_path, 2021, 2021)
    assert set(listed.closes) == {"3092.TW", "0050.TW"} and min(listed.closes["3092.TW"]) == date(2021, 5, 13)
    both = load_stock_data(tmp_path, 2021, 2021, universe="all")
    assert set(both.closes) == {"3092.TW", "6488.TWO", "0050.TW"}
    assert sorted(both.closes["3092.TW"]) == [date(2021, 5, 11), date(2021, 5, 12), date(2021, 5, 13), date(2021, 5, 14)]
    # its TPEx ex-dividend before the move carries over to the one series: units × 10 / 9.5
    assert abs(both.factors["3092.TW"][date(2021, 5, 12)] - 10 / 9.5) < 1e-9
    assert both.notes["universe"] == "all"


def test_the_listed_fingerprint_does_not_change_when_tpex_data_arrives(tmp_path):
    setup(tmp_path)
    before = stock_fingerprint(tmp_path, 2021, 2021)
    build_tpex_from_finmind(tmp_path)
    assert stock_fingerprint(tmp_path, 2021, 2021) == before
    wide = stock_fingerprint(tmp_path, 2021, 2021, universe="all")
    assert wide != before
    write_year([quote(date(2021, 5, 11), "6488", 499.0)], tmp_path / "stocks" / "tpex" / "2021.parquet")
    assert stock_fingerprint(tmp_path, 2021, 2021, universe="all") != wide


def test_universe_is_part_of_the_rule_only_when_it_is_not_the_default():
    rule = DailyRule(name="x", factors={"trend_200": 1.0})
    assert "universe" not in rule.canonical()
    assert DailyRule(name="x", factors={"trend_200": 1.0}, universe="twse").rule_hash == rule.rule_hash
    assert DailyRule(name="x", factors={"trend_200": 1.0}, universe="all").rule_hash != rule.rule_hash
    batch = tpex_batch()
    assert len(batch) == 6 and all(item.universe == "all" and "上市＋上櫃" in item.name for item in batch)
    assert [item.model_copy(update={"universe": "twse", "name": "x"}).rule_hash for item in batch] == [
        item.model_copy(update={"name": "x"}).rule_hash for item in risk_batch()[:6]]


def test_research_prices_read_tpex_stocks(tmp_path):
    setup(tmp_path)
    build_tpex_from_finmind(tmp_path)
    after = datetime.combine(date(2021, 5, 17), time(16), TAIPEI)
    assert ResearchPrices(tmp_path).history("6488.TWO", after) == [(date(2021, 5, 11), 500.0), (date(2021, 5, 14), 510.0)]


def test_forward_daily_rule_on_both_markets_holds_tpex_stocks_and_the_listed_rule_does_not(tmp_path):
    days, day = [], date(2024, 1, 1)
    while day <= date(2026, 10, 9):
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    history = tmp_path / "history"
    write_parquet([DailyRow(day, 100.0, 100.0, 100.0, 100.0, source="t") for day in days], history / "daily" / "TAIEX.parquet")
    write_parquet([DailyRow(day, 50.0, 50.0, 50.0, 50.0 + i * 0.01, source="t") for i, day in enumerate(days)],
                  history / "daily" / "0050.parquet")
    listed, tpex = {}, {}
    for i, day in enumerate(days):
        for number in range(4):
            listed.setdefault(day.year, []).append(quote(day, str(1101 + number), round(20 * (1 + 0.0002 * number) ** i, 2)))
        tpex.setdefault(day.year, []).append(quote(day, "6488", round(20 * 1.002 ** i, 2)))     # the strongest trend
    for year, rows in listed.items():
        write_year(rows, history / "stocks" / "twse" / f"{year}.parquet")
        write_year(tpex[year], history / "stocks" / "tpex" / f"{year}.parquet")
    items = []
    for universe in ("twse", "all"):
        rule = DailyRule(name=f"動能 {universe}", factors={"momentum_3": 1.0}, top=3, universe=universe)
        items.append({"rule_hash": rule.rule_hash, "name": rule.name, "rule": rule.model_dump(mode="json"), "kind": "daily",
                      "plan": "seed", "initial": 300_000, "monthly": 10_000, "since": "2026-10-01", "reason": "測試"})
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    (folder / "tracked.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    tracker = StockForwardTracker(tmp_path, min_quotes=1)
    written = tracker.record(date(2026, 10, 2), now=datetime.combine(date(2026, 10, 2), time(16), TAIPEI))
    held = {record["name"]: {item["code"] for item in record["holdings"]} for record in written}
    assert "6488" not in held["動能 twse"] and "6488" in held["動能 all"]
    assert written[0]["data_fingerprint"] != written[-1]["data_fingerprint"]


def test_a_period_fingerprint_ignores_what_the_nightly_refresh_adds_after_it(tmp_path):
    """2026-10-05: 0050 and this year's ex-rights file are rewritten every night; a period's version must not move."""
    setup(tmp_path)
    build_tpex_from_finmind(tmp_path)
    end = date(2021, 5, 13)
    listed, wide = stock_fingerprint(tmp_path, 2021, 2021, until=end), stock_fingerprint(tmp_path, 2021, 2021, end, "all")
    days = [date(2021, 5, 11), date(2021, 5, 12), date(2021, 5, 13), date(2021, 5, 14), date(2021, 5, 17)]
    write_parquet([DailyRow(day, 1.0, 1.0, 1.0, 100.0, source="t") for day in days], tmp_path / "daily" / "0050.parquet")
    path = tmp_path / "raw" / "tpex_ex_rights" / "2021.json"
    events = json.loads(path.read_text(encoding="utf-8"))
    events["tables"][0]["data"].append(["110/05/17", "6488", "除息", "510.00", "505.00"])      # after the period
    path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    assert stock_fingerprint(tmp_path, 2021, 2021, until=end) == listed
    assert stock_fingerprint(tmp_path, 2021, 2021, end, "all") == wide
    events["tables"][0]["data"][0][4] = "9.40"                                                 # a revision inside it
    path.write_text(json.dumps(events, ensure_ascii=False), encoding="utf-8")
    assert stock_fingerprint(tmp_path, 2021, 2021, end, "all") != wide
    assert stock_fingerprint(tmp_path, 2021, 2021, until=end) == listed                       # TPEx is not in the listed one
