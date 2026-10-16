"""Engine 1.5.0 (2026-10-16, the mechanism audit): every session's standing exits, refused orders tried again alone,
idle cash reinvested on decision days, stock dividends credited on distribution, the cooldown and eligibility as rule
fields, kept decisions replayed exactly."""

from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import daily
from quant_platform.research.actions import Action
from quant_platform.research.costs import CostModel
from quant_platform.research.daily import DailyRule, daily_rankings, simulate_daily
from quant_platform.research.decisions import DecisionStore, rankings as kept_rankings
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import SeedPlan

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
                      traded_value={symbol: {day: 5_000_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})


class Panel:
    """A scripted ranking with prices, a market switch, the market's locks and the trend."""

    def __init__(self, sessions, script, symbols, prices=None, on=None, untraded=(), rising=()):
        self.sessions, self.index, self.script = sessions, {day: i for i, day in enumerate(sessions)}, script
        self.symbols, self.row = list(symbols), {symbol: n for n, symbol in enumerate(symbols)}
        self.prices = prices or {}
        self.on = on
        shape = (len(self.symbols), len(sessions))
        self.traded = np.ones(shape, dtype=bool)
        for symbol, position in untraded:
            self.traded[self.row[symbol], position] = False
        self.up = np.zeros(shape, dtype=bool)
        self._rising = np.zeros(shape, dtype=bool)
        for symbol in rising:
            self._rising[self.row[symbol], :] = True

    def ranked(self, rule, position):
        day = self.sessions[position]
        known = [when for when in self.script if when <= day]
        return list(self.script[max(known)]) if known else []

    def price(self, symbol, position):
        return self.prices.get(symbol, [100.0] * len(self.sessions))[position]

    def risk_on(self, position):
        return True if self.on is None else self.on[position]

    def market_locks(self):
        return self.traded, self.up, self.up

    def gone(self):
        return {}

    def rising(self):
        return self._rising


DAYS = weekdays(date(2024, 1, 1), date(2024, 2, 23))     # Mondays: 0, 5, 10, 15, 20, 25, 30, 35


def test_a_weekly_rule_stops_out_the_session_the_stop_is_hit():
    prices = {"A": [100.0, 100.0, 80.0] + [80.0] * (len(DAYS) - 3)}
    panel = Panel(DAYS, {DAYS[0]: ["A", "B", "C", "D"]}, "ABCD", prices=prices)
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, keep=1, stop_loss=0.15, check="weekly")
    ranks = daily_rankings(panel, rule, DAYS[0], DAYS[-1])
    assert ranks[DAYS[2]] == ["B", "C"]                    # sold the Wednesday it fell 20% (it waited for Monday)
    assert ranks[DAYS[5]] == ["B", "C", "D"]               # the place is filled at the next decision


def test_the_market_filter_sells_the_session_the_market_turns():
    on = [True, True, True, False] + [True] * (len(DAYS) - 4)
    panel = Panel(DAYS, {DAYS[0]: ["A", "B", "C"]}, "ABC", on=on)
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, check="weekly", market_filter="taiex_200")
    ranks = daily_rankings(panel, rule, DAYS[0], DAYS[-1])
    assert ranks[DAYS[3]] == [] and DAYS[4] not in ranks   # out on the Thursday; back only at the next decision
    assert ranks[DAYS[5]] == ["A", "B", "C"]


def test_a_refused_buy_fills_only_its_place_the_next_session():
    """1.5.0: the Monday pick X closes with no trade; Tuesday fills X's place with the best the rule would buy —
    B and C stay although a full decision would have sold them."""
    script = {DAYS[0]: ["X", "B", "C"], DAYS[1]: ["X", "D", "E", "B", "C"]}
    panel = Panel(DAYS, script, "XBCDE", untraded=[("X", 0)])
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, keep=1, min_hold=0, check="weekly")
    ranks = daily_rankings(panel, rule, DAYS[0], DAYS[-1])
    assert ranks[DAYS[0]] == ["B", "C"]
    assert ranks[DAYS[1]] == ["B", "C", "X"]
    assert DAYS[2] not in ranks and ranks[DAYS[5]] == ["X", "D", "E"]


def test_the_cooldown_is_the_rules_choice():
    prices = {"A": [100.0, 100.0, 100.0, 120.0, 110.0, 101.0] + [101.0] * (len(DAYS) - 6)}
    panel = Panel(DAYS, {DAYS[0]: ["A", "B", "C", "D", "E"]}, "ABCDE", prices=prices)
    base = {"name": "x", "factors": {"trend_200": 1.0}, "top": 3, "keep": 1, "min_hold": 0, "stop_loss": 0.15}
    waiting = daily_rankings(panel, DailyRule(**base), DAYS[0], DAYS[-1])
    at_once = daily_rankings(panel, DailyRule(**base, cooldown=1), DAYS[0], DAYS[-1])
    assert waiting[DAYS[5]] == at_once[DAYS[5]] == ["B", "C", "D"]          # stopped out
    assert "A" not in waiting[DAYS[6]] and at_once[DAYS[6]] == ["B", "C", "A"]   # bought back at once: its choice
    assert "cooldown" not in DailyRule(**base).canonical() and DailyRule(**base, cooldown=1).canonical()["cooldown"] == 1


