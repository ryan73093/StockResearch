"""Smart Money Concepts as daily factors (2026-11-22; 使用者：SMC加到策略研究裡)."""
from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import categories, daily, model, scan, smc

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


# ---- 2026-11-25 (使用者：做第二版; docs/research/results.md「SMC 第二版」): the structure as a state, the weekly
# structure, the vetoes by a signal in force, model W, the weekly batch


def test_the_structure_is_a_state_bullish_after_a_break_upward_bearish_after_a_close_under_the_swing_low():
    found = _run(BARS + TURN)
    state, bearish = found["smc_structure"][:, 0], found["smc_bearish"][:, 0]
    assert np.isnan(state[8]) and state[9] == 1.0 and state[14] == 1.0      # no break yet; then bullish
    assert state[15] == -1.0 and bearish[15] == 1.0                         # the close under the swing low
    assert state[16] == 1.0 and np.isnan(bearish[16]) and np.isnan(bearish[14])
    assert smc.measured("smc_bearish", bearish)[14] == 0.0 and np.isnan(smc.measured("smc_structure", state)[8])


def _weekdays(start: date, count: int) -> list[date]:
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def test_a_weekly_bar_is_the_weeks_first_open_highest_high_lowest_low_and_last_close():
    days = [date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20), date(2026, 11, 23), date(2026, 11, 24)]
    weeks = smc.week_numbers(days)
    assert weeks.tolist() == [0, 0, 0, 1, 1]
    nan = np.nan
    open_ = np.array([[10.0, nan], [11.0, 5.0], [12.0, nan], [13.0, 6.0], [14.0, 7.0]])
    high = np.array([[10.5, nan], [11.9, 5.5], [12.2, nan], [13.5, 6.5], [14.5, 7.5]])
    low = np.array([[9.5, nan], [10.8, 4.5], [11.5, nan], [12.5, 5.5], [13.5, 6.5]])
    close = np.array([[10.2, nan], [11.5, 5.2], [12.0, nan], [13.2, 6.2], [14.2, 7.2]])
    week_open, week_high, week_low, week_close = smc.weekly_bars(open_, high, low, close, weeks)
    assert week_open.tolist() == [[10.0, 5.0], [13.0, 6.0]] and week_high.tolist() == [[12.2, 5.5], [14.5, 7.5]]
    assert week_low.tolist() == [[9.5, 4.5], [12.5, 5.5]] and week_close.tolist() == [[12.0, 5.2], [14.2, 7.2]]
    assert smc.week_numbers([date(2026, 2, 13), date(2026, 2, 23)]).tolist() == [0, 2]     # a week without a session


def _walk(sessions: int = 700, symbols: int = 8):
    generator = np.random.default_rng(5)
    close = 50 * np.cumprod(1 + generator.normal(0.0004, 0.02, (sessions, symbols)), axis=0)
    open_ = close * (1 + generator.normal(0, 0.008, close.shape))
    high = np.maximum(open_, close) * (1 + np.abs(generator.normal(0, 0.006, close.shape)))
    low = np.minimum(open_, close) * (1 - np.abs(generator.normal(0, 0.006, close.shape)))
    return open_, high, low, close, _weekdays(date(2023, 1, 2), sessions)


