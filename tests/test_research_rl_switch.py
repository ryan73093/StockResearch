"""2026-10-28 (research_method §7「RL 第五版：換股時機」): the switch-or-keep robot walks the weeks with holdings."""
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np

from quant_platform.research import rl_switch


def test_switching_pays_the_change_and_keeping_keeps_the_holdings():
    sessions = [date(2024, 1, 1) + timedelta(days=i) for i in range(30)]
    # stock 0 rises 1% a session, 1 falls 1%, 2 flat
    filled = np.vstack([np.cumprod(np.full(30, 1.01)), np.cumprod(np.full(30, 0.99)), np.ones(30)])
    fp = SimpleNamespace(panel=SimpleNamespace(filled=filled), sessions=sessions)
    found = [{"position": 0, "date": "2024-01-01", "new": [1], "score": {0: 0.5, 1: 1.0, 2: 0.1},
              "taiex_trend": 0.0, "taiex_vol": 0.0, "market_risk": 0.0},
             {"position": 5, "date": "2024-01-06", "new": [0], "score": {0: 1.0, 1: 0.2, 2: 0.1},
              "taiex_trend": 0.0, "taiex_vol": 0.0, "market_risk": 0.0}]
    heard = []
    kept = rl_switch.run_policy(fp, found, lambda index, state: False, learn=heard.append)
    assert kept["switch"].tolist() == [True, False]                       # the first week buys the list
    assert abs(kept["ret"].iloc[1] - (0.99 ** 5 - 1)) < 1e-9 and abs(kept["gain"].iloc[1] - 0.8) < 1e-9
    switched = rl_switch.run_policy(fp, found, lambda index, state: True)
    assert abs(switched["ret"].iloc[1] - (1.01 ** 5 - 1 - rl_switch.COST * 1.0)) < 1e-9
    assert len(heard) == 1 and abs(heard[0] - (0.99 ** 5 - 1)) < 1e-9
