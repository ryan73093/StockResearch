"""The US overheating condition and the share held that reads it (空頭防護第十四版, 2026-11-22)."""
from datetime import date, timedelta

import numpy as np

from quant_platform.research import daily, market_signals, pool, stress, us_factors
from quant_platform.research.costs import CostModel
from quant_platform.research.daily import DailyRule, FactorPanel
from quant_platform.research.stock_forward import StockForwardTracker
from quant_platform.research.stock_rules import Panel
from tests.test_research_daily import market, weekdays

FREE = CostModel(fee_rate=0.0, minimum_fee=0, slippage_bps=0.0)


def test_the_count_reads_only_readings_dated_before_the_session(monkeypatch):
    start = date(2020, 1, 1)
    days = [start + timedelta(days=number) for number in range(400)]
    low = {day: -float(number) for number, day in enumerate(days)}               # always its lowest so far: never high
    high = {**low, **{day: 100.0 for day in days[300:]}}                         # jumps on day 300 and stays
    readings = {f"{market_signals.MACRO}{name}": (high if number < 3 else low)
                for number, name in enumerate(market_signals.HOT_READINGS)}
    monkeypatch.setattr(market_signals, "us_macro", lambda history: readings)
    counts = market_signals.us_hot("unused", [days[299], days[300], days[301], days[399]])
    # the reading dated day 300 is known from the session after it, not on its own date
    assert counts[days[299]] == 0 and counts[days[300]] == 0 and counts[days[301]] == 3 and counts[days[399]] == 3
    assert market_signals.us_hot("unused", [days[0]]) == {}                       # nothing known yet: left out
    assert market_signals.HOT_COUNT == 3 and len(market_signals.HOT_READINGS) == 5


def _panel(below_from: int | None, hot: set[int]):
    days = weekdays(date(2019, 1, 1), date(2021, 12, 31))
    path = [20 * 1.001 ** number for number in range(len(days))]
    data = market(days, {f"{1101 + number}.TW": dict(zip(days, path)) for number in range(4)})
    taiex = np.linspace(100, 300, len(days))
    if below_from is not None:
        taiex = np.concatenate([taiex[:below_from], np.full(len(days) - below_from, 50.0)])
    fp = FactorPanel(Panel(data), market=taiex)
    fp.us_hot_counts = {day: (market_signals.HOT_COUNT if position in hot else market_signals.HOT_COUNT - 1)
                        for position, day in enumerate(days)}
    return days, data, fp


def test_the_half_version_holds_half_below_the_line_or_while_the_us_overheats():
    days, data, fp = _panel(below_from=700, hot=set(range(400, 450)) | set(range(720, 740)))
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=4, vol_scale="us_hot_half", execution="same")
    levels = daily.exposure_schedule(data, fp, rule, FREE)
    assert levels[days[399]] == 1.0 and levels[days[400]] == 0.5 and levels[days[449]] == 0.5 and levels[days[450]] == 1.0
    assert levels[days[710]] == 0.5 and levels[days[730]] == 0.5                  # below the line: half, never less
    assert set(levels.values()) == {0.5, 1.0}
    half = daily.exposure_schedule(data, fp, rule.model_copy(update={"vol_scale": "taiex_half"}), FREE)
    assert all(levels[day] <= half[day] for day in days)                          # never holds more than the half guard
    later = daily.exposure_schedule(data, fp, rule.model_copy(update={"execution": "next"}), FREE)
    assert later[days[400]] == 1.0 and later[days[401]] == 0.5                    # decided at a close, held the next session


def test_the_quarter_version_cuts_again_only_below_the_line():
    days, data, fp = _panel(below_from=700, hot=set(range(400, 450)) | set(range(720, 740)))
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=4, vol_scale="us_hot_quarter", execution="same")
    levels = daily.exposure_schedule(data, fp, rule, FREE)
    assert levels[days[420]] == 1.0                                               # overheated above the line: untouched
    assert levels[days[710]] == 0.5 and levels[days[730]] == 0.25 and levels[days[745]] == 0.5
    assert set(levels.values()) == {0.25, 0.5, 1.0}


def test_the_setting_is_registered_where_a_share_held_is():
    for name in daily.US_HOT_SCALES:
        assert name in daily.MARKET_SCALES and name in pool.SCALE_WORDS
        assert DailyRule(name="x", factors={"momentum_3": 1.0}, vol_scale=name).canonical()["vol_scale"] == name
    assert {"bear_us_hot", "bear_us_hot_quarter"} <= set(stress.SHORT)
    assert len(daily.BATCHES["bear14"].__doc__) > 0 and len(daily.BEAR_US_HOT) == 2
    item = {"kind": "daily", "rule": {"factors": {"momentum_3": 1.0}, "vol_scale": "us_hot_half"}}
    assert "us_hot" in StockForwardTracker.uses(None, item)