def test_the_weekly_structure_reads_the_weeks_before_the_sessions_own_and_a_cut_in_mid_week_changes_nothing():
    open_, high, low, close, days = _walk()
    daily_values = smc.structure(open_, high, low, close)
    whole = smc.weekly_structure(open_, high, low, close, days, daily_values)
    assert set(whole) == set(smc.SMC_V2) - {"smc_structure", "smc_bearish"}
    weeks = smc.week_numbers(days)
    by_week = smc.structure(*smc.weekly_bars(open_, high, low, close, weeks), swing=smc.WEEK_SWING,
                            break_window=smc.WEEK_BREAK, warmup=smc.WEEK_WARMUP)
    for name in whole:
        assert np.isfinite(whole[name]).any(), name
    for t in (300, 301, 302, 303, 304, 655):                # every weekday of one week, and a later one
        assert np.array_equal(whole["smc_w_structure"][t], by_week["smc_structure"][weeks[t] - 1], equal_nan=True)
        assert np.array_equal(whole["smc_w_break_up_8"][t], by_week["smc_break_up_20"][weeks[t] - 1], equal_nan=True)
        part_daily = smc.structure(open_[:t + 1], high[:t + 1], low[:t + 1], close[:t + 1])
        part = smc.weekly_structure(open_[:t + 1], high[:t + 1], low[:t + 1], close[:t + 1], days[:t + 1], part_daily)
        for name in whole:
            assert np.array_equal(whole[name][t], part[name][t], equal_nan=True), (name, t)
    assert np.isnan(whole["smc_w_structure"][:5]).all()     # the first week has no week before it
    bullish = whole["smc_w_structure"] == 1                 # the daily signals only where the week's structure is bullish
    assert np.array_equal(np.isfinite(whole["smc_break_up_w"]), bullish & np.isfinite(daily_values["smc_break_up_20"]))
    assert np.array_equal(np.isfinite(whole["smc_fvg_retest_w"]), bullish & np.isfinite(daily_values["smc_fvg_retest_10"]))


def test_the_second_versions_factors_are_registered_and_measured_as_events():
    for name in smc.SMC_V2:
        assert name in daily.RULE_FACTORS and name in scan.SHORT and name not in daily.FACTOR_LABELS
        assert categories.FACTOR_FAMILY[name] == "價格行為 SMC" and name not in scan.signals()
    assert set(smc.EVENTS_V2) < set(smc.SMC_V2) and not set(smc.SMC_V2) & set(smc.SMC_FACTORS)
    values = np.array([[np.nan, 0.5]])
    for name in smc.EVENTS_V2:
        assert smc.measured(name, values).tolist() == [[0.0, 0.5]]
    assert np.isnan(smc.measured("smc_w_structure", values)[0, 0])


def test_a_signal_veto_keeps_out_the_stocks_with_the_signal_in_force_and_is_part_of_the_rules_input():
    class Panel:
        symbols = ["1101.TW", "2330.TW", "2454.TW"]

        def matrix(self, factor):
            assert factor == "smc_sweep_low_10"
            return np.array([[np.nan, 0.4], [0.9, np.nan], [np.nan, np.nan]], dtype=np.float32)

    assert daily.FactorPanel.signal_vetoed(Panel(), 0, "smc_sweep_low_10") == {"2330.TW"}
    assert daily.FactorPanel.signal_vetoed(Panel(), 1, "smc_sweep_low_10") == {"1101.TW"}
    assert daily.SIGNAL_VETOES == {"k1": "smc_sweep_low_10", "k2": "smc_bearish", "k2x": "smc_bearish"}
    assert not set(daily.SIGNAL_VETOES) & set(daily.VETO_FACTORS) and set(daily.SIGNAL_VETOES) <= set(daily.VETO_WORDS)
    for code, factor in daily.SIGNAL_VETOES.items():
        rule = daily.DailyRule(name="x", factors={"momentum_3": 1.0}, exclude=code)
        assert daily.rule_inputs(rule) == ["momentum_3", factor] and rule.canonical()["exclude"] == code
        plain = daily.DailyRule(name="x", factors={"momentum_3": 1.0})
        assert rule.rule_hash != plain.rule_hash


def _events(folder, name: str, rows: dict) -> None:
    import json

    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(json.dumps({"factors": {
        factor: {"periods": {"before_regime": {"excess_20": first}, "regime": {"excess_20": second}}}
        for factor, (first, second) in rows.items()}}), encoding="utf-8")


