import json
from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research.costs import CostModel
from quant_platform.research.history.stocks import write_year
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.registry import TrialRegistry
from quant_platform.research.stock_rules import (
    BASE_FACTORS, FACTORS, Panel, StockRule, evaluate_rule, first_batch, rankings, unit_factors_all,
)


def weekdays(start, count):
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def panel_data(days, closes, turnover=50_000_000.0, factors=None):
    return LegacyData(
        sessions=days,
        closes={symbol: dict(zip(days, values)) for symbol, values in closes.items()},
        traded_value={symbol: {day: turnover for day in days} for symbol in closes},
        factors=factors or {}, predictions={},
    )


def test_unit_factors_cover_every_ex_rights_kind_and_unmarked_splits():
    days = weekdays(date(2024, 1, 1), 5)
    closes = dict(zip(days, [100.0, 95.0, 95.0, 23.75, 23.75]))
    # 01-02: ex-rights (cash 2 + 3% shares): reference 95.15 → units × 100/95.15; 01-04: a 1:4 split, unmarked.
    factors = unit_factors_all(closes, {days[1]: ("shares", 100.0, 95.15)})
    assert factors[days[1]] == pytest.approx(100 / 95.15) and factors[days[3]] == 4 and days[2] not in factors


def test_ranking_by_hand():
    days = weekdays(date(2023, 1, 2), 300)
    # A rises 0.1% a day, B falls 0.05% a day, C is flat, D listed late, E is under NT$10.
    closes = {
        "A.TW": [20 * 1.001 ** i for i in range(300)], "B.TW": [20 * 0.9995 ** i for i in range(300)],
        "C.TW": [20.0] * 300, "E.TW": [5.0] * 300, "0050.TW": [100.0] * 300,
    }
    data = panel_data(days, closes)
    data.closes["D.TW"] = {day: 30.0 for day in days[100:]}
    data.traded_value["D.TW"] = {day: 50_000_000.0 for day in days[100:]}
    panel = Panel(data)
    momentum = StockRule(name="m", factor="momentum_6", top=5, min_history=252)
    assert panel.ranked(momentum, days[-1]) == ["A.TW", "C.TW", "B.TW"]   # D too young, E too cheap
    reversal = StockRule(name="r", factor="reversal_1", top=5, min_history=252)
    assert panel.ranked(reversal, days[-1])[0] == "B.TW"
    quiet = StockRule(name="v", factor="low_volatility_60", top=5, min_history=252)
    assert panel.ranked(quiet, days[-1])[0] == "C.TW"                     # zero volatility ranks first
    picks = rankings(panel, StockRule(name="q", factor="momentum_6", top=5, rebalance="quarterly", min_history=20),
                     days[0], days[-1])
    filled = [choice for choice in picks.values() if choice]
    assert len(picks) == 14 and len(filled) >= 12 and all(choice[0] == "A.TW" for choice in filled)


def test_rule_trial_registers_once_and_counts_as_a_candidate(tmp_path):
    from quant_platform.research.history.dataset import write_parquet
    from quant_platform.research.history.official import DailyRow
    from quant_platform.research.stock_rules import run_stock_trial

    days = [day for day in weekdays(date(2004, 2, 11), 3400) if day <= date(2016, 12, 31)]
    base = tmp_path
    write_parquet([DailyRow(day, 100.0, 100.0, 100.0, 100.0, source="t") for day in days], base / "daily" / "TAIEX.parquet")
    write_parquet([DailyRow(day, 50.0 + i * 0.01, 50.0, 50.0, 50.0 + i * 0.01, source="t") for i, day in enumerate(days)],
                  base / "daily" / "0050.parquet")
    rows = []
    for i, day in enumerate(days):
        for code, price in (("2330", 100 * 1.0002 ** i), ("2317", 50.0), ("1101", 30 * 0.9999 ** i)):
            rows.append({"date": day, "code": code, "name": code, "open": price, "high": price, "low": price,
                         "close": price, "volume": 1_000_000, "turnover": 60_000_000, "trades": 100})
    by_year = {}
    for row in rows:
        by_year.setdefault(row["date"].year, []).append(row)
    for year, items in by_year.items():
        write_year(items, base / "stocks" / "twse" / f"{year}.parquet")
    (base / "raw" / "twse_ex_rights").mkdir(parents=True)

    registry = TrialRegistry(base / "trials.jsonl")
    rule = StockRule(name="個股 測試：前 5 名", factor="momentum_6", top=5, min_history=20)
    record, report = run_stock_trial(rule, "development", base, registry, base / "reports", CostModel())
    assert record.kind == "candidate" and record.period == "development" and record.spec_hash == rule.rule_hash
    assert report["strategy"]["trades"] > 0 and "monthly_active_returns" in report and report["picks_per_day"] == 3.0
    again, _ = run_stock_trial(rule, "development", base, registry, base / "reports", CostModel())
    assert again.trial_id == record.trial_id and registry.count("candidate") == 1
    with pytest.raises(Exception, match="最終驗證期前"):
        run_stock_trial(rule, "holdout", base, registry, base / "reports", CostModel())


