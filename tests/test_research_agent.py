import json
from datetime import UTC, date, datetime, timedelta

import pytest

from quant_platform.research.agent.llm import (
    BudgetExceeded,
    LLMError,
    ResponsesClient,
    UsageLedger,
    call_cost,
    resolve_openai_key,
)
from quant_platform.research.agent.researcher import AgentLimits, ResearchAgent, rule_key
from quant_platform.research.market import MarketData
from quant_platform.research.spec import BASELINES

NOW = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)  # 22:00 in Taipei


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


SESSIONS = weekdays(date(2013, 1, 2), date(2022, 12, 30))
MARKET = MarketData(
    sessions=SESSIONS,
    closes={
        "0050": {day: 50.0 + (index % 60) * 0.5 for index, day in enumerate(SESSIONS)},
        "00878": {day: 15.0 for day in SESSIONS if day >= date(2020, 7, 20)},
    },
    tax_kind={"0050": "stock_etf", "00878": "stock_etf"},
)


def completed(payload, usage=None):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {
        "status": "completed",
        "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}],
        "usage": usage or {"input_tokens": 8_000, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 2_000},
    }


class FakePost:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.bodies = []

    def __call__(self, url, body, headers, timeout):
        self.bodies.append(body)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def client(tmp_path, post, budget=3.0):
    return ResponsesClient("sk-test", "gpt-6-luna", UsageLedger(tmp_path / "agent" / "usage.jsonl"), budget,
                           post=post, clock=lambda: NOW)


def test_cost_by_hand_and_key_file(tmp_path, monkeypatch):
    usage = {"input_tokens": 10_000, "input_tokens_details": {"cached_tokens": 2_000}, "output_tokens": 3_000}
    # (8,000 × 0.125 + 2,000 × 0.0125 + 3,000 × 0.50) / 1M
    assert call_cost("gpt-6-luna", usage) == pytest.approx(0.002525)
    assert call_cost("unknown-model", usage) is None

    secrets = tmp_path / "other.env"
    secrets.write_text("OTHER=1\nOPENAI_API_KEY='sk-from-file'\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_KEY_ENV_FILE", str(secrets))
    assert resolve_openai_key() == "sk-from-file"
    assert resolve_openai_key("sk-direct") == "sk-direct"


def test_json_call_parses_records_cost_and_stops_at_the_budget(tmp_path):
    post = FakePost(completed('```json\n{"hypothesis": "h", "specs": []}\n```'))
    model = client(tmp_path, post, budget=0.0026)

    payload, _usage = model.json_call("instructions", "input", "research_round")

    assert payload == {"hypothesis": "h", "specs": []}
    body = post.bodies[0]
    assert body["store"] is False and body["model"] == "gpt-6-luna" and body["text"]["format"]["type"] == "json_object"
    assert "JSON" in body["input"]  # the json_object format requires the word in the input (HTTP 400 otherwise)
    assert model.budget_status()["spent_usd"] == pytest.approx(0.002)   # 8,000 × 0.125 + 2,000 × 0.50
    second = client(tmp_path, FakePost(completed({"x": 1})), budget=0.0026)
    second.json_call("i", "u", "research_round")                        # 0.004 now spent
    with pytest.raises(BudgetExceeded):
        client(tmp_path, FakePost(), budget=0.0026).json_call("i", "u", "research_round")  # no request is made


def test_failed_calls_are_recorded_and_unpriced_models_refused(tmp_path):
    incomplete = {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                  "usage": {"input_tokens": 1_000, "output_tokens": 6_000}}
    model = client(tmp_path, FakePost(incomplete, completed("not json")))
    with pytest.raises(LLMError, match="max_output_tokens"):
        model.json_call("i", "u", "research_round")
    with pytest.raises(LLMError, match="不是 JSON"):
        model.json_call("i", "u", "research_round")
    rows = UsageLedger(tmp_path / "agent" / "usage.jsonl").entries()
    assert [row["status"] for row in rows] == ["failed", "failed"] and rows[0]["cost_usd"] > 0

    unpriced = ResponsesClient("sk", "gpt-unknown", UsageLedger(tmp_path / "u.jsonl"), 3.0, post=FakePost())
    with pytest.raises(LLMError, match="單價"):
        unpriced.json_call("i", "u", "x")