def test_an_event_factors_verdict_needs_both_periods_and_the_newest_study_counts(tmp_path):
    folder = tmp_path / "instance" / "research" / "factors"
    assert daily.smc_event_verdicts(folder) == {}
    edge = 0.02 * 20 / 252                                  # 2 points a year over 20 sessions
    _events(folder, "events-20261122-171546.json", {"smc_sweep_low_10": (-0.003, -0.006), "smc_bearish": (-0.01, -0.01),
                                                    "smc_break_up_w": (edge * 1.01, edge * 1.01)})
    _events(folder, "events-20261125-220000.json", {"smc_bearish": (-0.01, 0.001), "smc_w_break_up_8": (0.01, edge * 0.9)})
    assert daily.smc_event_verdicts(folder) == {"smc_sweep_low_10": "反向", "smc_bearish": "弱", "smc_break_up_w": "強",
                                                "smc_w_break_up_8": "弱"}


def test_the_weekly_batch_is_the_first_versions_frames_once_a_week_and_the_strong_weekly_factors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rules = daily.BATCHES["smc2"]()
    assert len(rules) == len(smc.SMC_FACTORS) * 2 * 2 and len({rule.name for rule in rules}) == len(rules)
    for rule in rules:
        assert rule.check == "weekly" and rule.eligibility == "entry" and rule.industry_cap == 0.3 and rule.core == 0
        assert (rule.hold, rule.min_hold) in {("days", 20), ("trend", 0)}
    first = {rule.rule_hash for rule in daily.BATCHES["smc"]()}
    assert not first & {rule.rule_hash for rule in rules}
    _events(tmp_path / "instance" / "research" / "factors", "events-20261125-220000.json",
            {"smc_break_up_w": (0.01, 0.01), "smc_w_break_up_8": (0.01, -0.01), "smc_sweep_low_10": (-0.01, -0.01),
             "smc_bearish": (0.0, -0.01)})
    more = daily.BATCHES["smc2"]()
    assert len(more) == len(rules) + 4
    assert {name for rule in more[len(rules):] for name in rule.factors} == {"smc_break_up_w", "trend_200"}
    # the vetoes: only the signal the event study calls reversed, on the 44 rules
    asked = []
    monkeypatch.setattr(daily, "_veto_rules", lambda pairs: asked.extend(pairs) or [])
    daily.BATCHES["smcveto"]()
    assert {code for _trial, code in asked} == {"k1"}
    assert len(asked) == sum(len(group) for group in daily.BETA_GUARD.values()) == 44
    # the second step: the structure veto alone on the 24 deep-learning rules the first step never touched
    asked.clear()
    daily.BATCHES["smcveto2"]()
    assert asked == [(trial, "k2") for trial in daily.CASH_VETO_DEEP] and len(asked) == 24
    assert not set(daily.CASH_VETO_DEEP) & {trial for group in daily.BETA_GUARD.values() for trial in group}


def test_hold_structure_keeps_a_holding_while_its_structure_is_bullish_and_sells_the_session_it_is_not():
    """2026-11-26 (SMC 第三版): SMC's own exit. Out of the keep zone — or its signal lapsed, with eligibility "entry" —
    a holding stays while the daily structure is bullish, and is sold the session it is not, whatever the rule's
    decision days."""
    from tests.test_research_daily import MarketPanel, weekdays

    days = weekdays(date(2024, 1, 1), date(2024, 1, 12))               # two weeks: decisions on the Mondays
    script = {day: ["D", "E", "F", "A", "B", "C"] for day in days}
    script[days[0]] = ["A", "B", "C"]

    class Structured(MarketPanel):
        def structure_up(self):
            state = np.zeros((len(self.symbols), len(days)), dtype=bool)
            state[self.row["A"], :] = True                             # bullish throughout
            state[self.row["B"], :3] = True                            # turns bearish on the Thursday (session 3)
            return state                                               # C: never had a structure

    panel = Structured(days, script, "ABCDEF", rising="C")             # above its average does not keep C
    rule = daily.DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, keep=1, check="weekly", min_hold=20,
                           hold="structure")
    assert rule.min_hold == 0 and rule.canonical()["hold"] == "structure"
    assert daily.rule_inputs(rule) == ["momentum_3", "smc_structure"]
    ranks = daily.daily_rankings(panel, rule, days[0], days[-1])
    assert ranks[days[0]] == ["A", "B", "C"]
    assert ranks[days[1]] == ["A", "B"]                                # C sold the next session: no bullish structure
    assert ranks[days[3]] == ["A"]                                     # B sold the Thursday its structure turned
    assert ranks[days[5]] == ["A", "D", "E"]                           # the next Monday fills the places; A stays
    trend = daily.DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, keep=1, check="weekly", hold="trend")
    assert daily.daily_rankings(panel, trend, days[0], days[1])[days[1]] == ["C"]     # the other state hold: its own
    plain = daily.DailyRule(name="x", factors={"momentum_3": 1.0})
    assert "hold" not in plain.canonical() and daily.rule_inputs(plain) == ["momentum_3"]


