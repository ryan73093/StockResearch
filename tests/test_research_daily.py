from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import daily
from quant_platform.research.costs import CostModel
from quant_platform.research.daily import (
    DailyRule,
    FactorPanel,
    daily_rankings,
    gate,
    simulate_daily,
)
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.registry import TrialRegistry
from quant_platform.research.stock_rules import Panel, SeedPlan

FREE = CostModel(fee_rate=0.0, minimum_fee=0, slippage_bps=0.0)


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def market(days, closes):
    closes = {**closes, "0050.TW": closes.get("0050.TW", {day: 100.0 for day in days})}
    return LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 50_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})


def test_technical_factors_on_known_prices():
    days = weekdays(date(2023, 1, 2), date(2024, 3, 29))
    rising = [10 * 1.002 ** n for n in range(len(days))]
    data = market(days, {"1101.TW": dict(zip(days, rising)), "1102.TW": {day: 20.0 for day in days}})
    fp = FactorPanel(Panel(data))
    row, last = fp.symbols.index("1101.TW"), len(days) - 1
    assert fp.matrix("rsi_14")[row, last] == pytest.approx(100.0)       # never a down day
    assert fp.matrix("kd_k")[row, last] == pytest.approx(100.0)         # always at the top of its 9-day range
    assert fp.matrix("breakout_55")[row, last] == pytest.approx(1.0)
    expected = rising[-1] / np.mean(rising[-200:]) - 1
    assert fp.matrix("trend_200")[row, last] == pytest.approx(expected, rel=1e-5)
    assert fp.matrix("momentum_3")[row, last] == pytest.approx(1.002 ** 63 - 1, rel=1e-5)


class ScriptedPanel:
    def __init__(self, sessions, script):
        self.sessions, self.index, self.script = sessions, {day: i for i, day in enumerate(sessions)}, script

    def ranked(self, rule, position):
        return list(self.script.get(self.sessions[position], []))


def test_keep_zone_and_minimum_holding_in_sessions():
    days = weekdays(date(2024, 1, 1), date(2024, 1, 5))
    script = {days[0]: ["A", "B", "C", "D", "E"], days[1]: ["D", "E", "F", "A", "B", "C"],
              days[2]: ["D", "E", "F", "A", "B", "C"]}
    panel = ScriptedPanel(days, script)
    base = {"name": "x", "factors": {"momentum_3": 1.0}, "top": 3, "keep": 1}
    plain = daily_rankings(panel, DailyRule(**base, min_hold=0), days[0], days[2])
    held = daily_rankings(panel, DailyRule(**base, min_hold=2), days[0], days[2])
    assert plain[days[1]] == ["D", "E", "F"]
    # bought on the first session, still kept on the second (1 < 2), replaced on the third
    assert held[days[1]] == ["A", "B", "C"] and held[days[2]] == ["D", "E", "F"]


def test_core_new_names_and_top_ups_only_on_contribution_days():
    days = weekdays(date(2024, 1, 1), date(2024, 1, 5))
    data = market(days, {symbol: {day: 10.0 for day in days} for symbol in ("A.TW", "B.TW", "C.TW", "D.TW")})
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, core=0.5, min_trade=1_000)
    ranks = {days[0]: ["A.TW", "B.TW", "C.TW"], days[2]: ["A.TW", "B.TW", "D.TW"]}
    ledger = []
    run = simulate_daily(data, rule, FREE, days[0], days[-1], ranks, SeedPlan(300_000, 10_000), ledger=ledger)
    trades = [(entry["day"], entry["symbol"], entry["side"], entry["shares"]) for entry in ledger]
    # 01-01: 300,000 → half in 0050 (1,500 × 100), the rest 50,000 in each pick
    assert sorted(trades[:4]) == sorted([(days[0], "0050.TW", "BUY", 1500)] +
                                        [(days[0], s, "BUY", 5000) for s in ("A.TW", "B.TW", "C.TW")])
    # 01-03: C drops out (50,000 less 0.3% tax = 49,850) and D comes in; nothing else trades
    assert trades[4:6] == [(days[2], "C.TW", "SELL", 5000), (days[2], "D.TW", "BUY", 4985)]
    # 01-05: 10,000 arrives; total 309,850 → 0050 is 4,925 short of half, D 1,791, A and B 1,641:
    # biggest gap first, every order at least 1,000
    assert sorted(trades[6:]) == sorted([(days[4], "0050.TW", "BUY", 49), (days[4], "D.TW", "BUY", 179),
                                         (days[4], "B.TW", "BUY", 164), (days[4], "A.TW", "BUY", 164)])
    assert run.final_value == pytest.approx(309_850) and run.trades == 10 and run.taxes == 150
    benchmark = simulate_daily(data, None, FREE, days[0], days[-1], plan=SeedPlan(300_000, 10_000))
    assert benchmark.final_value == pytest.approx(310_000) and benchmark.trades == 2


