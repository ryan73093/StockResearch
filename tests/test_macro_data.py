from datetime import UTC, datetime, timedelta

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.domain.entities import MacroObservation


class FakeFredProvider:
    def fetch(self, series_id: str, start: datetime, end: datetime) -> list[MacroObservation]:
        rows: list[MacroObservation] = []
        for index in range(13):
            event_time = datetime(2023 + index // 12, index % 12 + 1, 1, tzinfo=UTC)
            value = 100.0 + index if series_id in {"CPIAUCSL", "GDP"} else 1.0 + index / 10
            rows.append(
                MacroObservation(
                    None,
                    series_id,
                    event_time,
                    event_time + timedelta(days=20),
                    datetime(2025, 1, 1, tzinfo=UTC),
                    value,
                    f"{series_id}-{index}-{value}",
                    "fred-test",
                )
            )
        return rows


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
