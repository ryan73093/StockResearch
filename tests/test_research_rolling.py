"""2026-10-16 (使用者：訓練到兩個月前，後面兩個月拿來做前向測試；模型也要每月更新): the monthly rolling forward test."""
from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import daily, model, rolling
from quant_platform.research.costs import CostModel
from quant_platform.research.daily import DailyRule, FactorPanel
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


def market(days, stocks=12, seed=2):
    rng = np.random.default_rng(seed)
    drift = np.linspace(-0.0006, 0.0008, stocks)
    closes = {f"{1101 + n}.TW": dict(zip(days, np.maximum(30 * np.cumprod(1 + drift[n] + rng.normal(0, 0.01, len(days))), 11.0)))
              for n in range(stocks)}
    closes["0050.TW"] = {day: 50.0 * (1.0002 ** n) for n, day in enumerate(days)}
    return LegacyData(sessions=days, closes=closes, traded_value={s: {d: 5e9 for d in v} for s, v in closes.items()},
                      factors={}, predictions={})


def test_the_cutoff_is_the_end_of_the_month_two_months_back():
    assert rolling.cutoff_for(date(2026, 10, 16)) == date(2026, 8, 31)
    assert rolling.cutoff_for(date(2026, 11, 2)) == date(2026, 9, 30)
    assert rolling.cutoff_for(date(2027, 1, 5)) == date(2026, 11, 30)
    assert rolling.cutoff_for(date(2027, 3, 31)) == date(2027, 1, 31)


def test_an_evaluation_to_the_cutoff_is_the_full_one_cut_there(monkeypatch):
    monkeypatch.setattr(daily, "RECENT_START", date(2024, 1, 1))
    monkeypatch.setattr(daily, "REGIME_START", date(2024, 4, 1))
    monkeypatch.setattr(daily, "RECENT_END", date(2024, 12, 31))
    monkeypatch.setattr(daily, "WINDOWS", {"1y": 3})
    days = weekdays(date(2023, 1, 2), date(2024, 12, 31))
    data = market(days)
    fp = FactorPanel(Panel(data))
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, hold="trend")
    cut = daily.evaluate(data, fp, rule, FREE, end=date(2024, 8, 30))
    assert cut["end"] == "2024-08-30" and cut["strategy"]["final_value"] > 0
    full = daily.evaluate(data, fp, rule, FREE)
    assert cut["windows"]["1y"]["count"] < full["windows"]["1y"]["count"]   # only the windows that end by the cutoff
    assert cut["strategy"]["final_value"] != full["strategy"]["final_value"]


def test_the_holdout_is_a_fresh_account_after_the_cutoff_against_0050():
    days = weekdays(date(2024, 1, 1), date(2024, 12, 31))
    data = market(days)
    fp = FactorPanel(Panel(data))
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, hold="trend", min_history=20)
    rows = [{"trial": 1, "rule_hash": rule.rule_hash, "name": rule.name, "spec": rule.canonical()}]
    found = rolling.holdout(data, fp, rows, date(2024, 10, 31), FREE)
    item = found[0]
    assert item["start"] == "2024-11-01" and item["end"] == "2024-12-31" and item["sessions"] == 43
    assert item["contributed"] == 300_000 + 10_000 * 2                       # 11/5 and 12/5
    assert item["usable"] == (item["value"] >= item["benchmark_value"])


def test_a_monthly_model_scores_only_the_sessions_after_its_cutoff(monkeypatch, tmp_path):
    monkeypatch.setattr(model, "WARMUP", 60)
    days = weekdays(date(2016, 1, 1), date(2018, 6, 29))
    data = market(days, stocks=30)
    fp = FactorPanel(Panel(data))
    out = tmp_path / "gbm-x"
    model.train(fp, out, years=[2018], label="excess", version="gbm-1.1.0")
    before = model.scores(fp, out)
    found = model.train_until(fp, out, date(2018, 3, 30), "excess", "gbm-1.1.0")
    after = model.scores(fp, out)
    assert (out / "monthly" / "2018-03-30.pkl").is_file() and found["samples"] > 0
    cut = fp.index[date(2018, 3, 30)]
    assert np.allclose(np.nan_to_num(before[: cut + 1]), np.nan_to_num(after[: cut + 1]))   # on and before: the yearly model
    assert not np.allclose(np.nan_to_num(before[cut + 1:]), np.nan_to_num(after[cut + 1:]))  # after: the monthly one
    assert model.digest(out) != "" and "monthly" in str(sorted(out.rglob("*.pkl")))


