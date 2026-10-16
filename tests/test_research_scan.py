"""2026-10-10: the broad two-stage strategy search (research/scan.py)."""

from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import scan
from quant_platform.research.costs import CostModel
from quant_platform.research.daily import DailyRule, FactorPanel, daily_rankings
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel

FREE = CostModel(fee_rate=0.0, minimum_fee=0, slippage_bps=0.0)


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def planted(stocks=40, seed=2):
    """Each stock keeps its own drift: past winners keep winning, so momentum is the planted signal."""
    rng = np.random.default_rng(seed)
    days = weekdays(date(2014, 1, 1), date(2020, 12, 31))
    drifts = np.linspace(-0.0015, 0.0025, stocks)
    closes, industries = {}, {}
    for number, drift in enumerate(drifts):
        series = 20 * np.cumprod(1 + drift + rng.normal(0, 0.012, len(days)))
        code = str(1101 + number)
        closes[f"{code}.TW"] = dict(zip(days, np.maximum(series, 10.5)))
        industries[code] = f"產業{number % 4}"
    closes["0050.TW"] = dict(zip(days, 50 * np.cumprod(1 + rng.normal(0.0003, 0.008, len(days)))))
    data = LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 50_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})
    return data, FactorPanel(Panel(data), industries)


def test_the_fast_account_picks_what_the_engine_picks():
    data, fp = planted()
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=scan.TOP, keep=scan.KEEP, min_hold=scan.MIN_HOLD,
                     industry_cap=scan.INDUSTRY_SHARE)
    days = [day for day in fp.sessions if date(2016, 1, 4) <= day <= date(2016, 12, 30)]
    first, last = fp.index[days[0]], fp.index[days[-1]]
    engine = daily_rankings(fp, rule, days[0], days[-1])
    values = fp.matrix("momentum_3")[:, first:last + 1]
    eligible = np.column_stack([fp.eligible(rule, position) for position in range(first, last + 1)])
    score = np.where(eligible, values, np.nan).astype(np.float32)
    groups = sorted({fp.industry(symbol) for symbol in fp.symbols})
    industry = np.array([groups.index(fp.industry(symbol)) for symbol in fp.symbols])
    _returns, _entries, history = scan.account(score, np.ones(len(days), dtype=bool), industry,
                                              np.zeros_like(score), None, 0.0, 0.0, record=True)
    for index, day in enumerate(days[:-1]):
        assert sorted(fp.symbols[row] for row in history[index]) == sorted(engine[day])


def test_the_fast_account_holds_by_the_trend_as_the_engine_does():
    """scan 1.2.0: hold "trend" — out of the top 60, kept while above the 20-session average."""
    data, fp = planted()
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=scan.TOP, keep=scan.KEEP,
                     industry_cap=scan.INDUSTRY_SHARE, hold="trend")
    days = [day for day in fp.sessions if date(2016, 1, 4) <= day <= date(2016, 12, 30)]
    first, last = fp.index[days[0]], fp.index[days[-1]]
    engine = daily_rankings(fp, rule, days[0], days[-1])
    values = fp.matrix("momentum_3")[:, first:last + 1]
    eligible = np.column_stack([fp.eligible(rule, position) for position in range(first, last + 1)])
    score = np.where(eligible, values, np.nan).astype(np.float32)
    groups = sorted({fp.industry(symbol) for symbol in fp.symbols})
    industry = np.array([groups.index(fp.industry(symbol)) for symbol in fp.symbols])
    _returns, _entries, history = scan.account(score, np.ones(len(days), dtype=bool), industry, np.zeros_like(score),
                                              None, 0.0, 0.0, record=True, rising=fp.rising()[:, first:last + 1])
    for index, day in enumerate(days[:-1]):
        assert sorted(fp.symbols[row] for row in history[index]) == sorted(engine[day])
    item = scan.candidates(["momentum_3"], largest=1)[0]
    assert item["hold"] == "trend" and scan.to_rule(item).hold == "trend" and scan.to_rule(item).min_hold == 0


