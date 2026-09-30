from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from apscheduler.schedulers.base import BaseScheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    from quant_platform.container import Container

logger = logging.getLogger(__name__)

_WEEKDAY_INDEX = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


def run_universe_backfill(container: "Container") -> object:
    """Backfill one throttled batch and immediately build its local features.

    Expensive cross-sectional model/backtest stages run once when the first
    configured seasoned-asset target is reached, instead of rerunning after every 20 names.
    """
    result = container.automation_service.run_exclusive_maintenance(
        lambda: container.universe_expansion_service.run_batch(
            batch_size=20, fetch_auxiliary=False
        )
    )
    if result is None:
        logger.info("Universe backfill skipped while a daily data workflow is active")
        return None
    if result.symbols and result.inserted_bars:
        container.feature_label_pipeline.run("TW", symbols=list(result.symbols))
        container.backtest_research_pipeline.run(
            "TW", symbols=list(result.symbols)
        )
    overview = container.universe_expansion_service.overview()
    if (
        result.attempted
        and overview.data_ready_assets >= overview.target_ready_assets
    ):
        container.model_research_pipeline.run("TW")
        container.factor_research_pipeline.run("TW")
        container.ensemble_research_pipeline.run("TW")
        container.portfolio_research_pipeline.run("TW")
        container.daily_decision_pipeline.run("TW")
    return result


def _parse_schedule(value: str) -> tuple[int, int]:
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid schedule {value!r}; expected HH:MM") from exc
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"Invalid schedule {value!r}; expected HH:MM")
    return hour, minute


def _is_scheduled_weekday(value: str, weekday: int) -> bool:
    """Support the APScheduler weekday forms persisted by AutomationService."""
    for part in value.lower().split(","):
        token = part.strip()
        if "-" in token:
            start, end = (item.strip() for item in token.split("-", 1))
            if start in _WEEKDAY_INDEX and end in _WEEKDAY_INDEX:
                left, right = _WEEKDAY_INDEX[start], _WEEKDAY_INDEX[end]
                allowed = (
                    range(left, right + 1)
                    if left <= right
                    else tuple(range(left, 7)) + tuple(range(0, right + 1))
                )
                if weekday in allowed:
                    return True
        elif token in _WEEKDAY_INDEX and weekday == _WEEKDAY_INDEX[token]:
            return True
    return False


def _exchange_closure(container: "Container", market: str, local_now: datetime) -> str | None:
    """Name of today's exchange closure for holiday-aware markets, else ``None``."""
    calendar = container.market_calendar.calendar(market)
    if not calendar.holiday_aware:
        return None
    closure = calendar.closure(local_now.date())
    return closure.name if closure is not None else None


def run_scheduled_workflow(
    container: "Container", job_key: str, market: str, timezone: str,
    now: datetime | None = None,
) -> object | None:
    """Cron entry point: skip exchange holidays instead of re-running old data."""
    zone = ZoneInfo(timezone)
    local_now = (now or datetime.now(zone)).astimezone(zone)
    closure = _exchange_closure(container, market, local_now)
    if closure is not None:
        logger.info("Skipping %s: %s market closed (%s)", job_key, market, closure)
        return None
    return container.automation_service.execute(job_key)


def refresh_market_calendar(container: "Container") -> object | None:
    return container.market_calendar.refresh_if_due()


def run_startup_catch_up(
    container: "Container", now: datetime | None = None
) -> tuple[str, ...]:
    """Run today's enabled workflows when the server missed their scheduled time.

    Success is looked up directly in the run table in UTC. A window of recent
    runs is not enough: one workflow writes several sub-job rows, which pushed
    the day's success out of view and re-ran the full workflow every 15 minutes.
    """
    overview = container.automation_service.overview()
    executed: list[str] = []
    for schedule in overview.schedules:
        if not schedule.enabled:
            continue
        zone = ZoneInfo(schedule.timezone)
        local_now = (now or datetime.now(zone)).astimezone(zone)
        if not _is_scheduled_weekday(schedule.weekdays, local_now.weekday()):
            continue
        if _exchange_closure(container, schedule.market, local_now) is not None:
            continue
        due_at = local_now.replace(
            hour=schedule.hour, minute=schedule.minute, second=0, microsecond=0
        )
        if local_now < due_at:
            continue
        already_succeeded = container.automation_service.succeeded_since(
            "daily_market_data", schedule.market, due_at
        )
        market_is_fresh = container.daily_market_data_pipeline.is_fresh(
            schedule.market, local_now
        )
        if already_succeeded and market_is_fresh:
            continue
        logger.warning(
            "Startup catch-up is running missed workflow %s (due %s)",
            schedule.job_key,
            due_at.isoformat(),
        )
        result = container.automation_service.execute(schedule.job_key)
        if result.status in {"succeeded", "skipped_locked"}:
            executed.append(schedule.job_key)
    return tuple(executed)


