"""2026-10-10: trend-quality factors, the revenue calendar and the lottery gate (Taiwan-quant review)."""

from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import daily
from quant_platform.research.daily import DailyRule, FactorPanel, check_days, daily_rankings
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def market(days, closes, opens=None):
    closes = {**closes, "0050.TW": {day: 100.0 for day in days}}
    return LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 50_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors={}, predictions={}, opens=opens or {})


def test_continuity_and_intraday_momentum_by_hand():
    days = weekdays(date(2023, 1, 2), date(2024, 6, 28))
    steady = [20 * 1.001 ** n for n in range(len(days))]                       # up every day
    choppy = [20 * (1.03 if n % 2 else 1.0) * 1.0005 ** n for n in range(len(days))]   # up, down, up...
    opens = {"1101.TW": {day: price / 1.01 for day, price in zip(days, steady)}}      # +1% open to close
    fp = FactorPanel(Panel(market(days, {"1101.TW": dict(zip(days, steady)), "1102.TW": dict(zip(days, choppy))}, opens)))
    last, first, second = len(days) - 1, fp.symbols.index("1101.TW"), fp.symbols.index("1102.TW")
    fip = fp.matrix("fip_12")
    assert fip[first, last] == pytest.approx(1.0)                  # every day up, rising: fully continuous
    assert abs(fip[second, last]) < 0.02                           # as many up days as down days
    assert np.isnan(fip[first, 200])                               # not a year of history yet
    imom = fp.matrix("imom_12")
    assert imom[first, last] == pytest.approx(231 * np.log(1.01), rel=1e-4)
    assert np.isnan(imom[second, last])                            # no opening prices: unknown
    # only sessions up to 21 before: changing the last 21 sessions changes nothing
    later = {**{day: price for day, price in zip(days, steady)}, **{day: 5.0 for day in days[-21:]}}
    changed = FactorPanel(Panel(market(days, {"1101.TW": later, "1102.TW": dict(zip(days, choppy))}, opens)))
    assert changed.matrix("fip_12")[first, last] == pytest.approx(fip[first, last])


def test_residual_momentum_takes_out_the_industry():
    rng = np.random.default_rng(3)
    days = weekdays(date(2023, 1, 2), date(2024, 6, 28))
    common = rng.normal(0.002, 0.01, len(days))                     # the industry rallies together
    closes, industries = {}, {}
    for number in range(6):
        extra = 0.0015 if number == 0 else (-0.0015 if number == 1 else 0.0)
        noise = rng.normal(0, 0.004, len(days))
        series = 20 * np.cumprod(1 + common + extra + noise)
        code = str(2301 + number)
        closes[f"{code}.TW"] = dict(zip(days, series))
        industries[code] = "電子零組件業"
    fp = FactorPanel(Panel(market(days, closes)), industries)
    last = len(days) - 1
    values = {symbol: fp.matrix("resid_mom_12")[fp.symbols.index(symbol), last] for symbol in closes}
    assert max(values, key=values.get) == "2301.TW"                # beats its own industry, not just rising
    assert min(values, key=values.get) == "2302.TW"
    trend = {symbol: fp.matrix("trend_200")[fp.symbols.index(symbol), last] for symbol in closes}
    assert min(trend.values()) > 0                                 # all rose: plain trend cannot tell them apart well


def test_the_revenue_calendar_decides_the_session_after_the_deadline():
    october = weekdays(date(2026, 10, 1), date(2026, 10, 31))     # the 10th is a Saturday: deadline Monday 12th
    assert check_days(october, "revenue") == {date(2026, 10, 13)}
    september = weekdays(date(2026, 9, 1), date(2026, 9, 30))     # the 10th is a Thursday
    assert check_days(september, "revenue") == {date(2026, 9, 11)}
    holiday = [day for day in september if day != date(2026, 9, 10)]   # the 10th is not a session
    assert check_days(holiday, "revenue") == {date(2026, 9, 14)}


class GatedPanel:
    def __init__(self, sessions, script, gated):
        self.sessions, self.index = sessions, {day: i for i, day in enumerate(sessions)}
        self.script, self.gated = script, gated

    def ranked(self, rule, position):
        return list(self.script[self.sessions[position]])

    def excluded(self, position):
        return set(self.gated.get(self.sessions[position], ()))


def test_the_lottery_gate_blocks_new_buys_but_keeps_holdings():
    days = weekdays(date(2024, 1, 1), date(2024, 1, 3))
    panel = GatedPanel(days, {days[0]: ["A", "B", "C", "D"], days[1]: ["A", "B", "C", "D"], days[2]: ["D", "A", "B", "C"]},
                       {days[0]: {"B"}, days[1]: {"A", "D"}, days[2]: {"A", "D"}})
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, keep=2, min_hold=0, exclude="v1")
    ranks = daily_rankings(panel, rule, days[0], days[-1])
    assert ranks[days[0]] == ["A", "C", "D"]                       # B is gated: the next best instead
    assert ranks[days[1]] == ["A", "C", "D"]                       # A and D are gated now but already held
    assert "exclude" not in rule.model_copy(update={"exclude": "none"}).canonical()


