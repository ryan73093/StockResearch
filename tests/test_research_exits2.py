"""2026-10-17 (docs/research_dl_exits_review.md E2): the weekly optimal-stopping exit (research/exits2.py)."""
from datetime import date, timedelta

import numpy as np

from quant_platform.research import exits2
from quant_platform.research.daily import FactorPanel
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


class Fixed:
    def __init__(self, values):
        self.values = np.asarray(values, dtype=float)

    def predict(self, _x):
        return self.values


def test_a_swap_needs_the_gap_beyond_the_cost_and_the_models_spread(monkeypatch):
    weeks = exits2.Weeks.__new__(exits2.Weeks)
    weeks.x = np.zeros((5, len(exits2.FEATURES)), dtype=np.float32)
    weeks.week = np.zeros(5, dtype=int)
    weeks.replacement = np.array([4])                    # sample 4 is the week's replacement
    test = np.ones(5, dtype=bool)
    # keep values: 0.00 (clearly worse than the replacement's 0.05 less the 0.6% cost), 0.045 (inside the cost),
    # 0.02 and 0.03 (worse, but the second model disagrees on sample 3)
    first = Fixed([0.00, 0.045, 0.02, 0.03, 0.05])
    second = Fixed([0.00, 0.045, 0.02, 0.06, 0.05])
    swapped = exits2.decide(weeks, [first, second], test)
    assert swapped.tolist() == [True, False, True, False, False]
    monkeypatch.setattr(exits2, "MAX_SWAPS", 1)
    assert exits2.decide(weeks, [first, second], test).tolist() == [True, False, False, False, False]   # largest first


def test_the_gate_by_hand():
    good = {str(year): {"swapped_gap": -0.02, "random_gap": -0.001, "swaps_per_week": 1.5} for year in range(2016, 2021)}
    assert exits2.gate_check(good)["passed"]
    small = {key: {**value, "swapped_gap": -0.005} for key, value in good.items()}
    assert not exits2.gate_check(small)["passed"]                  # not two round trips
    churn = {key: {**value, "swaps_per_week": 4.0} for key, value in good.items()}
    assert not exits2.gate_check(churn)["passed"]
    lucky = {**good, "2016": {**good["2016"], "random_gap": -0.03}, "2017": {**good["2017"], "random_gap": -0.03}}
    assert not exits2.gate_check(lucky)["passed"]                  # random swaps did as well in two years


def test_it_trains_walk_forward_and_reports_the_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(exits2, "ITERATIONS", 2)
    monkeypatch.setattr(exits2, "BOOTSTRAPS", 2)
    monkeypatch.setattr(exits2, "ZONE", 20)
    monkeypatch.setattr(exits2, "TOP", 8)
    monkeypatch.setattr(exits2, "GATE_YEARS", (2019,))
    monkeypatch.setattr(exits2, "MIN_TRAIN", 100)
    from quant_platform.research import daily

    monkeypatch.setattr(daily, "RECENT_START", date(2016, 1, 1))
    days = weekdays(date(2015, 1, 1), date(2019, 12, 31))
    rng = np.random.default_rng(9)
    closes = {f"{1101 + n}.TW": dict(zip(days, np.maximum(30 * np.cumprod(1 + rng.normal(0.0003, 0.015, len(days))), 11.0)))
              for n in range(40)}
    closes["0050.TW"] = {day: 50.0 for day in days}
    data = LegacyData(sessions=days, closes=closes, traded_value={s: {d: 5e9 for d in v} for s, v in closes.items()},
                      factors={}, predictions={})
    fp = FactorPanel(Panel(data))
    meta = exits2.train(fp, tmp_path, factor="momentum_3", years=[2019])
    assert meta["version"] == exits2.VERSION and "passed" in meta["gate"]
    item = meta["years"]["2019"]
    assert item["swaps_per_week"] <= 3 and item["test_samples"] > 0 and item["train_samples"] > 0
    assert (tmp_path / "2019.pkl").is_file() and len(__import__("pickle").loads((tmp_path / "2019.pkl").read_bytes())) == 2
