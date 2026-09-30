from dataclasses import replace
from datetime import UTC, datetime, timedelta

from quant_platform.application.macro_data import MACRO_FEATURES
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.domain.entities import MacroObservation


class FakeFredProvider:
    def __init__(self, months: int = 13, revisions: dict[tuple[str, int], float] | None = None):
        self.months = months
        self.revisions = revisions or {}

    def fetch(self, series_id: str, start: datetime, end: datetime) -> list[MacroObservation]:
        rows: list[MacroObservation] = []
        for index in range(self.months):
            event_time = datetime(2023 + index // 12, index % 12 + 1, 1, tzinfo=UTC)
            value = 100.0 + index if series_id in {"CPIAUCSL", "GDP"} else 1.0 + index / 10
            available_time = event_time + timedelta(days=20)
            ingested_at = datetime(2025, 1, 1, tzinfo=UTC)
            if (series_id, index) in self.revisions:
                value = self.revisions[(series_id, index)]
                available_time = ingested_at = datetime(2025, 3, 1, tzinfo=UTC)
            rows.append(
                MacroObservation(
                    None,
                    series_id,
                    event_time,
                    available_time,
                    ingested_at,
                    value,
                    f"{series_id}-{index}-{value}",
                    "fred-test",
                )
            )
        return rows


def _stored_macro_rows(pipeline) -> set[tuple]:
    names = list(MACRO_FEATURES.values())
    symbols = [item.symbol for item in pipeline._universe.list_all()]
    return {
        (item.symbol, item.feature_name, item.event_time.replace(tzinfo=None),
         item.available_time.replace(tzinfo=None), item.value)
        for item in pipeline._features.list_features(symbols, names)
    }


def _full_rewrite_rows(pipeline) -> set[tuple]:
    """What the pre-S1-W07 full materialization would have stored."""
    assets = [
        item.symbol for item in pipeline._universe.list_active()
        if item.asset_type in {"EQUITY", "ETF"}
    ]
    return {
        (symbol, name, event.replace(tzinfo=None), available.replace(tzinfo=None), value)
        for name, points in pipeline.desired_series().items()
        for event, available, value in points
        for symbol in assets
    }


def test_macro_features_are_written_incrementally_and_match_a_full_rewrite(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'macro-inc.db'}"))
    pipeline = container.macro_data_pipeline
    pipeline._provider = FakeFredProvider(months=13)
    assets = [
        item.symbol for item in pipeline._universe.list_active()
        if item.asset_type in {"EQUITY", "ETF"}
    ]
    assert assets

    first = pipeline.run(datetime(2025, 2, 1, tzinfo=UTC))
    points_per_symbol = sum(len(points) for points in pipeline.desired_series().values())
    assert first.rebuilt_symbols == len(assets)
    assert first.feature_values == len(assets) * points_per_symbol

    unchanged = pipeline.run(datetime(2025, 2, 2, tzinfo=UTC))
    assert (unchanged.feature_values, unchanged.changed_points, unchanged.rebuilt_symbols) == (0, 0, 0)

    # One more month for every series: 7 new points (the YoY series now reach 2 points).
    pipeline._provider = FakeFredProvider(months=14)
    grown = pipeline.run(datetime(2025, 3, 2, tzinfo=UTC))
    assert grown.changed_points == 7
    assert grown.feature_values == 7 * len(assets)
    assert _stored_macro_rows(pipeline) == _full_rewrite_rows(pipeline)

    # A revised CPI base month changes the YoY of the month twelve months later.
    pipeline._provider = FakeFredProvider(months=14, revisions={("CPIAUCSL", 1): 99.0, ("DGS10", 3): 9.9})
    revised = pipeline.run(datetime(2025, 3, 3, tzinfo=UTC))
    assert revised.changed_points == 2
    assert _stored_macro_rows(pipeline) == _full_rewrite_rows(pipeline)


def test_new_symbols_get_the_full_macro_series(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'macro-new.db'}"))
    pipeline = container.macro_data_pipeline
    pipeline._provider = FakeFredProvider(months=13)
    pipeline.run(datetime(2025, 2, 1, tzinfo=UTC))
    existing = next(
        item for item in pipeline._universe.list_active()
        if item.asset_type in {"EQUITY", "ETF"}
    )
    pipeline._universe.add(replace(existing, symbol="9999.TW", company_abbreviation="測試"))

    result = pipeline.run(datetime(2025, 2, 2, tzinfo=UTC))

    assert result.rebuilt_symbols == 1 and result.changed_points == 0
    assert result.feature_values == sum(len(points) for points in pipeline.desired_series().values())
    assert _stored_macro_rows(pipeline) == _full_rewrite_rows(pipeline)


def test_macro_pipeline_is_idempotent_and_builds_point_in_time_features(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'macro.db'}"))
    container.macro_data_pipeline._provider = FakeFredProvider()

    first = container.macro_data_pipeline.run(datetime(2025, 2, 1, tzinfo=UTC))
    second = container.macro_data_pipeline.run(datetime(2025, 2, 1, tzinfo=UTC))
    overview = container.macro_data_overview_service.get_overview()

    assert first.status == "succeeded"
    assert first.inserted == 91
    assert first.feature_values > 0
    assert second.inserted == 0
    assert overview["total_rows"] == 91
    assert overview["series_count"] == 7


def test_macro_dashboard_has_real_methodology_and_chart(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'macro-page.db'}"))
    response = create_app(container).test_client().get("/macro-data")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "總經資料中心" in body
    assert "ALFRED vintage" in body
    assert "macroChart" in body
    assert "美國消費者物價指數" in body
