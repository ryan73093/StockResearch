"""OpenAI Responses API transport and cost ledger for the AI researcher (S4-W04).

Follows the VectorDB project: plain ``urllib`` (no SDK), ``store: false`` so
nothing is kept at OpenAI, no automatic retry (a timed-out call may already
have been billed), and the monthly budget is checked before every call with a
hard stop. Every call, failed or not, is appended to the usage ledger with the
price in force at that moment. Prompts, keys and response bodies are never
logged.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib import error, request
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
RESPONSES_URL = "https://api.openai.com/v1/responses"
# USD per 1M tokens. gpt-6-luna as derived by VectorDB from the OpenAI bill
# (2026-09-24～30); cached input estimated at 10% of input.
PRICES: dict[str, dict[str, float]] = {
    "gpt-6-luna": {"input": 0.125, "cached_input": 0.0125, "output": 0.50},
}


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    """This month's budget is used up; no call is made."""


def resolve_openai_key(direct: str = "") -> str:
    """OPENAI_API_KEY, or OPENAI_API_KEY from the dotenv file OPENAI_KEY_ENV_FILE points to."""
    if direct:
        return direct
    if os.getenv("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    source = os.getenv("OPENAI_KEY_ENV_FILE")
    if not source:
        return ""
    path = Path(source).expanduser()
    if not path.is_file():
        raise LLMError("OPENAI_KEY_ENV_FILE 指定的金鑰檔不存在")
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.strip().partition("=")
        if key.strip() == "OPENAI_API_KEY":
            return value.strip().strip('"').strip("'")
    raise LLMError("OPENAI_KEY_ENV_FILE 指定的檔案沒有 OPENAI_API_KEY")


def token_counts(usage: object) -> tuple[int, int, int]:
    """Input, cached input and output tokens from a Responses API usage block."""
    usage = usage if isinstance(usage, dict) else {}

    def count(value: object) -> int:
        return value if type(value) is int and value >= 0 else 0

    input_tokens = count(usage.get("input_tokens"))
    details = usage.get("input_tokens_details") or {}
    cached = count(details.get("cached_tokens")) if isinstance(details, dict) else 0
    return input_tokens, min(cached, input_tokens), count(usage.get("output_tokens"))


def call_cost(model: str, usage: object) -> float | None:
    price = PRICES.get(model)
    if price is None:
        return None
    input_tokens, cached, output_tokens = token_counts(usage)
    return round(
        ((input_tokens - cached) * price["input"] + cached * price["cached_input"]
         + output_tokens * price["output"]) / 1_000_000,
        8,
    )


@dataclass(frozen=True)
class UsageLedger:
    """Append-only JSONL of model calls (instance/research/agent/usage.jsonl)."""

    path: Path

    def record(self, entry: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def entries(self) -> list[dict[str, object]]:
        if not self.path.is_file():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows

    def month_spend(self, now: datetime, operations: tuple[str, ...] | None = None) -> float:
        """This month's spend; only the calls whose operation is in ``operations`` when given."""
        local = now.astimezone(TAIPEI)
        month = f"{local:%Y-%m}"
        return round(sum(
            float(entry.get("cost_usd") or 0) for entry in self.entries()
            if str(entry.get("month", "")) == month
            and (operations is None or str(entry.get("operation", "")) in operations)
        ), 8)


class ResponsesClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        ledger: UsageLedger,
        monthly_budget_usd: float,
        timeout: float = 120.0,
        max_output_tokens: int = 6000,
        post: Callable[[str, dict, dict[str, str], float], dict] | None = None,
        clock: Callable[[], datetime] | None = None,
        operations: tuple[str, ...] | None = None,
        total_budget_usd: float | None = None,
        label: str = "AI 研究員",
    ) -> None:
        # 2026-10-09 (使用者：上限 5 美元): every model call shares one monthly total; a purpose
        # (``operations``) has its own cap inside it, so the news scoring cannot starve the researcher.
        self._operations = operations
        self._total = total_budget_usd
        self._label = label
        self._key = api_key
        self.model = model
        self._ledger = ledger
        self._budget = monthly_budget_usd
        self._timeout = timeout
        self._max_output_tokens = max_output_tokens
        self._post = post or _post_json
        self._clock = clock or (lambda: datetime.now(UTC))

    def budget_status(self) -> dict[str, float]:
        now = self._clock()
        spent = self._ledger.month_spend(now, self._operations)
        status = {"spent_usd": spent, "budget_usd": self._budget, "remaining_usd": max(0.0, self._budget - spent)}
        if self._total is not None:
            total = self._ledger.month_spend(now)
            status.update(total_spent_usd=total, total_budget_usd=self._total,
                          remaining_usd=max(0.0, min(self._budget - spent, self._total - total)))
        return status

    def json_call(self, instructions: str, user_input: str, operation: str, context: str = "") -> tuple[dict, dict]:
        """One Responses call that must answer with a JSON object; returns (object, usage)."""
        if not self._key:
            raise LLMError("尚未設定 OPENAI_API_KEY（.env 或 OPENAI_KEY_ENV_FILE）")
        if self.model not in PRICES:
            raise LLMError(f"沒有 {self.model} 的單價，無法控管預算；請先在 research/agent/llm.py 加入")
        status = self.budget_status()
        if status["spent_usd"] >= self._budget:
            raise BudgetExceeded(
                f"本月{self._label}預算 US${self._budget:.2f} 已用完（已用 US${status['spent_usd']:.4f}），停止呼叫"
            )
        if self._total is not None and status["total_spent_usd"] >= self._total:
            raise BudgetExceeded(
                f"本月 LLM 總預算 US${self._total:.2f} 已用完（已用 US${status['total_spent_usd']:.4f}），停止呼叫"
            )
        if "json" not in user_input.lower():
            # The json_object format requires the word in the input messages (HTTP 400 otherwise).
            user_input += "\n請以 JSON 物件回覆。"
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": user_input,
            "max_output_tokens": self._max_output_tokens,
            "store": False,
            "text": {"format": {"type": "json_object"}},
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self._key}"}
        started = time.perf_counter()
        usage: dict = {}
        outcome = "failed"
        try:
            data = self._post(RESPONSES_URL, body, headers, self._timeout)
            usage = data.get("usage") or {}
            if data.get("status") != "completed":
                reason = (data.get("incomplete_details") or {}).get("reason")
                raise LLMError(f"模型未完成回覆（{reason or data.get('status')}）")
            text = "\n".join(
                part.get("text", "")
                for item in data.get("output") or [] if isinstance(item, dict)
                and item.get("type") == "message" and item.get("role") == "assistant"
                for part in item.get("content") or [] if part.get("type") == "output_text"
            ).strip()
            if not text:
                raise LLMError("模型沒有回傳文字（可能拒絕回答）")
            try:
                payload = json.loads(_strip_fence(text))
            except ValueError as exc:
                raise LLMError("模型回傳的不是 JSON") from exc
            if not isinstance(payload, dict):
                raise LLMError("模型回傳的 JSON 不是物件")
            outcome = "completed"
            return payload, usage
        finally:
            now = self._clock()
            input_tokens, cached, output_tokens = token_counts(usage)
            self._ledger.record({
                "at": now.isoformat(timespec="seconds"),
                "month": f"{now.astimezone(TAIPEI):%Y-%m}",
                "model": self.model,
                "operation": operation,
                "context": context,
                "status": outcome,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "input_tokens": input_tokens,
                "cached_input_tokens": cached,
                "output_tokens": output_tokens,
                "cost_usd": call_cost(self.model, usage),
            })


def _strip_fence(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        value = value.split("\n", 1)[1] if "\n" in value else ""
        value = value.rsplit("```", 1)[0]
    return value.strip()


def _post_json(url: str, body: dict, headers: dict[str, str], timeout: float) -> dict:
    http_request = request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            return json.loads(response.read(8 * 1024 * 1024).decode("utf-8"))
    except error.HTTPError as exc:
        # The error message names the bad parameter; it never contains the key.
        try:
            detail = str((json.loads(exc.read(64 * 1024).decode("utf-8")).get("error") or {}).get("message", ""))
        except (ValueError, OSError, AttributeError):
            detail = ""
        raise LLMError(f"OpenAI API HTTP {exc.code}：{detail[:200] or '請檢查金鑰、模型權限或配額'}") from None
    except (error.URLError, TimeoutError, OSError) as exc:
        raise LLMError(f"OpenAI API 連線失敗或逾時（{exc.__class__.__name__}），未自動重試") from None
    except ValueError:
        raise LLMError("OpenAI API 回應無法解析") from None
