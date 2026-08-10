from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
from zoneinfo import ZoneInfo

import pytest

from quant_platform.application.services import DataQualityError, MarketDataIngestionService
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import SqlAlchemyMarketBarRepository
from quant_platform.domain.entities import MarketBar
from quant_platform.data_sources.yahoo import YahooFinanceProvider


class FakeMarketDataProvider:
    name = "fake"

    def __init__(self, bars: list[MarketBar]) -> None:
        self._bars = bars

    def fetch_daily_bars(self, symbol, market, start, end):
        return self._bars


def make_bar(**changes) -> MarketBar:
    event_time = datetime(2025, 1, 2, 16, tzinfo=UTC)
    values = {
        "symbol": "SPY",
        "market": "US",
        "interval": "1d",
        "event_time": event_time,
        "available_time": event_time + timedelta(minutes=15),
        "ingested_at": datetime(2025, 1, 3, tzinfo=UTC),
        "open": Decimal("580"),
        "high": Decimal("590"),
        "low": Decimal("575"),
        "close": Decimal("588"),
        "adjusted_close": Decimal("588"),
        "volume": 1_000_000,
        "source": "fake",
    }
    values.update(changes)
    return MarketBar(**values)


def test_ingestion_is_idempotent_and_reports_coverage(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'market.db'}"))
    repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    service = MarketDataIngestionService(FakeMarketDataProvider([make_bar()]), repository)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 4, tzinfo=UTC)

    first = service.ingest_daily("spy", "US", start, end)
    second = service.ingest_daily("SPY", "US", start, end)

    assert first.inserted == 1
    assert second.inserted == 0
    assert second.duplicates == 1
    coverage = repository.list_coverage()
    assert len(coverage) == 1
    assert coverage[0].symbol == "SPY"
    assert coverage[0].row_count == 1


def test_sqlite_market_bars_are_normalized_to_utc(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'utc.db'}"))
    repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    taipei = ZoneInfo("Asia/Taipei")
    local_close = datetime(2025, 7, 18, 13, 30, tzinfo=taipei)
    bar = make_bar(
        symbol="2324.TW",
        market="TW",
        event_time=local_close,
        available_time=local_close + timedelta(minutes=15),
        ingested_at=local_close + timedelta(minutes=20),
    )

    assert repository.add_missing([bar]) == 1
    stored = repository.list_bars("2324.TW")[0]

    assert stored.event_time == datetime(2025, 7, 18, 5, 30, tzinfo=UTC)
    assert stored.available_time == datetime(2025, 7, 18, 5, 45, tzinfo=UTC)
    assert stored.ingested_at == datetime(2025, 7, 18, 5, 50, tzinfo=UTC)


def test_rejects_bar_available_before_market_event(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'quality.db'}"))
    repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    invalid = make_bar(available_time=datetime(2025, 1, 2, 15, tzinfo=UTC))
    service = MarketDataIngestionService(FakeMarketDataProvider([invalid]), repository)

    with pytest.raises(DataQualityError, match="cannot precede"):
        service.ingest_daily(
            "SPY",
            "US",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 4, tzinfo=UTC),
        )


def test_rejects_daily_bar_ingested_before_it_is_available(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'future.db'}"))
    repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    event_time = datetime(2025, 1, 2, 16, tzinfo=UTC)
    invalid = make_bar(
        event_time=event_time,
        available_time=event_time + timedelta(minutes=15),
        ingested_at=event_time + timedelta(minutes=5),
    )
    service = MarketDataIngestionService(FakeMarketDataProvider([invalid]), repository)

    with pytest.raises(DataQualityError, match="before its available_time"):
        service.ingest_daily(
            "SPY",
            "US",
            datetime(2025, 1, 1, tzinfo=UTC),
            datetime(2025, 1, 4, tzinfo=UTC),
        )


def test_empty_provider_response_is_a_failure_not_a_zero_row_success(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'empty.db'}"))
    repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    service = MarketDataIngestionService(FakeMarketDataProvider([]), repository)

    with pytest.raises(DataQualityError, match="returned no daily bars"):
        service.ingest_daily(
            "0050.TW",
            "TW",
            datetime(2021, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_yahoo_chart_fallback_parses_adjusted_daily_bars(monkeypatch):
    payload = {
        "chart": {
            "error": None,
            "result": [{
                "timestamp": [1735781400],
                "indicators": {
                    "quote": [{
                        "open": [195.0], "high": [198.0], "low": [194.0],
                        "close": [197.0], "volume": [1234567],
                    }],
                    "adjclose": [{"adjclose": [52.75]}],
                },
            }],
        }
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    monkeypatch.setattr("quant_platform.data_sources.yahoo.urlopen", lambda *_args, **_kwargs: Response())
    YahooFinanceProvider._last_request_at = 0.0
    bars = YahooFinanceProvider()._fetch_chart_bars(
        "0050.TW", "TW",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 4, tzinfo=UTC),
    )

    assert len(bars) == 1
    assert bars[0].symbol == "0050.TW"
    assert bars[0].close == Decimal("197.0")
    assert bars[0].adjusted_close == Decimal("52.75")
    assert bars[0].source == "yahoo_finance"


def test_data_dashboard_renders_empty_state(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'page.db'}"))
    from quant_platform.dashboard.app import create_app

    response = create_app(container).test_client().get("/data")
    assert response.status_code == 200
    assert "NO DATASETS YET" in response.get_data(as_text=True)
