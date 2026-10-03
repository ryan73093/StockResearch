import json
from datetime import date, datetime, time, timedelta

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
    tracker = StockForwardTracker(tmp_path, min_quotes=1)
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
    tracker = StockForwardTracker(tmp_path, min_quotes=1)
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
                              "full_period_excess": 0.2, "plan": {"kind": "ContributionPlan"}})
    tracker = StockForwardTracker(tmp_path)
    added = tracker.sync(date(2026, 10, 7))
    assert [item["rule_hash"] for item in added] == [RULE.rule_hash] and added[0]["since"] == "2026-10-07"
    assert "兩段期間都贏" in added[0]["reason"]
    assert tracker.sync(date(2026, 10, 8)) == [] and len(tracker.tracked()) == 1
