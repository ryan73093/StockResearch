from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from apscheduler.schedulers.base import BaseScheduler
    from apscheduler.schedulers.background import BackgroundScheduler
    from quant_platform.container import Container

logger = logging.getLogger(__name__)
INTERRUPTED = "服務停止時中斷（未完成）"


def booted_at() -> datetime | None:
    """When Windows last started; a run that began before it cannot still be running."""
    import ctypes
    import os

    if os.name != "nt":
        return None
    try:
        ticks = ctypes.windll.kernel32.GetTickCount64
        ticks.restype = ctypes.c_ulonglong
        return datetime.now(UTC) - timedelta(milliseconds=ticks())
    except (AttributeError, OSError):
        return None


def close_interrupted_runs(database_url: str, now: datetime | None = None) -> int:
    """scripts/stop-services.ps1 calls this once every service process has stopped: whatever is
    still "running" was interrupted (10/01 a stopped backtest kept showing "running")."""
    from quant_platform.database.engine import Database
    from quant_platform.database.repositories import SqlAlchemySchedulerJobRunRepository

    checked_at = now or datetime.now(UTC)
    repository = SqlAlchemySchedulerJobRunRepository(Database(database_url).session_factory)
    return repository.fail_stale_running(checked_at + timedelta(seconds=1), checked_at, error=INTERRUPTED)

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
    result = container.automation_service.execute(job_key)
    if getattr(result, "status", None) == "failed":
        _notify_failure(container, f"workflow:{job_key}:{local_now.date()}", f"每日資料流程（{market}）", result.error)
    return result


def _notify_failure(container: "Container", key: str, what: str, error: str | None) -> None:
    from quant_platform.application.notifications import failure_text

    try:
        container.notification_service.send(
            key, f"{what}失敗", failure_text(what, error, container.settings.public_url)
        )
    except Exception:  # a notification problem must not hide the original failure
        logger.exception("Failure notification %s could not be sent", key)


def refresh_market_calendar(container: "Container") -> object | None:
    return container.market_calendar.refresh_if_due()


def refresh_ex_dividends(container: "Container") -> object | None:
    """S5-W03: the exchanges' ex-dividend previews, at most every 12 hours."""
    result = container.dividend_calendar.refresh_if_due()
    if result is not None:
        logger.info("Ex-dividend previews: %s", result)
    return result


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
        elif result.status == "failed":  # the same key as the cron run: at most one message a day
            _notify_failure(
                container, f"workflow:{schedule.job_key}:{local_now.date()}",
                f"每日資料流程（{schedule.market}）", result.error,
            )
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
    try:
        return container.database_backup.run()
    except Exception as exc:
        _notify_failure(container, f"backup:{datetime.now().date()}", "夜間資料庫備份", str(exc))
        raise


def notify_plan_advice(container: "Container", now: datetime | None = None) -> str | None:
    """Trading days 13:45–14:25 (REQUIREMENTS §11). Invest days: push the plan's
    orders once the close is in; at 14:15 warn once when the close is still
    missing or no share is affordable. Other days: one "no action" summary
    unless LINE_DAILY_SUMMARY=false."""
    from datetime import time as clock

    from quant_platform.application.notifications import idle_text, plan_advice_text, plan_problem_text

    service = container.notification_service
    if not service.enabled:
        return None
    zone = ZoneInfo(container.settings.scheduler_timezone)
    local_now = (now or datetime.now(zone)).astimezone(zone)
    if not clock(13, 45) <= local_now.time() <= clock(14, 25):
        return None
    if _exchange_closure(container, "TW", local_now) is not None:
        return None
    decision = container.plan_decision_service.decide(local_now)
    today = local_now.date()
    public_url = container.settings.public_url
    if decision.invest_day != today:
        if decision.kind == "idle" and container.settings.line_daily_summary:
            return service.send(f"daily:{today}", "今日不需操作", idle_text(decision, today, public_url))
        return None
    missed = getattr(decision, "missed_day", None)
    if decision.kind in {"invest", "rebalance"}:
        if missed is not None:  # once per missed invest day, not every session until a buy is reported
            return service.send(f"plan-catch-up:{missed}", "補買提醒", plan_advice_text(decision, public_url))
        return service.send(f"plan:{today}", "今日投入建議", plan_advice_text(decision, public_url))
    if missed is None and decision.kind in {"missing_data", "idle"} and local_now.time() >= clock(14, 15):
        return service.send(f"plan-problem:{today}", "投入日提醒", plan_problem_text(decision, public_url))
    return None


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
    if (base / "stocks" / "twse").is_dir():
        # R2: today's all-market quotes for the stock-rule forward simulation (15:30).
        try:
            from quant_platform.research.history.stocks import append_current_year

            appended = append_current_year(base, OfficialHistoryClient(base / "raw"), local_now.date())
            logger.info("Stock quotes appended: last day %s, %s rows today", appended.get("last_day"),
                        appended.get("today_rows"))
            if (base / "stocks" / "tpex").is_dir():   # R6: TPEx stocks too (official daily quotes)
                appended = append_current_year(base, OfficialHistoryClient(base / "raw"), local_now.date(), exchange="tpex")
                logger.info("TPEx quotes appended: last day %s, %s rows today", appended.get("last_day"),
                            appended.get("today_rows"))
        except Exception:  # the ETF refresh above stays done; the forward record waits for the quotes
            logger.exception("Stock quotes append failed")
    return manifest.get("requests")


