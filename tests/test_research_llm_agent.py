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


def test_a_short_list_is_filled_to_twenty_and_a_stock_under_disposition_is_not_bought_anew():
    """2026-11-20 (使用者：看研究中交易的策略再參考；設規則要有依據): every forward rule that held up holds 20, none of
    the 104 holding 10 did; the disposition list fits as an exclusion (docs/research/results.md「代理人的硬規則依據」)."""
    agreed = [str(3000 + i) for i in range(30)]
    industry = lambda code: "半導體" if code in ("3000", "3001", "3002", "3003", "3004", "3005", "3006") else code  # noqa: E731
    codes, filled, dropped = llm_agent.complete(["2330", "9999"], agreed, held=["9999"], blocked={"2330", "9999", "3007"},
                                                industry=industry)
    assert dropped == ["2330"] and codes[0] == "9999"                     # a new buy blocked; a holding kept
    assert len(codes) == 20 and "3007" not in codes                       # filled from the agreed, never a blocked one
    assert [code for code in codes if industry(code) == "半導體"] == ["3000", "3001", "3002", "3003", "3004", "3005"]
    assert filled == codes[1:]
    already = [str(4000 + i) for i in range(20)]
    assert llm_agent.complete(already, agreed, [], set(), lambda code: code) == (already, [], [])
    twin = llm_agent.control_choice(["5000", "5001", "5002"], held=["5001"], blocked={"5001", "5002"})
    assert twin == ["5001", "5000"]                                       # the holding stays; the blocked one not bought


def test_the_filled_places_follow_the_twins_rule_and_an_agents_own_sale_is_not_put_back():
    """2026-11-20: places filled before stay while still among the agreed (the twin's own rule), so an agent differs
    from its twin only by the stocks it names; a stock the agent named and dropped is not filled back."""
    agreed = [str(3000 + i) for i in range(30)]
    same = lambda code: code                                              # noqa: E731
    yesterday = {"llm": {"hold": ["1111", "2222"] + agreed[25:30] + agreed[:13], "filled": agreed[25:30] + agreed[:13]}}
    keep, skip = llm_agent.filling(yesterday, ["2222"])
    assert keep == agreed[25:30] + agreed[:13] and skip == {"1111"}       # 1111 was its own pick, now sold
    held = yesterday["llm"]["hold"]
    codes, filled, _dropped = llm_agent.complete(["2222", "3010"], agreed + ["1111"], held, set(), same, keep=keep, skip=skip)
    assert codes[:2] == ["2222", "3010"] and "1111" not in codes
    assert filled[:5] == agreed[25:30]                                    # yesterday's fills low in the agreed: kept first
    # then the rest of yesterday's fills (3010 is its own pick today), then the agreed order: 3013
    assert len(codes) == 20 and filled[5:] == [code for code in agreed[:14] if code != "3010"]
    gone = llm_agent.complete([], agreed[:10], held, set(), same, keep=agreed[25:30])[0]
    assert not set(agreed[25:30]) & set(gone)                             # fills that left the agreed are not kept
    assert llm_agent.filling(None, ["2330"]) == ([], set())


def test_the_disposition_list_is_known_only_after_its_announcement(tmp_path):
    import pandas as pd

    (tmp_path / "chips").mkdir()
    pd.DataFrame([{"date": date(2026, 11, 16), "code": "6108", "start": date(2026, 11, 17), "end": date(2026, 11, 23), "order": 1},
                  {"date": date(2026, 11, 20), "code": "2466", "start": date(2026, 11, 23), "end": date(2026, 12, 4), "order": 2},
                  {"date": date(2026, 11, 2), "code": "1101", "start": date(2026, 11, 3), "end": date(2026, 11, 14), "order": 1}]
                 ).to_parquet(tmp_path / "chips" / "AlertDisposition.parquet")
    assert llm_agent.disposition(tmp_path, date(2026, 11, 20)) == {"6108"}       # announced that day: tonight's news
    assert llm_agent.disposition(tmp_path, date(2026, 11, 23)) == {"6108", "2466"}
    assert llm_agent.disposition(tmp_path / "missing", date(2026, 11, 20)) == set()
    assert "請選 20 檔" in llm_agent.INSTRUCTIONS and llm_agent.VERSION == "llm-agent-1.1.0"
