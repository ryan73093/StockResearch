"""2026-10-17 (使用者：深度學習繼續優化): the second deep version (research/model_deep2.py)."""
import json
from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import model, model_deep2
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


def test_the_inputs_are_the_days_z_scores_and_the_gate_by_hand():
    values = np.array([[1.0, 5.0], [2.0, np.nan], [3.0, 7.0], [100.0, 9.0]])      # symbols × sessions
    eligible = np.array([[True, True], [True, True], [True, True], [False, True]])
    z = model_deep2._zscore(values, eligible)                                      # sessions × symbols
    assert z[0, :3] == pytest.approx([-1.2247449, 0.0, 1.2247449])                 # mean 2, sd √(2/3); 100 not eligible
    assert z[0, 3] == 0.0 and z[1, 1] == 0.0                                       # not eligible, unknown
    assert model_deep2._gain(np.arange(60.0), np.arange(60.0)) == pytest.approx(49.5 - 29.5)
    assert model_deep2._gain(np.arange(10.0), np.arange(10.0)) is None             # under 50 stocks

    def meta(version, gains):
        return {"version": version, "years": {str(year): {"top20_gain": gain} for year, gain in zip(range(2016, 2021), gains)}}

    gbm = meta("gbm-2.2.0", [0.012] * 5)
    assert model_deep2.gate_check(meta("dl-2.0.0", [0.02, 0.015, 0.01, 0.007, 0.01]), gbm)["passed"]
    low = model_deep2.gate_check(meta("dl-2.0.0", [0.011] * 5), gbm)                # below gbm-2.2.0's average
    assert not low["passed"] and low["years_above_floor"] == 5
    few = model_deep2.gate_check(meta("dl-2.0.0", [0.05, 0.03, 0.002, 0.001, 0.0]), gbm)   # only two years ≥ 0.6%
    assert not few["passed"] and few["top20_gain"] > 0.012
    assert not model_deep2.gate_check({"years": {"2016": {"top20_gain": 0.5}}}, gbm)["passed"]   # years missing


def test_the_losses_aim_at_the_top():
    import torch

    target = torch.tensor([0.30, 0.02, 0.0, -0.01, -0.05, 0.01, 0.0, 0.02, -0.02, 0.03])
    groups = [(0, 10)]
    right = model_deep2._loss("listnet", target * 10, target, groups)
    wrong = model_deep2._loss("listnet", -target * 10, target, groups)
    assert right < wrong
    # the top tenth: only the +30% stock is a positive; ranking it first costs less
    first = torch.tensor([3.0] + [-3.0] * 9)
    last = torch.tensor([-3.0] * 9 + [3.0])
    assert model_deep2._loss("top10", first, target, groups) < model_deep2._loss("top10", last, target, groups)


def _panel():
    days = weekdays(date(2015, 1, 1), date(2018, 12, 31))
    rng = np.random.default_rng(7)
    closes = {f"{1101 + n}.TW": dict(zip(days, np.maximum(30 * np.cumprod(1 + rng.normal(0.0002 * n, 0.012, len(days))), 11.0)))
              for n in range(60)}
    closes["0050.TW"] = {day: 50.0 for day in days}
    data = LegacyData(sessions=days, closes=closes, traded_value={s: {d: 5e9 for d in v} for s, v in closes.items()},
                      factors={}, predictions={})
    return FactorPanel(Panel(data))


def test_the_development_years_first_and_the_rest_only_past_the_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(model_deep2, "EPOCHS", 1)
    monkeypatch.setattr(model_deep2, "SEEDS", (0, 1))
    monkeypatch.setattr(model_deep2, "LENGTH", 10)
    monkeypatch.setattr(model_deep2, "GATE_YEARS", (2017,))
    snapshot = tmp_path / "gbm-2.2.0"
    snapshot.mkdir()
    # the yardstick: its features are the snapshot; its 2017 top-20 gain is out of reach, so the gate stops 2018
    (snapshot / "meta.json").write_text(json.dumps({"version": "gbm-2.2.0", "features": ["momentum_3", "reversal_1"],
                                                    "years": {"2017": {"top20_gain": 9.0}}}), encoding="utf-8")
    fp = _panel()
    meta = model.train(fp, tmp_path / "dl-2.0.0", years=[2017, 2018], label="deep2", version="dl-2.0.0")
    assert meta["label"] == "deep2" and meta["loss"] == "listnet" and meta["features"] == ["momentum_3", "reversal_1"]
    year = meta["years"]["2017"]
    assert year["top20_gain"] is not None and year["ic"] is not None and len(year["held_curves"]) == 2
    assert "2018" not in meta["years"] and not meta["gate"]["passed"] and "沒過門檻" in meta["stopped"]
    assert sorted(path.name for path in (tmp_path / "dl-2.0.0").glob("*.pt")) == ["2017-0.pt", "2017-1.pt"]
    scores = model.scores(fp, tmp_path / "dl-2.0.0")
    in_2017 = [position for position, day in enumerate(fp.sessions) if day.year == 2017]
    later = [position for position, day in enumerate(fp.sessions) if day.year == 2018]
    first = scores[in_2017[0]][np.isfinite(scores[in_2017[0]])]
    assert first.size >= 50 and ((first > 0) & (first <= 1)).all()                 # the seeds' ranks averaged
    assert np.isfinite(scores[later[-1]]).sum() >= 50                               # forward days: the 2017 networks
    # past the gate the other years are trained
    (snapshot / "meta.json").write_text(json.dumps({"version": "gbm-2.2.0", "features": ["momentum_3", "reversal_1"],
                                                    "years": {"2017": {"top20_gain": -9.0}}}), encoding="utf-8")
    monkeypatch.setattr(model_deep2, "GATE_FLOOR", -9.0)
    monkeypatch.setattr(model_deep2, "GATE_COUNT", 1)
    meta = model.train(fp, tmp_path / "dl-2.1.0", years=[2017, 2018], label="deep2", version="dl-2.1.0")
    assert meta["loss"] == "top10" and meta["gate"]["passed"] and "2018" in meta["years"] and "stopped" not in meta
    # stage 0 (free): the two models side by side, written next to the model
    result = model_deep2.diagnose(fp, tmp_path, "dl-2.1.0", against="dl-2.0.0")
    row = result["years"]["2017"]
    assert -1 <= row["correlation"] <= 1 and 0 <= row["overlap"] <= 1 and 0 <= row["turnover_mine"] <= 1
    assert row["gain_blend"] is not None and result["development"]["overlap"] == row["overlap"]
    assert (tmp_path / "dl-2.1.0" / "diagnostics.json").is_file()


def test_the_cost_aware_rules_wait_for_the_gate(monkeypatch):
    from quant_platform.research import daily

    monkeypatch.setattr(model_deep2, "gate", lambda root, version: {"passed": False})
    assert daily.deep2_cost_batch() == []
    monkeypatch.setattr(model_deep2, "gate", lambda root, version: {"passed": True})
    rules = daily.deep2_cost_batch()
    assert [rule.factors for rule in rules] == [{"ml_deep2_top": 1.0}, {"ml_gbm_ext": 1.0},
                                                {"ml_deep2_top": 0.5, "ml_gbm_ext": 0.5}]
    assert all(rule.switching == "cost1" and rule.hold == "days" and rule.min_hold == 0 for rule in rules)
