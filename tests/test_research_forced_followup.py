"""2026-10-28 (research_method §7「選股條件的緩衝」): each forward forced sale against the holdings kept that day."""
from datetime import date, timedelta
from types import SimpleNamespace

from quant_platform.research import forced_followup


def test_a_forced_sale_is_followed_against_the_holdings_kept_that_day():
    decisions = {"a@2026-09-01": {
        "days": {"2026-09-01": ["K1", "K2", "N"], "2026-09-02": ["K1", "K2", "N"]},
        "why": {"2026-09-01": {"S": "不再符合選股條件（股價低於門檻、成交值不足或沒有分數）", "N": "新進前 20 名（當天第 3 名）"},
                "2026-09-02": {"T": "跌出前 60 名（當天第 73 名）"}}}}
    found = forced_followup.events(decisions)
    assert found == [{"account": "a@2026-09-01", "day": "2026-09-01", "sold": ["S"], "kept": ["K1", "K2"]}]
    sessions = [date(2026, 9, 1) + timedelta(days=i) for i in range(70)]
    prices = {"S": lambda i: 100 * (0.99 ** i), "K1": lambda i: 100 * (1.01 ** i), "K2": lambda i: 50.0}
    fp = SimpleNamespace(sessions=sessions, index={day: i for i, day in enumerate(sessions)},
                         row={symbol: n for n, symbol in enumerate(prices)},
                         price=lambda symbol, i: prices[symbol](i))
    result = forced_followup.follow(fp, found)
    gap = 0.99 ** 20 - 1 - ((1.01 ** 20 - 1) + 0) / 2
    assert result["sales"] == 1 and abs(result["rows"][0]["gap_20"] - round(gap, 5)) < 1e-9
    assert result["horizons"]["20"]["right"] == 1.0 and result["horizons"]["60"]["sales"] == 1
    short = SimpleNamespace(**{**fp.__dict__, "sessions": sessions[:30]})
    assert forced_followup.follow(short, found)["horizons"]["60"]["sales"] == 0      # not 60 sessions on yet
