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
import math
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
from quant_platform.research.history.dataset import read_series
from quant_platform.research.metrics import unit_values
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
    UNIVERSES,
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
# 1.1.0 (2026-10-05): trailing stop and market filter (R7); the data version covers the chip data and
# only what the period covers (0050 and this year's ex-rights file are rewritten nightly).
# 1.2.0 (2026-10-06): inverse-volatility weights and the account's own trend filter (R7b).
ENGINE_VERSION = "daily-1.2.0"
SHADOW_WARMUP = 252         # sessions before the shadow account starts (the factors need a year)
STOP_COOLDOWN = 20          # sessions a stopped-out stock may not be bought again
PERIOD = "recent"
TECHNICAL = {
    "ma_cross_20_60": "20 日均線高於 60 日均線的幅度（黃金交叉）",
    "rsi_14": "RSI（14 日）",
    "macd_hist": "MACD 柱狀體",
    "kd_k": "KD 的 K 值（9 日）",
    "bollinger_b": "布林通道位置 %B（20 日）",
    "breakout_55": "接近 55 日高點（突破）",
}
from quant_platform.research.chips import CHIP_FACTORS, EVENT_FACTORS, ChipStore
from quant_platform.research.fundamentals import STATEMENT_FACTORS, FundamentalStore

# 2026-10-06: five quarterly statement factors (research/fundamentals.py), dated by the filing deadline
FACTOR_LABELS = {**PRICE_FACTORS, **TECHNICAL, **CHIP_FACTORS, **STATEMENT_FACTORS, **EVENT_FACTORS}
# R15 stage B (2026-10-06): scores from a model trained on the factors above (research/model.py).
MODEL_FACTORS = {"ml_gbm": "機器學習綜合分數（30 個因子、排名標籤、逐年滾動訓練）",
                 "ml_gbm_excess": "機器學習綜合分數（30 個因子、超額報酬標籤、逐年滾動訓練）",
                 "ml_gbm_statements": "機器學習綜合分數（35 個因子含財報、超額報酬標籤、逐年滾動訓練）",
                 "ml_gbm_60": "機器學習綜合分數（35 個因子含財報、之後 60 個交易日超額報酬標籤、逐年滾動訓練）"}
RULE_FACTORS = {**FACTOR_LABELS, **MODEL_FACTORS}
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
    # At most this share of the picks from one industry (2026-10-04: a trend rule bunched into passive
    # components and fell 41% in a month); 0 = no limit. Left out of the hash at 0.
    industry_cap: float = Field(default=0.0, ge=0.0, le=1.0)
    # R6 (2026-10-05): "all" ranks the TPEx stocks too. Left out of the hash when "twse".
    universe: Literal["twse", "all"] = "twse"
    # R7 (2026-10-05), risk controls, left out of the hash when off. stop_loss: sell a holding whose
    # adjusted close falls this far below its highest since it was bought, and do not buy it back for
    # STOP_COOLDOWN sessions. market_filter "taiex_200": no stocks while TAIEX closes below its
    # 200-session average (the 0050 core stays; contributions wait in cash).
    stop_loss: float = Field(default=0.0, ge=0.0, le=0.5)
    market_filter: Literal["none", "taiex_200"] = "none"
    # R7b (2026-10-06), risk controls that look at the holdings themselves, left out of the hash when
    # off. weighting "inverse_vol": each pick's target in proportion to 1 / its 60-session volatility
    # (the calmer, the more money) instead of equal amounts. account_filter "own_200": while this rule's
    # own stock account (the same rule with no core and no filter, the "shadow") is below its
    # 200-session average value, the stock part is held in 0050 instead; back above, the stocks return.
    weighting: Literal["equal", "inverse_vol"] = "equal"
    account_filter: Literal["none", "own_200"] = "none"
    # 2026-10-07 (the owner: 降換手): rank on each factor's average over the last `smooth` sessions
    # instead of the day's value; 0 = the day's value. Left out of the hash when 0.
    smooth: int = Field(default=0, ge=0, le=60)
    # R15 C1b (2026-10-07): "q1" = the learned exit (research/exits.py) decides keep or sell for every
    # holding each session; a sold stock is not bought back for 20 sessions. Left out of the hash when "none".
    exit_model: Literal["none", "q1"] = "none"
    # R15 D (2026-10-09): "v1" = a stock with a bad-news flag in force (research/news_events.py) is not newly
    # bought (holdings are kept); "v1-off" = the same rule computing nothing different, the forward control.
    # Left out of the hash when "none". News exists only from 2026-10-05: forward observation only.
    news_veto: Literal["none", "v1", "v1-off"] = "none"

    @field_validator("factors")
    @classmethod
    def _known(cls, value: dict[str, float]) -> dict[str, float]:
        for name, weight in value.items():
            if name not in RULE_FACTORS:
                raise ValueError(f"未知因子：{name}")
            if not weight or abs(weight) > 3:
                raise ValueError("因子權重要在 -3～3 之間且不為 0")
        return value

    def canonical(self) -> dict[str, object]:
        data = self.model_dump(mode="json")
        if not data.get("industry_cap"):
            data.pop("industry_cap", None)
        if data.get("universe") == "twse":
            data.pop("universe", None)
        if not data.get("stop_loss"):
            data.pop("stop_loss", None)
        if data.get("market_filter") == "none":
            data.pop("market_filter", None)
        if data.get("weighting") == "equal":
            data.pop("weighting", None)
        if data.get("account_filter") == "none":
            data.pop("account_filter", None)
        if not data.get("smooth"):
            data.pop("smooth", None)
        if data.get("exit_model") == "none":
            data.pop("exit_model", None)
        if data.get("news_veto") == "none":
            data.pop("news_veto", None)
        return data

    @property
    def industry_limit(self) -> int | None:
        return max(1, int(self.industry_cap * self.top)) if self.industry_cap else None

    @property
    def rule_hash(self) -> str:
        body = {key: value for key, value in self.canonical().items() if key != "name"}
        body["engine_family"] = "daily"
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()

    @property
    def label(self) -> str:
        parts = []
        for name, weight in self.factors.items():
            text = RULE_FACTORS[name]
            parts.append(("反向：" if weight < 0 else "") + text + (f"×{abs(weight):g}" if abs(weight) != 1 else ""))
        return "＋".join(parts)


