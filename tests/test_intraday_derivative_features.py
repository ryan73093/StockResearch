from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.api.app import create_api
from quant_platform.dashboard.app import create_app
from quant_platform.database.repositories import (
    SqlAlchemyFeatureLabelStoreRepository,
    SqlAlchemyPointInTimeDataRepository,
)
from quant_platform.domain.entities import PointInTimeObservation
from quant_platform.feature_engineering.intraday_derivatives import (
    IntradayDerivativeFeatureEngine,
)


def _observation(dataset, entity, event, available, raw, key="record"):
    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True)
    return PointInTimeObservation(
        id=None, dataset_key=dataset, entity_id=entity, event_time=event,
        available_time=available, ingested_at=available + timedelta(minutes=5),
        revision_key=key, content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        payload_json=payload, source="test", source_uri="https://example.invalid",
    )


def _container(tmp_path):
    return build_container(Settings(database_url=f"sqlite:///{tmp_path / 'features.db'}"))


def test_minute_features_use_only_batch_available_time(tmp_path) -> None:
    container = _container(tmp_path)
    definition = next(
        item for item in SqlAlchemyPointInTimeDataRepository(
            container.database.session_factory
        ).list_datasets() if item.dataset_key == "tw_stock_1m"
    )
    day = datetime(2026, 1, 2, 1, tzinfo=UTC)
    available = datetime(2026, 1, 2, 7, 50, tzinfo=UTC)
    rows = [
        _observation("tw_stock_1m", "2330", day + timedelta(minutes=index), available, {
            "open": 100 + index, "high": 102 + index, "low": 99 + index,
            "close": 101 + index, "volume": (index + 1) * 100,
        }, f"minute-{index}") for index in range(3)
    ]
    values = IntradayDerivativeFeatureEngine().compute(
        definition, rows, lambda value: f"{value}.TW",
        computed_at=datetime(2026, 1, 3, tzinfo=UTC),
    )
    by_name = {item.feature_name: item for item in values}

    assert by_name["intraday_return"].value == pytest.approx(0.03)
    assert by_name["intraday_volume_shares"].value == 600
    assert by_name["intraday_max_minute_volume_share"].value == 0.5
    assert all(item.available_time == available for item in values)
    assert all(item.symbol == "2330.TW" for item in values)


def test_odd_lot_options_and_futures_features_have_expected_units_and_ratios(tmp_path) -> None:
    container = _container(tmp_path)
    datasets = {
        item.dataset_key: item for item in SqlAlchemyPointInTimeDataRepository(
            container.database.session_factory
        ).list_datasets()
    }
    engine = IntradayDerivativeFeatureEngine()
    event = datetime(2026, 1, 2, 6, tzinfo=UTC)
    available = event + timedelta(hours=4)
    odd = engine.compute(datasets["tw_odd_lot_daily"], [_observation(
        "tw_odd_lot_daily", "2330", event, available, {
            "TradePrice": 1000, "TradeVolume": 1200, "Transaction": 6,
            "TradeValue": 1_200_000, "BestBidPrice": 999,
            "BestAskPrice": 1001, "BestBidVolume": 600, "BestAskVolume": 400,
        }
    )], computed_at=available + timedelta(hours=1))
    odd_values = {item.feature_name: item.value for item in odd}
    assert odd_values["odd_lot_average_trade_size"] == 200
    assert odd_values["odd_lot_spread_bps"] == pytest.approx(20)
    assert odd_values["odd_lot_order_imbalance"] == pytest.approx(0.2)

    options = engine.compute(datasets["tw_options_daily"], [
        _observation("tw_options_daily", "TXO", event, available, {
            "PutCall": "C", "ExercisePrice": 23000, "volume": 100, "open_interest": 200,
        }, "call"),
        _observation("tw_options_daily", "TXO", event, available, {
            "PutCall": "P", "ExercisePrice": 23000, "volume": 150, "open_interest": 300,
        }, "put"),
    ], computed_at=available + timedelta(hours=1))
    option_values = {item.feature_name: item.value for item in options}
    assert option_values["option_put_call_volume_ratio"] == 1.5
    assert option_values["option_put_call_oi_ratio"] == 1.5
    assert option_values["option_total_volume_contracts"] == 250

    futures_rows = []
    for index, close in enumerate((23000, 23230)):
        current_event = event + timedelta(days=index)
        current_available = available + timedelta(days=index)
        futures_rows.append(_observation("tw_futures_daily", "TX", current_event, current_available, {
            "contract_date": "202601", "trading_session": "position", "close": close,
            "max": close + 100, "min": close - 100, "volume": 10000,
            "open_interest": 50000 + index * 500,
        }, f"future-{index}"))
    futures = engine.compute(
        datasets["tw_futures_daily"], futures_rows,
        computed_at=available + timedelta(days=2),
    )
    returns = [item.value for item in futures if item.feature_name == "futures_front_return_1d"]
    assert returns == [pytest.approx(0.01)]

    definitions = {item.name: json.loads(item.parameters_json)["unit"] for item in engine.definitions}
    assert definitions["odd_lot_trade_price"] == "元／股"
    assert definitions["futures_volume_contracts"] == "口"
    assert definitions["option_put_call_volume_ratio"] == "倍"


