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
                      traded_value={symbol: {day: 5_000_000_000.0 for day in series} for symbol, series in closes.items()},
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
    assert tier(good)[0] == "T0 候選" and tier(good, {"sessions": 30, "excess": 0.02})[1] == "串接前向 30／60 個交易日"
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


def test_large_caps_ranks_only_the_largest_and_buys_equal_amounts():
    """2026-10-09: ``large_caps`` keeps the ranking to the N largest by market value that day; the
    picks still get equal amounts (never market-value shares)."""
    days = weekdays(date(2023, 1, 2), date(2024, 3, 29))
    speeds = {"1101.TW": 1.004, "1102.TW": 1.003, "1103.TW": 1.002, "1104.TW": 1.001, "1105.TW": 1.0005}
    data = market(days, {symbol: {day: 20 * speed ** n for n, day in enumerate(days)} for symbol, speed in speeds.items()})
    fp = FactorPanel(Panel(data))
    sizes = {"1101.TW": 1.0, "1102.TW": 5.0, "1103.TW": 4.0, "1104.TW": 3.0, "1105.TW": 9.0, "0050.TW": float("nan")}
    caps = np.array([[np.log(sizes[symbol] * 1e10)] * len(days) for symbol in fp.symbols], dtype=np.float32)
    late = len(days) - 20
    caps[fp.symbols.index("1101.TW"), late:] = np.log(8e10)          # the fastest grows into the top 3
    fp._cache["market_cap"] = caps
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, keep=1, min_hold=0, large_caps=3)
    assert fp.ranked(rule, late - 1) == ["1102.TW", "1103.TW", "1105.TW"]     # 1101 is the strongest but small
    assert fp.ranked(rule, late) == ["1101.TW", "1102.TW", "1105.TW"]         # 1103 drops to fourth largest
    plain = rule.model_copy(update={"large_caps": 0})
    assert fp.ranked(plain, late - 1)[:3] == ["1101.TW", "1102.TW", "1103.TW"]
    assert "large_caps" not in plain.canonical() and plain.rule_hash != rule.rule_hash
    ledger = []
    start = days[late - 5]
    simulate_daily(data, rule, FREE, start, days[late - 1], daily_rankings(fp, rule, start, days[late - 1]),
                   plan=SeedPlan(300_000, 0), ledger=ledger)
    bought = [row["shares"] * row["price"] for row in ledger if row["side"] == "BUY"]
    assert len(bought) == 3 and max(bought) - min(bought) < 0.01 * max(bought)   # a third each


def test_the_large_cap_batch_is_all_stocks_one_rule_per_family():
    from quant_platform.research import blend
    from quant_platform.research.categories import classify, uses_0050

    rules = daily.BATCHES["largecap"]()
    singles = [rule for rule in rules if getattr(rule, "kind", None) != "blend"]
    assert len(rules) == 5 and len({rule.rule_hash for rule in singles}) == 4
    assert all(rule.large_caps == blend.LARGE_CAPS and rule.core == 0 and rule.weighting == "equal" for rule in singles)
    assert all(not uses_0050(rule.canonical()) for rule in singles)
    assert "只挑市值前 100 大、每檔等額" in classify(singles[0].canonical())["traits"]


def test_the_second_large_cap_batch_tests_new_hypotheses_not_tweaks():
    from quant_platform.research.categories import classify, uses_0050

    first = {rule.rule_hash for rule in daily.BATCHES["largecap"]() if getattr(rule, "kind", None) != "blend"}
    rules = daily.BATCHES["largecap2"]()
    assert len(rules) == 3 and not first & {rule.rule_hash for rule in rules}
    assert all(rule.large_caps == 100 and rule.core == 0 and not uses_0050(rule.canonical()) for rule in rules)
    assert len({tuple(sorted(rule.factors)) + (rule.weighting,) for rule in rules}) == 3
    calm = classify(rules[0].canonical())["traits"]                      # weighted by calmness: not equal amounts
    assert "只挑市值前 100 大" in calm and "依波動度配置" in calm and not any("等額" in trait for trait in calm)