def test_gate():
    good = {"full_period_excess": 0.3, "since_2020_excess": 0.1, "max_drawdown": -0.30, "benchmark_max_drawdown": -0.32,
            "windows": {"1y": {"count": 100, "win_ratio": 0.55}, "3y": {"count": 80, "win_ratio": 0.7, "median_excess": 0.05}}}
    assert gate(good) == []
    bad = {**good, "since_2020_excess": -0.2, "max_drawdown": -0.45,
           "windows": {"1y": {"count": 100, "win_ratio": 0.4}, "3y": {"count": 80, "win_ratio": 0.5, "median_excess": -0.01}}}
    reasons = gate(bad)
    assert len(reasons) == 4 and "2020-10 起輸 0050" in reasons[0]


def test_a_rule_runs_registers_once_and_reports_both_periods(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "RECENT_START", date(2024, 1, 1))
    monkeypatch.setattr(daily, "REGIME_START", date(2024, 7, 1))
    monkeypatch.setattr(daily, "RECENT_END", date(2024, 12, 31))
    monkeypatch.setattr(daily, "WINDOWS", {"1y": 6})
    days = weekdays(date(2023, 1, 2), date(2024, 12, 31))
    closes = {f"{1101 + i}.TW": {day: 20 * (1 + (i - 2) * 0.0006) ** n for n, day in enumerate(days)} for i in range(6)}
    data = market(days, closes)
    fp = FactorPanel(Panel(data))
    rule = DailyRule(name="每天 3 個月動能：前 3 名", factors={"momentum_3": 1.0}, top=3)
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    record, report = daily.run_trial(rule, tmp_path, registry, tmp_path / "reports", FREE, data, fp, "daily:test")
    # the strongest trends keep rising, 0050 is flat: the rule wins both periods and every window
    assert report["full_period_excess"] > 0 and report["since_2020"]["excess"] > 0
    assert report["windows"]["1y"]["count"] > 0 and report["windows"]["1y"]["win_ratio"] == 1.0
    assert set(report["yearly"]) == {"2024"} and report["curve"]
    assert record.period == "recent" and record.metrics["reasons"] == [] and record.data_fingerprint == "daily:test"
    again, _ = daily.run_trial(rule, tmp_path, registry, tmp_path / "reports", FREE, data, fp, "daily:test")
    assert again.trial_id == record.trial_id and len(registry.records()) == 1


def test_factor_batch_is_one_rule_per_factor():
    rules = daily.factor_batch()
    assert len(rules) == 2 * (len(daily.FIRST_FACTORS) + len(daily.OSCILLATORS)) == 44   # as run on 2026-10-04
    assert len({rule.rule_hash for rule in rules}) == len(rules)
    assert all(len(rule.name) <= 80 for rule in rules)
    with pytest.raises(ValueError):
        DailyRule(name="x", factors={"no_such_factor": 1.0})


class IndustryPanel(ScriptedPanel):
    def __init__(self, sessions, script, industries):
        super().__init__(sessions, script)
        self.industries = industries

    def industry(self, symbol):
        return self.industries[symbol]


def test_industry_cap_skips_a_full_industry_for_the_next_best():
    days = weekdays(date(2024, 1, 1), date(2024, 1, 2))
    ranked = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L"]
    industries = {name: ("電子零組件業" if name in "ABCDE" else f"其他{name}") for name in ranked}
    panel = IndustryPanel(days, {days[0]: ranked}, industries)
    base = {"name": "x", "factors": {"momentum_3": 1.0}, "top": 10}
    assert daily_rankings(panel, DailyRule(**base), days[0], days[0])[days[0]] == ranked[:10]
    capped = DailyRule(**base, industry_cap=0.3)                     # at most 3 of 10 from one industry
    assert capped.industry_limit == 3
    assert daily_rankings(panel, capped, days[0], days[0])[days[0]] == ["A", "B", "C", "F", "G", "H", "I", "J", "K", "L"]
    assert "industry_cap" not in DailyRule(**base).canonical() and DailyRule(**base).rule_hash != capped.rule_hash


