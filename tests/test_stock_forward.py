import json
from datetime import date, datetime, time, timedelta

import pytest

from quant_platform.research.history.dataset import write_parquet
from quant_platform.research.history.official import DailyRow
from quant_platform.research.history.stocks import write_year
from quant_platform.research.registry import TrialRegistry
from quant_platform.research.stock_forward import TAIPEI, StockForwardTracker, reconcile
from quant_platform.research.stock_rules import StockRule

RULE = StockRule(name="測試：6 月動能前 5 名", factor="momentum_6", top=5)


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def build(base, days, missing=()):
    """Index and 0050 on every day; six stocks with different trends, absent on ``missing`` days."""
    history = base / "history"
    write_parquet([DailyRow(day, 100.0, 100.0, 100.0, 100.0, source="t") for day in days], history / "daily" / "TAIEX.parquet")
    write_parquet([DailyRow(day, 50.0 + i * 0.01, 50.0, 50.0, 50.0 + i * 0.01, source="t") for i, day in enumerate(days)],
                  history / "daily" / "0050.parquet")
    by_year = {}
    for i, day in enumerate(days):
        if day in missing:
            continue
        for number in range(6):
            price = round(20 * (1 + 0.0002 * (number - 2)) ** i, 2)
            by_year.setdefault(day.year, []).append({
                "date": day, "code": str(1101 + number), "name": f"公司{number}", "open": price, "high": price,
                "low": price, "close": price, "volume": 1_000_000, "turnover": 60_000_000, "trades": 100})
    for year, rows in by_year.items():
        write_year(rows, history / "stocks" / "twse" / f"{year}.parquet")
    return history


def track(base, rule=RULE, since="2026-10-01"):
    folder = base / "forward" / "stocks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tracked.json").write_text(json.dumps([{
        "rule_hash": rule.rule_hash, "name": rule.name, "rule": rule.model_dump(mode="json"), "since": since,
        "reason": "測試"}], ensure_ascii=False), encoding="utf-8")


def at(day, hour=16):
    return datetime.combine(day, time(hour), tzinfo=TAIPEI)


def test_forward_records_follow_each_other_and_reconcile(tmp_path):
    days = weekdays(date(2024, 1, 1), date(2026, 10, 9))
    build(tmp_path, days, missing={date(2026, 10, 7)})
    track(tmp_path)
    tracker = StockForwardTracker(tmp_path, min_quotes=1, experiments=False)
    assert tracker.record(date(2026, 9, 30), now=at(date(2026, 9, 30))) == []        # before the forward start
    for day in (date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)):
        written = tracker.record(day, now=at(day))
        assert len(written) == 1 and not written[0]["late"]
    assert tracker.record(date(2026, 10, 5), now=at(date(2026, 10, 5))) == []        # once per day
    assert tracker.record(date(2026, 10, 7), now=at(date(2026, 10, 7))) == []        # quotes not on disk yet
    records = tracker.records()
    first, buy_day, after = records[0], records[2], records[3]
    assert first["contributed"] == 0 and first["value"] == 0 and first["holdings"] == []
    # 10-05 is the first contribution day: NT$10,000 into the five strongest of six stocks
    assert buy_day["contributed"] == 10_000 and len(buy_day["trades_today"]) == 5
    assert {trade["side"] for trade in buy_day["trades_today"]} == {"BUY"}
    assert "1101" not in {item["code"] for item in buy_day["holdings"]}            # the weakest trend is left out
    assert buy_day["fees_total"] >= 5 and 9_900 < buy_day["value"] < 10_100 and buy_day["benchmark_value"] > 9_900
    assert after["trades_today"] == [] and after["replay_check"] == {"days": 3, "mismatches": []}
    assert reconcile(records) == {}
    row = tracker.summary()[0]
    assert row["sessions"] == 4 and row["last_trade_day"] == "2026-10-05" and row["problems"] == []
    late = tracker.record(date(2026, 10, 8), now=at(date(2026, 10, 9), 9))
    assert late[0]["late"] and tracker.summary()[0]["late"] == 1


