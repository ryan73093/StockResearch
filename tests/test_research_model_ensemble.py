"""2026-10-12: gbm-2.0.0, the ensemble aimed at the top of the list (research/model_ensemble.py)."""

import json
from datetime import date, timedelta

import numpy as np

from quant_platform.research import model, model_ensemble
from quant_platform.research.daily import FactorPanel
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel


def planted(stocks=60, seed=7):
    """Every stock keeps its own drift under noise: the trend factors predict."""
    rng = np.random.default_rng(seed)
    days, day = [], date(2013, 1, 1)
    while day <= date(2016, 12, 30):
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    drifts = np.linspace(-0.002, 0.002, stocks)
    closes = {}
    for number, drift in enumerate(drifts):
        series = 20 * np.cumprod(1 + drift + rng.normal(0, 0.01, len(days)))
        closes[f"{1101 + number}.TW"] = dict(zip(days, np.maximum(series, 10.5)))
    closes["0050.TW"] = {day: 100.0 for day in days}
    return LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 50_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})


def test_the_ensemble_learns_the_planted_signal_and_reports_every_kind(tmp_path, monkeypatch):
    monkeypatch.setattr(model, "PARAMS", {**model.PARAMS, "max_iter": 30, "min_samples_leaf": 50})
    fp = FactorPanel(Panel(planted()))
    folder = tmp_path / "gbm-2.0.0"
    meta = model.train(fp, folder, "data:x", [2016], label="ensemble", version="gbm-2.0.0")
    year = meta["years"]["2016"]
    assert meta["label"] == "ensemble" and set(year["kinds"]) == set(model_ensemble.KINDS)
    assert year["ic"] > 0.1 and year["top20_gain"] > 0
    assert year["train_until"] < "2016-01-01"
    assert not {"revenue_yoy", "revenue_yoy_3m", "revenue_accel"} & set(meta["features"])
    score = model.scores(fp, folder)
    last = len(fp.sessions) - 1
    row = np.isfinite(score[last])
    assert row.sum() > 20 and 0 <= np.nanmin(score[last]) and np.nanmax(score[last]) <= 1
    best = fp.symbols[int(np.nanargmax(score[last]))]
    assert int(best.split(".")[0]) - 1101 >= 40                          # one of the steadily rising stocks


def test_the_gate_compares_the_development_years(tmp_path):
    def write(name, gains):
        folder = tmp_path / name
        folder.mkdir()
        years = {str(year): {"top20_gain": gain} for year, gain in zip(range(2015, 2022), gains)}
        (folder / "meta.json").write_text(json.dumps({"years": years}), encoding="utf-8")

    write("gbm-1.4.0", [0.5, 0.010, 0.008, 0.007, 0.008, 0.009, 0.9])
    write("gbm-2.0.0", [0.0, 0.011, 0.009, 0.008, 0.008, 0.009, 0.0])   # 2015 and 2021 do not count
    check = model_ensemble.gate(tmp_path)
    assert check["passed"] and check["years"] == [2016, 2017, 2018, 2019, 2020]
    write_lower = tmp_path / "gbm-2.0.0" / "meta.json"
    write_lower.write_text(json.dumps({"years": {str(y): {"top20_gain": 0.001} for y in range(2016, 2021)}}), encoding="utf-8")
    assert not model_ensemble.gate(tmp_path)["passed"]


def test_the_technical_model_reads_only_the_robot_s_price_and_volume_factors():
    from quant_platform.research import daily
    from quant_platform.research.tech_rl import TECH_FEATURES

    names = model.version_features("gbm-2.1.0")
    assert names == tuple(TECH_FEATURES) and model.VERSIONS["gbm-2.1.0"]["label"] == "ensemble"
    rules = daily.BATCHES["techmodel"]()
    assert len(rules) == 1 and rules[0].factors == {"ml_tech_ens": 1.0}


def test_the_monthly_version_refits_every_month_and_scores_with_the_months_model(tmp_path, monkeypatch):
    """2026-10-20: gbm-2.4.0 (model G refitted every month): monthly/<the last session before the month>.pkl, each
    learning only labels ended before its month; scores() uses the month's model."""
    monkeypatch.setattr(model, "PARAMS", {**model.PARAMS, "max_iter": 20, "min_samples_leaf": 50})
    fitted = []
    real = model_ensemble.fit_year

    def spy(x, y20, rank20, y60, rows20, rows60, params):
        fitted.append(max(rows20))
        return real(x, y20, rank20, y60, rows20, rows60, params)
    monkeypatch.setattr(model_ensemble, "fit_year", spy)
    fp = FactorPanel(Panel(planted()))
    folder = tmp_path / "gbm-2.4.0"
    meta = model.train(fp, folder, "data:x", [2016], label="ensemble", version="gbm-2.4.0")
    kept = sorted(path.stem for path in (folder / model.MONTHLY).glob("*.pkl"))
    assert len(kept) == 12 and not list(folder.glob("*.pkl")) and len(fitted) == 12
    assert kept[0] == "2015-12-31" and all(stamp < "2016-12-31" for stamp in kept)
    assert fitted == sorted(fitted) and fitted[-1] > fitted[0]                     # later months learn more
    samples = model.sample_columns(len(fp.sessions))
    first_june = next(index for index, day in enumerate(fp.sessions) if day.year == 2016 and day.month == 6)
    assert samples[fitted[5]] + model.HORIZON < first_june                          # June learned before June
    assert meta["years"]["2016"]["top20_gain"] is not None
    score = model.scores(fp, folder)
    assert np.isfinite(score[len(fp.sessions) - 1]).sum() > 20 and np.isnan(score[first_june - 200]).all()
