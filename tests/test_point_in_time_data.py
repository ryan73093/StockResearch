from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta

import pytest

from quant_platform.api.app import create_api
from quant_platform.application.point_in_time_data import PointInTimeDataService
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.database.repositories import SqlAlchemyPointInTimeDataRepository
from quant_platform.data_sources.finmind_pit import FinMindPointInTimeProvider
from quant_platform.data_sources.twse_pit import TwsePointInTimeProvider
from quant_platform.domain.entities import PointInTimeDataset, PointInTimeObservation


class FakePointInTimeProvider:
    name = "finmind"
    has_token = True

    def fetch(self, definition, entity_id, start, end, ingested_at):
        payload = {"date": "2026-07-01", "stock_id": entity_id, "close": 1000.0, "volume": 1234}
        canonical = json.dumps(payload, sort_keys=True)
        return [PointInTimeObservation(
            id=None,
            dataset_key=definition.dataset_key,
            entity_id=entity_id,
            event_time=datetime(2026, 7, 1, 1, 0, tzinfo=UTC),
            available_time=datetime(2026, 7, 1, 7, 50, tzinfo=UTC),
            ingested_at=ingested_at,
            revision_key=f"{entity_id}:minute=09:00",
            content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
            payload_json=canonical,
            source="finmind",
            source_uri="https://example.invalid/source",
        )]


def _service(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'pit.db'}"))
    repository = SqlAlchemyPointInTimeDataRepository(container.database.session_factory)
    return container, repository, PointInTimeDataService(repository, FakePointInTimeProvider())


def test_catalog_describes_intraday_derivatives_access_and_units(tmp_path) -> None:
    _, repository, _ = _service(tmp_path)
    datasets = {item.dataset_key: item for item in repository.list_datasets()}

    assert len(datasets) == 9
    assert datasets["tw_stock_1m"].source_dataset == "TaiwanStockKBar"
    assert datasets["tw_stock_1m"].access_tier == "sponsor"
    assert datasets["tw_futures_daily"].enabled is True
    assert datasets["tw_odd_lot_daily"].enabled is True
    assert json.loads(datasets["tw_options_daily"].unit_schema_json)["volume"] == "口"


def test_as_of_query_returns_only_revision_visible_at_that_time(tmp_path) -> None:
    _, repository, _ = _service(tmp_path)
    event = datetime(2026, 7, 1, 6, tzinfo=UTC)

    def revision(value: float, available: datetime) -> PointInTimeObservation:
        payload = json.dumps({"close": value}, sort_keys=True)
        return PointInTimeObservation(
            id=None, dataset_key="tw_futures_daily", entity_id="TX", event_time=event,
            available_time=available, ingested_at=available + timedelta(minutes=5),
            revision_key="TX:202607:regular", content_hash=hashlib.sha256(payload.encode()).hexdigest(),
            payload_json=payload, source="finmind", source_uri="https://example.invalid",
        )

    original = revision(23000, datetime(2026, 7, 1, 10, tzinfo=UTC))
    corrected = revision(23010, datetime(2026, 7, 2, 10, tzinfo=UTC))
    assert repository.add_revisions([original, corrected, original]) == 2

    before = repository.list_observations(
        "tw_futures_daily", "TX", as_of=datetime(2026, 7, 1, 12, tzinfo=UTC)
    )
    after = repository.list_observations(
        "tw_futures_daily", "TX", as_of=datetime(2026, 7, 2, 12, tzinfo=UTC)
    )
    assert json.loads(before[0].payload_json)["close"] == 23000
    assert json.loads(after[0].payload_json)["close"] == 23010
    coverage = repository.list_coverage()[0]
    assert coverage.row_count == 1
    assert coverage.revision_count == 1


