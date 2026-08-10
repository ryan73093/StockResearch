from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import SqlAlchemyFeatureLabelStoreRepository
from quant_platform.domain.entities import MarketBar
from quant_platform.feature_engineering import CrossAssetFeatureEngine, FeatureEngine
from quant_platform.feature_engineering.intraday_derivatives import (
    INTRADAY_DERIVATIVE_DEFINITIONS,
)
from quant_platform.labels import LabelEngine


def make_bars(symbol: str = "SPY", count: int = 30, growth: float = 0.01) -> list[MarketBar]:
    bars = []
    for index in range(count):
        event_time = datetime(2025, 1, 1, 21, tzinfo=UTC) + timedelta(days=index)
        price = Decimal(str(100 * (1 + growth) ** index))
        bars.append(
            MarketBar(
                symbol=symbol,
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
                volume=1_000 + index * 10,
                source="test",
            )
        )
    return bars


def test_features_use_current_bar_available_time():
    bars = make_bars()
    features = FeatureEngine().compute(bars)
    return_1d = next(
        item for item in features if item.feature_name == "return_1d" and item.event_time == bars[1].event_time
    )

    assert return_1d.value == pytest.approx(0.01)
    assert return_1d.available_time == bars[1].available_time


def test_forward_labels_are_not_available_at_prediction_time():
    bars = make_bars(count=26)
    labels = LabelEngine().compute(bars, bars)
    future = next(
        item for item in labels if item.label_name == "future_return_5d" and item.event_time == bars[0].event_time
    )
    excess = next(
        item for item in labels if item.label_name == "excess_return_5d" and item.event_time == bars[0].event_time
    )

    assert future.value == pytest.approx((1.01**5) - 1)
    assert future.available_time == bars[5].available_time
    assert future.available_time > future.event_time
    assert excess.value == pytest.approx(0.0)


def test_feature_store_upsert_is_idempotent(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'features.db'}"))
    repository = SqlAlchemyFeatureLabelStoreRepository(container.database.session_factory)
    features = FeatureEngine().compute(make_bars())

    repository.upsert_features(features)
    first_count = sum(item.row_count for item in repository.feature_coverage())
    repository.upsert_features(features)
    second_count = sum(item.row_count for item in repository.feature_coverage())

    assert first_count == len(features)
    assert second_count == first_count
    assert len(repository.list_definitions()) == 21 + len(INTRADAY_DERIVATIVE_DEFINITIONS)


def test_cross_asset_features_respect_indicator_available_time():
    target = make_bars("2330.TW", count=22)
    context = make_bars("^GSPC", count=22, growth=0.02)
    context[20] = replace(
        context[20], available_time=target[20].available_time + timedelta(days=1)
    )
    values = CrossAssetFeatureEngine().compute(target, {"^GSPC": context})
    same_day = [
        item for item in values
        if item.feature_name == "sp500_return_20d" and item.event_time == target[20].event_time
    ]
    assert same_day == []
    next_day = next(
        item for item in values
        if item.feature_name == "sp500_return_20d" and item.event_time == target[21].event_time
    )
    assert next_day.available_time <= target[21].available_time


def test_feature_store_dashboard_explains_empty_materialization(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'feature-page.db'}"))
    from quant_platform.dashboard.app import create_app

    response = create_app(container).test_client().get("/features")

    assert response.status_code == 200
    assert "Feature & Label Store" in response.get_data(as_text=True)
    assert "future_return_5d" in response.get_data(as_text=True)
