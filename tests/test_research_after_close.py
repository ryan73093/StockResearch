"""2026-10-14 (使用者：要在 14:30 前算出建議): today's orders from TWSE's table before the stock files are written."""
import json
from datetime import date, datetime, time, timedelta

from quant_platform.research import after_close
from quant_platform.research.daily import DailyRule
from quant_platform.research.history.dataset import write_parquet
from quant_platform.research.history.official import DailyRow
from quant_platform.research.history.stocks import write_year
from quant_platform.research.stock_forward import TAIPEI

RULE = DailyRule(name="每天 3 個月動能：前 3 名", factors={"momentum_3": 1.0}, top=3, min_hold=0)


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def price(number, i):
    return round(20 * (1 + 0.0004 * (number - 2)) ** i + (0.3 if (i + number) % 7 == 0 else 0.0), 2)


def build(history, days):
    write_parquet([DailyRow(day, 100.0, 100.0, 100.0, 100.0, source="t") for day in days], history / "daily" / "TAIEX.parquet")
    write_parquet([DailyRow(day, 50.0, 50.0, 50.0, 50.0, source="t") for day in days], history / "daily" / "0050.parquet")
    by_year = {}
    for i, day in enumerate(days):
        for number in range(6):
            close = price(number, i)
            by_year.setdefault(day.year, []).append({
                "date": day, "code": str(1101 + number), "name": f"公司{number}", "open": close, "high": close,
                "low": close, "close": close, "volume": 3_000_000, "turnover": 60_000_000_000, "trades": 100})
    for year, rows in by_year.items():
        write_year(rows, history / "stocks" / "twse" / f"{year}.parquet")


def table(day, index):
    """TWSE's MI_INDEX answer for ``day``: the index table and the quotes (0050 and the six stocks)."""
    quotes = [["0050", "元大台灣50", "1,000", "10", "50,000", "50.00", "50.00", "50.00", "50.00"]]
    for number in range(6):
        close = f"{price(number, index):.2f}"
        quotes.append([str(1101 + number), f"公司{number}", "3,000,000", "100", "60,000,000,000", close, close, close, close])
    return {"stat": "OK", "tables": [
        {"title": "價格指數(臺灣證券交易所)", "fields": ["指數", "收盤指數"], "data": [["發行量加權股價指數", "100.00"]]},
        {"title": "每日收盤行情", "fields": ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價",
                                            "最低價", "收盤價"], "data": quotes}]}


def track(research):
    folder = research / "forward" / "stocks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tracked.json").write_text(json.dumps([{
        "rule_hash": RULE.rule_hash, "name": RULE.name, "rule": RULE.canonical(), "kind": "daily", "plan": "seed",
        "since": "2026-10-01"}], ensure_ascii=False), encoding="utf-8")


def test_the_table_gives_the_stocks_0050_and_taiex():
    rows, benchmark, taiex = after_close.today_table(table(date(2026, 10, 13), 5), date(2026, 10, 13))
    assert len(rows) == 6 and benchmark == 50.0 and taiex == 100.0                # 0050 is not a stock row


def test_todays_orders_from_the_table_equal_the_orders_once_the_files_have_today(tmp_path):
    days = weekdays(date(2025, 1, 1), date(2026, 10, 13))
    today = days[-1]
    early, late = tmp_path / "early", tmp_path / "late"
    build(early / "history", days[:-1])                  # 13:50: the files end yesterday
    build(late / "history", days)                        # after 15:14: today is in the files
    for research in (early, late):
        track(research)
    moment = datetime.combine(today, time(13, 52), tzinfo=TAIPEI)
    first = after_close.prepare(early / "history", early, today, table(today, len(days) - 1), now=moment, min_rows=1)
    second = after_close.prepare(late / "history", late, today, table(today, len(days) - 1), now=moment, min_rows=1)
    assert first["finished_at"] and first["done"] == first["total"] == 1
    assert first["rules"][0]["holdings"] == second["rules"][0]["holdings"] and first["rules"][0]["holdings"]
    assert first["rules"][0]["orders"] == second["rules"][0]["orders"]
    assert after_close.load(early, today)["rules"][0]["name"] == RULE.name
    assert after_close.prepare(early / "history", early, today, {"stat": "OK", "tables": []}, min_rows=1) is None   # not out yet


