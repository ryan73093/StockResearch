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


TAIPEI_TZ = timezone(timedelta(hours=8))
TW_CLOSE = datetime(2025, 1, 3, 13, 30, tzinfo=TAIPEI_TZ)


def official_bar(symbol, now, close=TW_CLOSE):
    return MarketBar(
        symbol=symbol, market="TW", interval="1d", event_time=close,
        available_time=close + timedelta(minutes=15), ingested_at=now, open=Decimal("100"),
        high=Decimal("101"), low=Decimal("99"), close=Decimal("100"), adjusted_close=Decimal("100"),
        volume=1_000, source="twse_tpex_official",
    )


class LateOfficialProvider:
    """The official table is empty until ``ready_after`` polls (S1-W05: 13:51 on 2026-10-01)."""

    def __init__(self, ready_after):
        self.ready_after = ready_after
        self.polls = 0

    def fetch_snapshot(self, symbols, now):
        return []

    def fetch_range(self, symbols, start, end, now):
        self.polls += 1
        return [official_bar(symbol, now) for symbol in symbols] if self.polls >= self.ready_after else []


def tw_pipeline(tmp_path, official, yahoo, wait_minutes=10):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'tw.db'}"))
    universe_repository = SqlAlchemyResearchUniverseRepository(container.database.session_factory)
    for asset in universe_repository.list_all():
        universe_repository.set_active(asset.symbol, False)
    container.research_universe_service.add_asset("0050.TW", "TW", asset_type="ETF", data_start=date(2020, 1, 1))
    market_repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    started = datetime(2025, 1, 3, 5, 50, tzinfo=UTC)  # 13:50 Taipei
    clock = {"now": started}
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        clock["now"] += timedelta(seconds=seconds)

    pipeline = DailyMarketDataPipeline(
        universe_repository, market_repository, MarketDataIngestionService(yahoo, market_repository),
        SqlAlchemySchedulerJobRunRepository(container.database.session_factory), official,
        official_wait=timedelta(minutes=wait_minutes), official_poll_seconds=30, sleep=sleep,
        clock=lambda: clock["now"],
    )
    return pipeline, market_repository, started, sleeps


class NoYahoo:
    name = "yahoo_finance"

    def fetch_daily_bars(self, *_args, **_kwargs):
        raise AssertionError("the official close covers this symbol")


def test_tw_run_waits_for_the_official_close_instead_of_asking_yahoo_per_symbol(tmp_path):
    official = LateOfficialProvider(ready_after=3)
    pipeline, _bars, started, sleeps = tw_pipeline(tmp_path, official, NoYahoo())

    result = pipeline.run("TW", now=started)

    assert result.status == "succeeded" and result.fresh and result.inserted == 1
    assert sleeps == [30, 30, 30]                      # 13:50:30, 13:51:00, 13:51:30


def test_tw_run_falls_back_to_yahoo_when_the_official_close_never_comes(tmp_path):
    class TodayYahoo(DynamicFakeProvider):
        calls = 0

        def fetch_daily_bars(self, symbol, market, start, end):
            from dataclasses import replace

            type(self).calls += 1
            return [replace(official_bar(symbol, end), source="yahoo_finance")]

    pipeline, _bars, started, sleeps = tw_pipeline(tmp_path, LateOfficialProvider(ready_after=10_000), TodayYahoo(),
                                                   wait_minutes=2)

    result = pipeline.run("TW", now=started)

    assert sleeps == [30, 30, 30, 30] and TodayYahoo.calls == 1
    assert result.status == "succeeded" and result.fresh


def test_tw_rerun_does_not_wait_when_the_official_close_is_stored(tmp_path):
    official = LateOfficialProvider(ready_after=10_000)
    pipeline, bars, started, sleeps = tw_pipeline(tmp_path, official, NoYahoo())
    bars.add_missing([official_bar("0050.TW", started)])

    result = pipeline.run("TW", now=started + timedelta(minutes=20))

    assert sleeps == [] and result.status == "succeeded"


def test_yahoo_skips_yfinance_after_three_empty_symbols(monkeypatch):
    import sys
    import types

    import pandas as pd

    from quant_platform.data_sources import yahoo as yahoo_module
    from quant_platform.data_sources.yahoo import YahooFinanceProvider

    tickers = []
    fake = types.SimpleNamespace(
        set_tz_cache_location=lambda path: None,
        Ticker=lambda symbol: tickers.append(symbol) or types.SimpleNamespace(history=lambda **kwargs: pd.DataFrame()),
    )
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    monkeypatch.setattr(yahoo_module.clock, "sleep", lambda seconds: None)
    monkeypatch.setattr(YahooFinanceProvider, "_yfinance_empty_streak", 0)
    monkeypatch.setattr(YahooFinanceProvider, "_yfinance_skip_until", 0.0)
    charts = []
    monkeypatch.setattr(YahooFinanceProvider, "_fetch_chart_bars",
                        lambda self, symbol, *args: charts.append(symbol) or [])
    provider = YahooFinanceProvider()
    start, end = datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 1, 3, tzinfo=UTC)

    for symbol in ("A.TW", "B.TW", "C.TW", "D.TW", "E.TW"):
        provider.fetch_daily_bars(symbol, "TW", start, end)

    assert sorted(set(tickers)) == ["A.TW", "B.TW", "C.TW"]   # three tries each, then no more yfinance
    assert charts == ["A.TW", "B.TW", "C.TW", "D.TW", "E.TW"]


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
            "database_backup",
            "close_availability_probe",
            "research_history_refresh",
            "forward_simulation",
            "line_plan_advice",
            "research_agent",
            "weekly_research_report",
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