def test_costs_and_returns_by_hand():
    score = np.array([[3.0, 3.0, 3.0], [2.0, np.nan, 2.0]] + [[np.nan] * 3] * 2, dtype=np.float32)
    moves = np.array([[0.10, 0.0, 0.0], [0.02, 0.0, 0.0], [0, 0, 0], [0, 0, 0]], dtype=np.float32)
    returns, entries = scan.account(score, np.array([True, True, True]), np.arange(4), moves, None, 0.001, 0.004)
    # day 0: two names at half each, both bought (0.1% of the account): 6% - 0.1%; day 1: the second is not
    # ranked any more (not eligible, as in the engine): sold, 0.4% on its half
    assert returns == pytest.approx([0.5 * 0.10 + 0.5 * 0.02 - 0.001, -0.5 * 0.004])
    assert entries == 2


def test_the_screen_finds_the_planted_signal_and_finalists_differ(tmp_path):
    data, fp = planted()
    arrays = scan.prepare(data, fp, FREE, tmp_path / "arrays")
    assert arrays.days[0] >= date(2015, 6, 1) and arrays.days[-2] <= scan.DEV_END
    items = scan.candidates(["momentum_6", "reversal_1", "trend_200"], largest=1)
    assert len(items) == 24 and {item["check"] for item in items} == {"daily", "weekly"}   # 1.3.0: both frequencies
    rows = scan.screen(tmp_path / "arrays", items, FREE, 0, tmp_path)
    best = max((row for row in rows if row["universe"] == "all"), key=lambda row: row["excess"])
    worst = min((row for row in rows if row["universe"] == "all"), key=lambda row: row["excess"])
    assert best["factors"] != ["reversal_1"] and worst["factors"] == ["reversal_1"]
    assert all(row["entries_per_month"] == 0 for row in rows if row["universe"] == "large")   # no market values here
    chosen = scan.finalists(arrays, rows, FREE, 5)
    assert all(scan.passes(row) for row in chosen)
    assert len({tuple(row["factors"]) + (row["weighting"],) for row in chosen}) == len(chosen)


