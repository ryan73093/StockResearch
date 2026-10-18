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


def test_the_accounts_follow_the_months_rolling_selection_from_the_day_after_the_cutoff(tmp_path):
    """2026-10-17 (使用者：前向觀察的起點照現在的月份往前推兩個月): the month's passing rules are tracked from the day
    after the cutoff (October → 2026-09-01); nothing is added before the month is graded; the next month moves on."""
    from quant_platform.research.daily import DailyRule

    days = weekdays(date(2024, 1, 1), date(2026, 11, 13))
    build(tmp_path, days)
    rule = DailyRule(name="每天 3 個月動能：前 3 名", factors={"momentum_3": 1.0}, top=3)
    other = DailyRule(name="每天 6 個月動能：前 3 名", factors={"momentum_6": 1.0}, top=3)
    tracker = StockForwardTracker(tmp_path, min_quotes=1, experiments=False)
    assert tracker.sync(date(2026, 10, 2)) == []                                   # the month not graded yet
    folder = tmp_path / "rolling"
    folder.mkdir()
    (folder / "2026-08-31.json").write_text(json.dumps({"cutoff": "2026-08-31", "graded": [
        {"passed": True, "tier": "T0 候選", "spec": rule.canonical()},
        {"passed": False, "tier": "T3", "spec": other.canonical()}]}, ensure_ascii=False), encoding="utf-8")
    written = tracker.record(date(2026, 10, 2), now=at(date(2026, 10, 2)))
    item = tracker.tracked()[0]
    assert len(tracker.tracked()) == 1 and item["since"] == "2026-09-01" and "2026-08-31" in item["reason"]
    assert written[0]["date"] == "2026-09-01" and written[0]["contributed"] == 300_000 and len(written[0]["holdings"]) == 3
    records = tracker.records()
    assert records[-1]["date"] == "2026-10-02" and records[-1]["contributed"] == 310_000 and reconcile(records) == {}
    # November: the cutoff is 2026-09-30 — the account starts again on 2026-10-01, its September records archived
    (folder / "2026-09-30.json").write_text(json.dumps({"cutoff": "2026-09-30", "graded": [
        {"passed": True, "tier": "T0 候選", "spec": rule.canonical()}]}, ensure_ascii=False), encoding="utf-8")
    tracker.record(date(2026, 11, 2), now=at(date(2026, 11, 2)))
    assert tracker.tracked()[0]["since"] == "2026-10-01" and tracker.records()[0]["date"] == "2026-10-01"
    assert (tmp_path / "forward" / "stocks" / "log-archive.jsonl").is_file()


def test_a_blend_in_the_months_selection_is_tracked_as_a_blend(tmp_path):
    """2026-10-18: a blend of daily rules that passes the month is followed like a rule — from the day after the
    cutoff, its account the sum of its sleeves."""
    from quant_platform.research.blend import BlendRule, Sleeve
    from quant_platform.research.daily import DailyRule

    days = weekdays(date(2024, 1, 1), date(2026, 10, 9))
    build(tmp_path, days)
    one = DailyRule(name="每天 3 個月動能：前 3 名", factors={"momentum_3": 1.0}, top=3)
    two = DailyRule(name="每天 6 個月動能：前 3 名", factors={"momentum_6": 1.0}, top=3)
    blend = BlendRule(name="組合：3 個月動能 60%＋6 個月動能 40%", sleeves=(Sleeve(rule=one, share=0.6), Sleeve(rule=two, share=0.4)))
    folder = tmp_path / "rolling"
    folder.mkdir()
    (folder / "2026-08-31.json").write_text(json.dumps({"cutoff": "2026-08-31", "graded": [
        {"passed": True, "tier": "T0 候選（高風險）", "spec": blend.canonical()}]}, ensure_ascii=False), encoding="utf-8")
    tracker = StockForwardTracker(tmp_path, min_quotes=1, experiments=False)
    tracker.record(date(2026, 10, 2), now=at(date(2026, 10, 2)))
    item = tracker.tracked()[0]
    assert item["kind"] == "blend" and item["rule_hash"] == blend.rule_hash and item["since"] == "2026-09-01"
    assert BlendRule.model_validate(item["rule"]).rule_hash == blend.rule_hash
    records = tracker.records()
    assert records[0]["date"] == "2026-09-01" and records[-1]["contributed"] == 310_000 and reconcile(records) == {}
    assert len(records[0]["holdings"]) >= 3                                  # both sleeves' stocks


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
    # 2026-10-15: the account is no longer added by itself (it held only 0050); tracked as it was from 10-01
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tracked.json").write_text(json.dumps([{**META_AI, "rule": {}, "plan": "seed", "initial": 300_000,
                                                      "monthly": 10_000, "since": "2026-10-01"}], ensure_ascii=False),
                                         encoding="utf-8")
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
    # 2026-10-17: accounts come only from the month's rolling selection (none graded here): nothing added
    assert added == [] and tracker.sync(date(2026, 10, 12)) == []
    old = next(item for item in tracker.tracked() if item["rule_hash"] == "old-half")
    assert old["stopped"] == "2026-10-09" and "0050" in old["stop_reason"]
    written = tracker.record(date(2026, 10, 9), now=at(date(2026, 10, 9)))
    assert written == []                                 # the stopped 0050 account is not recorded