def test_pipeline_persists_current_and_immutable_revisions_idempotently(tmp_path) -> None:
    container = _container(tmp_path)
    point_repository = SqlAlchemyPointInTimeDataRepository(container.database.session_factory)
    feature_repository = SqlAlchemyFeatureLabelStoreRepository(container.database.session_factory)
    event = datetime(2026, 1, 2, 7, tzinfo=UTC)
    available = event
    original = _observation("tw_odd_lot_daily", "2330", event, available, {
        "TradePrice": 1000, "TradeVolume": 1000, "Transaction": 10,
        "TradeValue": 1_000_000, "BestBidPrice": 999, "BestAskPrice": 1001,
        "BestBidVolume": 500, "BestAskVolume": 500,
    })
    assert point_repository.add_revisions([original]) == 1
    first = container.intraday_derivative_feature_pipeline.run(
        now=event + timedelta(days=1), dataset_keys=["tw_odd_lot_daily"]
    )
    second = container.intraday_derivative_feature_pipeline.run(
        now=event + timedelta(days=1), dataset_keys=["tw_odd_lot_daily"]
    )
    assert first.computed_feature_count == 7
    assert first.inserted_revision_count == 7
    assert second.inserted_revision_count == 0

    revised_raw = json.loads(original.payload_json)
    revised_raw["TradePrice"] = 1010
    corrected = _observation(
        "tw_odd_lot_daily", "2330", event, available + timedelta(hours=1),
        revised_raw, key="record",
    )
    assert point_repository.add_revisions([corrected]) == 1
    third = container.intraday_derivative_feature_pipeline.run(
        now=event + timedelta(days=1), dataset_keys=["tw_odd_lot_daily"]
    )
    assert third.inserted_revision_count == 7

    before = feature_repository.list_feature_revisions(
        ["2330.TW"], ["odd_lot_trade_price"], as_of=available + timedelta(minutes=30)
    )
    after = feature_repository.list_feature_revisions(
        ["2330.TW"], ["odd_lot_trade_price"], as_of=available + timedelta(hours=2)
    )
    assert before[0].value == 1000
    assert after[0].value == 1010
    assert len(feature_repository.list_features(["2330.TW"], ["odd_lot_trade_price"])) == 1


def test_feature_pipeline_rejects_future_or_unavailable_lineage(tmp_path) -> None:
    container = _container(tmp_path)
    now = datetime(2026, 1, 2, tzinfo=UTC)
    future = _observation(
        "tw_odd_lot_daily", "2330", now, now + timedelta(hours=1),
        {"TradePrice": 1000},
    )
    repository = SqlAlchemyPointInTimeDataRepository(container.database.session_factory)
    repository.add_revisions([future])
    result = container.intraday_derivative_feature_pipeline.run(
        now=now, dataset_keys=["tw_odd_lot_daily"]
    )
    assert result.observation_count == 0
    assert result.computed_feature_count == 0


def test_intraday_feature_page_and_api_expose_explicit_units(tmp_path) -> None:
    container = _container(tmp_path)
    repository = SqlAlchemyPointInTimeDataRepository(container.database.session_factory)
    event = datetime(2026, 7, 17, 6, 30, tzinfo=UTC)
    repository.add_revisions([_observation(
        "tw_odd_lot_daily", "2330", event, event, {
            "TradePrice": 1_045, "TradeVolume": 1_200, "Transaction": 6,
            "TradeValue": 1_254_000, "BestBidPrice": 1_040,
            "BestAskPrice": 1_050, "BestBidVolume": 600, "BestAskVolume": 400,
        }
    )])
    container.intraday_derivative_feature_pipeline.run(
        now=event + timedelta(hours=1), dataset_keys=["tw_odd_lot_daily"]
    )

    page = create_app(container).test_client().get(
        "/intraday-features?symbol=2330"
    ).get_data(as_text=True)
    assert "盤中與衍生特徵" in page
    assert "1,045.00 元／股" in page
    assert "1,200 股" in page
    assert "6 筆" in page
    assert "股／筆" in page
    assert "bps" in page
    assert "26 項" in page

    api_paths = {route.path for route in create_api(container).routes}
    assert "/api/v1/intraday-features" in api_paths
    assert "/api/v1/pipelines/intraday-features" in api_paths
