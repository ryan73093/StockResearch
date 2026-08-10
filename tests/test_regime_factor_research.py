from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant_platform.domain.entities import (
    FeatureDefinition,
    FeatureValue,
    LabelValue,
    MarketBar,
    RegimeState,
)
from quant_platform.factor import FactorResearchEngine
from quant_platform.regime import RegimeDetectionEngine
from quant_platform.config import Settings
from quant_platform.container import build_container


def make_market_bars(count: int = 280) -> list[MarketBar]:
    output = []
    for index in range(count):
        event_time = datetime(2024, 1, 1, 21, tzinfo=UTC) + timedelta(days=index)
        price = Decimal(str(100 + index * 0.25))
        output.append(
            MarketBar(
                symbol="SPY",
                market="US",
                interval="1d",
                event_time=event_time,
                available_time=event_time + timedelta(minutes=15),
                ingested_at=event_time + timedelta(hours=1),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                adjusted_close=price,
                volume=1_000_000,
                source="test",
            )
        )
    return output


def test_regime_is_point_in_time_and_ignores_future_prices():
    bars = make_market_bars()
    engine = RegimeDetectionEngine()
    computed_at = datetime(2025, 1, 1, tzinfo=UTC)
    original = engine.compute(bars, computed_at=computed_at)
    changed = list(bars)
    for index in range(240, len(changed)):
        bar = changed[index]
        changed[index] = MarketBar(
            **{field: getattr(bar, field) for field in bar.__dataclass_fields__ if field not in {"close", "adjusted_close"}},
            close=Decimal("1"),
            adjusted_close=Decimal("1"),
        )
    recomputed = engine.compute(changed, computed_at=computed_at)
    original_at_220 = next(item for item in original if item.event_time == bars[220].event_time)
    recomputed_at_220 = next(item for item in recomputed if item.event_time == bars[220].event_time)

    assert original_at_220 == recomputed_at_220
    assert original_at_220.trend_regime == "BULL"
    assert original_at_220.available_time == bars[220].available_time


def test_factor_engine_recovers_monotonic_cross_section():
    symbols = [f"S{index}" for index in range(6)]
    features: list[FeatureValue] = []
    labels: list[LabelValue] = []
    regimes: list[RegimeState] = []
    start = datetime(2025, 1, 1, 21, tzinfo=UTC)
    computed_at = start + timedelta(days=80)
    for day in range(40):
        event_time = start + timedelta(days=day)
        regimes.append(
            RegimeState(
                symbol="SPY",
                event_time=event_time,
                available_time=event_time + timedelta(minutes=15),
                regime_version="1.0.0",
                trend_regime="BULL",
                volatility_regime="LOW_VOL",
                composite_regime="BULL_LOW_VOL",
                trend_score=0.1,
                volatility_score=-0.2,
                confidence=0.8,
                computed_at=computed_at,
            )
        )
        for rank, symbol in enumerate(symbols):
            features.append(
                FeatureValue(
                    symbol=symbol,
                    feature_name="test_factor",
                    feature_version="1.0.0",
                    event_time=event_time,
                    available_time=event_time + timedelta(minutes=15),
                    computed_at=computed_at,
                    value=float(rank),
                )
            )
            for label_name, horizon in (
                ("future_return_5d", 5),
                ("future_return_20d", 20),
                ("excess_return_5d", 5),
            ):
                labels.append(
                    LabelValue(
                        symbol=symbol,
                        label_name=label_name,
                        label_version="1.0.0",
                        event_time=event_time,
                        available_time=event_time + timedelta(days=horizon),
                        computed_at=computed_at,
                        value=rank / 100,
                    )
                )
    definition = FeatureDefinition(
        name="test_factor",
        version="1.0.0",
        family="test",
        description="Synthetic monotonic factor",
        lookback=1,
        parameters_json="{}",
    )

    result = FactorResearchEngine().evaluate(
        "US", [definition], features, labels, regimes, as_of=computed_at
    )[0]

    assert result.rank_ic_5d == pytest.approx(1.0)
    assert result.rank_ic_20d == pytest.approx(1.0)
    assert result.quantile_spread_5d and result.quantile_spread_5d > 0
    assert result.turnover == pytest.approx(0.0)
    assert result.best_regime == "BULL_LOW_VOL"


def test_factor_research_dashboard_renders_methodology(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'factor-page.db'}"))
    from quant_platform.dashboard.app import create_app

    response = create_app(container).test_client().get("/factors")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Market Regime &amp; Factor Research" not in body
    assert "Market Regime & Factor Research" in body
    assert "PROMOTION GATE" in body