def test_eligibility_entry_leaves_the_holding_to_the_hold_rule():
    script = {DAYS[0]: ["A", "B", "C"], DAYS[1]: ["D", "E", "F", "B", "C"]}     # A no longer eligible (not ranked)
    panel = Panel(DAYS, script, "ABCDEF", rising="A")
    base = {"name": "x", "factors": {"trend_200": 1.0}, "top": 3, "keep": 1, "hold": "trend"}
    both = daily_rankings(panel, DailyRule(**base), DAYS[0], DAYS[1])
    entry = daily_rankings(panel, DailyRule(**base, eligibility="entry"), DAYS[0], DAYS[1])
    assert both[DAYS[1]] == ["D", "E", "F"]                 # ineligible: sold although still rising
    assert entry[DAYS[1]] == ["A", "D", "E"]                # still rising: kept


def test_idle_cash_tops_up_the_picks_on_the_next_decision():
    days = DAYS[:10]
    data = market(days, {f"{name}.TW": {day: 10.0 for day in days} for name in "ABC"})
    ranks = {days[0]: ["A.TW", "B.TW", "C.TW"], days[2]: ["A.TW", "B.TW"], days[5]: ["A.TW", "B.TW"]}
    base = {"name": "x", "factors": {"trend_200": 1.0}, "top": 3, "check": "weekly", "hold": "trend"}
    for reinvest, bought in (("decision", True), ("contribution", False)):
        ledger = []
        rule = DailyRule(**base, reinvest=reinvest)
        simulate_daily(data, rule, FREE, days[0], days[-1], ranks, SeedPlan(99_990, 0), ledger=ledger)
        buys = [row for row in ledger if row["side"] == "BUY" and row["day"] == days[5]]
        assert bool(buys) == bought
        if bought:                                          # C's 33,330 less the 0.3% tax, split between A and B
            assert sorted(row["shares"] for row in buys) == [1661, 1661]
    assert "reinvest" not in DailyRule(**base).canonical()


def test_a_stock_dividend_is_sellable_only_from_its_distribution():
    days = weekdays(date(2024, 6, 3), date(2024, 8, 9))
    data = replace(market(days, {"A.TW": {day: 10.0 for day in days}}),
                   actions={"A.TW": {days[2]: Action(0.0, 0.1, None, "finmind")}})   # 1 new share per 10, PAY_LAG later
    ranks = {days[0]: ["A.TW"], days[5]: [], days[25]: []}
    ledger, snapshots = [], {}
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, check="weekly")
    run = simulate_daily(data, rule, FREE, days[0], days[-1], ranks, SeedPlan(10_000, 0), ledger=ledger,
                         snapshots=snapshots)
    assert snapshots[days[2]][1]["A.TW"] == 1100                          # owned from the ex-date
    sells = [(row["day"], row["shares"]) for row in ledger if row["side"] == "SELL"]
    assert sells[0] == (days[5], 1000)                                     # only the credited shares can be sold
    adjust = [(row["day"], row["shares"], row["factor"], row["credited"]) for row in ledger if row["side"] == "ADJUST"]
    assert adjust == [(days[2], 100, pytest.approx(1.1), days[2 + daily.PAY_LAG])]     # owned (the records) from the ex-date
    credit = [row for row in ledger if row["side"] == "CREDIT"]
    assert [(row["day"], row["shares"], row["declared"]) for row in credit] == [(days[2 + daily.PAY_LAG], 100, days[2])]
    assert sells[1] == (days[25], 100)
    assert run.values[3] == pytest.approx(11_000)


def test_kept_decisions_replay_the_next_day_execution_and_the_pending_orders(tmp_path):
    """A rule filled at the next close keeps the decision taken on ``through`` (it was lost before 1.5.0); a refused
    buy pending after ``through`` is filled as the full replay fills it."""
    script = {DAYS[0]: ["X", "B", "C"], DAYS[1]: ["X", "D", "E", "B", "C"], DAYS[5]: ["D", "E", "F"]}
    panel = Panel(DAYS, script, "XBCDEF", untraded=[("X", 1)])
    for rule in (DailyRule(name="x", factors={"trend_200": 1.0}, top=3, keep=1, min_hold=0, check="weekly",
                           execution="next"),
                 DailyRule(name="x", factors={"trend_200": 1.0}, top=3, keep=1, min_hold=0, check="weekly")):
        full = daily_rankings(panel, rule, DAYS[0], DAYS[-1])
        for through in (DAYS[0], DAYS[1], DAYS[4]):
            store = DecisionStore(tmp_path / rule.execution / through.isoformat())
            kept_rankings(panel, rule, DAYS[0], through, store, "k", through)
            store.save()
            replay = kept_rankings(panel, rule, DAYS[0], DAYS[-1], DecisionStore(tmp_path / rule.execution / through.isoformat()), "k")
            assert replay == full, (rule.execution, through)


