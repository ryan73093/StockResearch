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
