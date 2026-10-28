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


def test_the_direct_loss_pays_for_turnover_and_crowding_by_hand(tmp_path):
    """2026-10-28 (dl-4.0.0, RL 第九版): equal scores hold each day's stocks equally; the loss is minus the excess over
    four weeks, plus the cost of the turnover to the next week's weights and the penalty for holding under 20 stocks."""
    import torch

    target = torch.tensor([0.04, 0.0, -0.01, 0.02, 0.01, 0.0])
    groups = [(0, 3), (3, 6)]                                  # a day and the day a week later
    same = np.array([0, 1, 2, 0, 1, 2])
    flat = torch.zeros(6)
    gains = (0.01 + 0.01) / 2                                  # each day's average
    crowd = 1 / 3 - 1 / model_deep2.DIRECT_SPREAD              # three stocks' worth
    expected = 100 * (-(gains / model_deep2.DIRECT_WEEKS) + crowd)
    assert float(model_deep2._loss("direct", flat, target, groups, same)) == pytest.approx(expected, rel=1e-5)
    moved = np.array([0, 1, 2, 3, 4, 5])                       # all new stocks a week later: turnover 1
    assert float(model_deep2._loss("direct", flat, target, groups, moved)) == pytest.approx(
        expected + 100 * model_deep2.DIRECT_COST, rel=1e-5)
    leaning = torch.tensor([2.0, 0.0, -2.0, 2.0, 0.0, -2.0])   # toward the better stock on both days
    assert model_deep2._loss("direct", leaning, target, groups, same) < model_deep2._loss("direct", -leaning, target, groups, same)

    # the gate: the top-20 gain less 0.5% × 4 × the weekly turnover, against dl-2.1.0's; three of five years better
    years = (2021, 2022, 2023, 2024, 2025)
    meta = {"version": "dl-4.0.0", "years": {str(year): {"top20_gain": 0.012} for year in years}}
    other = {"version": "dl-2.1.0", "years": {str(year): {"top20_gain": 0.015} for year in years}}
    low = {year: 0.2 for year in years}                        # 0.012 - 0.004 = 0.008
    high = {year: 0.5 for year in years}                       # 0.015 - 0.010 = 0.005
    check = model_deep2.gate_check_direct(meta, other, low, high, years)
    assert check["passed"] and check["net_gain"] == pytest.approx(0.008) and check["against_net_gain"] == pytest.approx(0.005)
    failed = model_deep2.gate_check_direct(meta, other, high, low, years)
    assert failed["passed"] is False and json.dumps(failed)        # 10-29: a numpy bool did not go into meta.json
    assert not model_deep2.gate_check_direct(meta, other, {2021: 0.2}, high, years)["passed"]   # years missing

    # the turnover: the top 20 of 60 stocks moving by five places a week
    days = [day.isoformat() for day in weekdays(date(2021, 1, 4), date(2021, 3, 31))]
    scores = np.stack([np.roll(np.arange(60.0), number) for number in range(len(days))]).astype(np.float16)
    with open(tmp_path / "deep.npz", "wb") as handle:
        np.savez_compressed(handle, days=np.array(days), symbols=np.array([str(n) for n in range(60)]), scores=scores)
    turnover = model_deep2.turnover_by_year(tmp_path, (2021,))
    assert turnover[2021] == pytest.approx(5 / 20)
    assert model_deep2.LOSSES["dl-4.0.0"] == "direct" and model_deep2.gate_years("dl-4.0.0") == years


def test_the_direct_version_trains_on_pairs_a_week_apart(monkeypatch, tmp_path):
    monkeypatch.setattr(model_deep2, "EPOCHS", 1)
    monkeypatch.setattr(model_deep2, "SEEDS", (0,))
    monkeypatch.setattr(model_deep2, "LENGTH", 10)
    monkeypatch.setattr(model_deep2, "RECENT_GATE_YEARS", (2017,))
    snapshot = tmp_path / "gbm-2.2.0"
    snapshot.mkdir()
    (snapshot / "meta.json").write_text(json.dumps({"version": "gbm-2.2.0", "features": ["momentum_3"]}), encoding="utf-8")
    _data, fp = _panel()
    meta = model.train(fp, tmp_path / "dl-4.0.0", years=[2017], label="deep2", version="dl-4.0.0")
    assert meta["loss"] == "direct" and meta["years"]["2017"]["top20_gain"] is not None