def test_tiers():
    from quant_platform.research.daily import tier

    windows = {"1y": {"count": 100, "win_ratio": 0.6}, "3y": {"count": 80, "win_ratio": 0.8, "median_excess": 0.2}}
    good = {"full_period_excess": 2.0, "since_2020_excess": 0.4, "max_drawdown": -0.36, "benchmark_max_drawdown": -0.34,
            "windows": windows}
    assert tier(good)[0] == "T0 候選" and tier(good, {"sessions": 30, "excess": 0.02})[1] == "前向觀察 30／60 個交易日"
    assert tier(good, {"sessions": 60, "excess": 0.01, "problems": []})[0] == "T0"
    assert tier(good, {"sessions": 60, "excess": -0.01, "problems": []})[0] == "T0 候選"
    assert tier({**good, "max_drawdown": -0.52})[0] == "T1"                      # wins, more volatile
    assert tier({**good, "since_2020_excess": -0.1})[0] == "T2"                  # only part of the time
    assert tier({**good, "full_period_excess": -0.5})[0] == "T3"


class PricedPanel(ScriptedPanel):
    """A scripted ranking with adjusted closes and a market switch (R7)."""

    def __init__(self, sessions, script, prices, on=None):
        super().__init__(sessions, script)
        self.prices, self.on = prices, on

    def price(self, symbol, position):
        return self.prices[symbol][position]

    def risk_on(self, position):
        return self.on[position]


def test_trailing_stop_sells_15_percent_below_the_high_and_waits_20_sessions():
    days = weekdays(date(2024, 1, 1), date(2024, 2, 16))
    script = {day: ["A", "B", "C", "D", "E"] for day in days}
    a = [100.0, 100.0, 100.0, 120.0, 110.0, 101.0] + [101.0] * (len(days) - 6)   # 101 < 120 × 0.85 = 102
    panel = PricedPanel(days, script, {name: a if name == "A" else [100.0] * len(days) for name in "ABCDE"})
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, keep=1, stop_loss=0.15)
    out = daily_rankings(panel, rule, days[0], days[-1])
    assert out[days[4]] == ["A", "B", "C"]                  # 110 is only 8% below the high
    assert out[days[5]] == ["B", "C", "D"]                  # stopped out, the next best bought
    assert out[days[24]] == ["B", "C", "D"]                 # not bought back for 20 sessions
    assert out[days[25]] == ["B", "C", "A"]                 # D's 20 sessions are up and A ranks first again
    plain = daily_rankings(panel, DailyRule(name="x", factors={"trend_200": 1.0}, top=3, keep=1), days[0], days[-1])
    assert plain[days[5]] == ["A", "B", "C"]


def test_market_filter_holds_cash_and_keeps_the_0050_core():
    days = weekdays(date(2024, 1, 1), date(2024, 1, 12))
    on = [True, True, False, False, True, True, True, True, True, True]
    panel = PricedPanel(days, {day: ["A", "B", "C"] for day in days}, {}, on)
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, core=0.5, market_filter="taiex_200")
    ranks = daily_rankings(panel, rule, days[0], days[-1])
    assert ranks[days[1]] == ["A", "B", "C"] and ranks[days[2]] == [] == ranks[days[3]] and ranks[days[4]] == ["A", "B", "C"]
    data = market(days, {f"{name}.TW": {day: 10.0 for day in days} for name in "ABC"})
    ranks = {day: [f"{symbol}.TW" for symbol in names] for day, names in ranks.items()}
    snapshots = {}
    simulate_daily(data, rule, FREE, days[0], days[-1], ranks, SeedPlan(100_000, 0), snapshots=snapshots)
    cash, units = snapshots[days[2]]
    assert set(units) == {"0050.TW"} and cash == pytest.approx(50_000 * 0.997, abs=5)   # sold (0.3% tax); core stays
    assert set(snapshots[days[4]][1]) == {"0050.TW", "A.TW", "B.TW", "C.TW"}


def test_market_filter_compares_taiex_with_its_200_session_average():
    days = weekdays(date(2023, 1, 2), date(2024, 1, 31))
    data = market(days, {"1101.TW": {day: 10.0 for day in days}})
    taiex = np.array([100.0] * 230 + [80.0] * (len(days) - 230))
    fp = FactorPanel(Panel(data), market=taiex)
    assert fp.risk_on(10) and fp.risk_on(229) and not fp.risk_on(240)
    with pytest.raises(ValueError):
        FactorPanel(Panel(data)).risk_on(240)


