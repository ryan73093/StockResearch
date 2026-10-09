"""R15 D (2026-10-09): headlines turned into event fields, the budget caps, the bad-news veto."""

import gzip
import json
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from quant_platform.research import news_events
from quant_platform.research.agent.llm import BudgetExceeded, ResponsesClient, UsageLedger


def write_news(history, day, code, titles):
    folder = history / "raw" / "finmind" / "TaiwanStockNews" / f"{day:%Y%m%d}"
    folder.mkdir(parents=True, exist_ok=True)
    rows = [{"date": f"{day} 09:00:00", "stock_id": code, "title": title, "link": f"https://x/{code}/{n}", "source": "UDN"}
            for n, title in enumerate(titles)]
    (folder / f"{code}.json.gz").write_bytes(gzip.compress(json.dumps({"code": code, "data": rows}).encode("utf-8")))


class FakeClient:
    model = "fake"

    def __init__(self, answer=None, fail_after=None):
        self.calls, self._answer, self._fail_after = [], answer, fail_after

    def json_call(self, instructions, user_input, operation, context=""):
        if self._fail_after is not None and len(self.calls) >= self._fail_after:
            raise BudgetExceeded("本月新聞事件評分預算已用完")
        self.calls.append((operation, user_input))
        items = [json.loads(line) for line in user_input.splitlines()[1:]]
        rows = self._answer(items) if self._answer else [[number, stocks[:1], "營收", -1, 1, -1, 0]
                                                        for number, stocks, _title in items]
        return {"r": rows}, {"input_tokens": 10, "output_tokens": 5}

    def budget_status(self):
        return {"spent_usd": 0.0}


def test_identical_headlines_are_scored_once_with_every_stock(tmp_path):
    day = date(2026, 10, 6)
    write_news(tmp_path, day, "2330", ["台積電9月營收年減  5%", "大盤收高"])
    write_news(tmp_path, day, "2317", ["大盤收高"])
    items = news_events.headlines(tmp_path, day)
    assert {item["title"]: sorted(item["stocks"]) for item in items} == {"台積電9月營收年減 5%": ["2330"],
                                                                        "大盤收高": ["2317", "2330"]}


def test_a_day_is_scored_written_and_never_scored_again(tmp_path):
    day = date(2026, 10, 6)
    write_news(tmp_path, day, "2330", ["台積電9月營收年減5%"])
    write_news(tmp_path, day, "2317", ["鴻海、台積電同步走高"])
    assert news_events.pending_days(tmp_path) == [day]

    def answer(items):
        output = []
        for number, stocks, title in items:
            if "營收" in title:
                output.append([number, ["2330", "9999"], "營收", -1, 1, -1, 0])     # 9999 was not filed: dropped
            # the second headline is left unanswered
        return output

    client = FakeClient(answer)
    frame = news_events.score_day(client, tmp_path, day, now=datetime(2026, 10, 7, 13, 45, tzinfo=UTC))
    assert client.calls[0][0] == news_events.OPERATION and len(client.calls) == 1
    first = frame[frame["title"] == "台積電9月營收年減5%"].iloc[0]
    assert first["about"] and first["direction"] == -1 and first["numbers"] and first["type"] == "營收"
    assert first["scored_at"].startswith("2026-10-07T21:45") and first["prompt_version"] == news_events.PROMPT_VERSION
    assert not frame[frame["stock_id"] == "2317"].iloc[0]["scored"]
    assert news_events.pending_days(tmp_path) == [] and news_events.events_path(tmp_path, day).is_file()


def test_a_budget_stop_leaves_the_day_for_the_next_run(tmp_path):
    day = date(2026, 10, 6)
    write_news(tmp_path, day, "2330", [f"標題 {n}" for n in range(60)])           # two calls of 50
    outcome = news_events.run(FakeClient(fail_after=1), tmp_path)
    assert outcome["scored"] == [] and "預算" in outcome["error"] and news_events.pending_days(tmp_path) == [day]