def test_missed_days_are_caught_up_as_late_and_tampering_is_caught(tmp_path):
    days = weekdays(date(2024, 1, 1), date(2026, 10, 9))
    build(tmp_path, days)
    track(tmp_path)
    tracker = StockForwardTracker(tmp_path, min_quotes=1, experiments=False)
    # the computer was off from 10-01 to 10-05: the 10-06 run writes the missed sessions too
    written = tracker.record(date(2026, 10, 6), now=at(date(2026, 10, 6)))
    assert [(record["date"], record["late"]) for record in written] == [
        ("2026-10-01", True), ("2026-10-02", True), ("2026-10-05", True), ("2026-10-06", False)]
    records = tracker.records()
    assert reconcile(records) == {} and len(records[2]["trades_today"]) == 5 and records[3]["trades_total"] == 5
    records[-1]["cash"] += 100
    records[-1]["value"] += 100
    problems = reconcile(records)[RULE.rule_hash]
    assert len(problems) == 1 and "現金" in problems[0]
    records[-1]["holdings"][0]["units"] += 1
    assert any("股數" in problem for problem in reconcile(records)[RULE.rule_hash])


def test_rules_that_win_both_periods_are_tracked_from_the_day_they_qualify(tmp_path):
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    (tmp_path / "reports").mkdir()
    loser = StockRule(name="驗證期輸", factor="momentum_6", top=10)
    good = {"3y": {"count": 10, "win_ratio": 0.8, "median_excess": 0.05}, "5y": {"count": 5, "win_ratio": 0.8,
                                                                               "median_excess": 0.08}}
    bad = {"3y": {"count": 10, "win_ratio": 0.3, "median_excess": -0.05}}
    for number, (rule, period, windows) in enumerate(((RULE, "development", good), (RULE, "validation", good),
                                                      (loser, "development", good), (loser, "validation", bad))):
        report = f"r{number}.json"
        (tmp_path / "reports" / report).write_text(json.dumps({"spec": rule.canonical()}, ensure_ascii=False),
                                                   encoding="utf-8")
        registry.register(kind="candidate", period=period, spec_hash=rule.rule_hash, spec_name=rule.name,
                          input_hash=f"i{number}", data_fingerprint="stocks:x", report_file=report, metrics={
                              "windows": windows, "max_drawdown": -0.2, "benchmark_max_drawdown": -0.25,
                              "full_period_excess": 0.2, "plan": {"kind": "SeedPlan"}, "engine": "stocks-1.2.0"})
    tracker = StockForwardTracker(tmp_path, experiments=False)
    added = tracker.sync(date(2026, 10, 7))
    assert [item["rule_hash"] for item in added] == [RULE.rule_hash] and added[0]["since"] == "2026-10-07"
    assert "兩段期間都贏" in added[0]["reason"] and added[0]["plan"] == "seed" and added[0]["initial"] == 300_000
    assert tracker.sync(date(2026, 10, 8)) == [] and len(tracker.tracked()) == 1


def test_daily_rules_that_pass_the_new_design_are_tracked_and_recorded_every_day(tmp_path):
    from quant_platform.research.daily import DailyRule

    days = weekdays(date(2024, 1, 1), date(2026, 10, 9))
    build(tmp_path, days)
    rule = DailyRule(name="每天 3 個月動能：前 3 名", factors={"momentum_3": 1.0}, top=3)
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "d.json").write_text(json.dumps({"spec": rule.canonical()}, ensure_ascii=False), encoding="utf-8")
    TrialRegistry(tmp_path / "trials.jsonl").register(
        kind="candidate", period="recent", spec_hash=rule.rule_hash, spec_name=rule.name, input_hash="d1",
        data_fingerprint="daily:x", report_file="d.json",
        metrics={"full_period_excess": 0.4, "since_2020_excess": 0.2, "max_drawdown": -0.2, "benchmark_max_drawdown": -0.3,
                 "windows": {"1y": {"count": 50, "win_ratio": 0.7}, "3y": {"count": 30, "win_ratio": 0.8, "median_excess": 0.1}}})
    tracker = StockForwardTracker(tmp_path, min_quotes=1, experiments=False)
    written = tracker.record(date(2026, 10, 2), now=at(date(2026, 10, 2)))
    item = tracker.tracked()[0]
    assert item["kind"] == "daily" and item["plan"] == "seed" and "新設計 T0 候選" in item["reason"]
    # the account starts on 10-02 with NT$300,000 and buys the three strongest trends the same day
    assert len(written) == 1 and written[0]["contributed"] == 300_000 and len(written[0]["holdings"]) == 3
    tracker.record(date(2026, 10, 5), now=at(date(2026, 10, 5)))
    records = tracker.records()
    assert records[-1]["contributed"] == 310_000 and reconcile(records) == {}


