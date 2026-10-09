"""2026-10-09: one account split across strategy families (research/blend.py)."""

import json
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from quant_platform.research import blend, daily
from quant_platform.research.costs import CostModel
from quant_platform.research.daily import DailyRule, FactorPanel, daily_rankings, simulate_daily
from quant_platform.research.legacy_challenger import LegacyData, RunResult
from quant_platform.research.registry import TrialRegistry
from quant_platform.research.stock_rules import Panel, SeedPlan

FREE = CostModel(fee_rate=0.0, minimum_fee=0, slippage_bps=0.0)
UP = DailyRule(name="每天 3 個月動能：前 3 名", factors={"momentum_3": 1.0}, top=3)
CALM = DailyRule(name="每天 60 日低波動：前 3 名", factors={"low_volatility_60": 1.0}, top=3)


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def market():
    days = weekdays(date(2023, 1, 2), date(2024, 12, 31))
    closes = {f"{1101 + i}.TW": {day: 20 * (1 + (i - 2) * 0.0006) ** n * (1 + 0.01 * ((n + i) % 3 - 1))
                                 for n, day in enumerate(days)} for i in range(6)}
    closes["0050.TW"] = {day: 100.0 * 1.0002 ** n for n, day in enumerate(days)}
    data = LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 50_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})
    return data, FactorPanel(Panel(data))


def test_shares_must_cover_the_account_and_sleeves_keep_no_0050():
    blend.BlendRule(name="x", sleeves=(blend.Sleeve(rule=UP, share=0.25), blend.Sleeve(rule=CALM, share=0.25)), core=0.5)
    with pytest.raises(ValidationError):
        blend.BlendRule(name="x", sleeves=(blend.Sleeve(rule=UP, share=0.5), blend.Sleeve(rule=CALM, share=0.25)))
    with pytest.raises(ValidationError):
        blend.BlendRule(name="x", sleeves=(blend.Sleeve(rule=UP.model_copy(update={"core": 0.5}), share=0.5),
                                           blend.Sleeve(rule=CALM, share=0.5)))
    one = blend.BlendRule(name="甲", sleeves=(blend.Sleeve(rule=UP, share=0.5), blend.Sleeve(rule=CALM, share=0.5)))
    renamed = blend.BlendRule(name="乙", sleeves=(blend.Sleeve(rule=UP.model_copy(update={"name": "別名"}), share=0.5),
                                                blend.Sleeve(rule=CALM, share=0.5)))
    assert one.rule_hash == renamed.rule_hash and one.rule_hash != UP.rule_hash
    assert isinstance(blend.parse_spec(one.canonical()), blend.BlendRule)
    assert isinstance(blend.parse_spec(UP.canonical()), DailyRule)
    assert blend.BlendRule.model_validate(one.model_dump(mode="json")) == one


def test_the_account_is_the_sum_of_its_sleeves_by_hand():
    days = [date(2024, 1, 1), date(2024, 1, 2)]
    first = RunResult("a", days, [100.0, 110.0], [100.0, 0.0], [(days[0], 100.0)])
    second = RunResult("b", days, [50.0, 45.0], [50.0, 0.0], [(days[0], 40.0), (days[1], 10.0)])
    first.trades, second.trades, first.fees, second.taxes = 2, 3, 5, 7
    combined = blend.combine("ab", [first, second])
    assert combined.values == [150.0, 155.0] and combined.flows == [150.0, 0.0]
    assert combined.contributions == [(days[0], 140.0), (days[1], 10.0)] and combined.contributed == 150.0
    assert (combined.trades, combined.fees, combined.taxes) == (5, 5, 7)


