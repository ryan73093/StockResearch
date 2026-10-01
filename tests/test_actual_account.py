from datetime import date, timedelta

import pytest

from quant_platform.application.actual_account import ActualAccountError, ActualAccountService, compare_on
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.database.engine import Database
from quant_platform.database.repositories import SqlAlchemyActualAccountRepository
from quant_platform.research.history.dataset import write_parquet
from quant_platform.research.history.official import DailyRow

TODAY = date(2026, 9, 30)


@pytest.fixture
def service(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'account.db'}")
    database.create_schema()
    research = tmp_path / "research"
    days = [TODAY - timedelta(days=offset) for offset in range(40, -1, -1)]
    sessions = [day for day in days if day.weekday() < 5]
    write_parquet(
        [DailyRow(day, 100.0, 100.0, 100.0, 100.0, source="test") for day in sessions],
        research / "history" / "daily" / "0050.parquet",
    )
    return ActualAccountService(
        SqlAlchemyActualAccountRepository(database.session_factory),
        price_lookup=lambda symbols: {"0050.TW": 110.0},
        research_dir=research,
        today=lambda: TODAY,
    )


def test_ledger_by_hand(service):
    service.record_cash_flow({"kind": "deposit", "day": "2026-09-01", "amount": "20,000"})
    buy = service.record_trade({"day": "2026-09-01", "symbol": "0050", "side": "BUY", "shares": "150", "price": "100"})
    sell = service.record_trade({"day": "2026-09-20", "symbol": "0050", "side": "SELL", "shares": "50", "price": "110"})
    service.record_cash_flow({"kind": "dividend", "day": "2026-09-25", "amount": "100", "symbol": "0050"})

    assert (buy.fee, buy.tax) == (21, 0)           # floor(15,000 × 0.1425%) = 21
    assert (sell.fee, sell.tax) == (20, 5)          # minimum fee; ETF tax 0.1% of 5,500
    overview = service.overview()
    holding = overview.holdings[0]
    assert holding.shares == 100 and holding.average_cost == pytest.approx(150.21 * 100 / 150)
    assert overview.cash == pytest.approx(20_000 - 15_021 + 5_475 + 100)
    assert overview.market_value == pytest.approx(100 * 110.0)
    assert overview.realized == pytest.approx(5_475 - 15_021 / 150 * 50)
    assert overview.xirr is not None and overview.xirr > 0


def test_default_fee_follows_the_trades_broker(service):
    service.record_cash_flow({"kind": "deposit", "day": "2026-09-01", "amount": "20,000", "broker": "taishin"})
    cathay = service.record_trade({"day": "2026-09-01", "symbol": "0050", "side": "BUY", "shares": "99",
                                   "price": "100.2", "broker": "cathay"})
    unknown = service.record_trade({"day": "2026-09-01", "symbol": "0050", "side": "BUY", "shares": "1",
                                    "price": "100"})

    assert (cathay.fee, cathay.broker) == (14, "cathay")     # floor(9,919.80 × 0.1425%), rebate not modelled
    assert (unknown.fee, unknown.broker) == (20, "")          # no broker: the plan's (here conservative)
    assert service.overview().flows[0].broker == "taishin"
    with pytest.raises(ActualAccountError, match="券商"):
        service.record_trade({"day": "2026-09-01", "symbol": "0050", "side": "BUY", "shares": "1",
                              "price": "100", "broker": "nowhere"})