def test_each_purpose_has_a_cap_inside_the_monthly_total(tmp_path):
    ledger = UsageLedger(tmp_path / "usage.jsonl")
    now = datetime(2026, 10, 9, 12, tzinfo=UTC)
    for operation, cost in (("news_events", 3.9), ("research_daily_round", 0.5)):
        ledger.record({"month": "2026-10", "operation": operation, "cost_usd": cost})
    assert ledger.month_spend(now) == pytest.approx(4.4)
    assert ledger.month_spend(now, ("news_events",)) == pytest.approx(3.9)

    def client(operations, cap):
        return ResponsesClient("key", "gpt-6-luna", ledger, cap, operations=operations, total_budget_usd=5.0,
                               clock=lambda: now, post=lambda *args: {"status": "completed", "output": [
                                   {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "{}"}]}]})

    client(("news_events",), 4.0).json_call("i", "json", "news_events")           # 3.9 < 4.0: allowed
    ledger.record({"month": "2026-10", "operation": "news_events", "cost_usd": 0.2})
    with pytest.raises(BudgetExceeded, match="新聞|預算"):
        client(("news_events",), 4.0).json_call("i", "json", "news_events")       # its own cap
    ledger.record({"month": "2026-10", "operation": "research_daily_round", "cost_usd": 0.5})
    with pytest.raises(BudgetExceeded, match="總預算"):
        client(("research_daily_round",), 2.0).json_call("i", "json", "research_daily_round")   # 5.1 ≥ 5 total


def test_a_flag_is_in_force_from_the_session_after_scoring_for_ten_sessions(tmp_path):
    import pandas as pd

    sessions = [date(2026, 10, 5) + timedelta(days=n) for n in range(30) if (date(2026, 10, 5) + timedelta(days=n)).weekday() < 5]
    folder = tmp_path / "news_events"
    folder.mkdir()
    rows = [{"news_day": "2026-10-06", "stock_id": "2330", "title": "下修財測", "link": "", "source": "", "scored": True,
             "about": True, "type": "財測展望", "direction": -1, "numbers": False, "surprise": -1, "risk": True,
             "model": "m", "prompt_version": "p", "scored_at": "2026-10-07T21:45:00+08:00"},
            {"news_day": "2026-10-06", "stock_id": "2317", "title": "接單暢旺", "link": "", "source": "", "scored": True,
             "about": True, "type": "訂單產品", "direction": 1, "numbers": True, "surprise": 1, "risk": False,
             "model": "m", "prompt_version": "p", "scored_at": "2026-10-07T21:45:00+08:00"}]
    pd.DataFrame(rows).to_parquet(folder / "20261006.parquet", index=False)
    veto = news_events.veto_matrix(tmp_path, sessions, ["2330.TW", "2317.TW"])
    first = sessions.index(date(2026, 10, 8))                 # scored the evening of 10-07
    last = sessions.index(date(2026, 10, 6)) + news_events.VETO_SESSIONS
    assert not veto[0, :first].any() and veto[0, first: last + 1].all() and not veto[0, last + 1:].any()
    assert not veto[1].any()                                  # good news never vetoes


def test_the_veto_blocks_new_buys_and_the_control_changes_nothing(tmp_path):
    from quant_platform.research.daily import DailyRule, FactorPanel, daily_rankings
    from quant_platform.research.legacy_challenger import LegacyData
    from quant_platform.research.stock_rules import Panel

    days = [date(2024, 1, 1) + timedelta(days=n) for n in range(600) if (date(2024, 1, 1) + timedelta(days=n)).weekday() < 5]
    closes = {f"{1101 + i}.TW": {day: 20 * (1 + 0.001 * (i + 1)) ** n for n, day in enumerate(days)} for i in range(5)}
    closes["0050.TW"] = {day: 100.0 for day in days}
    data = LegacyData(sessions=days, closes=closes, traded_value={s: {d: 5e7 for d in v} for s, v in closes.items()},
                      factors={}, predictions={})
    fp = FactorPanel(Panel(data))
    strongest = "1105.TW"
    veto = np.zeros((len(fp.symbols), len(days)), dtype=bool)
    veto[fp.row[strongest], :] = True
    fp._cache["__news_veto__"] = veto
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, min_hold=0)
    start, end = days[300], days[-1]                       # every stock eligible (a year of history)
    plain = daily_rankings(fp, rule, start, end)
    vetoed = daily_rankings(fp, rule.model_copy(update={"news_veto": "v1"}), start, end)
    control = daily_rankings(fp, rule.model_copy(update={"news_veto": "v1-off"}), start, end)
    assert control == plain and all(strongest in held for held in plain.values())
    assert all(strongest not in held for held in vetoed.values())
    assert "news_veto" not in rule.canonical() and rule.model_copy(update={"news_veto": "v1"}).rule_hash != rule.rule_hash