def test_structure_up_is_the_bullish_state_of_the_daily_structure():
    class Panel:
        _cache: dict = {}

        def matrix(self, factor):
            assert factor == daily.STRUCTURE_FACTOR == "smc_structure"
            return np.array([[np.nan, 1.0, -1.0], [1.0, 1.0, np.nan]], dtype=np.float32)

    assert daily.FactorPanel.structure_up(Panel()).tolist() == [[False, True, False], [True, True, False]]


def test_a_rule_holding_by_the_structure_says_so_wherever_its_settings_are_read():
    from quant_platform.research import holding, pool

    rule = daily.DailyRule(name="x", factors={"smc_break_up_20": 1.0}, eligibility="entry", hold="structure")
    spec = rule.canonical()
    assert holding.style_of(spec) is None                              # not one of the three styles compared
    assert "持有：結構轉空才賣" in categories.classify(spec)["traits"]
    assert any("結構轉空" in line for line in pool.describe_daily(spec))
    sell = next(row for section in pool.settings_sections(spec) for row in section["rows"] if row["item"] == "什麼時候賣")
    assert "結構轉空才賣" in sell["value"] and "波段低點" in sell["value"]


def test_the_third_versions_batches_are_smcs_own_rules_and_the_44_holding_by_the_structure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rules = daily.BATCHES["smc3"]()
    assert len(rules) == len(smc.SMC_FACTORS) * 2 * 2 and len({rule.name for rule in rules}) == len(rules)
    assert len({rule.rule_hash for rule in rules}) == len(rules)
    for rule in rules:
        assert (rule.hold, rule.min_hold, rule.eligibility, rule.industry_cap) == ("structure", 0, "entry", 0.3)
        assert rule.name.endswith("｜持有：結構轉空才賣") and len(rule.name) <= 80
    assert {rule.check for rule in rules} == {"daily", "weekly"}
    earlier = {rule.rule_hash for name in ("smc", "smc2") for rule in daily.BATCHES[name]()}
    assert not earlier & {rule.rule_hash for rule in rules}
    _events(tmp_path / "instance" / "research" / "factors", "events-20261125-220000.json",
            {"smc_break_up_w": (0.01, 0.01), "smc_w_break_up_8": (0.01, -0.01)})
    assert len(daily.BATCHES["smc3"]()) == len(rules) + 4
    # the 44: each rule's own settings, only the hold changed
    import json
    from types import SimpleNamespace

    from quant_platform.research import registry

    research = tmp_path / "instance" / "research"
    (research / "reports").mkdir(parents=True)
    trials = [trial for group in daily.BETA_GUARD.values() for trial in group]
    stored = []
    for trial in trials:
        base = daily.DailyRule(name=f"深度模型＋外資買超：前 20 名、每週決策、看成本才換股 {trial}｜持有：月線上續抱",
                               factors={"momentum_3": 1.0, "foreign_buy_20": float(1 + trial % 2)}, top=10 + trial % 7,
                               check="weekly", hold="trend", execution="next")
        (research / "reports" / f"{trial}.json").write_text(json.dumps({"spec": base.canonical()}), encoding="utf-8")
        stored.append(SimpleNamespace(trial_id=trial, spec_name=base.name, report_file=f"{trial}.json"))
    monkeypatch.setattr(registry, "TrialRegistry", lambda path: SimpleNamespace(records=lambda: stored))
    made = daily.BATCHES["smcexit"]()
    assert len(made) == 44 and len({rule.name for rule in made}) == 44
    for trial, rule in zip(trials, made):
        assert (rule.hold, rule.min_hold, rule.execution, rule.check) == ("structure", 0, "next", "weekly")
        assert f"#{trial} 的設定" in rule.name and rule.name.endswith("｜持有：結構轉空才賣") and len(rule.name) <= 80
        assert "月線上續抱" not in rule.name and rule.top == 10 + trial % 7