def record_forward_simulation(container: "Container", now: datetime | None = None) -> object | None:
    """S5-W05: append today's state of every tracked strategy (after the 15:15 refresh)."""
    from quant_platform.container import _instance_dir
    from quant_platform.research.forward import ForwardTracker

    research = _instance_dir(container.settings.database_url) / "research"
    if not (research / "history" / "manifest.json").is_file():
        return None
    zone = ZoneInfo(container.settings.scheduler_timezone)
    local_now = (now or datetime.now(zone)).astimezone(zone)
    if _exchange_closure(container, "TW", local_now) is not None:
        return None
    written = ForwardTracker(research).record(local_now.date())
    logger.info("Forward simulation recorded %s strategies", len(written))
    if (research / "history" / "stocks" / "twse").is_dir():
        try:
            from quant_platform.research.stock_forward import StockForwardTracker

            stocks = StockForwardTracker(research).record(local_now.date())
            logger.info("Stock-rule forward simulation recorded %s rules", len(stocks))
            written = written + stocks
        except Exception:
            logger.exception("Stock-rule forward simulation failed")
    return len(written)


def update_chips(container: "Container", now: datetime | None = None) -> object | None:
    """S9-W04 (2026-10-06): at 21:30 on trading days, the exchanges' daily chip reports for every session
    the chip files lack (up to the last 20), then the chip files rebuilt (FinMind history + the reports)."""
    from datetime import timedelta

    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    from quant_platform.container import _instance_dir
    from quant_platform.research.chips import build
    from quant_platform.research.history.chips_daily import fetch, fetch_revenue
    from quant_platform.research.history.official import OfficialHistoryClient
    from quant_platform.research.jobs import JobLog

    zone = ZoneInfo(container.settings.scheduler_timezone)
    local_now = (now or datetime.now(zone)).astimezone(zone)
    if _exchange_closure(container, "TW", local_now) is not None:
        return None
    research = _instance_dir(container.settings.database_url) / "research"
    base = research / "history"
    shareholding = base / "chips" / "TaiwanStockShareholding.parquet"
    if not shareholding.is_file():
        return None
    last = pc.max(pq.read_table(shareholding, columns=["date"])["date"]).as_py()
    calendar = container.market_calendar.calendar("TW")
    sessions, day = [], local_now.date()
    while day > last and len(sessions) < 20:
        if calendar.is_trading_day(day):
            sessions.append(day)
        day -= timedelta(days=1)
    with JobLog(research).start(f"每晚籌碼（官方日報，{len(sessions)} 個交易日）", "scheduler update_chips",
                                total=2) as job:
        job.update(done=0, current="證交所與櫃買日報", force=True)
        client = OfficialHistoryClient(base / "raw")
        fetched = fetch(base, client, sessions, local_now.date())
        try:
            fetched["revenue"] = fetch_revenue(base, client)        # the latest month's table (2 requests)
        except Exception:  # a failed revenue table must not stop the chips
            logger.exception("Monthly revenue table failed")
        job.update(done=1, current="重建籌碼檔", force=True)
        written = build(base)
        job.update(done=2, force=True)
        job.payload["summary"] = f"{fetched['first']}～{fetched['last']}；請求 {fetched['requests']} 次"
    logger.info("Chips updated: %s; %s", fetched, written)
    return fetched