def test_the_ai_researcher_account_follows_its_best_rule_from_each_quarter(tmp_path, monkeypatch):
    """2026-10-09: the researcher as a strategy — 0050 until it has a qualifying rule, switching on the first
    session of a quarter by selling at that close; the carried money is not new money."""
    from quant_platform.research import stock_forward
    from quant_platform.research.daily import DailyRule
    from quant_platform.research.stock_forward import META_AI, review_days

    days = weekdays(date(2024, 1, 1), date(2027, 1, 15))
    build(tmp_path, days)
    rule = DailyRule(name="AI：3 個月動能", factors={"momentum_3": 1.0}, top=3)
    choices = iter([None, {"rule_hash": rule.rule_hash, "name": rule.name, "tier": "T1", "spec": rule.canonical(),
                           "trial_id": 7}])
    monkeypatch.setattr(stock_forward, "best_ai_rule", lambda base: next(choices))
    assert review_days(days, date(2026, 10, 1), date(2027, 1, 15)) == [date(2026, 10, 1), date(2027, 1, 1)]
    tracker = StockForwardTracker(tmp_path, min_quotes=1)
    tracker.record(date(2026, 10, 1), now=at(date(2026, 10, 1)))
    assert any(item["rule_hash"] == META_AI["rule_hash"] for item in tracker.tracked())
    tracker.record(date(2027, 1, 8), now=at(date(2027, 1, 8)))
    mine = [record for record in tracker.records() if record["rule_hash"] == META_AI["rule_hash"]]
    by_day = {record["date"]: record for record in mine}
    autumn = by_day["2026-12-31"]
    assert {row["code"] for row in autumn["holdings"]} == {"0050"} and autumn["excess"] == pytest.approx(0.0, abs=1e-4)
    switch = by_day["2027-01-01"]
    sides = {(trade["code"], trade["side"]) for trade in switch["trades_today"]}
    assert ("0050", "SELL") in sides and any(side == "BUY" and code != "0050" for code, side in sides)
    assert "0050" not in {row["code"] for row in switch["holdings"]}
    assert mine[-1]["contributed"] == 300_000 + 10_000 * 4              # Oct, Nov, Dec, Jan: the carry is not money in
    assert reconcile(tracker.records()) == {}
    assert [item["name"] for item in tracker.meta_decisions()] == ["0050", rule.name]


def test_accounts_that_hold_0050_stop_and_new_ones_are_not_tracked(tmp_path):
    """2026-10-09: the owner holds 0050 apart; rules with a 0050 share stop being recorded (records kept)."""
    from quant_platform.research.daily import DailyRule

    days = weekdays(date(2024, 1, 1), date(2026, 10, 9))
    build(tmp_path, days)
    pure = DailyRule(name="每天 3 個月動能：前 3 名", factors={"momentum_3": 1.0}, top=3)
    half = pure.model_copy(update={"name": pure.name + "、一半放 0050", "core": 0.5})
    (tmp_path / "reports").mkdir()
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    for number, rule in enumerate((pure, half)):
        (tmp_path / "reports" / f"d{number}.json").write_text(json.dumps({"spec": rule.canonical()}, ensure_ascii=False),
                                                             encoding="utf-8")
        registry.register(kind="candidate", period="recent", spec_hash=rule.rule_hash, spec_name=rule.name,
                          input_hash=f"d{number}", data_fingerprint="daily:x", report_file=f"d{number}.json",
                          metrics={"full_period_excess": 0.4, "since_2020_excess": 0.2, "max_drawdown": -0.2,
                                   "benchmark_max_drawdown": -0.3, "windows": {"1y": {"count": 50, "win_ratio": 0.7},
                                   "3y": {"count": 30, "win_ratio": 0.8, "median_excess": 0.1}}})
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tracked.json").write_text(json.dumps([{"rule_hash": "old-half", "name": "舊的一半 0050", "kind": "daily",
                                                      "rule": half.model_dump(mode="json"), "since": "2026-10-01"}],
                                                    ensure_ascii=False), encoding="utf-8")
    tracker = StockForwardTracker(tmp_path, min_quotes=1, experiments=False)
    added = tracker.sync(date(2026, 10, 9))
    assert [item["rule_hash"] for item in added] == [pure.rule_hash]            # the half-0050 rule is not added
    old = next(item for item in tracker.tracked() if item["rule_hash"] == "old-half")
    assert old["stopped"] == "2026-10-09" and "0050" in old["stop_reason"]
    written = tracker.record(date(2026, 10, 9), now=at(date(2026, 10, 9)))
    assert {record["rule_hash"] for record in written} == {pure.rule_hash}
