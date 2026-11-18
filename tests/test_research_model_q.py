"""2026-11-18 (gbm-2.12.0, docs/research/results.md「模型 Q」): from 9/1 to 11/17 0050 fell 14% and the rules reading a
model moved 1.4 to 1.65 times the market. Model Q learns the return net of the stock's beta; its gate looks at the
stretches the index fell."""
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np

from quant_platform.research import model


def weekdays(start, count):
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def market_of(count, seed=3):
    rng = np.random.default_rng(seed)
    return 100 * np.cumprod(1 + rng.normal(0, 0.01, count))


def panel(prices, market, days):
    return SimpleNamespace(panel=SimpleNamespace(filled=np.asarray(prices, dtype=float)), market=market, sessions=days,
                           symbols=[f"{1101 + row}.TW" for row in range(len(prices))])


def test_a_stocks_beta_is_its_share_of_the_markets_move_as_known_that_day():
    days = weekdays(date(2021, 1, 4), 200)
    market = market_of(len(days))
    moves = market[1:] / market[:-1] - 1
    twice = 50 * np.concatenate([[1], np.cumprod(1 + 2 * moves)])           # moves twice the market
    steady = 30 * 1.001 ** np.arange(len(days))                             # its own way: nothing of the market's
    half = 20 * np.concatenate([[1], np.cumprod(1 + 0.5 * moves + 0.0005)])
    young = np.where(np.arange(len(days)) < 150, np.nan, twice)             # too few of the 120 sessions traded
    fp = panel([twice, steady, half, young], market, days)
    betas = model.market_betas(fp, [170, 60])
    assert np.allclose(betas[0][:3], [2.0, 0.0, 0.5], atol=1e-4)
    assert np.isnan(betas[0][3])                                            # 21 sessions of the 120
    assert np.isnan(betas[1]).all()                                         # column 60: no 120 sessions before it
    later = panel([np.concatenate([twice[:171], twice[171:] * 3])], market, days)
    assert np.allclose(model.market_betas(later, [170])[0], [2.0], atol=1e-4)   # what comes after the day changes nothing


def test_the_residual_label_takes_the_stocks_share_of_the_markets_move_out():
    days = weekdays(date(2021, 1, 4), 220)
    market = market_of(len(days))
    moves = market[1:] / market[:-1] - 1
    rows = []
    for number in range(9):                         # betas 0, 0.25 … 2, each with its own steady extra a day
        extra = 0.0002 * (number - 4)
        rows.append(40 * np.concatenate([[1], np.cumprod(1 + number * 0.25 * moves + extra)]))
    fp = panel(rows, market, days)
    eligible = np.ones((9, len(days)), dtype=bool)
    column, horizon = 180, 20
    labels = model.label_block(fp, [column], eligible, "residual", horizon)[0]
    prices = fp.panel.filled
    returns = prices[:, column + horizon] / prices[:, column] - 1
    net = returns - model.market_betas(fp, [column])[0] * (market[column + horizon] / market[column] - 1)
    for a, b in ((2, 3), (3, 5), (4, 6)):           # away from the clipped ends: the differences are the net returns'
        assert np.isclose(labels[a] - labels[b], net[a] - net[b], atol=1e-6)
    plain = model.label_block(fp, [column], eligible, "excess", horizon)[0]
    assert np.isclose(plain[3] - plain[5], returns[3] - returns[5], atol=1e-6)      # the plain label is as it was
    ranks = model.label_block(fp, [column], eligible, "residual_rank", horizon)[0]
    assert np.array_equal(np.argsort(ranks), np.argsort(net))                       # ranked by the net return
    assert np.isnan(model.label_block(fp, [210], eligible, "residual", horizon)[0]).all()   # past the data


