from __future__ import annotations

import json
from datetime import UTC, datetime
from io import BytesIO

import pytest

from quant_platform.api.app import create_api
from quant_platform.application.google_trends import GoogleTrendsCsvImportService
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.database.repositories import SqlAlchemyPointInTimeDataRepository


def _service(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'trends.db'}"))
    repository = SqlAlchemyPointInTimeDataRepository(container.database.session_factory)
    return repository, GoogleTrendsCsvImportService(repository)


def test_google_trends_csv_import_is_point_in_time_and_idempotent(tmp_path) -> None:
    repository, service = _service(tmp_path)
    downloaded = datetime(2026, 7, 20, 8, tzinfo=UTC)
    content = (
        "Category: All categories\n\n"
        "Week,台積電: (Taiwan),TSMC: (Taiwan),isPartial\n"
        "2026-07-05,80,40,FALSE\n"
        "2026-07-12,<1,50,TRUE\n"
    )

    first = service.import_csv(content, downloaded_at=downloaded)
    second = service.import_csv(content, downloaded_at=downloaded)

    assert (first.series_count, first.received, first.inserted, first.partial_count) == (2, 4, 4, 2)
    assert second.inserted == 0
    rows = repository.list_observations("google_trends", "台積電")
    assert len(rows) == 2
    latest = json.loads(rows[0].payload_json)
    assert latest["interest"] == 0.5
    assert latest["interest_label"] == "<1"
    assert latest["is_partial"] is True
    assert latest["query_context"] == {"category": "All categories"}
    assert rows[0].available_time == downloaded
    assert rows[0].source == "google_trends_csv"


def test_google_trends_revision_only_appears_after_new_download(tmp_path) -> None:
    repository, service = _service(tmp_path)
    original_time = datetime(2026, 7, 20, 8, tzinfo=UTC)
    revised_time = datetime(2026, 7, 21, 8, tzinfo=UTC)
    original = "Week,台積電: (Taiwan)\n2026-07-05,80\n"
    revised = "Week,台積電: (Taiwan)\n2026-07-05,81\n"

    service.import_csv(original, downloaded_at=original_time)
    service.import_csv(revised, downloaded_at=revised_time)

    before = repository.list_observations("google_trends", "台積電", as_of=original_time)
    after = repository.list_observations("google_trends", "台積電", as_of=revised_time)
    assert json.loads(before[0].payload_json)["interest"] == 80
    assert json.loads(after[0].payload_json)["interest"] == 81
    coverage = next(
        item for item in repository.list_coverage() if item.dataset_key == "google_trends"
    )
    assert (coverage.row_count, coverage.revision_count) == (1, 1)


def test_google_trends_csv_rejects_ambiguous_hourly_and_future_rows(tmp_path) -> None:
    _, service = _service(tmp_path)
    with pytest.raises(ValueError, match="時區"):
        service.import_csv(
            "Hour,台積電: (Taiwan)\n2026-07-20 08:00,10\n",
            downloaded_at=datetime(2026, 7, 20, 9, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="晚於資料取得時間"):
        service.import_csv(
            "Day,台積電: (Taiwan)\n2026-07-21,10\n",
            downloaded_at=datetime(2026, 7, 20, 9, tzinfo=UTC),
        )


def test_google_trends_keeps_different_query_contexts_as_distinct_series(tmp_path) -> None:
    repository, service = _service(tmp_path)
    downloaded = datetime(2026, 7, 20, 8, tzinfo=UTC)
    service.import_csv(
        "Category: All categories\n\nWeek,台積電: (Taiwan)\n2026-07-05,80\n",
        downloaded_at=downloaded,
    )
    service.import_csv(
        "Category: Finance\n\nWeek,台積電: (Taiwan)\n2026-07-05,60\n",
        downloaded_at=downloaded,
    )

    rows = repository.list_observations("google_trends", "台積電")
    assert len(rows) == 2
    assert {json.loads(row.payload_json)["interest"] for row in rows} == {60, 80}
    coverage = next(
        item for item in repository.list_coverage() if item.dataset_key == "google_trends"
    )
    assert (coverage.row_count, coverage.revision_count) == (2, 0)


def test_google_trends_dashboard_upload_and_api_surface(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'trends-web.db'}"))
    client = create_app(container).test_client()
    empty_page = client.get("/google-trends")
    assert empty_page.status_code == 200
    assert "Google 搜尋趨勢" in empty_page.get_data(as_text=True)
    assert "hero-panel" not in empty_page.get_data(as_text=True)

    response = client.post(
        "/google-trends/import",
        data={
            "downloaded_at": "2026-07-20T16:00",
            "csv_file": (
                BytesIO("Week,台積電: (Taiwan)\n2026-07-05,80\n".encode()),
                "multiTimeline.csv",
            ),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Google Trends 匯入完成" in body
    assert "80／100" in body
    overview = container.google_trends_service.overview("台積電")
    assert (overview.keyword_count, overview.observation_count) == (1, 1)
    assert overview.latest_available_time == datetime(2026, 7, 20, 8, tzinfo=UTC)

    paths = {route.path for route in create_api(container).routes}
    assert "/api/v1/google-trends" in paths
    assert "/api/v1/google-trends/imports" in paths