def test_the_space_is_wide_all_stock_and_every_rule_is_valid():
    from quant_platform.research.categories import uses_0050

    names = scan.signals()
    count = len(names)
    assert count >= 40 and not set(scan.LEFT_OUT) & set(names) and set(names) <= set(scan.SHORT)
    items = scan.candidates()
    assert len(items) == (count + count * (count - 1) // 2 + count * (count - 1) * (count - 2) // 6) * 8   # 1.3.0: daily and weekly
    required = scan.candidates(require="ml_gbm_quality")
    assert required and all("ml_gbm_quality" in item["factors"] for item in required)
    assert len(required) == (1 + (count - 1) + (count - 1) * (count - 2) // 2) * 8
    rules = [scan.to_rule(item) for item in items[::37]]                  # names fit (≤ 80) and parse
    assert all(rule.core == 0 and rule.top == 20 and not uses_0050(rule.canonical()) for rule in rules)
    assert scan.to_rule({"factors": ["ml_gbm_60", "trend_200"], "universe": "large", "weighting": "equal",
                         "check": "weekly"}).large_caps == 100


def test_a_later_scan_skips_near_copies_of_earlier_finalists(tmp_path):
    data, fp = planted()
    arrays = scan.prepare(data, fp, FREE, tmp_path / "arrays")
    items = scan.candidates(["momentum_6", "momentum_12_1", "trend_200"], largest=2, smallest=2)
    assert len(items) == 3 * 8 and all(len(item["factors"]) == 2 for item in items)
    rows = scan.screen(tmp_path / "arrays", items, FREE, 0, tmp_path)
    alone = scan.finalists(arrays, rows, FREE, 10)
    assert alone
    again = scan.finalists(arrays, rows, FREE, 10, seeds=[{key: row[key] for key in ("factors", "universe", "weighting", "check")}
                                                       for row in alone])
    assert again == []                                   # each one is a copy of a seed (correlation 1)


def test_seeds_with_a_newer_signal_are_skipped_and_a_finished_screen_resumes(tmp_path):
    data, fp = planted()
    arrays = scan.prepare(data, fp, FREE, tmp_path / "arrays")
    items = scan.candidates(["momentum_6", "trend_200"], largest=1)
    rows = scan.screen(tmp_path / "arrays", items, FREE, 0, tmp_path)
    newer = {"factors": ["ml_gbm_quality"], "universe": "all", "weighting": "equal", "check": "weekly"}
    assert scan.finalists(arrays, rows, FREE, 5, seeds=[newer]) == scan.finalists(arrays, rows, FREE, 5)


def test_effective_trials_count_groups_not_copies(tmp_path):
    rng = np.random.default_rng(0)
    base = rng.normal(0, 0.01, (3, 300))
    first = np.repeat(base, 4, axis=0) + rng.normal(0, 0.0005, (12, 300))     # 12 near-copies of 3 strategies
    np.save(tmp_path / "screen-0.excess.npy", first[:7].astype(np.float16))
    np.save(tmp_path / "screen-1.excess.npy", first[7:].astype(np.float16))
    found = scan.effective_trials(tmp_path, sample=12)
    assert found["screened"] == 12 and found["groups_090"] == 3 and found["components_95"] <= 3


def test_stability_selection_needs_both_halves_and_keeps_excess_apart(tmp_path):
    data, fp = planted()
    arrays = scan.prepare(data, fp, FREE, tmp_path / "arrays")
    items = scan.candidates(["momentum_6", "momentum_12_1", "trend_200", "reversal_1"], largest=1)
    rows = scan.screen(tmp_path / "arrays", items, FREE, 0, tmp_path)
    assert all({"first_half", "second_half", "worst_1y"} <= set(row) for row in rows)
    chosen = scan.finalists(arrays, rows, FREE, 10, stable=True)
    assert chosen and all(row["first_half"] > 0 and row["second_half"] > 0 for row in chosen)
    assert [row["factors"] for row in chosen] != [["reversal_1"]]


def test_an_old_scan_gets_its_effective_trials_from_a_sample(tmp_path):
    import json

    data, fp = planted()
    folder = tmp_path / "scans" / "scan-1.0.0-x"
    scan.prepare(data, fp, FREE, folder / "arrays")
    items = scan.candidates(["momentum_6", "momentum_12_1", "trend_200", "reversal_1"], largest=2)
    rows = scan.screen(folder / "arrays", items, FREE, 0, folder)
    (folder / "screen-0.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    found = scan.sample_effective(folder, FREE, sample=20, workers=0)
    assert found["screened"] == len(rows) and 1 <= found["groups_090"] <= found["sample"] <= 20
    total = scan.all_effective(tmp_path)
    assert total == {"scans": 1, "groups_090": found["groups_090"], "screened": len(rows)}


def test_an_equal_screen_score_goes_to_the_more_liquid_stock():
    """scan 1.2.1 (2026-10-15): the screen broke ties by stock code too."""
    import numpy as np

    from quant_platform.research import scan

    stocks, sessions = 30, 3
    score = np.full((stocks, sessions), 0.5)                          # every stock equal
    liquidity = np.tile(np.arange(stocks, dtype=float)[:, None], (1, sessions))   # later rows more liquid
    _returns, _entries, history = scan.account(score, np.ones(sessions, dtype=bool), np.arange(stocks), np.zeros((stocks, sessions)),
                                               None, 0.0, 0.0, record=True, liquidity=liquidity)
    assert sorted(history[0]) == list(range(stocks - scan.TOP, stocks))
    _returns, _entries, plain = scan.account(score, np.ones(sessions, dtype=bool), np.arange(stocks), np.zeros((stocks, sessions)),
                                             None, 0.0, 0.0, record=True)
    assert sorted(plain[0]) == list(range(scan.TOP))                  # without it, the first rows (the old way)
