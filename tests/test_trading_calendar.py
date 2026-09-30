from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quant_platform.application.after_hours_ai import AfterHoursAiService
from quant_platform.application.services import MarketDataIngestionService
from quant_platform.application.universe import DailyMarketDataPipeline
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import (
    SqlAlchemyMarketBarRepository,
    SqlAlchemyResearchUniverseRepository,
    SqlAlchemySchedulerJobRunRepository,
)
from types import SimpleNamespace

from quant_platform.domain.entities import DailyDecision, MarketBar
from quant_platform.market_calendar import (
    MarketCalendarStore,
    MarketClosure,
    TradingCalendar,
    parse_twse_holiday_schedule,
)
from quant_platform.scheduler.runner import run_scheduled_workflow, run_startup_catch_up

TAIPEI = timezone(timedelta(hours=8))
DECISION_TIME = datetime(2026, 9, 24, 5, 50, tzinfo=UTC)


def _decision(gates: str = "[]") -> DailyDecision:
    return DailyDecision(
        id=1, market="TW", symbol="2330.TW", decision_version="1.0.0",
        event_time=DECISION_TIME, status="候選", score=0.82, predicted_return_5d=0.03,
        prediction_dispersion=0.01, model_rank=0.9, regime="BULL_NORMAL_VOL",
        factor_score=0.7, strategy_score=0.8, suggested_weight=0.10,
        reasons_json='["多模型與策略一致偏多"]', risks_json="[]", gate_checks_json=gates,
        computed_at=DECISION_TIME,
    )


def _bar() -> MarketBar:
    return MarketBar(
        symbol="2330.TW", market="TW", interval="1d", event_time=DECISION_TIME,
        available_time=DECISION_TIME, ingested_at=DECISION_TIME, open=Decimal("98"),
        high=Decimal("102"), low=Decimal("97"), close=Decimal("100"),
        adjusted_close=Decimal("100"), volume=1_000_000, source="test",
    )


class DecisionRepo:
    def __init__(self, decision):
        self.decision = decision

    def list_latest(self, market=None):
        return [self.decision]


class PaperService:
    def overview(self, now=None):
        return SimpleNamespace(
            account=SimpleNamespace(cash=Decimal("1000000")), positions=(),
            equity=Decimal("1000000"), orders=(), fills=(), total_return=Decimal("0"),
        )


def _taipei(year, month, day, hour=14, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=TAIPEI)


# --- TWSE schedule parsing -------------------------------------------------


def test_parse_keeps_closures_and_drops_informational_trading_days():
    payload = {
        "stat": "ok",
        "data": [
            ["2026-01-01", "中華民國開國紀念日", "依規定放假1日。"],
            ["2026-01-02", "國曆新年開始交易日", "國曆新年開始交易。"],
            ["2026-02-11", "農曆春節前最後交易日", "農曆春節前最後交易。\r\n"],
            ["2026-02-12", "市場無交易，僅辦理結算交割作業", ""],
            ["2026-09-25", "中秋節", "依規定放假1日。<br><br>"],
        ],
    }

    closures = parse_twse_holiday_schedule(payload, 2026)

    assert [item.day for item in closures] == [
        date(2026, 1, 1), date(2026, 2, 12), date(2026, 9, 25),
    ]
    assert closures[-1].description == "依規定放假1日。"
    assert all(item.source == "twse" for item in closures)


def test_parse_treats_empty_year_as_unpublished_and_rejects_bad_payload():
    assert parse_twse_holiday_schedule({"stat": "ok", "data": [], "total": 0}, 2027) is None
    with pytest.raises(ValueError):
        parse_twse_holiday_schedule({"stat": "error"}, 2026)


# --- Calendar rules ---------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    return MarketCalendarStore(tmp_path)


def test_packaged_calendar_closes_2026_mid_autumn_and_teachers_day(store):
    calendar = store.calendar("TW")

    assert calendar.covers(date(2026, 9, 25))
    assert not calendar.is_trading_day(date(2026, 9, 25))
    assert not calendar.is_trading_day(date(2026, 9, 28))
    assert calendar.closure(date(2026, 9, 25)).name == "中秋節"
    assert calendar.previous_trading_day(date(2026, 9, 28)) == date(2026, 9, 24)
    assert calendar.next_trading_day(date(2026, 9, 24)) == date(2026, 9, 29)


def test_lunar_new_year_settlement_days_are_closed_and_counted_correctly(store):
    calendar = store.calendar("TW")

    assert calendar.is_trading_day(date(2026, 2, 11))
    assert not calendar.is_trading_day(date(2026, 2, 12))
    assert calendar.next_trading_day(date(2026, 2, 11)) == date(2026, 2, 23)
    assert calendar.sessions_after(date(2026, 2, 11), date(2026, 2, 23)) == 1


def test_uncovered_year_falls_back_to_weekdays_and_reports_it(store):
    calendar = store.calendar("TW")

    assert not calendar.covers(date(2031, 1, 1))
    assert calendar.is_trading_day(date(2031, 1, 1))
    assert not calendar.is_trading_day(date(2031, 1, 4))


