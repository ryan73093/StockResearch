"""Engine 1.4.0 (2026-10-13; 使用者：所有交易都要照台股的交易規則和機制): corporate actions as a holder lives
them, fees per order part, and the turnover cap."""
import gzip
import json
from datetime import date, timedelta

import pytest

from quant_platform.research import actions, daily
from quant_platform.research.actions import Action, build_actions, health_premium, whole_shares
from quant_platform.research.costs import CostModel
from quant_platform.research.daily import DailyRule, simulate_daily
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


def market(days, closes, turnover=5_000_000_000.0):       # deep enough for the cap
    closes = {**closes, "0050.TW": {day: 100.0 for day in days}}
    return LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: turnover for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={})


def hold(data, days, ledger, budget=100_000):
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, min_hold=0, check="weekly")
    return simulate_daily(data, rule, FREE, days[0], days[-1], {days[0]: ["1101.TW"]}, SeedPlan(budget, 0),
                          ledger=ledger)


def test_a_cash_dividend_is_cash_on_its_payment_date_and_the_shares_stay_whole():
    days = weekdays(date(2024, 7, 1), date(2024, 8, 30))
    prices = {day: (50.0 if n < 10 else 48.0) for n, day in enumerate(days)}       # NT$2 ex-dividend on the 11th
    data = market(days, {"1101.TW": prices})
    data.actions["1101.TW"] = {days[10]: Action(2.0, 0.0, days[25], "finmind")}
    ledger = []
    run = hold(data, days, ledger)
    assert all(float(row["shares"]).is_integer() for row in ledger if row["side"] in ("BUY", "SELL"))
    dividend = next(row for row in ledger if row["side"] == "DIVIDEND")
    assert dividend["cash"] == pytest.approx(2_000 * 2.0) and dividend["premium"] == 0      # 2,000 shares
    paid = next(row for row in ledger if row["side"] == "PAID")
    assert paid["day"] == days[25] and paid["amount"] == pytest.approx(4_000)
    assert run.values[10] == pytest.approx(100_000)          # the dividend is owned from the ex-date
    assert run.values[-1] == pytest.approx(2_000 * 48.0 + 4_000)


def test_the_health_insurance_premium_by_period():
    assert health_premium(19_999, date(2024, 1, 2)) == 0
    assert health_premium(20_000, date(2024, 1, 2)) == 422             # 2.11%
    assert health_premium(20_000, date(2018, 1, 2)) == 382             # 1.91%
    assert health_premium(5_000, date(2015, 1, 2)) == 100              # 2% from NT$5,000


