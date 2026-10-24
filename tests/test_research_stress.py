"""2026-10-24 (使用者：測這些策略在每一次大空頭撐不撐得過，2008 年的金融海嘯撐得過嗎): research/stress.py."""
from datetime import date, timedelta

import numpy as np

from quant_platform.research import stress


def test_a_bear_is_a_fall_of_a_fifth_and_ends_back_at_its_high():
    units = np.array([1.0, 1.2, 1.1, 0.9, 0.95, 1.25, 1.3, 1.2, 1.05, 1.0])
    found = stress.bears([date(2008, 1, 1) + timedelta(days=i) for i in range(10)], units, depth=0.2)
    assert len(found) == 2
    assert found[0]["high"] == 1 and found[0]["low"] == 3 and found[0]["back"] == 5
    assert abs(found[0]["fall"] - (0.9 / 1.2 - 1)) < 1e-9
    assert found[1]["high"] == 6 and found[1]["back"] is None                  # 1.3 → 1.0 and never back


def test_a_strategy_is_judged_over_0050s_fall_and_back_from_its_own_low():
    days = [date(2007, 10, 1) + timedelta(days=i) for i in range(8)]
    base = np.array([1.0, 0.9, 0.7, 0.6, 0.8, 1.0, 1.1, 1.2])
    mine = np.array([1.0, 1.05, 0.6, 0.5, 0.7, 0.9, 1.0, 1.1])         # higher the day after the high: not "back"
    falls = stress.bears(days, base, depth=0.2)
    row = stress.judge(days, mine, base, falls)[0]
    assert row["fall"] == -0.5 and row["base_fall"] == -0.4 and abs(row["gap"] + 0.1) < 1e-9
    assert row["back"] == days[6].isoformat() and row["base_back"] == days[5].isoformat()
    held = stress.verdict([row])
    assert held["held_2008"] is False                      # back a day after 0050 (and exactly 10 points worse)
    row["back"], row["days"] = row["base_back"], row["base_days"]
    assert stress.verdict([row])["held_2008"] is True and stress.verdict([row])["worse_bears"] == 0
