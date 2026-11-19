"""2026-11-19: the stock-picking agent, forward only (research/llm_agent.py)."""
import json
from datetime import date, timedelta

import numpy as np

from quant_platform.research import llm_agent


def test_the_twin_keeps_its_holdings_among_the_agreed_and_fills_by_agreement():
    assert llm_agent.control_choice(["2330", "2317", "2454", "3008"], ["3008", "1101"]) == ["3008", "2330", "2317", "2454"]
    assert len(llm_agent.control_choice([str(1000 + i) for i in range(30)], [])) == llm_agent.HOLD


def test_a_decision_that_breaks_a_hard_rule_is_not_used():
    allowed = lambda code: code != "0050"                                 # noqa: E731
    same = lambda code: "半導體"                                          # noqa: E731
    seen = {"2330", "2317", "0050"} | {str(2000 + i) for i in range(8)}
    codes, reasons, problem = llm_agent.validate({"hold": [{"code": "2330", "reason": "共識高"}]}, allowed, seen, same)
    assert codes == ["2330"] and reasons == ["共識高"] and problem is None
    assert "不是可交易" in llm_agent.validate({"hold": [{"code": "0050"}]}, allowed, seen, same)[2]
    assert "沒有查過" in llm_agent.validate({"hold": [{"code": "9999"}]}, allowed, seen, same)[2]   # a code it never looked at
    assert "同一產業" in llm_agent.validate({"hold": [{"code": str(2000 + i)} for i in range(8)]}, allowed, seen, same)[2]
    assert llm_agent.validate({"tool": "market"}, allowed, seen, same)[2].startswith("回覆格式")


class _Desk:
    def __init__(self):
        self.asked = []

    def tool_market(self, args):
        return "大盤"

    def tool_portfolio(self, args):
        return "持股"

    def tool_journal(self, args):
        return "日誌"

    def tool_consensus(self, args):
        return "共識"

    def tool_card(self, args):
        self.asked.append(args)
        return "資料卡 2330"


class _Client:
    model = "none"

    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def json_call(self, instructions, prompt, operation, context=""):
        self.prompts.append(prompt)
        return self.answers.pop(0), {}


def test_the_agent_asks_for_tools_then_decides_and_what_it_saw_is_sent_back(monkeypatch):
    monkeypatch.setattr(llm_agent, "screen_signals", lambda: ["momentum_6"])
    desk = _Desk()
    client = _Client([{"tool": "card", "args": {"codes": ["2330"]}, "why": "看細節"}, {"tool": "nothing"},
                      {"hold": [{"code": "2330", "reason": "好"}], "view": "穩", "lesson": "少換股"}])
    answer, trace, _cost = llm_agent.converse(desk, client, "2026-11-19")
    assert answer["hold"][0]["code"] == "2330" and desk.asked == [{"codes": ["2330"]}]
    assert [step["tool"] for step in trace] == ["card", "nothing"]
    assert "資料卡 2330" in client.prompts[1] and "沒有這個工具" in client.prompts[2]
    stuck = _Client([{"tool": "card", "args": {}}] * llm_agent.STEPS)
    answer, trace, _cost = llm_agent.converse(_Desk(), stuck, "2026-11-19")
    assert answer is None and len(trace) == llm_agent.STEPS - 1          # the last step had to be the decision
    assert "一定要做出決定" in stuck.prompts[-1]


def test_a_choice_ranks_its_stocks_from_the_next_session(tmp_path):
    days = [date(2026, 11, 16) + timedelta(days=i) for i in range(5)]
    fp = type("FP", (), {"sessions": days, "symbols": ["2330.TW", "2317.TW"], "row": {"2330.TW": 0, "2317.TW": 1}})()
    folder = llm_agent.folder(tmp_path)
    folder.mkdir(parents=True)
    (folder / "decisions.jsonl").write_text(json.dumps({"date": "2026-11-17", "llm": {"hold": ["2317", "2330"]},
                                                        "control": {"hold": ["2330"]}}), encoding="utf-8")
    scores = llm_agent.scores(fp, tmp_path, "llm")
    assert np.isnan(scores[:2]).all() and scores[2, 1] > scores[2, 0]
    assert np.isnan(llm_agent.scores(fp, tmp_path, "control")[2, 1])
    assert llm_agent.first_decision(tmp_path) == "2026-11-17"
