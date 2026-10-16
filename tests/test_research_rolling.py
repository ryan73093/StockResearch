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
    november = datetime(2026, 11, 2, 22, 30, tzinfo=zone)
    assert start_rolling(container, november, launch=started.append) == "rolling"   # a new cutoff: 2026-09-30
    assert started == ["rolling", "rolling-holdout", "rolling"]
