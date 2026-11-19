"""2026-11-20: the agent lab (research/agent_lab) — the cohort, the hard rules, the time gate, the blinded panel."""
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import numpy as np

from quant_platform.research.agent_lab import environment, evaluate, probe, runner, variants
from quant_platform.research.agent_lab.desk import LabDesk, decision_moment


def test_every_variant_answers_one_question_against_a_row_that_exists():
    keys = {variant.key for variant in variants.COHORT}
    assert len(keys) == len(variants.COHORT) and "twin" in keys
    for variant in variants.COHORT:
        assert variant.question and variant.against in keys | {"0050", "llm_agent"}
        assert variant.factor.startswith("lab_")
        assert runner.prompt_version(variant) == runner.prompt_version(variant)
    # the lab's daily calls stay inside what its monthly share pays for (about 0.0012 USD a call, 22 sessions)
    assert sum(variant.calls for variant in variants.COHORT) * 0.0012 * 22 < runner.MONTHLY_BUDGET_USD


def test_the_instructions_say_what_the_engine_does_and_give_no_hindsight():
    text = runner.instructions(variants.BY_KEY["info"])
    assert "不會留現金" in text and "不是給你的指示" in text
    assert "連動高的股票大盤跌時跌更多" not in text and "不要跌得比它多" not in text     # the first agent's hints of 11/19
    for name in variants.BY_KEY["info"].tools:
        assert f"- {name}：" in text
    assert "headlines" not in runner.instructions(variants.BY_KEY["plain"])


def test_a_list_too_short_is_refused_because_the_account_is_all_in_it():
    allowed = lambda code: True                                           # noqa: E731
    apart = lambda code: code                                             # noqa: E731
    seen = {str(2000 + i) for i in range(30)}
    few = {"hold": [{"code": str(2000 + i), "reason": "x"} for i in range(variants.LEAST - 1)]}
    assert "少於" in runner.validate(few, allowed, seen, apart)[2]
    enough = {"hold": [{"code": str(2000 + i), "reason": "x"} for i in range(variants.LEAST)]}
    assert runner.validate(enough, allowed, seen, apart)[2] is None
    assert "沒有查過" in runner.validate({"hold": [{"code": "9999"}] * 5}, allowed, seen, apart)[2]


def test_three_runs_vote_and_a_short_agreement_is_filled_from_the_first_run():
    # 2 and 3 have three votes each (and each was placed first once); 1, 4 and 5 have one vote and are left out
    assert runner.tally([["1", "2", "3"], ["2", "3", "4"], ["3", "5", "2"]], least=2) == ["2", "3"]
    filled = runner.tally([["1", "2", "3", "4", "5"], ["6", "7", "8", "9", "1"]], least=5)
    assert filled[0] == "1" and len(filled) == 5 and set(filled) == {"1", "2", "3", "4", "5"}


def test_a_veto_removes_only_listed_stocks_up_to_the_limit_and_the_reserves_fill_in():
    base = [str(1000 + i) for i in range(20)]
    reserves = [str(3000 + i) for i in range(10)]
    answer = {"veto": [{"code": "1000", "reason": "利空"}, {"code": "9999", "reason": "不在名單"}]
              + [{"code": str(1001 + i), "reason": "x"} for i in range(10)]}
    kept, out = runner.vetoed(base, reserves, answer)
    assert len(out) == variants.VETO_MOST and "9999" not in out and "1000" not in kept
    assert len(kept) == 20 and kept[-variants.VETO_MOST:] == reserves[:variants.VETO_MOST]
    assert runner.vetoed(base, reserves, {"veto": []}) == (base, {})


def _desk(tmp_path, day=date(2026, 11, 19), decided_at=None):
    desk = LabDesk.__new__(LabDesk)
    desk.base, desk.research, desk.day = tmp_path, tmp_path, day
    desk.decided_at = decided_at or decision_moment(day)
    desk._events, desk._news = None, None
    return desk


