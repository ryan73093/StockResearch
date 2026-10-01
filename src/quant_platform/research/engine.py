"""Cash-flow backtest engine (S3-W04).

One simulation walks the exchange sessions between ``start`` and ``end``:

1. corporate actions at the open: splits and stock dividends multiply units
   (fractions are paid out at the day's close); cash dividends are recorded on
   the ex-date and paid ``dividend_lag_days`` later;
2. dividends due and contributions arrive as cash;
3. on invest days the spec decides a budget and the orders, which fill in the
   14:30 after-hours odd-lot auction at close ± slippage (research/costs.py);
   sells settle before buys;
4. the account is valued at the close.

Signals only read closes up to the current session, which is known at 13:30,
before the after-hours order window. Everything is deterministic: the same
inputs give the same ``output_hash``.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from quant_platform.research.allocation import target_weights
from quant_platform.research.cashflow import ContributionPlan
from quant_platform.research.costs import CostModel, affordable_shares, fill_price
from quant_platform.research.market import MarketData
from quant_platform.research.metrics import annualized, max_drawdown, unit_values, xirr
from quant_platform.research.spec import StrategySpec

# 1.1.0: orders postponed past no-trade sessions; explicit contributions
# 1.2.0: the input hash covers only the data up to the simulation's end (MarketData.fingerprint_until)
# 2026-10-01 (version unchanged on purpose): signals read split-adjusted closes, and specs may use
# allocation.defensive / allocation.rotation. A run is affected only when a signal instrument has a unit
# ratio up to its end, and exactly those runs get "signals": "split-adjusted" in their input hash, so
# every earlier run (the whole development period) keeps its hash and its recorded result.
ENGINE_VERSION = "1.2.0"
DRAWDOWN_LOOKBACK = 252


@dataclass(frozen=True, slots=True)
class Trade:
    day: date
    asset: str
    side: str
    shares: int
    price: float
    amount: float
    fee: int
    tax: int


@dataclass(slots=True)
class Account:
    cash: float = 0.0
    shares: dict[str, int] = field(default_factory=dict)
    pending_dividends: list[tuple[date, float]] = field(default_factory=list)


@dataclass(frozen=True)
class SimulationResult:
    spec_name: str
    spec_hash: str
    input_hash: str
    start: date
    end: date
    days: list[date]
    values: list[float]
    flows: list[float]
    trades: list[Trade]
    contributions: list[tuple[date, float]]
    final_value: float
    final_cash: float
    total_contributed: float
    dividends_received: float
    fees: int
    taxes: int
    xirr: float | None
    unit_cagr: float | None
    max_drawdown: float
    drawdown_peak: date | None
    drawdown_trough: date | None

    def summary(self) -> dict[str, object]:
        return {
            "spec": self.spec_name,
            "spec_hash": self.spec_hash,
            "input_hash": self.input_hash,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "final_value": round(self.final_value, 2),
            "final_cash": round(self.final_cash, 2),
            "total_contributed": round(self.total_contributed, 2),
            "profit": round(self.final_value - self.total_contributed, 2),
            "dividends_received": round(self.dividends_received, 2),
            "fees": self.fees,
            "taxes": self.taxes,
            "trades": len(self.trades),
            "xirr": None if self.xirr is None else round(self.xirr, 6),
            "unit_cagr": None if self.unit_cagr is None else round(self.unit_cagr, 6),
            "max_drawdown": round(self.max_drawdown, 6),
            "drawdown_peak": self.drawdown_peak.isoformat() if self.drawdown_peak else None,
            "drawdown_trough": self.drawdown_trough.isoformat() if self.drawdown_trough else None,
        }

    def monthly_returns(self) -> dict[str, float]:
        """Unit-value return per calendar month (YYYY-MM), month-end to month-end."""
        units = unit_values(self.values, self.flows)
        month_end: dict[str, float] = {}
        for day, unit in zip(self.days, units):
            month_end[f"{day:%Y-%m}"] = unit
        output: dict[str, float] = {}
        previous = units[0]
        for month, unit in month_end.items():
            output[month] = unit / previous - 1 if previous else 0.0
            previous = unit
        return output

    @property
    def output_hash(self) -> str:
        payload = {
            "summary": self.summary(),
            "trades": [
                [trade.day.isoformat(), trade.asset, trade.side, trade.shares, trade.price, trade.fee, trade.tax]
                for trade in self.trades
            ],
            "values": [round(value, 4) for value in self.values],
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def input_hash(
    spec: StrategySpec, plan: ContributionPlan, costs: CostModel, market: MarketData,
    start: date, end: date, dividend_lag_days: int, execution_lag: int = 0,
    contributions: list[tuple[date, float]] | None = None,
) -> str:
    payload = {
        "engine": ENGINE_VERSION,
        "spec": spec.spec_hash,
        "plan": plan.as_dict(),
        "contributions": [[day.isoformat(), amount] for day, amount in contributions or []],
        "costs": costs.as_dict(),
        "data": market.fingerprint_until(end),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "dividend_lag_days": dividend_lag_days,
    }
    if execution_lag:
        payload["execution_lag"] = execution_lag
    if market.has_unit_ratios(spec.signals, end):
        payload["signals"] = "split-adjusted"
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def common_start(spec: StrategySpec, market: MarketData) -> date:
    """First session on which every instrument of the spec has traded."""
    return max(market.first_day(asset) for asset in set(spec.assets) | {spec.signal} | spec.signals)


def _invest_days(spec: StrategySpec, sessions: list[date], contributions: list[tuple[date, float]]) -> set[date]:
    if spec.contribution.invest_on == "contribution_day":
        return {day for day, _ in contributions}
    days = set()
    months = sorted({(day.year, day.month) for day in sessions})
    for year, month in months:
        target = date(year, month, spec.contribution.day_of_month or 1)
        position = bisect.bisect_left(sessions, target)
        if position < len(sessions) and (sessions[position].year, sessions[position].month) == (year, month):
            days.add(sessions[position])
    return days


def _multiplier(spec: StrategySpec, market: MarketData, day: date) -> float:
    sizing = spec.sizing
    if sizing.type == "moving_average":
        closes = market.adjusted_trailing(spec.signal, day, sizing.ma_sessions)
        if len(closes) < sizing.ma_sessions:
            return 1.0
        return sizing.weak_multiplier if closes[-1] < sum(closes) / len(closes) else sizing.strong_multiplier
    if sizing.type == "drawdown":
        closes = market.adjusted_trailing(spec.signal, day, DRAWDOWN_LOOKBACK)
        if not closes:
            return 1.0
        drawdown = closes[-1] / max(closes) - 1
        return sizing.weak_multiplier if drawdown <= -sizing.drawdown_threshold else sizing.strong_multiplier
    return 1.0


def simulate(
    spec: StrategySpec,
    market: MarketData,
    plan: ContributionPlan,
    costs: CostModel | None = None,
    start: date | None = None,
    end: date | None = None,
    dividend_lag_days: int = 25,
    execution_lag: int = 0,
    contributions: list[tuple[date, float]] | None = None,
) -> SimulationResult:
    """``execution_lag`` > 0 decides on the invest day's close but fills that
    many sessions later (robustness check "晚一天執行"). ``contributions``
    replaces the plan's schedule with actual deposits (the DCA shadow account);
    each moves to the next session when its date is closed."""
    costs = costs or CostModel()
    first = common_start(spec, market)
    start = max(start or first, first)
    end = end or min(market.last_day(asset) for asset in spec.assets)
    sessions = [day for day in market.sessions if start <= day <= end]
    if not sessions:
        raise ValueError(f"{start}～{end} 沒有交易日")
    if contributions is None:
        contributions = plan.schedule(sessions, start, end)
    else:
        moved: dict[date, float] = {}
        for day, amount in contributions:
            position = bisect.bisect_left(sessions, day)
            if position < len(sessions) and amount:
                moved[sessions[position]] = moved.get(sessions[position], 0.0) + float(amount)
        contributions = sorted(moved.items())
    by_day: dict[date, float] = {}
    for day, amount in contributions:
        by_day[day] = by_day.get(day, 0.0) + amount
    decision_days = sorted(_invest_days(spec, sessions, contributions))
    position = {day: index for index, day in enumerate(sessions)}
    # execution day → decision day (signals read closes up to the decision day)
    invest_days = {
        sessions[position[day] + execution_lag]: day
        for day in decision_days if position[day] + execution_lag < len(sessions)
    }

    account = Account(shares={asset: 0 for asset in spec.assets})
    trades: list[Trade] = []
    values: list[float] = []
    flows: list[float] = []
    dividends_received = 0.0
    new_money = 0.0
    pending_signal_day: date | None = None

    def execute(day: date, asset: str, side: str, shares: int, price: float) -> None:
        amount = shares * price
        fee = costs.fee(amount)
        tax = costs.tax(amount, market.tax_kind.get(asset, "stock_etf"), side)
        if side == "BUY":
            account.cash -= amount + fee
            account.shares[asset] += shares
        else:
            account.cash += amount - fee - tax
            account.shares[asset] -= shares
        trades.append(Trade(day, asset, side, shares, price, round(amount, 2), fee, tax))

    for day in sessions:
        # 1. corporate actions at the open
        for asset in spec.assets:
            ratio = market.unit_ratios.get(asset, {}).get(day)
            if ratio and account.shares[asset]:
                exact = account.shares[asset] * ratio
                whole = math.floor(exact + 1e-9)
                fraction = exact - whole
                account.shares[asset] = whole
                if fraction > 1e-9:
                    account.cash += fraction * (market.close(asset, day) or 0.0)
            cash = market.dividends.get(asset, {}).get(day)
            if cash and account.shares[asset]:
                account.pending_dividends.append(
                    (day + timedelta(days=dividend_lag_days), account.shares[asset] * cash)
                )
        # 2. dividends due and new money
        due = [item for item in account.pending_dividends if item[0] <= day]
        if due:
            account.pending_dividends = [item for item in account.pending_dividends if item[0] > day]
            paid = sum(amount for _, amount in due)
            account.cash += paid
            dividends_received += paid
        contribution = by_day.get(day, 0.0)
        account.cash += contribution
        new_money += contribution

        # 3. orders in the after-hours window; a session without a trade in
        #    one of the assets postpones the orders to the next session that has one
        if day in invest_days:
            pending_signal_day = invest_days[day]
        if pending_signal_day is not None and all(market.close(asset, day) for asset in spec.assets):
            _invest(spec, market, costs, account, day, new_money, execute, pending_signal_day)
            new_money = 0.0
            pending_signal_day = None

        # 4. valuation at the close
        value = account.cash + sum(
            shares * (market.last_close(asset, day) or 0.0) for asset, shares in account.shares.items()
        )
        values.append(value)
        flows.append(contribution)

    final_value = values[-1]
    total = sum(amount for _, amount in contributions)
    units = unit_values(values, flows)
    drawdown, peak, trough = max_drawdown(units)
    rate = xirr([(day, -amount) for day, amount in contributions] + [(sessions[-1], final_value)])
    return SimulationResult(
        spec_name=spec.name,
        spec_hash=spec.spec_hash,
        input_hash=input_hash(
            spec, plan, costs, market, start, end, dividend_lag_days, execution_lag, contributions
        ),
        start=sessions[0],
        end=sessions[-1],
        days=sessions,
        values=values,
        flows=flows,
        trades=trades,
        contributions=contributions,
        final_value=final_value,
        final_cash=account.cash,
        total_contributed=total,
        dividends_received=dividends_received,
        fees=sum(trade.fee for trade in trades),
        taxes=sum(trade.tax for trade in trades),
        xirr=rate,
        unit_cagr=annualized(units[-1] / units[0], (sessions[-1] - sessions[0]).days),
        max_drawdown=drawdown,
        drawdown_peak=sessions[peak] if drawdown < 0 else None,
        drawdown_trough=sessions[trough] if drawdown < 0 else None,
    )


def _invest(spec, market, costs, account, day, new_money, execute, signal_day) -> None:
    sizing = spec.sizing
    if sizing.type == "all_cash":
        budget = account.cash
    else:
        budget = min(account.cash, new_money * _multiplier(spec, market, signal_day))
        reserve_cap = new_money * sizing.max_reserve_months
        if account.cash - budget > reserve_cap:
            budget = account.cash - reserve_cap
    weights = target_weights(spec, lambda asset, sessions: market.adjusted_trailing(asset, signal_day, sessions))
    prices = {asset: market.close(asset, day) for asset in spec.assets}
    holdings = {asset: account.shares[asset] * prices[asset] for asset in spec.assets}
    invested = sum(holdings.values())

    # One instrument at 100% never drifts; a cash target (defensive with no weights) does.
    if (len(weights) > 1 or sum(weights.values()) < 1 - 1e-9) and spec.allocation.rebalance == "band" and invested > 0:
        drift = max(abs(holdings[asset] / invested - weights[asset]) for asset in weights)
        if drift > spec.allocation.band:
            target_total = invested + budget
            for asset in sorted(weights):
                excess = holdings[asset] - weights[asset] * target_total
                if excess > 0:
                    price = fill_price(prices[asset], "SELL", costs.slippage_bps)
                    shares = min(account.shares[asset], math.floor(excess / price))
                    if shares > 0:
                        before = account.cash
                        execute(day, asset, "SELL", shares, price)
                        budget += account.cash - before
                        holdings[asset] -= shares * prices[asset]

    budget = min(budget, account.cash)
    if budget <= 0:
        return
    target_total = sum(holdings.values()) + budget
    gaps = {asset: max(0.0, weights[asset] * target_total - holdings[asset]) for asset in weights}
    gap_total = sum(gaps.values())
    allowances = {
        asset: (budget * gaps[asset] / gap_total) if gap_total > 0 else budget * weights[asset]
        for asset in weights
    }
    for asset in sorted(allowances):
        allowance = min(allowances[asset], account.cash)
        price = fill_price(prices[asset], "BUY", costs.slippage_bps)
        shares = affordable_shares(allowance, price, costs)
        if shares > 0:
            execute(day, asset, "BUY", shares, price)
