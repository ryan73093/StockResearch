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
    assert set(meta["years"]) == {"2015", "2016"} and meta["features"] == list(model.features())
    assert len(model.features()) == len(FACTOR_LABELS) - 3 - 5 - 3 - 3 - 2 - 3 - 2 - 1 - 2 - 2 - 2 - 3 - 4 - 10  # not trend quality, raw v2, time, short, alerts, lending, corporate, issuance, dealers, disclosures, pledges, industries, holders, paid (2026-10-22: + the two government-bank factors; 10-23: + the two branch factors)
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


def test_the_deep_models_networks_and_monthly_scores_are_part_of_the_digest(tmp_path):
    """2026-10-28 (research_method §7「重現性」): the risk model's refit to 8/31 (monthly/<cutoff>.npz, .pt) changed
    September's scores while the digest, hashing pickles only, stayed the same."""
    (tmp_path / "2016-0.pt").write_bytes(b"network")
    before = model.digest(tmp_path)
    assert before != model.digest(tmp_path / "missing")
    (tmp_path / "monthly").mkdir()
    (tmp_path / "monthly" / "2026-08-31.npz").write_bytes(b"scores")
    after = model.digest(tmp_path)
    assert after != before
    (tmp_path / "monthly" / "2026-08-31.npz").write_bytes(b"other scores")
    assert model.digest(tmp_path) not in (before, after)
    pickles = tmp_path / "pickles"
    pickles.mkdir()
    (pickles / "2016.pkl").write_bytes(b"model")
    import hashlib
    expected = hashlib.sha256()
    expected.update(b"2016.pkl")
    expected.update(hashlib.sha256(b"model").digest())
    assert model.digest(pickles) == expected.hexdigest()          # a folder of pickles hashes as before


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


def test_the_60_session_model_learns_a_longer_label_and_only_the_label_differs():
    """2026-10-09: gbm-1.3.0 forecasts the next 60 sessions; every other version keeps 20."""
    from quant_platform.research import daily

    assert model.MODELS["ml_gbm_60"] == {"version": "gbm-1.3.0", "label": "excess", "horizon": 60}
    assert all(spec.get("horizon", model.HORIZON) == 20 for name, spec in model.MODELS.items() if name != "ml_gbm_60")
    full, half = daily.model_60_batch()
    weekly = next(rule for rule in daily.turnover_batch() if rule.check == "weekly" and rule.core == half.core)
    assert half.model_copy(update={"name": "x", "factors": {"ml_gbm_statements": 1.0}}).rule_hash == \
        weekly.model_copy(update={"name": "x"}).rule_hash
    assert full.core == 0.0 and "ml_gbm_60" in daily.MODEL_FACTORS


def test_label_and_training_windows_follow_the_horizon():
    days = [date(2024, 1, 1) + timedelta(days=index) for index in range(80)]
    closes = {"1101.TW": {day: 10.0 + index for index, day in enumerate(days)},
              "1102.TW": {day: 10.0 for day in days}, "0050.TW": {day: 100.0 for day in days}}
    data = LegacyData(sessions=days, closes=closes, traded_value={s: {d: 5e7 for d in v} for s, v in closes.items()},
                      factors={}, predictions={})
    fp = FactorPanel(Panel(data))
    eligible = np.ones((len(fp.symbols), len(days)), dtype=bool)
    row = fp.symbols.index("1101.TW")
    twenty = model.label_block(fp, [0], eligible, "excess", 20)[0]
    sixty = model.label_block(fp, [0], eligible, "excess", 60)[0]
    assert sixty[row] > twenty[row] > 0                     # the rising stock gains more over 60 sessions
    assert np.isnan(model.label_block(fp, [30], eligible, "excess", 60)[0]).all()   # past the data: unknown
    samples = list(range(0, 80, 5))
    assert model.training_columns(fp, samples, 2024, 60) == []          # no label window ends before 2024
    later = model.training_columns(fp, samples, 2025, 60)
    assert later and max(later) + 60 < len(days)


