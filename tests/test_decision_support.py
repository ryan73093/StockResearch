from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import SqlAlchemyDailyDecisionRepository
from quant_platform.domain.entities import DailyDecision


def _decision(computed_at: datetime, status: str) -> DailyDecision:
    return DailyDecision(
        id=None,
        market="TW",
        symbol="0050.TW",
        decision_version="1.0.0",
        event_time=datetime(2026, 1, 2, tzinfo=UTC),
        status=status,
        score=0.7,
        predicted_return_5d=0.01,
        prediction_dispersion=0.002,
        model_rank=0.8,
        regime="BULL_LOW_VOL",
        factor_score=0.7,
        strategy_score=0.6,
        suggested_weight=0,
        reasons_json='["測試理由"]',
        risks_json="[]",
        gate_checks_json='["研究門檻"]',
        computed_at=computed_at,
    )


def test_latest_decision_prefers_latest_computation(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'decisions.db'}"))
    repository = SqlAlchemyDailyDecisionRepository(container.database.session_factory)
    first = _decision(datetime(2026, 1, 3, tzinfo=UTC), "資料不足")
    latest = replace(
        first,
        event_time=first.event_time - timedelta(days=1),
        computed_at=first.computed_at + timedelta(hours=1),
        status="觀察",
    )
    repository.upsert([first])
    repository.upsert([latest])
    restored = repository.get_latest("0050.TW")
    assert restored is not None
    assert restored.status == "觀察"
    assert len(repository.list_latest()) == 1


def test_technical_analysis_explains_bullish_and_risk_evidence(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'technical.db'}"))
    summary = container.stock_research_service._technical_analysis(
        {
            "close_to_sma_20": 0.05, "momentum_20d": 0.08, "momentum_60d": 0.15,
            "rsi_14": 72, "volume_zscore_20": 1.4, "drawdown_252d": -0.01,
            "volatility_20d": 0.42, "atr_14_pct": 0.04,
        },
        None,
    )
    assert summary.stance == "技術面偏多"
    assert any(item.signal == "過熱" for item in summary.evidence)
    assert any("高波動" in item for item in summary.risk_notes)
