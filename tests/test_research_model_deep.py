"""2026-10-16 (使用者：deep learning 的項目有沒有研究？): the recurrent network over each stock's last sessions."""
from datetime import date, timedelta

import numpy as np

from quant_platform.research import model_deep
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


def test_the_deep_model_learns_year_by_year_and_keeps_every_sessions_scores(monkeypatch, tmp_path):
    monkeypatch.setattr(model_deep, "EPOCHS", 1)
    monkeypatch.setattr(model_deep, "SEEDS", (0,))
    monkeypatch.setattr(model_deep, "LENGTH", 10)
    monkeypatch.setattr(model_deep, "FACTORS", ("momentum_3", "reversal_1"))
    from quant_platform.research import model

    monkeypatch.setattr(model, "WARMUP", 60)
    days = weekdays(date(2016, 1, 1), date(2018, 12, 31))
    rng = np.random.default_rng(3)
    drift = np.linspace(-0.0008, 0.0008, 30)
    closes = {f"{1101 + n}.TW": dict(zip(days, np.maximum(30 * np.cumprod(1 + drift[n] + rng.normal(0, 0.01, len(days))), 11.0)))
              for n in range(30)}
    closes["0050.TW"] = {day: 50.0 for day in days}
    data = LegacyData(sessions=days, closes=closes, traded_value={s: {d: 5e9 for d in v} for s, v in closes.items()},
                      factors={}, predictions={})
    fp = FactorPanel(Panel(data))
    meta = model.train(fp, tmp_path, years=[2018], label="deep", version="dl-1.0.0")
    assert meta["label"] == "deep" and meta["years"]["2018"]["ic"] is not None and (tmp_path / "deep.npz").is_file()
    scores = model.scores(fp, tmp_path)
    first = fp.index[next(day for day in fp.sessions if day.year == 2018)]
    assert np.isnan(scores[first - 1]).all() and np.isfinite(scores[first]).sum() > 10
    # sessions after the kept ones (the forward days): scored by the latest year's networks, the same as kept
    later = weekdays(date(2016, 1, 1), date(2019, 1, 18))
    for symbol, series in closes.items():
        last = series[days[-1]]
        series.update({day: last for day in later[len(days):]})
    longer = LegacyData(sessions=later, closes=closes, traded_value={s: {d: 5e9 for d in v} for s, v in closes.items()},
                        factors={}, predictions={})
    fp2 = FactorPanel(Panel(longer))
    extended = model.scores(fp2, tmp_path)
    assert np.isfinite(extended[len(days):]).sum(axis=1).min() > 10
    kept = np.isfinite(scores[len(days) - 1])
    assert np.allclose(extended[len(days) - 1][kept], scores[len(days) - 1][kept])


def test_the_deep_model_is_a_model_factor_the_robots_did_not_learn(tmp_path):
    from quant_platform.research import daily, rl_timing, scan

    # in the search unless its model was trained and failed its gate (2026-10-17, scan.failed_models): none here
    assert "ml_deep" in daily.MODEL_FACTORS and "ml_deep" in scan.signals(tmp_path)
    assert "ml_deep" not in rl_timing.factor_names(rl_timing.ALL_VERSION)