def test_the_readings_are_refreshed_for_an_account_that_reads_them(tmp_path, monkeypatch):
    from quant_platform.research.history import us

    asked = {}
    monkeypatch.setattr(us, "fetch_revised", lambda history, pause, only, **extra: asked.setdefault("revised", only) and {})
    monkeypatch.setattr(us, "fetch_daily", lambda history, pause, only, **extra: asked.setdefault("daily", only) and {})
    assert us_factors.refresh_hot(tmp_path)["fetched"] is True
    assert asked == {"revised": us_factors.HOT_REVISED, "daily": us_factors.HOT_DAILY}

    def broken(history, pause, only, **extra):
        raise OSError("no answer")
    monkeypatch.setattr(us, "fetch_revised", broken)
    assert us_factors.refresh_hot(tmp_path)["fetched"] is False                   # the files are kept, the orders go on


def test_a_trial_with_the_setting_registers_alone_and_in_a_blend(tmp_path, monkeypatch):
    """2026-11-22: bear14's 20 blends stopped on a name the blend's trial never imported — no test ran a trial with the
    setting. Both kinds of trial run and keep the readings among their inputs."""
    import numpy as np

    from quant_platform.research import blend
    from quant_platform.research.registry import TrialRegistry
    from tests.test_research_blend import CALM, UP
    from tests.test_research_blend import market as small_market

    monkeypatch.setattr(daily, "RECENT_START", date(2024, 1, 1))
    monkeypatch.setattr(daily, "REGIME_START", date(2024, 7, 1))
    monkeypatch.setattr(daily, "RECENT_END", date(2024, 12, 31))
    monkeypatch.setattr(daily, "WINDOWS", {"1y": 6})
    data, plain = small_market()
    fp = FactorPanel(plain.panel, market=np.linspace(100, 300, len(plain.sessions)))
    hot = {day: (3 if day.month in (3, 4) else 0) for day in fp.sessions}
    fp.us_hot_counts = hot
    monkeypatch.setattr(market_signals, "us_hot", lambda history, sessions: {day: hot[day] for day in sessions})
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    guarded = [rule.model_copy(update={"vol_scale": "us_hot_half"}) for rule in (UP, CALM)]
    mix = blend.BlendRule(name="過熱減碼的組合", sleeves=(blend.Sleeve(rule=guarded[0], share=0.5),
                                                    blend.Sleeve(rule=guarded[1], share=0.5)))
    record, report = blend.run_blend_trial(mix, tmp_path, registry, tmp_path / "reports", FREE, data, fp, "daily:test")
    assert record.spec_hash == mix.rule_hash and report["curve"]
    single, _report = daily.run_trial(guarded[0], tmp_path, registry, tmp_path / "reports", FREE, data, fp, "daily:test")
    assert single.spec_hash == guarded[0].rule_hash and len(registry.records()) == 2
    assert daily.us_hot_digest(tmp_path, list(fp.sessions), date(2024, 12, 31)) != daily.us_hot_digest(
        tmp_path, list(fp.sessions), date(2024, 3, 15))                              # the readings to the end are the input


def test_a_composite_factor_declares_what_is_under_it(tmp_path):
    """2026-11-22: the block robots and the consensus lists kept an input naming model files only — the statements
    downloaded again changed their results under an unchanged hash (6 of 30 sampled results did not reproduce)."""
    from quant_platform.research import rl_timing

    assert daily.composite_inputs(["momentum_3", "ml_gbm_k"]) == []
    blocks = daily.composite_inputs(["rl_blocks"])
    assert set(blocks) == set(rl_timing.BLOCKS) and "eps_growth" in blocks and "ml_gbm_ens" in blocks
    assert set(daily.composite_inputs(["block_equal"])) == set(blocks)
    assert set(rl_timing.SLOTS_35["集保籌碼"]) <= set(daily.composite_inputs(["rl_rolling35"]))
    members = daily.composite_inputs(["t0_consensus"])
    assert len(members) > 10 and "t0_consensus" not in members
    plain = daily.input_digests(tmp_path, ["momentum_3"], False)
    assert "composite" not in plain and "statements" not in plain          # a rule without one keeps its input
    robot = daily.input_digests(tmp_path, ["rl_blocks"], False)
    assert robot["composite"] == sorted(blocks) and "statements" in robot   # EPS growth reads the statements
    assert "statements" in daily.input_digests(tmp_path, ["t0_consensus"], False)


def test_the_sweep_asks_for_the_whole_pool_and_a_stale_input_is_run_again(tmp_path):
    from quant_platform.research.registry import TrialRegistry

    assert daily.BATCHES["rerun_poolsweep"] is daily.pool_sweep_batch
    # asked-again batches are no rule's source: the sources rebuild leaves every rerun* batch out by its name
    import inspect

    from quant_platform.research import pool

    assert 'name.startswith("rerun")' in inspect.getsource(pool)
    assert daily.BATCHES["rerun_guardorigins"] is daily.guard_origins_batch
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    record = registry.register(kind="candidate", period="recent", spec_hash="s", spec_name="x", input_hash="h1",
                               data_fingerprint="d", metrics={})
    assert daily.mark_stale(tmp_path, [record], "the revenue method of 11/19") == 1
    assert daily.mark_stale(tmp_path, [record], "again") == 0 and daily.stale_inputs(tmp_path) == {"h1"}
