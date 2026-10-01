from dataclasses import replace
from datetime import date, datetime
from types import SimpleNamespace
from urllib.error import URLError
from zoneinfo import ZoneInfo

import pytest

from quant_platform.application.notifications import (
    PUSH_URL,
    LineError,
    LineMessagingClient,
    NotificationService,
    failure_text,
    plan_advice_text,
)
from quant_platform.application.plan_decision import PlanDecision, PlanOrder
from quant_platform.config import Settings

TAIPEI = ZoneInfo("Asia/Taipei")
TOKEN = "test-channel-token"
LINE = Settings(line_enabled=True, line_channel_access_token=TOKEN, line_to="U0123456789abcdef")


class FakePost:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, payload, headers, timeout):
        self.calls.append((url, payload, dict(headers)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class Deliveries:
    def __init__(self):
        self.items = []

    def save_delivery(self, value):
        self.items.insert(0, replace(value, id=len(self.items) + 1))
        return len(self.items)

    def list_deliveries(self, limit=20):
        return self.items[:limit]


def client(post):
    return LineMessagingClient(TOKEN, "U0123456789abcdef", post=post, sleep=lambda _seconds: None)


def test_push_sends_one_text_message_with_a_retry_key():
    post = FakePost((200, "{}"))
    client(post).push("你好")

    url, payload, headers = post.calls[0]
    assert url == PUSH_URL == "https://api.line.me/v2/bot/message/push"
    assert payload == {"to": "U0123456789abcdef", "messages": [{"type": "text", "text": "你好"}]}
    assert headers["Authorization"] == f"Bearer {TOKEN}" and len(headers["X-Line-Retry-Key"]) == 36


def test_server_errors_and_timeouts_retry_with_the_same_key_and_409_counts_as_sent():
    post = FakePost((500, "{}"), URLError("timeout"), (409, '{"message":"accepted already"}'))
    client(post).push("x")

    keys = {headers["X-Line-Retry-Key"] for _url, _payload, headers in post.calls}
    assert len(post.calls) == 3 and len(keys) == 1


def test_client_errors_are_not_retried_and_never_echo_the_token():
    post = FakePost((401, '{"message":"Authentication failed"}'))
    with pytest.raises(LineError) as error:
        client(post).push("x")

    assert len(post.calls) == 1
    assert "401" in str(error.value) and "token 無效" in str(error.value) and TOKEN not in str(error.value)


def test_service_sends_each_key_once_and_records_failures():
    deliveries = Deliveries()
    post = FakePost((200, "{}"), (400, '{"message":"bad"}'), (200, "{}"))
    service = NotificationService(LINE, deliveries, client=client(post))

    assert service.send("plan:2026-10-05", "今日投入建議", "a") == "sent"
    assert service.send("plan:2026-10-05", "今日投入建議", "a") == "duplicate"
    assert service.send("plan-problem:2026-10-05", "投入日提醒", "b") == "failed"
    assert service.send("plan-problem:2026-10-05", "投入日提醒", "b") == "sent"   # a failure may be retried
    assert [item.status for item in deliveries.items] == ["sent", "failed", "sent"]
    assert deliveries.items[1].error.startswith("LINE 回應 400")
    assert service.status()["last"].subject == "plan-problem:2026-10-05｜投入日提醒"

    disabled = NotificationService(Settings(), deliveries)
    assert disabled.send("x", "y", "z") == "disabled" and not disabled.status()["configured"]


def decision(kind="invest", headline="今天依計畫投入：買進 0050 99 股", reasons=()):
    orders = [PlanOrder("0050", "BUY", 99, 100.2, 100.0, 9_919.8, 3, 0)] if kind == "invest" else []
    return PlanDecision(kind, headline, 1, "定期定額基準", date(2026, 10, 5), orders, list(reasons))


def test_advice_text_lists_the_orders():
    text = plan_advice_text(decision(), "https://stockresearch.pimi-sunsun.com")

    assert text.startswith("【盤後決策台】10/05（一） 投入日")
    assert "0050 買進 99 股，限價 100.20（收盤 100.00），約 9,920 元，手續費 3 元" in text
    assert "計畫第 1 版・定期定額基準" in text and text.endswith("https://stockresearch.pimi-sunsun.com/")
    assert failure_text("夜間資料庫備份", "disk full").splitlines()[:2] == ["【盤後決策台】夜間資料庫備份失敗", "disk full"]


class FakeContainer:
    def __init__(self, result, closed=False):
        self.sent = []
        self.settings = Settings(public_url="", scheduler_timezone="Asia/Taipei")
        self.plan_decision_service = SimpleNamespace(decide=lambda now: result)
        outer = self

        class Service:
            enabled = True

            def send(self, key, title, text, once=True):
                outer.sent.append((key, title, text))
                return "sent"

        self.notification_service = Service()
        calendar = SimpleNamespace(
            holiday_aware=True, closure=lambda day: SimpleNamespace(name="休市") if closed else None
        )
        self.market_calendar = SimpleNamespace(calendar=lambda market: calendar)


def at(hour, minute):
    return datetime(2026, 10, 5, hour, minute, tzinfo=TAIPEI)


def test_plan_advice_job_sends_on_invest_days_inside_the_window():
    from quant_platform.scheduler.runner import notify_plan_advice

    invest = FakeContainer(decision())
    assert notify_plan_advice(invest, at(13, 40)) is None            # before the window
    assert notify_plan_advice(invest, at(13, 50)) == "sent"
    assert invest.sent[0][0] == "plan:2026-10-05"

    missing = FakeContainer(decision("missing_data", "0050 今日收盤尚未取得", ["盤後資料通常在 13:45 前後齊全。"]))
    assert notify_plan_advice(missing, at(14, 10)) is None           # still waiting for the close
    assert notify_plan_advice(missing, at(14, 15)) == "sent"
    assert missing.sent[0][0] == "plan-problem:2026-10-05" and "今日收盤尚未取得" in missing.sent[0][2]

    idle = replace(decision("idle", "今天不需操作：下次投入日 11/05", ["「定期定額基準」只在投入日操作。"]),
                   invest_day=date(2026, 11, 5))
    idle_day = FakeContainer(idle)
    assert notify_plan_advice(idle_day, at(13, 45)) == "sent"           # the daily summary (REQUIREMENTS §11)
    key, _title, text = idle_day.sent[0]
    assert key == "daily:2026-10-05" and "10/05（一） 今天不需操作" in text and "下次投入日 11/05（四）" in text

    quiet = FakeContainer(idle)
    quiet.settings = Settings(line_daily_summary=False)
    assert notify_plan_advice(quiet, at(13, 45)) is None and quiet.sent == []
    assert notify_plan_advice(FakeContainer(decision(), closed=True), at(13, 50)) is None


def test_failed_workflow_sends_one_failure_message():
    from quant_platform.scheduler.runner import run_scheduled_workflow

    container = FakeContainer(decision())
    container.automation_service = SimpleNamespace(
        execute=lambda job_key: SimpleNamespace(status="failed", error="TWSE timeout")
    )

    run_scheduled_workflow(container, "tw_daily", "TW", "Asia/Taipei", now=at(13, 50))

    key, title, text = container.sent[0]
    assert key == "workflow:tw_daily:2026-10-05" and "每日資料流程（TW）失敗" in text and "TWSE timeout" in text


def test_system_page_shows_line_status_and_test_button(tmp_path):
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    app = create_app(container).test_client()
    body = app.get("/system").get_data(as_text=True)
    assert "LINE 通知" in body and "未設定" in body and "傳送 LINE 測試訊息" in body

    response = app.post("/system/line-test", follow_redirects=True)
    assert "LINE 通知尚未設定或未啟用" in response.get_data(as_text=True)