def test_market_rules_no_buy_at_limit_up_no_sell_at_limit_down_and_retry_next_session():
    """1.3.0: a locked limit-up close cannot be bought, a locked limit-down close cannot be sold; the refused
    order is tried again at the next close."""
    days = weekdays(date(2024, 1, 1), date(2024, 1, 10))                   # 8 sessions
    up = [10.0, 11.0, 11.5, 11.5, 11.5, 11.5, 11.5, 11.5]                     # +10% on the 2nd session: locked
    down = [20.0, 20.0, 20.0, 18.0, 17.0, 17.0, 17.0, 17.0]                   # −10% on the 4th: locked
    data = market(days, {"1101.TW": dict(zip(days, up)), "1102.TW": dict(zip(days, down))})
    assert daily.price_limits(data, "1101.TW", days[1]) == (9.0, 11.0)
    script = {days[0]: ["1102.TW"], days[1]: ["1101.TW"], days[3]: ["1101.TW"]}
    ledger = []
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, min_hold=0)
    simulate_daily(data, rule, FREE, days[0], days[-1], script, SeedPlan(100_000, 0), ledger=ledger)
    trades = [(row["day"], row["symbol"], row["side"]) for row in ledger if row["side"] in ("BUY", "SELL", "BLOCKED")]
    assert (days[0], "1102.TW", "BUY") in trades
    assert (days[1], "1102.TW", "SELL") in trades                           # an ordinary sell
    assert (days[1], "1101.TW", "BLOCKED") in trades                        # limit-up close: not bought ...
    assert (days[2], "1101.TW", "BUY") in trades                            # ... bought at the next close
    blocked = [row for row in ledger if row["side"] == "BLOCKED"]
    assert blocked[0]["reason"] == "漲停買不到"


def test_a_locked_limit_down_holding_is_sold_the_next_session():
    days = weekdays(date(2024, 1, 1), date(2024, 1, 10))
    falling = [20.0, 20.0, 18.0, 17.5, 17.5, 17.5, 17.5, 17.5]               # −10% locked on the 3rd session
    other = [30.0] * 8
    data = market(days, {"1101.TW": dict(zip(days, falling)), "1102.TW": dict(zip(days, other))})
    script = {days[0]: ["1101.TW"], days[2]: ["1102.TW"]}                   # dropped on the limit-down day
    ledger = []
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, min_hold=0, check="weekly")
    simulate_daily(data, rule, FREE, days[0], days[-1], script, SeedPlan(100_000, 0), ledger=ledger)
    sells = [row["day"] for row in ledger if row["side"] == "SELL" and row["symbol"] == "1101.TW"]
    assert sells == [days[3]]                                                # not on the locked day, the next one
    assert any(row["side"] == "BLOCKED" and row["reason"] == "跌停賣不掉" for row in ledger)


def test_the_limit_reference_follows_the_ex_rights_adjustment():
    days = weekdays(date(2024, 7, 1), date(2024, 7, 3))
    data = market(days, {"1101.TW": dict(zip(days, [100.0, 98.0, 98.0]))})
    data.factors["1101.TW"] = {days[1]: 1.02}                                # ex-rights: reference 100 / 1.02
    low, high = daily.price_limits(data, "1101.TW", days[1])
    assert high == pytest.approx(107.5) and low == pytest.approx(88.3)        # 98.04 × 1.1 = 107.84 → 107.5 tick
    assert daily.price_limits(data, "1101.TW", date(2015, 5, 4)) == (None, None)


def test_the_rule_decides_again_the_next_session_when_the_market_refuses():
    """1.3.0 (使用者：被擋下隔天就要重新評估): a weekly rule whose best pick closes locked limit-up on the check
    day does not hold it that day and ranks again at the next session."""
    days = weekdays(date(2023, 1, 2), date(2024, 1, 31))
    rng = np.random.default_rng(2)
    closes = {f"{1101 + n}.TW": [20.0] for n in range(6)}
    for index in range(1, len(days)):
        for n in range(6):
            closes[f"{1101 + n}.TW"].append(closes[f"{1101 + n}.TW"][-1] * (1 + 0.0004 * n + rng.normal(0, 0.005)))
    check = next(index for index, day in enumerate(days) if day.year == 2024 and day.weekday() == 0)
    leader = closes["1105.TW"]
    leader[check] = round(leader[check - 1] * 1.1 - 0.04, 2)                     # a big up day ...
    data = market(days, {symbol: dict(zip(days, series)) for symbol, series in closes.items()})
    fp = FactorPanel(Panel(data))
    _traded, locked_up, _down = fp.market_locks()
    row = fp.symbols.index("1105.TW")
    low, high = daily.price_limits(data, "1105.TW", days[check])
    assert bool(locked_up[row, check]) == (leader[check] >= high - 1e-6)          # same limits as the engine's
    leader[check] = high                                                          # ... closing locked at the limit
    data = market(days, {symbol: dict(zip(days, series)) for symbol, series in closes.items()})
    fp = FactorPanel(Panel(data))
    assert fp.market_locks()[1][row, check]
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, keep=1, min_hold=0, check="weekly")
    ranks = daily_rankings(fp, rule, days[check], days[check + 3])              # nothing held before
    assert "1105.TW" not in ranks[days[check]]                                    # not bought at limit-up
    assert days[check + 1] in ranks                                               # decided again the next session
    assert days[check + 2] not in ranks                                           # then back to weekly


