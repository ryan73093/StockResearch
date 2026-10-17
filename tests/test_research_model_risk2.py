"""2026-10-17 (docs/research_dl_exits_review.md R3): the vol-neutral drop-risk model and its veto."""
from datetime import date, timedelta

import numpy as np

from quant_platform.research import model, model_deep, model_risk2
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


def test_the_label_is_measured_against_the_stocks_own_swing(monkeypatch):
    days = weekdays(date(2020, 1, 1), date(2020, 6, 30))
    calm = [100.0 * (1.001 if index % 2 else 0.999) for index in range(len(days))]          # ±0.1% a day
    calm[100] = calm[99] * 0.94                                                              # then −6% at once
    wild = [100.0 * (1.03 if index % 2 else 0.97) for index in range(len(days))]           # ±3% a day, −6% is usual
    data = LegacyData(sessions=days, closes={"A.TW": dict(zip(days, calm)), "B.TW": dict(zip(days, wild))},
                      traded_value={s: {d: 1e9 for d in days} for s in ("A.TW", "B.TW")}, factors={}, predictions={})
    fp = FactorPanel(Panel(data))
    eligible = np.ones((2, len(days)), dtype=bool)
    out = model_risk2.labels(fp, [90], eligible, 20)
    assert out[0, fp.symbols.index("A.TW")] == 1          # 6% is many times a calm stock's 20-session swing
    assert out[0, fp.symbols.index("B.TW")] == 0          # and less than twice a wild one's


def test_groups_and_the_top_of_each_group_by_hand():
    volatility = np.arange(100, dtype=float)
    momentum = np.tile(np.arange(10, dtype=float), 10)
    known = np.ones(100, dtype=bool)
    group = model_risk2.groups(volatility, momentum, known)
    assert group.min() == 0 and group.max() == 49 and (group[:10] // 5 == 0).all() and (group[90:] // 5 == 9).all()
    score = np.zeros(100)
    score[[0, 10, 20]] = 1.0                               # one stock riskier than the rest in three groups
    vetoed = model_risk2.top_of_groups(score, np.repeat(np.arange(10), 10))
    assert set(np.flatnonzero(vetoed)) == {0, 10, 20}
    assert not model_risk2.top_of_groups(score, np.array([0] * 4 + [-1] * 96)).any()   # a group under 5 stocks


def test_the_gate_by_hand():
    good = {str(year): {"auc_within_volatility": 0.58, "vetoed_excess": -0.004} for year in range(2016, 2021)}
    assert model_risk2.gate_check(good)["passed"]
    weak = {**good, "2016": {"auc_within_volatility": 0.40, "vetoed_excess": -0.004}}
    assert not model_risk2.gate_check(weak)["passed"]       # the average AUC under 0.55
    winners = {**good, "2016": {"auc_within_volatility": 0.58, "vetoed_excess": 0.01},
               "2017": {"auc_within_volatility": 0.58, "vetoed_excess": 0.02}}
    assert not model_risk2.gate_check(winners)["passed"]    # it vetoed winners in two of the five years


def test_it_trains_on_the_device_and_keeps_its_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(model_deep, "EPOCHS", 1)
    monkeypatch.setattr(model_deep, "SEEDS", (0,))
    monkeypatch.setattr(model_deep, "LENGTH", 10)
    monkeypatch.setattr(model_deep, "FACTORS", ("momentum_3", "reversal_1"))
    monkeypatch.setattr(model, "WARMUP", 60)
    monkeypatch.setattr(model_risk2, "GATE_YEARS", (2018,))
    monkeypatch.setattr(model_risk2, "AUC_ROWS", 5)
    days = weekdays(date(2016, 1, 1), date(2018, 12, 31))
    rng = np.random.default_rng(5)
    closes = {f"{1101 + n}.TW": dict(zip(days, np.maximum(30 * np.cumprod(1 + rng.normal(0, 0.01 + n * 0.0005, len(days))), 11.0)))
              for n in range(60)}
    closes["0050.TW"] = {day: 50.0 for day in days}
    data = LegacyData(sessions=days, closes=closes, traded_value={s: {d: 5e9 for d in v} for s, v in closes.items()},
                      factors={}, predictions={})
    fp = FactorPanel(Panel(data))
    meta = model.train(fp, tmp_path / "dl-risk-2.0.0", years=[2018], label="deep_risk2", version="dl-risk-2.0.0")
    assert meta["version"] == "dl-risk-2.0.0" and meta["label"] == "deep_risk2" and meta["target"] == "drop_vol"
    assert meta["years"]["2018"]["auc_within_volatility"] is not None and "passed" in meta["gate"]
    assert model_risk2.gate(tmp_path) == meta["gate"]
    scores = model.scores(fp, tmp_path / "dl-risk-2.0.0")
    first = fp.index[next(day for day in fp.sessions if day.year == 2018)]
    finite = scores[first][np.isfinite(scores[first])]
    assert finite.size > 10 and ((finite >= 0) & (finite <= 1)).all()