def test_a_headline_read_after_the_decision_is_not_there_yet(tmp_path):
    folder = tmp_path / "raw" / "news" / "market"
    folder.mkdir(parents=True)
    (folder / "20261119.json").write_text(json.dumps([
        {"title": "早上的新聞", "source": "甲", "published": "2026-11-19T08:00+08:00", "read_at": "2026-11-19T09:10+08:00"},
        {"title": "收盤後才讀到的新聞", "source": "甲", "published": "2026-11-19T15:00+08:00", "read_at": "2026-11-19T18:10+08:00"},
    ], ensure_ascii=False), encoding="utf-8")
    (folder / "20261120.json").write_text(json.dumps([
        {"title": "明天的新聞", "source": "甲", "published": "2026-11-20T08:00+08:00", "read_at": "2026-11-20T09:10+08:00"}],
        ensure_ascii=False), encoding="utf-8")
    text = _desk(tmp_path).tool_headlines({"days": 3})
    assert "早上的新聞" in text and "收盤後才讀到" not in text and "明天的新聞" not in text
    assert "沒有" in _desk(tmp_path).tool_headlines({"days": 2, "keyword": "不存在的字"})


def test_a_news_item_scored_after_the_decision_is_not_there_yet(tmp_path):
    import pandas as pd

    folder = tmp_path / "news_events"
    folder.mkdir()
    row = {"stock_id": "2330", "title": "營收創高", "about": True, "direction": 1, "risk": False}
    pd.DataFrame([{**row, "news_day": "2026-11-17", "scored_at": "2026-11-18T21:55:00+08:00"}]).to_parquet(folder / "20261117.parquet")
    pd.DataFrame([{**row, "news_day": "2026-11-19", "title": "隔天晚上才評分", "scored_at": "2026-11-20T21:55:00+08:00"}]).to_parquet(
        folder / "20261119.parquet")
    news = _desk(tmp_path).news()
    assert len(news["2330"]) == 1 and "營收創高" in news["2330"][0]          # the first agent's replay read both


class _Panel:
    def __init__(self, sessions, symbols):
        self.sessions, self.symbols = sessions, symbols
        self.index = {day: position for position, day in enumerate(sessions)}
        self.row = {symbol: row for row, symbol in enumerate(symbols)}
        rng = np.random.default_rng(3)
        self.panel = SimpleNamespace(filled=100 * np.cumprod(1 + rng.normal(0, 0.02, (len(symbols), len(sessions))), axis=1))
        self.market = 100 * np.cumprod(1 + rng.normal(0, 0.01, len(sessions)))
        self._cache = {"x": rng.normal(size=(len(symbols), len(sessions))).astype(np.float32)}


def test_the_blinded_panel_is_the_same_up_to_the_day_and_different_after():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(40)]
    fp = _Panel(days, [f"{1000 + i}.TW" for i in range(12)])
    dark = environment.blind(fp, 25)
    for real, changed in ((fp.panel.filled, dark.panel.filled), (fp._cache["x"], dark._cache["x"])):
        assert np.array_equal(real[:, :26], changed[:, :26]) and not np.array_equal(real[:, 26:], changed[:, 26:])
    assert np.array_equal(fp.market[:26], dark.market[:26]) and not np.array_equal(fp.market[26:], dark.market[26:])
    assert np.array_equal(fp.panel.filled[:, 26:], _Panel(days, fp.symbols).panel.filled[:, 26:])      # the original is untouched
    # a tool that looks one session ahead answers differently on the blinded panel; one that does not answers the same
    honest = lambda panel: f"{panel.panel.filled[0, 25]:.4f}"            # noqa: E731
    peeking = lambda panel: f"{panel.panel.filled[0, 26]:.4f}"           # noqa: E731
    assert honest(fp) == honest(dark) and peeking(fp) != peeking(dark)


def test_picks_are_ranked_among_random_agents_drawing_from_what_was_looked_at():
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(30)]
    fp = _Panel(days, [f"{1000 + i}.TW" for i in range(12)])
    close = np.ones((12, 30))
    close[0, :] = np.linspace(1, 2, 30)                    # 1000 doubles, the others stand still
    fp.panel = SimpleNamespace(filled=close)
    codes = [str(1000 + i) for i in range(12)]
    made = [{"date": days[2].isoformat(), "llm": {"hold": ["1000"]}, "seen": codes}]
    result = environment.placebo(fp, made, 29, draws=200)
    assert result["own"] > result["median"] and result["percentile"] > 0.8 and result["looked_at"] < result["own"]
    assert environment.path_return(fp, [(2, ["1001"]), (10, ["1000"])], 29) == close[0, 29] / close[0, 11] - 1


