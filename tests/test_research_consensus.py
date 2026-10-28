"""2026-10-11: the T0 consensus list (research/consensus.py)."""

from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import consensus, daily
from quant_platform.research.daily import DailyRule, FactorPanel
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def market(stocks=12, seed=4, first=date(2022, 1, 3), last=date(2023, 12, 29)):
    rng = np.random.default_rng(seed)
    days = weekdays(first, last)
    closes = {}
    for number in range(stocks):
        series = 20 * np.cumprod(1 + 0.0002 * number + rng.normal(0, 0.01, len(days)))
        closes[f"{1101 + number}.TW"] = dict(zip(days, np.maximum(series, 10.5)))
    closes["0050.TW"] = {day: 100.0 for day in days}
    data = LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 50_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})
    return FactorPanel(Panel(data))


def test_a_member_ranks_as_its_own_rule_does():
    fp = market()
    last = len(fp.sessions) - 1
    for rule in (DailyRule(name="a", factors={"momentum_3": 1.0}),
                 DailyRule(name="b", factors={"momentum_3": 1.0, "rsi_14": 1.0, "reversal_5d": 1.0})):
        part = consensus.member_percentiles(fp, rule)[:, last]
        order = [fp.symbols[row] for row in np.argsort(-np.nan_to_num(part, nan=-1), kind="stable")
                 if np.isfinite(part[row])]
        assert order[:5] == fp.ranked(rule, last)[:5]
        assert np.nanmax(part) == pytest.approx(1.0)


def test_one_member_one_vote_and_uncovered_stocks_have_none(monkeypatch):
    fp = market()
    first, second = DailyRule(name="a", factors={"momentum_3": 1.0}), DailyRule(name="b", factors={"reversal_5d": 1.0})
    monkeypatch.setattr(consensus, "members", lambda factor="t0_consensus": [(1, "a", first), (2, "b", second)])
    score = consensus.scores(fp).T                                       # symbols × sessions
    last = len(fp.sessions) - 1
    expected = (consensus.member_percentiles(fp, first)[:, last] + consensus.member_percentiles(fp, second)[:, last]) / 2
    assert score[:, last] == pytest.approx(expected, nan_ok=True)
    assert np.isnan(score[:, 10]).all()                                  # no stock has a year of history yet


def test_the_frozen_members_are_all_stock_and_the_batch_is_two_structures():
    from quant_platform.research.categories import classify, uses_0050

    rules = consensus.members()
    assert len(rules) == 25 and all(rule.core == 0 and not uses_0050(rule.canonical()) for _t, _n, rule in rules)
    assert len(consensus.members_digest()) == 64
    batch = daily.BATCHES["consensus"]()
    assert [rule.top for rule in batch] == [20, 40] and all(rule.factors == {"t0_consensus": 1.0} for rule in batch)
    assert classify(batch[0].canonical())["family"] == "T0 共識清單"


def test_the_size_diagnostic_and_the_small_cap_replay():
    from quant_platform.research import diagnostics
    from quant_platform.research.costs import CostModel

    fp = market()
    caps = np.array([[np.log((row + 1) * 1e9)] * len(fp.sessions) for row in range(len(fp.symbols))], dtype=np.float32)
    fp._cache["market_cap"] = caps                                        # 1101 smallest ... 1112 largest
    data = LegacyData(sessions=fp.sessions, closes={symbol: {day: float(fp.panel.close[fp.row[symbol], index])
                                                             for index, day in enumerate(fp.sessions)}
                                                    for symbol in fp.symbols},
                      traded_value={}, factors={}, predictions={})
    data.closes["0050.TW"] = {day: 100.0 for day in fp.sessions}
    rule = DailyRule(name="x", factors={"reversal_5d": 1.0}, top=3, keep=1, min_hold=0)
    start, end = fp.sessions[260], fp.sessions[-1]
    free = CostModel(fee_rate=0.0, minimum_fee=0, slippage_bps=0.0)
    _run, plain = diagnostics.replay(data, fp, rule, free, start, end)
    _run, large = diagnostics.replay(data, fp, rule, free, start, end, without_small=True)
    smallest = {fp.symbols[row] for row in range(len(fp.symbols)) if diagnostics._cap_percentiles(fp)[row, -1] <= 0.2}
    assert smallest and all(not set(units) & smallest for _cash, units in large.values())
    assert fp.eligible.__self__ is fp                                     # the panel is restored afterwards
    described = diagnostics.describe(data, fp, large)
    assert described["smallest_fifth_share"] == 0 and described["tsmc_share"] == 0
    a = {(2024, 1): {"A", "B"}, (2024, 2): {"A"}}
    b = {(2024, 1): {"B", "C"}, (2024, 2): {"A"}}
    assert diagnostics.overlap(a, b) == pytest.approx((1 / 3 + 1) / 2, abs=1e-3)