# --- factor matrices ---------------------------------------------------------------------------
class FactorPanel:
    """Every factor for every stock and session (symbols × sessions), computed once with rolling
    windows on the dividend- and split-adjusted closes; ranking a day is then a column lookup."""

    def __init__(self, panel: Panel, industries: dict[str, str] | None = None, chips: ChipStore | None = None,
                 market: np.ndarray | None = None, models: Path | None = None) -> None:
        self.panel = panel
        self.models = models                                  # R15-B: the yearly model files (ml_gbm)
        self.industries = industries or {}
        self.chips = chips
        self.sessions, self.index, self.symbols = panel.sessions, panel.index, panel.symbols
        self.row = {symbol: row for row, symbol in enumerate(panel.symbols)}
        self.market = market                                  # TAIEX close per session (R7 market filter)
        self._market_average = (pd.Series(market).rolling(200).mean().to_numpy() if market is not None else None)
        self._prices = pd.DataFrame(panel.filled.T)
        self._cache: dict[str, np.ndarray] = {}
        turnover = pd.DataFrame(panel.turnover.T)
        self.turnover_20 = turnover.rolling(20, min_periods=1).mean().to_numpy().T
        self.turnover_120 = turnover.rolling(120, min_periods=1).mean().to_numpy().T
        self.age = np.arange(len(self.sessions))[None, :] - panel.first[:, None]

    def news_vetoed(self, position: int) -> set[str]:
        """The stocks under a bad-news flag at ``position`` (R15 D); none without the chip store's history."""
        if "__news_veto__" not in self._cache:
            if self.chips is None:
                self._cache["__news_veto__"] = np.zeros((len(self.symbols), len(self.sessions)), dtype=bool)
            else:
                from quant_platform.research.news_events import veto_matrix

                self._cache["__news_veto__"] = veto_matrix(Path(self.chips._folder).parent, self.sessions, self.symbols)
        column = self._cache["__news_veto__"][:, position]
        return {self.symbols[row] for row in np.flatnonzero(column)}

    def matrix(self, factor: str) -> np.ndarray:
        if factor not in self._cache:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                frame = self._compute(factor)
            # computed as sessions × symbols; kept as symbols × sessions so a day is a column
            self._cache[factor] = np.ascontiguousarray(np.asarray(frame, dtype=np.float32).T)
        return self._cache[factor]

    def _compute(self, factor: str):
        if factor in MODEL_FACTORS:
            if self.models is None:
                return np.full((len(self.sessions), len(self.symbols)), np.nan)
            from quant_platform.research.model import MODELS, scores

            return scores(self, Path(self.models) / MODELS[factor]["version"])      # sessions × symbols
        if factor in STATEMENT_FACTORS:
            # the statements sit next to the chip files: the store follows the chip store's history
            if self.chips is None:
                return np.full((len(self.sessions), len(self.symbols)), np.nan)
            store = FundamentalStore(Path(self.chips._folder).parent, self.sessions, self.symbols)
            if not store.available():
                return np.full((len(self.sessions), len(self.symbols)), np.nan)
            return store.matrix(factor).T                           # back to sessions × symbols
        if factor in CHIP_FACTORS or factor in EVENT_FACTORS:
            if self.chips is None or not self.chips.available():
                return np.full((len(self.sessions), len(self.symbols)), np.nan)
            return self.chips.matrix(factor).T                      # back to sessions × symbols
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

    def industry(self, symbol: str) -> str:
        return self.industries.get(symbol.split(".")[0], "未分類")

    def price(self, symbol: str, position: int) -> float:
        """The adjusted close (dividends reinvested): its ratios between two days are the total return."""
        return float(self.panel.filled[self.row[symbol], position])

    def risk_on(self, position: int) -> bool:
        """TAIEX at or above its 200-session average (true until 200 sessions exist)."""
        if self.market is None:
            raise ValueError("市場濾網需要加權指數序列（FactorPanel market）")
        average = self._market_average[position]
        return bool(not np.isfinite(average) or self.market[position] >= average)

    def eligible(self, rule: DailyRule, position: int) -> np.ndarray:
        return ((self.panel.close[:, position] >= rule.min_price) & (self.turnover_20[:, position] >= rule.min_turnover)
                & (self.age[:, position] >= rule.min_history))

    def smoothed(self, factor: str, sessions: int) -> np.ndarray:
        """The factor averaged over the last ``sessions`` sessions (symbols × sessions; days with no value
        are skipped, so a stock with fewer days averages what it has)."""
        if sessions <= 1:
            return self.matrix(factor)
        key = f"{factor}@{sessions}"
        if key not in self._cache:
            frame = pd.DataFrame(self.matrix(factor).T).rolling(sessions, min_periods=1).mean()
            self._cache[key] = np.ascontiguousarray(frame.to_numpy(dtype=np.float32).T)
        return self._cache[key]

    def ranked(self, rule: DailyRule, position: int) -> list[str]:
        mask = self.eligible(rule, position)
        columns = {name: self.smoothed(name, getattr(rule, "smooth", 0))[:, position] for name in rule.factors}
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
    peak: dict[str, float] = {}           # R7: highest adjusted close since bought
    stopped: dict[str, int] = {}          # R7: session count of the last stop-out (or learned exit)
    buy_price: dict[str, float] = {}      # C1b: adjusted close on the buying session
    exit_agent = None
    if getattr(rule, "exit_model", "none") != "none" and getattr(fp, "models", None) is not None:
        from quant_platform.research.exits import EXIT_VERSION, ExitAgent

        exit_agent = ExitAgent(fp, Path(fp.models) / EXIT_VERSION)
    for count, day in enumerate(sessions):
        if day not in checks:
            continue
        position = fp.index[day]
        if rule.market_filter != "none" and not fp.risk_on(position):
            current, bought, peak = [], {}, {}
            output[day] = []
            continue
        ranked = fp.ranked(rule, position)
        if rule.stop_loss:
            for symbol in list(current):
                price = fp.price(symbol, position)
                if not np.isfinite(price):
                    continue
                peak[symbol] = max(peak.get(symbol, price), price)
                if price < peak[symbol] * (1 - rule.stop_loss):
                    current.remove(symbol)
                    stopped[symbol] = count
        if exit_agent is not None and current:
            for symbol in current:
                price = fp.price(symbol, position)
                if np.isfinite(price):
                    peak[symbol] = max(peak.get(symbol, price), price)
            held_for = {symbol: count - bought[symbol] for symbol in current}
            for symbol in exit_agent.sells(position, list(current), held_for, buy_price, peak, ranked):
                current.remove(symbol)
                stopped[symbol] = count
        if stopped:
            barred = {symbol for symbol, when in stopped.items() if count - when < STOP_COOLDOWN}
            ranked = [symbol for symbol in ranked if symbol not in barred]
        keep_zone = set(ranked[: rule.top * rule.keep])
        eligible = set(ranked)
        kept = [symbol for symbol in current
                if symbol in keep_zone or (symbol in eligible and count - bought[symbol] < rule.min_hold)]
        held = set(kept)
        limit = rule.industry_limit
        counts: dict[str, int] = defaultdict(int)
        if limit:
            for symbol in kept:
                counts[fp.industry(symbol)] += 1
        chosen = []
        vetoed = fp.news_vetoed(position) if getattr(rule, "news_veto", "none") == "v1" else set()
        for symbol in ranked:
            if len(kept) + len(chosen) >= rule.top:
                break
            if symbol in held or symbol in vetoed:
                continue
            if limit:
                group = fp.industry(symbol)
                if counts[group] >= limit:
                    continue                      # this industry is full: the next best from another
                counts[group] += 1
            chosen.append(symbol)
        current = kept + chosen
        bought = {symbol: bought.get(symbol, count) for symbol in current}
        if rule.stop_loss or exit_agent is not None:
            peak = {symbol: peak[symbol] if symbol in peak else fp.price(symbol, position) for symbol in current}
        if exit_agent is not None:
            buy_price = {symbol: buy_price.get(symbol, fp.price(symbol, position)) for symbol in current}
        output[day] = list(current)
    return output