def test_the_worker_starts_the_month_once_then_refreshes_the_holdout_each_trading_day(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.scheduler.runner import start_rolling

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'r.db'}"))
    zone = ZoneInfo("Asia/Taipei")
    started = []
    friday = datetime(2026, 10, 16, 22, 30, tzinfo=zone)
    assert start_rolling(container, friday, launch=started.append) == "rolling"
    assert start_rolling(container, friday, launch=started.append) is None          # the month is running, not done
    (tmp_path / "research" / "rolling" / "2026-08-31.json").write_text("{}", encoding="utf-8")
    monday = datetime(2026, 10, 19, 22, 30, tzinfo=zone)
    assert start_rolling(container, monday, launch=started.append) == "rolling-holdout"
    assert start_rolling(container, monday, launch=started.append) is None          # once a day
    assert start_rolling(container, datetime(2026, 10, 24, 22, 30, tzinfo=zone), launch=started.append) is None  # Saturday
    container.market_calendar.add_manual_closure(datetime(2026, 10, 23).date(), "颱風休市")
    assert start_rolling(container, datetime(2026, 10, 23, 22, 30, tzinfo=zone), launch=started.append) is None  # typhoon
    november = datetime(2026, 11, 2, 22, 30, tzinfo=zone)
    assert start_rolling(container, november, launch=started.append) == "rolling"   # a new cutoff: 2026-09-30
    assert started == ["rolling", "rolling-holdout", "rolling"]


def test_a_blend_is_graded_to_the_cutoff_and_run_after_it_like_a_rule(monkeypatch):
    """2026-10-18: a blend of daily rules (no 0050 part) goes through the month like a single rule — graded on data up
    to the cutoff, then a fresh account after it, the sum of its sleeves."""
    from quant_platform.research.blend import BlendRule, Sleeve, evaluate_blend

    monkeypatch.setattr(daily, "RECENT_START", date(2024, 1, 1))
    monkeypatch.setattr(daily, "REGIME_START", date(2024, 4, 1))
    monkeypatch.setattr(daily, "RECENT_END", date(2024, 12, 31))
    monkeypatch.setattr(daily, "WINDOWS", {"1y": 3})
    days = weekdays(date(2023, 1, 2), date(2024, 12, 31))
    data = market(days)
    fp = FactorPanel(Panel(data))
    one = DailyRule(name="a", factors={"momentum_3": 1.0}, top=3, hold="trend", min_history=20)
    two = DailyRule(name="b", factors={"momentum_6": 1.0}, top=3, hold="trend", min_history=20)
    blend = BlendRule(name="組合：a 60%＋b 40%", sleeves=(Sleeve(rule=one, share=0.6), Sleeve(rule=two, share=0.4)))
    cut = evaluate_blend(data, fp, blend, FREE, end=date(2024, 8, 30))
    assert cut["end"] == "2024-08-30" and cut["strategy"]["final_value"] > 0
    rows = [{"trial": 9, "rule_hash": blend.rule_hash, "name": blend.name, "spec": blend.canonical()}]
    item = rolling.holdout(data, fp, rows, date(2024, 10, 31), FREE)[0]
    assert item["start"] == "2024-11-01" and item["contributed"] == 300_000 + 10_000 * 2
    parts = [rolling.holdout(data, fp, [{"trial": n, "rule_hash": rule.rule_hash, "name": rule.name,
                                         "spec": rule.canonical()}], date(2024, 10, 31), FREE)[0]
             for n, rule in enumerate((one, two))]
    # each sleeve gets its share of every amount; with no costs the blend is the shares of the two accounts
    assert item["value"] == pytest.approx(0.6 * parts[0]["value"] + 0.4 * parts[1]["value"], rel=0.02)


def test_the_months_rules_include_blends_without_0050(tmp_path):
    import json

    from quant_platform.research.blend import BlendRule, Sleeve
    from quant_platform.research.registry import TrialRegistry

    one = DailyRule(name="a", factors={"momentum_3": 1.0}, top=3)
    two = DailyRule(name="b", factors={"momentum_6": 1.0}, top=3)
    stocks = BlendRule(name="組合：a＋b", sleeves=(Sleeve(rule=one, share=0.5), Sleeve(rule=two, share=0.5)))
    cored = BlendRule(name="組合：a＋b＋0050", core=0.2, sleeves=(Sleeve(rule=one, share=0.4), Sleeve(rule=two, share=0.4)))
    reports = tmp_path / "reports"
    reports.mkdir()
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    for number, spec in enumerate((one, stocks, cored), start=1):
        (reports / f"{number}.json").write_text(json.dumps({"spec": spec.canonical()}, ensure_ascii=False), encoding="utf-8")
        registry.register(spec_hash=spec.rule_hash, spec_name=spec.name, kind="candidate", period="recent",
                          input_hash=str(number), data_fingerprint="x", metrics={"engine": "daily-1.5.0+blend-1.0.0"},
                          report_file=f"{number}.json")
    names = [item["spec"]["name"] for item in rolling.rules(tmp_path)]
    assert names == ["a", "組合：a＋b"]                                   # the blend with 0050 is left out


