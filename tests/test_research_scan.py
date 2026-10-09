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
    industry = np.array([hash(fp.industry(symbol)) % 97 for symbol in fp.symbols])
    _returns, _entries, history = scan.account(score, np.ones(len(days), dtype=bool), industry,
                                              np.zeros_like(score), None, 0.0, 0.0, record=True)
    for index, day in enumerate(days[:-1]):
        assert sorted(fp.symbols[row] for row in history[index]) == sorted(engine[day])


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
    assert len(items) == 12 and {item["check"] for item in items} == {"daily"}
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
    assert len(names) == 39 and not set(scan.LEFT_OUT) & set(names) and set(names) <= set(scan.SHORT)
    items = scan.candidates()
    assert len(items) == (39 + 39 * 38 // 2 + 39 * 38 * 37 // 6) * 4
    rules = [scan.to_rule(item) for item in items[::37]]                  # names fit (≤ 80) and parse
    assert all(rule.core == 0 and rule.top == 20 and not uses_0050(rule.canonical()) for rule in rules)
    assert scan.to_rule({"factors": ["ml_gbm_60", "trend_200"], "universe": "large", "weighting": "equal",
                         "check": "weekly"}).large_caps == 100