def test_an_order_is_round_lots_at_the_close_and_the_odd_lot_with_a_limit():
    entry = {"symbol": "2330.TW", "side": "BUY", "shares": 1_234, "price": 1_005.0, "fee": 1_767, "tax": 0}
    row = after_close.order_lines(entry, 1_000.0, 990.0, {"2330": "台積電"})
    lots, odd = row["parts"]
    assert lots["shares"] == 1_000 and lots["price"] == 1_000.0 and "盤後定價" in lots["session"]
    assert odd["shares"] == 234 and odd["limit"] == 1_010.0 and "盤後零股" in odd["session"]
    refused = after_close.order_lines({"symbol": "2330.TW", "side": "BLOCKED", "order": "BUY", "reason": "漲停買不到"},
                                      1_085.0, 990.0, {})
    assert refused["refused"] == "漲停買不到" and "parts" not in refused


def test_todays_decisions_are_kept_for_the_record_and_the_board(tmp_path):
    """2026-10-15 (research/decisions.py): the 13:50 orders write down today's decisions, so the 15:30 record and
    the board hold what the orders said, whatever data comes later."""
    from quant_platform.research.decisions import DecisionStore, key

    days = weekdays(date(2025, 1, 1), date(2026, 10, 13))
    today = days[-1]
    build(tmp_path / "history", days[:-1])
    track(tmp_path)
    after_close.prepare(tmp_path / "history", tmp_path, today, table(today, len(days) - 1),
                        now=datetime.combine(today, time(13, 52), tzinfo=TAIPEI), min_rows=1)
    kept = DecisionStore(tmp_path).fixed(key(RULE.rule_hash, date(2026, 10, 1)))
    assert kept["through"] == today and kept["days"][date(2026, 10, 1)]           # every day since the common start
    assert len(kept["days"][today]) == 3



def test_the_table_the_orders_read_is_kept_and_compared_with_the_days_data(tmp_path):
    """2026-10-27 (forward audit: 10/27 decisions differed from the ranking on the 15:16 data; the 13:50 table had been
    overwritten): the orders' table is kept, and the stocks whose numbers changed afterwards are listed."""
    days = weekdays(date(2025, 1, 1), date(2026, 10, 13))
    today = days[-1]
    build(tmp_path / "history", days)
    track(tmp_path)
    early = table(today, len(days) - 1)
    early["tables"][1]["data"][1][4] = "40,000,000,000"          # 1101's turnover at 13:50, lower than the day's data
    del early["tables"][1]["data"][6]                               # 1106 not in the table yet
    after_close.prepare(tmp_path / "history", tmp_path, today, early,
                        now=datetime.combine(today, time(13, 52), tzinfo=TAIPEI), min_rows=1)
    assert (tmp_path / after_close.FOLDER / f"{today.isoformat()}.table.json").is_file()
    found = after_close.revisions(tmp_path, tmp_path / "history", today)
    assert set(found) == {"1101"} and found["1101"]["orders"]["turnover"] == 40_000_000_000
    assert found["1101"]["data"]["turnover"] == 60_000_000_000
    assert after_close.revisions(tmp_path, tmp_path / "history", today, {"1102.TW", "1106.TW"}) == {
        "1106": {"orders": None, "data": {"close": price(5, len(days) - 1), "volume": 3_000_000, "turnover": 60_000_000_000}}}
    assert after_close.revisions(tmp_path, tmp_path / "history", days[-2]) is None          # no table kept that day


def test_a_record_waits_for_todays_orders_that_are_still_being_computed(tmp_path):
    """2026-10-29: the orders keep today's decisions only at their end; the forward record and the board wait for them
    (10-29: 3,607 accounts' orders ran past 14:30; the 15:30 record does not wait on its own). A report not written for
    a while is a run that died; no report, or a finished one: no wait."""
    import os
    import threading
    import time as clock

    day = date(2026, 10, 29)
    assert after_close.wait_for_orders(tmp_path, day, pause=0.05) is False             # no orders today
    folder = tmp_path / after_close.FOLDER
    folder.mkdir(parents=True)
    path = folder / f"{day.isoformat()}.json"
    path.write_text(json.dumps({"done": 10, "total": 20, "finished_at": None}), encoding="utf-8")

    def finish():
        clock.sleep(0.3)
        path.write_text(json.dumps({"done": 20, "total": 20, "finished_at": "2026-10-29T14:35:00+08:00"}), encoding="utf-8")
    writer = threading.Thread(target=finish)
    began = clock.monotonic()
    writer.start()
    assert after_close.wait_for_orders(tmp_path, day, pause=0.05) is True
    writer.join()
    assert clock.monotonic() - began >= 0.25
    assert after_close.wait_for_orders(tmp_path, day, pause=0.05) is False             # finished: no wait
    path.write_text(json.dumps({"done": 10, "total": 20, "finished_at": None}), encoding="utf-8")
    old = clock.time() - 3600
    os.utime(path, (old, old))                                                          # not written for an hour
    assert after_close.wait_for_orders(tmp_path, day, pause=0.05, alive=600) is False