def test_model_w_is_model_k_with_the_smc_readings(tmp_path, monkeypatch):
    import json

    features = model.version_features("gbm-2.18.0")
    assert features == model.version_features("gbm-2.7.0") + model.SMC_FEATURES_W
    assert len(model.SMC_FEATURES_W) == 9 and len(set(features)) == len(features)
    assert set(model.SMC_FEATURES_W) == set(smc.SMC_FACTORS) | {"smc_structure", "smc_w_structure"}
    assert model.MODELS["ml_gbm_w"]["version"] == "gbm-2.18.0" and "target" not in model.MODELS["ml_gbm_w"]
    assert model.DOWN_GATES["gbm-2.18.0"] == "gbm-2.7.0"
    assert "ml_gbm_w" in daily.MODEL_FACTORS and scan.SHORT["ml_gbm_w"] == "模型W"
    monkeypatch.chdir(tmp_path)
    assert daily.BATCHES["modelw"]() == []                           # not trained: no rules
    folder = tmp_path / "instance" / "research" / "models" / "gbm-2.18.0"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({"down_gate": {"passed": False, "top20_gain": 0.012,
                                                               "against_top20_gain": 0.013}}), encoding="utf-8")
    assert daily.BATCHES["modelw"]() == []
    assert "ml_gbm_w" in scan.failed_models(tmp_path / "instance" / "research" / "models")
    (folder / "meta.json").write_text(json.dumps({"down_gate": {"passed": True}}), encoding="utf-8")
    rules = daily.BATCHES["modelw"]()
    assert len(rules) == 3 and all(rule.factors == {"ml_gbm_w": 1.0} for rule in rules)


def test_a_model_reads_an_smc_event_without_a_signal_as_zero_not_as_unknown():
    class Panel:
        symbols = ["a", "b", "c", "d"]

        def matrix(self, name):
            return np.array([[np.nan], [0.5], [np.nan], [1.0]], dtype=np.float32)      # symbols × sessions

    eligible = np.array([[True], [True], [True], [False]])
    block = model.feature_block(Panel(), [0], eligible, ("smc_sweep_low_10", "smc_discount"))
    event, place = block[0, :, 0], block[0, :, 1]
    assert np.isfinite(event[:3]).all() and event[1] > event[0] == event[2] and np.isnan(event[3])
    assert np.isnan(place[0]) and np.isfinite(place[1])              # a place in the leg stays unknown outside it