def _panel():
    days = weekdays(date(2015, 1, 1), date(2018, 12, 31))
    rng = np.random.default_rng(7)
    closes = {f"{1101 + n}.TW": dict(zip(days, np.maximum(30 * np.cumprod(1 + rng.normal(0.0002 * n, 0.012, len(days))), 11.0)))
              for n in range(60)}
    closes["0050.TW"] = {day: 50.0 for day in days}
    data = LegacyData(sessions=days, closes=closes, traded_value={s: {d: 5e9 for d in v} for s, v in closes.items()},
                      factors={}, predictions={})
    return data, FactorPanel(Panel(data))


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
    data, fp = _panel()
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
    # the truncation check: the kept 2018 scores come back the same from data cut at the day
    from quant_platform.research import leakcheck

    monkeypatch.setattr(leakcheck, "truncated_panel",
                        lambda history, full, until: (None, FactorPanel(Panel(leakcheck.truncated_data(full, until)))))
    days = [day for day in fp.sessions if day.year == 2018][100:102]
    report = model_deep2.leak_check(tmp_path, data, tmp_path, "dl-2.1.0", days)
    assert not report["leak"] and all(item["checked"] >= 50 for item in report["results"].values())


def test_the_cost_aware_rules_wait_for_the_gate(monkeypatch):
    from quant_platform.research import daily

    monkeypatch.setattr(model_deep2, "gate", lambda root, version: {"passed": False})
    assert daily.deep2_cost_batch() == []
    monkeypatch.setattr(model_deep2, "gate", lambda root, version: {"passed": True})
    rules = daily.deep2_cost_batch()
    assert [rule.factors for rule in rules] == [{"ml_deep2_top": 1.0}, {"ml_gbm_ext": 1.0},
                                                {"ml_deep2_top": 0.5, "ml_gbm_ext": 0.5}]
    assert all(rule.switching == "cost1" and rule.hold == "days" and rule.min_hold == 0 for rule in rules)


def test_the_risk_weights_by_hand(monkeypatch):
    """2026-10-17 (R1): each pick's money in proportion to 1 / its drop probability, floored at RISK_FLOOR."""
    from quant_platform.research import daily

    _data, fp = _panel()
    day = fp.sessions[300]
    names = fp.symbols[:4]
    risk = np.full((len(fp.symbols), len(fp.sessions)), np.nan)
    for row, chance in zip(range(4), (0.1, 0.2, 0.4, 0.01)):
        risk[row, fp.index[day]] = chance
    real = fp.matrix
    monkeypatch.setattr(fp, "matrix", lambda name: risk if name == "dl_risk" else real(name))
    rule = daily.DailyRule(name="r", factors={"momentum_3": 1.0}, weighting="inverse_risk")
    weights = daily.daily_weights(fp, rule, {day: list(names)})[day]
    inverse = np.array([10, 5, 2.5, 20])                     # 0.01 counts as the floor, 0.05
    assert [round(weights[name], 6) for name in names] == [round(value, 6) for value in inverse / inverse.sum()]
    monkeypatch.setattr(model_deep2, "gate", lambda root, version: {"passed": True})
    rules = daily.deep2_risk_batch()
    assert len(rules) == 3 and all(rule.weighting == "inverse_risk" and rule.switching == "cost1" for rule in rules)
    assert all(rule.name.endswith("依預測下跌機率配置") for rule in rules)


def test_the_large_cap_rules_rank_only_the_largest(monkeypatch):
    from quant_platform.research import daily

    monkeypatch.setattr(model_deep2, "gate", lambda root, version: {"passed": True})
    rules = daily.deep2_large_batch()
    assert len(rules) == 3 and all(rule.large_caps == 100 and rule.switching == "cost1" for rule in rules)
    assert all("市值前 100 大挑 20 名" in rule.name for rule in rules)