def test_the_label_in_units_of_the_stocks_own_volatility_does_not_favour_the_volatile():
    """2026-11-18 (dl-2.8.1): dl-2.8.0's top 20 kept dl-2.1.0's beta - a volatile stock is likelier to be among the
    day's highest returns whatever is taken off. "residual_z" divides what the beta leaves by the stock's own volatility
    net of the market over the same 120 sessions."""
    days = weekdays(date(2021, 1, 4), 220)
    market = market_of(len(days))
    moves = market[1:] / market[:-1] - 1
    rng = np.random.default_rng(11)
    rows, sizes = [], [0.002, 0.004, 0.008, 0.016, 0.004, 0.008, 0.002, 0.016, 0.004, 0.008, 0.002, 0.016]
    for number, size in enumerate(sizes):           # beta 1, its own noise of four sizes
        rows.append(40 * np.concatenate([[1], np.cumprod(1 + moves + rng.normal(0, size, len(moves)))]))
    fp = panel(rows, market, days)
    eligible = np.ones((len(rows), len(days)), dtype=bool)
    column, horizon = 180, 20
    slopes, left = model.market_betas(fp, [column], spreads=True)
    assert np.allclose(slopes, model.market_betas(fp, [column]), equal_nan=True)        # the betas are as they were
    prices = fp.panel.filled
    window = prices[:, column - 119:column + 1] / prices[:, column - 120:column] - 1
    index = market[column - 119:column + 1] / market[column - 120:column] - 1
    for row in (0, 3, 7):                           # by hand: the volatility of what the beta leaves
        own = window[row] - window[row].mean() - slopes[0][row] * (index - index.mean())
        assert np.isclose(left[0][row], np.sqrt((own ** 2).mean()), rtol=1e-4)
    assert np.all(np.abs(left[0] / np.array(sizes) - 1) < 0.25)                          # about the noise put in
    returns = prices[:, column + horizon] / prices[:, column] - 1
    net = returns - slopes[0] * (market[column + horizon] / market[column] - 1)
    floor = np.percentile(left[0], 5)
    units = net / np.maximum(left[0], floor)
    labels = model.label_block(fp, [column], eligible, "residual_z", horizon)[0]
    inside = [row for row in np.argsort(units)[2:-2]]                                    # away from the clipped ends
    for a, b in zip(inside, inside[1:]):
        assert np.isclose(labels[a] - labels[b], units[a] - units[b], atol=1e-4)
    later = panel([np.concatenate([row[:column + 1], row[column + 1:] * 2]) for row in rows], market, days)
    assert np.allclose(model.market_betas(later, [column], spreads=True)[1], left)      # only what was known that day
    assert np.isnan(model.label_block(fp, [210], eligible, "residual_z", horizon)[0]).all()