def test_timing_one_stock_is_in_after_the_entry_and_out_after_the_structure_turns():
    """2026-11-27 (SMC 第四版 (g)): a policy says after each close whether it wants the stock; the order fills at the
    next session's open, the costs on that day; a session without a trade changes nothing."""
    nan = np.nan
    found = {"smc_structure": np.array([[nan], [1.0], [1.0], [1.0], [-1.0], [-1.0], [1.0]], dtype=np.float32),
             "smc_w_structure": np.array([[1.0], [1.0], [-1.0], [1.0], [1.0], [1.0], [1.0]], dtype=np.float32),
             "smc_ob_retest_10": np.array([[nan], [nan], [1.0], [0.9], [nan], [1.0], [nan]], dtype=np.float32),
             "smc_fvg_retest_10": np.full((7, 1), nan, dtype=np.float32),
             "smc_setup_10": np.full((7, 1), nan, dtype=np.float32),
             "smc_sweep_low_10": np.array([[nan], [nan], [nan], [nan], [1.0], [0.9], [nan]], dtype=np.float32)}
    assert smc.timing_wants(found, "structure")[:, 0].tolist() == [False, True, True, True, False, False, True]
    assert smc.timing_wants(found, "structure_w")[:, 0].tolist() == [False, True, False, True, False, False, True]
    # the retest inside a bullish structure starts it (session 2), the bearish structure ends it; the retest of
    # session 5 is in a bearish structure and starts nothing
    assert smc.timing_wants(found, "block")[:, 0].tolist() == [False, False, True, True, False, False, False]
    assert smc.timing_wants(found, "sweep")[:, 0].tolist() == [False, False, False, False, True, True, False]
    assert not smc.timing_wants(found, "gap").any() and set(smc.TIMING_POLICIES) >= set(smc.TIMING_ENTRIES)
    open_ = np.array([[10.0], [10.0], [11.0], [nan], [12.0], [9.0], [9.5]])
    close = np.array([[10.0], [10.5], [11.5], [nan], [12.5], [9.2], [9.8]])
    wants = smc.timing_wants(found, "structure")
    returns, inside, entered = smc.timing_returns(open_, close, wants, buy=0.01, sell=0.02)
    assert entered[:, 0].tolist() == [False, False, True, False, False, False, False]
    assert inside[:, 0].tolist() == [False, False, True, True, True, True, False]
    assert returns[2, 0] == pytest.approx(11.5 / 11.0 * 0.99 - 1)       # bought at session 2's open
    assert returns[3, 0] == 0.0                                         # no trade: nothing changes
    assert returns[4, 0] == pytest.approx(12.5 / 11.5 - 1)              # held, from the last traded close
    assert returns[5, 0] == pytest.approx(9.0 / 12.5 * 0.98 - 1)        # sold at the open after the bearish close
    assert returns[6, 0] == 0.0 and returns[:2, 0].tolist() == [0.0, 0.0]


def test_the_timing_veto_bars_a_bearish_stock_and_sells_a_holding_the_session_it_turns():
    """2026-11-27 (SMC 第四版 (i)): exclude "k2x" — the rule picks the stock, SMC says when to be in it."""
    from tests.test_research_daily import MarketPanel, weekdays

    days = weekdays(date(2024, 1, 1), date(2024, 1, 12))
    script = {day: ["A", "B", "C", "D", "E", "F"] for day in days}

    class Timed(MarketPanel):
        def signal_vetoed(self, position, factor):
            assert factor == "smc_bearish"
            marked = {"C"}                                             # bearish throughout: never bought
            if position >= self.index[days[3]]:
                marked.add("B")                                        # turns bearish on the Thursday
            return marked

    panel = Timed(days, script, "ABCDEF")
    rule = daily.DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, keep=1, check="weekly", exclude="k2x")
    assert daily.rule_inputs(rule) == ["momentum_3", "smc_bearish"] and rule.canonical()["exclude"] == "k2x"
    ranks = daily.daily_rankings(panel, rule, days[0], days[-1])
    assert ranks[days[0]] == ["A", "B", "D"]                           # C is barred; the next best takes its place
    assert ranks[days[3]] == ["A", "D"]                                # B sold the session it turned, between decisions
    assert ranks[days[5]] == ["A", "D", "E"]                           # the next Monday: B still barred, E bought
    veto = daily.DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, keep=1, check="weekly", exclude="k2")
    kept = daily.daily_rankings(panel, veto, days[0], days[-1])
    assert all("B" in names for names in kept.values())                # "k2" alone never sells a holding
    assert daily.SIGNAL_EXITS == ("k2x",) and "k2x" in daily.VETO_WORDS
    assert "日線結構空方不買、持股轉空就賣" in categories.classify(rule.canonical())["traits"]


