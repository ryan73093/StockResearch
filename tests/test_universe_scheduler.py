from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from quant_platform.application.services import MarketDataIngestionService
from quant_platform.application.universe import DailyMarketDataPipeline
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import (
    SqlAlchemyMarketBarRepository,
    SqlAlchemyResearchUniverseRepository,
    SqlAlchemySchedulerJobRunRepository,
)
from quant_platform.domain.entities import MarketBar
from quant_platform.scheduler.runner import run_startup_catch_up, start_background_scheduler


class DynamicFakeProvider:
    name = "yahoo_finance"

    def fetch_daily_bars(self, symbol, market, start, end):
        event_time = datetime(2025, 1, 2, 16, tzinfo=UTC)
        price = Decimal("100")
        return [
            MarketBar(
                symbol=symbol,
                market=market,
                interval="1d",
                event_time=event_time,
                available_time=event_time + timedelta(minutes=15),
                ingested_at=event_time + timedelta(hours=1),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                adjusted_close=price,
                volume=1_000,
                source=self.name,
            )
        ]


def test_universe_add_and_soft_deactivate(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'universe.db'}"))
    service = container.research_universe_service

    asset = service.add_asset(
        symbol="aapl",
        market="US",
        asset_type="EQUITY",
        sector="Technology",
        benchmark_symbol="SPY",
        data_start=date(2020, 1, 1),
    )
    assert asset.symbol == "AAPL"
    assert "AAPL" in service.active_symbols("US")

    assert service.set_active("AAPL", False)
    assert "AAPL" not in service.active_symbols("US")
    assert any(item.symbol == "AAPL" and not item.active for item in service.list_all())


def test_daily_pipeline_is_audited_and_idempotent(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'pipeline.db'}"))
    universe_repository = SqlAlchemyResearchUniverseRepository(container.database.session_factory)
    for asset in universe_repository.list_all():
        universe_repository.set_active(asset.symbol, False)
    container.research_universe_service.add_asset("AAPL", "US", data_start=date(2020, 1, 1))

    market_repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    run_repository = SqlAlchemySchedulerJobRunRepository(container.database.session_factory)
    pipeline = DailyMarketDataPipeline(
        universe_repository,
        market_repository,
        MarketDataIngestionService(DynamicFakeProvider(), market_repository),
        run_repository,
    )

    first = pipeline.run("US", now=datetime(2025, 1, 3, tzinfo=UTC))
    second = pipeline.run("US", now=datetime(2025, 1, 3, 1, tzinfo=UTC))

    assert first.status == "succeeded"
    assert first.inserted == 1
    assert second.inserted == 0
    runs = pipeline.list_recent_runs()
    assert len(runs) == 2
    assert all(run.status.value == "succeeded" for run in runs)


def test_daily_pipeline_fails_closed_when_latest_session_is_stale(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'stale.db'}"))
    universe_repository = SqlAlchemyResearchUniverseRepository(container.database.session_factory)
    for asset in universe_repository.list_all():
        universe_repository.set_active(asset.symbol, False)
    container.research_universe_service.add_asset(
        "0050.TW", "TW", asset_type="ETF", data_start=date(2020, 1, 1)
    )
    market_repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    run_repository = SqlAlchemySchedulerJobRunRepository(container.database.session_factory)
    class StaleTwProvider(DynamicFakeProvider):
        def fetch_daily_bars(self, symbol, market, start, end):
            values = super().fetch_daily_bars(symbol, market, start, end)
            stale_close = datetime(2025, 1, 2, 5, 30, tzinfo=UTC)
            return [
                MarketBar(
                    symbol=item.symbol,
                    market=item.market,
                    interval=item.interval,
                    event_time=stale_close,
                    available_time=stale_close + timedelta(minutes=15),
                    ingested_at=item.ingested_at,
                    open=item.open,
                    high=item.high,
                    low=item.low,
                    close=item.close,
                    adjusted_close=item.adjusted_close,
                    volume=item.volume,
                    source=item.source,
                )
                for item in values
            ]

    pipeline = DailyMarketDataPipeline(
        universe_repository,
        market_repository,
        MarketDataIngestionService(StaleTwProvider(), market_repository),
        run_repository,
    )

    result = pipeline.run(
        "TW",
        now=datetime(2025, 1, 3, 6, 0, tzinfo=UTC),  # 14:00 Taipei
    )

    assert result.status == "failed"
    assert result.fresh is False
    assert result.expected_date == "2025-01-03"
    assert "__market_freshness__" in result.failures