def configure_scheduler(scheduler: "BaseScheduler", container: "Container") -> int:
    from apscheduler.triggers.cron import CronTrigger

    schedules = container.automation_service.overview().schedules
    for schedule in schedules:
        if not schedule.enabled:
            continue
        scheduler.add_job(
            run_scheduled_workflow,
            args=[container, schedule.job_key, schedule.market, schedule.timezone],
            trigger=CronTrigger(
                day_of_week=schedule.weekdays, hour=schedule.hour, minute=schedule.minute,
                timezone=schedule.timezone,
            ),
            id=schedule.job_key,
            name=schedule.display_name,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )
    return len(scheduler.get_jobs())


def archive_superseded_predictions(container: "Container") -> object | None:
    """Move superseded experiments' predictions to Parquet outside trading hours."""
    return container.automation_service.run_exclusive_maintenance(
        container.prediction_archive.apply
    )


def backup_database(container: "Container") -> object | None:
    """Nightly online backup; the job run and the manifest record the result."""
    if container.database_backup is None:
        logger.warning("Database backup skipped: the database is not a SQLite file")
        return None
    return container.database_backup.run()


def refresh_research_history(container: "Container", now: datetime | None = None) -> object | None:
    """Keep the long-history research dataset current (S3): after the close on
    trading days, re-request only the current month and year; everything else
    comes from the cache. Skips until the initial fetch has produced a manifest."""
    from quant_platform.container import _instance_dir
    from quant_platform.research.history.actions import build_actions
    from quant_platform.research.history.dataset import HistoryDataset
    from quant_platform.research.history.official import OfficialHistoryClient

    base = _instance_dir(container.settings.database_url) / "research" / "history"
    if not (base / "manifest.json").is_file():
        logger.info("Research history refresh skipped: run the initial fetch first")
        return None
    zone = ZoneInfo(container.settings.scheduler_timezone)
    local_now = (now or datetime.now(zone)).astimezone(zone)
    if _exchange_closure(container, "TW", local_now) is not None:
        return None
    manifest = HistoryDataset(base, client=OfficialHistoryClient(base / "raw")).build()
    build_actions(base, OfficialHistoryClient(base / "raw"))
    logger.info("Research history refreshed: %s", manifest.get("requests"))
    return manifest.get("requests")


def probe_close_availability(container: "Container") -> object:
    """S1-W05 measurement tick; the probe itself limits to 13:30–14:45 on trading days."""
    return container.close_availability.run()