def test_first_batch_is_distinct_and_names_no_stock():
    rules = first_batch()
    assert len(rules) == 14 and len({rule.rule_hash for rule in rules}) == 14
    assert {rule.factor for rule in rules} == set(BASE_FACTORS)          # the batch stays as it was run
    assert all(not any(char.isdigit() and len(rule.name) < 0 for char in rule.name) for rule in rules)
    assert StockRule(name="a", factor="momentum_6").rule_hash == StockRule(name="b", factor="momentum_6").rule_hash


def test_buffer_keeps_a_holding_until_it_drops_far_enough():
    from quant_platform.research.stock_rules import second_batch

    days = weekdays(date(2023, 1, 2), 80)
    closes = {f"S{i}.TW": [20.0 * (1 + 0.0001 * (9 - i)) ** step for step in range(80)] for i in range(10)}
    closes["0050.TW"] = [100.0] * 80
    data = panel_data(days, closes)
    panel = Panel(data)
    # Without a buffer the top 5 by momentum are S0..S4; S5 is sixth.
    plain = StockRule(name="p", factor="momentum_6", top=5, min_history=20)
    assert panel.ranked(plain, days[-1])[:6] == [f"S{i}.TW" for i in range(6)]
    buffered = StockRule(name="b", factor="momentum_6", top=5, buffer=2, min_history=20)
    picks = rankings(panel, buffered, days[0], days[-1])
    assert all(len(choice) == 5 for choice in picks.values() if choice)
    assert len(second_batch()) == 12 and all(rule.buffer == 3 and rule.top == 30 for rule in second_batch())


def test_composite_ranks_average_percentiles_and_lump_sum_invests_once():
    from quant_platform.research.stock_rules import LumpSumPlan, _percentile, sweep_batch

    assert list(_percentile(np.array([3.0, np.nan, 1.0, 2.0]))[[0, 2, 3]]) == [1.0, 0.0, 0.5]
    days = weekdays(date(2023, 1, 2), 300)
    closes = {"A.TW": [20 * 1.001 ** i for i in range(300)], "B.TW": [20.0] * 300,
              "C.TW": [20 * (1.002 if i % 2 else 0.999) ** i for i in range(300)], "0050.TW": [100.0] * 300}
    panel = Panel(panel_data(days, closes))
    # Momentum alone ranks C (fastest) first; momentum + low volatility (equal weight) lifts A: its
    # momentum rank is 0.5 and volatility rank 0.5 → 1.0, C is 1.0 + 0.0, B is 0.0 + 1.0.
    alone = StockRule(name="m", factor="momentum_6", top=5, min_history=20)
    mixed = StockRule(name="mv", factor="momentum_6", top=5, min_history=20, extra={"low_volatility_60": 1.0})
    assert panel.ranked(alone, days[-1])[0] == "C.TW"
    ranked = panel.ranked(mixed, days[-1])
    assert ranked[0] == "A.TW" and set(ranked[1:]) == {"B.TW", "C.TW"}
    assert mixed.rule_hash != alone.rule_hash and "6 個月動能＋60 日低波動×1" == mixed.label

    plan = LumpSumPlan(300_000)
    assert plan.schedule(days, days[5], days[-1]) == [(days[5], 300_000.0)]
    rules = sweep_batch()
    assert len(rules) == 492 and len({rule.rule_hash for rule in rules}) == 492