def test_other_markets_use_weekday_rule_without_coverage_warning(store):
    calendar = store.calendar("US")

    assert calendar.holiday_aware is False
    assert calendar.covers(date(2031, 1, 1))
    assert calendar.is_trading_day(date(2026, 9, 25))


def test_manual_typhoon_closure_is_applied_and_removable(store):
    typhoon = date(2026, 10, 15)
    assert store.is_trading_day("TW", typhoon)

    store.add_manual_closure(typhoon, "颱風休市", note="人事行政總處公告")
    closure = store.calendar("TW").closure(typhoon)

    assert closure.name == "颱風休市"
    assert closure.source == "manual"
    assert not store.is_trading_day("TW", typhoon)
    assert store.remove_manual_closure(typhoon)
    assert store.is_trading_day("TW", typhoon)


def test_manual_closure_requires_reason(store):
    with pytest.raises(ValueError):
        store.add_manual_closure(date(2026, 10, 15), "  ")


def test_calendar_walk_rejects_corrupt_data():
    closures = [
        MarketClosure(date(2026, 1, 1) + timedelta(days=offset), "錯誤")
        for offset in range(60)
    ]
    with pytest.raises(ValueError):
        TradingCalendar("TW", closures, [2026]).previous_trading_day(date(2026, 2, 20))


# --- Refresh ----------------------------------------------------------------


class FakeScheduleClient:
    def __init__(self, published=None, fail=False):
        self.published = published or {}
        self.fail = fail
        self.calls = []

    def source_url(self, year):
        return f"https://example.test/{year}"

    def fetch_year(self, year):
        self.calls.append(year)
        if self.fail:
            raise RuntimeError("network down")
        return self.published.get(year)


def test_refresh_stores_published_years_and_skips_unpublished(tmp_path):
    client = FakeScheduleClient({2031: [MarketClosure(date(2031, 1, 1), "開國紀念日")]})
    store = MarketCalendarStore(tmp_path, client=client)

    results = store.refresh([2031, 2032])
    calendar = store.calendar("TW")

    assert results == {2031: 1, 2032: None}
    assert calendar.covers(date(2031, 6, 1))
    assert not calendar.covers(date(2032, 6, 1))
    assert not calendar.is_trading_day(date(2031, 1, 1))


def test_refresh_if_due_runs_once_per_day_and_backs_off_after_failure(tmp_path):
    client = FakeScheduleClient({2026: []})
    store = MarketCalendarStore(tmp_path, client=client)
    morning = _taipei(2026, 10, 1, 7)

    store.refresh_if_due(morning)
    store.refresh_if_due(morning + timedelta(hours=3))
    assert client.calls == [2026, 2027]

    failing = FakeScheduleClient(fail=True)
    store = MarketCalendarStore(tmp_path, client=failing)
    assert store.refresh_if_due(morning) is None
    assert store.refresh_if_due(morning + timedelta(hours=1)) is None
    assert failing.calls == [2026]
    store.refresh_if_due(morning + timedelta(hours=7))
    assert failing.calls == [2026, 2026]


# --- Daily market pipeline --------------------------------------------------


def test_expected_session_rolls_back_over_holidays(store):
    calendar = store.calendar("TW")
    expected = DailyMarketDataPipeline.expected_session_date

    assert expected("TW", _taipei(2026, 9, 25), calendar) == date(2026, 9, 24)
    assert expected("TW", _taipei(2026, 9, 28), calendar) == date(2026, 9, 24)
    assert expected("TW", _taipei(2026, 9, 29, 13, 0), calendar) == date(2026, 9, 24)
    assert expected("TW", _taipei(2026, 9, 29, 14, 0), calendar) == date(2026, 9, 29)


def _official_pipeline(tmp_path, close_day: date, calendar_store: MarketCalendarStore):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'calendar.db'}"))
    universe = SqlAlchemyResearchUniverseRepository(container.database.session_factory)
    for asset in universe.list_all():
        universe.set_active(asset.symbol, False)
    container.research_universe_service.add_asset(
        "0050.TW", "TW", asset_type="ETF", data_start=date(2020, 1, 1)
    )
    bars = SqlAlchemyMarketBarRepository(container.database.session_factory)

    class NoYahooProvider:
        name = "yahoo_finance"

        def fetch_daily_bars(self, *_args, **_kwargs):
            return []

    class OfficialProvider:
        def fetch_snapshot(self, symbols, now):
            close = datetime.combine(close_day, datetime.min.time(), tzinfo=TAIPEI).replace(
                hour=13, minute=30
            )
            return [MarketBar(
                symbol=symbols[0], market="TW", interval="1d",
                event_time=close, available_time=close + timedelta(minutes=15),
                ingested_at=now, open=Decimal("100"), high=Decimal("101"),
                low=Decimal("99"), close=Decimal("100"), adjusted_close=Decimal("100"),
                volume=1_000, source="twse_tpex_official",
            )]

    return DailyMarketDataPipeline(
        universe,
        bars,
        MarketDataIngestionService(NoYahooProvider(), bars),
        SqlAlchemySchedulerJobRunRepository(container.database.session_factory),
        OfficialProvider(),
        calendar_store=calendar_store,
    )


