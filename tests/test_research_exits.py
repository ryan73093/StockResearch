"""R15 C1b (2026-10-07): the learned exit for each holding."""

import pickle
from datetime import date, timedelta

import numpy as np
import pytest
from sklearn.dummy import DummyRegressor

from quant_platform.research import exits
from quant_platform.research.daily import DailyRule, FactorPanel, daily_rankings
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def market(stocks=12, seed=5):
    rng = np.random.default_rng(seed)
    days = weekdays(date(2013, 1, 1), date(2017, 6, 30))
    closes = {}
    for number in range(stocks):
        price, series = 20.0, {}
        drift = 0.0004 * (number - stocks / 2)
        for day in days:
            price *= 1 + drift + rng.normal(0, 0.012)
            series[day] = max(price, 10.5)
        closes[f"{1101 + number}.TW"] = series
    closes["0050.TW"] = {day: 100.0 for day in days}
    return LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 50_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})


RULE = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, keep=1, min_hold=0)


def test_a_state_row_by_hand():
    data = market()
    fp = FactorPanel(Panel(data))
    context = exits.Context(fp)
    position = len(fp.sessions) - 1
    symbol = fp.symbols[0]
    price = fp.price(symbol, position)
    ranked = fp.ranked(RULE, position)
    row = context.rows(position, [symbol], {symbol: 7}, {symbol: price / 1.1}, {symbol: price / 0.8}, ranked)[0]
    named = dict(zip(exits.FEATURES, row, strict=True))
    assert named["held_sessions"] == 7 and named["gain_since_buy"] == pytest.approx(0.1, rel=1e-5)
    assert named["fall_from_peak"] == pytest.approx(-0.2, rel=1e-5)
    assert named["place_in_rule"] == pytest.approx(ranked.index(symbol) / len(ranked) if symbol in ranked else 1.0)
    replacement = next(item for item in ranked if item != symbol)
    gap = context.percentiles["trend_200"][fp.row[replacement], position] - context.percentiles["trend_200"][fp.row[symbol], position]
    assert named["replacement_trend_gap"] == pytest.approx(gap)


def test_samples_are_the_holding_against_its_replacement():
    data = market()
    fp = FactorPanel(Panel(data))
    start = fp.sessions[260]
    x, y, positions = exits.dataset(fp, RULE, start, fp.sessions[-1])
    assert len(x) == len(y) == len(positions) > 100 and x.shape[1] == len(exits.FEATURES)
    ranks = daily_rankings(fp, RULE, start, fp.sessions[-1])
    position = positions[0]
    holdings = ranks[fp.sessions[position]]
    ranked = fp.ranked(RULE, position)
    replacement = next(item for item in ranked if item not in holdings)
    prices = fp.panel.filled

    def gain(symbol):
        return prices[fp.row[symbol], position + exits.HORIZON] / prices[fp.row[symbol], position] - 1

    assert y[0] == pytest.approx(np.clip(gain(holdings[0]) - gain(replacement), -0.5, 0.5), rel=1e-4)


def constant(folder, value, year=2016):
    folder.mkdir(parents=True, exist_ok=True)
    model = DummyRegressor(strategy="constant", constant=value).fit(np.zeros((2, len(exits.FEATURES))), [value, value])
    (folder / f"{year}.pkl").write_bytes(pickle.dumps(model))


def test_the_rule_sells_what_the_agent_says_and_does_not_buy_it_back(tmp_path):
    data = market()
    rule = RULE.model_copy(update={"exit_model": "q1"})
    start, end = date(2016, 3, 1), date(2016, 6, 30)
    plain = daily_rankings(FactorPanel(Panel(data)), RULE, start, end)
    constant(tmp_path / exits.EXIT_VERSION, 0.10)                       # always worth keeping
    keep = daily_rankings(FactorPanel(Panel(data), models=tmp_path), rule, start, end)
    assert keep == plain
    constant(tmp_path / exits.EXIT_VERSION, -0.10)                      # always worth replacing
    sell = daily_rankings(FactorPanel(Panel(data), models=tmp_path), rule, start, end)
    days = sorted(sell)
    first, second = sell[days[0]], sell[days[1]]
    assert not set(first) & set(second)                                 # all sold the next day ...
    assert not set(first) & set(sell[days[10]])                         # ... and not bought back within 20 sessions
    assert "exit_model" not in RULE.canonical() and rule.rule_hash != RULE.rule_hash


def test_walk_forward_models_are_saved_per_year(tmp_path):
    data = market(stocks=30)                                   # 10 holdings a week: enough samples before 2016
    fp = FactorPanel(Panel(data))
    meta = exits.train(fp, tmp_path, RULE.model_copy(update={"top": 10}), years=[2016, 2017])
    assert set(meta["years"]) <= {"2016", "2017"} and (tmp_path / "2016.pkl").is_file()
    item = meta["years"]["2016"]
    assert item["train_rows"] > 500 and 0 <= item["sell_share"] <= 1