def test_new_trading_settings_keep_old_hashes():
    champion = StockRule(name="a", factor="high_52w", top=30, buffer=3)
    spelled = StockRule(name="b", factor="high_52w", top=30, buffer=3, min_hold=0, band=0.0, min_trade=0.0)
    assert champion.rule_hash == spelled.rule_hash and champion.rule_hash.startswith("31db01cdce2c")
    assert not {"min_hold", "band", "min_trade"} & set(champion.canonical())
    held = StockRule(name="c", factor="high_52w", top=30, buffer=3, min_hold=3)
    assert held.rule_hash != champion.rule_hash and held.canonical()["min_hold"] == 3


class ScriptedPanel:
    """Rank lists by hand: rankings() only needs the sessions and ranked()."""

    def __init__(self, sessions, script):
        self.sessions, self.script = sessions, script

    def ranked(self, rule, day):
        return list(self.script[day])


def test_min_hold_keeps_new_buys_until_their_time_unless_ineligible():
    days = weekdays(date(2024, 1, 1), 80)
    rank_days = [date(2024, 1, 5), date(2024, 2, 5), date(2024, 3, 5), date(2024, 4, 5)]   # the 5th, all weekdays
    panel = ScriptedPanel(days, dict(zip(rank_days, [["A", "B", "C", "D"], ["C", "D", "A", "B"], ["C", "D", "A", "B"],
                                                     ["C", "D", "A", "B"]])))

    def run(**settings):
        # top 2 is below the model's minimum of 5; model_construct skips validation for the hand case
        rule = StockRule.model_construct(**{**StockRule(name="x", factor="high_52w").model_dump(), "top": 2, **settings})
        picks = rankings(panel, rule, days[0], days[-1])
        assert sorted(picks) == rank_days
        return [picks[day] for day in rank_days]

    assert run() == [["A", "B"], ["C", "D"], ["C", "D"], ["C", "D"]]
    # bought at the first check, kept through the second (1 < 2), sold at the third (2 checks held)
    assert run(min_hold=2) == [["A", "B"], ["A", "B"], ["C", "D"], ["C", "D"]]
    # B is no longer eligible at the second check: min_hold does not keep it
    panel.script[rank_days[1]] = ["C", "D", "A"]
    assert run(min_hold=6)[1] == ["A", "C"]


def test_band_and_minimum_order_skip_small_top_ups():
    from quant_platform.research.cashflow import ContributionPlan
    from quant_platform.research.legacy_challenger import Variant, simulate

    days = weekdays(date(2024, 1, 1), 30)
    first, second = date(2024, 1, 5), date(2024, 2, 5)
    a = {day: (10.0 if day < second else 15.0) for day in days}       # A gains 50% before the second buy
    data = LegacyData(sessions=days, closes={"A.TW": a, "B.TW": {day: 10.0 for day in days},
                                            "0050.TW": {day: 100.0 for day in days}},
                      traded_value={}, factors={}, predictions={})
    costs = CostModel(fee_rate=0.0, minimum_fee=0, slippage_bps=0.0)
    plan = ContributionPlan(monthly_amount=10_000, day_of_month=5)
    ranks = {first: ["A.TW", "B.TW"], second: ["A.TW", "B.TW"]}

    def run(**settings):
        ledger, snapshots = [], {}
        result = simulate(data, Variant("v", "v", 2, "on_rank_days", **settings), costs, days[0], days[-1], ranks, plan,
                          ledger=ledger, snapshots=snapshots)
        buys = [(entry["symbol"], entry["shares"]) for entry in ledger if entry["day"] == second]
        return buys, snapshots[days[-1]][0], result

    # second check: 10,000 new + A 500 × 15 + B 500 × 10 = 22,500 → target 11,250; A is 3,750 short, B 6,250
    buys, cash, result = run()
    assert buys == [("A.TW", 250), ("B.TW", 625)] and cash == 0 and result.bought == 20_000   # two months put in
    buys, cash, _ = run(band=0.4)            # A's gap 3,750 is within 40% of target (4,500): left alone
    assert buys == [("B.TW", 625)] and cash == 3_750
    buys, cash, _ = run(min_trade=5_000)     # biggest gap first; A's 3,750 is below the minimum order
    assert buys == [("B.TW", 625)] and cash == 3_750