def test_a_blend_equals_its_sleeves_run_with_their_share_of_the_money():
    data, fp = market()
    start, end = date(2024, 1, 2), date(2024, 12, 31)
    mix = blend.BlendRule(name="動能＋低波動＋0050", sleeves=(blend.Sleeve(rule=UP, share=0.25), blend.Sleeve(rule=CALM, share=0.25)),
                          core=0.5)
    account = blend.BlendAccount(data, fp, mix, FREE, start, end)
    ledger, snapshots = [], {}
    run = account.run(start, end, SeedPlan(), ledger=ledger, snapshots=snapshots)
    parts = [simulate_daily(data, rule, FREE, start, end, daily_rankings(fp, rule, start, end), SeedPlan(75_000, 2_500))
             for rule in (UP, CALM)]
    parts.append(simulate_daily(data, None, FREE, start, end, plan=SeedPlan(150_000, 5_000)))
    assert run.values == pytest.approx([sum(values) for values in zip(*(part.values for part in parts))])
    assert run.contributed == pytest.approx(300_000 + 10_000 * 12)
    last = run.days[-1]
    cash, units = snapshots[last]
    assert cash + sum(count * data.closes[symbol][last] for symbol, count in units.items()) == pytest.approx(run.values[-1])
    assert ledger == sorted(ledger, key=lambda entry: entry["day"]) and any(e["symbol"] == "0050.TW" for e in ledger)


def test_a_blend_is_one_trial_judged_like_a_rule(tmp_path, monkeypatch):
    monkeypatch.setattr(daily, "RECENT_START", date(2024, 1, 1))
    monkeypatch.setattr(daily, "REGIME_START", date(2024, 7, 1))
    monkeypatch.setattr(daily, "RECENT_END", date(2024, 12, 31))
    monkeypatch.setattr(daily, "WINDOWS", {"1y": 6})
    data, fp = market()
    mix = blend.BlendRule(name="動能＋低波動", sleeves=(blend.Sleeve(rule=UP, share=0.5), blend.Sleeve(rule=CALM, share=0.5)))
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    record, report = blend.run_blend_trial(mix, tmp_path, registry, tmp_path / "reports", FREE, data, fp, "daily:test")
    assert record.spec_hash == mix.rule_hash and record.period == "recent" and report["spec"]["kind"] == "blend"
    assert report["windows"]["1y"]["count"] > 0 and report["curve"] and "reasons" in record.metrics
    assert record.metrics["engine"].endswith(blend.BLEND_VERSION)
    again, _ = blend.run_blend_trial(mix, tmp_path, registry, tmp_path / "reports", FREE, data, fp, "daily:test")
    assert again.trial_id == record.trial_id and len(registry.records()) == 1


def test_a_qualifying_blend_is_tracked_forward_and_reconciles(tmp_path):
    from test_stock_forward import at, build
    from test_stock_forward import weekdays as forward_days

    from quant_platform.research.stock_forward import StockForwardTracker, reconcile

    build(tmp_path, forward_days(date(2024, 1, 1), date(2026, 10, 9)))
    mix = blend.BlendRule(name="組合：動能前 3＋前 4", sleeves=(blend.Sleeve(rule=UP, share=0.5),
                                                          blend.Sleeve(rule=UP.model_copy(update={"top": 4}), share=0.5)))
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "b.json").write_text(json.dumps({"spec": mix.canonical()}, ensure_ascii=False), encoding="utf-8")
    TrialRegistry(tmp_path / "trials.jsonl").register(
        kind="candidate", period="recent", spec_hash=mix.rule_hash, spec_name=mix.name, input_hash="b1",
        data_fingerprint="daily:x", report_file="b.json",
        metrics={"full_period_excess": 0.4, "since_2020_excess": 0.2, "max_drawdown": -0.2, "benchmark_max_drawdown": -0.3,
                 "windows": {"1y": {"count": 50, "win_ratio": 0.7}, "3y": {"count": 30, "win_ratio": 0.8, "median_excess": 0.1}}})
    tracker = StockForwardTracker(tmp_path, min_quotes=1, experiments=False)
    written = tracker.record(date(2026, 10, 2), now=at(date(2026, 10, 2)))
    assert tracker.tracked()[0]["kind"] == "blend"
    assert len(written) == 1 and written[0]["contributed"] == 300_000
    assert written[0]["holdings"] and not any(row["code"] == "0050" for row in written[0]["holdings"])
    tracker.record(date(2026, 10, 5), now=at(date(2026, 10, 5)))
    assert tracker.records()[-1]["contributed"] == 310_000 and reconcile(tracker.records()) == {}
