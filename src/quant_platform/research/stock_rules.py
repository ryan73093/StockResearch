"""Rule-based stock portfolios against 0050 DCA (REQUIREMENTS §7, §8; 2026-10-03).

A rule never names a stock: on each contribution day it ranks every listed common stock that is
eligible (price, liquidity, listing age) by one factor computed from its own past prices and
dividends, buys the top N in equal weight and sells what dropped out (monthly or quarterly). The
universe is the survivorship-free daily quotes in history/stocks (every stock on the days it
traded), so a stock that later delisted is held until its last quote and then valued at it.
Fills, fees, the 0.3% tax, dividends and splits follow research/legacy_challenger.py; the cash
flow, windows and excess definition follow the ETF research. Every rule run is registered as a
candidate trial and counts in the multiple-testing correction.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from quant_platform.research.cashflow import ContributionPlan
from quant_platform.research.costs import CostModel
from quant_platform.research.history.dataset import read_series, sha256
from quant_platform.research.history.stocks import read_year
from quant_platform.research.legacy_challenger import (
    BENCHMARK, SPLIT_GAP, LegacyData, Variant, _excess, exchange_events, simulate, windows,
)
from quant_platform.research.metrics import unit_values
from quant_platform.research.periods import PERIODS, ResearchGateError
from quant_platform.research.registry import TrialRegistry

FACTORS = {
    "momentum_12_1": "12 個月動能（扣最近 1 個月）",
    "momentum_6": "6 個月動能",
    "reversal_1": "1 個月反轉",
    "low_volatility_60": "60 日低波動",
    "high_52w": "接近 52 週高點",
    "dividend_yield": "近 12 個月現金殖利率",
}
STANDARD_PLAN = ContributionPlan(monthly_amount=10_000, day_of_month=5)
ENGINE_VERSION = "stocks-1.0.0"


class StockRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80)
    factor: Literal["momentum_12_1", "momentum_6", "reversal_1", "low_volatility_60", "high_52w", "dividend_yield"]
    top: int = Field(default=20, ge=5, le=50)
    rebalance: Literal["monthly", "quarterly"] = "monthly"
    buffer: int = Field(default=1, ge=1, le=5)   # keep a holding while it still ranks within buffer × top
    min_turnover: float = Field(default=20_000_000, ge=0)   # 20-session average NT$ traded
    min_price: float = Field(default=10.0, ge=0)
    min_history: int = Field(default=252, ge=20, le=504)   # sessions listed before a stock is ranked
    universe: Literal["twse"] = "twse"

    def canonical(self) -> dict[str, object]:
        return self.model_dump(mode="json")

    @property
    def rule_hash(self) -> str:
        body = {key: value for key, value in self.canonical().items() if key != "name"}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


# --- data -------------------------------------------------------------------------
def unit_factors_all(closes: dict[date, float], events: dict[date, tuple[str, float, float]]) -> dict[date, float]:
    """Units multiplier per session for any ex-rights event (cash reinvested at the reference price,
    shares as distributed: either way units × before = value), plus unmarked splits."""
    factors: dict[date, float] = {}
    days = sorted(closes)
    for previous, day in zip(days, days[1:]):
        event = events.get(day)
        if event is not None:
            _kind, before, reference = event
            if before > reference > 0:
                factors[day] = before / reference
            continue
        ratio = closes[previous] / closes[day] if closes[day] > 0 else 1.0
        if not SPLIT_GAP < ratio < 1 / SPLIT_GAP:
            whole = round(ratio) if ratio >= 1 else 1 / round(1 / ratio)
            factors[day] = whole if abs(ratio - whole) <= 0.15 * whole else ratio
    return factors


def load_stock_data(base: str | Path, first_year: int, last_year: int) -> LegacyData:
    """Every listed TWSE stock's closes and turnover for the years, 0050 as the benchmark, and the
    unit factors from the official ex-rights tables."""
    base = Path(base)
    closes: dict[str, dict[date, float]] = {}
    traded: dict[str, dict[date, float]] = {}
    for year in range(first_year, last_year + 1):
        for row in read_year(base / "stocks" / "twse" / f"{year}.parquet"):
            symbol = f"{row['code']}.TW"
            closes.setdefault(symbol, {})[row["date"]] = float(row["close"])
            traded.setdefault(symbol, {})[row["date"]] = float(row["turnover"] or 0)
    benchmark = {row["date"]: float(row["close"]) for row in read_series(base / "daily" / "0050.parquet")
                 if row["close"] and first_year <= row["date"].year <= last_year}
    closes[BENCHMARK] = benchmark
    traded[BENCHMARK] = {day: 1e12 for day in benchmark}
    events = exchange_events(base / "raw", first_year)
    factors = {symbol: unit_factors_all(series, events.get(symbol, {})) for symbol, series in closes.items()}
    sessions = sorted(row["date"] for row in read_series(base / "daily" / "TAIEX.parquet")
                      if first_year <= row["date"].year <= last_year)
    return LegacyData(sessions=sessions, closes=closes, traded_value=traded, factors=factors, predictions={},
                      notes={"symbols": len(closes) - 1, "years": [first_year, last_year]})


def stock_fingerprint(base: str | Path, first_year: int, last_year: int) -> str:
    base = Path(base)
    digest = hashlib.sha256()
    for year in range(first_year, last_year + 1):
        path = base / "stocks" / "twse" / f"{year}.parquet"
        digest.update(f"{year}:{sha256(path) if path.is_file() else 'missing'}".encode())
    digest.update(sha256(base / "daily" / "0050.parquet").encode())
    for path in sorted((base / "raw" / "twse_ex_rights").glob("*.json")):
        if path.stem.isdigit() and first_year <= int(path.stem) <= last_year:
            digest.update(f"{path.stem}:{sha256(path)}".encode())
    return "stocks:" + digest.hexdigest()


# --- ranking ------------------------------------------------------------------------
class Panel:
    """Adjusted closes and turnover of every stock aligned to the sessions (NaN where not traded)."""

    def __init__(self, data: LegacyData) -> None:
        self.sessions = data.sessions
        self.index = {day: position for position, day in enumerate(self.sessions)}
        symbols = sorted(symbol for symbol in data.closes if symbol != BENCHMARK)
        self.symbols = symbols
        count = len(self.sessions)
        self.close = np.full((len(symbols), count), np.nan)
        self.adjusted = np.full((len(symbols), count), np.nan)
        self.turnover = np.full((len(symbols), count), np.nan)
        self.cash = np.zeros((len(symbols), count))      # cash dividend per unit on its ex-date
        self.first = np.full(len(symbols), count)
        for row, symbol in enumerate(symbols):
            growth = 1.0
            factors = data.factors.get(symbol, {})
            for day, close in sorted(data.closes[symbol].items()):
                position = self.index.get(day)
                if position is None:
                    continue
                growth *= factors.get(day, 1.0)
                self.close[row, position] = close
                self.adjusted[row, position] = close * growth
                self.turnover[row, position] = data.traded_value[symbol].get(day, 0.0)
                self.first[row] = min(self.first[row], position)
                if day in factors and 1 < factors[day] < 1 / SPLIT_GAP:
                    # an ex-rights day: the payout per unit is what the unit factor reinvested
                    self.cash[row, position] = close * (factors[day] - 1)
        self.filled = _forward_fill(self.adjusted)

    def ranked(self, rule: StockRule, day: date) -> list[str]:
        position = self.index[day]
        start = max(0, position - 19)
        turnover = np.nanmean(self.turnover[:, start:position + 1], axis=1)
        eligible = (
            (self.close[:, position] >= rule.min_price) & (turnover >= rule.min_turnover)
            & (position - self.first >= rule.min_history)
        )
        score = self._score(rule.factor, position)
        eligible &= ~np.isnan(score)
        order = [index for index in np.argsort(-score, kind="stable") if eligible[index]]
        return [self.symbols[index] for index in order]

    def _score(self, factor: str, position: int) -> np.ndarray:
        prices = self.filled

        def back(sessions: int) -> np.ndarray:
            return prices[:, max(0, position - sessions)]

        now = prices[:, position]
        if factor == "momentum_12_1":
            return back(21) / back(252) - 1
        if factor == "momentum_6":
            return now / back(126) - 1
        if factor == "reversal_1":
            return -(now / back(21) - 1)
        if factor == "low_volatility_60":
            window = prices[:, max(0, position - 60):position + 1]
            returns = window[:, 1:] / window[:, :-1] - 1
            return -np.nanstd(returns, axis=1)
        if factor == "high_52w":
            window = prices[:, max(0, position - 252):position + 1]
            return now / np.nanmax(window, axis=1)
        if factor == "dividend_yield":
            paid = self.cash[:, max(0, position - 252):position + 1].sum(axis=1)
            return paid / self.close[:, position]
        raise ValueError(factor)


def _forward_fill(values: np.ndarray) -> np.ndarray:
    output = values.copy()
    for row in range(output.shape[0]):
        last = np.nan
        for column in range(output.shape[1]):
            if np.isnan(output[row, column]):
                output[row, column] = last
            else:
                last = output[row, column]
    return output


def rankings(panel: Panel, rule: StockRule, start: date, end: date,
             plan: ContributionPlan = STANDARD_PLAN) -> dict[date, list[str]]:
    """Picks on each contribution day; a quarterly rule repeats its picks on the two months between."""
    sessions = [day for day in panel.sessions if start <= day <= end]
    output: dict[date, list[str]] = {}
    current: list[str] = []
    for count, (day, _amount) in enumerate(plan.schedule(sessions, start, end)):
        if rule.rebalance == "monthly" or count % 3 == 0 or not current:
            ranked = panel.ranked(rule, day)
            keep_zone = set(ranked[: rule.top * rule.buffer])
            kept = [symbol for symbol in current if symbol in keep_zone]       # still good enough: hold
            fresh = [symbol for symbol in ranked if symbol not in kept]
            current = kept + fresh[: max(0, rule.top - len(kept))]
        output[day] = list(current)
    return output


# --- evaluation ---------------------------------------------------------------------
def evaluate_rule(data: LegacyData, panel: Panel, rule: StockRule, costs: CostModel, start: date, end: date,
                  plan: ContributionPlan = STANDARD_PLAN, window_months: tuple[int, ...] = (36, 60)) -> dict:
    ranks = rankings(panel, rule, start, end, plan)
    first = next((day for day in sorted(ranks) if ranks[day]), None)
    if first is None:
        raise ResearchGateError("期間內沒有任何一天有合格的股票")
    variant = Variant(rule.rule_hash[:12], rule.name, rule.top, "monthly")
    run = simulate(data, variant, costs, first, end, ranks, plan)
    benchmark = simulate(data, None, costs, first, end, plan=plan)
    cache: dict = {}
    report = {
        "engine": ENGINE_VERSION, "spec": rule.canonical(), "rule_hash": rule.rule_hash,
        "start": first.isoformat(), "end": end.isoformat(),
        "monthly_active_returns": _monthly_active(run, benchmark),
        "strategy": run.summary(), "benchmark": benchmark.summary(),
        "full_period_excess": round(_excess(run, benchmark), 6),
        "windows": {f"{months // 12}y": windows(data, variant, costs, first, end, months, ranks, cache)
                    for months in window_months},
        "picks_per_day": round(statistics.fmean(len(picks) for picks in ranks.values()), 1),
    }
    return report


def _monthly_active(run, benchmark) -> dict[str, float]:
    def monthly(result) -> dict[str, float]:
        units = unit_values(result.values, result.flows)
        ends: dict[str, float] = {}
        for day, unit in zip(result.days, units):
            ends[f"{day:%Y-%m}"] = unit
        output, previous = {}, units[0]
        for month, unit in ends.items():
            output[month] = unit / previous - 1 if previous else 0.0
            previous = unit
        return output

    mine, theirs = monthly(run), monthly(benchmark)
    return {month: round(mine[month] - theirs[month], 10) for month in mine if month in theirs}


def run_stock_trial(rule: StockRule, period: str, base: str | Path, registry: TrialRegistry, reports_dir: Path,
                    costs: CostModel | None = None, data: LegacyData | None = None, panel: Panel | None = None,
                    generated_at: str | None = None) -> tuple[object, dict]:
    """Evaluate one rule on one research period and register it (reused when already run)."""
    if period not in ("development", "validation", "holdout"):
        raise ResearchGateError("個股規則只能用開發、驗證或保留期評估")
    start, end = PERIODS[period]
    costs = costs or CostModel()
    fingerprint = stock_fingerprint(base, start.year, end.year)
    payload = {"rule": rule.canonical(), "period": period, "costs": costs.as_dict(), "data": fingerprint,
               "engine": ENGINE_VERSION}
    input_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    existing = registry.find("candidate", period, input_hash)
    if existing is not None:
        return existing, json.loads((reports_dir / existing.report_file).read_text(encoding="utf-8"))
    if period == "holdout":
        mine = [record for record in registry.records() if record.spec_hash == rule.rule_hash]
        if not any(record.period == "validation" for record in mine):
            raise ResearchGateError("保留期前必須先有驗證期試驗")
        if any(record.period == "holdout" for record in mine):
            raise ResearchGateError("此規則已評估過保留期，每個候選只能評估一次")
    data = data or load_stock_data(base, start.year, end.year)
    panel = panel or Panel(data)
    report = evaluate_rule(data, panel, rule, costs, start, end)
    report["data_fingerprint"] = fingerprint
    stamp = generated_at or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_file = f"{period}-stocks-{rule.rule_hash[:8]}-{stamp}.json"
    (reports_dir / report_file).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    strategy, benchmark = report["strategy"], report["benchmark"]
    record = registry.register(
        kind="candidate", period=period, spec_hash=rule.rule_hash, spec_name=rule.name, input_hash=input_hash,
        data_fingerprint=fingerprint, report_file=report_file,
        metrics={
            "engine": ENGINE_VERSION, "start": report["start"], "end": report["end"],
            "xirr": strategy["xirr"], "max_drawdown": strategy["max_drawdown"],
            "benchmark_xirr": benchmark["xirr"], "benchmark_max_drawdown": benchmark["max_drawdown"],
            "trades": strategy["trades"], "costs": strategy["fees"] + strategy["taxes"],
            "full_period_excess": report["full_period_excess"], "windows": report["windows"],
            "cost_scale": 1.0, "execution_lag": 0,
        },
    )
    return record, report


def second_batch() -> list[StockRule]:
    """Turnover kept down: hold while still in the top 3 × N, top 30, monthly and quarterly."""
    rules = []
    for factor, label in FACTORS.items():
        for rebalance, word in (("monthly", "每月"), ("quarterly", "每季")):
            rules.append(StockRule(name=f"個股 {label}：前 30 名、{word}換股、留到跌出前 90", factor=factor, top=30,
                                   rebalance=rebalance, buffer=3))
    return rules



def first_batch() -> list[StockRule]:
    """Six factors × top 10 and top 30, monthly; the two momentum factors also quarterly."""
    rules = []
    for factor, label in FACTORS.items():
        for top in (10, 30):
            rules.append(StockRule(name=f"個股 {label}：前 {top} 名、每月換股", factor=factor, top=top))
    for factor, label in (("momentum_12_1", FACTORS["momentum_12_1"]), ("momentum_6", FACTORS["momentum_6"])):
        rules.append(StockRule(name=f"個股 {label}：前 20 名、每季換股", factor=factor, top=20, rebalance="quarterly"))
    return rules


def describe(report: dict) -> str:
    strategy, benchmark = report["strategy"], report["benchmark"]
    parts = [f"{report['spec']['name']}：{report['start']}～{report['end']} 期末 {strategy['final_value']:,.0f}"
             f"（基準 {benchmark['final_value']:,.0f}），XIRR {strategy['xirr']:.2%}（基準 {benchmark['xirr']:.2%}），"
             f"最大回撤 {strategy['max_drawdown']:.1%}（基準 {benchmark['max_drawdown']:.1%}），交易 {strategy['trades']} 筆、"
             f"費稅 {strategy['fees'] + strategy['taxes']:,} 元，全期超額 {report['full_period_excess']:+.2%}"]
    for key, item in report["windows"].items():
        if item.get("count"):
            parts.append(f"{key} 視窗 {item['count']} 個、勝率 {item['win_ratio']:.0%}、中位 {item['median_excess']:+.2%}、"
                         f"最差 {item['worst_excess']:+.2%}")
    return "；".join(parts)


BATCHES = {"first": first_batch, "second": second_batch}
