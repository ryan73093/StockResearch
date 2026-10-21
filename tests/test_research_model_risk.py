"""2026-10-16 (使用者：深度學習能不能用到交易裡輔助這些策略？): the drop-risk model."""
from datetime import date, timedelta

import numpy as np

from quant_platform.research import model, model_deep, model_risk
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


def test_the_drop_label_and_auc_by_hand():
    days = weekdays(date(2020, 1, 1), date(2020, 3, 31))
    falling = [100.0] * 10 + [80.0] * (len(days) - 10)                      # −20% on the 11th session
    flat = [50.0] * len(days)
    data = LegacyData(sessions=days, closes={"A.TW": dict(zip(days, falling)), "B.TW": dict(zip(days, flat))},
                      traded_value={s: {d: 1e9 for d in days} for s in ("A.TW", "B.TW")}, factors={}, predictions={})
    fp = FactorPanel(Panel(data))
    eligible = np.ones((2, len(days)), dtype=bool)
    out = model_risk.labels(fp, [0, 9, 10], eligible, 20)
    row = fp.symbols.index("A.TW")
    assert out[0, row] == 1 and out[1, row] == 1 and out[2, row] == 0       # the fall is ahead, then behind
    assert (out[:, fp.symbols.index("B.TW")] == 0).all()
    assert model_risk.auc(np.array([0.9, 0.8, 0.1, 0.2]), np.array([1, 1, 0, 0])) == 1.0
    assert model_risk.auc(np.array([0.5, 0.5]), np.array([1, 0])) == 0.5


def test_the_risk_model_trains_on_the_device_and_scores_probabilities(monkeypatch, tmp_path):
    monkeypatch.setattr(model_deep, "EPOCHS", 1)
    monkeypatch.setattr(model_deep, "SEEDS", (0,))
    monkeypatch.setattr(model_deep, "LENGTH", 10)
    monkeypatch.setattr(model_deep, "FACTORS", ("momentum_3", "reversal_1"))
    monkeypatch.setattr(model, "WARMUP", 60)
    monkeypatch.setattr(model_risk, "DROP", 0.05)
    days = weekdays(date(2016, 1, 1), date(2018, 12, 31))
    rng = np.random.default_rng(4)
    closes = {f"{1101 + n}.TW": dict(zip(days, np.maximum(30 * np.cumprod(1 + rng.normal(0, 0.01 + n * 0.001, len(days))), 11.0)))
              for n in range(30)}
    closes["0050.TW"] = {day: 50.0 for day in days}
    data = LegacyData(sessions=days, closes=closes, traded_value={s: {d: 5e9 for d in v} for s, v in closes.items()},
                      factors={}, predictions={})
    fp = FactorPanel(Panel(data))
    meta = model.train(fp, tmp_path, years=[2018], label="deep_risk", version="dl-risk-1.0.0")
    year = meta["years"]["2018"]
    assert meta["target"] == "drop" and year["auc"] is not None and year["auc_volatility"] is not None
    scores = model.scores(fp, tmp_path)
    first = fp.index[next(day for day in fp.sessions if day.year == 2018)]
    finite = scores[first][np.isfinite(scores[first])]
    assert finite.size > 10 and ((finite >= 0) & (finite <= 1)).all()
    # 2026-10-21 (每月滾動，模型也要更新): refitted to a cutoff, the sessions after it take the refit's probabilities
    cutoff = [day for day in fp.sessions if day.year == 2018][100]
    found = model_risk.train_until(fp, tmp_path, cutoff)
    assert found["scored_days"] > 0 and date.fromisoformat(found["label_until"]) <= cutoff
    after = model.scores(fp, tmp_path)
    past = fp.index[cutoff]
    assert np.array_equal(np.nan_to_num(after[:past + 1]), np.nan_to_num(scores[:past + 1]))
    later = after[past + 1][np.isfinite(after[past + 1])]
    assert later.size > 10 and ((later >= 0) & (later <= 1)).all()
    assert not np.array_equal(np.nan_to_num(after[past + 1:]), np.nan_to_num(scores[past + 1:]))