def retrain_models(container: "Container", now: datetime | None = None) -> object | None:
    """Every trading day at 22:45: a model that a forward-observed rule uses and that has no model for this
    year yet gets one (trained on everything before the year's first session). Once a year in practice."""
    from quant_platform.container import _instance_dir
    from quant_platform.research.daily import MODEL_FACTORS
    from quant_platform.research.jobs import JobLog
    from quant_platform.research.model import MODELS, model_dir, train_year
    from quant_platform.research.stock_forward import StockForwardTracker

    zone = ZoneInfo(container.settings.scheduler_timezone)
    local_now = (now or datetime.now(zone)).astimezone(zone)
    if _exchange_closure(container, "TW", local_now) is not None:
        return None
    research = _instance_dir(container.settings.database_url) / "research"
    base = research / "history"
    used = {name for item in StockForwardTracker(research).tracked() if item.get("kind") == "daily"
            for name in (item.get("rule") or {}).get("factors", {}) if name in MODEL_FACTORS}
    trained = []
    for name in sorted(used):
        version = MODELS[name]["version"]
        if (model_dir(base, version) / f"{local_now.year}.pkl").is_file():
            continue
        with JobLog(research).start(f"重訓機器學習模型（{version}，{local_now.year} 年）", "scheduler retrain_models",
                                    total=1) as job:
            meta = train_year(base, version, local_now.year, job=job)
            job.payload["summary"] = f"{local_now.year} 年模型：訓練 {meta['years'].get(str(local_now.year), {}).get('train_rows')} 筆"
        trained.append(version)
    if trained:
        logger.info("Models retrained for %s: %s", local_now.year, trained)
    return trained


def build_stock_snapshot(container: "Container", now: datetime | None = None) -> object | None:
    """S9-W05: at 15:45 on trading days, every stock's factors on the latest session for the stock page
    (after the 15:16 quotes and the 15:30 forward record)."""
    from quant_platform.container import _instance_dir
    from quant_platform.research.snapshot import build

    zone = ZoneInfo(container.settings.scheduler_timezone)
    local_now = (now or datetime.now(zone)).astimezone(zone)
    if _exchange_closure(container, "TW", local_now) is not None:
        return None
    research = _instance_dir(container.settings.database_url) / "research"
    if not (research / "history" / "stocks" / "twse").is_dir():
        return None
    result = build(research / "history", research, local_now.date())
    logger.info("Stock snapshot built: %s", result)
    return result


def fetch_official_close(container: "Container", now: datetime | None = None) -> object | None:
    """S9-W03: from 13:49 on trading days, fetch TWSE's all-market quotes for today (first seen 19–21
    minutes after the close) so Today's orders read today's close from the research price store."""
    from datetime import time as clock

    from quant_platform.container import _instance_dir
    from quant_platform.research.prices import fetch_today_close

    zone = ZoneInfo(container.settings.scheduler_timezone)
    local_now = (now or datetime.now(zone)).astimezone(zone)
    if not clock(13, 49) <= local_now.time() <= clock(14, 25):
        return None
    if _exchange_closure(container, "TW", local_now) is not None:
        return None
    base = _instance_dir(container.settings.database_url) / "research" / "history"
    if not (base / "manifest.json").is_file():
        return None
    from quant_platform.research.prices import ResearchPrices

    if len(ResearchPrices(base).today_quotes(local_now.date())) > 500:
        return None                                   # already have today's table: nothing to log
    count = fetch_today_close(base, local_now.date())
    if count:
        logger.info("Official close for %s: %s codes", local_now.date(), count)
    return count


def collect_news(container: "Container", now: datetime | None = None) -> object | None:
    """R15/R16 (2026-10-05): at 14:40 on trading days, the previous trading day's news of the most traded
    listed stocks and the forward holdings (FinMind, one request per stock); kept for the LLM analyst."""
    from datetime import timedelta

    from quant_platform.container import _instance_dir
    from quant_platform.research.jobs import JobLog
    from quant_platform.research.news import candidates, collect

    zone = ZoneInfo(container.settings.scheduler_timezone)
    local_now = (now or datetime.now(zone)).astimezone(zone)
    if _exchange_closure(container, "TW", local_now) is not None or not container.settings.finmind_token:
        return None
    research = _instance_dir(container.settings.database_url) / "research"
    base = research / "history"
    if not (base / "stocks" / "twse").is_dir():
        return None
    calendar = container.market_calendar.calendar("TW")
    day = calendar.previous_trading_day(local_now.date() - timedelta(days=1))
    codes = candidates(base, research, day)
    with JobLog(research).start(f"收集新聞（{day}，{len(codes)} 檔）", "scheduler collect_news") as job:
        return collect(base, day, codes, container.settings.finmind_token, job=job)