def test_risk_controls_are_part_of_the_rule_only_when_on():
    base = DailyRule(name="x", factors={"trend_200": 1.0})
    assert "stop_loss" not in base.canonical() and "market_filter" not in base.canonical()
    batch = daily.overlay_batch()
    assert len({rule.rule_hash for rule in batch} | {base.rule_hash}) == 7 and len({rule.name for rule in batch}) == 6
    assert all(rule.industry_cap == 0.3 and rule.factors == {"trend_200": 1.0} for rule in batch)


def test_inverse_volatility_weights_give_the_calmer_stock_more():
    days = weekdays(date(2023, 1, 2), date(2024, 3, 29))
    swing = {"1101.TW": 0.01, "1102.TW": 0.02, "1103.TW": 0.04}            # daily up/down swings
    closes = {symbol: {day: 100 * (1 + size) ** (n % 2) for n, day in enumerate(days)} for symbol, size in swing.items()}
    fp = FactorPanel(Panel(market(days, closes)))
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, weighting="inverse_vol")
    weights = daily.daily_weights(fp, rule, {days[-1]: list(swing)})[days[-1]]
    assert sum(weights.values()) == pytest.approx(1.0)
    sigma = {symbol: -fp.matrix("low_volatility_60")[fp.row[symbol], len(days) - 1] for symbol in swing}
    products = [weights[symbol] * sigma[symbol] for symbol in swing]
    assert products == pytest.approx([products[0]] * 3, rel=1e-5)          # share × volatility is the same
    assert weights["1101.TW"] > weights["1102.TW"] > weights["1103.TW"]
    assert daily.daily_weights(fp, DailyRule(name="x", factors={"trend_200": 1.0}), {days[-1]: list(swing)}) is None


def test_weights_and_parking_in_0050_by_hand():
    days = weekdays(date(2024, 1, 1), date(2024, 1, 12))
    data = market(days, {"A.TW": {day: 10.0 for day in days}, "B.TW": {day: 10.0 for day in days},
                         "0050.TW": {day: 100.0 for day in days}})
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, core=0.5)
    ranks = {day: ["A.TW", "B.TW"] for day in days}
    weights = {day: {"A.TW": 0.75, "B.TW": 0.25} for day in days}
    snapshots = {}
    simulate_daily(data, rule, FREE, days[0], days[-1], ranks, SeedPlan(100_000, 0), snapshots=snapshots,
                   weights=weights, parked={days[2], days[3]})
    assert snapshots[days[0]][1] == {"0050.TW": 500, "A.TW": 3750, "B.TW": 1250}
    # parked: the stocks sold (0.3% tax: 49,850) buy 498 more units of 0050
    cash, held = snapshots[days[2]]
    assert held == {"0050.TW": 998} and cash == pytest.approx(50, abs=1)
    assert snapshots[days[3]][1] == {"0050.TW": 998}
    # back: the 0050 above the core (49,875) is sold as 498 units (0.1% tax: 49,750.2, cash 49,800.2); of the
    # stock half (99,800.2 / 2) A's 75% is 37,425 → 3,742 shares, B gets the 12,380.2 left → 1,238 shares
    cash, held = snapshots[days[4]]
    assert held == {"0050.TW": 500, "A.TW": 3742, "B.TW": 1238} and cash == pytest.approx(2.0)   # taxes round down to whole NT$


def test_account_filter_parks_only_after_the_rule_itself_turns_down():
    days = weekdays(date(2022, 1, 3), date(2024, 12, 31))
    peak = 560
    closes = {}
    for number, symbol in enumerate(("1101.TW", "1102.TW", "1103.TW")):
        series, price = {}, 20.0 + number
        for n, day in enumerate(days):
            price *= 1.001 if n < peak else 0.996
            series[day] = price
        closes[symbol] = series
    data = market(days, closes)
    fp = FactorPanel(Panel(data))
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, account_filter="own_200")
    parked = daily.account_parking(data, fp, rule, FREE)
    assert parked and min(parked) > days[peak] and days[-1] in parked
    assert daily.account_parking(data, fp, DailyRule(name="x", factors={"trend_200": 1.0}, top=3), FREE) is None


def test_holding_controls_are_part_of_the_rule_only_when_on():
    base = DailyRule(name="x", factors={"trend_200": 1.0})
    assert "weighting" not in base.canonical() and "account_filter" not in base.canonical()
    batch = daily.holdings_batch()
    assert len({rule.rule_hash for rule in batch} | {base.rule_hash}) == 7 and len({rule.name for rule in batch}) == 6


