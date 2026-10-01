"""LINE notifications through the Messaging API (S5-W06).

LINE Notify ended on 2025-03-31, so messages are push messages from the
user's own LINE Official Account (a Messaging API channel) to their LINE
user ID. Only a few kinds are sent:

- the plan's order advice on an invest day, once the close is in (13:45–14:25);
- a warning when an invest day's close is still missing at 14:15, or the
  money is not enough for one share;
- failures of the daily workflow and of the nightly backup;
- a test message from the System page.

Every attempt is recorded in ``notification_deliveries`` (channel ``line``)
with a key in the subject; a key that was sent already is not sent again, so
the five-minute job can run repeatedly. Requests carry an X-Line-Retry-Key,
so a retried request is never delivered twice; only timeouts and 5xx are
retried. The channel access token comes from the environment and is never
logged or shown.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from quant_platform.domain.entities import NotificationDelivery

logger = logging.getLogger(__name__)
PUSH_URL = "https://api.line.me/v2/bot/message/push"
MAX_TEXT = 5000
WEEKDAYS = "一二三四五六日"


class LineError(RuntimeError):
    pass


def post_json(url: str, payload: dict, headers: dict[str, str], timeout: float) -> tuple[int, str]:
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def _reason(body: str) -> str:
    try:
        payload = json.loads(body)
    except ValueError:
        return body[:200]
    details = "；".join(str(item.get("message", "")) for item in payload.get("details") or [] if isinstance(item, dict))
    return (str(payload.get("message", "")) + (f"（{details}）" if details else ""))[:300]


class LineMessagingClient:
    def __init__(
        self,
        token: str,
        to: str,
        post: Callable[[str, dict, dict[str, str], float], tuple[int, str]] = post_json,
        timeout: float = 10.0,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._to = to
        self._post = post
        self._timeout = timeout
        self._retries = retries
        self._sleep = sleep

    def push(self, text: str) -> None:
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "X-Line-Retry-Key": str(uuid.uuid4()),  # the same key on every retry of this message
        }
        payload = {"to": self._to, "messages": [{"type": "text", "text": text[:MAX_TEXT]}]}
        for attempt in range(self._retries + 1):
            try:
                status, body = self._post(PUSH_URL, payload, headers, self._timeout)
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < self._retries:
                    self._sleep(2 ** attempt)
                    continue
                raise LineError(f"連不上 LINE：{exc.__class__.__name__}") from exc
            if status in (200, 409):  # 409: already accepted under this retry key, not sent twice
                return
            if status >= 500 and attempt < self._retries:
                self._sleep(2 ** attempt)
                continue
            hint = {401: "（channel access token 無效或過期）", 400: "（user ID 或內容不正確）",
                    403: "（此 channel 沒有權限）", 429: "（超過頻率或本月則數上限）"}.get(status, "")
            raise LineError(f"LINE 回應 {status}{hint}：{_reason(body)}")


class NotificationService:
    def __init__(self, settings, repository, client: LineMessagingClient | None = None) -> None:
        self._configured = bool(settings.line_channel_access_token and settings.line_to)
        self._enabled = bool(settings.line_enabled and self._configured)
        self._repository = repository
        self._client = client or (
            LineMessagingClient(settings.line_channel_access_token, settings.line_to) if self._configured else None
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def status(self) -> dict[str, object]:
        history = self._history()
        return {
            "configured": self._configured,
            "enabled": self._enabled,
            "last": history[0] if history else None,
            "recent": history[:5],
        }

    def _history(self) -> list[NotificationDelivery]:
        return [item for item in self._repository.list_deliveries(200) if item.channel == "line"]

    def already_sent(self, key: str) -> bool:
        return any(item.status == "sent" and item.subject.split("｜", 1)[0] == key for item in self._history())

    def send(self, key: str, title: str, text: str, once: bool = True) -> str:
        """sent | duplicate | disabled | failed"""
        if not self._enabled or self._client is None:
            return "disabled"
        if once and self.already_sent(key):
            return "duplicate"
        error = None
        try:
            self._client.push(text)
            status = "sent"
        except LineError as exc:
            status, error = "failed", str(exc)
            logger.warning("LINE notification %s failed: %s", key, exc)
        self._repository.save_delivery(NotificationDelivery(
            id=None, channel="line", recipient="LINE", subject=f"{key}｜{title}"[:240], status=status,
            related_run_id=None, attempted_at=datetime.now(UTC), error=error,
        ))
        return status


# --- message texts ----------------------------------------------------------

def _day_label(day) -> str:
    return f"{day:%m/%d}（{WEEKDAYS[day.weekday()]}）"


def plan_advice_text(decision, public_url: str = "") -> str:
    lines = [f"【盤後決策台】{_day_label(decision.invest_day)} 投入日", decision.headline]
    for order in decision.orders:
        side = "買進" if order.side == "BUY" else "賣出"
        lines.append(
            f"・{order.symbol} {side} {order.shares:,} 股，限價 {order.limit_price:,.2f}"
            f"（收盤 {order.reference_close:,.2f}），約 {order.amount:,.0f} 元，手續費 {order.fee} 元"
            + (f"、稅 {order.tax} 元" if order.tax else "")
        )
    lines.append("盤後零股 13:40–14:30 委託，14:30 撮合。")
    lines.append(f"計畫第 {decision.plan_version} 版・{decision.strategy}")
    if public_url:
        lines.append(public_url.rstrip("/") + "/")
    return "\n".join(lines)


def idle_text(decision, today, public_url: str = "") -> str:
    lines = [f"【盤後決策台】{_day_label(today)} 今天不需操作"]
    if decision.invest_day:
        lines.append(f"下次投入日 {_day_label(decision.invest_day)}・{decision.strategy}")
    lines += decision.reasons[:1]
    if public_url:
        lines.append(public_url.rstrip("/") + "/")
    return "\n".join(lines)


def plan_problem_text(decision, public_url: str = "") -> str:
    lines = [f"【盤後決策台】{_day_label(decision.invest_day)} 投入日：{decision.headline}"]
    lines += [f"・{reason}" for reason in decision.reasons[:3]]
    lines.append("請到網站確認；盤後零股收單到 14:30。")
    if public_url:
        lines.append(public_url.rstrip("/") + "/")
    return "\n".join(lines)


def failure_text(what: str, error: str | None, public_url: str = "") -> str:
    lines = [f"【盤後決策台】{what}失敗", (error or "未知錯誤")[:500], "請到「系統」頁查看紀錄。"]
    if public_url:
        lines.append(public_url.rstrip("/") + "/system")
    return "\n".join(lines)