def test_the_core_satellite_account_is_fixed_before_the_run():
    """2026-10-17: the T0 consensus (cost-aware) 63% + the deep rule 37%, shares by risk parity on the development
    period, fixed in the code; 100% stocks."""
    from quant_platform.research import daily
    from quant_platform.research.categories import uses_0050

    blend = daily.core_satellite_batch()[0]
    shares = [sleeve.share for sleeve in blend.sleeves]
    assert shares == [daily.CORE_SHARE, 1 - daily.CORE_SHARE] and blend.core == 0.0 and len(blend.name) <= 80
    assert [sleeve.rule.factors for sleeve in blend.sleeves] == [{"t0_consensus": 1.0}, {"ml_deep2_top": 1.0}]
    assert not any(uses_0050(sleeve.rule.canonical()) for sleeve in blend.sleeves)


def test_the_attention_version_sees_the_day_and_its_own_gate(monkeypatch, tmp_path):
    """2026-10-17 (D3): dl-3.0.0's stocks of one day attend to each other; scoring keeps days whole; its gate is
    against dl-2.1.0 (0.2 points more on average, better in 4 of 5 years)."""
    import torch

    network = model_deep2._network(3, 2, "attention")
    network.eval()
    steps, now, market = torch.randn(6, 5, 3), torch.randn(6, 2), torch.zeros(6, 3)
    together = network(steps, now, market, [(0, 6)])
    apart = network(steps, now, market, [(0, 3), (3, 6)])
    assert together.shape == (6,) and not torch.allclose(together, apart)    # the day's others change a score
    assert model_deep2._runs(np.array([5, 5, 6, 6, 6, 9])) == [(0, 2), (2, 5), (5, 6)]

    def meta(version, gains):
        return {"version": version, "years": {str(year): {"top20_gain": gain} for year, gain in zip(range(2016, 2021), gains)}}

    d1 = meta("dl-2.1.0", [0.010, 0.017, -0.001, 0.016, 0.033])
    assert model_deep2.gate_check_d3(meta("dl-3.0.0", [0.013, 0.020, 0.002, 0.019, 0.032]), d1)["passed"]
    assert not model_deep2.gate_check_d3(meta("dl-3.0.0", [0.011, 0.018, 0.0, 0.017, 0.034]), d1)["passed"]  # +0.1
    assert not model_deep2.gate_check_d3(meta("dl-3.0.0", [0.03, 0.03, -0.01, 0.01, 0.02]), d1)["passed"]   # 2 of 5
    (tmp_path / "dl-2.1.0").mkdir()
    (tmp_path / "dl-2.1.0" / "meta.json").write_text(__import__("json").dumps(d1), encoding="utf-8")
    (tmp_path / "dl-3.0.0").mkdir()
    (tmp_path / "dl-3.0.0" / "meta.json").write_text(
        __import__("json").dumps(meta("dl-3.0.0", [0.013, 0.020, 0.002, 0.019, 0.032])), encoding="utf-8")
    assert model_deep2.gate(tmp_path, "dl-3.0.0")["against"] == "dl-2.1.0"


def test_the_attention_version_trains(monkeypatch, tmp_path):
    monkeypatch.setattr(model_deep2, "EPOCHS", 1)
    monkeypatch.setattr(model_deep2, "SEEDS", (0,))
    monkeypatch.setattr(model_deep2, "LENGTH", 10)
    monkeypatch.setattr(model_deep2, "GATE_YEARS", (2017,))
    snapshot = tmp_path / "gbm-2.2.0"
    snapshot.mkdir()
    (snapshot / "meta.json").write_text(__import__("json").dumps({"version": "gbm-2.2.0", "features": ["momentum_3"]}),
                                        encoding="utf-8")
    _data, fp = _panel()
    meta = model.train(fp, tmp_path / "dl-3.0.0", years=[2017], label="deep2", version="dl-3.0.0")
    assert meta["architecture"] == "attention" and meta["years"]["2017"]["top20_gain"] is not None
    scores = model.scores(fp, tmp_path / "dl-3.0.0")
    later = [position for position, day in enumerate(fp.sessions) if day.year == 2018]
    assert np.isfinite(scores[later[-1]]).sum() >= 50