def test_a_stock_dividend_gives_whole_shares_and_the_fraction_is_lost():
    whole, fraction = whole_shares(1_234, 0.05)
    assert whole == 61 and fraction == pytest.approx(0.7)
    days = weekdays(date(2024, 7, 1), date(2024, 8, 30))
    data = market(days, {"1101.TW": {day: (33.0 if n < 10 else 30.0) for n, day in enumerate(days)}})
    data.actions["1101.TW"] = {days[10]: Action(0.0, 0.1, days[20], "finmind")}            # NT$1 stock dividend
    ledger = []
    run = hold(data, days, ledger, budget=100_000)
    bought = next(row["shares"] for row in ledger if row["side"] == "BUY")                  # 3,030 shares
    adjust = next(row for row in ledger if row["side"] == "ADJUST")
    assert adjust["shares"] == bought // 10                                                  # 303 new shares
    assert run.values[-1] == pytest.approx((bought + bought // 10) * 30.0 + 100_000 - bought * 33.0)


def test_a_cash_capital_increase_is_not_subscribed():
    days = weekdays(date(2024, 7, 1), date(2024, 7, 31))
    data = market(days, {"1101.TW": {day: (50.0 if n < 10 else 48.0) for n, day in enumerate(days)}})
    data.actions["1101.TW"] = {days[10]: Action(0.0, 0.0, None, "rights")}
    ledger = []
    run = hold(data, days, ledger)
    assert not [row for row in ledger if row["side"] in ("ADJUST", "DIVIDEND")]
    assert run.values[-1] == pytest.approx(2_000 * 48.0)                                    # the right's value is lost


def test_a_split_outside_the_tables_gives_whole_shares_and_the_fraction_is_lost():
    days = weekdays(date(2024, 7, 1), date(2024, 7, 31))
    data = market(days, {"1101.TW": {day: (50.0 if n < 10 else 71.4) for n, day in enumerate(days)}})
    data.factors["1101.TW"] = {days[10]: 0.7}                                                # a capital reduction
    ledger = []
    run = hold(data, days, ledger, budget=100_050)
    adjust = next(row for row in ledger if row["side"] == "ADJUST")
    assert adjust["factor"] == pytest.approx(1_400 / 2_001)                                  # 2,001 → 1,400 shares
    assert run.values[10] == pytest.approx(1_400 * 71.4)                                     # 0.7 share lost


def test_the_fee_is_charged_per_order_part_with_the_period_minimum():
    days = weekdays(date(2019, 7, 1), date(2019, 7, 5)) + weekdays(date(2021, 7, 1), date(2021, 7, 5))
    data = market(days, {"1101.TW": {day: 10.0 for day in days}})
    costs = CostModel(fee_rate=0.001425, minimum_fee=1, slippage_bps=0.0)
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, min_hold=0, check="weekly", min_trade=0)
    for first in (days[0], days[5]):
        ledger = []
        simulate_daily(data, rule, costs, first, days[-1], {first: ["1101.TW"]}, SeedPlan(12_400, 0), ledger=ledger)
        buy = next(row for row in ledger if row["side"] == "BUY")
        odd = int(buy["shares"]) - 1_000
        odd_fee = int(odd * 10.0 * 0.001425)                                                 # NT$3 for 237 shares
        expected = 20 + (max(20, odd_fee) if first.year == 2019 else max(1, odd_fee))        # 1 lot + the odd lot
        assert 1_000 < buy["shares"] < 1_240 and buy["fee"] == expected


def test_an_order_is_at_most_its_share_of_the_days_turnover_and_the_rest_follows():
    days = weekdays(date(2024, 7, 1), date(2024, 7, 12))
    data = market(days, {"1101.TW": {day: 10.0 for day in days}}, turnover=50_000_000.0)      # 0.1% = NT$50,000
    ledger = []
    hold(data, days, ledger, budget=120_000)
    buys = [(row["day"], row["shares"]) for row in ledger if row["side"] == "BUY"]
    assert buys == [(days[0], 5_000), (days[1], 5_000), (days[2], 2_000)]
    assert any(row["side"] == "BLOCKED" and "成交值" in row["reason"] for row in ledger)


def write_policy(base, code, rows):
    path = base / "raw" / "finmind" / "TaiwanStockDividend" / f"{code}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps({"data": rows}).encode("utf-8")))


def test_build_actions_reads_the_policy_then_the_tables(tmp_path):
    day = date(2024, 7, 10)
    write_policy(tmp_path, "1101", [{"CashEarningsDistribution": 1.5, "CashStatutorySurplus": 0.5,
                                     "CashExDividendTradingDate": "2024-07-10", "CashDividendPaymentDate": "2024-08-09",
                                     "StockEarningsDistribution": 0.5, "StockStatutorySurplus": 0,
                                     "StockExDividendTradingDate": "2024-07-10"}])
    write_policy(tmp_path, "1102", [{"CashEarningsDistribution": 1.0, "CashExDividendTradingDate": "2023-07-10"}])
    events = {
        "1101.TW": {day: {"market": "twse", "kind": "權息", "before": 50.0, "reference": 46.0, "value": 4.0}},
        "1102.TW": {day: {"market": "twse", "kind": "權", "before": 50.0, "reference": 49.0, "value": 1.0}},
        "1103.TW": {day: {"market": "twse", "kind": "息", "before": 50.0, "reference": 48.5, "value": 1.5}},
        "1104.TW": {day: {"market": "twse", "kind": "權息", "before": 50.0, "reference": 47.0, "value": 3.0}},
        "6101.TWO": {day: {"market": "tpex", "kind": "除權息", "before": 30.0, "reference": 27.0, "value": 3.0,
                           "cash": 1.0, "stock": 0.07, "rights": 0.0}},
    }
    built = build_actions(tmp_path, events)
    assert built["1101.TW"][day] == Action(2.0, 0.05, date(2024, 8, 9), "finmind")
    assert built["1102.TW"][day] == Action(0.0, 0.0, None, "rights")       # policies known, none that day
    assert built["1103.TW"][day] == Action(1.5, 0.0, None, "twse-cash")
    assert built["1104.TW"][day] == Action(3.0, 0.0, None, "estimate")     # no policies at all
    assert built["6101.TWO"][day] == Action(1.0, 0.07, None, "tpex")
    assert actions.PAR == 10.0 and daily.ODD_LOT_FEE_FROM == date(2020, 10, 26)


def test_the_trade_audit_flags_each_broken_rule():
    from quant_platform.research.trade_audit import audit

    days = weekdays(date(2024, 7, 1), date(2024, 7, 5))
    data = market(days, {"1101.TW": {days[0]: 10.0, days[1]: 11.0, days[2]: 10.0},
                         "9101.TW": {day: 10.0 for day in days}}, turnover=1_000_000.0)
    costs = CostModel(fee_rate=0.001425, minimum_fee=1, slippage_bps=0.0)
    ledger = [
        {"day": days[1], "symbol": "1101.TW", "side": "BUY", "shares": 100, "price": 11.0, "fee": 1, "tax": 0},    # locked limit-up
        {"day": days[3], "symbol": "1101.TW", "side": "SELL", "shares": 100, "price": 10.0, "fee": 1, "tax": 3},   # no trade that day
        {"day": days[2], "symbol": "1101.TW", "side": "BUY", "shares": 10.5, "price": 10.02, "fee": 0, "tax": 0},  # fraction, tick, fee
        {"day": days[2], "symbol": "9101.TW", "side": "BUY", "shares": 2_000, "price": 10.0, "fee": 28, "tax": 0}, # 2% of turnover, TDR
    ]
    found = audit(data, ledger, costs)
    assert set(found) >= {"limit_up_buy", "traded", "whole", "tick", "fee", "turnover", "instrument"}
    assert "limit_down_sell" not in found and "inside_limits" not in found


def test_an_ex_date_on_a_closed_day_counts_on_the_next_session():
    """2015-07-10, 2016-07-08, 2023-08-03, 2024-07-24/25: typhoons closed the market on scheduled ex-dates."""
    from quant_platform.research.actions import move_to_open_days

    sessions = [date(2024, 7, 23), date(2024, 7, 26), date(2024, 7, 29)]
    events = {"1519.TW": {date(2024, 7, 25): "ex"}, "2330.TW": {date(2024, 7, 23): "same"}}
    moved, count = move_to_open_days(events, sessions)
    assert moved == {"1519.TW": {date(2024, 7, 26): "ex"}, "2330.TW": {date(2024, 7, 23): "same"}} and count == 1


def test_the_exchanges_limits_on_ex_days_and_none_when_it_traded_beyond_them():
    days = weekdays(date(2024, 7, 1), date(2024, 7, 5))
    data = market(days, {"1101.TW": dict(zip(days, [100.0, 100.0, 95.0, 120.0, 120.0]))})
    data.notes["limits"] = {"1101.TW": {days[2]: (85.5, 104.5)}}
    assert daily.price_limits(data, "1101.TW", days[2]) == (85.5, 104.5)        # the table's, not 90～110
    assert daily.price_limits(data, "1101.TW", days[3]) == (None, None)         # +26%: no limit that day
    assert daily.price_limits(data, "1101.TW", days[4]) == (108.0, 132.0)


def test_next_session_execution_fills_the_day_after_the_decision():
    """1.4.0 "next": decided on a close, filled in the next session's closing auction (the platform's numbers
    come after 15:14); a locked limit-up on the filling day refuses the buy and the rule decides again."""
    from quant_platform.research.daily import FactorPanel, daily_rankings
    from quant_platform.research.stock_rules import Panel

    days = weekdays(date(2023, 1, 2), date(2024, 1, 31))
    closes = {f"{1101 + n}.TW": {day: 20.0 * (1 + 0.0005 * (n + 1)) ** i for i, day in enumerate(days)} for n in range(4)}
    data = market(days, closes)
    fp = FactorPanel(Panel(data))
    same = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, keep=1, min_hold=0, check="weekly")
    following = same.model_copy(update={"execution": "next"})
    assert "execution" not in same.canonical() and following.canonical()["execution"] == "next"
    start = days[-30]
    plain, later = daily_rankings(fp, same, start, days[-1]), daily_rankings(fp, following, start, days[-1])
    first = min(plain)
    assert days[days.index(first) + 1] in later and later[days[days.index(first) + 1]] == plain[first]
    assert daily.participation(following) == daily.NEXT_DAY_PARTICIPATION


