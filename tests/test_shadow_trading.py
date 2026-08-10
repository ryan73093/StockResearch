from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quant_platform.application.shadow_trading import ShadowTradingService
from quant_platform.database import Database
from quant_platform.database.repositories import SqlAlchemyShadowTradingRepository
from quant_platform.domain.entities import MarketBar, ShadowOrderStatus
from quant_platform.execution import BrokerOrderRequest, DisabledBrokerAdapter


def _bars(count: int = 22) -> list[MarketBar]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    output = []
    for index in range(count):
        close = Decimal("100") + Decimal(index) / 10
        event = start + timedelta(days=index)
        output.append(MarketBar(
            symbol="2330.TW", market="TW", interval="1d", event_time=event,
            available_time=event + timedelta(hours=6),
            ingested_at=event + timedelta(hours=6), open=close - Decimal("0.2"),
            high=close + 1, low=close - 1, close=close,
            adjusted_close=close, volume=1_000_000, source="test",
        ))
    return output


def _service(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'shadow.db'}")
    database.create_schema()
    repository = SqlAlchemyShadowTradingRepository(database.session_factory)
    bars = _bars()
    bar_repository = SimpleNamespace(list_bars=lambda symbol: bars)
    universe = SimpleNamespace(get=lambda symbol: SimpleNamespace(asset_type="EQUITY"))
    cpu = SimpleNamespace(
        experiment_id=7,
        summary=SimpleNamespace(promotion_gate="RESEARCH_ONLY"),
        folds=(SimpleNamespace(
            policy_weights=(0.0,) * 8,
            feature_mean=(0.0,) * 5,
            feature_std=(1.0,) * 5,
        ),),
    )
    rl = SimpleNamespace(overview=lambda symbol: SimpleNamespace(
        cpu_agent=cpu, neural_agents=()
    ))
    return ShadowTradingService(repository, bar_repository, universe, rl), bars, repository


def test_shadow_order_is_idempotent_and_does_not_claim_execution_permission(tmp_path):
    service, _, repository = _service(tmp_path)
    first = service.generate("2330", "cpu")
    second = service.generate("2330", "cpu")
    assert first.reused is False
    assert second.reused is True
    assert first.order.id == second.order.id
    assert first.order.status == ShadowOrderStatus.PENDING
    assert first.order.side == "BUY"
    assert first.order.quantity > 0
    assert first.order.notional_twd > 0
    assert first.order.promotion_gate == "RESEARCH_ONLY"
    assert "不具券商執行權限" in first.order.reason
    assert len(repository.list_orders()) == 1


def test_shadow_order_waits_for_next_bar_then_evaluates_with_costs(tmp_path):
    service, bars, _ = _service(tmp_path)
    order = service.generate("2330", "cpu").order
    waiting = service.process_pending()
    assert waiting.evaluated == 0
    assert waiting.pending == 1
    next_event = bars[-1].event_time + timedelta(days=1)
    next_close = bars[-1].close + Decimal("2")
    bars.append(MarketBar(
        symbol="2330.TW", market="TW", interval="1d", event_time=next_event,
        available_time=next_event + timedelta(hours=6),
        ingested_at=next_event + timedelta(hours=6), open=bars[-1].close + Decimal("1"),
        high=next_close + 1, low=next_close - 2, close=next_close,
        adjusted_close=next_close, volume=1_000_000, source="test",
    ))
    processed = service.process_pending()
    evaluated = service.overview().orders[0]
    assert processed.evaluated == 1
    assert processed.pending == 0
    assert evaluated.status == ShadowOrderStatus.EVALUATED
    assert evaluated.execution_time == next_event
    assert evaluated.fill_price is not None
    assert evaluated.commission_twd is not None and evaluated.commission_twd > 0
    assert evaluated.pnl_twd is not None
    assert evaluated.return_rate is not None
    assert evaluated.benchmark_return is not None
    assert evaluated.id == order.id


def test_disabled_broker_adapter_is_a_hard_safety_boundary():
    adapter = DisabledBrokerAdapter()
    assert adapter.capability().connected is False
    assert adapter.capability().live_trading_enabled is False
    with pytest.raises(PermissionError, match="不會送出"):
        adapter.submit(BrokerOrderRequest(
            client_order_id="shadow-1", symbol="2330.TW", side="BUY",
            quantity=1, order_type="MARKET", limit_price=None,
        ))