def test_an_unsold_delisted_holding_is_worthless_on_its_delisting_date():
    """1.3.1 (使用者：沒賣掉的股票下市就是馬桶紙): no trade after the 3rd session, delisted on the 6th; the rule
    wants it sold from the 4th but there is no trade — on the delisting date the holding is written off."""
    days = weekdays(date(2024, 1, 1), date(2024, 2, 9))                    # 30 sessions
    data = market(days, {"1101.TW": {day: 20.0 for day in days[:3]}, "1102.TW": {day: 30.0 for day in days}})
    data.notes["delisted"] = {"1101": [days[5]]}
    script = {days[0]: ["1101.TW"], days[3]: ["1102.TW"]}
    ledger = []
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, min_hold=0, check="weekly")
    run = simulate_daily(data, rule, FREE, days[0], days[-1], script, SeedPlan(100_000, 10_000), ledger=ledger)
    rows = [row for row in ledger if row["symbol"] == "1101.TW" and row["side"] in ("SELL", "BLOCKED")]
    assert [(row["day"], row["side"]) for row in rows] == [(days[3], "BLOCKED"), (days[4], "BLOCKED"), (days[5], "SELL")]
    assert rows[0]["reason"] == "沒有成交" and rows[-1]["price"] == 0.0 and rows[-1]["reason"] == "下市，持股歸零"
    assert run.values[4] == pytest.approx(110_000) and run.values[5] == pytest.approx(10_000)  # the 5th's 10,000 stays
    assert any(row["side"] == "BUY" and row["symbol"] == "1102.TW" for row in ledger)    # the next contribution buys the new pick


def test_a_suspension_that_ends_is_sold_when_the_stock_trades_again():
    days = weekdays(date(2024, 1, 1), date(2024, 2, 9))
    halted = {day: 20.0 for n, day in enumerate(days) if not 3 <= n < 8}     # no trade on sessions 3..7
    data = market(days, {"1101.TW": halted, "1102.TW": {day: 30.0 for day in days}})
    script = {days[0]: ["1101.TW"], days[3]: ["1102.TW"]}
    ledger = []
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, min_hold=0, check="weekly")
    simulate_daily(data, rule, FREE, days[0], days[-1], script, SeedPlan(100_000, 0), ledger=ledger)
    sold = [row for row in ledger if row["side"] == "SELL" and row["symbol"] == "1101.TW"]
    assert [row["day"] for row in sold] == [days[8]] and sold[0]["price"] == 20.0
    assert sum(row["side"] == "BLOCKED" and row["symbol"] == "1101.TW" for row in ledger) == 5


def test_a_stock_that_never_trades_again_and_is_not_listed_is_written_off_after_the_idle_limit():
    days = weekdays(date(2024, 1, 1), date(2024, 2, 9))
    data = market(days, {"1101.TW": {day: 20.0 for day in days[:3]}, "1102.TW": {day: 30.0 for day in days}})
    assert daily.write_off_days(data) == {"1101.TW": days[2 + daily.IDLE_LIMIT]}
    short = market(days[:10], {"1101.TW": {day: 20.0 for day in days[:3]}})
    assert daily.write_off_days(short) == {}                                  # the data ends too soon to tell