def test_fixed_holding_rules_give_way_to_their_trend_versions(tmp_path):
    """2026-10-14 (使用者：前向從 10/1 只有買入，哪來的賣出): a fixed 20-session holding cannot sell for weeks."""
    from quant_platform.research.daily import DailyRule

    fixed = DailyRule(name="每天 3 個月動能：前 3 名", factors={"momentum_3": 1.0}, top=3)
    free = DailyRule(name="每天 6 個月動能：前 3 名", factors={"momentum_6": 1.0}, top=3, min_hold=0)
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    (folder / "tracked.json").write_text(json.dumps([
        {"rule_hash": rule.rule_hash, "name": rule.name, "rule": rule.canonical(), "kind": "daily", "plan": "seed",
         "since": "2026-10-04"} for rule in (fixed, free)], ensure_ascii=False), encoding="utf-8")
    tracker = StockForwardTracker(tmp_path, experiments=False)
    added = tracker.switch_to_trend(date(2026, 10, 14), date(2026, 10, 1))
    assert len(added) == 1 and added[0]["since"] == "2026-10-01" and added[0]["rule"]["hold"] == "trend"
    items = {item["rule_hash"]: item for item in tracker.tracked()}
    assert items[fixed.rule_hash]["stopped"] == "2026-10-14" and not items[free.rule_hash].get("stopped")
    assert tracker.switch_to_trend(date(2026, 10, 15), date(2026, 10, 1)) == []          # done once


def test_blends_with_fixed_holding_sleeves_give_way_too_and_the_ai_account_stops(tmp_path):
    """2026-10-15: the three tracked blends still held each sleeve a fixed 20 sessions; the AI researcher's account
    held nothing but 0050 (paused, no qualifying rule)."""
    from quant_platform.research.blend import BlendRule, Sleeve
    from quant_platform.research.daily import DailyRule
    from quant_platform.research.stock_forward import META_AI, META_STOPPED

    fixed = DailyRule(name="每天 3 個月動能：前 3 名", factors={"momentum_3": 1.0}, top=3)
    trend = DailyRule(name="每天 6 個月動能：前 3 名", factors={"momentum_6": 1.0}, top=3, min_hold=0, hold="trend")
    blend = BlendRule(name="組合：動能各半", sleeves=(Sleeve(rule=fixed, share=0.5), Sleeve(rule=trend, share=0.5)))
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    (folder / "tracked.json").write_text(json.dumps([
        {"rule_hash": blend.rule_hash, "name": blend.name, "rule": blend.canonical(), "kind": "blend", "plan": "seed",
         "since": "2026-10-09"},
        {**META_AI, "rule": {}, "plan": "seed", "since": "2026-10-12"}], ensure_ascii=False), encoding="utf-8")
    tracker = StockForwardTracker(tmp_path, experiments=False)
    added = tracker.switch_to_trend(date(2026, 10, 15), date(2026, 10, 1))
    assert len(added) == 1 and added[0]["kind"] == "blend" and added[0]["since"] == "2026-10-01"
    sleeves = added[0]["rule"]["sleeves"]
    assert [sleeve["rule"]["hold"] for sleeve in sleeves] == ["trend", "trend"] and sleeves[1]["rule"] == trend.canonical()
    assert "1 個子策略" in added[0]["reason"]
    assert tracker.stop(META_AI["rule_hash"], date(2026, 10, 15), META_STOPPED)
    assert not tracker.stop(META_AI["rule_hash"], date(2026, 10, 16), META_STOPPED)      # already stopped
    items = {item["rule_hash"]: item for item in tracker.tracked()}
    assert items[blend.rule_hash]["stopped"] == "2026-10-15" and "0050" in items[META_AI["rule_hash"]]["stop_reason"]
    assert tracker.sync(date(2026, 10, 16)) == []             # and the AI account is not added again