def test_holiday_run_accepts_previous_session_without_false_failure(tmp_path):
    pipeline = _official_pipeline(tmp_path, date(2026, 9, 24), MarketCalendarStore(tmp_path))

    result = pipeline.run("TW", now=_taipei(2026, 9, 25))

    assert result.status == "succeeded"
    assert result.expected_date == "2026-09-24"
    assert result.fresh is True


def test_trading_day_with_old_data_still_fails_closed_with_typhoon_hint(tmp_path):
    pipeline = _official_pipeline(tmp_path, date(2026, 9, 24), MarketCalendarStore(tmp_path))

    result = pipeline.run("TW", now=_taipei(2026, 9, 29))

    assert result.status == "failed"
    assert result.expected_date == "2026-09-29"
    assert "add-closure 2026-09-29" in result.failures["__market_freshness__"]


def test_manual_typhoon_closure_makes_previous_session_current(tmp_path):
    store = MarketCalendarStore(tmp_path)
    store.add_manual_closure(date(2026, 9, 29), "颱風休市")
    pipeline = _official_pipeline(tmp_path, date(2026, 9, 24), store)

    result = pipeline.run("TW", now=_taipei(2026, 9, 29))

    assert result.status == "succeeded"
    assert result.expected_date == "2026-09-24"


# --- Scheduler --------------------------------------------------------------


def _scheduler_container(tmp_path):
    return build_container(Settings(
        database_url=f"sqlite:///{tmp_path / 'scheduler.db'}",
        scheduler_enabled=True,
        scheduler_timezone="Asia/Taipei",
        tw_data_schedule="13:35",
        us_data_schedule="06:30",
    ))


def test_scheduled_workflow_skips_tw_holiday_but_runs_us(tmp_path):
    container = _scheduler_container(tmp_path)
    calls = []
    container.automation_service.execute = lambda job_key: calls.append(job_key) or "ran"

    skipped = run_scheduled_workflow(
        container, "tw_daily_market_data", "TW", "Asia/Taipei", _taipei(2026, 9, 25, 13, 35)
    )
    ran = run_scheduled_workflow(
        container, "us_daily_market_data", "US", "Asia/Taipei", _taipei(2026, 9, 25, 6, 30)
    )

    assert skipped is None
    assert ran == "ran"
    assert calls == ["us_daily_market_data"]


def test_startup_catch_up_does_not_rerun_tw_on_holiday(tmp_path):
    container = _scheduler_container(tmp_path)
    calls = []

    def record(job_key):
        calls.append(job_key)
        return type("Result", (), {"status": "succeeded"})()

    container.automation_service.execute = record
    executed = run_startup_catch_up(container, _taipei(2026, 9, 25, 14, 0))

    assert "tw_daily_market_data" not in executed
    assert "tw_daily_market_data" not in calls


# --- Data quality and after-hours plan -------------------------------------


def test_data_quality_warns_when_current_year_is_not_covered(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'dq.db'}"))

    uncovered = container.data_quality_service.evaluate(
        "TW", as_of=datetime(2031, 1, 6, 8, tzinfo=UTC), stage="raw"
    )
    covered = container.data_quality_service.evaluate(
        "TW", as_of=datetime(2026, 9, 29, 8, tzinfo=UTC), stage="raw"
    )

    assert any(item.code == "CALENDAR_COVERAGE" for item in uncovered.issues)
    assert not any(item.code == "CALENDAR_COVERAGE" for item in covered.issues)


def test_after_hours_plan_names_the_holiday(tmp_path):
    blocked = _decision(gates='["股票池少於 30 檔"]')
    plan = AfterHoursAiService(
        DecisionRepo(blocked), BarRepoOn(date(2026, 9, 24)), PaperService(),
        calendar_store=MarketCalendarStore(tmp_path),
    ).generate(datetime(2026, 9, 25, 6, 0, tzinfo=UTC))

    assert plan.headline == "今日休市（中秋節）：不交易"


def test_after_hours_plan_previews_pre_holiday_close_on_holiday(tmp_path):
    close = datetime(2026, 9, 24, 5, 50, tzinfo=UTC)
    decision = replace(_decision(), event_time=close, computed_at=close)
    plan = AfterHoursAiService(
        DecisionRepo(decision), BarRepoOn(date(2026, 9, 24)), PaperService(),
        calendar_store=MarketCalendarStore(tmp_path),
    ).generate(datetime(2026, 9, 28, 6, 0, tzinfo=UTC))

    assert plan.buy_count == 1
    assert plan.mode.startswith("休市預覽")
    assert plan.submission_allowed is False


class BarRepoOn:
    def __init__(self, day: date):
        self._close = datetime.combine(day, datetime.min.time(), tzinfo=UTC).replace(
            hour=5, minute=30
        )

    def list_bars(self, symbol, interval="1d", source=None, as_of=None):
        if not symbol.endswith(".TW"):
            return []
        return [replace(
            _bar(), event_time=self._close, available_time=self._close,
            ingested_at=self._close,
        )]
