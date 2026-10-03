"""Daily-decision stock research on the recent market (roadmap S9-W02, owner's decision 2026-10-04).

The owner: a strategy can trade every trading day, not only on the monthly contribution day, and the
market of 2005 (7% price limits, call auctions, no day trading, no intraday odd lots) says little about
today. So rules here are chosen on 2015-06-01 (the ±10% limit) to 2026-09-30, are checked separately
from 2020-10-26 (continuous trading since 2020-03, intraday odd lots since 2020-10-26), and are judged
in the end by forward observation from 2026-10-05; data before 2015-06 is not used to choose rules.

The account is the owner's: NT$300,000 to start, NT$10,000 on the 5th of every month, everything
tradable. Every trading day after the close the rule ranks the eligible listed stocks by its factors
(price, trading and technical factors; one or several, percentile ranks added with weights, a negative
weight reverses a factor), sells a holding that fell out of its keep zone (top × keep) once it has
been held ``min_hold`` sessions, and buys the names that entered, at the close (the after-hours odd-lot
fill model, broker fees, 0.3% tax on stock sales). Existing positions are topped up only on
contribution days, and no order is smaller than ``min_trade``. ``core`` keeps that share of the
account in 0050. The benchmark is the same cash flow into 0050.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import warnings
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, field_validator

from quant_platform.research.costs import CostModel, affordable_shares, fill_price
from quant_platform.research.legacy_challenger import (
    BENCHMARK,
    LegacyData,
    RunResult,
    _month_starts,
    _tax_kind,
    _tick,
)
from quant_platform.research.registry import TrialRegistry
from quant_platform.research.stock_rules import FACTORS as PRICE_FACTORS
from quant_platform.research.stock_rules import (
    STANDARD_PLAN,
    WARMUP_YEARS,
    Panel,
    SeedPlan,
    _monthly_active,
    activity,
    curve,
    load_stock_data,
    stock_fingerprint,
)

RECENT_START = date(2015, 6, 1)      # daily price limit ±10%
REGIME_START = date(2020, 10, 26)    # continuous trading (2020-03-23) and intraday odd lots (2020-10-26)
RECENT_END = date(2026, 9, 30)       # forward observation starts after this
ENGINE_VERSION = "daily-1.0.0"
PERIOD = "recent"
TECHNICAL = {
    "ma_cross_20_60": "20 日均線高於 60 日均線的幅度（黃金交叉）",
    "rsi_14": "RSI（14 日）",
    "macd_hist": "MACD 柱狀體",
    "kd_k": "KD 的 K 值（9 日）",
    "bollinger_b": "布林通道位置 %B（20 日）",
    "breakout_55": "接近 55 日高點（突破）",
}
FACTOR_LABELS = {**PRICE_FACTORS, **TECHNICAL}
WINDOWS = {"1y": 12, "3y": 36}


class DailyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80)
    factors: dict[str, float] = Field(min_length=1, max_length=4)   # factor -> weight; negative reverses
    top: int = Field(default=20, ge=3, le=50)
    check: Literal["daily", "weekly", "monthly"] = "daily"
    keep: int = Field(default=3, ge=1, le=5)
    min_hold: int = Field(default=20, ge=0, le=250)                 # sessions
    core: float = Field(default=0.0, ge=0.0, le=0.9)                # share of the account in 0050
    min_trade: float = Field(default=1_000.0, ge=0)
    min_price: float = Field(default=10.0, ge=0)
    min_turnover: float = Field(default=20_000_000, ge=0)
    min_history: int = Field(default=252, ge=20, le=504)

    @field_validator("factors")
    @classmethod
    def _known(cls, value: dict[str, float]) -> dict[str, float]:
        for name, weight in value.items():
            if name not in FACTOR_LABELS:
                raise ValueError(f"未知因子：{name}")
            if not weight or abs(weight) > 3:
                raise ValueError("因子權重要在 -3～3 之間且不為 0")
        return value

    def canonical(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @property
    def rule_hash(self) -> str:
        body = {key: value for key, value in self.canonical().items() if key != "name"}
        body["engine_family"] = "daily"
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()

    @property
    def label(self) -> str:
        parts = []
        for name, weight in self.factors.items():
            text = FACTOR_LABELS[name]
            parts.append(("反向：" if weight < 0 else "") + text + (f"×{abs(weight):g}" if abs(weight) != 1 else ""))
        return "＋".join(parts)


# --- factor matrices ---------------------------------------------------------------------------
class FactorPanel:
    """Every factor for every stock and session (symbols × sessions), computed once with rolling
    windows on the dividend- and split-adjusted closes; ranking a day is then a column lookup."""

    def __init__(self, panel: Panel) -> None:
        self.panel = panel
        self.sessions, self.index, self.symbols = panel.sessions, panel.index, panel.symbols
        self._prices = pd.DataFrame(panel.filled.T)
        self._cache: dict[str, np.ndarray] = {}
        turnover = pd.DataFrame(panel.turnover.T)
        self.turnover_20 = turnover.rolling(20, min_periods=1).mean().to_numpy().T
        self.turnover_120 = turnover.rolling(120, min_periods=1).mean().to_numpy().T
        self.age = np.arange(len(self.sessions))[None, :] - panel.first[:, None]

    def matrix(self, factor: str) -> np.ndarray:
        if factor not in self._cache:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                frame = self._compute(factor)
            # computed as sessions × symbols; kept as symbols × sessions so a day is a column
            self._cache[factor] = np.ascontiguousarray(np.asarray(frame, dtype=np.float32).T)
        return self._cache[factor]

    def _compute(self, factor: str):
        p = self._prices
        if factor in ("low_volatility_60", "low_volatility_250", "low_max_return"):
            returns = p.pct_change(fill_method=None)
        if factor == "momentum_12_1":
            return (p.shift(21) / p.shift(252) - 1).to_numpy()
        if factor == "momentum_6":
            return (p / p.shift(126) - 1).to_numpy()
        if factor == "momentum_3":
            return (p / p.shift(63) - 1).to_numpy()
        if factor == "reversal_1":
            return (-(p / p.shift(21) - 1)).to_numpy()
        if factor == "reversal_5d":
            return (-(p / p.shift(5) - 1)).to_numpy()
        if factor == "low_volatility_60":
            return (-returns.rolling(60).std()).to_numpy()
        if factor == "low_volatility_250":
            return (-returns.rolling(250).std()).to_numpy()
        if factor == "low_max_return":
            return (-returns.rolling(21).max()).to_numpy()
        if factor == "high_52w":
            return (p / p.rolling(252).max()).to_numpy()
        if factor == "dividend_yield":
            paid = pd.DataFrame(self.panel.cash.T).rolling(252, min_periods=1).sum()
            return (paid / pd.DataFrame(self.panel.close.T)).to_numpy()
        if factor == "liquidity":
            return np.log(np.maximum(self.turnover_20.T, 1.0))
        if factor == "volume_surge":
            return (self.turnover_20 / np.maximum(self.turnover_120, 1.0)).T
        if factor == "trend_200":
            return (p / p.rolling(200).mean() - 1).to_numpy()
        if factor == "ma_cross_20_60":
            return (p.rolling(20).mean() / p.rolling(60).mean() - 1).to_numpy()
        if factor == "rsi_14":
            change = p.diff()
            gain = change.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
            loss = (-change.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
            return (100 - 100 / (1 + gain / loss)).to_numpy()
        if factor == "macd_hist":
            line = p.ewm(span=12, adjust=False).mean() - p.ewm(span=26, adjust=False).mean()
            return ((line - line.ewm(span=9, adjust=False).mean()) / p).to_numpy()
        if factor == "kd_k":
            low, high = p.rolling(9).min(), p.rolling(9).max()
            rsv = (p - low) / (high - low) * 100
            return rsv.ewm(alpha=1 / 3, adjust=False).mean().to_numpy()
        if factor == "bollinger_b":
            mean, deviation = p.rolling(20).mean(), p.rolling(20).std()
            return ((p - (mean - 2 * deviation)) / (4 * deviation)).to_numpy()
        if factor == "breakout_55":
            return (p / p.rolling(55).max()).to_numpy()
        raise ValueError(factor)

    def eligible(self, rule: DailyRule, position: int) -> np.ndarray:
        return ((self.panel.close[:, position] >= rule.min_price) & (self.turnover_20[:, position] >= rule.min_turnover)
                & (self.age[:, position] >= rule.min_history))

    def ranked(self, rule: DailyRule, position: int) -> list[str]:
        mask = self.eligible(rule, position)
        columns = {name: self.matrix(name)[:, position] for name in rule.factors}
        for values in columns.values():
            mask &= np.isfinite(values)
        if not mask.any():
            return []
        if len(columns) == 1:
            name, weight = next(iter(rule.factors.items()))
            score = columns[name][mask] * (1 if weight > 0 else -1)
        else:
            score = np.zeros(int(mask.sum()))
            for name, weight in rule.factors.items():
                score += weight * _percentile(columns[name][mask])
        chosen = np.flatnonzero(mask)
        order = np.argsort(-score, kind="stable")
        return [self.symbols[chosen[index]] for index in order]


def _percentile(values: np.ndarray) -> np.ndarray:
    if len(values) == 1:
        return np.ones(1)
    return np.argsort(np.argsort(values, kind="stable"), kind="stable") / (len(values) - 1)


def check_days(sessions: list[date], check: str) -> set[date]:
    if check == "daily":
        return set(sessions)
    if check == "weekly":
        seen, output = set(), set()
        for day in sessions:
            week = day.isocalendar()[:2]
            if week not in seen:
                seen.add(week)
                output.add(day)
        return output
    return {day for day, _amount in STANDARD_PLAN.schedule(sessions, sessions[0], sessions[-1])} if sessions else set()


def daily_rankings(fp: FactorPanel, rule: DailyRule, start: date, end: date) -> dict[date, list[str]]:
    """The holdings the rule wants on each check day: keep a holding while it ranks within top × keep
    (or until it has been held min_hold sessions while still eligible), fill up with the best others."""
    sessions = [day for day in fp.sessions if start <= day <= end]
    checks = check_days(sessions, rule.check)
    output: dict[date, list[str]] = {}
    current: list[str] = []
    bought: dict[str, int] = {}
    for count, day in enumerate(sessions):
        if day not in checks:
            continue
        ranked = fp.ranked(rule, fp.index[day])
        keep_zone = set(ranked[: rule.top * rule.keep])
        eligible = set(ranked)
        kept = [symbol for symbol in current
                if symbol in keep_zone or (symbol in eligible and count - bought[symbol] < rule.min_hold)]
        held = set(kept)
        fresh = [symbol for symbol in ranked if symbol not in held]
        current = kept + fresh[: max(0, rule.top - len(kept))]
        bought = {symbol: bought.get(symbol, count) for symbol in current}
        output[day] = list(current)
    return output


# --- simulation --------------------------------------------------------------------------------
def simulate_daily(data: LegacyData, rule: DailyRule | None, costs: CostModel, start: date, end: date,
                   ranks: dict[date, list[str]] | None = None, plan=None,
                   ledger: list[dict[str, object]] | None = None,
                   snapshots: dict | None = None) -> RunResult:
    """``rule=None`` is the benchmark: the same cash flow into 0050 on the day it arrives."""
    plan = plan or SeedPlan()
    sessions = [day for day in data.sessions if start <= day <= end]
    contributions = plan.schedule(sessions, start, end)
    by_day = dict(contributions)
    result = RunResult(rule.rule_hash[:12] if rule else "dca_0050", sessions, [], [], contributions)
    cash, units = 0.0, defaultdict(float)
    picks: list[str] = []
    minimum = max(rule.min_trade, 1.0) if rule else 1.0

    def price(symbol: str, day: date) -> float:
        series = data.closes.get(symbol, {})
        return series[day] if day in series else (data.last_close(symbol, day) or 0.0)

    def buy(symbol: str, budget: float, day: date) -> None:
        nonlocal cash
        close = data.closes.get(symbol, {}).get(day)
        if close is None or budget <= 0:
            return
        fill = fill_price(close, "BUY", costs.slippage_bps, _tick(symbol))
        shares = affordable_shares(min(budget, cash), fill, costs)
        if shares > 0:
            fee = costs.fee(shares * fill)
            cash -= shares * fill + fee
            units[symbol] += shares
            result.trades += 1
            result.fees += fee
            result.bought += shares * fill
            if ledger is not None:
                ledger.append({"day": day, "symbol": symbol, "side": "BUY", "shares": shares, "price": fill,
                               "fee": fee, "tax": 0})

    def sell_all(symbol: str, day: date) -> None:
        nonlocal cash
        close = data.closes.get(symbol, {}).get(day)
        if close is None or units[symbol] <= 0:
            return
        shares, fill = units[symbol], fill_price(close, "SELL", costs.slippage_bps, _tick(symbol))
        amount = shares * fill
        fee, tax = costs.fee(amount), costs.tax(amount, _tax_kind(symbol), "SELL")
        cash += amount - fee - tax
        units[symbol] = 0.0
        result.trades += 1
        result.fees += fee
        result.taxes += tax
        result.sold += amount
        if ledger is not None:
            ledger.append({"day": day, "symbol": symbol, "side": "SELL", "shares": shares, "price": fill,
                           "fee": fee, "tax": tax})

    for day in sessions:
        for symbol in [held for held, count in units.items() if count > 0]:
            factor = data.factors.get(symbol, {}).get(day, 1.0)
            if factor != 1.0:
                units[symbol] *= factor
                if ledger is not None:
                    ledger.append({"day": day, "symbol": symbol, "side": "ADJUST", "factor": factor})
        contribution = by_day.get(day, 0.0)
        cash += contribution
        if rule is None:
            if cash > 0:
                buy(BENCHMARK, cash, day)
        else:
            today = ranks.get(day) if ranks else None
            if today is not None:
                for symbol in [held for held, count in units.items() if count > 0 and held != BENCHMARK and held not in today]:
                    sell_all(symbol, day)
                picks = today
            if today is not None or contribution:
                total = cash + sum(count * price(symbol, day) for symbol, count in units.items() if count > 0)
                targets: dict[str, float] = {}
                if rule.core > 0:
                    targets[BENCHMARK] = total * rule.core
                for symbol in picks:
                    targets[symbol] = targets.get(symbol, 0.0) + total * (1 - rule.core) / len(picks)
                orders = []
                for symbol, target in targets.items():
                    held_value = units[symbol] * price(symbol, day)
                    gap = target - held_value
                    if gap > 0 and (held_value <= 0 or contribution):
                        orders.append((gap, symbol))
                for gap, symbol in sorted(orders, reverse=True):
                    amount = min(gap, cash)
                    if amount >= minimum:
                        buy(symbol, amount, day)
        value = cash + sum(count * price(symbol, day) for symbol, count in units.items() if count > 0)
        result.values.append(value)
        result.flows.append(contribution)
        if snapshots is not None:
            snapshots[day] = (cash, {symbol: count for symbol, count in units.items() if count > 0})
    return result


def _excess(run: RunResult, benchmark: RunResult) -> float:
    return (run.final_value - benchmark.final_value) / run.contributed if run.contributed else 0.0


def window_stats(data: LegacyData, rule: DailyRule, costs: CostModel, ranks: dict[date, list[str]], months: int,
                 start: date, end: date, cache: dict) -> dict[str, object]:
    excesses = []
    for first in _month_starts(data.sessions, start, end, months):
        total = first.year * 12 + first.month - 1 + months
        last = date(total // 12, total % 12 + 1, 1) - timedelta(days=1)
        run = simulate_daily(data, rule, costs, first, last, ranks)
        if (first, last) not in cache:
            cache[(first, last)] = simulate_daily(data, None, costs, first, last)
        if run.contributed:
            excesses.append(_excess(run, cache[(first, last)]))
    if not excesses:
        return {"count": 0}
    return {"count": len(excesses), "win_ratio": round(sum(value > 0 for value in excesses) / len(excesses), 4),
            "median_excess": round(statistics.median(excesses), 6), "worst_excess": round(min(excesses), 6),
            "best_excess": round(max(excesses), 6)}


def gate(metrics: dict) -> list[str]:
    """Pass: beats 0050 from 2015-06 and again from 2020-10, wins >= 60% of 3-year windows with a
    positive median and >= 50% of 1-year windows, and the drawdown is no more than 5 points deeper."""
    reasons = []
    for key, label in (("full_period_excess", "2015-06 起"), ("since_2020_excess", "2020-10 起")):
        value = metrics.get(key)
        if value is None or value <= 0:
            reasons.append(f"{label}輸 0050（{(value or 0):+.0%}）")
    windows = metrics.get("windows") or {}
    three, one = windows.get("3y") or {}, windows.get("1y") or {}
    if three.get("count") and ((three.get("win_ratio") or 0) < 0.6 or (three.get("median_excess") or 0) <= 0):
        reasons.append(f"3 年視窗勝率 {(three.get('win_ratio') or 0):.0%}、中位 {(three.get('median_excess') or 0):+.1%}")
    if one.get("count") and (one.get("win_ratio") or 0) < 0.5:
        reasons.append(f"1 年視窗勝率 {(one.get('win_ratio') or 0):.0%} < 50%")
    strategy, benchmark = metrics.get("max_drawdown"), metrics.get("benchmark_max_drawdown")
    if strategy is not None and benchmark is not None and strategy < benchmark - 0.05:
        reasons.append(f"最大回撤 {strategy:.0%} 比 0050 {benchmark:.0%} 深超過 5 個百分點")
    return reasons


def evaluate(data: LegacyData, fp: FactorPanel, rule: DailyRule, costs: CostModel,
             benchmark_cache: dict | None = None) -> dict[str, object]:
    cache = benchmark_cache if benchmark_cache is not None else {}
    ranks = daily_rankings(fp, rule, RECENT_START, RECENT_END)
    first = next((day for day in sorted(ranks) if ranks[day]), RECENT_START)
    run = simulate_daily(data, rule, costs, first, RECENT_END, ranks)
    benchmark = simulate_daily(data, None, costs, first, RECENT_END)
    regime_run = simulate_daily(data, rule, costs, REGIME_START, RECENT_END, ranks)
    regime_benchmark = simulate_daily(data, None, costs, REGIME_START, RECENT_END)
    yearly: dict[str, float] = {}
    active = _monthly_active(run, benchmark)
    for month, value in active.items():
        yearly[month[:4]] = yearly.get(month[:4], 0.0) + value
    return {
        "engine": ENGINE_VERSION, "spec": rule.canonical(), "rule_hash": rule.rule_hash, "label": rule.label,
        "start": first.isoformat(), "end": RECENT_END.isoformat(),
        "strategy": run.summary(), "benchmark": benchmark.summary(),
        "full_period_excess": round(_excess(run, benchmark), 6),
        "since_2020": {"start": REGIME_START.isoformat(), "strategy": regime_run.summary(),
                       "benchmark": regime_benchmark.summary(), "excess": round(_excess(regime_run, regime_benchmark), 6)},
        "windows": {key: window_stats(data, rule, costs, ranks, months, first, RECENT_END, cache.setdefault(key, {}))
                    for key, months in WINDOWS.items()},
        "monthly_active_returns": active, "yearly": {year: round(value, 4) for year, value in sorted(yearly.items())},
        "activity": activity(run), "curve": curve(run, benchmark),
    }


def load(history: str | Path) -> tuple[LegacyData, FactorPanel]:
    data = load_stock_data(history, RECENT_START.year - WARMUP_YEARS, RECENT_END.year)
    return data, FactorPanel(Panel(data))


def fingerprint(history: str | Path) -> str:
    return "daily:" + stock_fingerprint(history, RECENT_START.year - WARMUP_YEARS, RECENT_END.year,
                                        until=RECENT_END).removeprefix("stocks:")


def run_trial(rule: DailyRule, history: str | Path, registry: TrialRegistry, reports_dir: Path, costs: CostModel,
              data: LegacyData, fp: FactorPanel, data_fingerprint: str, benchmark_cache: dict | None = None,
              stamp: str | None = None) -> tuple[object, dict]:
    plan = {"kind": "SeedPlan", "initial": SeedPlan().initial, "monthly_amount": SeedPlan().monthly_amount,
            "day_of_month": 5}
    payload = {"rule": rule.canonical(), "period": PERIOD, "costs": costs.as_dict(), "data": data_fingerprint,
               "engine": ENGINE_VERSION, "plan": plan}
    input_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    existing = registry.find("candidate", PERIOD, input_hash)
    if existing is not None:
        return existing, json.loads((reports_dir / existing.report_file).read_text(encoding="utf-8"))
    report = evaluate(data, fp, rule, costs, benchmark_cache)
    report["data_fingerprint"] = data_fingerprint
    report["plan"] = plan
    stamp = stamp or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_file = f"{PERIOD}-daily-{rule.rule_hash[:8]}-{stamp}.json"
    (reports_dir / report_file).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    strategy, benchmark = report["strategy"], report["benchmark"]
    metrics = {
        "engine": ENGINE_VERSION, "start": report["start"], "end": report["end"], "plan": plan,
        "xirr": strategy["xirr"], "benchmark_xirr": benchmark["xirr"], "max_drawdown": strategy["max_drawdown"],
        "benchmark_max_drawdown": benchmark["max_drawdown"], "final_value": strategy["final_value"],
        "benchmark_final_value": benchmark["final_value"], "contributed": strategy["contributed"],
        "full_period_excess": report["full_period_excess"], "since_2020_excess": report["since_2020"]["excess"],
        "since_2020_final_value": report["since_2020"]["strategy"]["final_value"],
        "since_2020_benchmark_final_value": report["since_2020"]["benchmark"]["final_value"],
        "windows": report["windows"], "trades": strategy["trades"], "costs": strategy["fees"] + strategy["taxes"],
        "cost_share": report["activity"].get("cost_share"), "turnover": report["activity"].get("turnover"),
        "orders_per_month": report["activity"].get("orders_per_month"),
    }
    metrics["reasons"] = gate(metrics)
    record = registry.register(kind="candidate", period=PERIOD, spec_hash=rule.rule_hash, spec_name=rule.name,
                               input_hash=input_hash, data_fingerprint=data_fingerprint, report_file=report_file,
                               metrics=metrics)
    return record, report


# --- batches -----------------------------------------------------------------------------------
OSCILLATORS = ("rsi_14", "kd_k", "bollinger_b")


def factor_batch() -> list[DailyRule]:
    """One rule per factor (and the oscillators reversed: buy the oversold), alone and with half the
    account kept in 0050: different factors, not parameter variants (the owner, 2026-10-04)."""
    singles: list[tuple[str, dict[str, float]]] = [(FACTOR_LABELS[name], {name: 1.0}) for name in FACTOR_LABELS]
    singles += [(f"反向：{FACTOR_LABELS[name]}（買超賣）", {name: -1.0}) for name in OSCILLATORS]
    rules = []
    for label, factors in singles:
        for core, word in ((0.0, ""), (0.5, "、一半放 0050")):
            rules.append(DailyRule(name=f"每天 {label}：前 20 名{word}"[:80], factors=factors, core=core))
    return rules


BATCHES = {"factors": factor_batch}