# --- simulation --------------------------------------------------------------------------------
def simulate_daily(data: LegacyData, rule: DailyRule | None, costs: CostModel, start: date, end: date,
                   ranks: dict[date, list[str]] | None = None, plan=None,
                   ledger: list[dict[str, object]] | None = None,
                   snapshots: dict | None = None, weights: dict[date, dict[str, float]] | None = None,
                   parked: set[date] | None = None) -> RunResult:
    """``rule=None`` is the benchmark: the same cash flow into 0050 on the day it arrives. ``weights``:
    each pick's share of the stock part on its check day (equal when absent). ``parked``: days the stock
    part is held in 0050 (the stocks are sold and 0050 bought the same close; on the first day after,
    the 0050 above the core is sold and the picks bought)."""
    plan = plan or SeedPlan()
    sessions = [day for day in data.sessions if start <= day <= end]
    contributions = plan.schedule(sessions, start, end)
    by_day = dict(contributions)
    result = RunResult(rule.rule_hash[:12] if rule else "dca_0050", sessions, [], [], contributions)
    cash, units = 0.0, defaultdict(float)
    picks: list[str] = []
    shares_of: dict[str, float] = {}
    was_parked = False
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

    def sell_all(symbol: str, day: date, amount: float | None = None) -> None:
        """Sell the whole holding, or whole shares worth about ``amount``."""
        nonlocal cash
        close = data.closes.get(symbol, {}).get(day)
        if close is None or units[symbol] <= 0:
            return
        shares, fill = units[symbol], fill_price(close, "SELL", costs.slippage_bps, _tick(symbol))
        if amount is not None:
            shares = min(shares, float(math.floor(amount / fill)))
            if shares <= 0:
                return
        amount = shares * fill
        fee, tax = costs.fee(amount), costs.tax(amount, _tax_kind(symbol), "SELL")
        cash += amount - fee - tax
        units[symbol] -= shares
        if units[symbol] < 1e-9:
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
            park = parked is not None and day in parked
            today = [] if park else (ranks.get(day) if ranks else None)
            if today is not None:
                for symbol in [held for held, count in units.items() if count > 0 and held != BENCHMARK and held not in today]:
                    sell_all(symbol, day)
                picks = today
                if not park:
                    shares_of = (weights or {}).get(day) or {}
            if was_parked and not park:            # back from 0050: keep only the core in it
                total = cash + sum(count * price(symbol, day) for symbol, count in units.items() if count > 0)
                excess = units[BENCHMARK] * price(BENCHMARK, day) - total * rule.core
                if excess >= minimum:
                    sell_all(BENCHMARK, day, excess)
            if today is not None or contribution or park != was_parked:
                total = cash + sum(count * price(symbol, day) for symbol, count in units.items() if count > 0)
                targets: dict[str, float] = {}
                if rule.core > 0 or park:
                    targets[BENCHMARK] = total * (1.0 if park else rule.core)
                for symbol in picks:
                    share = shares_of.get(symbol, 1 / len(picks)) if shares_of else 1 / len(picks)
                    targets[symbol] = targets.get(symbol, 0.0) + total * (1 - rule.core) * share
                orders = []
                for symbol, target in targets.items():
                    held_value = units[symbol] * price(symbol, day)
                    gap = target - held_value
                    if gap > 0 and (held_value <= 0 or contribution or (park and symbol == BENCHMARK)):
                        orders.append((gap, symbol))
                for gap, symbol in sorted(orders, reverse=True):
                    amount = min(gap, cash)
                    if amount >= minimum:
                        buy(symbol, amount, day)
        if rule is not None:
            was_parked = parked is not None and day in parked
        value = cash + sum(count * price(symbol, day) for symbol, count in units.items() if count > 0)
        result.values.append(value)
        result.flows.append(contribution)
        if snapshots is not None:
            snapshots[day] = (cash, {symbol: count for symbol, count in units.items() if count > 0})
    return result


