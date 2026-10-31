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

    def risk_on(self, position, kind="taiex_200"):
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
    panel = HeldPanel(DAYS, script, "ABCDEF", rising="A")          # 1.6.0 asks for the held stocks' places too
    base = {"name": "x", "factors": {"trend_200": 1.0}, "top": 3, "keep": 1, "hold": "trend"}
    both = daily_rankings(panel, DailyRule(**base), DAYS[0], DAYS[1])
    entry = daily_rankings(panel, DailyRule(**base, eligibility="entry"), DAYS[0], DAYS[1])
    assert both[DAYS[1]] == ["D", "E", "F"]                 # ineligible: sold although still rising
    assert entry[DAYS[1]] == ["A", "D", "E"]                # still rising: kept


def test_eligibility_band_keeps_a_holding_ranked_down_to_70_percent_of_the_lines():
    """2026-10-27: A eligible; B's turnover 15,000,000 is 75% of the 20,000,000 line, C's 10,000,000 is 50%."""
    from types import SimpleNamespace

    fp = object.__new__(daily.FactorPanel)
    fp.symbols, fp.row = ["A", "B", "C"], {"A": 0, "B": 1, "C": 2}
    fp.panel = SimpleNamespace(close=np.array([[50.0], [50.0], [50.0]]))
    fp.turnover_20 = np.array([[30_000_000.0], [15_000_000.0], [10_000_000.0]])
    fp.age = np.array([[500], [500], [500]])
    scores = np.array([[1.0], [3.0], [2.0]], dtype=np.float32)
    fp.smoothed = lambda name, sessions: scores
    base = {"name": "x", "factors": {"trend_200": 1.0}, "top": 5}
    band, both = DailyRule(**base, eligibility="band"), DailyRule(**base)
    assert fp.ranked(band, 0) == ["A"]                              # a buy needs the full lines
    assert fp.ranked(band, 0, held=["B", "C"]) == ["B", "A"]        # held B within 70%: still ranked, first
    assert fp.ranked(both, 0, held=["B", "C"]) == ["A"]             # the usual rule: B is out
    assert band.canonical()["eligibility"] == "band" and band.rule_hash != both.rule_hash


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


def test_the_deep_risk_overlay_skips_and_sells_the_riskiest():
    """2026-10-16: risk_veto dl1 buys none of the day's riskiest; dl1x also sells a holding once it is among them."""
    script = {DAYS[0]: ["A", "B", "C", "D", "E"]}
    panel = Panel(DAYS, script, "ABCDE")
    flagged = {0: {"A"}, 1: set(), 2: {"B"}}
    panel.risky = lambda position, share: flagged.get(position, set()) if share == daily.RISK_VETO_SHARE else (
        {"B"} if position == 2 else set())
    base = {"name": "x", "factors": {"trend_200": 1.0}, "top": 3, "keep": 2, "check": "weekly", "hold": "trend"}
    plain = daily_rankings(panel, DailyRule(**base), DAYS[0], DAYS[4])
    veto = daily_rankings(panel, DailyRule(**base, risk_veto="dl1"), DAYS[0], DAYS[4])
    sell = daily_rankings(panel, DailyRule(**base, risk_veto="dl1x"), DAYS[0], DAYS[4])
    assert plain[DAYS[0]] == ["A", "B", "C"] and veto[DAYS[0]] == ["B", "C", "D"]   # A is the riskiest that day
    assert DAYS[2] not in veto and sell[DAYS[2]] == ["C", "D"]                       # B turns risky: dl1x sells it
    assert "risk_veto" not in DailyRule(**base).canonical()
    # 2026-10-25 (空頭防護第四版): dl1m is dl1x below TAIEX's 200-session average and nothing above it
    panel.risk_on = lambda position, kind="taiex_200": True
    assert daily_rankings(panel, DailyRule(**base, risk_veto="dl1m"), DAYS[0], DAYS[4]) == plain
    panel.risk_on = lambda position, kind="taiex_200": False
    assert daily_rankings(panel, DailyRule(**base, risk_veto="dl1m"), DAYS[0], DAYS[4]) == sell