def test_the_wide_version_learns_from_tpex_too_but_scores_the_listed(monkeypatch, tmp_path):
    """2026-10-18 (S9-W15): dl-2.2.0 learns from every stock that trades normally, TPEx (.TWO) and small ones included;
    it is scored and judged on the eligible listed stocks only, against dl-2.1.0."""
    monkeypatch.setattr(model_deep2, "EPOCHS", 1)
    monkeypatch.setattr(model_deep2, "SEEDS", (0,))
    monkeypatch.setattr(model_deep2, "LENGTH", 10)
    monkeypatch.setattr(model_deep2, "GATE_YEARS", (2017,))
    snapshot = tmp_path / "gbm-2.2.0"
    snapshot.mkdir()
    (snapshot / "meta.json").write_text(__import__("json").dumps({"version": "gbm-2.2.0", "features": ["momentum_3"]}),
                                        encoding="utf-8")
    days = weekdays(date(2015, 1, 1), date(2017, 12, 31))
    rng = np.random.default_rng(11)
    closes = {}
    for n in range(80):
        symbol = f"{1101 + n}.TW" if n < 60 else f"{6101 + n}.TWO"
        closes[symbol] = dict(zip(days, np.maximum(30 * np.cumprod(1 + rng.normal(0.0002, 0.012, len(days))), 11.0)))
    closes["0050.TW"] = {day: 50.0 for day in days}
    data = LegacyData(sessions=days, closes=closes, traded_value={s: {d: 5e9 for d in v} for s, v in closes.items()},
                      factors={}, predictions={})
    fp = FactorPanel(Panel(data))
    meta = model.train(fp, tmp_path / "dl-2.2.0", years=[2017], label="deep2", version="dl-2.2.0")
    assert meta["learned_from"].startswith("listed and TPEx") and meta["years"]["2017"]["top20_gain"] is not None
    scores = model.scores(fp, tmp_path / "dl-2.2.0")
    day = fp.index[next(day for day in fp.sessions if day.year == 2017)]
    scored = {fp.symbols[row] for row in np.flatnonzero(np.isfinite(scores[day]))}
    assert scored and all(symbol.endswith(".TW") for symbol in scored)          # TPEx learned from, not scored
    assert model_deep2.wide_mask(fp)[[fp.row[s] for s in fp.symbols if s.endswith(".TWO")], day].all()


def test_the_volatility_adjusted_version_learns_excess_over_volatility_and_trains(monkeypatch, tmp_path):
    """2026-10-20: dl-2.4.0 learns the top tenth of the excess ÷ the stock's 60-session volatility, judged on the excess."""
    monkeypatch.setattr(model_deep2, "EPOCHS", 1)
    monkeypatch.setattr(model_deep2, "SEEDS", (0,))
    monkeypatch.setattr(model_deep2, "LENGTH", 10)
    monkeypatch.setattr(model_deep2, "GATE_YEARS", (2017,))
    snapshot = tmp_path / "gbm-2.2.0"
    snapshot.mkdir()
    (snapshot / "meta.json").write_text(__import__("json").dumps({"version": "gbm-2.2.0", "features": ["momentum_3"]}),
                                        encoding="utf-8")
    seen = {}
    real_fit = model_deep2._fit

    def spy(inputs, kind, seed, train_days, held_days, rows, labels, architecture="gru", judged=None):
        seen.setdefault("labels", labels)
        seen.setdefault("judged", judged)
        return real_fit(inputs, kind, seed, train_days, held_days, rows, labels, architecture, judged)
    monkeypatch.setattr(model_deep2, "_fit", spy)
    _data, fp = _panel()
    meta = model.train(fp, tmp_path / "dl-2.4.0", years=[2017], label="deep2", version="dl-2.4.0")
    assert meta["years"]["2017"]["top20_gain"] is not None and seen["judged"] is not None
    column = next(iter(seen["labels"]))
    deviation = -fp.matrix("low_volatility_60")[:, column]
    raw = seen["judged"][1][column]
    known = np.isfinite(raw) & (deviation > 0)
    assert np.allclose(seen["labels"][column][known], raw[known] / deviation[known])   # learned: excess ÷ volatility


