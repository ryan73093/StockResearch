"""R15 stage B (2026-10-06): the walk-forward gradient-boosted baseline."""

from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import model
from quant_platform.research.daily import FACTOR_LABELS, MODEL_FACTORS, DailyRule, FactorPanel, daily_rankings
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def planted(seed=7, stocks=60):
    """Every stock keeps its own drift (-0.2%..+0.2% a day) under 1% noise: the trend factors predict."""
    rng = np.random.default_rng(seed)
    days = weekdays(date(2013, 1, 1), date(2016, 12, 30))
    drifts = np.linspace(-0.002, 0.002, stocks)
    closes = {}
    for number, drift in enumerate(drifts):
        price, series = 20.0, {}
        for day in days:
            price *= 1 + drift + rng.normal(0, 0.01)
            series[day] = max(price, 10.5)
        closes[f"{1101 + number}.TW"] = series
    closes["0050.TW"] = {day: 100.0 for day in days}
    data = LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 50_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})
    return data, drifts


def test_walk_forward_models_never_see_the_year_they_score_and_find_the_planted_signal(tmp_path):
    data, drifts = planted()
    fp = FactorPanel(Panel(data))
    samples = model.sample_columns(len(fp.sessions))
    for year in (2015, 2016):
        first = next(index for index, day in enumerate(fp.sessions) if day.year == year)
        assert all(column + model.HORIZON < first for column in model.training_columns(fp, samples, year))
    meta = model.train(fp, tmp_path, "data:x", [2015, 2016])
    assert set(meta["years"]) == {"2015", "2016"} and meta["features"] == list(FACTOR_LABELS)
    assert meta["years"]["2015"]["train_until"] < "2015-01-01"
    assert all(meta["years"][year]["ic"] > 0.1 and meta["years"][year]["ic_positive"] > 0.9 for year in ("2015", "2016"))
    assert all(meta["years"][year]["top_fifth_rank_gap"] > 0.05 for year in ("2015", "2016"))


def test_scores_use_each_years_model_and_become_a_rule_factor(tmp_path):
    data, drifts = planted()
    fp = FactorPanel(Panel(data))
    folder = tmp_path / model.MODEL_VERSION                   # the factor reads <models>/<its version>
    model.train(fp, folder, "data:x", [2015, 2016])
    scores = model.scores(fp, folder)
    first_2015 = next(index for index, day in enumerate(fp.sessions) if day.year == 2015)
    assert np.isnan(scores[:first_2015]).all() and np.isfinite(scores[first_2015:]).mean() > 0.9
    # the 2016 sessions are scored by the 2016 model: the same as predicting directly
    import pickle

    column = len(fp.sessions) - 1
    direct = pickle.loads((folder / "2016.pkl").read_bytes())
    block = model.feature_block(fp, [column], model.eligibility(fp))[0]
    assert scores[column] == pytest.approx(direct.predict(block).astype(np.float32), rel=1e-5)
    # a rule on the scores holds the stocks drifting up most
    rule = DailyRule(name="x", factors={"ml_gbm": 1.0}, top=10)
    fp_model = FactorPanel(Panel(data), models=tmp_path)
    held = daily_rankings(fp_model, rule, fp.sessions[-5], fp.sessions[-1])[fp.sessions[-1]]
    assert np.mean([drifts[int(symbol[:4]) - 1101] for symbol in held]) > 0.001
    assert "ml_gbm" in MODEL_FACTORS and "ml_gbm" not in FACTOR_LABELS
    assert FactorPanel(Panel(data)).matrix("ml_gbm").shape == (len(fp.symbols), len(fp.sessions))


def test_the_model_digest_follows_the_files(tmp_path):
    data, _drifts = planted(stocks=30)
    fp = FactorPanel(Panel(data))
    model.train(fp, tmp_path, "data:x", [2016])
    before = model.digest(tmp_path)
    assert before == model.digest(tmp_path) and before != model.digest(tmp_path / "missing")
    (tmp_path / "2016.pkl").write_bytes((tmp_path / "2016.pkl").read_bytes() + b"x")
    assert model.digest(tmp_path) != before


def test_excess_label_by_hand_and_the_second_model():
    from quant_platform.research.daily import MODEL_FACTORS

    data, drifts = planted()
    fp = FactorPanel(Panel(data))
    eligible = model.eligibility(fp)
    column = 300
    excess = model.label_block(fp, [column], eligible, "excess")[0]
    returns = fp.panel.filled[:, column + model.HORIZON] / fp.panel.filled[:, column] - 1
    returns = np.where(eligible[:, column], returns, np.nan)
    low, high = np.nanpercentile(returns, 1), np.nanpercentile(returns, 99)
    assert excess == pytest.approx(np.clip(returns, low, high) - np.nanmedian(returns), rel=1e-5, nan_ok=True)
    assert np.nanmedian(excess) == pytest.approx(0, abs=1e-6)
    assert set(MODEL_FACTORS) == set(model.MODELS) and model.VERSIONS[model.LATEST]["label"] == "excess"


def test_the_excess_return_model_finds_the_planted_signal(tmp_path):
    data, drifts = planted()
    fp = FactorPanel(Panel(data))
    meta = model.train(fp, tmp_path / "gbm-1.1.0", "data:x", [2015, 2016], label="excess", version="gbm-1.1.0")
    assert meta["label"] == "excess" and meta["version"] == "gbm-1.1.0"
    for year in ("2015", "2016"):
        assert meta["years"][year]["ic"] > 0.1 and meta["years"][year]["top20_gain"] > 0.005
    rule = DailyRule(name="x", factors={"ml_gbm_excess": 1.0}, top=10)
    fp_model = FactorPanel(Panel(data), models=tmp_path)
    held = daily_rankings(fp_model, rule, fp.sessions[-5], fp.sessions[-1])[fp.sessions[-1]]
    assert np.mean([drifts[int(symbol[:4]) - 1101] for symbol in held]) > 0.001


def test_a_year_added_later_keeps_the_other_years(tmp_path):
    data, _drifts = planted(stocks=30)
    fp = FactorPanel(Panel(data))
    model.train(fp, tmp_path, "data:x", [2015])
    first = (tmp_path / "2015.pkl").read_bytes()
    meta = model.train(fp, tmp_path, "data:y", [2016])
    assert set(meta["years"]) == {"2015", "2016"} and (tmp_path / "2015.pkl").read_bytes() == first