GOOD = {
    "name": "回撤：跌 8% 加倍、平時 0.8 倍",
    "description": "測試",
    "allocation": {"weights": {"0050": 1.0}},
    "sizing": {"type": "drawdown", "drawdown_threshold": 0.08, "weak_multiplier": 2.0, "strong_multiplier": 0.8},
}


def round_payload(*specs):
    return {"analysis": "第一輪", "hypothesis": "回撤時多買", "rationale": "均值回歸",
            "specs": list(specs), "extension_ideas": ["依月營收調整"]}


def agent(tmp_path, *responses, limits=AgentLimits(rounds_per_night=2, specs_per_round=3, trials_per_night=12)):
    return ResearchAgent(tmp_path, client(tmp_path, FakePost(*responses)), limits, clock=lambda: NOW)


def test_round_validates_specs_and_registers_development_trials(tmp_path):
    duplicate = {**BASELINES["ma_value"].model_dump(mode="json"), "name": "均線：換個名字"}
    payload = round_payload(
        GOOD,
        duplicate,                                                                  # same rule as a baseline
        {**GOOD, "name": "配置：權重不足", "allocation": {"weights": {"0050": 0.9}}},  # weights do not sum to 1
        {**GOOD, "name": "標的：00878", "allocation": {"weights": {"00878": 1.0}}},   # no development data
        {**GOOD, "name": "個股：台積電", "allocation": {"weights": {"2330": 1.0}}},    # not a catalog ETF
    )
    researcher = agent(tmp_path, completed(payload))

    entry = researcher.run_round(MARKET, 1, trials_left=12)

    assert entry["status"] == "ok" and entry["hypothesis"] == "回撤時多買"
    assert [item["name"] for item in entry["accepted"]] == [GOOD["name"]]
    reasons = {item["name"]: item["reason"] for item in entry["rejected"]}
    assert reasons["均線：換個名字"] == "與已測規則相同"
    assert "StrategySpec" in reasons["配置：權重不足"] and "00878" in reasons["標的：00878"]
    assert "StrategySpec" in reasons["個股：台積電"]
    records = researcher.registry.records()
    assert [(record.kind, record.period, record.spec_name) for record in records] == [
        ("candidate", "development", GOOD["name"]),
    ]
    assert json.loads((tmp_path / "journal.jsonl").read_text(encoding="utf-8").splitlines()[0])["round_id"]
    assert rule_key(BASELINES["ma_value"]) in researcher.tested_rules()


def test_prompt_shows_only_development_results(tmp_path):
    from quant_platform.research.agent.researcher import STANDARD_PLAN
    from quant_platform.research.periods import run_trial
    from quant_platform.research.spec import StrategySpec

    researcher = agent(tmp_path)
    spec = StrategySpec.model_validate(GOOD)
    for period in ("development", "validation"):
        run_trial(kind="candidate", spec=spec, period=period, market=MARKET, plan=STANDARD_PLAN,
                  registry=researcher.registry, reports_dir=tmp_path / "reports", window_months=(12,))
    validation = [record for record in researcher.registry.records() if record.period == "validation"][0]
    development = [record for record in researcher.registry.records() if record.period == "development"][0]

    instructions, user_input = researcher.build_prompt(MARKET)

    # The first night's model asked for the benchmark itself; it comes with every development trial.
    assert "比較基準「定期定額基準」（每月 5 日入帳當天全數買 0050）開發期" in user_input
    assert (f"XIRR {development.metrics['benchmark_xirr']:.2%}、"
            f"最大回撤 {development.metrics['benchmark_max_drawdown']:.2%}") in user_input
    assert f"{validation.metrics['benchmark_xirr']:.2%}" not in user_input or (
        validation.metrics["benchmark_xirr"] == development.metrics["benchmark_xirr"])

    assert "不要推測或引用 2017 年以後的行情" in instructions and "每輪最多提出 3 個設定" in instructions
    assert "0050（元大台灣50，2013-01-02 起）" in user_input and "00878" not in user_input.split("StrategySpec")[0]
    assert user_input.count(GOOD["name"]) == 1  # the development trial only
    assert str(validation.metrics["xirr"]) not in user_input


