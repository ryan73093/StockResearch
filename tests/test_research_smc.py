"""Smart Money Concepts as daily factors (2026-11-22; 使用者：SMC加到策略研究裡)."""
from datetime import date

import numpy as np

from quant_platform.research import categories, daily, scan, smc

# open, high, low, close — worked by hand with swing=2 (a swing point is the extreme of the 2 bars each side)
BARS = [
    (10.0, 10.5, 9.5, 10.0),    # 0
    (10.0, 11.0, 9.8, 10.8),    # 1
    (10.8, 12.0, 10.5, 11.5),   # 2  swing high 12.0, confirmed on 4
    (11.5, 11.6, 10.6, 10.8),   # 3
    (10.8, 11.0, 10.0, 10.2),   # 4
    (10.2, 10.4, 9.0, 9.4),     # 5  swing low 9.0, confirmed on 7
    (9.4, 10.2, 9.3, 10.0),     # 6
    (10.0, 10.9, 9.9, 10.7),    # 7
    (10.7, 11.2, 10.5, 10.6),   # 8  the last down candle before the break: the order block 10.5–11.2
    (10.6, 12.6, 10.6, 12.5),   # 9  closes above 12.0: the structure breaks upward
    (12.5, 13.5, 12.4, 13.4),   # 10 its low 12.4 above bar 8's high 11.2: a gap 11.2–12.4
    (13.4, 13.6, 12.9, 13.0),   # 11 swing high 13.6, confirmed on 13; its low above bar 9's high: a newer gap 12.6–12.9
    (13.0, 13.1, 12.2, 12.6),   # 12 back into the newest gap (low 12.2, closes on its lower edge), the leg's upper half
    (12.6, 12.7, 11.0, 11.2),   # 13 back into the order block (low 11.0), in the leg's lower half: the setup
    (11.2, 11.9, 11.1, 11.8),   # 14
]
SWEEP = [(11.8, 11.9, 8.8, 9.2),      # 15 trades under the swing low 9.0 and closes back above it
         (9.2, 9.3, 8.5, 8.6)]        # 16 closes under the sweep's low
TURN = [(11.8, 11.9, 8.4, 8.5),       # 15 closes under the swing low: the structure turns bearish
        (8.5, 14.2, 8.5, 14.0)]       # 16 closes above the swing high 13.6: bearish to bullish


def _run(rows):
    bars = np.array(rows, dtype=float)
    return smc.structure(*(bars[:, [column]] for column in range(4)), swing=2, warmup=0)


def test_swing_points_are_known_only_after_the_bars_that_confirm_them():
    bars = np.array(BARS, dtype=float)
    highs, lows = smc.swings(bars[:, [1]], bars[:, [2]], swing=2)
    assert {t: highs[t, 0] for t in range(len(BARS)) if np.isfinite(highs[t, 0])} == {4: 12.0, 13: 13.6}
    assert {t: lows[t, 0] for t in range(len(BARS)) if np.isfinite(lows[t, 0])} == {7: 9.0}


def test_the_break_the_order_block_the_gap_and_the_setup_by_hand():
    found = _run(BARS + SWEEP)

    def at(name, t):
        return float(found[name][t, 0])

    assert np.isnan(at("smc_break_up_20", 8)) and at("smc_break_up_20", 9) == 1.0      # the close above 12.0
    assert abs(at("smc_break_up_20", 14) - 0.75) < 1e-6                                # five sessions later, of 20
    assert np.isnan(found["smc_choch_up_20"][:, 0]).all()                              # never bearish before it
    assert np.isnan(at("smc_fvg_retest_10", 11)) and at("smc_fvg_retest_10", 12) == 1.0
    assert np.isnan(at("smc_ob_retest_10", 12)) and at("smc_ob_retest_10", 13) == 1.0
    assert np.isnan(at("smc_setup_10", 12)) and at("smc_setup_10", 13) == 1.0          # the gap's retest was too high
    assert abs(at("smc_ob_retest_10", 14) - 0.9) < 1e-6
    assert np.isnan(at("smc_fvg_retest_10", 13))        # 11.2 closes under the gap's lower edge 12.6: over
    # the leg runs from the swing low 9.0 to its high 13.6: a close of 11.8 is 0.609 of the way up
    assert abs(at("smc_discount", 14) - (1 - (11.8 - 9.0) / (13.6 - 9.0))) < 1e-6
    assert np.isnan(at("smc_discount", 8))                                             # no bullish structure yet


def test_a_sweep_counts_until_its_low_is_closed_under_and_ends_the_entries_above_it():
    found = _run(BARS + SWEEP)
    assert found["smc_sweep_low_10"][15, 0] == 1.0 and np.isnan(found["smc_sweep_low_10"][14, 0])
    # the close of 9.2 is under the order block's low (10.5): the entries resting on it are over
    for name in ("smc_ob_retest_10", "smc_setup_10"):
        assert np.isnan(found[name][15, 0])
    assert found["smc_break_up_20"][15, 0] > 0            # no close under the swing low: the structure holds
    assert np.isnan(found["smc_sweep_low_10"][16, 0])     # 8.6 closes under the sweep's low 8.8


def test_a_close_under_the_swing_low_turns_the_structure_and_the_next_break_is_a_change_of_character():
    found = _run(BARS + TURN)
    assert np.isnan(found["smc_break_up_20"][15, 0]) and np.isnan(found["smc_discount"][15, 0])
    assert np.isnan(found["smc_sweep_low_10"][15, 0])     # closed under it: a break, not a sweep
    assert found["smc_choch_up_20"][16, 0] == 1.0 and found["smc_break_up_20"][16, 0] == 1.0


