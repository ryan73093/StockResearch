"""OpenAI Responses API transport and cost ledger for the AI researcher (S4-W04).

Follows the VectorDB project: plain ``urllib`` (no SDK), ``store: false`` so
nothing is kept at OpenAI, no automatic retry (a timed-out call may already
have been billed), and the monthly budget is checked before every call with a
hard stop. Every call, failed or not, is appended to the usage ledger with the
price in force at that moment. Prompts, keys and response bodies are never
logged.

2026-11-20 (使用者：使用 claude api haiku 並繼續; D-051): the OpenAI account ran out of credit. A ``claude-…`` model
(the default is ``claude-haiku-5-5``) is called on the Claude Messages API through the official SDK (``ClaudeClient``)
with the same contract — one call answers one JSON object — the same ledger and the same monthly caps. Every caller
builds its client with ``model_client``, which picks the provider from the configured model's name.

2026-11-20 (使用者：不是要統一用 ai api 平台的 api 嗎; D-052): the calls go to this computer's AI API platform
(AiApiPlatform, ``AI_PLATFORM_URL``) with the project's key from it, not to a provider: the platform routes every
model and its dashboard counts each project's use. ``model_client`` makes a ``ClaudeClient`` for every model.
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
    # Claude Haiku 5.5 (Anthropic's price card, 2026-11): US$0.10 / 0.50 per 1M input / output tokens while the prompt
    # is at most 100K tokens, five times that above; cache reads 0.1x input, 5-minute cache writes 1.25x
    "claude-haiku-5-5": {"input": 0.10, "cached_input": 0.01, "cache_write": 0.125, "output": 0.50,
                         "long_above": 100_000, "long_factor": 5.0},
}
CLAUDE_BASE_URL = "https://api.anthropic.com"     # Anthropic itself: not used unless CLAUDE_API_BASE_URL names it
# 2026-11-20 (使用者：不是要統一用 ai api 平台的 api 嗎; D-052): every model call goes through this computer's AI API
# platform (C:\Dev\AiApiPlatform): one address, a key per project, its dashboard counting each project's use; it passes
# the Claude format through to Anthropic and translates it for OpenAI and Gemini models
AI_PLATFORM_URL = "http://127.0.0.1:5010"
CLAUDE_MAX_TOKENS = 16_000        # its thinking counts toward the cap (Claude Haiku 5.5 thinks by default)
CLAUDE_RETRIES = 2                # the SDK's own retries of 408/409/429/5xx and dropped connections
CLAUDE_EFFORT = "medium"          # Claude Haiku 5.5's default effort, sent explicitly


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


def is_claude(model: str) -> bool:
    return str(model or "").startswith("claude-")


def env_file_value(env_file: str, name: str) -> str:
    """``name``'s value in the dotenv file ``env_file`` (read now, not from the environment); "" when absent."""
    path = Path(env_file) if env_file else None
    if path is None or not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, _, value = line.strip().partition("=")
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


def resolve_anthropic_key(direct: str = "", env_file: str = "") -> str:
    """ANTHROPIC_API_KEY — the AI API platform's key for this project (D-052): given, in the environment, or — read
    again at each call — in ``env_file`` (the project's .env, which Settings.from_env names): a key the owner adds while
    the services run is used without a restart (the worker's 21:45 news scoring and 22:00 researcher, the web's
    knowledge questions). Never logged."""
    if direct or os.getenv("ANTHROPIC_API_KEY"):
        return direct or os.environ["ANTHROPIC_API_KEY"]
    return env_file_value(env_file, "ANTHROPIC_API_KEY")


def base_url_for(settings) -> str:
    """Where the model calls go: CLAUDE_API_BASE_URL when set, else the ANTHROPIC_BASE_URL written in the project's
    .env (the platform's own instructions for a project), else the platform. Never an ANTHROPIC_BASE_URL inherited from
    another program's environment (an AI tool's proxy): the project key must reach the platform only."""
    return (getattr(settings, "claude_api_base_url", "")
            or env_file_value(getattr(settings, "env_file", ""), "ANTHROPIC_BASE_URL") or AI_PLATFORM_URL)


def key_name(model: str = "") -> str:
    """The setting that holds the key (every model goes through the platform's Claude format since D-052)."""
    return "ANTHROPIC_API_KEY"