class MarketPanel(ScriptedPanel):
    """A scripted panel with the market's view: who traded, the limits, the 20-session trend."""

    def __init__(self, sessions, script, symbols, untraded=(), rising=(), written_off=()):
        super().__init__(sessions, script)
        self.written_off = written_off
        self.symbols, self.row = list(symbols), {symbol: n for n, symbol in enumerate(symbols)}
        shape = (len(symbols), len(sessions))
        self.traded = np.ones(shape, dtype=bool)
        for symbol, first in untraded:
            self.traded[self.row[symbol], first:] = False
        self.up = np.zeros(shape, dtype=bool)
        self._rising = np.zeros(shape, dtype=bool)
        for symbol in rising:
            self._rising[self.row[symbol], :] = True

    def market_locks(self):
        return self.traded, self.up, self.up

    def gone(self):
        return dict(self.written_off)

    def rising(self):
        return self._rising


def test_the_ranking_keeps_an_unsellable_holding_and_drops_it_when_written_off():
    days = weekdays(date(2024, 1, 1), date(2024, 2, 9))
    script = {day: ["B", "C", "D"] for day in days}
    script[days[0]] = ["A", "B", "C"]
    panel = MarketPanel(days, script, "ABCD", untraded=[("A", 1)], written_off=[("A", 12)])
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, keep=1, min_hold=0, check="weekly")
    ranks = daily_rankings(panel, rule, days[0], days[-1])
    assert days[1] not in ranks and "A" in ranks[days[5]]                       # next Monday: cannot be sold
    # 1.5.0: only the refused sell is tried again each session (unchanged: no new list); the next Monday decides
    assert not any(days[n] in ranks for n in (6, 7, 8, 9, 11)) and "A" in ranks[days[10]]
    assert ranks[days[12]] == ["B", "C", "D"]                                   # delisted: worthless, off the list
    assert days[13] not in ranks                                                # then back to weekly


def test_hold_trend_keeps_what_still_rises_and_sells_what_does_not():
    """2026-10-13 (使用者：還在漲就留著): out of the keep zone, a holding above its 20-session average stays,
    one below it is sold at once — no fixed minimum."""
    days = weekdays(date(2024, 1, 1), date(2024, 1, 5))
    script = {days[0]: ["A", "B", "C"], days[1]: ["D", "E", "F", "A", "B", "C"]}
    panel = MarketPanel(days, script, "ABCDEF", rising="A")
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, keep=1, min_hold=20, hold="trend")
    assert rule.min_hold == 0
    ranks = daily_rankings(panel, rule, days[0], days[1])
    assert ranks[days[1]] == ["A", "D", "E"]
    plain = DailyRule(name="x", factors={"momentum_3": 1.0})
    assert "hold" not in plain.canonical() and "hold" in rule.canonical()        # old rules keep their hash


def test_rising_is_the_adjusted_close_above_its_20_session_average():
    days = weekdays(date(2024, 1, 1), date(2024, 3, 29))
    up = [10 * 1.01 ** n for n in range(len(days))]
    down = [10 * 0.99 ** n for n in range(len(days))]
    fp = FactorPanel(Panel(market(days, {"1101.TW": dict(zip(days, up)), "1102.TW": dict(zip(days, down))})))
    rising = fp.rising()
    assert rising[fp.row["1101.TW"], -1] and not rising[fp.row["1102.TW"], -1]
    assert not rising[fp.row["1101.TW"], 10]                                    # no 20-session average yet


def test_a_flat_price_does_not_draw_the_inverse_volatility_weight():
    days = weekdays(date(2023, 1, 2), date(2024, 3, 29))
    swing = {"1101.TW": 0.01, "1102.TW": 0.02, "1103.TW": 0.04}
    closes = {symbol: {day: 100 * (1 + size) ** (n % 2) for n, day in enumerate(days)} for symbol, size in swing.items()}
    closes["1104.TW"] = {day: 50.0 for day in days}                            # a price that never moves
    fp = FactorPanel(Panel(market(days, closes)))
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=4, weighting="inverse_vol")
    weights = daily.daily_weights(fp, rule, {days[-1]: [*swing, "1104.TW"]})[days[-1]]
    assert weights["1104.TW"] == pytest.approx(weights["1102.TW"])             # the median, not everything