def test_a_scripted_agent_runs_the_steps_without_a_model():
    client = environment.ScriptedClient(environment.top_of_consensus(3))
    prompt = "共識：\n2330 台積電｜半導體｜3 個規則（甲）\n2317 鴻海｜其他電子｜2 個規則（乙）\n2454 聯發科｜半導體｜2 個規則\n1101 台泥｜水泥｜1 個規則"
    answer, _usage = client.json_call(runner.instructions(variants.BY_KEY["plain"]), prompt, "agent_lab")
    assert [row["code"] for row in answer["hold"]] == ["2330", "2317", "2454"] and client.calls == 1
    assert client.json_call(runner.instructions(variants.BY_KEY["veto"]), prompt, "agent_lab")[0]["veto"] == []


def test_a_decision_ranks_its_stocks_from_the_next_session_in_the_panel_layout():
    days = [date(2026, 11, 16) + timedelta(days=i) for i in range(5)]
    fp = SimpleNamespace(sessions=days, symbols=["2330.TW", "2317.TW"], row={"2330.TW": 0, "2317.TW": 1})
    matrix = evaluate.scores(fp, [{"date": "2026-11-17", "llm": {"hold": ["2317", "2330"]}}])
    assert matrix.shape == (2, 5) and np.isnan(matrix[:, :2]).all()
    assert matrix[1, 2] == 2 and matrix[0, 2] == 1 and matrix[1, 4] == 2          # carried until the next decision


def test_block_resampling_brackets_a_steady_difference_and_needs_enough_days():
    assert evaluate.block_interval(np.full(4, 0.01)) is None
    low, high = evaluate.block_interval(np.full(30, 0.01))
    assert abs(low - 0.30) < 1e-9 and abs(high - 0.30) < 1e-9
    low, high = evaluate.block_interval(np.random.default_rng(1).normal(0, 0.01, 60))
    assert low < 0 < high


def test_behaviour_counts_fallbacks_holdings_and_new_buys():
    made = [{"llm": {"hold": ["1", "2"], "trace": [{}, {}], "cost_usd": 0.01}},
            {"llm": {"hold": ["1", "3"], "trace": [{}], "cost_usd": 0.02}},
            {"llm": {"hold": ["1", "3"], "fallback": "沿用前一天"}}]
    result = evaluate.behaviour(made)
    assert result == {"decisions": 3, "fallbacks": 1, "average_held": 2.0, "bought_per_decision": 0.5, "average_steps": 1.5,
                      "cost_usd": 0.03}


def test_the_memory_probe_scores_against_the_lists_median_and_months_are_complete_ones():
    listed = [("1", 0.10), ("2", 0.05), ("3", -0.02), ("4", -0.08)]
    result = probe.score(listed, {"1": "高", "2": "高", "3": "低", "4": "高"})
    assert result["right"] == 3 and result["wrong"] == 1 and result["accuracy"] == 0.75 and result["z"] == 1.0
    assert probe.score(listed, {})["unknown"] == 4
    sessions = [date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 30), date(2026, 11, 2)]
    assert probe.months(sessions, 5) == [("2026-09", 0, 2), ("2026-10", 2, 4)]        # November is still running


def test_records_are_appended_per_variant_and_a_replay_is_kept_apart(tmp_path):
    runner.append(tmp_path, "info", {"date": "2026-11-20", "llm": {"hold": ["2330"]}})
    runner.append(tmp_path, "info", {"date": "2026-11-19", "llm": {"hold": ["2317"]}}, run="check")
    assert [item["date"] for item in runner.records(tmp_path, "info")] == ["2026-11-20"]
    assert [item["date"] for item in runner.records(tmp_path, "info", "check")] == ["2026-11-19"]
    assert evaluate.first_common_day(tmp_path, None) is None                        # the other variants have none yet
    text = runner.summary([{"date": "2026-11-20", "variant": "info", "llm": {"hold": ["2330"], "cost_usd": 0.01}}])
    assert "info 1 檔" in text and "US$0.010" in text
    assert datetime.fromisoformat("2026-11-20T15:40:00+08:00") == decision_moment(date(2026, 11, 20))