def test_smoothing_ranks_on_the_recent_average_and_stays_out_of_the_hash_at_zero():
    days = weekdays(date(2023, 1, 2), date(2024, 3, 29))
    # A rises steadily; B jumps up on the last day only: the day's momentum ranks B first, the 5-day average A
    a = [10 * 1.003 ** n for n in range(len(days))]
    b = [10.0] * (len(days) - 1) + [10.5]                       # +5% today: a 1% five-day average
    c = [10.0] * len(days)
    data = market(days, {"A.TW": dict(zip(days, a)), "B.TW": dict(zip(days, b)), "C.TW": dict(zip(days, c))})
    fp = FactorPanel(Panel(data))
    last = len(days) - 1
    plain = DailyRule(name="x", factors={"reversal_5d": -1.0}, top=3)          # = last week's return
    smooth = plain.model_copy(update={"smooth": 5})
    assert fp.ranked(plain, last)[0] == "B.TW" and fp.ranked(smooth, last)[0] == "A.TW"
    assert fp.smoothed("reversal_5d", 5)[:, last] == pytest.approx(fp.matrix("reversal_5d")[:, last - 4:last + 1].mean(axis=1))
    assert "smooth" not in plain.canonical() and smooth.rule_hash != plain.rule_hash
    assert len({rule.rule_hash for rule in daily.turnover_batch()}) == 4


def test_volatility_scaling_trims_to_cash_and_buys_back_by_hand():
    """2026-10-09 (使用者：知道什麼時候要賣): the stock part follows the exposure; the rest is cash, never 0050."""
    days = weekdays(date(2024, 1, 1), date(2024, 1, 12))
    closes = {f"{1101 + n}.TW": {day: 10.0 for day in days} for n in range(3)}
    data = market(days, closes)
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, keep=1, min_hold=0, vol_scale="v1")
    ranks = {day: list(closes) for day in days}
    exposure = {day: (0.5 if date(2024, 1, 8) <= day < date(2024, 1, 11) else 1.0) for day in days}
    plan = SeedPlan(initial=100_000, monthly_amount=0)
    ledger, books = [], {}
    run = simulate_daily(data, rule, FREE, days[0], days[-1], ranks, plan, ledger=ledger, snapshots=books, exposure=exposure)
    held = lambda day: sum(count * 10.0 for symbol, count in books[day][1].items() if symbol != "0050.TW")
    assert held(date(2024, 1, 5)) == pytest.approx(100_000, rel=0.005)              # fully invested
    assert held(date(2024, 1, 8)) == pytest.approx(50_000, rel=0.005)               # trimmed to half (whole shares)
    assert books[date(2024, 1, 8)][0] == pytest.approx(50_000, rel=0.005)           # the rest is cash
    assert held(date(2024, 1, 11)) == pytest.approx(100_000, rel=0.005)             # bought back
    assert not any(entry["symbol"] == "0050.TW" for entry in ledger)
    assert run.values[-1] == pytest.approx(100_000 - run.taxes) and run.taxes > 0     # only the 0.3% tax on the trim
    assert "vol_scale" not in rule.model_copy(update={"vol_scale": "none"}).canonical()


def test_the_exposure_falls_when_the_rule_turns_volatile():
    rng = np.random.default_rng(7)
    days = weekdays(date(2019, 1, 1), date(2024, 12, 31))
    noise = [0.004] * 1200 + [0.04] * (len(days) - 1200)                            # calm, then wild
    closes = {}
    for number in range(4):
        price, series = 20.0, {}
        for index, day in enumerate(days):
            price *= 1 + 0.0004 + rng.normal(0, noise[index])
            series[day] = max(price, 10.5)
        closes[f"{1101 + number}.TW"] = series
    data = market(days, closes)
    fp = FactorPanel(Panel(data))
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, vol_scale="v1")
    schedule = daily.exposure_schedule(data, fp, rule, FREE)
    calm = [schedule[day] for day in days[1000:1190] if day in schedule]
    wild = [schedule[day] for day in days[1230:1300] if day in schedule]
    assert np.mean(calm) >= 0.9 and max(wild) <= 0.5                          # invested in ordinary times, then cut
    assert all(value in (0.0, 0.25, 0.5, 0.75, 1.0) for value in schedule.values())
    assert daily.exposure_schedule(data, fp, rule.model_copy(update={"vol_scale": "none"}), FREE) is None