def test_the_gate_flags_a_jump_and_a_wild_stock():
    rng = np.random.default_rng(1)
    days = weekdays(date(2023, 1, 2), date(2023, 12, 29))
    closes = {}
    for number in range(20):
        noise = rng.normal(0.0005, 0.04 if number == 0 else 0.01, len(days))
        series = 20 * np.cumprod(1 + noise)
        if number == 1:
            series[-5:] *= 1.09                                     # a 9% jump five sessions ago
        closes[f"{1101 + number}.TW"] = dict(zip(days, series))
    fp = FactorPanel(Panel(market(days, closes)))
    flagged = fp.excluded(len(days) - 1)
    assert {"1101.TW", "1102.TW"} <= flagged and len(flagged) <= 7


def test_the_evidence_batch_is_six_new_all_stock_rules():
    from quant_platform.research.categories import classify, uses_0050

    rules = daily.BATCHES["evidence"]()
    assert len(rules) == 6 and len({rule.rule_hash for rule in rules}) == 6
    assert all(rule.core == 0 and not uses_0050(rule.canonical()) for rule in rules)
    traits = [trait for rule in rules for trait in classify(rule.canonical())["traits"]]
    assert "營收公布後每月決策" in traits and "不買樂透型股票" in traits
    assert {classify(rules[index].canonical())["family"] for index in (2, 3)} == {"趨勢動能"}


def test_the_raw_v2_factors_by_hand():
    days = weekdays(date(2023, 1, 2), date(2024, 6, 28))
    steady = [20 * 1.001 ** n for n in range(len(days))]                          # a new high every day
    flat = [20.0 + (n % 2) for n in range(len(days))]                              # 20, 21, 20, 21 ...
    opens = {"1101.TW": {day: price / 1.002 for day, price in zip(days, steady)},   # a gap up of 0 (open below close)
             "1102.TW": {day: price for day, price in zip(days, flat)}}
    fp = FactorPanel(Panel(market(days, {"1101.TW": dict(zip(days, steady)), "1102.TW": dict(zip(days, flat))}, opens)))
    first, second, last = fp.symbols.index("1101.TW"), fp.symbols.index("1102.TW"), len(days) - 1
    assert fp.matrix("high_52w_count_60")[first, last] == 60
    assert fp.matrix("range_position_60")[first, last] == pytest.approx(1.0)
    assert fp.matrix("range_position_60")[second, last] in (0.0, 1.0)
    # 1101 opens 0.2% below the close and the previous close is 0.1% lower: the gap is ln(1.001/1.002) < 0,
    # reversed and summed over 231 sessions
    assert fp.matrix("overnight_reversal_12")[first, last] == pytest.approx(-231 * np.log(1.001 / 1.002), rel=1e-3)
    assert np.isfinite(fp.matrix("low_ivol_60")[first, last])
    assert np.isnan(fp.matrix("low_pv_corr_20")[second, last])          # the traded value never changes: undefined


def test_the_novelty_gate_drops_a_copy_of_an_existing_signal(monkeypatch):
    from quant_platform.research import scan

    rng = np.random.default_rng(3)
    days = weekdays(date(2014, 1, 1), date(2020, 9, 30))
    closes = {f"{1101 + n}.TW": dict(zip(days, np.maximum(20 * np.cumprod(1 + rng.normal(0.0003, 0.015, len(days))), 10.5)))
              for n in range(40)}
    fp = FactorPanel(Panel(market(days, closes)))
    monkeypatch.setattr(scan, "signals", lambda: ["momentum_3", "trend_200", "range_position_60", "high_52w"])
    gate = scan.novelty(fp, ["range_position_60", "high_52w"])
    assert gate["high_52w"]["with"] is not None and set(gate) == {"range_position_60", "high_52w"}
    assert all(isinstance(item["kept"], bool) for item in gate.values())