def test_equal_values_share_a_percentile_and_an_equal_score_goes_to_the_more_liquid_stock():
    """Engine 1.4.2 (2026-10-15): equal factor values were ranked by stock code — where most stocks are equal
    (attention notices, EPS growth at its cap, exactly at the 52-week high) a later-listed code ranked higher."""
    from quant_platform.research.daily import _percentile

    assert list(_percentile(np.array([0.0, 0.0, 0.0, 1.0]))) == pytest.approx([1 / 3, 1 / 3, 1 / 3, 1.0])
    days = weekdays(date(2023, 1, 2), date(2024, 3, 29))
    data = market(days, {f"{1101 + i}.TW": {day: 20.0 for day in days} for i in range(3)})    # no momentum at all
    data.traded_value["1101.TW"] = {day: 6_000_000_000.0 for day in days}
    data.traded_value["1103.TW"] = {day: 9_000_000_000.0 for day in days}                    # the most liquid
    fp = FactorPanel(Panel(data))
    last = len(days) - 1
    single = DailyRule(name="t", factors={"momentum_3": 1.0}, top=3)
    double = DailyRule(name="t", factors={"momentum_3": 1.0, "momentum_6": 1.0}, top=3)
    assert fp.ranked(single, last) == fp.ranked(double, last) == ["1103.TW", "1101.TW", "1102.TW"]


def test_a_weekly_trend_rule_sells_the_session_a_holding_breaks_down():
    """Engine 1.4.3 (2026-10-16; 使用者：為什麼又有 20 天這種限制？好好 review 每一個機制): out of the keep zone and below
    its 20-session average, a holding is sold that session — a weekly rule used to wait for its next weekly decision."""
    from quant_platform.research.daily import daily_rankings

    days = weekdays(date(2023, 1, 2), date(2024, 3, 29))
    crash = next(day for day in days if day >= date(2024, 3, 13) and day.weekday() == 2)          # a Wednesday
    closes = {}
    for i in range(6):
        series = {}
        for n, day in enumerate(days):
            price = 20 * (1 + (i + 1) * 0.0008) ** n
            if i == 5 and day >= crash:
                price *= 0.6                                                   # the strongest falls 40% that day
            series[day] = price
        closes[f"{1101 + i}.TW"] = series
    fp = FactorPanel(Panel(market(days, closes)))
    weekly = DailyRule(name="t", factors={"momentum_3": 1.0}, top=3, keep=1, check="weekly", hold="trend", min_hold=0)
    fixed = weekly.model_copy(update={"hold": "days", "min_hold": 0})
    trend = daily_rankings(fp, weekly, date(2024, 1, 2), date(2024, 3, 29))
    assert crash in trend and "1106.TW" not in trend[crash]                     # sold on the Wednesday
    assert crash not in daily_rankings(fp, fixed, date(2024, 1, 2), date(2024, 3, 29))   # no trend: waits for the week


def test_a_drawdown_within_fifteen_points_is_a_high_risk_t0_candidate():
    """2026-10-18 (使用者：錢賺得多就可以；回撤上限放寬但保留，標註高風險)."""
    from quant_platform.research.daily import PASSING_TIERS, tier

    base = {"full_period_excess": 2.0, "since_2020_excess": 0.5, "benchmark_max_drawdown": -0.34,
            "windows": {"3y": {"count": 30, "win_ratio": 0.9, "median_excess": 0.2}, "1y": {"count": 50, "win_ratio": 0.6}}}
    assert tier({**base, "max_drawdown": -0.38})[0] == "T0 候選"                      # within 5 points
    grade, why = tier({**base, "max_drawdown": -0.45})
    assert grade == "T0 候選（高風險）" and "回撤" in why and grade in PASSING_TIERS    # 11 points deeper
    assert tier({**base, "max_drawdown": -0.54})[0] == "T1"                             # 20 points deeper
    assert tier({**base, "max_drawdown": -0.45, "since_2020_excess": -0.1})[0] == "T2"  # another gate failed
    forward = {"sessions": 60, "excess": 0.01}
    assert tier({**base, "max_drawdown": -0.45}, forward)[0] == "T0（高風險）"


