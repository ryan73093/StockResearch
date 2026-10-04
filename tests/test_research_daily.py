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
    assert len(rules) == 2 * (len(daily.FACTOR_LABELS) + len(daily.OSCILLATORS))
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