def test_the_month_forward_test_is_read_from_the_forward_accounts_records():
    # 2026-10-21 (使用者：每個月都要跑那麼久，我時間很多是不是): the forward accounts already run the month's passing
    # rules from the first session after the cutoff every day — read their records, run only the ones without them
    def day(when, value, paid, bench, trades):
        return {"date": when, "rule_hash": "a", "since": "2024-11-01", "value": value, "contributed": paid,
                "benchmark_value": bench, "trades_total": trades}

    records = [day("2024-11-01", 300_000, 300_000, 300_000, 5), day("2024-11-04", 330_000, 300_000, 303_000, 6),
               day("2024-11-05", 307_000, 310_000, 313_000, 6),
               {"date": "2024-11-05", "rule_hash": "old", "since": "2024-10-01", "value": 1, "contributed": 1,
                "benchmark_value": 1},                                   # an account of another month: not this one
               {"date": "2024-11-04", "rule_hash": "late", "since": "2024-11-01", "value": 1, "contributed": 1,
                "benchmark_value": 1}]                                   # not recorded through the newest day
    rows = [{"trial": 1, "rule_hash": "a", "name": "A"}, {"trial": 2, "rule_hash": "old", "name": "O"},
            {"trial": 3, "rule_hash": "late", "name": "L"}, {"trial": 4, "rule_hash": "none", "name": "N"}]
    found, missing = rolling.from_records(records, rows, date(2024, 10, 31))
    assert [row["rule_hash"] for row in missing] == ["old", "late", "none"]
    item = found[0]
    assert (item["start"], item["end"], item["sessions"], item["trades"]) == ("2024-11-01", "2024-11-05", 3, 6)
    assert item["value"] == 307_000 and item["benchmark_value"] == 313_000 and item["contributed"] == 310_000
    assert item["excess"] == pytest.approx((307_000 - 313_000) / 310_000, abs=1e-6) and item["usable"] is False
    # units: 1 → 1.1 → (307,000 − 10,000) / 330,000 × 1.1 = 0.99; the deepest fall from 1.1 to 0.99
    assert item["return"] == pytest.approx(-0.01, abs=1e-6) and item["drawdown"] == pytest.approx(-0.1, abs=1e-4)


def test_a_hopeless_t3_rule_carries_last_months_grade_except_each_quarter(tmp_path):
    # 2026-10-21 (使用者：每個月都要跑那麼久): under 70% of 0050 last month cannot pass a month later
    # 2026-10-27: judged on the gate's track, the account from 2020-10 (a row without its money is run again)
    def row(rule_hash, tier, value):
        return {"rule_hash": rule_hash, "tier": tier, "passed": False,
                "metrics": {"start": "2015-06-01", "final_value": 500.0, "benchmark_final_value": 100.0,
                            "since_2020_final_value": value, "since_2020_benchmark_final_value": 100.0}}

    old = {"rule_hash": "old", "tier": "T3", "metrics": {"final_value": 10.0, "benchmark_final_value": 100.0}}
    short = {"rule_hash": "short", "tier": "T3", "metrics": {"start": "2021-03-01", "final_value": 50.0,
                                                             "benchmark_final_value": 100.0}}
    rolling.save(tmp_path, date(2026, 10, 31), {"cutoff": "2026-10-31", "engine": "daily-1.5.0",
                                                 "graded": [row("far", "T3", 60.0), row("near", "T3", 80.0),
                                                            row("t2", "T2", 50.0), {"rule_hash": "bad", "error": "x"},
                                                            old, short]})
    found = rolling.carried(tmp_path, date(2026, 11, 30), "daily-1.5.0")
    assert [item["rule_hash"] for item in found] == ["far", "short"] and found[0]["carried_from"] == "2026-10-31"
    assert rolling.carried(tmp_path, date(2026, 12, 31), "daily-1.5.0") == []      # a quarter: every rule again
    assert rolling.carried(tmp_path, date(2026, 11, 30), "daily-1.6.0") == []      # a new engine: every rule again
    assert rolling.carried(tmp_path, date(2026, 10, 31), "daily-1.5.0") == []      # only an earlier month counts


def test_a_rule_is_graded_on_its_own_universes_stocks(monkeypatch):
    """2026-10-28 (research_method §7「重現性」): the list and the gate's backfill had graded the listed-plus-TPEx rules
    on the listed stocks; each worker now loads a universe once, when a rule needs it."""
    from quant_platform.research import daily

    loads = []
    monkeypatch.setattr(daily, "load", lambda base, universe="twse": loads.append(universe) or (universe, f"fp-{universe}"))
    worker = {"base": "history", "panels": {"twse": ("twse", "fp-twse")}}
    assert rolling.panel(worker, "twse") == ("twse", "fp-twse") and loads == []
    assert rolling.panel(worker, "all") == ("all", "fp-all") and loads == ["all"]
    assert rolling.panel(worker, "all") == ("all", "fp-all") and loads == ["all"]
    assert rolling._universe({"spec": {"universe": "all"}}) == "all"
    assert rolling._universe({"spec": '{"kind": "blend", "sleeves": []}'}) == "twse"
    assert rolling._universe({"spec": {"factors": {}}}) == "twse"
