"""2026-10-16 (使用者：不要給 RL 加一堆奇怪的限制，只受交易所規則限制；RL 沒打贏 0050 之前不要停): the robot 4.0."""
from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import rl_free
from quant_platform.research.costs import CostModel
from quant_platform.research.daily import DailyRule, FactorPanel, simulate_daily
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel, SeedPlan

FREE = CostModel(fee_rate=0.0, fee_discount=1.0, minimum_fee=0, slippage_bps=0.0)


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def market(days, stocks=12, seed=2):
    rng = np.random.default_rng(seed)
    drift = np.linspace(-0.001, 0.001, stocks)
    closes = {f"{1101 + n}.TW": dict(zip(days, np.maximum(30 * np.cumprod(1 + drift[n] + rng.normal(0, 0.01, len(days))), 11.0)))
              for n in range(stocks)}
    closes["0050.TW"] = {day: 50.0 for day in days}
    return LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 5e9 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})


def test_the_free_rule_holds_its_weights_and_the_rest_in_cash():
    days = weekdays(date(2024, 1, 1), date(2024, 3, 29))
    data = market(days)
    rule = DailyRule(name="自由", factors={"momentum_3": 1.0}, free=True)
    first, later = days[5], days[30]
    targets = {day: {"1101.TW": 0.5, "1102.TW": 0.3} for day in days if first <= day < later}
    targets.update({day: {"1101.TW": 0.2} for day in days if day >= later})
    snapshots, ledger = {}, []
    run = simulate_daily(data, rule, FREE, first, days[-1], plan=SeedPlan(initial=300_000, monthly_amount=0),
                         ledger=ledger, snapshots=snapshots, targets=targets)
    cash, units = snapshots[days[10]]
    value = run.values[run.days.index(days[10])]
    held = {symbol: count * data.closes[symbol][days[10]] / value for symbol, count in units.items()}
    assert held["1101.TW"] == pytest.approx(0.5, abs=0.02) and held["1102.TW"] == pytest.approx(0.3, abs=0.02)
    assert cash / value == pytest.approx(0.2, abs=0.02)                          # the rest in cash
    _cash, units = snapshots[later]
    assert "1102.TW" not in units and any(entry["side"] == "SELL" and entry["symbol"] == "1101.TW" for entry in ledger)
    assert DailyRule(name="x", factors={"momentum_3": 1.0}).rule_hash != rule.rule_hash and "free" in rule.canonical()


def test_the_free_agent_learns_from_every_session_and_keeps_its_curves(monkeypatch, tmp_path):
    monkeypatch.setattr(rl_free, "EPOCHS", 3)
    monkeypatch.setattr(rl_free, "SEEDS", (0,))
    monkeypatch.setattr(rl_free, "FIRST_TEST_YEAR", 2019)
    monkeypatch.setattr(rl_free, "feature_names", lambda: ["momentum_3", "reversal_1", "low_volatility_60"])
    from quant_platform.research import daily

    monkeypatch.setattr(daily, "RECENT_START", date(2017, 1, 2))
    days = weekdays(date(2016, 1, 1), date(2019, 6, 28))
    data = market(days)
    fp = FactorPanel(Panel(data))
    from quant_platform.research.rl_timing import benchmark_units

    world = rl_free.Market(fp, benchmark_units(data, fp.sessions), FREE)
    assert world.features.shape[1:] == (12, 3) and world.cost_sell >= world.cost_buy
    meta = rl_free.walk_forward(world, tmp_path)
    year = meta["yearly"]["2019"]
    assert len(year["curve_train"]) == len(year["curve_held"]) == 3 and year["train_sessions"] > 400
    assert 0 <= year["cash"] <= 1 and year["holdings"] >= 1
    weights = rl_free.weights(fp, tmp_path)
    first = fp.index[next(day for day in fp.sessions if day.year == 2019)]
    assert np.isnan(weights[first - 1]).all() and np.nansum(weights[first]) <= 1.0 + 1e-6


def test_sparsemax_gives_exact_zeros_and_sums_to_one():
    import torch

    weights = rl_free.sparsemax(torch.tensor([3.0, 1.0, 0.2, -1e4]))
    assert float(weights.sum()) == pytest.approx(1.0) and float(weights[3]) == 0.0 and float(weights[0]) > float(weights[1])


def test_the_robot_41_trains_in_the_owners_account(monkeypatch, tmp_path):
    monkeypatch.setattr(rl_free, "EPOCHS", 2)
    monkeypatch.setattr(rl_free, "SEEDS", (0,))
    monkeypatch.setattr(rl_free, "FIRST_TEST_YEAR", 2019)
    monkeypatch.setattr(rl_free, "feature_names", lambda: ["momentum_3", "reversal_1"])
    from quant_platform.research import daily

    monkeypatch.setattr(daily, "RECENT_START", date(2017, 1, 2))
    days = weekdays(date(2016, 1, 1), date(2019, 6, 28))
    data = market(days)
    fp = FactorPanel(Panel(data))
    from quant_platform.research.rl_timing import benchmark_units

    world = rl_free.Market(fp, benchmark_units(data, fp.sessions), CostModel(fee_rate=0.001425, fee_discount=0.28, minimum_fee=1))
    assert world.minimum_fee[0] == 20 and world.monthly.sum() > 0 and world.close.shape == world.returns.shape
    meta = rl_free.walk_forward(world, tmp_path, version=rl_free.VERSION_41)
    assert meta["version"] == rl_free.VERSION_41 and "2019" in meta["yearly"]
