import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from quant_platform.application.dividends import DividendCalendar, ExDividend, account_view, parse_tpex, parse_twse
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.domain.entities import ActualCashFlow, ActualTrade

TWSE_FIELDS = ["除權除息日期", "股票代號", "名稱", "除權息", "無償配股率", "現金增資配股率", "現金增資認購價",
               "現金股利", "詳細資料", "參考價<br>試算"]
PENDING = "<p style= text-align:center;>待公告實際收益分配金額</p>"


def twse(*rows):
    return {"stat": "OK", "fields": TWSE_FIELDS, "data": [list(row) + ["", ""] for row in rows]}


def tpex(*rows):
    return [{"ExRrightsExDividendDate": day, "SecuritiesCompanyCode": code, "CompanyName": name,
             "ExRrightsExDividend": kind, "CashDividend": cash} for day, code, name, kind, cash in rows]


def test_parse_both_exchanges():
    listed = parse_twse(twse(
        ("115年10月16日", "0056", "元大高股息", "息", "0", "0", "0", "1.07000000"),
        ("115年10月19日", "00878", "國泰永續高股息", "息", "0", "0", "0", PENDING),
        ("115年10月20日", "2330", "台積電", "權", "0.1", "0", "0", "0.00000000"),
    ))
    otc = parse_tpex(tpex(
        ("1151015", "00679B", "元大美債20年", "除息", "0.38200000"),
        ("1151105", "00687B", "國泰20年美債", "除息", "尚未公告"),
        ("1150922", "8042", "金山電", "除權", "0.00000000"),
    ))

    assert listed == [
        ExDividend("0056", "元大高股息", date(2026, 10, 16), 1.07, "TWSE"),
        ExDividend("00878", "國泰永續高股息", date(2026, 10, 19), None, "TWSE"),   # amount not announced yet
    ]
    assert otc == [
        ExDividend("00679B", "元大美債20年", date(2026, 10, 15), 0.382, "TPEx"),
        ExDividend("00687B", "國泰20年美債", date(2026, 11, 5), None, "TPEx"),
    ]


def test_refresh_keeps_history_fills_in_amounts_and_drops_moved_events(tmp_path):
    answers = {}
    clock = [datetime(2026, 10, 1, 1, tzinfo=UTC)]
    calendar = DividendCalendar(tmp_path / "events.json", fetch=lambda url: answers[url], clock=lambda: clock[0])
    from quant_platform.application.dividends import TPEX_URL, TWSE_URL

    answers[TWSE_URL] = twse(("115年09月20日", "0050", "元大台灣50", "息", "0", "0", "0", "1.00000000"),
                             ("115年10月19日", "00878", "國泰永續高股息", "息", "0", "0", "0", PENDING),
                             ("115年10月30日", "0056", "元大高股息", "息", "0", "0", "0", PENDING))
    answers[TPEX_URL] = tpex(("1151015", "00679B", "元大美債20年", "除息", "尚未公告"))
    assert calendar.refresh() == {"added": 4, "updated": 0, "removed": 0, "count": 4, "errors": []}
    assert calendar.updated_at() == clock[0]
    assert calendar.refresh_if_due() is None                                # fresh for 12 hours

    # Next day: 0050 (past) left the table, 00878 is announced, 0056 moved to 11/02, TPEx is down.
    clock[0] += timedelta(days=1)
    answers[TWSE_URL] = twse(("115年10月19日", "00878", "國泰永續高股息", "息", "0", "0", "0", "0.40000000"),
                             ("115年11月02日", "0056", "元大高股息", "息", "0", "0", "0", PENDING))
    answers[TPEX_URL] = None                                                # parse → no rows: not an answer
    result = calendar.refresh_if_due()
    assert result["added"] == 1 and result["updated"] == 1 and result["removed"] == 1

    events = {(event.symbol, event.ex_date): event.cash for event in calendar.events()}
    assert events == {
        ("0050", date(2026, 9, 20)): 1.0,          # past events stay for the reminder
        ("00679B", date(2026, 10, 15)): None,      # TPEx did not answer: nothing removed
        ("00878", date(2026, 10, 19)): 0.4,        # amount filled in
        ("0056", date(2026, 11, 2)): None,         # moved from 10/30
    }

    answers[TWSE_URL] = twse(("115年10月19日", "00878", "國泰永續高股息", "息", "0", "0", "0", PENDING))
    clock[0] += timedelta(days=1)
    calendar.refresh()
    assert {event.symbol: event.cash for event in calendar.events()}["00878"] == 0.4   # never un-announced
    assert json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))["events"][0]["symbol"] == "0050"


def trade(day, symbol, side, shares):
    return ActualTrade(None, day, symbol, side, shares, Decimal("30"), 1, 0)