def test_later_bars_do_not_change_an_earlier_sessions_values():
    generator = np.random.default_rng(11)
    close = 50 * np.cumprod(1 + generator.normal(0, 0.02, (400, 6)), axis=0)
    open_ = close * (1 + generator.normal(0, 0.008, close.shape))
    high = np.maximum(open_, close) * (1 + np.abs(generator.normal(0, 0.006, close.shape)))
    low = np.minimum(open_, close) * (1 - np.abs(generator.normal(0, 0.006, close.shape)))
    for array in (open_, high, low, close):
        array[120:130, 2] = np.nan                         # a suspension
    whole = smc.structure(open_, high, low, close)
    cut = 300
    part = smc.structure(open_[:cut + 1], high[:cut + 1], low[:cut + 1], close[:cut + 1])
    for name in smc.SMC_FACTORS:
        assert np.allclose(whole[name][cut], part[name][cut], equal_nan=True), name
        assert np.isfinite(whole[name]).any(), name        # every factor fires on a random walk
    assert np.isnan(whole["smc_break_up_20"][:smc.WARMUP - 1]).all()


def test_measuring_reads_no_signal_as_zero_but_not_the_place_in_the_leg():
    values = np.array([[np.nan, 0.5]])
    assert smc.measured("smc_sweep_low_10", values).tolist() == [[0.0, 0.5]]
    assert np.isnan(smc.measured("smc_discount", values)[0, 0])


def test_the_highs_and_lows_come_from_the_stock_files_and_the_after_close_table(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    days = [date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)]
    folder = tmp_path / "stocks" / "twse"
    folder.mkdir(parents=True)
    pq.write_table(pa.table({"date": pa.array([days[0], days[0], days[1]], pa.date32()), "code": ["1101", "2330", "2330"],
                             "high": [23.5, 1010.0, 1020.0], "low": [22.8, 995.0, 0.0]}), folder / "2026.parquet")
    high, low = smc.raw_high_low(tmp_path, days, ["2330.TW", "1101.TW", "9999.TW"],
                                 extra={days[2]: {"2330.TW": (1030.0, 1001.0), "1101.TW": (0, 0)}})
    assert high[0].tolist()[:2] == [1010.0, 23.5] and low[0].tolist()[:2] == [995.0, 22.8]
    assert np.isnan(high[1, 0]) and np.isnan(low[1, 0])            # a low of 0 is no bar
    assert (high[2, 0], low[2, 0]) == (1030.0, 1001.0)             # the day the files do not have yet
    assert np.isnan(high[:, 2]).all() and np.isnan(high[2, 1])


def test_the_bars_are_adjusted_and_a_missing_high_or_low_falls_back_to_the_open_and_close(tmp_path):
    class Panel:
        sessions = [date(2026, 11, 19), date(2026, 11, 20)]
        symbols = ["2330.TW"]
        close = np.array([[100.0, 110.0]])
        adjusted = np.array([[200.0, 220.0]])           # twice the raw price: dividends reinvested
        open = np.array([[98.0, np.nan]])
        notes = {"extra_bars": {date(2026, 11, 19): {"2330.TW": (104.0, 97.0)}}}

    open_, high, low, close = smc.bars(Panel, tmp_path)
    assert (open_[0, 0], high[0, 0], low[0, 0], close[0, 0]) == (196.0, 208.0, 194.0, 200.0)
    assert (open_[1, 0], high[1, 0], low[1, 0], close[1, 0]) == (220.0, 220.0, 220.0, 220.0)


def test_the_factors_are_registered_everywhere_a_rule_factor_is():
    for name in smc.SMC_FACTORS:
        assert name in daily.RULE_FACTORS and name in scan.SHORT
        assert categories.FACTOR_FAMILY[name] == "價格行為 SMC"
        assert name not in scan.signals()                # not in the searches before its strength is judged
        assert name not in daily.FACTOR_LABELS           # no model, robot or earlier search list changes
    assert "價格行為 SMC" in categories.FAMILY_ORDER and "價格行為 SMC" in categories.FAMILY_HOW
    assert categories.classify({"factors": {"smc_setup_10": 1.0}})["family"] == "價格行為 SMC"


def test_the_batch_is_every_factor_alone_and_with_the_trend_in_the_three_holding_styles():
    rules = daily.BATCHES["smc"]()
    assert len(rules) == len(smc.SMC_FACTORS) * 2 * 3 and len({rule.name for rule in rules}) == len(rules)
    for rule in rules:
        assert rule.eligibility == "entry" and rule.check == "daily" and rule.core == 0 and rule.industry_cap == 0.3
        assert sum(name in smc.SMC_FACTORS for name in rule.factors) == 1
        assert set(rule.factors) - set(smc.SMC_FACTORS) <= {"trend_200"}
    assert {(rule.hold, rule.min_hold) for rule in rules} == {("days", 20), ("trend", 0), ("days", 0)}


def test_the_after_close_tables_candle_reaches_the_panel(tmp_path):
    """The session the stock files do not have yet: its high and low ride in the data's notes."""
    import inspect

    from quant_platform.research import stock_rules

    source = inspect.getsource(stock_rules.load_stock_data)
    assert 'extra_bars.setdefault(extra_day, {})[symbol]' in source and '"extra_bars": extra_bars' in source