def test_the_opening_auction_fills_at_the_open_and_not_at_a_locked_open():
    """2026-10-16 (使用者：盤前或盤中買會不會比較好？): execution "open" decides on the close and fills at the next
    session's open; an open at limit-up is not bought."""
    days = DAYS[:6]
    data = replace(market(days, {"A.TW": {day: 10.0 for day in days}, "B.TW": {day: 10.0 for day in days}}),
                   opens={"A.TW": {day: 9.8 for day in days}, "B.TW": {days[1]: 11.0, **{day: 10.0 for day in days[2:]}}})
    ledger = []
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, execution="open")
    assert daily.lag_of(rule) == 1 and daily.participation(rule) == daily.NEXT_DAY_PARTICIPATION
    simulate_daily(data, rule, FREE, days[0], days[-1], {days[1]: ["A.TW", "B.TW"]}, SeedPlan(100_000, 0), ledger=ledger)
    buys = {(row["day"], row["symbol"]): row["price"] for row in ledger if row["side"] == "BUY"}
    assert buys[(days[1], "A.TW")] == 9.8                                     # the open, not the close
    assert (days[1], "B.TW") not in buys and any(row["side"] == "BLOCKED" and row["symbol"] == "B.TW" for row in ledger)
    assert buys[(days[2], "B.TW")] == 10.0                                    # tried again at the next open
    assert "execution" in rule.canonical()


def test_the_ranking_sees_the_open_locks_for_the_opening_auction():
    from quant_platform.research.daily import FactorPanel
    from quant_platform.research.stock_rules import Panel as PricePanel

    days = weekdays(date(2024, 1, 1), date(2024, 1, 10))
    data = replace(market(days, {"1101.TW": {day: 10.0 for day in days}}),
                   opens={"1101.TW": {**{day: 10.0 for day in days}, days[2]: 11.0}})
    fp = FactorPanel(PricePanel(data))
    row = fp.symbols.index("1101.TW")
    _traded, up_close, _down = fp.market_locks()
    traded, up_open, _down = fp.market_locks("open")
    assert not up_close[row, 2] and up_open[row, 2] and traded[row, 2]       # closed at 10, opened at limit-up 11


def test_the_split_fill_sells_at_the_open_and_buys_at_the_close():
    """2026-10-22 (盤中成交時機研究): execution "split" — sells in the next session's opening auction, buys in its
    closing auction; a sell refused by a limit-down open, a buy by a limit-up close."""
    days = DAYS[:6]
    closes = {"A.TW": {day: 10.0 for day in days}, "B.TW": {day: 10.0 for day in days}}
    data = replace(market(days, closes), opens={"A.TW": {day: 9.8 for day in days}, "B.TW": {day: 10.2 for day in days}})
    ledger = []
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, execution="split")
    assert daily.lag_of(rule) == 1 and daily.participation(rule) == daily.NEXT_DAY_PARTICIPATION
    simulate_daily(data, rule, FREE, days[0], days[-1], {days[1]: ["A.TW"], days[3]: ["B.TW"]}, SeedPlan(100_000, 0),
                   ledger=ledger)
    trades = {(row["day"], row["symbol"], row["side"]): row["price"] for row in ledger if row["side"] in ("BUY", "SELL")}
    assert trades[(days[1], "A.TW", "BUY")] == 10.0                     # bought at the close, not the 9.8 open
    assert trades[(days[3], "A.TW", "SELL")] == 9.8                     # sold at the open
    assert trades[(days[3], "B.TW", "BUY")] == 10.0
    assert rule.canonical()["execution"] == "split"


def test_the_opening_auction_cap_tightens_only_the_open_side(monkeypatch):
    """2026-10-22 (開盤賣做不做得到): OPEN_PARTICIPATION caps a fill in the opening auction — a "split" rule's sells —
    and leaves its buys at the close alone; the engine keeps it at NEXT_DAY_PARTICIPATION."""
    assert daily.OPEN_PARTICIPATION == daily.NEXT_DAY_PARTICIPATION
    days = DAYS[:8]
    closes = {"A.TW": {day: 10.0 for day in days}, "B.TW": {day: 10.0 for day in days}}
    data = replace(market(days, closes), opens={"A.TW": {day: 9.8 for day in days}, "B.TW": {day: 10.0 for day in days}},
                   traded_value={"A.TW": {day: 20_000_000.0 for day in days}, "B.TW": {day: 20_000_000.0 for day in days},
                                 "0050.TW": {day: 5_000_000_000.0 for day in days}})
    rule = DailyRule(name="x", factors={"trend_200": 1.0}, top=3, execution="split")

    def sells(cap):
        monkeypatch.setattr(daily, "OPEN_PARTICIPATION", cap)
        ledger = []
        simulate_daily(data, rule, FREE, days[0], days[-1], {days[1]: ["A.TW"], days[3]: ["B.TW"]},
                       SeedPlan(100_000, 0), ledger=ledger)
        return ledger

    plain, tight = sells(daily.NEXT_DAY_PARTICIPATION), sells(0.002)
    sold = lambda ledger, day: sum(row["shares"] for row in ledger if row["side"] == "SELL" and row["day"] == day)
    bought = lambda ledger: sum(row["shares"] for row in ledger if row["side"] == "BUY" and row["symbol"] == "A.TW")
    assert bought(plain) == bought(tight) == 10_000                          # the close side keeps 0.5%: 100,000
    assert sold(plain, days[3]) == 10_000                                    # 0.5% of 20,000,000 at 9.8: all of it
    assert sold(tight, days[3]) == 4_081 and sold(tight, days[4]) == 4_081   # 0.2%: 40,000 at 9.8, the rest later
    assert any(row["side"] == "BLOCKED" and row["order"] == "SELL" and "0.2%" in row["reason"] for row in tight)