def test_a_session_without_a_trade_counts_as_nothing_traded():
    from quant_platform.research.daily import FactorPanel
    from quant_platform.research.stock_rules import Panel as PricePanel

    days = weekdays(date(2023, 1, 2), date(2024, 3, 29))
    rare = {day: 20.0 for n, day in enumerate(days) if n % 20 == 0}           # trades one session in twenty
    data = market(days, {"1101.TW": rare, "1102.TW": {day: 20.0 for day in days}})
    data = replace(data, traded_value={**data.traded_value, "1101.TW": {day: 30_000_000.0 for day in rare}})
    fp = FactorPanel(PricePanel(data))
    row = fp.symbols.index("1101.TW")
    last = max(fp.index[day] for day in rare)
    assert fp.turnover_20[row, last] == pytest.approx(1_500_000.0)            # 30 million over 20 sessions
    rule = DailyRule(name="x", factors={"trend_200": 1.0})
    assert "1101.TW" not in fp.ranked(rule, last)


def test_a_holding_the_market_would_not_let_the_rule_sell_is_not_topped_up():
    """The rule drops B on the decision day but B closes locked limit-down: B stays in the list (refused) and the
    idle cash goes to A only."""
    days = DAYS[:10]
    b = [20.0] * 5 + [18.0] + [18.0] * 4                                           # −10% locked on the Monday
    data = market(days, {"A.TW": {day: 10.0 for day in days}, "B.TW": dict(zip(days, b)),
                         "C.TW": {day: 10.0 for day in days}})
    ranks = {days[0]: ["A.TW", "B.TW", "C.TW"], days[2]: ["A.TW", "B.TW"], days[5]: ["A.TW", "B.TW"]}
    ledger = []
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, check="weekly", hold="trend")
    simulate_daily(data, rule, FREE, days[0], days[-1], ranks, SeedPlan(99_990, 0), ledger=ledger)
    monday = [(row["symbol"], row["side"]) for row in ledger if row["day"] == days[5] and row["side"] == "BUY"]
    assert monday == [("A.TW", "BUY")]


def test_every_day_follows_from_the_day_before_through_stock_dividends_and_reductions():
    """What the forward record checks (stock_forward._continuity): yesterday's holdings × today's adjustments plus
    today's trades are today's holdings; yesterday's cash plus new money, income, money back and trades is today's."""
    days = weekdays(date(2024, 6, 3), date(2024, 9, 27))
    a = {day: 10.0 for day in days}
    b = {day: 20.0 for n, day in enumerate(days) if not 30 <= n < 38}               # suspended, then a reduction
    b.update({day: 25.0 for n, day in enumerate(days) if n >= 38})
    data = replace(market(days, {"A.TW": a, "B.TW": b}),
                   actions={"A.TW": {days[3]: Action(0.5, 0.1, None, "finmind")}},
                   notes={"reductions": {"B.TW": {days[38]: (0.75, 1.25)}}})
    ranks = {days[0]: ["A.TW", "B.TW"], days[45]: ["A.TW"]}
    ledger, snapshots = [], {}
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, check="weekly", hold="trend")
    run = simulate_daily(data, rule, FREE, days[0], days[-1], ranks, SeedPlan(100_000, 10_000), ledger=ledger,
                         snapshots=snapshots)
    sides = {row["side"] for row in ledger}
    assert {"ADJUST", "CREDIT", "REFUND", "DIVIDEND"} <= sides
    flows = dict(zip(run.days, run.flows))
    for before, day in zip(days, days[1:]):
        cash, units = snapshots[before][0], dict(snapshots[before][1])
        for row in ledger:
            if row["day"] != day:
                continue
            if row["side"] == "ADJUST":
                units[row["symbol"]] = units.get(row["symbol"], 0.0) * row["factor"]
            elif row["side"] == "DIVIDEND":
                cash += row["net"]
            elif row["side"] == "REFUND":
                cash += row["cash"]
            elif row["side"] == "BUY":
                units[row["symbol"]] = units.get(row["symbol"], 0.0) + row["shares"]
                cash -= row["shares"] * row["price"] + row["fee"]
            elif row["side"] == "SELL":
                units[row["symbol"]] = units.get(row["symbol"], 0.0) - row["shares"]
                cash += row["shares"] * row["price"] - row["fee"] - row["tax"]
        cash += flows[day]
        now = snapshots[day][1]
        for symbol in set(units) | set(now):
            assert units.get(symbol, 0.0) == pytest.approx(now.get(symbol, 0.0), abs=1e-6), (day, symbol)
        assert cash == pytest.approx(snapshots[day][0], abs=1e-6), day