def test_prompt_notes_a_synthetic_backfill(tmp_path):
    from dataclasses import replace

    researcher = agent(tmp_path)
    noted = replace(MARKET, notes={"0050": "2014-10-30 以前為合成（2 × 0050 含息日報酬 − 3.5%／年）"})
    _instructions, user_input = researcher.build_prompt(noted)
    assert "0050（元大台灣50，2013-01-02 起，2014-10-30 以前為合成（2 × 0050 含息日報酬 − 3.5%／年））" in user_input


def test_night_stops_at_limits_and_errors_and_saves_stats(tmp_path):
    second = {**GOOD, "name": "回撤：跌 12% 三倍", "sizing": {**GOOD["sizing"], "drawdown_threshold": 0.12,
                                                            "weak_multiplier": 3.0}}
    third = {**GOOD, "name": "回撤：跌 6% 1.5 倍", "sizing": {**GOOD["sizing"], "drawdown_threshold": 0.06,
                                                             "weak_multiplier": 1.5}}
    researcher = agent(
        tmp_path, completed(round_payload(GOOD, second)), completed(round_payload(third)),
        limits=AgentLimits(rounds_per_night=3, specs_per_round=2, trials_per_night=3),
    )

    entries = researcher.run_night(MARKET)

    assert [entry["status"] for entry in entries] == ["ok", "ok"]          # 3 trials used: the night ends
    assert sum(len(entry["accepted"]) for entry in entries) == 3
    assert list((tmp_path / "stats").glob("development-*.json"))
    assert not (tmp_path / "agent" / "running.lock").exists()

    failing = agent(tmp_path / "other", LLMError("OpenAI API HTTP 401"))
    entries = failing.run_night(MARKET)
    assert [entry["status"] for entry in entries] == ["llm_error"] and "401" in entries[0]["error"]


def test_a_manual_run_and_the_scheduled_one_share_one_night(tmp_path):
    second = {**GOOD, "name": "回撤：跌 12% 三倍", "sizing": {**GOOD["sizing"], "drawdown_threshold": 0.12,
                                                            "weak_multiplier": 3.0}}
    limits = AgentLimits(rounds_per_night=3, specs_per_round=2, trials_per_night=12)
    manual = ResearchAgent(tmp_path, client(tmp_path, FakePost(completed(round_payload(GOOD)))), limits,
                           clock=lambda: NOW - timedelta(hours=1))
    manual.run_round(MARKET, 1, trials_left=12)                     # 21:00: one round, one trial
    error = ResearchAgent(tmp_path, client(tmp_path, FakePost(LLMError("timeout"))), limits,
                          clock=lambda: NOW - timedelta(minutes=30))
    error.run_round(MARKET, 2, trials_left=11)                       # a failed round does not count

    scheduled = agent(tmp_path, completed(round_payload(second)), completed(round_payload()), limits=limits)
    assert scheduled.used_tonight() == (1, 1)
    entries = scheduled.run_night(MARKET)                            # 22:00: rounds 2 and 3 only
    assert [entry["round_id"][-1] for entry in entries] == ["2", "3"]

    assert agent(tmp_path, limits=limits).run_night(MARKET) == []   # the night is used up: no model call
    tomorrow = ResearchAgent(tmp_path, None, limits, clock=lambda: NOW + timedelta(hours=21))
    assert tomorrow.used_tonight() == (0, 0)


def test_a_running_night_is_not_started_twice(tmp_path):
    lock = tmp_path / "agent" / "running.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("123", encoding="utf-8")

    assert agent(tmp_path).run_night(MARKET) == []
    assert lock.exists()


def test_research_page_shows_the_agent(tmp_path):
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False)
    research = tmp_path / "research"
    agent(research, completed(round_payload(GOOD))).run_round(MARKET, 1, trials_left=12)

    body = create_app(build_container(settings)).test_client().get("/research?tab=legacy").get_data(as_text=True)

    assert "AI 研究員" in body and "gpt-6-luna" in body and "回撤時多買" in body
    assert GOOD["name"] in body and "累計 1 輪、1 個 AI 試驗" in body


def test_research_hour_must_be_at_night():
    from quant_platform.config.settings import _night_hour

    assert _night_hour("22") == 22 and _night_hour("3") == 3
    with pytest.raises(ValueError):
        _night_hour("12")