def test_drawdown_and_month_ends_by_hand(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'history.db'}")
    database.create_schema()
    research = tmp_path / "research"
    sessions = [TODAY - timedelta(days=offset) for offset in range(40, -1, -1)]
    sessions = [day for day in sessions if day.weekday() < 5]
    write_parquet([DailyRow(day, 100.0, 100.0, 100.0, 100.0, source="test") for day in sessions],
                  research / "history" / "daily" / "0050.parquet")

    def close(day):
        return 100.0 if day < date(2026, 9, 11) else (80.0 if day < date(2026, 9, 21) else 110.0)

    service = ActualAccountService(
        SqlAlchemyActualAccountRepository(database.session_factory),
        price_lookup=lambda symbols: {"0050.TW": 110.0}, research_dir=research, today=lambda: TODAY,
        bar_history=lambda symbol: [(day, close(day)) for day in sessions] if symbol == "0050.TW" else [],
    )
    service.record_cash_flow({"kind": "deposit", "day": "2026-09-01", "amount": "10000"})
    service.record_trade({"day": "2026-09-01", "symbol": "0050", "side": "BUY", "shares": "99", "price": "100.2",
                          "fee": "20"})

    overview = service.overview()

    # 9,960.20 on 09-01 → 60.20 + 99 × 80 = 7,980.20 from 09-11: a 19.88% fall in unit value.
    assert overview.max_drawdown == pytest.approx(7_980.2 / 9_960.2 - 1)
    september = overview.monthly[0]
    assert (september["month"], september["day"]) == ("2026-09", date(2026, 9, 30))
    assert september["actual"] == pytest.approx(60.2 + 99 * 110)
    # The shadow buys the same 10,000 at 100.20 (research closes stay at 100): 99 shares and 60.20 cash.
    assert september["shadow"] == pytest.approx(99 * 100 + 60.2)
    assert september["difference"] == pytest.approx(990.0)

    # Any day (S5-W04): Sunday 09-13 uses Friday 09-11, when 0050 closed at 80 in the account's bars.
    sunday = compare_on(overview, date(2026, 9, 13))
    assert (sunday["session"], sunday["shadow_day"]) == (date(2026, 9, 11), date(2026, 9, 11))
    assert sunday["actual"] == pytest.approx(60.2 + 99 * 80)
    assert sunday["shadow"] == pytest.approx(99 * 100 + 60.2)
    assert sunday["difference"] == pytest.approx(-1_980.0) and sunday["invested"] == 10_000
    assert compare_on(overview, date(2026, 8, 31)) == {"error": "2026-08-31 還沒有任何紀錄（第一筆是 2026-09-01）"}


def test_holdings_page_compares_any_day(tmp_path):
    from datetime import UTC, datetime
    from decimal import Decimal

    from quant_platform.database.repositories import SqlAlchemyMarketBarRepository
    from quant_platform.domain.entities import MarketBar

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    sessions = [date(2026, 9, 1) + timedelta(days=offset) for offset in range(30)]
    SqlAlchemyMarketBarRepository(container.database.session_factory).add_missing([
        MarketBar(
            symbol="0050.TW", market="TW", interval="1d",
            event_time=datetime(day.year, day.month, day.day, 5, 30, tzinfo=UTC),
            available_time=datetime(day.year, day.month, day.day, 6, tzinfo=UTC),
            ingested_at=datetime(day.year, day.month, day.day, 6, tzinfo=UTC),
            open=Decimal(100), high=Decimal(100), low=Decimal(100),
            close=Decimal(100 if day.day < 11 else 80), adjusted_close=Decimal(100), volume=1_000, source="test",
        )
        for day in sessions if day.weekday() < 5
    ])
    client = create_app(container).test_client()
    client.post("/holdings/cash", data={"kind": "deposit", "day": "2026-09-01", "amount": "10000"})
    client.post("/holdings/trade", data={"day": "2026-09-01", "symbol": "0050", "side": "BUY", "shares": "99",
                                         "price": "100.2", "fee": "20"})

    body = client.get("/holdings?on=2026-09-13").get_data(as_text=True)
    assert 'id="compare" open' in body and 'name="on" type="date" value="2026-09-13"' in body
    assert "2026-09-11" in body and "（2026-09-13 沒有收盤，取前一個交易日）" in body
    assert "7,980 元" in body and "10,000 元" in body          # 60.20 + 99 × 80; the 10,000 deposited

    assert "還沒有任何紀錄（第一筆是 2026-09-01）" in client.get("/holdings?on=2026-08-01").get_data(as_text=True)
    assert "請選今天以前的日期" in client.get("/holdings?on=2999-01-01").get_data(as_text=True)
    assert "日期格式是 YYYY-MM-DD" in client.get("/holdings?on=9/13").get_data(as_text=True)
    assert 'id="compare" open' not in client.get("/holdings").get_data(as_text=True)


