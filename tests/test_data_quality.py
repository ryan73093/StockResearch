from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, call

import pytest

from quant_platform.application.data_quality import DataQualityGateError
from quant_platform.application.research_pipeline import DailyResearchPipeline
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import (
    SqlAlchemyFeatureLabelStoreRepository,
    SqlAlchemyMarketBarRepository,
)
from quant_platform.domain.entities import MarketBar
from quant_platform.feature_engineering import FeatureEngine


def _bars(count: int = 70, invalid: bool = False) -> list[MarketBar]:
    output = []
    event = datetime(2026, 1, 5, 21, tzinfo=UTC)
    while len(output) < count:
        if event.weekday() < 5:
            price = Decimal("100") + len(output)
            output.append(MarketBar(
                symbol="SPY", market="US", interval="1d", event_time=event,
                available_time=event + timedelta(minutes=15),
                ingested_at=event + timedelta(hours=1), open=price,
                high=price + Decimal("1"), low=price - Decimal("1"),
                close=price, adjusted_close=price,
                volume=-1 if invalid and len(output) == count - 1 else 1_000_000,
                source="test",
            ))
        event += timedelta(days=1)
    return output


def _single_spy(container) -> None:
    for asset in container.research_universe_service.list_all():
        container.research_universe_service.set_active(asset.symbol, asset.symbol == "SPY")


def test_point_in_time_market_bar_query_uses_latest_visible_revision(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'revisions.db'}"))
    repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    original = _bars(1)[0]
    correction = MarketBar(
        **{
            **{name: getattr(original, name) for name in original.__dataclass_fields__},
            "available_time": original.available_time + timedelta(days=1),
            "ingested_at": original.ingested_at + timedelta(days=1),
            "close": Decimal("105"),
            "adjusted_close": Decimal("105"),
            "high": Decimal("106"),
        }
    )
    repository.add_missing([original, correction])

    before = repository.list_bars("SPY", as_of=correction.available_time - timedelta(seconds=1))
    after = repository.list_bars("SPY", as_of=correction.available_time)

    assert len(before) == len(after) == 1
    assert before[0].close == Decimal("100")
    assert after[0].close == Decimal("105")


def test_full_quality_snapshot_is_persisted_and_research_allowed(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'quality.db'}"))
    _single_spy(container)
    bar_repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    feature_repository = SqlAlchemyFeatureLabelStoreRepository(container.database.session_factory)
    bars = _bars()
    bar_repository.add_missing(bars)
    feature_repository.register_definitions(FeatureEngine.definitions)
    feature_repository.upsert_features(
        FeatureEngine().compute(bars, computed_at=bars[-1].ingested_at)
    )

    first = container.data_quality_service.evaluate(
        "US", as_of=bars[-1].ingested_at, stage="full"
    )
    second = container.data_quality_service.evaluate(
        "US", as_of=bars[-1].ingested_at, stage="full"
    )

    assert first.snapshot.research_allowed
    assert first.snapshot.bar_coverage_rate == pytest.approx(1.0)
    assert first.snapshot.feature_coverage_rate == pytest.approx(1.0)
    assert first.snapshot.id == second.snapshot.id
    assert all(check.unit for check in first.checks)


def test_raw_gate_blocks_invalid_market_values(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'blocked.db'}"))
    _single_spy(container)
    bars = _bars(invalid=True)
    SqlAlchemyMarketBarRepository(container.database.session_factory).add_missing(bars)

    view = container.data_quality_service.evaluate(
        "US", as_of=bars[-1].ingested_at, stage="raw"
    )

    assert not view.snapshot.research_allowed
    assert view.snapshot.invalid_value_count == 1
    assert any(item.code == "INVALID_OHLCV" for item in view.issues)
    with pytest.raises(DataQualityGateError, match="資料品質閘門阻擋研究"):
        container.data_quality_service.ensure_research_ready(view)


