"""2026-10-20: the LLM trader, forward only (research/llm_trader.py)."""
import json
from datetime import date, timedelta

import numpy as np

from quant_platform.research import llm_trader


def test_the_twin_keeps_its_holdings_still_in_the_candidates_and_fills_by_rank():
    cands = [{"code": code} for code in ("2330", "2317", "2454", "3008")]
    assert llm_trader.control_choice(cands, ["3008", "1101"]) == ["3008", "2330", "2317", "2454"]
    assert len(llm_trader.control_choice([{"code": str(1000 + i)} for i in range(30)], [])) == llm_trader.HOLD


def test_an_answer_that_breaks_a_hard_rule_is_not_used():
    cands = [{"code": code} for code in ("2330", "2317", "2454")]
    same = lambda code: "半導體"                                          # noqa: E731
    codes, reasons, problem = llm_trader.validate({"hold": [{"code": "2330", "reason": "名次前段"}]}, cands, [], same)
    assert codes == ["2330"] and reasons == ["名次前段"] and problem is None
    assert llm_trader.validate({"hold": [{"code": "9999"}]}, cands, [], same)[2].startswith("挑了候選以外")
    assert llm_trader.validate({"hold": [{"code": "0050"}]}, cands + [{"code": "0050"}], [], same)[2].startswith("挑了 ETF")
    many = [{"code": str(2000 + i)} for i in range(8)]
    assert "同一產業" in llm_trader.validate({"hold": many}, many, [], same)[2]               # 8 > 6 in one industry
    assert llm_trader.validate({"nothing": 1}, cands, [], same)[2].startswith("回覆格式")
    assert llm_trader.validate({"hold": [{"code": "1101"}]}, cands, ["1101"], same)[2] is None  # a holding may stay


def test_a_choice_ranks_its_stocks_from_the_next_session_until_the_next_choice(tmp_path):
    days = [date(2026, 10, 19) + timedelta(days=i) for i in range(5)]
    fp = type("FP", (), {"sessions": days, "symbols": ["2330.TW", "2317.TW", "2454.TW"],
                         "row": {"2330.TW": 0, "2317.TW": 1, "2454.TW": 2}})()
    folder = llm_trader.folder(tmp_path)
    folder.mkdir(parents=True)
    lines = [{"date": "2026-10-20", "llm": {"hold": ["2317", "2330"]}, "control": {"hold": ["2454"]}},
             {"date": "2026-10-22", "llm": {"hold": ["2454"]}, "control": {"hold": ["2454"]}}]
    (folder / "decisions.jsonl").write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    scores = llm_trader.scores(fp, tmp_path, "llm")
    assert np.isnan(scores[:2]).all()                                    # nothing until the session after 10-20
    assert scores[2, 1] > scores[2, 0] and np.isnan(scores[2, 2])         # 2317 first, then 2330
    assert scores[3, 1] > 0                                              # carried to 10-22
    assert np.isfinite(scores[4, 2]) and np.isnan(scores[4, :2]).all()    # 10-22's choice from 10-23
    assert np.isfinite(llm_trader.scores(fp, tmp_path, "control")[2, 2])


def test_a_replayed_day_reads_the_week_of_news_before_it_not_the_latest(tmp_path):
    from datetime import date

    import pandas as pd

    from quant_platform.research import llm_trader

    folder = tmp_path / "news_events"
    folder.mkdir()
    for day in ("20261005", "20261006", "20261020", "20261021"):
        pd.DataFrame([{"stock_id": "2330", "about": True, "direction": 1, "risk": False, "numbers": False,
                       "news_day": day, "title": f"news {day}"}]).to_parquet(folder / f"{day}.parquet")
    lines = llm_trader.news_of(tmp_path, {"2330"}, date(2026, 10, 6))["2330"]
    assert [line.split()[0] for line in lines] == ["20261005", "20261006"]        # not 10-20 or 10-21


def test_the_account_shows_the_reasons_the_model_wrote(tmp_path):
    """2026-10-22: a buy shows the reason in the latest decision before it that holds the stock; a sale the decision
    that dropped it; a trade the model gave no reason for keeps the engine's own words."""
    folder = llm_trader.folder(tmp_path)
    folder.mkdir(parents=True)
    lines = [{"date": "2026-10-19", "llm": {"hold": ["2330", "2317"], "reasons": {"2330": "AI 需求強", "2317": "伺服器"}}},
             {"date": "2026-10-20", "replayed": True, "llm": {"hold": ["2330", "2454"], "reasons": {"2330": "續強", "2454": "新訂單"}}}]
    (folder / "decisions.jsonl").write_text("\n".join(json.dumps(line, ensure_ascii=False) for line in lines), encoding="utf-8")
    days = [{"date": "2026-10-21", "trades_today": [{"code": "2454", "side": "BUY"}, {"code": "2317", "side": "SELL"},
                                                    {"code": "1101", "side": "BUY"}]}]
    found = llm_trader.trade_reasons(tmp_path, days)["2026-10-21"]
    assert found["2454"] == "LLM 2026-10-20（補做）：新訂單"
    assert found["2317"] == "LLM 2026-10-20（補做）的選擇裡沒有它了"
    assert "1101" not in found
