from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from quant_platform.config import Settings
from quant_platform.config.settings import (
    DEFAULT_PAUSED_MODULES,
    PAUSABLE_MODULES,
    parse_paused_modules,
)
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.runtime.locks import LocalExecutionLockManager


def test_automation_defaults_persist_and_can_be_updated(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'automation.db'}"
    container = build_container(Settings(database_url=database_url, email_enabled=False))
    overview = container.automation_service.overview()
    assert {item.job_key for item in overview.schedules} == {
        "tw_daily_market_data", "us_daily_market_data"
    }
    default_tw = next(item for item in overview.schedules if item.market == "TW")
    assert (default_tw.hour, default_tw.minute) == (13, 50)
    updated = container.automation_service.update_schedule(
        "tw_daily_market_data", hour=15, minute=5, max_retries=2, enabled=False
    )
    assert (updated.hour, updated.minute, updated.max_retries, updated.enabled) == (15, 5, 2, False)
    rebuilt = build_container(Settings(database_url=database_url, email_enabled=False))
    persisted = rebuilt.automation_service.overview().schedules
    tw = next(item for item in persisted if item.market == "TW")
    assert (tw.hour, tw.minute, tw.enabled) == (15, 5, False)


def test_unconfigured_email_is_audited_and_page_renders(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'email.db'}"))
    assert container.automation_service.test_email() == "skipped"
    delivery = container.automation_service.overview().deliveries[0]
    assert delivery.status == "skipped"
    response = create_app(container).test_client().get("/automation")
    assert response.status_code == 200
    assert "自動排程與通知" in response.get_data(as_text=True)


def test_local_execution_lock_rejects_duplicate_and_checks_owner_token():
    locks = LocalExecutionLockManager()
    token = locks.acquire("tw_daily_market_data", 60)
    assert token
    assert locks.acquire("tw_daily_market_data", 60) is None
    locks.release("tw_daily_market_data", "wrong-owner")
    assert locks.acquire("tw_daily_market_data", 60) is None
    locks.release("tw_daily_market_data", token)
    assert locks.acquire("tw_daily_market_data", 60)


def test_health_reports_local_runtime_when_redis_is_disabled(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'health.db'}"))
    snapshot = container.health_service.check()
    assert snapshot.status == "healthy"
    assert snapshot.redis == "disabled"
    assert snapshot.lock_backend == "local"


def test_scheduler_startup_closes_stale_running_audits(tmp_path):
    container = build_container(Settings(
        database_url=f"sqlite:///{tmp_path / 'stale-runs.db'}"
    ))
    now = datetime(2026, 8, 22, 1, 0, tzinfo=UTC)
    run_id = container.automation_service._runs.start(
        "model_zoo_research", "TW", now - timedelta(hours=7)
    )

    assert container.automation_service.recover_stale_runs(now=now) == 1
    recovered = next(
        item for item in container.automation_service.overview().recent_runs
        if item.id == run_id
    )
    assert recovered.status.value == "failed"
    assert "自動關閉" in (recovered.error or "")


def _mock_tw_workflow(service):
    service._daily_pipeline = MagicMock()
    service._paper_trading = MagicMock()
    service._shadow_trading = MagicMock()
    service._promotions = MagicMock()
    service._model_governance = MagicMock()
    service._point_in_time_data = MagicMock()
    service._intraday_features = MagicMock()
    service._earnings_calls = MagicMock()
    service._after_hours_ai = MagicMock()
    service._universe_expansion = MagicMock()
    service._after_hours_ai.generate.return_value = SimpleNamespace(
        to_markdown=lambda: "## 盤後 AI 零股決策"
    )
    service._daily_pipeline.run.side_effect = (
        lambda market, early_decision_callback=None:
        early_decision_callback(SimpleNamespace(market=market))
    )
    service._reports = MagicMock()
    service._reports.generate.return_value = SimpleNamespace(
        title="台股每日研究報告",
        body_markdown="研究結果",
    )


def test_paused_modules_default_to_requirements_section_13():
    assert parse_paused_modules(None) == DEFAULT_PAUSED_MODULES
    assert DEFAULT_PAUSED_MODULES == set(PAUSABLE_MODULES)
    assert parse_paused_modules("none") == frozenset()
    assert parse_paused_modules(" Shadow_Trading, promotions ") == {"shadow_trading", "promotions"}
    with pytest.raises(ValueError):
        parse_paused_modules("shadow_trading,daily_decision")


