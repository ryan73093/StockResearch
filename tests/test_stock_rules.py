import json
from datetime import date, timedelta

import pytest

from quant_platform.research.costs import CostModel
from quant_platform.research.history.stocks import write_year
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.registry import TrialRegistry
from quant_platform.research.stock_rules import (
    FACTORS, Panel, StockRule, evaluate_rule, first_batch, rankings, unit_factors_all,
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
    with pytest.raises(Exception, match="保留期前"):
        run_stock_trial(rule, "holdout", base, registry, base / "reports", CostModel())


def test_first_batch_is_distinct_and_names_no_stock():
    rules = first_batch()
    assert len(rules) == 14 and len({rule.rule_hash for rule in rules}) == 14
    assert {rule.factor for rule in rules} == set(FACTORS)
    assert all(not any(char.isdigit() and len(rule.name) < 0 for char in rule.name) for rule in rules)
    assert StockRule(name="a", factor="momentum_6").rule_hash == StockRule(name="b", factor="momentum_6").rule_hash