def daily_weights(fp: FactorPanel, rule: DailyRule, ranks: dict[date, list[str]]) -> dict[date, dict[str, float]] | None:
    """R7b: each pick's share of the stock part, in proportion to 1 / its 60-session volatility (a pick
    without one takes the median of the others); None for equal amounts."""
    if rule.weighting == "equal":
        return None
    volatility = -fp.matrix("low_volatility_60")             # stored negated: the daily standard deviation
    output: dict[date, dict[str, float]] = {}
    for day, names in ranks.items():
        if not names:
            continue
        position = fp.index[day]
        sigmas = np.array([volatility[fp.row[symbol], position] for symbol in names], dtype=float)
        valid = np.isfinite(sigmas) & (sigmas > 0)
        sigmas = np.where(valid, sigmas, np.median(sigmas[valid]) if valid.any() else 1.0)
        inverse = 1 / sigmas
        output[day] = {symbol: float(share) for symbol, share in zip(names, inverse / inverse.sum(), strict=True)}
    return output


def account_parking(data: LegacyData, fp: FactorPanel, rule: DailyRule, costs: CostModel) -> set[date] | None:
    """R7b: the sessions on which the rule's own stock account (no core, no filter, from SHADOW_WARMUP
    sessions into the data) closes below its 200-session average unit value; None when the filter is off."""
    if rule.account_filter == "none":
        return None
    shadow = rule.model_copy(update={"account_filter": "none", "core": 0.0})
    sessions = fp.sessions
    start, end = sessions[min(SHADOW_WARMUP, len(sessions) - 1)], sessions[-1]
    ranks = daily_rankings(fp, shadow, start, end)
    run = simulate_daily(data, shadow, costs, start, end, ranks, weights=daily_weights(fp, shadow, ranks))
    units = unit_values(run.values, run.flows)
    average = pd.Series(units).rolling(200).mean().to_numpy()
    return {day for day, unit, mean in zip(run.days, units, average, strict=True) if np.isfinite(mean) and unit < mean}


def _excess(run: RunResult, benchmark: RunResult) -> float:
    return (run.final_value - benchmark.final_value) / run.contributed if run.contributed else 0.0


def window_stats(data: LegacyData, rule: DailyRule, costs: CostModel, ranks: dict[date, list[str]], months: int,
                 start: date, end: date, cache: dict, weights: dict | None = None,
                 parked: set[date] | None = None) -> dict[str, object]:
    return account_windows(
        data, lambda first, last: simulate_daily(data, rule, costs, first, last, ranks, weights=weights, parked=parked),
        costs, months, start, end, cache)


