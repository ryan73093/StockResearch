"""The factor read from the US side (2026-11-21)."""
from datetime import date, timedelta

import numpy as np

from quant_platform.research import categories, daily, scan, us_factors


def test_the_lead_reads_only_us_sessions_dated_before_the_taiwan_session():
    closes = {date(2026, 11, 16): 100.0, date(2026, 11, 17): 110.0, date(2026, 11, 18): 121.0, date(2026, 11, 19): 121.0}
    sessions = [date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)]
    found = us_factors.lead(closes, sessions)
    assert np.isnan(found[0])
    # the Taiwan session of the 18th knows the US session of the 17th (+10%), not its own date's (+10% again)
    assert abs(found[1] - 0.10) < 1e-12 and abs(found[2] - 0.10) < 1e-12 and abs(found[3]) < 1e-12
    # a Taiwan session before the series starts has nothing
    assert np.isnan(us_factors.lead(closes, [date(2026, 11, 13), date(2026, 11, 16)])).all()


def test_the_rolling_slope_follows_the_last_window_and_needs_enough_days():
    generator = np.random.default_rng(3)
    x = generator.normal(0, 0.02, 300)
    noise = generator.normal(0, 0.001, 300)
    y = np.column_stack([2.0 * x + noise, np.where(np.arange(300) < 150, 0.5, 1.5) * x + noise, np.full(300, np.nan)])
    y[250:, 2] = x[250:]
    slope = us_factors.rolling_slope(x, y, window=120, least=100)
    assert np.isnan(slope[98, 0]) and abs(slope[99, 0] - 2.0) < 0.05
    assert abs(slope[140, 1] - 0.5) < 0.05 and abs(slope[299, 1] - 1.5) < 0.05     # the window forgets the old slope
    assert np.isnan(slope[299, 2])                                                  # 50 known days of 120
    # the same as a direct fit on the window
    direct = np.polyfit(x[180:300], y[180:300, 0], 1)[0]
    assert abs(slope[299, 0] - direct) < 1e-9


def test_a_later_us_session_does_not_change_an_earlier_days_value():
    generator = np.random.default_rng(5)
    start = date(2025, 1, 1)
    days = [start + timedelta(days=number) for number in range(400) if (start + timedelta(days=number)).weekday() < 5]
    closes = dict(zip(days, 100 * np.cumprod(1 + generator.normal(0, 0.02, len(days)))))
    sessions = days[1:]
    y = generator.normal(0, 0.02, (len(sessions), 4))
    whole = us_factors.rolling_slope(us_factors.lead(closes, sessions), y)
    cut = 200
    earlier = {day: value for day, value in closes.items() if day < sessions[cut]}
    part = us_factors.rolling_slope(us_factors.lead(earlier, sessions[:cut + 1]), y[:cut + 1])
    assert np.allclose(whole[cut], part[cut], equal_nan=True)


def test_the_factor_is_registered_everywhere_a_rule_factor_is():
    assert "us_sox_beta" in daily.RULE_FACTORS and "us_sox_beta" in daily.US_FACTORS
    assert categories.FACTOR_FAMILY["us_sox_beta"] == "美股連動" and "美股連動" in categories.FAMILY_ORDER
    assert "us_sox_beta" in scan.SHORT and "us_sox_beta" in scan.JUDGED_IN


def test_the_index_closes_are_part_of_a_runs_input(tmp_path):
    assert us_factors.digest(tmp_path, date(2026, 9, 30)) is None
    assert "us" in daily.input_digests(tmp_path, ["us_sox_beta"], False)
    assert "us" not in daily.input_digests(tmp_path, ["momentum_3"], False)


def test_the_index_is_refreshed_before_the_orders_only_when_it_lacks_last_night(tmp_path):
    import gzip
    import json
    from datetime import datetime

    from quant_platform.research.history import us

    def chart(days):
        stamps = [int(datetime(day.year, day.month, day.day, 16, 0, tzinfo=us.NEW_YORK).timestamp()) for day in days]
        return json.dumps({"chart": {"result": [{"timestamp": stamps, "indicators": {
            "quote": [{"close": [100.0 + number for number in range(len(days))], "volume": [1.0] * len(days)}]}}]}}).encode("utf-8")
    asked = []

    def get(url):
        asked.append(url)
        return chart([date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)])
    folder = tmp_path / "raw" / "yahoo_us"
    folder.mkdir(parents=True)
    (folder / "_SOX.json.gz").write_bytes(gzip.compress(chart([date(2026, 11, 18), date(2026, 11, 19)])))
    # Monday the 23rd wants Friday the 20th: one request, then nothing more to ask
    report = us_factors.refresh(tmp_path, date(2026, 11, 23), get=get)
    assert report == {"wanted": "2026-11-20", "last": "2026-11-20", "fetched": True, "stale": False} and len(asked) == 1
    assert us_factors.refresh(tmp_path, date(2026, 11, 23), get=get)["fetched"] is False and len(asked) == 1

    def broken(url):
        raise OSError("no answer")
    report = us_factors.refresh(tmp_path, date(2026, 11, 25), get=broken)
    assert report["stale"] and report["last"] == "2026-11-20"             # the file is kept and the report says so