def key_configured(settings) -> bool:
    """Whether the project has its AI API platform key (no call is made)."""
    return bool(resolve_anthropic_key(getattr(settings, "anthropic_api_key", ""), getattr(settings, "env_file", "")))


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
    """US$ of one call. ``input_tokens`` counts every prompt token, ``cached_tokens`` the ones read from the cache and
    ``cache_creation_input_tokens`` (Claude) the ones written to it; a prompt above ``long_above`` tokens is billed at
    ``long_factor`` times every rate."""
    price = PRICES.get(model)
    if price is None:
        return None
    input_tokens, cached, output_tokens = token_counts(usage)
    written = usage.get("cache_creation_input_tokens") if isinstance(usage, dict) else 0
    written = written if type(written) is int and 0 <= written <= input_tokens - cached else 0
    factor = price.get("long_factor", 1.0) if input_tokens > price.get("long_above", float("inf")) else 1.0
    return round(
        factor * ((input_tokens - cached - written) * price["input"] + cached * price["cached_input"]
                  + written * price.get("cache_write", price["input"]) + output_tokens * price["output"]) / 1_000_000,
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
        self._no_key = "尚未設定 OPENAI_API_KEY（.env 或 OPENAI_KEY_ENV_FILE）"

    def budget_status(self) -> dict[str, float]:
        now = self._clock()
        spent = self._ledger.month_spend(now, self._operations)
        status = {"spent_usd": spent, "budget_usd": self._budget, "remaining_usd": max(0.0, self._budget - spent)}
        if self._total is not None:
            total = self._ledger.month_spend(now)
            status.update(total_spent_usd=total, total_budget_usd=self._total,
                          remaining_usd=max(0.0, min(self._budget - spent, self._total - total)))
        return status

    def _guard(self) -> None:
        """Before anything is sent: a key, a price for the model and room in this month's budgets."""
        if not self._key:
            raise LLMError(self._no_key)
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

    def _record(self, operation: str, context: str, outcome: str, started: float, usage: dict) -> None:
        """Every call, failed or not, with the price in force (no prompt, key or reply)."""
        now = self._clock()
        input_tokens, cached, output_tokens = token_counts(usage)
        entry = {
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
        }
        if isinstance(usage, dict) and usage.get("served_model"):
            entry["served_model"] = usage["served_model"]
        written = usage.get("cache_creation_input_tokens") if isinstance(usage, dict) else None
        if written:
            entry["cache_write_input_tokens"] = written
        self._ledger.record(entry)

    def json_call(self, instructions: str, user_input: str | list[str], operation: str,
                  context: str = "") -> tuple[dict, dict]:
        """One Responses call that must answer with a JSON object; returns (object, usage). ``user_input`` may be a
        list of parts (a stable beginning, then what changes): they are sent joined."""
        self._guard()
        if isinstance(user_input, (list, tuple)):
            user_input = "\n\n".join(str(part) for part in user_input if part)
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
            self._record(operation, context, outcome, started, usage)


def _get(item: object, name: str) -> object:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def claude_usage(usage: object) -> dict:
    """A Messages API usage block in the ledger's terms: every prompt token in ``input_tokens``, the ones read from the
    cache in ``input_tokens_details.cached_tokens``, the ones written to it in ``cache_creation_input_tokens``."""
    if usage is None:
        return {}

    def count(name: str) -> int:
        value = _get(usage, name)
        return value if type(value) is int and value >= 0 else 0

    read, written, fresh = count("cache_read_input_tokens"), count("cache_creation_input_tokens"), count("input_tokens")
    return {"input_tokens": fresh + read + written, "input_tokens_details": {"cached_tokens": read},
            "cache_creation_input_tokens": written, "output_tokens": count("output_tokens")}


def json_object(text: str) -> dict:
    """The JSON object a reply holds: the whole reply, a fenced block, or the outermost braces in it."""
    value = _strip_fence(text)
    try:
        payload = json.loads(value)
    except ValueError:
        start, end = value.find("{"), value.rfind("}")
        if start < 0 or end <= start:
            raise LLMError("模型回傳的不是 JSON") from None
        try:
            payload = json.loads(value[start: end + 1])
        except ValueError:
            raise LLMError("模型回傳的不是 JSON") from None
    if not isinstance(payload, dict):
        raise LLMError("模型回傳的 JSON 不是物件")
    return payload


def claude_error(exc: Exception, model: str, where: str = "AI API 平台") -> LLMError:
    """An SDK error as an LLMError in the owner's language: the status and the API's own message, never the key.
    ``where``: the service the call went to."""
    if isinstance(exc, LLMError):
        return exc
    try:
        import anthropic
    except ImportError:
        return LLMError("沒有安裝 anthropic 套件（pip install anthropic）")
    detail = str(getattr(exc, "message", "") or "")[:200]
    if isinstance(exc, anthropic.AuthenticationError):
        return LLMError(f"{where}的金鑰無效或停用（HTTP 401）：.env 的 ANTHROPIC_API_KEY 要是平台給 StockResearch 的專案金鑰")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return LLMError(f"{where}沒有權限（HTTP 403）：{detail or '這把金鑰不能用這個模型'}")
    if isinstance(exc, anthropic.NotFoundError):
        return LLMError(f"{where}找不到模型 {model}（HTTP 404）")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError(f"{where}速率限制（HTTP 429，已自動重試 {CLAUDE_RETRIES} 次）：{detail}")
    if isinstance(exc, anthropic.BadRequestError):
        return LLMError(f"{where} HTTP 400：{detail or '請求格式不對'}")
    if isinstance(exc, anthropic.APIStatusError):
        return LLMError(f"{where} HTTP {exc.status_code}：{detail or '伺服器錯誤'}")
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMError(f"{where}逾時（已自動重試 {CLAUDE_RETRIES} 次）")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError(f"連不到{where}（已自動重試 {CLAUDE_RETRIES} 次；平台的服務有沒有在跑？）")
    return LLMError(f"{where}呼叫失敗（{exc.__class__.__name__}）")


class ClaudeClient(ResponsesClient):
    """2026-11-20 (D-051, D-052): ``json_call`` in the Claude Messages format through the official SDK, sent to the AI
    API platform (which passes it to Anthropic, or translates it for another provider's model), with the budget checks
    and the ledger of ``ResponsesClient``. The instructions are the system prompt; the input is one user turn whose
    parts are text blocks, the one before the last marked for the prompt cache — a caller that sends a growing
    conversation as parts (a stable beginning, then the step's question) pays a tenth for the part it sent before. The
    reply's text is parsed as JSON (no prefill, no sampling settings: the model accepts neither). A refusal or a reply
    cut off at ``max_tokens`` is an LLMError."""

    def __init__(self, api_key: str, model: str, ledger: UsageLedger, monthly_budget_usd: float,
                 timeout: float = 180.0, max_output_tokens: int = CLAUDE_MAX_TOKENS,
                 create: Callable[..., object] | None = None, clock: Callable[[], datetime] | None = None,
                 operations: tuple[str, ...] | None = None, total_budget_usd: float | None = None,
                 label: str = "AI 研究員", base_url: str = AI_PLATFORM_URL, effort: str | None = CLAUDE_EFFORT) -> None:
        super().__init__(api_key, model, ledger, monthly_budget_usd, timeout=timeout,
                         max_output_tokens=max(int(max_output_tokens), CLAUDE_MAX_TOKENS), clock=clock,
                         operations=operations, total_budget_usd=total_budget_usd, label=label)
        self._no_key = "尚未設定 AI API 平台的專案金鑰（.env 的 ANTHROPIC_API_KEY）"
        self._create = create
        self._base_url = base_url or AI_PLATFORM_URL
        self._where = "AI API 平台" if self._base_url.rstrip("/") == AI_PLATFORM_URL else "Claude API"
        self._effort = effort
        self._sdk = None
        self.served: list[str] = []      # the model each answer says it came from (2026-11-21: the model lock)

    @property
    def effort(self) -> str | None:
        return self._effort

    def sdk(self):
        """The SDK client, made once. The base URL is given explicitly: an ANTHROPIC_BASE_URL another program left in
        the environment must not send the research calls (and the project's key) elsewhere."""
        if self._sdk is None:
            import anthropic

            self._sdk = anthropic.Anthropic(api_key=self._key, base_url=self._base_url, timeout=self._timeout,
                                            max_retries=CLAUDE_RETRIES)
        return self._sdk

    def body(self, instructions: str, user_input: str | list[str]) -> dict:
        parts = [str(part) for part in user_input] if isinstance(user_input, (list, tuple)) else [str(user_input)]
        content: list[dict] = [{"type": "text", "text": part} for part in parts if part.strip()]
        if not content or "json" not in (instructions + content[-1]["text"]).lower():
            content.append({"type": "text", "text": "請以 JSON 物件回覆。"})
        claude = is_claude(self.model)         # the cache mark and the effort are Claude's; another model gets neither
        if claude and len(content) >= 2:
            content[-2]["cache_control"] = {"type": "ephemeral"}
        body = {"model": self.model, "max_tokens": self._max_output_tokens, "system": instructions,
                "messages": [{"role": "user", "content": content}]}
        if claude and self._effort:
            body["output_config"] = {"effort": self._effort}
        return body

    def json_call(self, instructions: str, user_input: str | list[str], operation: str,
                  context: str = "") -> tuple[dict, dict]:
        self._guard()
        body = self.body(instructions, user_input)
        started = time.perf_counter()
        usage: dict = {}
        outcome = "failed"
        try:
            try:
                message = (self._create or self.sdk().messages.create)(**body)
            except Exception as exc:  # noqa: BLE001 - every SDK error becomes an LLMError without the key
                raise claude_error(exc, self.model, self._where) from None
            usage = claude_usage(_get(message, "usage"))
            # 2026-11-21 (使用者：避免串了不同的模型導致的差異): the answer names its model; one that is not the model
            # asked for (the platform routed or translated it elsewhere) is refused, not used as if it were
            served = str(_get(message, "model") or "")
            if served:
                usage["served_model"] = served
                self.served.append(served)
                if not same_model(self.model, served):
                    raise LLMError(f"{self._where}回應的模型是 {served}，不是設定的 {self.model}；這次的回答不採用")
            stop = _get(message, "stop_reason")
            if stop == "refusal":
                category = _get(_get(message, "stop_details"), "category")
                raise LLMError(f"模型拒絕回答（{category or '沒有說明類別'}）")
            if stop == "max_tokens":
                raise LLMError("模型未完成回覆（max_tokens）")
            text = "\n".join(str(_get(block, "text") or "") for block in _get(message, "content") or []
                           if _get(block, "type") == "text").strip()
            if not text:
                raise LLMError("模型沒有回傳文字")
            payload = json_object(text)
            outcome = "completed"
            return payload, usage
        finally:
            self._record(operation, context, outcome, started, usage)


def same_model(asked: str, served: str) -> bool:
    """Whether an answer's model is the one asked for: the same name, or the name with a snapshot date after it
    (``claude-haiku-5-5`` → ``claude-haiku-5-5-20261001``)."""
    asked, served = asked.strip().lower(), served.strip().lower()
    rest = served[len(asked):] if served.startswith(asked) else None
    return rest is not None and (rest == "" or (rest[0] in "-@" and rest[1:].replace("-", "").isdigit()))


def model_client(settings, ledger: UsageLedger, monthly_budget_usd: float, *, operations: tuple[str, ...] | None = None,
                 total_budget_usd: float | None = None, label: str = "AI 研究員",
                 max_output_tokens: int = 6000, model: str | None = None,
                 effort: str | None = None) -> ResponsesClient | None:
    """The client for ``settings.research_agent_model`` inside the given monthly share, through the AI API platform
    (D-052) whatever the model: the platform sends a ``claude-…`` model to Anthropic and translates the Claude format for
    a ``gpt-…`` or ``gemini-…`` one. None without the project's platform key (ANTHROPIC_API_KEY). ``ResponsesClient``
    (OpenAI's own API) is no longer made here."""
    key = resolve_anthropic_key(getattr(settings, "anthropic_api_key", ""), getattr(settings, "env_file", ""))
    if not key:
        return None
    # ``model`` / ``effort``: a caller whose results are compared over time (the agents) names its own, so a change of
    # the shared setting for another feature does not change the model under a running comparison
    return ClaudeClient(key, model or settings.research_agent_model, ledger, monthly_budget_usd, operations=operations,
                        total_budget_usd=total_budget_usd, label=label, max_output_tokens=max_output_tokens,
                        base_url=base_url_for(settings),
                        effort=effort or getattr(settings, "research_agent_effort", "") or CLAUDE_EFFORT)


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