def test_the_command_line_takes_every_registered_model_version(monkeypatch):
    """2026-10-20: a hand-kept list of versions left gbm-2.3.0 and dl-2.4.0 out and their training never ran."""
    import sys

    from quant_platform.research import __main__ as cli
    from quant_platform.research.model import VERSIONS

    seen = {}
    monkeypatch.setattr(sys, "argv", ["x", "model", "--model-version", "dl-2.4.0", "--help"])
    try:
        cli.main()
    except SystemExit as stop:
        seen["code"] = stop.code
    assert seen["code"] == 0 and {"gbm-2.3.0", "dl-2.4.0"} <= set(VERSIONS)


def test_the_technical_version_sees_prices_and_volumes_only_and_its_own_gate(monkeypatch, tmp_path):
    """2026-10-20: dl-2.5.0 (the technical robot's third version) — the sequence without the chip columns, the robot's
    20 technical factors as the snapshot, judged against gbm-2.1.0; its scores load with the same inputs."""
    from quant_platform.research.tech_rl import TECH_FEATURES

    monkeypatch.setattr(model_deep2, "EPOCHS", 1)
    monkeypatch.setattr(model_deep2, "SEEDS", (0,))
    monkeypatch.setattr(model_deep2, "LENGTH", 10)
    monkeypatch.setattr(model_deep2, "GATE_YEARS", (2017,))
    _data, fp = _panel()
    eligible = model.eligibility(fp)
    full = model_deep2.sequence_inputs(fp, eligible)
    bare = model_deep2.sequence_inputs(fp, eligible, chips=False)
    assert full.shape[2] == len(model_deep2.SEQUENCE) and bare.shape[2] == len(model_deep2.SEQUENCE) - len(model_deep2.CHIPS)
    assert np.array_equal(bare, full[:, :, :bare.shape[2]])                       # the same price and volume columns
    meta = model.train(fp, tmp_path / "dl-2.5.0", years=[2017], label="deep2", version="dl-2.5.0")
    assert meta["features"] == list(TECH_FEATURES) and not set(model_deep2.CHIPS) & set(meta["sequence"])
    assert model_deep2.gate(tmp_path, "dl-2.5.0") == {"version": "dl-2.5.0", "passed": False, "reason": "not trained"}
    yardstick = tmp_path / "gbm-2.1.0"
    yardstick.mkdir()
    (yardstick / "meta.json").write_text(json.dumps({"version": "gbm-2.1.0", "years": {"2017": {"top20_gain": -1.0}}}),
                                         encoding="utf-8")
    assert model_deep2.gate(tmp_path, "dl-2.5.0")["against"] == "gbm-2.1.0"
    scores = model_deep2.scores(fp, tmp_path / "dl-2.5.0")
    assert scores.shape == (len(fp.sessions), len(fp.symbols)) and np.isfinite(scores).any()


def test_the_monthly_version_refits_every_month_on_labels_ended_before_it(monkeypatch, tmp_path):
    """2026-10-20: dl-2.6.0 is dl-2.1.0 refitted every month; each fit learns only from labels ended before its month."""
    monkeypatch.setattr(model_deep2, "EPOCHS", 1)
    monkeypatch.setattr(model_deep2, "SEEDS", (0,))
    monkeypatch.setattr(model_deep2, "LENGTH", 10)
    monkeypatch.setattr(model_deep2, "GATE_YEARS", (2017,))
    snapshot = tmp_path / "gbm-2.2.0"
    snapshot.mkdir()
    (snapshot / "meta.json").write_text(json.dumps({"version": "gbm-2.2.0", "features": ["momentum_3"]}), encoding="utf-8")
    fits = []
    real_fit = model_deep2._fit

    def spy(inputs, kind, seed, train_days, held_days, rows, labels, architecture="gru", judged=None):
        fits.append((max(train_days), max(held_days)))
        return real_fit(inputs, kind, seed, train_days, held_days, rows, labels, architecture, judged)
    monkeypatch.setattr(model_deep2, "_fit", spy)
    _data, fp = _panel()
    meta = model.train(fp, tmp_path / "dl-2.6.0", years=[2017], label="deep2", version="dl-2.6.0")
    months = sorted({day.month for day in fp.sessions if day.year == 2017})
    assert len(fits) == len(months) and meta["years"]["2017"]["top20_gain"] is not None
    firsts = [next(index for index, day in enumerate(fp.sessions) if day.year == 2017 and day.month == month)
              for month in months]
    assert all(held + model.HORIZON < first for (_train, held), first in zip(fits, firsts, strict=True))
    assert fits[-1][1] > fits[0][1]                                                  # later months learn more
    assert model_deep2.gate(tmp_path, "dl-2.6.0")["reason"] == "not trained"          # no dl-2.1.0 yardstick here
    # 2026-10-21: the nightly refit of a new month fits that month only; the other months' scores stay as they were
    before = model_deep2._load_scores(tmp_path / "dl-2.6.0", fp.symbols)
    fits.clear()
    model.train(fp, tmp_path / "dl-2.6.0", years=[2017], label="deep2", version="dl-2.6.0", months=[months[-1]])
    after = model_deep2._load_scores(tmp_path / "dl-2.6.0", fp.symbols)
    assert len(fits) == 1 and fits[0][1] + model.HORIZON < firsts[-1]
    earlier = [day for day in before if day < fp.sessions[firsts[-1]].isoformat()]
    assert earlier and all(np.array_equal(before[day], after[day], equal_nan=True) for day in earlier)


