from __future__ import annotations

import logging
import smtplib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Callable

from quant_platform.application.ports import (
    AutomationRepository, ExecutionLockManager, SchedulerJobRunRepository,
)
from quant_platform.config.settings import Settings
from quant_platform.domain.entities import AutomationSchedule, NotificationDelivery, SchedulerJobRun

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AutomationOverview:
    schedules: tuple[AutomationSchedule, ...]
    recent_runs: tuple[SchedulerJobRun, ...]
    deliveries: tuple[NotificationDelivery, ...]
    email_configured: bool
    scheduler_enabled: bool
    lock_backend: str


@dataclass(frozen=True, slots=True)
class AutomationExecutionResult:
    job_key: str
    market: str
    status: str
    attempts: int
    report_title: str | None
    notification_status: str
    error: str | None


class SmtpEmailNotifier:
    """Small SMTP adapter; credentials remain in environment configuration only."""

    def __init__(self, settings: Settings, smtp_factory: Callable[..., object] = smtplib.SMTP) -> None:
        self._settings = settings
        self._smtp_factory = smtp_factory

    @property
    def configured(self) -> bool:
        return bool(
            self._settings.email_enabled and self._settings.smtp_host
            and self._settings.smtp_from and self._settings.report_email_to
        )

    def send(self, subject: str, body: str) -> tuple[str, str | None]:
        if not self.configured:
            return "skipped", "Email 尚未設定；請在 .env 填入 SMTP 與收件人。"
        message = EmailMessage()
        message["From"] = self._settings.smtp_from
        message["To"] = self._settings.report_email_to
        message["Subject"] = subject
        message.set_content(body)
        try:
            with self._smtp_factory(self._settings.smtp_host, self._settings.smtp_port, timeout=20) as client:
                if self._settings.smtp_starttls:
                    client.starttls()
                if self._settings.smtp_username:
                    client.login(self._settings.smtp_username, self._settings.smtp_password)
                client.send_message(message)
            return "sent", None
        except Exception as exc:  # SMTP failures must not erase the research result.
            logger.exception("Email notification failed")
            return "failed", str(exc)


