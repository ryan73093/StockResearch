"""2026-11-20 (使用者：使用 claude api haiku 並繼續; D-051): the Claude client — the same contract, ledger and caps as the
OpenAI one, on the Messages API through the official SDK. No test makes a network call."""
import json
from datetime import UTC, datetime

import anthropic
import httpx2
import pytest

from quant_platform.config.settings import Settings
from quant_platform.research.agent.llm import (
    CLAUDE_BASE_URL,
    CLAUDE_MAX_TOKENS,
    BudgetExceeded,
    ClaudeClient,
    LLMError,
    ResponsesClient,
    UsageLedger,
    call_cost,
    claude_error,
    json_object,
    key_configured,
    model_client,
)

NOW = datetime(2026, 11, 20, 7, 40, tzinfo=UTC)          # 15:40 in Taipei
MODEL = "claude-haiku-5-5"


def reply(text, stop="end_turn", usage=None, category=None):
    return {"content": [{"type": "thinking", "thinking": "", "signature": "sig"}, {"type": "text", "text": text}],
            "stop_reason": stop, "stop_details": {"category": category} if category else None,
            "usage": usage or {"input_tokens": 200, "cache_read_input_tokens": 1_000, "cache_creation_input_tokens": 300,
                               "output_tokens": 400}}


class FakeCreate:
    def __init__(self, *answers):
        self.answers, self.bodies = list(answers), []

    def __call__(self, **body):
        self.bodies.append(body)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def claude(tmp_path, create, budget=1.0, **options):
    return ClaudeClient("sk-ant-secret", MODEL, UsageLedger(tmp_path / "agent" / "usage.jsonl"), budget, create=create,
                        clock=lambda: NOW, **options)


def ledger(tmp_path):
    return UsageLedger(tmp_path / "agent" / "usage.jsonl").entries()


def test_a_call_sends_the_parts_with_a_cache_mark_and_reads_the_json_after_the_thinking(tmp_path):
    create = FakeCreate(reply('```json\n{"hold": [{"code": "2330"}]}\n```'))
    model = claude(tmp_path, create, operations=("agent_lab",), label="AI 代理人實驗室")

    payload, usage = model.json_call("你是代理人，只回覆 JSON 物件。", ["今天的起點資料", "【第 1 步你查了 card】資料卡",
                                                              "這是第 2 步"], "agent_lab", context="2026-11-20-2")

    assert payload == {"hold": [{"code": "2330"}]}
    body = create.bodies[0]
    assert body["model"] == MODEL and body["system"] == "你是代理人，只回覆 JSON 物件。"
    assert body["max_tokens"] >= CLAUDE_MAX_TOKENS and body["output_config"] == {"effort": "medium"}
    assert not {"temperature", "top_p", "top_k", "thinking"} & set(body)     # the model takes no sampling settings
    content = body["messages"][0]["content"]
    assert [block["text"] for block in content] == ["今天的起點資料", "【第 1 步你查了 card】資料卡", "這是第 2 步"]
    # the conversation so far is cached; the step's question, which changes, comes after the mark
    assert [("cache_control" in block) for block in content] == [False, True, False]
    assert body["messages"][0]["role"] == "user" and len(body["messages"]) == 1          # no prefill
    assert usage["input_tokens"] == 1_500 and usage["input_tokens_details"]["cached_tokens"] == 1_000
    entry = ledger(tmp_path)[-1]
    # 200 × 0.10 + 1,000 × 0.01 + 300 × 0.125 + 400 × 0.50, per 1M tokens
    assert entry["cost_usd"] == pytest.approx(0.0002675) and entry["status"] == "completed"
    assert entry["model"] == MODEL and entry["cache_write_input_tokens"] == 300 and entry["context"] == "2026-11-20-2"
    assert "sk-ant-secret" not in json.dumps(entry)


def test_a_single_input_has_no_cache_mark_and_json_is_asked_for_when_nobody_did(tmp_path):
    create = FakeCreate(reply('{"ok": true}'), reply('{"ok": true}'))
    claude(tmp_path, create).json_call("回覆 JSON。", "一段文字", "knowledge")
    assert [("cache_control" in block) for block in create.bodies[0]["messages"][0]["content"]] == [False]
    claude(tmp_path, create).json_call("說明", "一段文字", "knowledge")
    content = create.bodies[1]["messages"][0]["content"]
    assert content[-1]["text"] == "請以 JSON 物件回覆。" and "cache_control" in content[0]


def test_a_refusal_or_a_reply_cut_off_is_an_error_and_still_on_the_ledger(tmp_path):
    with pytest.raises(LLMError, match="拒絕.*cyber"):
        claude(tmp_path, FakeCreate(reply("", stop="refusal", category="cyber"))).json_call("i", "u", "llm_agent")
    with pytest.raises(LLMError, match="max_tokens"):
        claude(tmp_path, FakeCreate(reply('{"a"', stop="max_tokens"))).json_call("i", "u", "llm_agent")
    with pytest.raises(LLMError, match="不是 JSON"):
        claude(tmp_path, FakeCreate(reply("今天不做決定"))).json_call("i", "u", "llm_agent")
    entries = ledger(tmp_path)
    assert [entry["status"] for entry in entries] == ["failed"] * 3 and all(entry["cost_usd"] > 0 for entry in entries)