def test_a_monthly_cutoff_refit_scores_every_session_after_it(monkeypatch, tmp_path):
    """2026-10-21 (使用者 2026-10-16：每月滾動，模型也要更新): retrained on the labels ended by the cutoff, the sessions
    after the cutoff take its scores; the yearly scores before it stay."""
    monkeypatch.setattr(model_deep2, "EPOCHS", 1)
    monkeypatch.setattr(model_deep2, "SEEDS", (0, 1))
    monkeypatch.setattr(model_deep2, "LENGTH", 10)
    monkeypatch.setattr(model_deep2, "GATE_YEARS", (2017,))
    monkeypatch.setattr(model_deep2, "GATE_FLOOR", -9.0)
    monkeypatch.setattr(model_deep2, "GATE_COUNT", 1)
    snapshot = tmp_path / "gbm-2.2.0"
    snapshot.mkdir()
    (snapshot / "meta.json").write_text(json.dumps({"version": "gbm-2.2.0", "features": ["momentum_3", "reversal_1"],
                                                    "years": {"2017": {"top20_gain": -9.0}}}), encoding="utf-8")
    _data, fp = _panel()
    folder = tmp_path / "dl-2.1.0"
    model.train(fp, folder, years=[2017], label="deep2", version="dl-2.1.0")
    before = model.scores(fp, folder)
    cutoff = [day for day in fp.sessions if day.year == 2018][60]
    found = model_deep2.train_until(fp, folder, "dl-2.1.0", cutoff)
    assert found["cutoff"] == cutoff.isoformat() and found["scored_days"] > 0
    assert date.fromisoformat(found["label_until"]) <= cutoff                       # nothing after the cutoff learned
    assert sorted(path.name for path in (folder / "monthly").glob("*.pt")) == [f"{cutoff}-0.pt", f"{cutoff}-1.pt"]
    after = model.scores(fp, folder)
    past = fp.index[cutoff]
    assert np.array_equal(np.nan_to_num(after[:past + 1]), np.nan_to_num(before[:past + 1]))   # up to it: as before
    assert not np.array_equal(np.nan_to_num(after[past + 1:]), np.nan_to_num(before[past + 1:]))  # after: refitted
    assert np.isfinite(after[-1]).sum() >= 50