def dividend(day, symbol, amount):
    return ActualCashFlow(None, day, "dividend", Decimal(amount), symbol, "")


def test_account_view_by_hand():
    events = [
        ExDividend("0056", "元大高股息", date(2026, 9, 16), 0.866, "TWSE"),
        ExDividend("0056", "元大高股息", date(2026, 10, 15), 0.8, "TWSE"),
        ExDividend("00679B", "元大美債20年", date(2026, 10, 20), None, "TPEx"),
        ExDividend("0056", "元大高股息", date(2026, 11, 14), None, "TWSE"),
        ExDividend("0050", "元大台灣50", date(2026, 10, 1), 1.0, "TWSE"),        # never held
        ExDividend("0056", "元大高股息", date(2026, 6, 1), 1.0, "TWSE"),          # before the first fill
    ]
    trades = [
        trade(date(2026, 9, 1), "0056", "BUY", 1_000),
        trade(date(2026, 9, 16), "0056", "BUY", 500),     # bought on the ex-date: not entitled that time
        trade(date(2026, 10, 2), "00679B", "BUY", 200),
    ]

    view = account_view(events, [], trades, today=date(2026, 10, 25))

    # 09-16: 1,000 units × 0.866 = 866; 10-15: 1,500 × 0.8 = 1,200; 00679B 10-20: amount not announced.
    assert [(line["symbol"], line["ex_date"], line["units"], line["estimate"]) for line in view["due"]] == [
        ("0056", date(2026, 9, 16), 1_000, 866),
        ("0056", date(2026, 10, 15), 1_500, 1_200),
        ("00679B", date(2026, 10, 20), 200, None),
    ]
    assert [(line["symbol"], line["units"], line["estimate"]) for line in view["upcoming"]] == [("0056", 1_500, None)]

    # A dividend recorded on 10-14 settles the September payment only (the earliest open one).
    later = account_view(events, [dividend(date(2026, 10, 14), "0056", "866")], trades, today=date(2026, 10, 25))
    assert [line["ex_date"] for line in later["due"] if line["symbol"] == "0056"] == [date(2026, 10, 15)]

    from quant_platform.application.dividends import per_unit_text

    assert [per_unit_text(value) for value in (1.0, 0.866, 0.382, 1.07, None)] == ["1.00", "0.866", "0.382", "1.07", "待公告"]

    # The reminder lasts 90 days after the ex-date (the last one is 11-14).
    assert len(account_view(events, [], trades, today=date(2027, 2, 12))["due"]) == 1
    assert account_view(events, [], trades, today=date(2027, 2, 13))["due"] == []


def test_holdings_page_offers_to_record_the_dividend(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    today = date.today()
    ex_date, next_ex = today - timedelta(days=10), today + timedelta(days=20)
    calendar = DividendCalendar(
        tmp_path / "events" / "ex_dividends.json",
        fetch=lambda url: twse((f"{ex_date.year - 1911}年{ex_date:%m月%d日}", "0056", "元大高股息", "息", "0", "0", "0",
                                "0.86600000"),
                               (f"{next_ex.year - 1911}年{next_ex:%m月%d日}", "0056", "元大高股息", "息", "0", "0", "0",
                                PENDING)) if "twse" in url else [],
    )
    calendar.refresh()
    client = create_app(container).test_client()
    client.post("/holdings/cash", data={"kind": "deposit", "day": (today - timedelta(days=30)).isoformat(), "amount": "50000"})
    client.post("/holdings/trade", data={"day": (today - timedelta(days=30)).isoformat(), "symbol": "0056",
                                         "side": "BUY", "shares": "1000", "price": "38"})

    body = client.get("/holdings").get_data(as_text=True)
    assert "待記錄 1 筆" in body and "已除息，待記錄" in body and "即將除息" in body and "待公告" in body
    assert "/holdings?kind=dividend&amp;symbol=0056&amp;amount=866&amp;note=" in body

    prefilled = client.get("/holdings?kind=dividend&symbol=0056&amount=866&note=x").get_data(as_text=True)
    assert "已帶入預估股利" in prefilled and 'name="amount" inputmode="decimal" value="866"' in prefilled
    assert '<option value="dividend" selected' in prefilled

    container.investment_plan_service.save({"monthly_amount": "10000", "salary_day": "5", "strategy_key": "benchmark_dca",
                                            "max_drawdown_tolerance": "30"})
    assert "有 1 筆已除息的股利還沒記錄" in client.get("/").get_data(as_text=True)   # Today's "需要留意"

    client.post("/holdings/cash", data={"kind": "dividend", "day": today.isoformat(), "amount": "856", "symbol": "0056"})
    assert "待記錄" not in client.get("/holdings").get_data(as_text=True)
    assert "股利還沒記錄" not in client.get("/").get_data(as_text=True)