def test_prose_around_the_json_is_read_and_anything_else_is_refused():
    assert json_object('好的，以下是決定：{"hold": [], "view": "觀望"}。') == {"hold": [], "view": "觀望"}
    assert json_object('```\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(LLMError, match="不是 JSON"):
        json_object("沒有決定")
    with pytest.raises(LLMError, match="不是物件"):
        json_object("[1, 2]")


def test_the_budget_stops_a_call_before_anything_is_sent(tmp_path):
    UsageLedger(tmp_path / "agent" / "usage.jsonl").record({"month": "2026-11", "operation": "agent_lab", "cost_usd": 1.5})
    create = FakeCreate()
    with pytest.raises(BudgetExceeded):
        claude(tmp_path, create, budget=1.5, operations=("agent_lab",)).json_call("i", "u", "agent_lab")
    with pytest.raises(BudgetExceeded):                   # the US$5 total counts every provider's calls
        claude(tmp_path, create, budget=9.0, operations=("knowledge",), total_budget_usd=1.0).json_call("i", "u", "knowledge")
    assert create.bodies == []


def _response(status, message):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx2.Response(status, request=request, json={"type": "error", "error": {"type": "x", "message": message}})


def test_sdk_errors_become_messages_without_the_key(tmp_path):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    cases = [
        (anthropic.AuthenticationError("invalid x-api-key", response=_response(401, "invalid"), body=None), "金鑰無效"),
        (anthropic.BadRequestError("Your credit balance is too low to access the Anthropic API.",
                                   response=_response(400, "low"), body=None), "credit balance is too low"),
        (anthropic.RateLimitError("rate limited", response=_response(429, "slow down"), body=None), "429"),
        (anthropic.NotFoundError("model: claude-haiku-5-5", response=_response(404, "no"), body=None), "找不到模型"),
        (anthropic.InternalServerError("overloaded", response=_response(529, "overloaded"), body=None), "HTTP 529"),
        (anthropic.APITimeoutError(request=request), "逾時"),
        (anthropic.APIConnectionError(request=request), "連線失敗"),
    ]
    for error, words in cases:
        assert words in str(claude_error(error, MODEL))
    with pytest.raises(LLMError) as raised:
        claude(tmp_path, FakeCreate(cases[0][0])).json_call("i", "u", "llm_agent")
    assert "金鑰無效" in str(raised.value) and "sk-ant-secret" not in str(raised.value)
    assert ledger(tmp_path)[-1]["status"] == "failed"


def test_the_configured_model_picks_the_provider_and_no_key_means_no_client(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_KEY_ENV_FILE", raising=False)
    book = UsageLedger(tmp_path / "usage.jsonl")
    assert Settings().research_agent_model == MODEL                    # the platform's default since 2026-11-20
    without = Settings(anthropic_api_key="")
    assert model_client(without, book, 1.0) is None and not key_configured(without)
    made = model_client(Settings(anthropic_api_key="sk-ant-x"), book, 1.0, max_output_tokens=2_000, label="研究知識庫")
    assert type(made) is ClaudeClient and made.model == MODEL and made._effort == "medium"
    assert made._max_output_tokens == CLAUDE_MAX_TOKENS                 # room for the thinking, whatever the caller asked
    old = model_client(Settings(research_agent_model="gpt-6-luna", openai_api_key="sk-x"), book, 1.0)
    assert type(old) is ResponsesClient and old.model == "gpt-6-luna"
    assert model_client(Settings(research_agent_model="gpt-6-luna", anthropic_api_key="sk-ant-x"), book, 1.0) is None


def test_a_base_url_left_in_the_environment_does_not_redirect_the_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9/elsewhere")
    model = claude(tmp_path, None)
    assert str(model.sdk().base_url).startswith(CLAUDE_BASE_URL)        # made, not called: no request


def test_the_price_of_a_long_prompt_and_of_the_old_model():
    long = {"input_tokens": 120_000, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 1_000}
    assert call_cost(MODEL, long) == pytest.approx(5 * (120_000 * 0.10 + 1_000 * 0.50) / 1e6)
    short = {"input_tokens": 10_000, "input_tokens_details": {"cached_tokens": 2_000}, "output_tokens": 3_000}
    assert call_cost("gpt-6-luna", short) == pytest.approx(0.002525)      # unchanged


def test_the_openai_client_takes_parts_and_sends_them_joined(tmp_path):
    bodies = []

    def post(url, body, headers, timeout):
        bodies.append(body)
        return {"status": "completed", "usage": {},
                "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "{}"}]}]}

    ResponsesClient("sk-test", "gpt-6-luna", UsageLedger(tmp_path / "u.jsonl"), 1.0, post=post,
                    clock=lambda: NOW).json_call("i", ["甲", "", "乙 JSON"], "agent_lab")
    assert bodies[0]["input"] == "甲\n\n乙 JSON"