def test_the_residual_labels_and_the_zone_check_by_hand():
    """2026-10-29 (技術分析機器人第七版): the label less its fit on dl-2.1.0's rank; inside dl-2.1.0's top 30, the new
    scores' top 15 over their bottom 15."""
    rng = np.random.default_rng(3)
    prior = np.tile(np.arange(60, dtype=float)[:, None], (1, 2))           # symbol × session: stock n ranks n
    noise = rng.normal(0, 0.01, 60)
    labels = {0: (0.002 * np.arange(60) + noise).astype(np.float32), 1: np.full(60, 0.01, dtype=np.float32)}
    rows = {0: np.arange(60), 1: np.arange(40)}                              # day 1: fewer than 50 known
    output = model_deep2.residual_labels(prior, labels, rows)
    rank = (np.arange(60) + 1) / 60
    assert abs(np.corrcoef(output[0], rank)[0, 1]) < 1e-6                    # nothing left of the prior's rank
    assert np.allclose(output[0], noise - np.polyval(np.polyfit(rank, noise, 1), rank), atol=1e-6)
    assert np.isnan(output[1]).all()
    gains = {0: np.zeros(60)}
    gains[0][[59, 57, 55]] = 0.06                                            # three of the prior's top 30 gain
    scores = {0: -np.arange(60, dtype=float)}                               # opposite order to the prior
    scores[0][[59, 57, 55]] = 100.0                                          # but those three first
    check = model_deep2.residual_check(scores, prior, gains, np.ones((60, 2), dtype=bool))
    assert check["weeks"] == 1 and check["zone_spread"] == round(0.06 * 3 / 15, 5)
    assert check["correlations"][0] < 0


def test_the_residual_gate_needs_both_unlike_and_useful():
    years = (2021, 2022, 2023, 2024, 2025)

    def meta(spreads, correlation):
        return {"version": "dl-2.5.2", "years": {str(year): {"residual": {"zone_spread": spread,
                                                                           "correlations": [correlation] * 3}}
                                                  for year, spread in zip(years, spreads)}}
    assert model_deep2.gate_check_residual(meta([0.01, 0.01, 0.01, 0.01, -0.01], 0.3), years)["passed"]
    assert not model_deep2.gate_check_residual(meta([0.01, 0.01, 0.01, 0.01, -0.01], 0.6), years)["passed"]
    assert not model_deep2.gate_check_residual(meta([0.01, 0.01, 0.01, -0.01, -0.01], 0.3), years)["passed"]
    assert not model_deep2.gate_check_residual(meta([0.005, 0.005, 0.005, 0.005, 0.005], 0.3), years)["passed"]
    assert not model_deep2.gate_check_residual(meta([0.01, 0.01, 0.01, 0.01, None], 0.3), years)["passed"]


def test_the_residual_version_learns_the_residual_and_trains(monkeypatch, tmp_path):
    """dl-2.5.2: the technical inputs plus dl-2.1.0's score, the residual learned and the epoch chosen on it, the year
    reported on the plain excess with the residual check."""
    from quant_platform.research.tech_rl import TECH_FEATURES

    monkeypatch.setattr(model_deep2, "EPOCHS", 1)
    monkeypatch.setattr(model_deep2, "SEEDS", (0,))
    monkeypatch.setattr(model_deep2, "LENGTH", 10)
    monkeypatch.setattr(model_deep2, "RECENT_GATE_YEARS", (2017,))
    _data, fp = _panel()
    real = fp.matrix
    monkeypatch.setattr(fp, "matrix", lambda name: real("momentum_3") if name == "ml_deep2_top" else real(name))
    seen = {}
    real_fit = model_deep2._fit

    def spy(inputs, kind, seed, train_days, held_days, rows, labels, architecture="gru", judged=None):
        seen.setdefault("labels", labels)
        seen.setdefault("judged", judged)
        return real_fit(inputs, kind, seed, train_days, held_days, rows, labels, architecture, judged)
    monkeypatch.setattr(model_deep2, "_fit", spy)
    meta = model.train(fp, tmp_path / "dl-2.5.2", years=[2017], label="deep2", version="dl-2.5.2")
    assert meta["features"] == [*TECH_FEATURES, "ml_deep2_top"] and seen["judged"] is None
    year = meta["years"]["2017"]
    assert year["top20_gain"] is not None and year["residual"]["weeks"] > 0
    assert len(year["residual"]["correlations"]) == year["residual"]["weeks"]
    column = next(column for column, label in seen["labels"].items() if np.isfinite(label).sum() >= 50)
    prior = real("momentum_3")[:, column]
    known = np.isfinite(seen["labels"][column]) & np.isfinite(prior)
    rank = __import__("pandas").Series(prior[known]).rank(pct=True).to_numpy()
    assert abs(np.corrcoef(seen["labels"][column][known], rank)[0, 1]) < 1e-4  # learned: the residual
    assert meta["gate"]["against"] == "dl-2.1.0" and "zone_spread" in meta["gate"]