def test_tw_daily_automation_skips_paused_modules_by_default(tmp_path):
    container = build_container(Settings(
        database_url=f"sqlite:///{tmp_path / 'tw-paused.db'}",
        email_enabled=False,
        pit_auto_ingestion_enabled=True,
    ))
    service = container.automation_service
    _mock_tw_workflow(service)

    result = service.execute("tw_daily_market_data")

    assert result.status == "succeeded"
    for paused in (
        service._point_in_time_data.run_scheduled,
        service._intraday_features.run,
        service._earnings_calls.refresh,
        service._model_governance.refresh,
        service._shadow_trading.run_daily,
        service._promotions.revalidate_all,
    ):
        paused.assert_not_called()
    service._paper_trading.process_pending.assert_called_once_with()
    service._universe_expansion.run_batch.assert_called_once_with()
    assert service._after_hours_ai.generate.call_count == 2
    assert service._after_hours_ai.submit_to_paper.call_count == 2
    service._reports.generate.assert_called_once_with("TW", index=False)


def test_tw_daily_automation_runs_every_module_when_nothing_is_paused(tmp_path):
    container = build_container(Settings(
        database_url=f"sqlite:///{tmp_path / 'tw-daily.db'}",
        email_enabled=False,
        paused_modules=frozenset(),
    ))
    service = container.automation_service
    _mock_tw_workflow(service)

    result = service.execute("tw_daily_market_data")

    assert result.status == "succeeded"
    assert service._daily_pipeline.run.call_args.args == ("TW",)
    assert callable(service._daily_pipeline.run.call_args.kwargs["early_decision_callback"])
    service._model_governance.refresh.assert_called_once_with()
    service._paper_trading.process_pending.assert_called_once_with()
    service._shadow_trading.run_daily.assert_called_once_with()
    service._promotions.revalidate_all.assert_called_once_with()
    service._intraday_features.run.assert_called_once_with(lookback_days=14)
    service._earnings_calls.refresh.assert_called_once_with("ALL")
    assert service._after_hours_ai.generate.call_count == 2
    assert service._after_hours_ai.submit_to_paper.call_count == 2
    service._universe_expansion.run_batch.assert_called_once_with()
    service._reports.generate.assert_called_once_with("TW", index=True)


def test_tw_daily_automation_refreshes_existing_prices_before_universe_expansion(tmp_path):
    container = build_container(Settings(
        database_url=f"sqlite:///{tmp_path / 'tw-refresh-order.db'}",
        email_enabled=False,
    ))
    service = container.automation_service
    calls: list[str] = []
    service._daily_pipeline = MagicMock(
        run=lambda market, early_decision_callback=None: calls.append("daily_pipeline")
    )
    service._universe_expansion = MagicMock(
        run_batch=lambda: calls.append("universe_expansion")
    )
    service._reports = MagicMock()
    service._reports.generate.return_value = SimpleNamespace(
        title="daily", body_markdown="daily"
    )
    service._paper_trading = None
    service._shadow_trading = None
    service._promotions = None
    service._model_governance = None
    service._point_in_time_data = None
    service._intraday_features = None
    service._earnings_calls = None
    service._after_hours_ai = None

    result = service.execute("tw_daily_market_data")

    assert result.status == "succeeded"
    assert calls == ["daily_pipeline", "universe_expansion"]


def test_daily_automations_share_one_database_write_lock(tmp_path):
    container = build_container(Settings(
        database_url=f"sqlite:///{tmp_path / 'daily-global-lock.db'}",
        email_enabled=False,
    ))
    service = container.automation_service
    token = service._locks.acquire("daily_database_write_workflow", 60)
    assert token
    try:
        result = service.execute("tw_daily_market_data")
    finally:
        service._locks.release("daily_database_write_workflow", token)

    assert result.status == "skipped_locked"
    assert result.attempts == 0
    assert "稍後" in (result.error or "")


def test_tw_daily_automation_can_run_point_in_time_ingestion(tmp_path):
    container = build_container(Settings(
        database_url=f"sqlite:///{tmp_path / 'pit-automation.db'}",
        email_enabled=False,
        pit_auto_ingestion_enabled=True,
        pit_default_entities="tw_futures_daily:TX",
        pit_lookback_days=3,
        paused_modules=frozenset(),
    ))
    service = container.automation_service
    service._daily_pipeline = MagicMock()
    service._point_in_time_data = MagicMock()
    service._intraday_features = MagicMock()
    service._earnings_calls = MagicMock()
    service._universe_expansion = MagicMock()
    service._reports = MagicMock()
    service._reports.generate.return_value = SimpleNamespace(
        title="台股每日研究報告", body_markdown="研究結果"
    )

    result = service.execute("tw_daily_market_data")

    assert result.status == "succeeded"
    service._point_in_time_data.run_scheduled.assert_called_once_with(
        "tw_futures_daily:TX", 3
    )
    service._intraday_features.run.assert_called_once_with(lookback_days=14)
    service._earnings_calls.refresh.assert_called_once_with("ALL")