def test_the_development_consensus_members_were_chosen_before_2020_10():
    rules = consensus.members("dev_consensus")
    assert len(rules) == 180 and all(name.startswith("掃描：") for _trial, name, _rule in rules)
    assert consensus.members_digest("dev_consensus") != consensus.members_digest()
    batch = daily.BATCHES["devconsensus"]()
    assert [rule.top for rule in batch] == [20, 40] and all(rule.factors == {"dev_consensus": 1.0} for rule in batch)


class SwitchPanel:
    def __init__(self, sessions, script, symbols):
        self.sessions, self.index, self.script = sessions, {day: i for i, day in enumerate(sessions)}, script
        self.row = {symbol: row for row, symbol in enumerate(symbols)}

    def ranked(self, rule, position):
        return list(self.script[self.sessions[position]])


def test_cost_aware_switching_keeps_a_holding_unless_the_swap_pays_a_round_trip(monkeypatch):
    days = [date(2024, 1, 1), date(2024, 1, 2)]
    symbols = ["A", "B", "C", "D", "E"]
    panel = SwitchPanel(days, {days[0]: ["A", "B", "E", "C", "D"], days[1]: ["C", "D", "E", "A", "B"]}, symbols)
    percentiles = np.array([[0.85, 0.85], [0.3, 0.3], [0.95, 0.95], [0.9, 0.9], [0.92, 0.92]])

    def gain(percentile):
        return 0.02 if percentile >= 0.9 else (0.015 if percentile >= 0.8 else 0.0)

    monkeypatch.setattr(daily, "switch_mapping", lambda fp, rule: (percentiles, gain))
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, keep=1, min_hold=0, switching="cost1")
    ranks = daily.daily_rankings(panel, rule, days[0], days[-1])
    assert sorted(ranks[days[0]]) == ["A", "B", "E"]
    # B (expects 0%) is swapped for C (2%): worth more than a round trip; A (1.5%) against D (2%) is not
    assert sorted(ranks[days[1]]) == ["A", "C", "E"]
    plain = daily.daily_rankings(panel, rule.model_copy(update={"switching": "none"}), days[0], days[-1])
    assert sorted(plain[days[1]]) == ["C", "D", "E"]
    assert "switching" not in rule.model_copy(update={"switching": "none"}).canonical()


def test_the_switch_mapping_uses_the_development_period_and_never_expects_less_for_a_better_rank():
    """2026-10-25: this test ran on 2022–2023 only, where the mapping was all zeros and passed — the same blind spot
    that left 698 forward accounts never swapping. Now on years that hold the development period, and not flat."""
    fp = market(stocks=30, first=date(2015, 1, 5), last=date(2021, 3, 31))
    rule = DailyRule(name="x", factors={"momentum_3": 1.0})
    percentiles, gain = daily.switch_mapping(fp, rule)
    values = [gain(p / 10 + 0.05) for p in range(10)]
    assert values == sorted(values) and percentiles.shape == (len(fp.symbols), len(fp.sessions))
    assert values[-1] > values[0]                                   # a mapping, not a flat zero
    with pytest.raises(ValueError):                                 # no development years: an error, never zeros
        daily.switch_mapping(market(stocks=30), rule)


def test_only_the_last_rules_switch_mappings_stay_in_memory(monkeypatch):
    """2026-10-28: every rule's percentiles (15-30 MB) stayed cached; replaying 3,391 forward accounts grew to 35-40 GB
    and the machine ran out of memory seven times."""
    fp = market(stocks=30, first=date(2015, 1, 5), last=date(2021, 3, 31))
    monkeypatch.setattr(daily, "SWITCH_KEEP", 3)
    rules = [DailyRule(name=str(top), factors={"momentum_3": 1.0}, top=top) for top in (5, 6, 7, 8)]
    first = daily.switch_mapping(fp, rules[0])
    for rule in rules[1:3]:
        daily.switch_mapping(fp, rule)
    assert daily.switch_mapping(fp, rules[0]) is first                # used again: the most recent now
    daily.switch_mapping(fp, rules[3])
    kept = [name for name in fp._cache if name.startswith("__switch__")]
    assert kept == [f"__switch__{rules[index].rule_hash}" for index in (2, 0, 3)]   # rules[1] the oldest, let go


def test_the_minimum_holding_comparison_changes_only_the_holding():
    rules = daily.BATCHES["minhold"]()
    assert len(rules) == 4 and all(rule.min_hold == 0 and rule.name.endswith("不設最短持有") for rule in rules)
    original = daily.BATCHES["consensus"]()[0]
    assert rules[0].model_copy(update={"min_hold": 20, "name": original.name}).rule_hash == original.rule_hash