def account_windows(data: LegacyData, account, costs: CostModel, months: int, start: date, end: date,
                    cache: dict) -> dict[str, object]:
    """Rolling windows of ``months`` from every month start: ``account(first, last)`` (a fresh account of
    the owner's cash flow) against the same money in 0050."""
    excesses = []
    for first in _month_starts(data.sessions, start, end, months):
        total = first.year * 12 + first.month - 1 + months
        last = date(total // 12, total % 12 + 1, 1) - timedelta(days=1)
        run = account(first, last)
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


TIERS = ("T0", "T0 候選", "T1", "T2", "T3")
TIER_LABELS = {
    "T0": "T0：可採用（全部門檻＋前向觀察確認）",
    "T0 候選": "T0 候選：全部門檻都過，等前向觀察",
    "T1": "T1：贏 0050，但波動較大",
    "T2": "T2：只有部分期間贏",
    "T3": "T3：輸 0050",
}
FORWARD_SESSIONS = 60   # about three months of forward observation before a rule can be T0


def tier(metrics: dict, forward: dict | None = None) -> tuple[str, str]:
    """The pool's grade of a daily rule (owner 2026-10-04): T3 lost to 0050 from 2015-06; T2 won from
    2015-06 but lost from 2020-10 or failed the rolling windows; T1 passed both periods and the windows
    but fell more than 5 points deeper than 0050 (more volatile); T0 候選 passed every gate; T0 is a
    T0 候選 that, after at least 60 sessions of forward observation, is not behind 0050 and reconciles."""
    reasons = gate(metrics)
    if (metrics.get("full_period_excess") or 0) <= 0:
        return "T3", reasons[0] if reasons else "2015-06 起輸 0050"
    partial = [reason for reason in reasons if "2020-10" in reason or "視窗" in reason]
    if partial:
        return "T2", "；".join(partial)
    if reasons:
        return "T1", "；".join(reasons)
    sessions = (forward or {}).get("sessions") or 0
    if (sessions >= FORWARD_SESSIONS and ((forward or {}).get("excess") or 0) >= 0
            and not (forward or {}).get("problems")):
        return "T0", f"前向觀察 {sessions} 個交易日、不輸 0050"
    return "T0 候選", f"前向觀察 {sessions}／{FORWARD_SESSIONS} 個交易日"


def evaluate(data: LegacyData, fp: FactorPanel, rule: DailyRule, costs: CostModel,
             benchmark_cache: dict | None = None) -> dict[str, object]:
    ranks = daily_rankings(fp, rule, RECENT_START, RECENT_END)
    weights, parked = daily_weights(fp, rule, ranks), account_parking(data, fp, rule, costs)
    first = next((day for day in sorted(ranks) if ranks[day]), RECENT_START)

    def account(start: date, end: date) -> RunResult:
        return simulate_daily(data, rule, costs, start, end, ranks, weights=weights, parked=parked)

    report = account_report(data, account, first, costs, benchmark_cache)
    report["parked_sessions"] = len([day for day in parked or () if first <= day <= RECENT_END])
    return {"engine": ENGINE_VERSION, "spec": rule.canonical(), "rule_hash": rule.rule_hash, "label": rule.label, **report}


def account_report(data: LegacyData, account, first: date, costs: CostModel,
                   benchmark_cache: dict | None = None) -> dict[str, object]:
    """What every account is judged by: ``account(start, end)`` runs the owner's cash flow from ``start``.
    From ``first`` and from 2020-10, the rolling windows, the monthly gaps, the activity and the curve,
    each against the same money in 0050."""
    cache = benchmark_cache if benchmark_cache is not None else {}
    run = account(first, RECENT_END)
    benchmark = simulate_daily(data, None, costs, first, RECENT_END)
    regime_run = account(REGIME_START, RECENT_END)
    regime_benchmark = simulate_daily(data, None, costs, REGIME_START, RECENT_END)
    yearly: dict[str, float] = {}
    active = _monthly_active(run, benchmark)
    for month, value in active.items():
        yearly[month[:4]] = yearly.get(month[:4], 0.0) + value
    return {
        "start": first.isoformat(), "end": RECENT_END.isoformat(),
        "strategy": run.summary(), "benchmark": benchmark.summary(),
        "full_period_excess": round(_excess(run, benchmark), 6),
        "since_2020": {"start": REGIME_START.isoformat(), "strategy": regime_run.summary(),
                       "benchmark": regime_benchmark.summary(), "excess": round(_excess(regime_run, regime_benchmark), 6)},
        "windows": {key: account_windows(data, account, costs, months, first, RECENT_END, cache.setdefault(key, {}))
                    for key, months in WINDOWS.items()},
        "monthly_active_returns": active, "yearly": {year: round(value, 4) for year, value in sorted(yearly.items())},
        "activity": activity(run), "curve": curve(run, benchmark),
    }


INDUSTRY_FILE = Path("raw") / "finmind" / "TaiwanStockInfo.json"


def load_industries(history: str | Path) -> dict[str, str]:
    """Code → industry from FinMind's TaiwanStockInfo (today's classification, applied to history;
    industries rarely change). Codes it no longer lists (stocks delisted long ago) take the most common
    industry of listed codes with the same first two digits (TWSE codes group by industry)."""
    path = Path(history) / INDUSTRY_FILE
    if not path.is_file():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8")).get("data") or []
    output: dict[str, str] = {}
    for row in sorted(rows, key=lambda item: str(item.get("date") or "")):     # the latest classification wins,
        code, group = str(row.get("stock_id") or ""), str(row.get("industry_category") or "")
        if not code or group in ("ETF", "Index", "大盤", "所有證券", ""):
            continue
        if group == "電子工業" and output.get(code) not in (None, "電子工業"):
            continue                                                            # but a specific one beats 電子工業
        output[code] = group
    by_prefix: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for code, group in output.items():
        by_prefix[code[:2]][group] += 1
    output["__prefix__"] = json.dumps({prefix: max(counts, key=counts.get) for prefix, counts in by_prefix.items()},
                                      ensure_ascii=False)
    return output


class Industries(dict):
    """The code → industry map with the two-digit fallback for codes it does not list."""

    def __init__(self, mapping: dict[str, str]) -> None:
        prefixes = json.loads(mapping.get("__prefix__", "{}")) if mapping else {}
        super().__init__({code: group for code, group in mapping.items() if code != "__prefix__"})
        self._prefixes = prefixes

    def get(self, code, default=None):
        if code in self:
            return self[code]
        return self._prefixes.get(str(code)[:2], default)


def market_closes(history: str | Path, sessions: list[date]) -> np.ndarray:
    """TAIEX's close on each session (carried over a missing day), for the market filter."""
    closes = {row["date"]: row["close"] for row in read_series(Path(history) / "daily" / "TAIEX.parquet") if row["close"]}
    return pd.Series([closes.get(day, np.nan) for day in sessions], dtype=float).ffill().to_numpy()


def load(history: str | Path, universe: str = "twse") -> tuple[LegacyData, FactorPanel]:
    data = load_stock_data(history, RECENT_START.year - WARMUP_YEARS, RECENT_END.year, universe=universe)
    panel = Panel(data)
    from quant_platform.research.model import models_root

    return data, FactorPanel(panel, Industries(load_industries(history)),
                             ChipStore(history, panel.sessions, panel.symbols, panel.close),
                             market_closes(history, panel.sessions), models_root(history))


def chips_digest(history: str | Path, until: date, universe: str = "twse") -> str:
    """The chip and fundamental data the period can see (published by ``until``) for the universe's
    codes: rebuilding the chip files with other codes or later days leaves it alone."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    base = Path(history)
    codes: set[str] = set()
    for exchange in UNIVERSES[universe]:
        for path in sorted((base / "stocks" / exchange).glob("*.parquet")):
            codes.update(pq.read_table(path, columns=["code"])["code"].unique().to_pylist())
    digest = hashlib.sha256()
    folder = base / "chips"
    for path in sorted(folder.glob("*.parquet")) if folder.is_dir() else []:
        table = pq.read_table(path)
        column = "available" if "available" in table.column_names else "date"
        table = table.filter(pc.and_(pc.less_equal(table[column], pa.scalar(until, pa.date32())),
                                     pc.is_in(table["code"], value_set=pa.array(sorted(codes)))))
        frame = table.sort_by([("date", "ascending"), ("code", "ascending")]).to_pandas()
        digest.update(path.name.encode())
        digest.update(pd.util.hash_pandas_object(frame, index=False).to_numpy().tobytes())
    return digest.hexdigest()


def fingerprint(history: str | Path, universe: str = "twse") -> str:
    stocks = stock_fingerprint(history, RECENT_START.year - WARMUP_YEARS, RECENT_END.year, until=RECENT_END,
                               universe=universe).removeprefix("stocks:")
    chips = chips_digest(history, RECENT_END, universe)
    return "daily:" + hashlib.sha256(f"{stocks}:{chips}".encode()).hexdigest()


def run_trial(rule: DailyRule, history: str | Path, registry: TrialRegistry, reports_dir: Path, costs: CostModel,
              data: LegacyData, fp: FactorPanel, data_fingerprint: str, benchmark_cache: dict | None = None,
              stamp: str | None = None) -> tuple[object, dict]:
    payload = {"rule": rule.canonical(), "period": PERIOD, "costs": costs.as_dict(), "data": data_fingerprint,
               "engine": ENGINE_VERSION, "plan": SEED_PLAN}
    payload.update(input_digests(history, list(rule.factors), rule.exit_model != "none"))
    return record_trial(registry, reports_dir, rule.rule_hash, rule.name, payload,
                        lambda: evaluate(data, fp, rule, costs, benchmark_cache), data_fingerprint, "daily", ENGINE_VERSION,
                        stamp)


SEED_PLAN = {"kind": "SeedPlan", "initial": SeedPlan().initial, "monthly_amount": SeedPlan().monthly_amount,
             "day_of_month": 5}


def input_digests(history: str | Path, factor_names: list[str], uses_exit: bool) -> dict[str, object]:
    """The files besides prices a run reads, as part of its input: statements, the exit models, the
    trained factor models."""
    payload: dict[str, object] = {}
    if any(name in STATEMENT_FACTORS for name in factor_names):  # the statements are part of the input
        from quant_platform.research.fundamentals import digest as statements_digest

        payload["statements"] = statements_digest(history, RECENT_END)
    if uses_exit:                                              # C1b: the exit models are part of the input
        from quant_platform.research.exits import digest as exits_digest
        from quant_platform.research.exits import exits_dir

        payload["exit"] = exits_digest(exits_dir(history))
    if any(name in MODEL_FACTORS for name in factor_names):     # R15-B: the trained models are part of the input
        from quant_platform.research.model import MODELS, digest, model_dir

        used = [name for name in factor_names if name in MODEL_FACTORS]
        if used == ["ml_gbm"]:                   # the form the gbm-1.0.0 trials were registered with
            payload["model"] = {"version": MODELS["ml_gbm"]["version"],
                                "digest": digest(model_dir(history, MODELS["ml_gbm"]["version"]))}
        else:
            payload["model"] = {name: {"version": MODELS[name]["version"],
                                       "digest": digest(model_dir(history, MODELS[name]["version"]))} for name in used}
    return payload


def record_trial(registry: TrialRegistry, reports_dir: Path, spec_hash: str, spec_name: str, payload: dict,
                 build_report, data_fingerprint: str, family: str, engine: str,
                 stamp: str | None = None) -> tuple[object, dict]:
    """Register one run on the selection period: the same input (``payload``) is looked up, not rerun;
    otherwise ``build_report()`` runs it, the report is saved and the metrics registered with the gate."""
    input_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    existing = registry.find("candidate", PERIOD, input_hash)
    if existing is not None:
        return existing, json.loads((reports_dir / existing.report_file).read_text(encoding="utf-8"))
    plan = payload["plan"]
    report = build_report()
    report["data_fingerprint"] = data_fingerprint
    report["plan"] = plan
    if "model" in payload:
        report["model"] = payload["model"]
    stamp = stamp or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    reports_dir.mkdir(parents=True, exist_ok=True)
    report_file = f"{PERIOD}-{family}-{spec_hash[:8]}-{stamp}.json"
    (reports_dir / report_file).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    strategy, benchmark = report["strategy"], report["benchmark"]
    metrics = {
        "engine": engine, "start": report["start"], "end": report["end"], "plan": plan,
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
    record = registry.register(kind="candidate", period=PERIOD, spec_hash=spec_hash, spec_name=spec_name,
                               input_hash=input_hash, data_fingerprint=data_fingerprint, report_file=report_file,
                               metrics=metrics)
    return record, report


# --- batches -----------------------------------------------------------------------------------
OSCILLATORS = ("rsi_14", "kd_k", "bollinger_b")
FIRST_FACTORS = tuple(PRICE_FACTORS) + tuple(TECHNICAL)


def factor_batch() -> list[DailyRule]:
    """One rule per factor (and the oscillators reversed: buy the oversold), alone and with half the
    account kept in 0050: different factors, not parameter variants (the owner, 2026-10-04)."""
    # the 19 price, trading and technical factors this batch was run with (the chip factors have their own)
    singles: list[tuple[str, dict[str, float]]] = [(FACTOR_LABELS[name], {name: 1.0}) for name in FIRST_FACTORS]
    singles += [(f"反向：{FACTOR_LABELS[name]}（買超賣）", {name: -1.0}) for name in OSCILLATORS]
    rules = []
    for label, factors in singles:
        for core, word in ((0.0, ""), (0.5, "、一半放 0050")):
            rules.append(DailyRule(name=f"每天 {label}：前 20 名{word}"[:80], factors=factors, core=core))
    return rules


def risk_batch() -> list[DailyRule]:
    """2026-10-04, the owner: add an industry cap to bring the drawdown into the gate. The two strongest
    trend factors and trend mixed with liquidity (large, actively traded stocks), each with at most 30%
    of the picks from one industry, alone and with half the account in 0050."""
    families = [
        ("站上 200 日均線的幅度", {"trend_200": 1.0}),
        ("接近 52 週高點", {"high_52w": 1.0}),
        ("站上 200 日均線＋成交值大", {"trend_200": 1.0, "liquidity": 1.0}),
    ]
    rules = []
    for label, factors in families:
        for core, word in ((0.0, ""), (0.5, "、一半放 0050")):
            rules.append(DailyRule(name=f"每天 {label}：前 20 名、同產業最多 3 成{word}"[:80], factors=factors,
                                   core=core, industry_cap=0.3))
    # the 30% cap with half in 0050 missed the drawdown gate by a fraction of a point: a 20% cap
    for core, word in ((0.0, ""), (0.5, "、一半放 0050")):
        rules.append(DailyRule(name=f"每天 站上 200 日均線的幅度：前 20 名、同產業最多 2 成{word}", factors={"trend_200": 1.0},
                               core=core, industry_cap=0.2))
    return rules


def chip_batch() -> list[DailyRule]:
    """R13 (2026-10-04): one rule per chip and fundamental factor, and the two whose opposite is the
    better-known story reversed (margin falling = retail leaving; small caps), alone and half in 0050."""
    singles: list[tuple[str, dict[str, float]]] = [(CHIP_FACTORS[name], {name: 1.0}) for name in CHIP_FACTORS]
    singles += [("反向：融資餘額減少（20 日）", {"margin_growth_20": -1.0}), ("反向：市值小（小型股）", {"market_cap": -1.0})]
    rules = []
    for label, factors in singles:
        for core, word in ((0.0, ""), (0.5, "、一半放 0050")):
            rules.append(DailyRule(name=f"每天 {label}：前 20 名{word}"[:80], factors=factors, core=core))
    return rules


def combo_batch() -> list[DailyRule]:
    """2026-10-04: alone the chip and fundamental factors lose to 0050 (their daily changes churn the
    top 20), so they confirm the strongest trend factor instead: trend plus revenue growth, plus
    investment-trust buying, plus foreign buying; industry cap 30%, alone and half in 0050."""
    families = [
        ("站上 200 日均線＋月營收年增", {"trend_200": 1.0, "revenue_yoy": 1.0}),
        ("站上 200 日均線＋投信買超", {"trend_200": 1.0, "trust_buy_20": 1.0}),
        ("站上 200 日均線＋外資買超", {"trend_200": 1.0, "foreign_buy_20": 1.0}),
    ]
    rules = []
    for label, factors in families:
        for core, word in ((0.0, ""), (0.5, "、一半放 0050")):
            rules.append(DailyRule(name=f"每天 {label}：前 20 名、同產業最多 3 成{word}"[:80], factors=factors,
                                   core=core, industry_cap=0.3))
    return rules


def tpex_batch() -> list[DailyRule]:
    """R6 (2026-10-05): the risk batch's three trend families (30% industry cap, alone and half in 0050)
    on listed plus TPEx stocks: the same rules on a wider universe, to see whether the TPEx small and
    mid caps add return or only volatility."""
    rules = []
    for rule in risk_batch()[:6]:
        name = rule.name.replace("每天 ", "每天（上市＋上櫃）", 1)[:80]
        rules.append(rule.model_copy(update={"name": name, "universe": "all"}))
    return rules


def overlay_batch() -> list[DailyRule]:
    """R7 (2026-10-05): two risk controls on the strongest rule so far (trend, top 20, 30% industry cap;
    its drawdown was 45%, and 39% with half in 0050): a 15% trailing stop, the TAIEX 200-day filter, and
    both; alone and half in 0050. New mechanisms with settings fixed in advance, not a parameter search."""
    variants = [("跌離買進後高點 15% 停損", {"stop_loss": 0.15}),
                ("加權指數跌破 200 日均線就空手", {"market_filter": "taiex_200"}),
                ("停損＋指數濾網", {"stop_loss": 0.15, "market_filter": "taiex_200"})]
    rules = []
    for label, extra in variants:
        for core, word in ((0.0, ""), (0.5, "、一半放 0050")):
            rules.append(DailyRule(name=f"每天 站上 200 日均線：前 20 名、同產業最多 3 成、{label}{word}",
                                   factors={"trend_200": 1.0}, industry_cap=0.3, core=core, **extra))
    return rules


def holdings_batch() -> list[DailyRule]:
    """R7b (2026-10-06, the owner: 繼續研究): the deepest drawdowns of the trend rule were its own
    holdings crashing while TAIEX did not, so two controls that look at the holdings: weights by inverse
    volatility, and the account's own trend (stock part to 0050 while the rule's own account is below
    its 200-day average), and both; alone and half in 0050. Settings fixed in advance."""
    variants = [("依波動度配置", {"weighting": "inverse_vol"}),
                ("帳戶跌破自己的 200 日均線就換 0050", {"account_filter": "own_200"}),
                ("依波動度配置＋帳戶濾網", {"weighting": "inverse_vol", "account_filter": "own_200"})]
    rules = []
    for label, extra in variants:
        for core, word in ((0.0, ""), (0.5, "、一半放 0050")):
            rules.append(DailyRule(name=f"每天 站上 200 日均線：前 20 名、同產業最多 3 成、{label}{word}",
                                   factors={"trend_200": 1.0}, industry_cap=0.3, core=core, **extra))
    return rules


def model_batch() -> list[DailyRule]:
    """R15 stage B (2026-10-06): hold the top 20 by the walk-forward model's score, with the 30% industry
    cap, alone and half in 0050, equal and inverse-volatility amounts (the one sizing that helped)."""
    rules = []
    for weighting, words in (("equal", ""), ("inverse_vol", "、依波動度配置")):
        for core, word in ((0.0, ""), (0.5, "、一半放 0050")):
            rules.append(DailyRule(name=f"每天 機器學習綜合分數：前 20 名、同產業最多 3 成{words}{word}",
                                   factors={"ml_gbm": 1.0}, industry_cap=0.3, core=core, weighting=weighting))
    return rules


def model_excess_batch() -> list[DailyRule]:
    """R15 stage B, gbm-1.1.0 (2026-10-06): the excess-return model's top 20, industry cap 30%, alone and
    half in 0050 (equal amounts: inverse volatility changed little for the first model)."""
    return [DailyRule(name=f"每天 機器學習（超額報酬標籤）：前 20 名、同產業最多 3 成{word}",
                      factors={"ml_gbm_excess": 1.0}, industry_cap=0.3, core=core)
            for core, word in ((0.0, ""), (0.5, "、一半放 0050"))]


def statement_batch() -> list[DailyRule]:
    """2026-10-06: one rule per statement factor, alone and half in 0050 (like the chip factors), and the
    statement-aware model's top 20 (gbm-1.2.0), industry cap 30%, alone and half in 0050."""
    rules = []
    for name, label in STATEMENT_FACTORS.items():
        for core, word in ((0.0, ""), (0.5, "、一半放 0050")):
            rules.append(DailyRule(name=f"每天 {label}：前 20 名{word}"[:80], factors={name: 1.0}, core=core))
    for core, word in ((0.0, ""), (0.5, "、一半放 0050")):
        rules.append(DailyRule(name=f"每天 機器學習（含財報）：前 20 名、同產業最多 3 成{word}",
                               factors={"ml_gbm_statements": 1.0}, industry_cap=0.3, core=core))
    return rules


