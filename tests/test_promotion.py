from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from quant_platform.application.promotion import PromotionPolicy, PromotionService
from quant_platform.database import Database
from quant_platform.database.repositories import (
    SqlAlchemyPromotionRepository, SqlAlchemyShadowTradingRepository,
)
from quant_platform.domain.entities import (
    PromotionStatus, ShadowOrder, ShadowOrderStatus,
)
from quant_platform.execution import BrokerOrderRequest, LocalSandboxBrokerAdapter


def _research(candidate: bool = True):
    summary = SimpleNamespace(
        promotion_gate="CANDIDATE" if candidate else "RESEARCH_ONLY",
        positive_excess_fold_ratio=0.75,
        compounded_return=0.25,
        compounded_benchmark_return=0.10,
        compounded_excess_return=0.15,
        median_sharpe=1.20,
        worst_max_drawdown=-0.12,
        total_cost_twd=3200.0,
    )
    return SimpleNamespace(
        experiment_id=7, fold_count=4, data_is_current=True, summary=summary,
    )


def _shadow(index: int, event: datetime) -> ShadowOrder:
    return ShadowOrder(
        id=None, symbol="2330.TW", algorithm="cpu", experiment_id=7,
        decision_time=event - timedelta(days=1), data_available_time=event,
        reference_price=Decimal("100"), previous_target_weight=0.0,
        target_weight=0.5, side="BUY", quantity=100,
        notional_twd=Decimal("10000"), status=ShadowOrderStatus.EVALUATED,
        promotion_gate="CANDIDATE", reason="test", created_at=event,
        execution_time=event, fill_price=Decimal("100"), gross_twd=Decimal("10000"),
        commission_twd=Decimal("20"), transaction_tax_twd=Decimal("0"),
        pnl_twd=Decimal("200"), return_rate=0.02,
        benchmark_return=0.01,
    )


def _service(tmp_path, candidate: bool = True):
    database = Database(f"sqlite:///{tmp_path / 'promotion.db'}")
    database.create_schema()
    promotions = SqlAlchemyPromotionRepository(database.session_factory)
    shadows = SqlAlchemyShadowTradingRepository(database.session_factory)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    order_ids = [shadows.save(_shadow(i, start + timedelta(days=i * 2))) for i in range(2)]
    research = _research(candidate)
    rl = SimpleNamespace(overview=lambda symbol: SimpleNamespace(
        cpu_agent=research, neural_agents=(),
    ))
    policy = PromotionPolicy(
        minimum_shadow_observations=2, minimum_shadow_days=1,
        approval_days=30,
    )
    return PromotionService(promotions, shadows, rl, policy), promotions, order_ids


def test_all_gates_then_manual_approval_are_required_for_idempotent_sandbox(tmp_path):
    service, repository, order_ids = _service(tmp_path)
    evaluated = service.evaluate("2330", "cpu")
    assert evaluated.review.status == PromotionStatus.REVIEW_READY
    assert evaluated.passed_count == evaluated.total_count == 11
    with pytest.raises(PermissionError, match="尚未取得"):
        service.submit_sandbox(evaluated.review.id, order_ids[0])

    approved = service.review(
        evaluated.review.id, "approve", "研究員甲", "樣本外與影子門檻均通過，核准沙盒驗證。",
    )
    assert approved.review.status == PromotionStatus.APPROVED
    assert approved.review.approval_expires_at is not None
    first = service.submit_sandbox(approved.review.id, order_ids[0])
    second = service.submit_sandbox(approved.review.id, order_ids[0])
    assert first.id == second.id
    assert first.environment == "sandbox"
    assert first.quantity == 100
    assert len(repository.list_executions()) == 1
    assert service.overview().sandbox_capability.live_trading_enabled is False


def test_failed_quantitative_gate_cannot_be_overridden_by_manual_approval(tmp_path):
    service, _, _ = _service(tmp_path, candidate=False)
    evaluated = service.evaluate("2330", "cpu")
    assert evaluated.review.status == PromotionStatus.INELIGIBLE
    assert evaluated.passed_count == 10
    with pytest.raises(PermissionError, match="全部量化門檻"):
        service.review(
            evaluated.review.id, "approve", "研究員甲", "即使人工想核准也必須由後端拒絕。",
        )


def test_local_sandbox_validates_orders_and_never_enables_live_trading():
    sandbox = LocalSandboxBrokerAdapter()
    capability = sandbox.capability()
    assert capability.connected is True
    assert capability.environment == "sandbox"
    assert capability.live_trading_enabled is False
    with pytest.raises(ValueError, match="股數"):
        sandbox.submit(BrokerOrderRequest(
            client_order_id="bad", symbol="2330.TW", side="BUY", quantity=0,
            order_type="MARKET", limit_price=None,
        ))


def test_expired_approval_is_automatically_revoked_on_read(tmp_path):
    service, repository, _ = _service(tmp_path)
    evaluated = service.evaluate("2330", "cpu")
    approved = service.review(
        evaluated.review.id, "approve", "研究員甲", "核准後刻意模擬到期以測試自動撤銷。",
    ).review
    repository.update_review(replace(
        approved, approval_expires_at=datetime.now(UTC) - timedelta(seconds=1)
    ))
    refreshed = service.overview().reviews[0].review
    assert refreshed.status == PromotionStatus.REVOKED
    assert refreshed.approval_expires_at is None