def test_the_rolling_correlation_is_the_one_of_the_same_window():
    generator = np.random.default_rng(11)
    x = generator.normal(0, 0.02, 400)
    y = np.column_stack([0.8 * x + generator.normal(0, 0.01, 400), generator.normal(0, 0.02, 400)])
    slope, related = us_factors.rolling_slope(x, y, window=250, least=200, correlation=True)
    assert abs(related[399, 0] - np.corrcoef(x[150:400], y[150:400, 0])[0, 1]) < 1e-9
    assert abs(slope[399, 0] - np.polyfit(x[150:400], y[150:400, 0], 1)[0]) < 1e-9
    assert np.isnan(related[198, 0]) and abs(related[399, 1]) < 0.2


def test_the_changes_read_only_us_sessions_dated_before_the_taiwan_session():
    days = [date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19)]
    values = np.array([100.0, 110.0, 121.0, 133.1])
    sessions = [date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)]
    last = us_factors._known(days, sessions)
    assert last.tolist() == [0, 1, 2, 3]
    assert np.allclose(us_factors._between(values, last, False)[1:], [0.10, 0.10, 0.10])
    assert np.allclose(us_factors._back(values, last, 2, False)[2:], [0.21, 0.21]) and np.isnan(us_factors._back(values, last, 2, False)[1])
    assert np.allclose(us_factors._between(values, last, True)[1:], [10.0, 11.0, 12.1])


def test_model_v_is_model_k_with_the_us_side(tmp_path, monkeypatch):
    """2026-11-22 (docs/research/results.md「模型 V：模型 K 加上美國那一邊的特徵」, written before the run): gbm-2.17.0 is
    model K's list and target plus 12 US features, judged by model Q's gate against model K; its three rules only past
    the gate, and a failed gate keeps it out of the searches."""
    import json

    from quant_platform.research import model
    from quant_platform.research.scan import SHORT, failed_models

    features = model.version_features("gbm-2.17.0")
    assert features == model.version_features("gbm-2.7.0") + model.US_FEATURES_V
    assert len(model.US_FEATURES_V) == 12 and len(set(features)) == len(features)
    per_stock = [name for name in model.US_FEATURES_V if not name.startswith("market:")]
    assert set(per_stock) == set(us_factors.US_FACTORS) | set(us_factors.US_FEATURES)
    assert tuple(name for name in model.US_FEATURES_V if name.startswith("market:")) == us_factors.US_MARKET
    assert not set(us_factors.US_FEATURES) & set(daily.RULE_FACTORS)              # a model's inputs, not rule factors
    assert model.MODELS["ml_gbm_v"]["version"] == "gbm-2.17.0" and "target" not in model.MODELS["ml_gbm_v"]
    assert model.DOWN_GATES["gbm-2.17.0"] == "gbm-2.7.0"
    assert "ml_gbm_v" in daily.MODEL_FACTORS and SHORT["ml_gbm_v"] == "模型V"
    monkeypatch.chdir(tmp_path)
    assert daily.BATCHES["modelv"]() == []                           # not trained: no rules
    folder = tmp_path / "instance" / "research" / "models" / "gbm-2.17.0"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({"down_gate": {"passed": False, "top20_gain": 0.012,
                                                               "against_top20_gain": 0.013}}), encoding="utf-8")
    assert daily.BATCHES["modelv"]() == []
    assert "ml_gbm_v" in failed_models(tmp_path / "instance" / "research" / "models")
    (folder / "meta.json").write_text(json.dumps({"down_gate": {"passed": True}}), encoding="utf-8")
    rules = daily.BATCHES["modelv"]()
    assert len(rules) == 3 and all(rule.factors == {"ml_gbm_v": 1.0} for rule in rules)


def test_a_us_market_reading_is_dated_before_the_session(tmp_path):
    import gzip
    import json
    from datetime import datetime

    from quant_platform.research.history import us

    days = [date(2026, 9, 1) + timedelta(days=number) for number in range(60)]
    days = [day for day in days if day.weekday() < 5]
    stamps = [int(datetime(day.year, day.month, day.day, 16, 0, tzinfo=us.NEW_YORK).timestamp()) for day in days]
    payload = {"chart": {"result": [{"timestamp": stamps, "indicators": {
        "quote": [{"close": [20.0 + number for number in range(len(days))], "volume": [1.0] * len(days)}]}}]}}
    folder = tmp_path / "raw" / "yahoo_us"
    folder.mkdir(parents=True)
    (folder / (us._file("^VIX") + ".json.gz")).write_bytes(gzip.compress(json.dumps(payload).encode("utf-8")))
    found = us_factors.market_reading(tmp_path, "market:us_vix", [days[0], days[1], days[10]])
    assert np.isnan(found[0]) and found[1] == 20.0 and found[2] == 29.0   # the session reads the US day before it
    assert np.isnan(us_factors.market_reading(tmp_path, "market:us_curve", [days[5]])).all()   # no file: nothing
