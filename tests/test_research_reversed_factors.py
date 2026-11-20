"""2026-11-21 (docs/research/results.md「投機的特徵反過來用」): seven factors whose bottom fifth was ahead of the average
stock after 2020-10, each its factor the other way round; the novelty gate does not hold one against its own factor."""

from datetime import date, timedelta

import numpy as np

from quant_platform.research import daily
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


def panel(end=date(2016, 12, 30)):
    rng = np.random.default_rng(7)
    days = weekdays(date(2014, 1, 1), end)
    closes = {f"{1101 + n}.TW": dict(zip(days, np.maximum(20 * np.cumprod(1 + rng.normal(0.0003, 0.02, len(days))), 10.5)))
              for n in range(40)}
    closes["0050.TW"] = {day: 100.0 for day in days}
    data = LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 50_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={}, opens={})
    return FactorPanel(Panel(data))


def test_each_reversed_factor_is_its_factor_the_other_way_round():
    from quant_platform.research.categories import FACTOR_FAMILY
    from quant_platform.research.scan import SHORT, signals

    fp = panel()
    assert set(daily.REVERSED_OF) == set(daily.REVERSED_FACTORS)
    for name, base in daily.REVERSED_OF.items():
        assert base in daily.FACTOR_LABELS and name in daily.RULE_FACTORS and name not in daily.FACTOR_LABELS
        assert SHORT[name] and FACTOR_FAMILY[name]
        if name in ("high_day_trade_20", "industry_leader_5", "high_ivol_60"):
            continue                       # chips, industries, the market: by hand below where the panel has them
        mine, theirs = fp.matrix(name), fp.matrix(base)
        assert np.isfinite(mine).sum() > 1000
        assert np.array_equal(np.isnan(mine), np.isnan(theirs))
        assert np.allclose(mine[np.isfinite(mine)], -theirs[np.isfinite(theirs)])
    # not in the searches until the strength analysis and the novelty gate say so (scan.JUDGED_IN)
    # 11/21 second step: ahead of the industry, the one of its group, passed the gate counting the same way only
    assert set(daily.REVERSED_OF) & set(signals()) == {"industry_leader_5"}
    # 1-month momentum by hand: the return over the last 21 sessions
    column = 300
    prices = fp._prices.to_numpy(dtype=float)
    assert np.isclose(fp.matrix("momentum_1m")[0, column], prices[column, 0] / prices[column - 21, 0] - 1, rtol=1e-5)


def test_the_novelty_gate_skips_a_reversed_factor_s_own_factor(monkeypatch):
    from quant_platform.research import scan

    fp = panel(date(2020, 9, 30))
    monkeypatch.setattr(scan, "signals", lambda: ["low_volatility_60", "momentum_3", "trend_200"])
    gate = scan.novelty(fp, ["high_vol_60", "momentum_1m"])
    assert gate["high_vol_60"]["with"] != "low_volatility_60"           # −1 against its own factor, by construction
    assert abs(gate["high_vol_60"]["max_correlation"]) < 0.99
    assert gate["momentum_1m"]["with"] in ("momentum_3", "low_volatility_60", "trend_200")
    assert set(gate["high_vol_60"]["among"]) == {"momentum_1m"}         # the new ones against each other, to read
    # the original against its reversed one, the other way: skipped as well
    monkeypatch.setattr(scan, "signals", lambda: ["high_vol_60", "momentum_3"])
    assert scan.novelty(fp, ["low_volatility_60"])["low_volatility_60"]["with"] != "high_vol_60"


def test_a_reversed_factor_reads_what_its_factor_reads(tmp_path):
    from quant_platform.research.chips import CHIP_FACTORS
    from quant_platform.research.dashboard import source_groups

    groups = {key: names for key, _label, _paid, names in source_groups()}
    for name, base in daily.REVERSED_OF.items():
        for key, names in groups.items():
            assert (name in names) == (base in names), (name, key)
    if "low_day_trade_20" in CHIP_FACTORS:
        assert "high_day_trade_20" in groups["chips"]
    assert daily.input_digests(tmp_path, ["momentum_1m"], False) == daily.input_digests(tmp_path, ["reversal_1"], False)


def test_the_signed_gate_counts_only_the_same_way(monkeypatch):
    """2026-11-21 (第二步, written before the run): the searches add signals as they are, so a signal ranking the
    stocks the other way from an existing one is no duplicate there; one ranking them the same way still is."""
    from quant_platform.research import scan

    fp = panel(date(2020, 9, 30))
    usual = fp.matrix

    def matrix(name):
        if name == "flipped":
            return -usual("momentum_3")
        if name == "copied":
            return usual("momentum_3") * 2.0
        return usual(name)

    monkeypatch.setattr(fp, "matrix", matrix)
    monkeypatch.setattr(scan, "signals", lambda: ["momentum_3", "trend_200"])
    plain = scan.novelty(fp, ["flipped"])["flipped"]
    assert plain["with"] == "momentum_3" and plain["max_correlation"] < -0.99 and plain["kept"] is False
    signed = scan.novelty(fp, ["flipped", "copied"], signed=True)
    assert signed["flipped"]["kept"] is True and signed["flipped"]["opposite_with"] == "momentum_3"
    assert signed["flipped"]["most_opposite"] < -0.99 and signed["flipped"]["max_correlation"] < 0.7
    assert signed["copied"]["kept"] is False and signed["copied"]["with"] == "momentum_3"