def gate_panel():
    """60 stocks over 2021-2022: 30 move twice the index, 30 a fifth of it with a steady 0.05% a day of their own;
    the index rises and falls by turns of 20 sessions."""
    days = weekdays(date(2020, 1, 1), 780)
    rng = np.random.default_rng(5)
    moves = np.where((np.arange(len(days) - 1) // 20) % 2 == 0, 0.004, -0.004) + rng.normal(0, 0.002, len(days) - 1)
    market = 100 * np.concatenate([[1], np.cumprod(1 + moves)])
    rows = [30 * np.concatenate([[1], np.cumprod(1 + 2 * moves)]) for _ in range(30)]
    rows += [30 * np.concatenate([[1], np.cumprod(1 + 0.2 * moves + 0.0005)]) for _ in range(30)]
    fp = panel(rows, market, days)
    lively = np.tile(np.concatenate([np.linspace(1, 2, 30), np.linspace(0, 0.5, 30)])[None, :], (len(days), 1))
    return fp, lively


def test_the_gate_asks_for_less_lost_when_the_index_falls_without_giving_the_rest_up(monkeypatch):
    fp, lively = gate_panel()
    tables = {"calm": -lively, "lively": lively}
    monkeypatch.setattr(model, "eligibility", lambda fp: np.ones((len(fp.symbols), len(fp.sessions)), dtype=bool))
    monkeypatch.setattr(model, "scores", lambda fp, folder: tables[str(folder)])
    monkeypatch.setattr(model, "DOWN_COUNT", 2)
    check = model.down_gate(fp, "calm", "lively", years=(2021, 2022))
    assert check["weeks"] > 60 and 0 < check["weeks_fallen"] < check["weeks"]
    assert check["fallen"] > 0 > check["against_fallen"]            # the calm 20 beat a falling index; the lively lose to it
    assert check["fallen"] - check["against_fallen"] >= model.DOWN_BETTER
    assert check["years_ahead"] == 2 and check["passed"]
    other = model.down_gate(fp, "lively", "calm", years=(2021, 2022))
    assert not other["passed"]                                       # more lost when the index falls
    monkeypatch.setattr(model, "DOWN_COUNT", 3)
    assert not model.down_gate(fp, "calm", "lively", years=(2021, 2022))["passed"]   # a year short


def test_a_version_that_gives_too_much_up_over_every_stretch_does_not_pass(monkeypatch):
    fp, lively = gate_panel()
    prices = fp.panel.filled
    prices[:30] = prices[:30] * (1.002 ** np.arange(prices.shape[1]))[None, :]      # the lively ones earn 0.2% a day more
    tables = {"calm": -lively, "lively": lively}
    monkeypatch.setattr(model, "eligibility", lambda fp: np.ones((len(fp.symbols), len(fp.sessions)), dtype=bool))
    monkeypatch.setattr(model, "scores", lambda fp, folder: tables[str(folder)])
    monkeypatch.setattr(model, "DOWN_COUNT", 2)
    check = model.down_gate(fp, "calm", "lively", years=(2021, 2022))
    assert check["against_top20_gain"] - check["top20_gain"] > model.DOWN_GIVEN_UP
    assert not check["passed"]


def test_model_q_is_model_k_s_features_with_the_other_target_and_its_own_gate(tmp_path):
    assert model.version_features("gbm-2.12.0") == model.version_features("gbm-2.7.0")
    assert model.VERSIONS["gbm-2.12.0"]["target"] == "residual" and "target" not in model.VERSIONS["gbm-2.7.0"]
    assert model.DOWN_GATES == {"gbm-2.12.0": "gbm-2.7.0"}
    assert model.stored_down_gate(tmp_path, "gbm-2.12.0") == {}      # not trained: no judgment, so not "failed"
    from quant_platform.research.daily import MODEL_FACTORS, model_q_batch
    from quant_platform.research.scan import SHORT, failed_models

    assert "ml_gbm_q" in MODEL_FACTORS and SHORT["ml_gbm_q"] == "模型Q"
    assert "ml_gbm_q" not in failed_models(tmp_path)
    (tmp_path / "gbm-2.12.0").mkdir()
    (tmp_path / "gbm-2.12.0" / "meta.json").write_text(
        '{"down_gate": {"top20_gain": 0.01, "against_top20_gain": 0.02, "passed": false}}', encoding="utf-8")
    assert "ml_gbm_q" in failed_models(tmp_path)                      # trained and judged: left out of the searches
    assert model_q_batch.__doc__ and "gbm-2.12.0" in model_q_batch.__doc__


def test_the_deep_version_is_dl_2_1_0_learning_the_net_return_and_keeps_its_gate_in_the_meta(tmp_path):
    """2026-11-18 (dl-2.8.0): the gate takes the scores and the prices, so it is judged when trained and read back."""
    from quant_platform.research import model_deep2
    from quant_platform.research.daily import MODEL_FACTORS
    from quant_platform.research.scan import SHORT, failed_models

    assert model_deep2.NET_OF_MARKET == {"dl-2.8.0": "dl-2.1.0", "dl-2.8.1": "dl-2.1.0"}
    assert model_deep2.NET_LABELS == {"dl-2.8.1": "residual_z"}            # dl-2.8.0 keeps the plain residual
    assert model.VERSIONS["dl-2.8.1"] == {"version": "dl-2.8.1", "label": "deep2"}
    assert model_deep2.LOSSES["dl-2.8.1"] == model_deep2.LOSSES["dl-2.1.0"]
    assert model_deep2.LOSSES["dl-2.8.0"] == model_deep2.LOSSES["dl-2.1.0"]
    assert model_deep2.gate_years("dl-2.8.0") == (2021, 2022, 2023, 2024, 2025)
    for group in (model_deep2.TECHNICAL, model_deep2.MONTHLY, model_deep2.RISK_ADJUSTED, model_deep2.WIDE):
        assert "dl-2.8.0" not in group                              # nothing else of dl-2.1.0 changed
    assert model.VERSIONS["dl-2.8.0"] == {"version": "dl-2.8.0", "label": "deep2"}
    assert "ml_deep2_net" in MODEL_FACTORS and SHORT["ml_deep2_net"] == "深度2扣連動"
    assert model_deep2.gate(tmp_path, "dl-2.8.0") == {"version": "dl-2.8.0", "passed": False, "reason": "not trained"}
    assert "ml_deep2_net" not in failed_models(tmp_path)            # not trained is not failed
    (tmp_path / "dl-2.8.0").mkdir()
    kept = {"version": "dl-2.8.0", "against": "dl-2.1.0", "top20_gain": 0.02, "against_top20_gain": 0.029, "passed": False}
    (tmp_path / "dl-2.8.0" / "meta.json").write_text(__import__("json").dumps({"gate": kept}), encoding="utf-8")
    assert model_deep2.gate(tmp_path, "dl-2.8.0") == kept
    assert "ml_deep2_net" in failed_models(tmp_path)


def test_the_months_refit_learns_the_versions_own_target(tmp_path, monkeypatch):
    """2026-11-18 11:36: model Q's refit to the month's cutoff learned the plain labels - train_until had its own copy
    of the label lines - so from the cutoff on the forward accounts would have read model K under model Q's name."""
    from quant_platform.research import model_ensemble

    days = weekdays(date(2024, 1, 1), 400)
    fp = SimpleNamespace(sessions=days, symbols=["1101.TW"])
    asked = []
    monkeypatch.setattr(model, "trained_features", lambda out, version: ("momentum_3",))
    monkeypatch.setattr(model, "eligibility", lambda fp: np.ones((1, len(days)), dtype=bool))
    monkeypatch.setattr(model, "feature_block", lambda fp, samples, eligible, names: np.zeros((len(samples), 1, 1)))
    monkeypatch.setattr(model, "label_block", lambda fp, samples, eligible, kind, horizon:
                        asked.append((kind, horizon)) or np.zeros((len(samples), 1)))
    monkeypatch.setattr(model_ensemble, "fit_year", lambda *args, **kwargs: {"fitted": True})
    model.train_until(fp, tmp_path / "gbm-2.12.0", days[-1], "ensemble", "gbm-2.12.0")
    assert asked == [("residual", 20), ("residual_rank", 20), ("residual", 60)]
    asked.clear()
    model.train_until(fp, tmp_path / "gbm-2.7.0", days[-1], "ensemble", "gbm-2.7.0")
    assert asked == [("excess", 20), ("rank", 20), ("excess", 60)]