class HeldPanel(Panel):
    """Panel whose ranking also places the held stocks the lines leave out (``shadow``: symbol -> place), as
    FactorPanel.ranked does for eligibility "band" and (1.6.0) "entry"."""

    def __init__(self, *args, shadow=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.shadow = shadow or {}

    def ranked(self, rule, position, held=None):
        names = super().ranked(rule, position)
        for symbol, place in sorted(self.shadow.items(), key=lambda item: item[1]):
            if held and symbol in held and symbol not in names:
                names.insert(place, symbol)
        return names


def test_eligibility_entry_keeps_a_holding_in_the_keep_zone_from_1_6_0(monkeypatch):
    """2026-10-31 (使用者：你帳面數字都一樣是怎樣): "entry" is "a holding no longer eligible is not sold" — but without a
    minimum hold the ranking left A out, so A fell out of the keep zone and was sold anyway (#7343 = #7024 to the
    yuan). From 1.6.0 A stays ranked (second, within the keep zone of 3) and is kept; the usual rule sells it; B and C
    leave by the keep zone either way."""
    script = {DAYS[0]: ["A", "B", "C"], DAYS[1]: ["D", "E", "F", "B", "C"]}     # A no longer eligible
    panel = HeldPanel(DAYS, script, "ABCDEF", shadow={"A": 1})
    base = {"name": "x", "factors": {"trend_200": 1.0}, "top": 3, "keep": 1, "min_hold": 0, "check": "daily"}
    both = daily_rankings(panel, DailyRule(**base), DAYS[0], DAYS[1])
    monkeypatch.setattr(daily, "ENTRY_RANKS_HELD", False)
    before = daily_rankings(panel, DailyRule(**base, eligibility="entry"), DAYS[0], DAYS[1])
    monkeypatch.setattr(daily, "ENTRY_RANKS_HELD", True)
    after = daily_rankings(panel, DailyRule(**base, eligibility="entry"), DAYS[0], DAYS[1])
    assert both[DAYS[1]] == before[DAYS[1]] == ["D", "E", "F"]      # sold although second in the ranking
    assert after[DAYS[1]] == ["A", "D", "E"]                        # 1.6.0: kept, as the setting says


def test_eligibility_entry_ranks_a_holding_whatever_its_lines_from_1_6_0(monkeypatch):
    """The FactorPanel side: with "entry" a held stock below the price and turnover lines stays ranked by its score
    (C at 50% of the turnover line too, unlike "band"); a stock not held still needs the lines."""
    from types import SimpleNamespace

    fp = object.__new__(daily.FactorPanel)
    fp.symbols, fp.row = ["A", "B", "C"], {"A": 0, "B": 1, "C": 2}
    fp.panel = SimpleNamespace(close=np.array([[50.0], [50.0], [50.0]]))
    fp.turnover_20 = np.array([[30_000_000.0], [15_000_000.0], [10_000_000.0]])
    fp.age = np.array([[500], [500], [500]])
    scores = np.array([[1.0], [3.0], [2.0]], dtype=np.float32)
    fp.smoothed = lambda name, sessions: scores
    entry = DailyRule(name="x", factors={"trend_200": 1.0}, top=5, eligibility="entry")
    monkeypatch.setattr(daily, "ENTRY_RANKS_HELD", True)
    assert fp.ranked(entry, 0) == ["A"]
    assert fp.ranked(entry, 0, held=["B", "C"]) == ["B", "C", "A"]
    assert fp.ranked(entry, 0, held=["C"]) == ["C", "A"]
    monkeypatch.setattr(daily, "ENTRY_RANKS_HELD", False)
    assert fp.ranked(entry, 0, held=["B", "C"]) == ["A"]            # before 1.6.0: as it was