def test_ingestion_is_idempotent_and_enforces_time_causality(tmp_path) -> None:
    _, repository, service = _service(tmp_path)
    now = datetime(2026, 7, 2, tzinfo=UTC)
    first = service.ingest(
        "tw_stock_1m", "2330", datetime(2026, 7, 1, tzinfo=UTC), now, now=now
    )
    second = service.ingest(
        "tw_stock_1m", "2330", datetime(2026, 7, 1, tzinfo=UTC), now, now=now
    )
    assert (first.received, first.inserted, second.inserted) == (1, 1, 0)

    bad = FakePointInTimeProvider()
    bad.fetch = lambda definition, entity_id, start, end, ingested_at: [PointInTimeObservation(
        id=None, dataset_key=definition.dataset_key, entity_id=entity_id,
        event_time=now, available_time=now + timedelta(hours=1), ingested_at=now,
        revision_key="future", content_hash="0" * 64, payload_json="{}",
        source="finmind", source_uri="x",
    )]
    with pytest.raises(ValueError, match="尚未公開"):
        PointInTimeDataService(repository, bad).ingest(
            "tw_futures_daily", "TX", now - timedelta(days=1), now, now=now
        )

    scheduled = service.run_scheduled(
        "tw_futures_daily:TX,tw_options_daily:TXO", 7, now=now
    )
    assert (scheduled.requested, scheduled.succeeded, scheduled.failed) == (2, 2, 0)


def test_finmind_normalization_uses_batch_publication_time_and_contract_keys() -> None:
    provider = FinMindPointInTimeProvider("https://example.invalid", token="token")
    definition = PointInTimeDataset(
        dataset_key="tw_stock_1m", display_name="台股一分鐘 K 線", category="盤中行情",
        market="TW", frequency="1m", entity_type="股票", source="finmind",
        source_dataset="TaiwanStockKBar", access_tier="sponsor", unit_schema_json="{}",
        publication_rule="15:50", revision_policy="versioned", history_start=date(2019, 1, 1),
        source_url="https://example.invalid", enabled=True, updated_at=datetime.now(UTC),
    )
    rows = provider._normalize(definition, "2330", [{
        "date": "2026-07-01", "minute": "09:00:00", "stock_id": "2330",
        "open": 1000, "high": 1005, "low": 998, "close": 1002, "volume": 100,
    }], datetime(2026, 7, 1, 8, tzinfo=UTC))

    assert rows[0].event_time == datetime(2026, 7, 1, 1, tzinfo=UTC)
    assert rows[0].available_time == datetime(2026, 7, 1, 7, 50, tzinfo=UTC)
    assert "minute" in rows[0].revision_key


def test_twse_odd_lot_snapshot_keeps_actual_download_time() -> None:
    provider = TwsePointInTimeProvider()
    definition = PointInTimeDataset(
        dataset_key="tw_odd_lot_daily", display_name="集中市場零股交易行情",
        category="盤後零股", market="TW", frequency="1d", entity_type="股票",
        source="twse_openapi", source_dataset="TWT53U", access_tier="public_latest",
        unit_schema_json='{"TradeVolume":"股"}', publication_rule="snapshot",
        revision_policy="versioned", history_start=None, source_url=provider.endpoint,
        enabled=True, updated_at=datetime.now(UTC),
    )
    downloaded = datetime(2026, 7, 1, 7, 5, tzinfo=UTC)
    rows = provider._normalize(definition, "2330", [{
        "Code": "2330", "Name": "台積電", "TradeVolume": "1234",
        "TradePrice": "1000.00",
    }, {"Code": "2317", "TradeVolume": "500"}], downloaded)

    assert len(rows) == 1
    assert rows[0].event_time == downloaded
    assert rows[0].available_time == downloaded
    assert rows[0].ingested_at == downloaded
    assert rows[0].source == "twse_openapi"
    payload = json.loads(rows[0].payload_json)
    assert payload["TradeVolume"] == 1234
    assert payload["TradePrice"] == 1000.0


def test_point_in_time_page_api_and_numeric_units(tmp_path) -> None:
    container, repository, service = _service(tmp_path)
    container.point_in_time_data_service = service
    service.ingest(
        "tw_stock_1m", "2330", datetime(2026, 7, 1, tzinfo=UTC),
        datetime(2026, 7, 2, tzinfo=UTC), now=datetime(2026, 7, 2, tzinfo=UTC),
    )
    page = create_app(container).test_client().get(
        "/point-in-time-data?dataset=tw_stock_1m&entity=2330"
    ).get_data(as_text=True)
    assert "盤中與衍生資料" in page
    assert "1,000 元／股" in page
    assert "1,234 股" in page
    assert "1 筆" in page
    assert "個版本" in page

    api_paths = {route.path for route in create_api(container).routes}
    assert "/api/v1/point-in-time/datasets" in api_paths
    assert "/api/v1/point-in-time/observations" in api_paths
    assert "/api/v1/point-in-time/ingestions" in api_paths