def turnover_batch() -> list[DailyRule]:
    """2026-10-07 (the owner: 都要): the T0 candidate trades about 37 times a month; two ways to trade less,
    fixed in advance — rank on the model score's 5-session average (still deciding daily), or decide once a
    week — each alone and half in 0050."""
    rules = []
    for label, extra in (("分數取近 5 日平均", {"smooth": 5}), ("每週決策", {"check": "weekly"})):
        for core, word in ((0.0, ""), (0.5, "、一半放 0050")):
            rules.append(DailyRule(name=f"機器學習（含財報）：前 20 名、同產業最多 3 成、{label}{word}",
                                   factors={"ml_gbm_statements": 1.0}, industry_cap=0.3, core=core, **extra))
    return rules


def exits_batch() -> list[DailyRule]:
    """R15 C1b (2026-10-07): the best trend rule with the learned exit, alone and half in 0050."""
    from quant_platform.research.exits import base_rule as exit_base

    rules = []
    for core, word in ((0.0, ""), (0.5, "、一半放 0050")):
        rules.append(exit_base().model_copy(update={
            "name": f"每天 站上 200 日均線：前 20 名、同產業最多 3 成、依波動度配置、學習出場{word}",
            "core": core, "exit_model": "q1"}))
    return rules


def model_60_batch() -> list[DailyRule]:
    """2026-10-09: the 60-session model (gbm-1.3.0), weekly, top 20, at most 3 in 10 per industry, alone and
    half in 0050 — the same settings as the weekly 20-session model rule, so only the label differs."""
    return [DailyRule(name=f"機器學習（含財報、預測 60 日）：前 20 名、同產業最多 3 成、每週決策{word}",
                      factors={"ml_gbm_60": 1.0}, industry_cap=0.3, check="weekly", core=core)
            for core, word in ((0.0, ""), (0.5, "、一半放 0050"))]


def t0_hunt_batch() -> list:
    """2026-10-09: the revenue event factor and three more blends (research/blend.py ``t0_hunt_batch``)."""
    from quant_platform.research.blend import t0_hunt_batch as hunt

    return hunt()


def blends_batch() -> list:
    """2026-10-09: accounts split across strategy families (research/blend.py)."""
    from quant_platform.research.blend import blend_batch

    return blend_batch()


BATCHES = {"factors": factor_batch, "risk": risk_batch, "chips": chip_batch, "combos": combo_batch,
           "tpex": tpex_batch, "overlays": overlay_batch, "holdings": holdings_batch, "model": model_batch,
           "model-excess": model_excess_batch, "statements": statement_batch, "turnover": turnover_batch,
           "exits": exits_batch, "blends": blends_batch, "model-60": model_60_batch,
           "t0hunt": t0_hunt_batch}