def test_a_winner_grown_past_trim_above_times_its_target_is_sold_back_to_it():
    """2026-10-18: the deep rule held 30% of the account in one stock — with trim_above 2 a holding is never left above
    twice its target on a decision day; the money goes to the picks below theirs. Off, the winner keeps growing."""
    days = weekdays(date(2024, 1, 1), date(2024, 3, 29))
    closes = {"A.TW": {day: 10.0 * 1.03 ** n for n, day in enumerate(days)}}
    closes.update({f"{name}.TW": {day: 10.0 for day in days} for name in "BCD"})
    data = market(days, closes)
    ranks = {day: ["A.TW", "B.TW", "C.TW", "D.TW"] for day in days}

    def shares(rule):
        books = {}
        run = simulate_daily(data, rule, FREE, days[0], days[-1], ranks, SeedPlan(100_000, 0), snapshots=books)
        out = []
        for day in days:
            cash, units = books[day]
            values = {symbol: count * closes[symbol][day] for symbol, count in units.items()}
            out.append((values.get("A.TW", 0.0) / (cash + sum(values.values())), cash))
        return out, run

    plain, _run = shares(DailyRule(name="x", factors={"trend_200": 1.0}, top=4, min_hold=0))
    trimmed, run = shares(DailyRule(name="x", factors={"trend_200": 1.0}, top=4, min_hold=0, trim_above=2.0))
    assert plain[-1][0] > 0.65                                             # left alone, A is two thirds of the account
    assert max(share for share, _cash in trimmed) <= 0.5 + 1e-3            # never above twice its quarter
    assert trimmed[-1][1] < 1_000 and run.sold > 30_000                    # sold back and the money put to work
    assert DailyRule(name="x", factors={"trend_200": 1.0}).rule_hash == \
        DailyRule(name="x", factors={"trend_200": 1.0}, trim_above=0.0).rule_hash   # off: the same rule


def test_each_entry_and_exit_says_why():
    """2026-10-19 (使用者：每一筆買賣要有原因): the ranking says why a stock came in or left on the day it decided."""
    days = weekdays(date(2024, 1, 1), date(2024, 1, 5))
    script = {days[0]: ["A", "B", "C", "D", "E"], days[1]: ["D", "E", "F", "A", "B", "C"],
              days[2]: ["D", "E", "F", "A", "B", "C"]}
    panel = ScriptedPanel(days, script)
    base = {"name": "x", "factors": {"momentum_3": 1.0}, "top": 3, "keep": 1}
    trace = {}
    daily_rankings(panel, DailyRule(**base, min_hold=0), days[0], days[2], trace=trace)
    assert trace["why"][days[0]]["A"] == "新進前 3 名（當天第 1 名）"
    assert trace["why"][days[1]]["D"] == "新進前 3 名（當天第 1 名）"
    assert trace["why"][days[1]]["A"] == "跌出前 3 名（當天第 4 名）"
    held = {}
    daily_rankings(panel, DailyRule(**base, min_hold=2), days[0], days[2], trace=held)
    assert days[1] not in held["why"]                                     # kept: no trade, nothing to explain
    assert held["why"][days[2]]["C"] == "跌出前 3 名（當天第 6 名）；這個策略設定買進後至少持有 2 個交易日，已滿"


def test_a_stop_loss_exit_says_so():
    days = weekdays(date(2024, 1, 1), date(2024, 1, 12))
    script = {day: ["A", "B", "C", "D", "E"] for day in days}
    a = [100.0, 100.0, 100.0, 120.0, 110.0, 101.0] + [101.0] * (len(days) - 6)
    panel = PricedPanel(days, script, {name: a if name == "A" else [100.0] * len(days) for name in "ABCDE"})
    trace = {}
    daily_rankings(panel, DailyRule(name="x", factors={"trend_200": 1.0}, top=3, keep=1, stop_loss=0.15),
                   days[0], days[-1], trace=trace)
    # A, stopped out, is barred from the day's ranking: D is third among the rest
    assert trace["why"][days[5]] == {"A": "從買進後的最高價跌超過 15%（停損）", "D": "新進前 3 名（當天第 3 名）"}


def test_a_score_ranked_within_its_volatility_tenth():
    """2026-10-20: the deep score ran −0.70 with low volatility — ranked within the day's volatility tenths, a volatile
    stock no longer ranks high for being volatile."""
    from quant_platform.research.daily import volatility_neutral

    volatility = np.arange(100, dtype=float)[:, None]               # one day, 100 stocks, ten tenths of 10
    score = volatility.copy()                                        # the score is the volatility itself
    score[5] = 1_000.0                                               # a calm stock the model likes most
    out = volatility_neutral(score, volatility)
    assert out.shape == (100, 1)
    assert out[5, 0] == 1.0 and out[9, 0] == pytest.approx(0.9)      # first tenth: stock 5 top, 9 second
    assert out[99, 0] == 1.0 and out[90, 0] == pytest.approx(0.1)    # the top of every tenth ties at 1
    few = volatility_neutral(score[:40], volatility[:40])
    assert np.isnan(few).all()                                       # fewer than 50 stocks: nothing


