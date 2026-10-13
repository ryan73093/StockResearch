"""2026-10-13: the owner's hypothesis check (research/hypothesis.py)."""

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from quant_platform.research import hypothesis
from quant_platform.research.costs import CostModel
from quant_platform.research.daily import FactorPanel
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel

TAIPEI = ZoneInfo("Asia/Taipei")


class FakeClient:
    def __init__(self, parsed):
        self.parsed, self.calls = parsed, []

    def json_call(self, instructions, user_input, operation, context=""):
        self.calls.append((operation, context))
        if context.endswith("-explain"):
            return {"explanation": "回測裡贏 0050，但回撤較深；回測不代表未來。"}, {"cost_usd": 0.0001}
        return dict(self.parsed), {"cost_usd": 0.0002}


def test_submit_queue_and_parse_keeps_only_known_factors(tmp_path):
    item = hypothesis.submit(tmp_path, "  股價接近一年新高、RSI 強的股票會繼續漲  ", datetime(2026, 10, 13, 10, 0, tzinfo=TAIPEI))
    assert item["status"] == "queued" and hypothesis.pending(tmp_path)[0]["id"] == item["id"]
    client = FakeClient({"restatement": "接近 52 週高點且 RSI 高", "factors": {"high_52w": 1, "rsi_14": 1, "made_up": 1,
                                                                               "trend_200": 0.5},
                         "universe": "huge", "check": "monthly", "missing": ["分點"], "testable": True})
    parsed, _usage = hypothesis.parse(client, item["text"], item["id"])
    assert parsed["factors"] == {"high_52w": 1.0, "rsi_14": 1.0}                     # unknown and odd weights dropped
    assert parsed["universe"] == "all" and parsed["check"] == "daily" and parsed["missing"] == ["分點"]
    assert "t0_consensus" not in hypothesis.vocabulary() and "rl_tech" not in hypothesis.vocabulary()


def market():
    rng = np.random.default_rng(5)
    days, day = [], date(2014, 1, 1)
    while day <= date(2026, 9, 30):
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    closes = {f"{1101 + n}.TW": dict(zip(days, np.maximum(20 * np.cumprod(1 + rng.normal(0.0002 * (n - 10) / 10, 0.012,
                                                                                         len(days))), 10.5)))
              for n in range(25)}
    closes["0050.TW"] = dict(zip(days, 50 * np.cumprod(1 + rng.normal(0.0003, 0.008, len(days)))))
    return LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 50_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})


def test_a_hypothesis_runs_as_a_registered_trial_with_a_verdict(tmp_path):
    research = tmp_path / "research"
    item = hypothesis.submit(research, "三個月漲最多的股票會繼續漲")
    client = FakeClient({"restatement": "三個月動能", "factors": {"momentum_3": 1}, "universe": "all", "check": "weekly",
                         "missing": [], "testable": True})
    data = market()
    fp = FactorPanel(Panel(data))
    free = CostModel(fee_rate=0.0, minimum_fee=0, slippage_bps=0.0)
    done = hypothesis.run_one(research, tmp_path / "history", item, client, data, fp, "data:x", free)
    assert done["status"] == "done" and done["verdict"] in ("好", "部分好", "不穩定", "不好")
    assert done["result"]["trial"] >= 1 and done["result"]["tier"] in ("T0 候選", "T1", "T2", "T3")
    assert done["explanation"].endswith("回測不代表未來。")
    saved = json.loads((research / "hypotheses" / f"{item['id']}.json").read_text(encoding="utf-8"))
    assert saved["status"] == "done" and saved["parsed"]["factors"] == {"momentum_3": 1.0}
    # the same idea again finds the first as a similar rule
    peers = hypothesis.similar(research, {"momentum_3": 1.0})
    assert peers and peers[0]["overlap"] == 1.0


def test_an_untestable_idea_says_what_is_missing(tmp_path):
    item = hypothesis.submit(tmp_path, "主力分點連續買超的股票會漲")
    client = FakeClient({"restatement": "主力分點買超", "factors": {}, "missing": ["券商分點進出"], "testable": False})
    done = hypothesis.run_one(tmp_path, tmp_path / "history", item, client, None, None, "x", None)
    assert done["verdict"] == "無法測試" and "券商分點進出" in done["reason"]


def test_the_page_takes_an_idea_and_the_worker_waits_outside_the_closing_hours(tmp_path):
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app
    from quant_platform.scheduler.runner import start_hypotheses

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    client = create_app(container).test_client()
    response = client.post("/research/hypotheses", data={"text": "殖利率高又站上年線的股票會贏大盤"})
    assert response.status_code == 302
    body = client.get("/research?tab=hypothesis").get_data(as_text=True)
    assert "AI 假設檢驗" in body and "殖利率高又站上年線的股票會贏大盤" in body and "等待中" in body
    started = []
    closing = datetime(2026, 10, 13, 13, 40, tzinfo=TAIPEI)
    assert start_hypotheses(container, closing, launch=lambda: started.append(1)) is None and not started
    morning = datetime(2026, 10, 13, 10, 0, tzinfo=TAIPEI)
    assert start_hypotheses(container, morning, launch=lambda: started.append(1)) == 1 and started == [1]