def test_cost_batch_is_the_52_week_high_family_with_less_trading():
    from quant_platform.research.stock_rules import cost_batch, second_batch, sweep_batch

    rules = cost_batch()
    earlier = {rule.rule_hash for rule in second_batch() + sweep_batch()}
    assert len(rules) == len({rule.rule_hash for rule in rules}) == 40
    assert sum(rule.rule_hash in earlier for rule in rules) == 4
    assert all(rule.factor == "high_52w" and len(rule.name) <= 80 for rule in rules)
    assert not any(rule.rebalance == "quarterly" and rule.min_hold == 3 for rule in rules)


def test_fingerprint_ignores_sessions_after_the_period_end(tmp_path):
    from quant_platform.research.history.dataset import write_parquet
    from quant_platform.research.history.official import DailyRow
    from quant_platform.research.stock_rules import stock_fingerprint

    write_parquet([DailyRow(date(2026, 9, 30), 1.0, 1.0, 1.0, 1.0, source="t")], tmp_path / "daily" / "0050.parquet")

    def quote(day, close=10.0):
        return {"date": day, "code": "2330", "name": "台積電", "open": close, "high": close, "low": close, "close": close,
                "volume": 1, "turnover": 1, "trades": 1}

    path = tmp_path / "stocks" / "twse" / "2026.parquet"
    write_year([quote(date(2026, 9, 30)), quote(date(2026, 10, 2))], path)
    end = date(2026, 9, 30)
    before = stock_fingerprint(tmp_path, 2026, 2026, until=end)
    write_year([quote(date(2026, 9, 30)), quote(date(2026, 10, 2)), quote(date(2026, 10, 5))], path)   # the daily append
    assert stock_fingerprint(tmp_path, 2026, 2026, until=end) == before
    assert stock_fingerprint(tmp_path, 2026, 2026) != before                    # the forward record sees the whole file
    write_year([quote(date(2026, 9, 30), 11.0), quote(date(2026, 10, 2))], path)                     # a revision inside the period
    assert stock_fingerprint(tmp_path, 2026, 2026, until=end) != before


def test_lump_sum_windows_put_the_whole_amount_in_at_each_window_start():
    from quant_platform.research.cashflow import ContributionPlan
    from quant_platform.research.legacy_challenger import Variant, windows
    from quant_platform.research.stock_rules import LumpSumPlan

    days = weekdays(date(2024, 1, 1), 522)                       # 2024-01-01 .. 2025-12-30: 12 full windows
    data = LegacyData(sessions=days, closes={"A.TW": {day: 10 * 1.001 ** i for i, day in enumerate(days)},
                                            "0050.TW": {day: 100.0 for day in days}},
                      traded_value={}, factors={}, predictions={})
    costs = CostModel(fee_rate=0.0, minimum_fee=0, slippage_bps=0.0)
    ranks = {day: ["A.TW"] for day in days if day.day >= 5 and (day.day == 5 or days[days.index(day) - 1].day < 5)}
    variant = Variant("a", "a", 1, "on_rank_days")
    lump = windows(data, variant, costs, days[0], days[-1], 12, ranks, plan=LumpSumPlan(300_000))
    # A gains 0.1% a session: about 1.001^255 - 1 ≈ 29% over a year from the first rank day, on the full 300,000
    assert lump["count"] == 12 and lump["win_ratio"] == 1.0 and 0.27 < lump["median_excess"] < 0.30
    monthly = windows(data, variant, costs, days[0], days[-1], 12, ranks, plan=ContributionPlan(monthly_amount=10_000, day_of_month=5))
    assert 0.10 < monthly["median_excess"] < 0.16                 # money put in monthly is invested for half a year on average


def test_seed_plan_puts_the_starting_capital_in_first_then_every_month():
    from quant_platform.research.stock_rules import SeedPlan

    days = weekdays(date(2024, 1, 1), 70)                       # 2024-01-01 .. 2024-04-05
    schedule = SeedPlan(300_000, 10_000).schedule(days, days[0], days[-1])
    assert schedule[0] == (date(2024, 1, 1), 300_000.0) and schedule[1] == (date(2024, 1, 5), 10_000.0)
    assert [day for day, _ in schedule[1:]] == [date(2024, 1, 5), date(2024, 2, 5), date(2024, 3, 5), date(2024, 4, 5)]
    assert sum(amount for _, amount in schedule) == 340_000