def test_the_novelty_gate_leaves_out_the_weeks_a_sparse_signal_is_the_same_for_all(monkeypatch):
    """2026-11-20: short_cover_ahead is 0 for most stocks most weeks; a week without a correlation turned the mean NaN
    and the gate passed it as 0.0. A copy of high_52w that is flat for the first half is caught by the other half."""
    from quant_platform.research import scan

    rng = np.random.default_rng(3)
    days = weekdays(date(2014, 1, 1), date(2020, 9, 30))
    closes = {f"{1101 + n}.TW": dict(zip(days, np.maximum(20 * np.cumprod(1 + rng.normal(0.0003, 0.015, len(days))), 10.5)))
              for n in range(40)}
    fp = FactorPanel(Panel(market(days, closes)))
    usual = fp.matrix

    def matrix(name):
        if name != "sparse":
            return usual(name)
        values = usual("high_52w").copy()
        values[:, : values.shape[1] // 2] = 0.0
        return values

    monkeypatch.setattr(fp, "matrix", matrix)
    monkeypatch.setattr(scan, "signals", lambda: ["momentum_3", "high_52w"])
    gate = scan.novelty(fp, ["sparse"])["sparse"]
    assert gate["with"] == "high_52w" and gate["max_correlation"] > 0.9 and gate["kept"] is False


def test_rolling_beta_and_the_beta_on_the_days_the_market_fell():
    """2026-11-19: a stock moving twice the market has beta 2; one that follows it only on its falling days has a
    high down beta and a low one over every day; nothing leaks from later sessions."""
    import pandas as pd

    from quant_platform.research.daily import BETA_FACTORS, FACTOR_LABELS, rolling_beta

    rng = np.random.default_rng(0)
    market = pd.Series(rng.normal(0, 0.01, 400))
    frame = pd.DataFrame({"double": 2 * market, "half": 0.5 * market,
                          "down": np.where(market < 0, 1.5 * market, rng.normal(0, 0.01, 400))})
    beta = rolling_beta(frame, market, window=250, least=200)
    assert beta.iloc[:199].isna().all().all() and np.allclose(beta.iloc[-1][["double", "half"]], [2.0, 0.5])
    down = rolling_beta(frame, market, window=250, least=200, down_only=True, down_least=60)
    assert abs(down.iloc[-1]["down"] - 1.5) < 1e-6 and beta.iloc[-1]["down"] < 1.0
    cut = rolling_beta(frame.iloc[:300], market.iloc[:300], window=250, least=200)
    assert np.allclose(cut.iloc[-1], beta.iloc[299])                 # the same with the later sessions cut off
    assert not set(BETA_FACTORS) & set(FACTOR_LABELS)                # no model or robot list changes


def test_the_reference_price_follows_the_turnover_and_the_pair_gap_buys_the_laggard():
    """2026-11-20: Grinblatt and Han's reference price, and the long side of a pair."""
    prices = np.array([[10.0], [12.0], [12.0], [12.0]])
    share = np.array([[0.5], [0.5], [0.0], [1.0]])
    reference = daily.reference_prices(prices, share, warmup=1)
    assert np.isnan(reference[0, 0])                                   # nothing known before the first session
    assert reference[1, 0] == pytest.approx(10.0)                      # the first price
    assert reference[2, 0] == pytest.approx(0.5 * 12 + 0.5 * 10)       # half the holders bought at 12
    assert reference[3, 0] == pytest.approx(11.0)                      # nobody traded: unchanged
    rng = np.random.default_rng(5)
    common = rng.normal(0, 0.01, 300)
    logs = np.column_stack([common + rng.normal(0, 0.002, 300), common + rng.normal(0, 0.002, 300),
                            rng.normal(0, 0.01, 300)])
    logs[-20:, 0] -= 0.01                                              # the first one trails its twin lately
    gaps = daily.pair_gaps(logs, {"x": [0, 1, 2]}, [0, 280], window=250, least=200, gap=20)
    assert np.isnan(gaps[:280]).all()                                  # no month start with a year behind it before
    assert gaps[-1, 0] > 0 > gaps[-1, 1]                               # the laggard ranks high, its twin low
    cut = daily.pair_gaps(logs[:290], {"x": [0, 1, 2]}, [0, 280], window=250, least=200, gap=20)
    assert np.allclose(cut[285], gaps[285], equal_nan=True)            # later sessions change nothing before them


def test_the_literature_factors_from_prices_alone_are_causal():
    """2026-11-20: the residual momentum, the skewness and the illiquidity use nothing after the session."""
    rng = np.random.default_rng(9)
    days = weekdays(date(2022, 1, 3), date(2024, 6, 28))
    closes = {f"{2301 + number}.TW": dict(zip(days, 20 * np.cumprod(1 + rng.normal(0.0003, 0.015, len(days)))))
              for number in range(5)}
    fp = FactorPanel(Panel(market(days, closes)))
    later = {symbol: {**series, **{day: 1.0 for day in days[-15:]}} for symbol, series in closes.items()}
    changed = FactorPanel(Panel(market(days, later)))
    at = len(days) - 16
    for name in ("beta_resid_mom_12", "low_iskew_250", "illiquidity_60"):
        a, b = fp.matrix(name)[:, at], changed.matrix(name)[:, at]
        assert np.isfinite(a).any(), name
        assert np.allclose(a, b, equal_nan=True), name