def run_research_agent(container: "Container") -> object | None:
    """S4-W04: one night of the AI researcher (development period only)."""
    from quant_platform.container import _instance_dir
    from quant_platform.research.agent.researcher import build_agent
    from quant_platform.research.market import available_assets, load_market

    from quant_platform.research.forward import STANDARD_PLAN
    from quant_platform.research.promotion import PromotionPipeline, promotion_limits

    research = _instance_dir(container.settings.database_url) / "research"
    base = research / "history"
    if not (base / "manifest.json").is_file():
        return None
    market = load_market(available_assets(base), base)
    entries = []
    if container.settings.research_agent_enabled:
        entries = build_agent(container.settings, research).run_night(market)
        logger.info(
            "AI researcher: %s rounds, %s trials, status %s",
            len(entries), sum(len(entry.get("accepted") or []) for entry in entries),
            [entry.get("status") for entry in entries],
        )
    # S4-W06: candidates that pass every gate move on (validation, holdout once, forward).
    # The aggressive track's drawdown limit is the owner's current plan tolerance (none: closed).
    limits = promotion_limits(container.investment_plan_service.current())
    events = PromotionPipeline(research, STANDARD_PLAN, **limits).advance(market)
    if events:
        logger.info("Promotion: %s", [(event.name, event.stage, event.outcome) for event in events])
    return len(entries)


def save_weekly_research_report(container: "Container", now: datetime | None = None) -> object | None:
    """S6-W04: keep a copy of the finished week's research report (Sunday 23:30)."""
    from quant_platform.container import _instance_dir
    from quant_platform.research.weekly import save_weekly_report

    zone = ZoneInfo(container.settings.scheduler_timezone)
    local_now = (now or datetime.now(zone)).astimezone(zone)
    return save_weekly_report(_instance_dir(container.settings.database_url) / "research", local_now.date())


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
        fetch_official_close,
        args=[container],
        trigger=CronTrigger(day_of_week="mon-fri", hour="13-14", minute="*", timezone=timezone),
        id="official_close_fetch",
        name="官方收盤（證交所全市場，交易日 13:49～14:25 每分鐘，到手即停）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=50,
    )
    scheduler.add_job(
        collect_news,
        args=[container],
        trigger=CronTrigger(day_of_week="mon-fri", hour=14, minute=40, timezone=timezone),
        id="news_collection",
        name="收集新聞（交易日 14:40，前一個交易日、成交值前 150 檔與前向觀察持股）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3 * 3600,
    )
    scheduler.add_job(
        record_forward_simulation,
        args=[container],
        trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=30, timezone=timezone),
        id="forward_simulation",
        name="前向模擬紀錄（交易日 15:30）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3 * 3600,
    )
    scheduler.add_job(
        retrain_models,
        args=[container],
        trigger=CronTrigger(day_of_week="mon-fri", hour=22, minute=45, timezone=timezone),
        id="model_retrain",
        name="機器學習模型年度重訓（交易日 22:45 檢查，新的一年才訓練）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=6 * 3600,
    )
    scheduler.add_job(
        update_chips,
        args=[container],
        trigger=CronTrigger(day_of_week="mon-fri", hour=21, minute=30, timezone=timezone),
        id="chips_update",
        name="每晚籌碼與基本面（交易日 21:30，證交所與櫃買日報）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=6 * 3600,
    )
    scheduler.add_job(
        build_stock_snapshot,
        args=[container],
        trigger=CronTrigger(day_of_week="mon-fri", hour=15, minute=45, timezone=timezone),
        id="stock_snapshot",
        name="個股因子快照（交易日 15:45，個股頁用）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3 * 3600,
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
        save_weekly_research_report,
        args=[container],
        trigger=CronTrigger(day_of_week="sun", hour=23, minute=30, timezone=timezone),
        id="weekly_research_report",
        name="每週研究報告存檔（週日 23:30）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=6 * 3600,
    )
    scheduler.add_job(
        run_research_agent,
        args=[container],
        trigger=CronTrigger(hour=container.settings.research_agent_hour, minute=0, timezone=timezone),
        id="research_agent",
        name=f"AI 研究員（每晚 {container.settings.research_agent_hour}:00，只用開發期）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        notify_plan_advice,
        args=[container],
        trigger=CronTrigger(day_of_week="mon-fri", hour="13-14", minute="*/5", timezone=timezone),
        id="line_plan_advice",
        name="LINE 投入日建議（投入日 13:45–14:25 每 5 分鐘，每天最多一則）",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=120,
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
    scheduler.add_job(
        refresh_ex_dividends,
        args=[container],
        trigger=IntervalTrigger(
            hours=1,
            start_date=datetime.now(ZoneInfo(timezone)) + timedelta(seconds=45),
            timezone=timezone,
        ),
        id="ex_dividend_refresh",
        name="證交所、櫃買除權除息預告更新（每 12 小時）",
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
    dependencies.automation_service.recover_stale_runs(booted_at=booted_at())
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
    # Worker logs go to instance/worker.stderr.log (scripts/run_local_services.ps1);
    # without a handler only warnings reached it and the file stayed empty.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("apscheduler").setLevel(logging.WARNING)  # "Running job …" every minute at 13–14
    run_scheduler_worker()


if __name__ == "__main__":
    main()