def test_tw_daily_pipeline_uses_bulk_official_close_without_yahoo(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'bulk.db'}"))
    universe_repository = SqlAlchemyResearchUniverseRepository(container.database.session_factory)
    for asset in universe_repository.list_all():
        universe_repository.set_active(asset.symbol, False)
    container.research_universe_service.add_asset(
        "0050.TW", "TW", asset_type="ETF", data_start=date(2020, 1, 1)
    )
    market_repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    run_repository = SqlAlchemySchedulerJobRunRepository(container.database.session_factory)

    class NoYahooProvider:
        name = "yahoo_finance"

        def fetch_daily_bars(self, *_args, **_kwargs):
            raise AssertionError("official-covered symbol must not call Yahoo")

    class BulkOfficialProvider:
        def fetch_snapshot(self, symbols, now):
            close = datetime(2025, 1, 3, 13, 30, tzinfo=timezone(timedelta(hours=8)))
            return [
                MarketBar(
                    symbol=symbols[0], market="TW", interval="1d",
                    event_time=close, available_time=close + timedelta(minutes=15),
                    ingested_at=now, open=Decimal("100"), high=Decimal("101"),
                    low=Decimal("99"), close=Decimal("100"),
                    adjusted_close=Decimal("100"), volume=1_000,
                    source="twse_tpex_official",
                )
            ]

    pipeline = DailyMarketDataPipeline(
        universe_repository,
        market_repository,
        MarketDataIngestionService(NoYahooProvider(), market_repository),
        run_repository,
        BulkOfficialProvider(),
    )

    result = pipeline.run("TW", now=datetime(2025, 1, 3, 6, 0, tzinfo=UTC))

    assert result.status == "succeeded"
    assert result.inserted == 1
    assert result.succeeded == 1
    assert result.failed == 0


def test_background_scheduler_registers_market_specific_jobs(tmp_path):
    container = build_container(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'schedule.db'}",
            scheduler_enabled=True,
            scheduler_timezone="Asia/Taipei",
            tw_data_schedule="14:30",
            us_data_schedule="06:30",
        )
    )
    scheduler = start_background_scheduler(container)
    assert scheduler is not None
    try:
        assert {job.id for job in scheduler.get_jobs()} == {
            "tw_daily_market_data",
            "us_daily_market_data",
            "startup_data_catch_up",
            "daily_data_freshness_guard",
            "tw_universe_continuous_backfill",
            "market_calendar_refresh",
            "prediction_archive",
        }
    finally:
        scheduler.shutdown(wait=False)


def test_startup_catch_up_runs_only_due_missing_market(tmp_path):
    container = build_container(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'catch-up.db'}",
            scheduler_enabled=True,
            scheduler_timezone="Asia/Taipei",
            tw_data_schedule="13:35",
            us_data_schedule="06:30",
        )
    )
    calls: list[str] = []
    original = container.automation_service.execute

    def record(job_key):
        calls.append(job_key)
        return type("Result", (), {"status": "succeeded"})()

    container.automation_service.execute = record
    try:
        executed = run_startup_catch_up(
            container,
            datetime(2026, 7, 24, 14, 0, tzinfo=timezone(timedelta(hours=8))),
        )
    finally:
        container.automation_service.execute = original

    assert executed == ("tw_daily_market_data", "us_daily_market_data")
    assert calls == ["tw_daily_market_data", "us_daily_market_data"]


def test_startup_catch_up_sees_todays_success_behind_many_later_runs(tmp_path):
    container = build_container(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'catch-up-success.db'}",
            scheduler_enabled=True,
            scheduler_timezone="Asia/Taipei",
            tw_data_schedule="13:35",
            us_data_schedule="06:30",
        )
    )
    runs = SqlAlchemySchedulerJobRunRepository(container.database.session_factory)
    # 14:00 Taipei is stored as naive 06:00 UTC; it must still count as after
    # the 13:35 Taipei due time, even with 40 newer sub-job rows on top.
    succeeded_at = datetime(2026, 7, 24, 6, 0, tzinfo=UTC)
    run_id = runs.start("daily_market_data", "TW", succeeded_at)
    runs.finish(run_id, "succeeded", succeeded_at + timedelta(minutes=1), "{}", None)
    for index in range(40):
        started = succeeded_at + timedelta(minutes=2, seconds=index)
        sub_id = runs.start("feature_label_build", "TW", started)
        runs.finish(sub_id, "succeeded", started, "{}", None)
    container.daily_market_data_pipeline.is_fresh = lambda market, now=None: True
    calls: list[str] = []

    def record(job_key):
        calls.append(job_key)
        return type("Result", (), {"status": "succeeded"})()

    container.automation_service.execute = record
    run_startup_catch_up(
        container, datetime(2026, 7, 24, 15, 0, tzinfo=timezone(timedelta(hours=8)))
    )

    assert "tw_daily_market_data" not in calls
    assert calls == ["us_daily_market_data"]
