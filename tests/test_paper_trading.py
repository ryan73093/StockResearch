from datetime import UTC, date, datetime
from decimal import Decimal

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.domain.entities import MarketBar, PaperOrderStatus
from quant_platform.database.repositories import SqlAlchemyMarketBarRepository
from quant_platform.dashboard.app import create_app


def _bar(day: int, open_price: str, close_price: str, volume: int = 1_000_000) -> MarketBar:
    event = datetime(2025, 1, day, tzinfo=UTC)
    available = datetime(2025, 1, day, 6, tzinfo=UTC)
    return MarketBar(
        symbol="2330.TW", market="TW", interval="1d",
        event_time=event, available_time=available, ingested_at=available,
        open=Decimal(open_price), high=Decimal(close_price) + 2,
        low=Decimal(open_price) - 2, close=Decimal(close_price),
        adjusted_close=Decimal(close_price), volume=volume, source="test",
    )


def _container(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'paper.db'}"))
    container.research_universe_service.add_asset(
        "2330.TW", "TW", "EQUITY", "半導體", "0050.TW", date(2020, 1, 1)
    )
    repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    repository.add_missing([
        _bar(2, "98", "100"), _bar(3, "110", "112"), _bar(6, "120", "121")
    ])
    return container


def test_next_open_execution_has_costs_and_never_fills_early(tmp_path):
    container = _container(tmp_path)
    service = container.paper_trading_service
    submitted = datetime(2025, 1, 2, 7, tzinfo=UTC)
    order = service.submit_order("2330", "BUY", 100, now=submitted)
    assert order.status == PaperOrderStatus.PENDING
    assert service.process_pending(datetime(2025, 1, 2, 8, tzinfo=UTC)).filled == 0
    result = service.process_pending(datetime(2025, 1, 3, 7, tzinfo=UTC))
    assert result.filled == 1
    overview = service.overview(datetime(2025, 1, 3, 7, tzinfo=UTC))
    fill = overview.fills[0]
    assert fill.price == Decimal("110.110000")
    assert fill.commission > 0
    assert fill.transaction_tax == 0
    assert overview.positions[0].position.quantity == 100
    assert overview.account.cash < overview.account.initial_cash
    assert service.process_pending(datetime(2025, 1, 3, 8, tzinfo=UTC)).filled == 0


def test_sell_realizes_pnl_and_charges_transaction_tax(tmp_path):
    service = _container(tmp_path).paper_trading_service
    service.submit_order("2330", "BUY", 100, datetime(2025, 1, 2, 7, tzinfo=UTC))
    service.process_pending(datetime(2025, 1, 3, 7, tzinfo=UTC))
    sell = service.submit_order("2330", "SELL", 40, datetime(2025, 1, 3, 7, tzinfo=UTC))
    assert sell.status == PaperOrderStatus.PENDING
    service.process_pending(datetime(2025, 1, 6, 7, tzinfo=UTC))
    overview = service.overview(datetime(2025, 1, 6, 7, tzinfo=UTC))
    sell_fill = next(item for item in overview.fills if item.side == "SELL")
    assert sell_fill.transaction_tax > 0
    assert overview.positions[0].position.quantity == 60
    assert overview.account.realized_pnl > 0


def test_risk_gate_rejects_concentration_and_short_selling(tmp_path):
    service = _container(tmp_path).paper_trading_service
    concentrated = service.submit_order(
        "2330", "BUY", 3000, datetime(2025, 1, 2, 7, tzinfo=UTC)
    )
    assert concentrated.status == PaperOrderStatus.REJECTED
    assert "20%" in (concentrated.rejection_reason or "")
    short = service.submit_order(
        "2330", "SELL", 1, datetime(2025, 1, 2, 7, tzinfo=UTC)
    )
    assert short.status == PaperOrderStatus.REJECTED
    assert "不允許放空" in (short.rejection_reason or "")


def test_paper_trading_page_labels_money_shares_percent_and_bps(tmp_path):
    container = _container(tmp_path)
    page = create_app(container).test_client().get("/paper-trading")
    assert page.status_code == 200
    text = page.get_data(as_text=True)
    assert "帳戶權益" in text and "元" in text
    assert "委託股數" in text and "股" in text and "張" in text
    assert "總曝險" in text and "%" in text
    assert "模擬滑價" in text and "bps" in text


def test_holdings_page_sends_paper_orders_and_comes_back(tmp_path):
    """S9-W05: the paper account lives on the holdings page; the legacy routes return there."""
    container = _container(tmp_path)
    client = create_app(container).test_client()
    response = client.post("/paper-trading/orders", data={"symbol": "2330", "side": "BUY", "quantity": "5",
                                                           "next": "/holdings?paper=1#paper"})
    assert response.status_code == 302 and response.headers["Location"].endswith("/holdings?paper=1#paper")
    body = client.get("/holdings?paper=1").get_data(as_text=True)
    assert "最近的委託" in body and "待成交" in body and "送出模擬委託" in body
    outside = client.post("/paper-trading/process", data={"next": "//evil.example"})
    assert outside.headers["Location"].endswith("/paper-trading")