def test_full_gate_blocks_when_features_are_missing(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'features-missing.db'}"))
    _single_spy(container)
    bars = _bars()
    SqlAlchemyMarketBarRepository(container.database.session_factory).add_missing(bars)

    raw = container.data_quality_service.evaluate(
        "US", as_of=bars[-1].ingested_at, stage="raw"
    )
    full = container.data_quality_service.evaluate(
        "US", as_of=bars[-1].ingested_at, stage="full"
    )

    assert raw.snapshot.research_allowed
    assert not full.snapshot.research_allowed
    assert full.snapshot.feature_coverage_rate == 0
    assert any(item.code == "FEATURE_MARKET_COVERAGE" for item in full.issues)


def test_daily_research_runs_raw_and_full_quality_gates_in_order() -> None:
    market_data, taiwan, macro, quality, features = (MagicMock() for _ in range(5))
    factor, backtest, ensemble, portfolio, models, decisions = (
        MagicMock() for _ in range(6)
    )
    quality.evaluate.side_effect = [MagicMock(name="raw"), MagicMock(name="full")]
    pipeline = DailyResearchPipeline(
        market_data, taiwan, macro, quality, features, factor, backtest,
        ensemble, portfolio, models, decisions,
    )
    now = datetime(2026, 7, 17, 22, tzinfo=UTC)

    pipeline.run("US", now=now)

    assert quality.evaluate.call_args_list == [
        call("US", as_of=now, stage="raw"),
        call("US", as_of=now, stage="full"),
    ]
    assert quality.ensure_research_ready.call_count == 2
    features.run.assert_called_once_with("US", now=now)
    models.run.assert_called_once_with("US", now=now)


def test_daily_research_stops_before_features_when_raw_gate_blocks() -> None:
    components = [MagicMock() for _ in range(11)]
    market_data, taiwan, macro, quality, features = components[:5]
    quality.evaluate.return_value = MagicMock()
    quality.ensure_research_ready.side_effect = DataQualityGateError("blocked")
    pipeline = DailyResearchPipeline(*components)

    with pytest.raises(DataQualityGateError, match="blocked"):
        pipeline.run("TW", now=datetime(2026, 7, 17, 8, tzinfo=UTC))

    features.run.assert_not_called()


def test_after_hours_preview_is_built_before_slower_auxiliary_sources() -> None:
    calls: list[str] = []
    components = [MagicMock() for _ in range(11)]
    market_data, taiwan, macro, quality, features = components[:5]
    decisions = components[-1]
    market_data.run.side_effect = lambda *args, **kwargs: calls.append("market")
    features.run.side_effect = lambda *args, **kwargs: calls.append("features")
    decisions.run.side_effect = lambda *args, **kwargs: calls.append("decision")
    taiwan.run.side_effect = lambda *args, **kwargs: calls.append("taiwan")
    macro.run.side_effect = lambda *args, **kwargs: calls.append("macro")
    quality.evaluate.side_effect = [MagicMock(name="raw"), MagicMock(name="full")]
    pipeline = DailyResearchPipeline(*components)

    pipeline.run(
        "TW",
        now=datetime(2026, 8, 5, 6, tzinfo=UTC),
        early_decision_callback=lambda _result: calls.append("plan"),
    )

    assert calls[:5] == ["market", "features", "decision", "plan", "taiwan"]


def test_quality_dashboard_shows_explicit_units(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'page.db'}"))
    _single_spy(container)
    bars = _bars()
    bar_repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    feature_repository = SqlAlchemyFeatureLabelStoreRepository(container.database.session_factory)
    bar_repository.add_missing(bars)
    feature_repository.register_definitions(FeatureEngine.definitions)
    feature_repository.upsert_features(FeatureEngine().compute(bars, bars[-1].ingested_at))
    container.data_quality_service.evaluate("US", bars[-1].ingested_at, "full")
    from quant_platform.dashboard.app import create_app

    body = create_app(container).test_client().get("/data-quality?market=US").get_data(as_text=True)

    assert "100.00%" in body
    assert "個標的" in body
    assert "個交易日" in body
    assert "個序列" in body