def test_industry_factors_by_hand():
    """2026-10-20: the industry's average daily log return summed over the window; the laggard subtracts the
    stock's own; unclassified stocks and industries with fewer than five stocks get none."""
    days = weekdays(date(2023, 1, 2), date(2023, 12, 29))
    rates = {"1101.TW": 0.001, "1102.TW": 0.002, "1103.TW": 0.003, "1104.TW": 0.004, "1105.TW": -0.001,
             "2301.TW": 0.002, "2302.TW": 0.001, "9999.TW": 0.005}
    closes = {symbol: {day: 10 * (1 + rate) ** n for n, day in enumerate(days)} for symbol, rate in rates.items()}
    industries = {"1101": "水泥", "1102": "水泥", "1103": "水泥", "1104": "水泥", "1105": "水泥",
                  "2301": "電子", "2302": "電子"}
    fp = FactorPanel(Panel(market(days, closes)), industries=industries)
    last = len(days) - 1
    cement = np.mean([np.log1p(rates[f"110{n}.TW"]) for n in range(1, 6)])
    row = fp.symbols.index("1103.TW")
    assert fp.matrix("industry_mom_1")[row, last] == pytest.approx(21 * cement, rel=1e-4)
    assert fp.matrix("industry_mom_6")[row, last] == pytest.approx(126 * cement, rel=1e-4)
    assert fp.matrix("industry_laggard_5")[row, last] == pytest.approx(5 * (cement - np.log1p(0.003)), abs=1e-6)
    assert fp.matrix("industry_mom_1")[fp.symbols.index("1101.TW"), last] == pytest.approx(21 * cement, rel=1e-4)
    assert np.isnan(fp.matrix("industry_mom_1")[fp.symbols.index("2301.TW"), last])     # two stocks: too few
    assert np.isnan(fp.matrix("industry_mom_1")[fp.symbols.index("9999.TW"), last])     # unclassified
    assert np.isnan(fp.matrix("industry_mom_6")[row, 90])                                # under 80% of six months


def test_a_winner_up_half_and_above_its_average_runs_on():
    """2026-10-20: winner_hold — a holding leaving the keep zone stays while it is up 50% since bought and rising."""
    days = weekdays(date(2024, 1, 1), date(2024, 1, 12))
    script = {day: ["A", "B", "C", "E"] if number < 3 else ["B", "C", "D", "E", "A"] for number, day in enumerate(days)}

    class WinnerPanel(PricedPanel):
        def __init__(self, prices, up):
            super().__init__(days, script, prices, [True] * len(days))
            self.symbols = sorted(prices)
            self.row = {symbol: row for row, symbol in enumerate(self.symbols)}
            self.up = up

        def rising(self):
            return np.array([self.up[symbol] for symbol in self.symbols])

    flat = [100.0] * len(days)
    climb = [100.0, 120.0, 140.0] + [160.0] * (len(days) - 3)          # up 60% when it leaves the top three
    always, never = [True] * len(days), [False] * len(days)
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, keep=1, min_hold=0, winner_hold=0.5)
    assert rule.canonical()["winner_hold"] == 0.5
    assert "winner_hold" not in DailyRule(name="x", factors={"trend_200": 1.0}).canonical()
    prices = {"A": climb, "B": flat, "C": flat, "D": flat, "E": flat}
    runs = daily_rankings(WinnerPanel(prices, {name: always for name in "ABCDE"}), rule, days[0], days[-1])
    assert runs[days[0]] == ["A", "B", "C"] and set(runs[days[3]]) == {"A", "B", "C"}   # A runs on; D waits
    plain = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, keep=1, min_hold=0)
    assert daily_rankings(WinnerPanel(prices, {name: always for name in "ABCDE"}), plain, days[0], days[-1])[days[3]] == ["B", "C", "D"]
    falling = {"A": never, "B": always, "C": always, "D": always, "E": always}   # up 60% but below its average
    assert daily_rankings(WinnerPanel(prices, falling), rule, days[0], days[-1])[days[3]] == ["B", "C", "D"]
    small = {**prices, "A": [100.0, 110.0, 120.0] + [140.0] * (len(days) - 3)}   # up only 40%
    assert daily_rankings(WinnerPanel(small, {name: always for name in "ABCDE"}), rule, days[0], days[-1])[days[3]] == ["B", "C", "D"]