def test_the_page_shows_each_designs_decision_the_board_a_replay_and_the_probe(tmp_path):
    """2026-11-20 (D-050): 研究 › AI 代理人 — read from the lab's files, empty or not."""
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app
    from quant_platform.research.agent_lab import view

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}", scheduler_in_web=False)
    client = create_app(build_container(settings)).test_client()
    empty = client.get("/research/agents")
    body = empty.get_data(as_text=True)
    assert empty.status_code == 200 and "還沒有實際的決定" in body and "<span>AI 代理人</span>" in body
    research = tmp_path / "research"
    runner.append(research, "info", {"date": "2026-11-19", "llm": {"hold": ["2330", "2317"]}})
    runner.append(research, "info", {"date": "2026-11-20", "llm": {
        "hold": ["2330", "2454"], "reasons": {"2454": "營收創高"}, "view": "大盤偏弱", "lesson": "少換股",
        "trace": [{"tool": "card"}, {"tool": "card"}, {"tool": "mood"}], "cost_usd": 0.0088}})
    runner.append(research, "veto", {"date": "2026-11-20", "llm": {"hold": ["1101"], "vetoed": {"3026": "20 日跌 54%"}}})
    row = {"key": "info", "label": variants.BY_KEY["info"].label, "counted": 301000, "sold": 302000, "counted_ahead_of_0050": 1000,
           "sold_ahead_of_0050": -500, "beta": 0.8, "fees_and_tax": 900, "orders": 12, "against": "plain",
           "versus": {"counted": 2000, "sold": 1500}, "verdict": "觀察中（3／60 個交易日）",
           "behaviour": {"decisions": 3, "fallbacks": 0, "average_held": 9.0, "bought_per_decision": 1.5, "average_steps": 5.0,
                         "cost_usd": 0.026}, "placebo": {"percentile": 0.62}}
    result = {"rows": [row, {"key": "team", "label": "分析師團隊", "error": "沒有資料"}], "start": "2026-11-23", "through": "2026-11-25",
              "sessions": 3, "first_decision": "2026-11-20", "benchmark": {"contributed": 300000, "sold": 300500, "value": 301000}}
    evaluate.write(research, result)
    evaluate.write(research, {**result, "run": "neutral-20261120", "replayed": True})
    evaluate.write(research, {**result, "run": "dry-20261120", "replayed": True})          # dry runs are not listed
    probe_folder = research / "agent_lab" / "probe"
    probe_folder.mkdir(parents=True)
    (probe_folder / "gpt-6-luna.json").write_text(json.dumps({"model": "gpt-6-luna", "made_at": "2026-11-20T01:35:00+08:00", "stocks": 40,
        "months": [{"month": "2025-03", "right": 27, "wrong": 13}, {"month": "2025-09", "right": 20, "wrong": 20},
                   {"month": "2026-06", "right": 0, "wrong": 0, "unknown": 40}, {"month": "2026-07", "error": "x"}]}), encoding="utf-8")
    found = view.page(research)
    info = next(item for item in found["designs"] if item["key"] == "info")
    assert [(row["code"], row["new"]) for row in info["holdings"]] == [("2330", False), ("2454", True)]
    assert [row["code"] for row in info["sold"]] == ["2317"] and info["tools"][0] == {"name": "個股資料", "count": 2}
    assert [replay["name"] for replay in found["replays"]] == ["neutral-20261120"]
    assert [(row["period"], row["asked"]) for row in found["probes"][0]["rows"]] == [("2025 上半年", 40), ("2025 下半年", 40)]
    body = client.get("/research/agents").get_data(as_text=True)
    for text in ("營收創高", "大盤偏弱", "個股資料 2 次", "20 日跌 54%", "301,000", "+1,000", "-500", "觀察中（3／60 個交易日）",
                 "算不出來：沒有資料", "neutral-20261120", "2025 上半年", "67.5", "2027-02-25", "不能當成代理人有本事的證據"):
        assert text in body, text
    assert "dry-20261120" not in body