class AutomationService:
    """Owns persisted schedules, single-process locks, retries and delivery audit."""

    def __init__(
        self,
        settings: Settings,
        repository: AutomationRepository,
        run_repository: SchedulerJobRunRepository,
        daily_pipeline: object,
        report_service: object,
        notifier: SmtpEmailNotifier,
        lock_manager: ExecutionLockManager,
        paper_trading_service: object | None = None,
        shadow_trading_service: object | None = None,
        promotion_service: object | None = None,
        model_governance_service: object | None = None,
        point_in_time_data_service: object | None = None,
        intraday_feature_pipeline: object | None = None,
        earnings_call_service: object | None = None,
        after_hours_ai_service: object | None = None,
        universe_expansion_service: object | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._runs = run_repository
        self._daily_pipeline = daily_pipeline
        self._reports = report_service
        self._notifier = notifier
        self._locks = lock_manager
        self._paper_trading = paper_trading_service
        self._shadow_trading = shadow_trading_service
        self._promotions = promotion_service
        self._model_governance = model_governance_service
        self._point_in_time_data = point_in_time_data_service
        self._intraday_features = intraday_feature_pipeline
        self._earnings_calls = earnings_call_service
        self._after_hours_ai = after_hours_ai_service
        self._universe_expansion = universe_expansion_service
        self.ensure_defaults()

    @staticmethod
    def _time(value: str) -> tuple[int, int]:
        hour, minute = (int(part) for part in value.split(":", 1))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("時間必須是 00:00 到 23:59")
        return hour, minute

    def ensure_defaults(self) -> None:
        now = datetime.now(UTC)
        defaults = (
            ("tw_daily_market_data", "台股每日研究流程", "TW", self._settings.tw_data_schedule, "mon-fri"),
            ("us_daily_market_data", "美股每日研究流程", "US", self._settings.us_data_schedule, "tue-sat"),
        )
        for key, name, market, time_text, weekdays in defaults:
            current = self._repository.get_schedule(key)
            if current is None:
                hour, minute = self._time(time_text)
                self._repository.upsert_schedule(AutomationSchedule(
                    job_key=key, display_name=name, market=market, hour=hour, minute=minute,
                    weekdays=weekdays, timezone=self._settings.scheduler_timezone, enabled=True,
                    max_retries=1, notify_on_success=True, notify_on_failure=True, updated_at=now,
                ))
            elif (
                key == "tw_daily_market_data"
                and self._settings.tw_data_schedule == "13:50"
                and (current.hour, current.minute) in {(13, 35), (14, 30)}
            ):
                # Migrate former defaults. Yahoo daily candles are deliberately
                # unavailable until 13:45, while 14:30 misses the odd-lot window.
                self._repository.upsert_schedule(
                    replace(current, hour=13, minute=50, updated_at=now)
                )

    def overview(self) -> AutomationOverview:
        return AutomationOverview(
            schedules=tuple(self._repository.list_schedules()),
            recent_runs=tuple(self._runs.list_recent(30)),
            deliveries=tuple(self._repository.list_deliveries(30)),
            email_configured=self._notifier.configured,
            scheduler_enabled=self._settings.scheduler_enabled,
            lock_backend=self._locks.backend,
        )

    def succeeded_since(self, job_name: str, market: str, since: datetime) -> bool:
        """Whether ``job_name`` succeeded for ``market`` at or after ``since``."""
        return self._runs.latest_succeeded(job_name, market, since) is not None

    def recover_stale_runs(
        self, now: datetime | None = None, max_age: timedelta = timedelta(hours=6),
        booted_at: datetime | None = None,
    ) -> int:
        """Close runs left "running": older than ``max_age``, or started before the last boot
        (nothing from before a reboot can still be running; 10/01 a backtest showed "running" for hours)."""
        checked_at = now or datetime.now(UTC)
        cutoff = checked_at - max_age
        if booted_at is not None and booted_at > cutoff:
            cutoff = booted_at
        recovered = self._runs.fail_stale_running(cutoff, checked_at)
        if recovered:
            logger.warning("Marked %s stale scheduler runs as failed", recovered)
        return recovered

    def update_schedule(self, job_key: str, **changes: object) -> AutomationSchedule:
        current = self._repository.get_schedule(job_key)
        if current is None:
            raise LookupError(f"找不到排程：{job_key}")
        allowed = {"hour", "minute", "enabled", "max_retries", "notify_on_success", "notify_on_failure"}
        values = {key: value for key, value in changes.items() if key in allowed}
        hour = int(values.get("hour", current.hour))
        minute = int(values.get("minute", current.minute))
        retries = int(values.get("max_retries", current.max_retries))
        if not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 0 <= retries <= 5:
            raise ValueError("時間或重試次數超出允許範圍")
        values.update(hour=hour, minute=minute, max_retries=retries,
                      updated_at=datetime.now(UTC))
        updated = replace(current, **values)
        self._repository.upsert_schedule(updated)
        return updated

    def _record_notification(self, subject: str, body: str, should_send: bool) -> str:
        if should_send:
            status, error = self._notifier.send(subject, body)
        else:
            status, error = "skipped", "此排程已關閉該狀態的通知。"
        self._repository.save_delivery(NotificationDelivery(
            id=None, channel="email", recipient=self._settings.report_email_to or "尚未設定",
            subject=subject, status=status, related_run_id=None,
            attempted_at=datetime.now(UTC), error=error,
        ))
        return status

    def execute(self, job_key: str) -> AutomationExecutionResult:
        schedule = self._repository.get_schedule(job_key)
        if schedule is None:
            raise LookupError(f"找不到排程：{job_key}")
        lock_token = self._locks.acquire(job_key, ttl_seconds=6 * 60 * 60)
        if lock_token is None:
            return AutomationExecutionResult(job_key, schedule.market, "skipped_locked", 0, None, "skipped", "已有相同工作執行中")
        workflow_lock_key = "daily_database_write_workflow"
        workflow_token = self._locks.acquire(
            workflow_lock_key, ttl_seconds=6 * 60 * 60
        )
        if workflow_token is None:
            self._locks.release(job_key, lock_token)
            return AutomationExecutionResult(
                job_key,
                schedule.market,
                "skipped_locked",
                0,
                None,
                "skipped",
                "另一個每日資料流程正在寫入；稍後由補抓守門員重試",
            )
        try:
            last_error: str | None = None
            for attempt in range(1, schedule.max_retries + 2):
                try:
                    def save_early_tw_plan(_decision_result) -> None:
                        if self._after_hours_ai is None:
                            return
                        early_plan = self._after_hours_ai.generate()
                        self._after_hours_ai.submit_to_paper(plan=early_plan)

                    self._daily_pipeline.run(
                        schedule.market,
                        early_decision_callback=(
                            save_early_tw_plan
                            if schedule.market == "TW" and self._after_hours_ai is not None
                            else None
                        ),
                    )
                    # Updating the already registered universe is the critical
                    # path for the 13:30-14:30 odd-lot decision.  Expanding the
                    # universe can involve slower external metadata providers,
                    # so it must never delay today's prices and early plan.
                    if (
                        schedule.market == "TW"
                        and self._universe_expansion is not None
                    ):
                        try:
                            self._universe_expansion.run_batch()
                        except Exception:
                            logger.exception(
                                "Daily TW universe expansion batch failed; "
                                "continuing after today's market refresh"
                            )
                    # Modules paused by REQUIREMENTS §13 (settings.paused_modules)
                    # keep their code and data but no longer run every day.
                    paused = self._settings.is_paused
                    if (
                        schedule.market == "TW"
                        and self._settings.pit_auto_ingestion_enabled
                        and self._point_in_time_data is not None
                        and not paused("point_in_time")
                    ):
                        self._point_in_time_data.run_scheduled(
                            self._settings.pit_default_entities,
                            self._settings.pit_lookback_days,
                        )
                    if (
                        schedule.market == "TW"
                        and self._intraday_features is not None
                        and not paused("intraday_features")
                    ):
                        self._intraday_features.run(lookback_days=14)
                    if (
                        schedule.market == "TW"
                        and self._earnings_calls is not None
                        and not paused("earnings_calls")
                    ):
                        self._earnings_calls.refresh("ALL")
                    if self._model_governance is not None and not paused("model_governance"):
                        self._model_governance.refresh()
                    if schedule.market == "TW" and self._paper_trading is not None:
                        self._paper_trading.process_pending()
                    if (
                        schedule.market == "TW"
                        and self._shadow_trading is not None
                        and not paused("shadow_trading")
                    ):
                        self._shadow_trading.run_daily()
                    if (
                        schedule.market == "TW"
                        and self._promotions is not None
                        and not paused("promotions")
                    ):
                        self._promotions.revalidate_all()
                    plan = (
                        self._after_hours_ai.generate()
                        if schedule.market == "TW" and self._after_hours_ai is not None
                        else None
                    )
                    if plan is not None:
                        self._after_hours_ai.submit_to_paper(plan=plan)
                    report = self._reports.generate(
                        schedule.market, index=not paused("rag_index")
                    )
                    subject = f"[Quant OS] {report.title}"
                    body = (
                        f"{report.body_markdown}\n\n{plan.to_markdown()}"
                        if plan is not None else report.body_markdown
                    )
                    delivery = self._record_notification(
                        subject, body, schedule.notify_on_success
                    )
                    return AutomationExecutionResult(job_key, schedule.market, "succeeded", attempt, report.title, delivery, None)
                except Exception as exc:
                    last_error = str(exc)
                    logger.exception("Automation attempt %s failed for %s", attempt, job_key)
            subject = f"[Quant OS] {schedule.display_name}執行失敗"
            delivery = self._record_notification(subject, last_error or "未知錯誤", schedule.notify_on_failure)
            return AutomationExecutionResult(job_key, schedule.market, "failed", schedule.max_retries + 1, None, delivery, last_error)
        finally:
            try:
                self._locks.release(workflow_lock_key, workflow_token)
                self._locks.release(job_key, lock_token)
            except Exception:
                logger.exception("Execution lock release failed for %s", job_key)

    def run_exclusive_maintenance(
        self, callback: Callable[[], object]
    ) -> object | None:
        """Run a database-writing maintenance task without overlapping daily jobs."""
        lock_key = "daily_database_write_workflow"
        token = self._locks.acquire(lock_key, ttl_seconds=6 * 60 * 60)
        if token is None:
            return None
        try:
            return callback()
        finally:
            self._locks.release(lock_key, token)

    def test_email(self) -> str:
        return self._record_notification(
            "[Quant OS] Email 測試", "若您收到這封信，代表每日研究報告通知設定可用。", True
        )