def _add_maintenance_jobs(scheduler: "BaseScheduler", container: "Container") -> None:
    """Nightly prediction archive (02:30) and database backup (03:00), the
    close-availability probe (trading days 13:30–14:45, S1-W05), plus the
    TWSE calendar refresh shortly after start and then at most once per day.

    The archive needs the same exclusive database lock as the daily workflows;
    the backup reads one consistent snapshot and needs no lock.
    """
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    timezone = container.settings.scheduler_timezone
    scheduler.add_job(
        archive_superseded_predictions,
        args=[container],
        trigger=CronTrigger(hour=2, minute=30, timezone=timezone),
        id="prediction_archive",
        name="封存舊實驗預測到 Parquet（每日 02:30）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        backup_database,
        args=[container],
        trigger=CronTrigger(hour=3, minute=0, timezone=timezone),
        id="database_backup",
        name="資料庫線上備份（每日 03:00，保留 7 份）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=2 * 60 * 60,
    )
    scheduler.add_job(
        refresh_research_history,
        args=[container],
        trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=15, timezone=timezone),
        id="research_history_refresh",
        name="研究用長歷史資料補抓當月（交易日 15:15）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        probe_close_availability,
        args=[container],
        trigger=CronTrigger(day_of_week="mon-fri", hour="13-14", minute="*", timezone=timezone),
        id="close_availability_probe",
        name="收盤資料時效實測（交易日 13:30–14:45 每分鐘）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=30,
    )
    scheduler.add_job(
        refresh_market_calendar,
        args=[container],
        trigger=IntervalTrigger(
            hours=1,
            start_date=datetime.now(ZoneInfo(timezone)) + timedelta(seconds=20),
            timezone=timezone,
        ),
        id="market_calendar_refresh",
        name="證交所開休市日期更新（每日一次）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )


def start_background_scheduler(container: "Container") -> "BackgroundScheduler | None":
    if not container.settings.scheduler_enabled:
        logger.info("Background scheduler disabled by configuration")
        return None

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.date import DateTrigger
    from apscheduler.triggers.interval import IntervalTrigger

    timezone = container.settings.scheduler_timezone
    scheduler = BackgroundScheduler(timezone=timezone)
    job_count = configure_scheduler(scheduler, container)
    _add_maintenance_jobs(scheduler, container)
    scheduler.add_job(
        run_startup_catch_up,
        args=[container],
        trigger=DateTrigger(
            run_date=datetime.now(ZoneInfo(timezone)) + timedelta(seconds=15),
            timezone=timezone,
        ),
        id="startup_data_catch_up",
        name="啟動後補跑今日漏失資料",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        run_startup_catch_up,
        args=[container],
        trigger=IntervalTrigger(
            minutes=15,
            start_date=datetime.now(ZoneInfo(timezone)) + timedelta(seconds=30),
            timezone=timezone,
        ),
        id="daily_data_freshness_guard",
        name="盤後資料漏跑自動補抓",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        run_universe_backfill,
        args=[container],
        trigger=IntervalTrigger(
            minutes=10,
            start_date=datetime.now(ZoneInfo(timezone)) + timedelta(minutes=2),
            timezone=timezone,
        ),
        id="tw_universe_continuous_backfill",
        name="台股研究池持續補資料（每批 20 檔）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    scheduler.start()
    logger.info(
        "Scheduler started with %s persisted jobs (%s)", job_count, timezone,
    )
    return scheduler


def run_scheduler_worker(container: "Container | None" = None) -> None:
    from apscheduler.triggers.date import DateTrigger
    from apscheduler.schedulers.blocking import BlockingScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from quant_platform.container import build_container

    dependencies = container or build_container()
    if not dependencies.settings.scheduler_enabled:
        logger.warning("Scheduler worker stopped because SCHEDULER_ENABLED=false")
        return
    dependencies.automation_service.recover_stale_runs()
    scheduler = BlockingScheduler(timezone=dependencies.settings.scheduler_timezone)
    job_count = configure_scheduler(scheduler, dependencies)
    _add_maintenance_jobs(scheduler, dependencies)
    timezone = dependencies.settings.scheduler_timezone
    scheduler.add_job(
        run_startup_catch_up,
        args=[dependencies],
        trigger=DateTrigger(
            run_date=datetime.now(ZoneInfo(timezone)) + timedelta(seconds=15),
            timezone=timezone,
        ),
        id="startup_data_catch_up",
        name="啟動後補跑今日漏失資料",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.add_job(
        run_startup_catch_up,
        args=[dependencies],
        trigger=IntervalTrigger(
            minutes=15,
            start_date=datetime.now(ZoneInfo(timezone)) + timedelta(seconds=30),
            timezone=timezone,
        ),
        id="daily_data_freshness_guard",
        name="盤後資料漏跑自動補抓",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    scheduler.add_job(
        run_universe_backfill,
        args=[dependencies],
        trigger=IntervalTrigger(
            minutes=10,
            start_date=datetime.now(ZoneInfo(timezone)) + timedelta(minutes=2),
            timezone=timezone,
        ),
        id="tw_universe_continuous_backfill",
        name="台股研究池持續補資料（每批 20 檔）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    logger.info(
        "Dedicated scheduler worker starting with %s persisted jobs and maintenance; lock backend=%s",
        job_count, dependencies.automation_service.overview().lock_backend,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler worker stopped")


def main() -> None:
    run_scheduler_worker()


if __name__ == "__main__":
    main()