def test_stress_scenarios_by_hand():
    from quant_platform.application.actual_account import ActualAccountOverview, Holding, stress_scenarios

    overview = ActualAccountOverview(
        cash=5_000, holdings=[Holding("0050", 100, 90.0, 100.0, 10_000, 1_000), Holding("00679B", 200, 25.0, 25.0, 5_000, 0)],
        market_value=15_000, total_value=20_000, net_deposits=20_000, dividends=0, fees=0, taxes=0, realized=0,
        xirr=None, shadow=None, flows=[], trades=[],
    )

    rows = stress_scenarios(overview, tolerance=0.25)

    # Only the 10,000 in 0050 falls; the bond ETF and the cash stay.
    assert [(row["drop"], row["loss"], row["after"], row["share"]) for row in rows] == [
        (0.20, 2_000, 18_000, 0.10), (0.30, 3_000, 17_000, 0.15), (0.55, 5_500, 14_500, 0.275),
    ]
    assert [row["over_tolerance"] for row in rows] == [False, False, True]


def test_invalid_entries_are_rejected(service):
    service.record_cash_flow({"kind": "deposit", "day": "2026-09-01", "amount": "5000"})
    service.record_trade({"day": "2026-09-01", "symbol": "0050", "side": "BUY", "shares": "10", "price": "100"})
    with pytest.raises(ActualAccountError, match="只有 10 股"):
        service.record_trade({"day": "2026-09-02", "symbol": "0050", "side": "SELL", "shares": "11", "price": "100"})
    with pytest.raises(ActualAccountError, match="未來"):
        service.record_cash_flow({"kind": "deposit", "day": "2026-10-05", "amount": "1"})
    with pytest.raises(ActualAccountError, match="代號"):
        service.record_trade({"day": "2026-09-01", "symbol": "台積電", "side": "BUY", "shares": "1", "price": "1"})
    with pytest.raises(ActualAccountError, match="超過目前現金"):
        service.record_cash_flow({"kind": "withdrawal", "day": "2026-09-03", "amount": "999999"})


def test_voided_entries_leave_the_books(service):
    deposit = service.record_cash_flow({"kind": "deposit", "day": "2026-09-01", "amount": "5000"})
    with pytest.raises(ActualAccountError):
        service.void("cash", deposit.id, "  ")
    assert service.void("cash", deposit.id, "重複輸入")
    assert service.overview().cash == 0 and service.overview().flows == []
    assert not service.void("cash", deposit.id, "再作廢一次")


def test_shadow_dca_invests_each_deposit_the_same_day(service):
    service.record_cash_flow({"kind": "deposit", "day": "2026-09-01", "amount": "10000"})

    shadow = service.overview().shadow

    # 99 shares at 100.20 (close + 20 bps, + fee 20) → 99 × 100 + 60.2 cash at the last close.
    assert shadow["value"] == pytest.approx(99 * 100 + 60.2)
    assert shadow["as_of"] == "2026-09-30" and shadow["trades"] == 1


def test_holdings_page_records_and_reports_errors(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    client = create_app(container).test_client()
    assert "開始記錄實際帳戶" in client.get("/holdings").get_data(as_text=True)

    bad = client.post("/holdings/trade", data={"day": "2026-09-01", "symbol": "0050", "side": "SELL", "shares": "5", "price": "100"})
    assert bad.status_code == 400 and "無法賣出" in bad.get_data(as_text=True)

    client.post("/holdings/cash", data={"kind": "deposit", "day": "2026-09-01", "amount": "20000"})
    response = client.post(
        "/holdings/trade", data={"day": "2026-09-01", "symbol": "0050", "side": "BUY", "shares": "150", "price": "100"},
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)
    assert "已記錄 2026-09-01 買進 0050 150 股" in body and "帳務明細" in body