def test_decisions_already_taken_stay_when_the_data_changes_afterwards(tmp_path, monkeypatch):
    """2026-10-15 (108 accounts failed the reconciliation): data that came after a decision — the evening's revenue
    table, a retrained model — changed it when the next day replayed from the start, and a recorded account jumped to
    holdings it never traded into. The decisions taken are kept (research/decisions.py)."""
    from quant_platform.research.daily import DailyRule, FactorPanel

    days = weekdays(date(2024, 1, 1), date(2026, 10, 9))
    build(tmp_path, days)
    rule = DailyRule(name="每天 3 個月動能：前 3 名、月線上續抱", factors={"momentum_3": 1.0}, top=3, min_hold=0, hold="trend")
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    (folder / "tracked.json").write_text(json.dumps([{
        "rule_hash": rule.rule_hash, "name": rule.name, "rule": rule.canonical(), "kind": "daily", "plan": "seed",
        "since": "2026-10-01"}], ensure_ascii=False), encoding="utf-8")
    tracker = StockForwardTracker(tmp_path, min_quotes=1, experiments=False)
    for day in (date(2026, 10, 1), date(2026, 10, 2)):
        tracker.record(day, now=at(day))
    bought = {row["code"] for row in tracker.records()[-1]["holdings"]}
    assert bought == {"1104", "1105", "1106"}                                    # the three strongest trends
    original = FactorPanel.ranked
    monkeypatch.setattr(FactorPanel, "ranked", lambda self, rule, position: list(reversed(original(self, rule, position))))
    assert [item["date"] for item in tracker.freeze(date(2026, 10, 5))] == []     # kept: the replay still holds them
    tracker.record(date(2026, 10, 5), now=at(date(2026, 10, 5)))
    records = tracker.records()
    assert reconcile(records) == {} and {row["code"] for row in records[-1]["holdings"]} >= bought   # still rising: kept
    (folder / "decisions.json").unlink()                                          # without them, the replay rewrites
    changed = tracker.freeze(date(2026, 10, 6))
    assert changed and changed[0]["date"] == "2026-10-05" and "1101" in changed[0]["replayed"]


def test_known_breaks_become_new_bases_with_their_reason(tmp_path):
    """2026-10-15: records that do not follow from the day before for a known reason (an engine or model change, data
    after the decision) are marked, not counted as reconciliation errors."""
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    (folder / "tracked.json").write_text(json.dumps([{"rule_hash": "r", "name": "測試", "kind": "daily", "rule": {},
                                                      "since": "2026-10-01"}], ensure_ascii=False), encoding="utf-8")
    base = {"rule_hash": "r", "name": "測試", "since": "2026-10-01", "contributed": 1_000.0, "benchmark_value": 1_000.0,
            "excess": 0.0, "trades_today": [], "adjustments_today": [], "income_today": 0.0, "trades_total": 1,
            "fees_total": 0, "taxes_total": 0}
    records = [{**base, "date": "2026-10-13", "cash": 0.0, "value": 1_000.0, "holdings": [{"code": "1101", "units": 10.0, "close": 100.0, "value": 1_000.0}]},
               {**base, "date": "2026-10-14", "cash": 0.0, "value": 1_000.0, "holdings": [{"code": "1102", "units": 10.0, "close": 100.0, "value": 1_000.0}]}]
    (folder / "log.jsonl").write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    tracker = StockForwardTracker(tmp_path, experiments=False)
    assert reconcile(tracker.records())["r"]                                     # 1102 appeared without a trade
    assert tracker.rebase("引擎改版後重算") == 1 and tracker.rebase("again") == 0
    assert reconcile(tracker.records(), tracker.rebases()) == {}
    row = tracker.summary()[0]
    assert row["problems"] == [] and row["rebased"] == [("2026-10-14", "引擎改版後重算")]


def test_a_restart_moves_every_live_account_to_the_common_start_and_keeps_the_old_records(tmp_path):
    """2026-10-17 (使用者：前向觀察一律從 10/01 開始，兩個交易日不能當觀察)."""
    import json as _json

    from quant_platform.research.stock_forward import StockForwardTracker

    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    (folder / "tracked.json").write_text(_json.dumps([
        {"rule_hash": "a", "name": "甲", "since": "2026-10-15", "kind": "daily"},
        {"rule_hash": "b", "name": "乙", "since": "2026-10-01", "kind": "daily", "stopped": "2026-10-10"}]), encoding="utf-8")
    (folder / "log.jsonl").write_text("".join(_json.dumps({"date": day, "rule_hash": key}) + "\n"
                                              for day, key in (("2026-10-02", "b"), ("2026-10-15", "a"), ("2026-10-16", "a"))),
                                      encoding="utf-8")
    tracker = StockForwardTracker(tmp_path)
    found = tracker.restart(date(2026, 10, 1), "測試")
    assert found["accounts"] == 1 and found["archived_records"] == 2
    items = {item["rule_hash"]: item for item in tracker.tracked()}
    assert items["a"]["since"] == "2026-10-01" and items["a"]["added"] == "2026-10-15" and items["b"]["since"] == "2026-10-01"
    assert [record["rule_hash"] for record in tracker.records()] == ["b"]            # the stopped account keeps its record
    assert len((folder / found["archive"]).read_text(encoding="utf-8").splitlines()) == 3