def test_the_fourth_versions_batches_are_the_large_stock_watchlist_and_the_timing_of_68_rules(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rules = daily.BATCHES["smc4"]()
    assert len(rules) == len(smc.SMC_FACTORS) * 2 and len({rule.rule_hash for rule in rules}) == len(rules)
    for rule in rules:
        assert (rule.hold, rule.eligibility, rule.top, rule.check) == ("structure", "entry", 10, "daily")
        assert rule.large_caps in (50, 150) and len(rule.factors) == 1 and len(rule.name) <= 80
    asked: list = []
    monkeypatch.setattr(daily, "_veto_rules", lambda pairs: asked.extend(pairs) or [])
    daily.BATCHES["smctiming"]()
    assert {code for _trial, code in asked} == {"k2x"} and len(asked) == 44 + 24
    assert [trial for trial, _code in asked][44:] == list(daily.CASH_VETO_DEEP)


def test_the_index_own_structure_is_a_market_switch(tmp_path, monkeypatch):
    """2026-11-27 (SMC 第五版): market_filter "taiex_smc" holds nothing, vol_scale "smc_half" half, while TAIEX's own
    daily structure is bearish — the last break of the index's bars was a close below a confirmed swing low."""
    from quant_platform.research.daily import DailyRule, FactorPanel, Panel
    from tests.test_research_daily import FREE, market, weekdays

    days = weekdays(date(2023, 1, 2), date(2023, 12, 29))
    data = market(days, {"1101.TW": {day: 10.0 for day in days}})
    wave = np.array([100 + 8 * np.sin(index / 6) + (0.15 * index if index < 130 else 19.5 - 0.5 * (index - 130))
                     for index in range(len(days))])
    fp = FactorPanel(Panel(data), market=wave)
    fp.market_bars = (wave, wave + 1, wave - 1, wave)
    state = fp.market_structure()
    expected = smc.structure(*(column.reshape(-1, 1) for column in fp.market_bars))["smc_structure"][:, 0]
    assert np.array_equal(state, expected, equal_nan=True)
    bearish, bullish = np.flatnonzero(state == -1), np.flatnonzero(state == 1)
    assert len(bearish) > 20 and len(bullish) > 20 and np.isnan(state[0])
    assert fp.risk_on(0, "taiex_smc")                                   # before its first break: not bearish
    assert not fp.risk_on(int(bearish[0]), "taiex_smc") and fp.risk_on(int(bullish[0]), "taiex_smc")
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, vol_scale="smc_half")
    levels = daily.exposure_schedule(data, fp, rule, FREE)
    assert levels[days[int(bearish[0])]] == daily.MARKET_HALF and levels[days[int(bullish[0])]] == 1.0
    assert rule.canonical()["vol_scale"] == "smc_half" and "smc_half" in daily.MARKET_SCALES
    filtered = DailyRule(name="x", factors={"trend_200": 1.0}, market_filter="taiex_smc")
    assert filtered.canonical()["market_filter"] == "taiex_smc"
    from quant_platform.research import pool

    assert "結構轉空" in pool.FILTER_WORDS["taiex_smc"] and "結構轉空" in pool.SCALE_WORDS["smc_half"]
    assert "大盤結構轉空就不持股" in categories.classify(filtered.canonical())["traits"]
    assert "大盤結構轉空減碼一半" in categories.classify(rule.canonical())["traits"]
    # the batch: the 44 with each switch
    asked: list = []
    monkeypatch.setattr(daily, "_guarded", lambda trials, guards: asked.append((trials, guards)) or [])
    daily.BATCHES["smcmarket"]()
    trials, guards = asked[0]
    assert len(trials) == 44 and [fields for _word, fields in guards] == [{"market_filter": "taiex_smc"},
                                                                         {"vol_scale": "smc_half"}]


def test_the_index_bars_are_read_from_the_stored_index_and_fingerprinted(tmp_path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    (tmp_path / "daily").mkdir()
    pq.write_table(pa.table({"date": [days[0], days[2]], "open": [100.0, 103.0], "high": [102.0, 104.0],
                             "low": [99.0, 100.5], "close": [101.0, 102.0]}), tmp_path / "daily" / "TAIEX.parquet")
    open_, high, low, close = daily.market_bars(tmp_path, days)
    assert open_.tolist()[0] == 100.0 and np.isnan(open_[1]) and high.tolist()[2] == 104.0 and low[0] == 99.0
    assert close[2] == 102.0
    first = daily.market_bars_digest(tmp_path, days, days[1])
    assert first == daily.market_bars_digest(tmp_path, days[:2], days[2])   # only the bars to the day asked
    assert first != daily.market_bars_digest(tmp_path, days, days[2])