def test_a_blends_ledgers_add_up_through_a_capital_reduction():
    """Two accounts holding the same stock through a 0.7 capital reduction: each logs its change in shares, so the
    audit of the joint ledger never sees a negative holding."""
    from quant_platform.research.trade_audit import audit

    days = weekdays(date(2024, 7, 1), date(2024, 7, 31))
    data = market(days, {"1101.TW": {day: (50.0 if n < 10 else 71.4) for n, day in enumerate(days)}})
    data.factors["1101.TW"] = {days[10]: 0.7}
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, min_hold=0, check="weekly")
    joint = []
    for budget in (100_050, 70_000):
        ledger = []
        simulate_daily(data, rule, FREE, days[0], days[-1], {days[0]: ["1101.TW"], days[15]: []},
                       SeedPlan(budget, 0), ledger=ledger)
        joint += ledger
    adjust = [row for row in joint if row["side"] == "ADJUST"]
    assert {row["kind"] for row in adjust} == {"split"} and all(row["shares"] < 0 for row in adjust)
    assert "negative" not in audit(data, sorted(joint, key=lambda row: row["day"]), FREE)


def test_an_opening_fill_is_judged_at_the_open_not_the_close():
    """2026-10-21: the 1.5.0 audit flagged 452 next-open buys as bought at a locked limit-up close."""
    from quant_platform.research.trade_audit import audit

    days = weekdays(date(2024, 7, 1), date(2024, 7, 5))
    data = market(days, {"1101.TW": {days[0]: 10.0, days[1]: 11.0, days[2]: 11.0}}, turnover=1_000_000.0)
    import dataclasses
    data = dataclasses.replace(data, opens={"1101.TW": {days[1]: 10.2, days[2]: 12.1}})
    costs = CostModel(fee_rate=0.001425, minimum_fee=1, slippage_bps=0.0)
    bought = {"day": days[1], "symbol": "1101.TW", "side": "BUY", "shares": 100, "price": 10.2, "fee": 1, "tax": 0}
    assert "limit_up_buy" not in audit(data, [bought], costs, execution="open")     # it opened below the limit
    assert "limit_up_buy" in audit(data, [bought], costs)                           # a close fill there is locked
    assert "limit_up_buy" not in audit(data, [bought], costs, execution=None)       # a blend: nearer the open


def test_a_blends_order_is_judged_by_the_sleeve_that_placed_it():
    """2026-10-21: 30,899 turnover flags, nearly all in blends whose next-day sleeves may order more."""
    from quant_platform.research import daily
    from quant_platform.research.trade_audit import audit

    days = weekdays(date(2024, 7, 1), date(2024, 7, 5))
    data = market(days, {"1101.TW": {day: 10.0 for day in days}}, turnover=1_000_000.0)
    costs = CostModel(fee_rate=0.001425, minimum_fee=1, slippage_bps=0.0)
    size = int(1_000_000 * (daily.SAME_DAY_PARTICIPATION + daily.NEXT_DAY_PARTICIPATION) / 2 / 10)   # between the two
    order = {"day": days[1], "symbol": "1101.TW", "side": "BUY", "shares": size, "price": 10.0, "fee": 100, "tax": 0}
    assert "turnover" in audit(data, [order], costs)                                           # a same-day order
    assert "turnover" not in audit(data, [order], costs, each=[(daily.NEXT_DAY_PARTICIPATION, "next")])