def test_the_market_state_is_one_value_a_day_placed_in_its_own_history():
    """2026-10-28 (gbm-2.9.0, research_method §7「模型 M」): a market reading is the same for every eligible stock of a
    day, as its percentile among the past sessions' readings; the other features stay cross-sectional percentiles."""
    data, _drifts = planted(stocks=30)
    fp = FactorPanel(Panel(data), market=np.linspace(100, 200, len(Panel(data).sessions)))
    eligible = np.ones((len(fp.symbols), len(fp.sessions)), dtype=bool)
    columns = [len(fp.sessions) - 100, len(fp.sessions) - 10]           # past the 200-session average and 120 readings
    block = model.feature_block(fp, columns, eligible, ("momentum_3", "market:trend"))
    market = block[:, :, 1]
    assert np.allclose(market[0], market[0][0]) and np.allclose(market[1], market[1][0])
    assert 0 < market[1][0] <= 1                        # a percentile against its own past
    assert not np.allclose(block[0, :, 0], block[0, 0, 0])   # the factor still varies across stocks
    assert "market:maintenance" in model.version_features("gbm-2.9.0")


def test_model_o_sees_model_m_s_features_and_the_earnings_family():
    """2026-10-28 (gbm-2.10.0, research_method §7「模型 O」): model M's list, the market's state still last, and the three
    earnings-news factors, which no earlier version had."""
    from quant_platform.research.fundamentals import EARNINGS_FACTORS

    mine, theirs = model.version_features("gbm-2.10.0"), model.version_features("gbm-2.9.0")
    assert set(mine) == set(theirs) | set(EARNINGS_FACTORS)
    assert mine[-len(model.MARKET_FEATURES):] == model.MARKET_FEATURES
    assert not set(EARNINGS_FACTORS) & set(model.version_features("gbm-2.7.0"))
    assert model.VERSIONS["gbm-2.10.0"]["features"] == "extended_o"


def test_the_technical_robot_s_fourth_version_is_gated_on_2021_to_2025():
    """2026-10-28: dl-2.5.1 is dl-2.5.0 judged on 2021-2025 against gbm-2.1.0 (four of five years at +0.6% or more)."""
    from quant_platform.research import model_deep2

    assert model_deep2.gate_years("dl-2.5.1") == (2021, 2022, 2023, 2024, 2025)
    assert model_deep2.gate_years("dl-2.5.0") == model_deep2.GATE_YEARS
    assert "dl-2.5.1" in model_deep2.TECHNICAL and model.VERSIONS["dl-2.5.1"]["label"] == "deep2"
    mine = {"version": "dl-2.5.1", "years": {str(year): {"top20_gain": gain} for year, gain in
                                             zip(range(2016, 2026), [0.0, 0.0, 0.0, 0.0, 0.0, 0.02, 0.007, 0.012, 0.009, 0.015])}}
    theirs = {"version": "gbm-2.1.0", "years": {str(year): {"top20_gain": gain} for year, gain in
                                                zip(range(2021, 2026), [0.019, 0.0036, 0.0063, 0.0052, 0.027])}}
    check = model_deep2.gate_check(mine, theirs, model_deep2.gate_years("dl-2.5.1"))
    assert check["passed"] and check["years"] == [2021, 2022, 2023, 2024, 2025] and check["years_above_floor"] == 5
    assert not model_deep2.gate_check(mine, theirs)["passed"]           # the old years: no gain there


def test_the_regated_versions_copy_every_setting_of_the_old_one():
    """2026-10-28: a re-gated version is the stopped one as it was — loss, inputs, universe, architecture, yardstick —
    only its gate years differ (2021-2025)."""
    from quant_platform.research import model_deep2 as d

    for new, old in d.REGATED.items():
        assert d.LOSSES[new] == d.LOSSES[old] and d.gate_years(new) == d.RECENT_GATE_YEARS
        for group in (d.TECHNICAL, d.MONTHLY, d.RISK_ADJUSTED, d.WIDE):
            assert (new in group) == (old in group)
        for table in (d.PLAIN_AGAINST, d.SNAPSHOT_OF, d.ARCHITECTURES, d.AGAINST_OF):
            assert table.get(new) == table.get(old)
        assert new in model.VERSIONS and model.VERSIONS[new].get("universe") == model.VERSIONS[old].get("universe")
    assert d.ARCHITECTURES["dl-3.0.1"] == "attention" and "dl-2.2.1" in d.WIDE and d.AGAINST_OF["dl-2.2.1"] == "dl-2.1.0"
    years = {str(year): {"top20_gain": 0.02} for year in range(2021, 2026)}
    weaker = {str(year): {"top20_gain": 0.015} for year in range(2021, 2026)}
    check = d.gate_check_d3({"years": years}, {"years": weaker}, d.gate_years("dl-3.0.1"))
    assert check["passed"] and check["years"] == [2021, 2022, 2023, 2024, 2025]
