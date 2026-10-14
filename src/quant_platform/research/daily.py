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
been held ``min_hold`` sessions (``hold="trend"``: at once unless it still closes above its 20-session
average), and buys the names that entered, at the close (the after-hours odd-lot
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
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from quant_platform.research.actions import PAR, PAY_LAG, health_premium, whole_shares
from quant_platform.research.costs import (  # noqa: F401 - ODD_LOT_FEE_FROM is read through this module too
    ODD_LOT_FEE_FROM, ROUND_LOT_MINIMUM_FEE, CostModel, affordable_shares, fill_price,
)
from quant_platform.research.costs import order_fee as _order_fee
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
# 1.3.0 (2026-10-13, 使用者：請符合市場交易規則): a stock closing at its limit-up price cannot be bought and one
# closing at its limit-down price cannot be sold (the queue on the locked side does not clear in the after-hours
# odd-lot auction); the limits are the exchange's — the reference price (the previous close, less the
# ex-rights adjustment) ± 10% (± 7% before 2015-06-01) rounded to the tick inward. When the market refuses an
# order (a locked limit or a session without a trade) the rule decides again at the next session — it ranks
# anew, as on a check day (使用者：被擋下隔天就要重新評估；漲停跌停買不到也賣不掉).
# 1.3.1 (2026-10-13; 使用者：沒賣掉的股票下市就是馬桶紙): a holding not sold before its stock is delisted is
# worth nothing — on the delisting date of the exchange's list (FinMind TaiwanStockDelisting) when the stock
# has not traded since, or IDLE_LIMIT sessions after its last trade when it never trades again and is not on
# the list; it leaves the list that day. A suspension that ends stays held and unsellable until the stock
# trades again. 1.3.0 kept a delisted stock forever as "cannot be sold", and with inverse-volatility weights
# its flat price (a volatility of about 0) drew almost the whole account onto an order that could never fill,
# so a few rules sat in cash for years: a volatility below VOL_FLOOR is now unknown (the median of the
# others). A refused order on a session without a trade is in the ledger.
# 1.4.0 (2026-10-13; 使用者：確實檢查所有交易都照台股的交易規則和機制): whole shares only — a cash dividend is
# cash on its payment date (less the health-insurance premium), a stock dividend whole new shares with the
# fraction lost (many companies keep a book-entry holder's fractions as the transfer fee), a cash capital
# increase is not subscribed (research/actions.py); a split or capital change outside the ex-rights tables
# gives whole shares, the fraction lost. The fee is charged per order part — the round lots (minimum NT$20)
# and the odd lot (the broker's odd-lot minimum, NT$20 before ODD_LOT_FEE_FROM). Execution (DailyRule
# ``execution``): "same" fills at the day's after-hours sessions (14:30; the fixed-price session is round lots
# at the close, the odd-lot auction one price near it), an order at most SAME_DAY_PARTICIPATION of the stock's
# turnover that day (both after-hours sessions together trade about 0.2–0.5% of the regular session); "next"
# — what the platform can do, its numbers coming after 15:14 — decides on the day's close and fills in the
# next session's closing auction (4–9% of the day's value), at most NEXT_DAY_PARTICIPATION. The rest is ordered
# again at the session after.
ENGINE_VERSION = "daily-1.4.0"
SAME_DAY_PARTICIPATION = 0.001              # an order's share of the stock's turnover that session, at most
NEXT_DAY_PARTICIPATION = 0.005
PRICE_LIMIT_WIDENED = date(2015, 6, 1)
IDLE_LIMIT = 20             # sessions after the last trade: a stock that never trades again and is not listed
VOL_FLOOR = 1e-4            # a daily standard deviation below this is a price that did not move: unknown
TREND_WINDOW = 20           # hold "trend": a holding outside the keep zone stays while above this average
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
# 2026-10-10 (Taiwan-quant review, reports/台股量化 開源專案與實證.md): three trend-quality measures over the
# 12 months before the last one (sessions -251..-21): how continuous the move was (Da, Gurun & Warachka
# 2014), the part earned inside the trading day (Taiwan: intraday momentum persists, overnight reverses),
# and the part not shared with the stock's industry (residual momentum, Blitz et al. 2011).
TREND_QUALITY = {
    "fip_12": "趨勢連續性（12 個月扣最近 1 個月：上漲日比例減下跌日比例，依方向）",
    "imom_12": "日內動能（12 個月扣最近 1 個月：開盤到收盤的累積報酬）",
    "resid_mom_12": "產業殘差動能（12 個月扣最近 1 個月：扣掉同產業平均後的累積報酬÷其標準差）",
}
QUALITY_WINDOW, QUALITY_SKIP = 231, 21
# 2026-10-13 (全面研究「之後再試」第 3 項): five new price-and-volume factors from the review — how often the
# stock set a 52-week high lately, where the close sits in its 60-session range, calmness after the market's
# share (idiosyncratic), price-volume correlation (low is better in A-share research) and the overnight part
# of the last year's move reversed (Taiwan: overnight momentum reverses). Not model features (a later model
# version would add them on purpose).
RAW_V2 = {
    "high_52w_count_60": "近 60 日創 52 週新高的次數",
    "range_position_60": "收盤在 60 日高低區間的位置",
    "low_ivol_60": "特質波動低（60 日，扣掉大盤）",
    "low_pv_corr_20": "量價相關低（20 日報酬與成交值變化的相關，反向）",
    "overnight_reversal_12": "隔夜反轉（12 個月扣最近 1 個月：開盤跳空的累積報酬，反向）",
}
# 2026-10-14 (使用者：因子裡面有考慮到時間的部分嗎？): the calendar and event-time family, never tested before —
# a stock's own same-calendar-month seasonality (Heston & Sadka 2008), the run-up to an announced ex-dividend date,
# and the investment trusts' quarter-end window dressing. Each uses only what was known at the session: earlier
# years, announcements made before it (the next session, as most come after the close), yesterday's chip data and
# the exchange's published calendar. Not model features.
TIME_FACTORS = {
    "season_month": "同月份季節性（這檔股票以前每年同一個月的平均報酬，至少 2 年）",
    "dividend_ahead": "即將除息（已公告、20 個交易日內除息的現金股利殖利率）",
    "trust_quarter_end": "投信季底作帳（季底前 20 個交易日的投信買超；季初 10 個交易日反向）",
}
DIVIDEND_AHEAD = 20                         # sessions before an announced ex-dividend date
QUARTER_RUN_UP, QUARTER_AFTER = 28, 14      # calendar days: about 20 sessions before a quarter's end, 10 after


def quarter_weights(sessions) -> np.ndarray:
    """trust_quarter_end's sign per session, from the calendar alone: +1 in the last QUARTER_RUN_UP days of March,
    June, September and December, -1 in the first QUARTER_AFTER days of the next month, 0 otherwise."""
    import calendar

    weights = np.zeros(len(sessions))
    for index, day in enumerate(sessions):
        if day.month in (3, 6, 9, 12):
            last = date(day.year, day.month, calendar.monthrange(day.year, day.month)[1])
            if (last - day).days < QUARTER_RUN_UP:
                weights[index] = 1.0
        elif day.month in (1, 4, 7, 10) and day.day <= QUARTER_AFTER:
            weights[index] = -1.0
    return weights
FACTOR_LABELS = {**PRICE_FACTORS, **TECHNICAL, **CHIP_FACTORS, **STATEMENT_FACTORS, **EVENT_FACTORS, **TREND_QUALITY,
                 **RAW_V2, **TIME_FACTORS}
# R15 stage B (2026-10-06): scores from a model trained on the factors above (research/model.py).
MODEL_FACTORS = {"ml_gbm": "機器學習綜合分數（30 個因子、排名標籤、逐年滾動訓練）",
                 "ml_gbm_excess": "機器學習綜合分數（30 個因子、超額報酬標籤、逐年滾動訓練）",
                 "ml_gbm_statements": "機器學習綜合分數（35 個因子含財報、超額報酬標籤、逐年滾動訓練）",
                 "ml_gbm_60": "機器學習綜合分數（35 個因子含財報、之後 60 個交易日超額報酬標籤、逐年滾動訓練）",
                 "ml_gbm_quality": "機器學習綜合分數（36 個因子含財報與趨勢品質、不含月營收、超額報酬標籤、逐年滾動訓練）",
                 "ml_gbm_ens": "機器學習集成（多種子提升樹、前五分之一分類器、脊迴歸、60 日標籤，36 個因子、逐年滾動訓練）",
                 "ml_tech_ens": "技術模型（同樣的集成，只用 20 個價量技術因子、逐年滾動訓練）"}
# 2026-10-10 (使用者：純技術分析的 RL 機器人): research/tech_rl.py — sees only price and volume.
TECH_FACTORS = {"rl_tech": "技術分析 RL 機器人：沒持有時想進場的機率（只看價格與成交量）",
                "rl_tech_weekly": "技術分析 RL 機器人（每週決策版）：沒持有時想進場的機率（只看價格與成交量）"}
TECH_FACTOR_VERSION = {"rl_tech": "tech-rl-1.0.0", "rl_tech_weekly": "tech-rl-1.1.0"}
TECH_EXITS = {"tech1": "rl_tech", "tech2": "rl_tech_weekly"}          # exit_model -> the robot's score
# 2026-10-11: every T0 candidate votes (research/consensus.py; members frozen per version)
CONSENSUS_FACTORS = {"t0_consensus": "T0 共識分數（25 個 T0 候選各自排名的百分位平均，一個策略一票）",
                     "dev_consensus": "開發期共識分數（180 個搜尋最後名單、只用 2015-06～2020-09 挑出，百分位平均）"}
# 2026-10-14 (使用者：因子裡面有考慮到時間的部分嗎？): which factors to use, decided by their realised record over the
# last year (research/factor_timing.py) — factor momentum. Built from the rule factors; not itself a model feature.
META_FACTORS = {"factor_momentum": "因子動能（最近一年已實現表現最好的 5 個因子的平均百分位，每天重選）",
                # the technical robot's second design (research/rl_timing.py) and its two yardsticks
                "rl_timing": "技術 RL 機器人 2.0（每 20 個交易日決定 20 個技術因子的權重，永遠滿倉）",
                "tech_equal": "20 個技術因子平均（機器人 2.0 的對照）",
                "tech_momentum": "技術因子動能（20 個技術因子中最近一年最強的 5 個；機器人 2.0 的對照）"}
RULE_FACTORS = {**FACTOR_LABELS, **MODEL_FACTORS, **TECH_FACTORS, **CONSENSUS_FACTORS, **META_FACTORS}
WINDOWS = {"1y": 12, "3y": 36}


class DailyRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=80)
    factors: dict[str, float] = Field(min_length=1, max_length=4)   # factor -> weight; negative reverses
    top: int = Field(default=20, ge=3, le=50)
    check: Literal["daily", "weekly", "monthly", "revenue"] = "daily"    # revenue: after the 10th (2026-10-10)
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
    exit_model: Literal["none", "q1", "tech1", "tech2"] = "none"   # tech1/tech2 (2026-10-10): the technical robot sells
    # R15 D (2026-10-09): "v1" = a stock with a bad-news flag in force (research/news_events.py) is not newly
    # bought (holdings are kept); "v1-off" = the same rule computing nothing different, the forward control.
    # Left out of the hash when "none". News exists only from 2026-10-05: forward observation only.
    news_veto: Literal["none", "v1", "v1-off"] = "none"
    # 2026-10-09 (使用者：好的策略應該要能夠避免回撤、知道什麼時候要賣): "v1" = the stock part is scaled by
    # the rule's own recent volatility against its usual level, the rest held in cash (never 0050) —
    # momentum crashes come after its volatility has jumped (Barroso & Santa-Clara 2015; Daniel & Moskowitz
    # 2016). Left out of the hash when "none". See ``exposure_schedule``.
    vol_scale: Literal["none", "v1"] = "none"
    # 2026-10-09 (使用者：只在大型股裡挑，但不要照 0050 的比例買台積電): only the ``large_caps`` largest listed
    # stocks by market value (the previous close times shares issued, chips.py ``market_cap``) can be picked;
    # the picks still get equal amounts, so no stock is held at its market-value share. 0 = no limit, left
    # out of the hash.
    large_caps: int = Field(default=0, ge=0, le=300)
    # 2026-10-10: "v1" = no new buys of lottery-like stocks — the stocks in the top fifth by 60-session
    # idiosyncratic volatility (against TAIEX) or the top tenth by the largest one-day gain in 21
    # sessions; holdings are kept by the usual rules. Left out of the hash when "none".
    exclude: Literal["none", "v1"] = "none"
    # 2026-10-12 (全面研究「之後再試」第 1 項): "cost1" = a holding that leaves the keep zone is replaced only when
    # the replacement's expected next-20-session excess beats the holding's by more than a round trip (about
    # 0.99%: fee both ways, the sell tax, slippage both ways). The expectation is the rule's own score
    # percentile mapped to the average next-20-session excess over the eligible median, estimated on the
    # development period only (2015-06..2020-09; ``switch_mapping``). Left out of the hash when "none".
    switching: Literal["none", "cost1"] = "none"
    # 2026-10-13 (使用者：超過 20 天才賣是策略，不是限制；要看它在漲還是在跌，還在漲就留著): "trend" = a holding
    # that leaves the keep zone is kept while its adjusted close is above its 20-session average (月線) and it
    # is still eligible, and sold at that close once it is not — no fixed minimum holding (min_hold is 0).
    # Left out of the hash when "days" (the fixed min_hold).
    hold: Literal["days", "trend"] = "days"
    # 2026-10-13 (engine 1.4.0; 使用者：所有交易都要照台股的機制): "same" = decide on the close and fill in the
    # same day's after-hours sessions (14:30); "next" = fill in the next session's closing auction — the
    # platform's numbers come after 15:14, so this is what it can actually do. Left out of the hash when "same".
    execution: Literal["same", "next"] = "same"

    @model_validator(mode="before")
    @classmethod
    def _trend_has_no_minimum(cls, data):
        if isinstance(data, dict) and data.get("hold") == "trend":
            data = {**data, "min_hold": 0}
        return data

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
        if data.get("vol_scale") == "none":
            data.pop("vol_scale", None)
        if not data.get("large_caps"):
            data.pop("large_caps", None)
        if data.get("exclude") == "none":
            data.pop("exclude", None)
        if data.get("switching") == "none":
            data.pop("switching", None)
        if data.get("hold") == "days":
            data.pop("hold", None)
        if data.get("execution") == "same":
            data.pop("execution", None)
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
        notes = getattr(panel, "notes", None) or {}
        self.delisted = notes.get("delisted") or {}           # 1.3.1: an unsold delisted holding is worthless
        self.official_limits = notes.get("limits") or {}      # 1.4.0: the exchange's limits on ex-rights days
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
        if factor in META_FACTORS:
            from quant_platform.research.factor_timing import scores as timing_scores
            from quant_platform.research.rl_timing import VERSION as ROBOT_VERSION
            from quant_platform.research.rl_timing import factor_names
            from quant_platform.research.rl_timing import scores as robot_scores

            if factor == "factor_momentum":
                return timing_scores(self)                                             # sessions × symbols
            if factor == "tech_momentum":
                return timing_scores(self, factor_names())
            folder = Path(self.models) / ROBOT_VERSION if self.models is not None else Path("missing")
            return robot_scores(self, folder, "equal" if factor == "tech_equal" else "robot")
        if factor in CONSENSUS_FACTORS:
            from quant_platform.research.consensus import scores as consensus_scores

            return consensus_scores(self, factor)                                      # sessions × symbols
        if factor in TECH_FACTORS:
            if self.models is None:
                return np.full((len(self.sessions), len(self.symbols)), np.nan)
            from quant_platform.research.tech_rl import scores as tech_scores

            return tech_scores(self, Path(self.models) / TECH_FACTOR_VERSION[factor])   # sessions × symbols
        if factor in MODEL_FACTORS:
            if self.models is None:
                return np.full((len(self.sessions), len(self.symbols)), np.nan)
            from quant_platform.research.model import MODELS, scores

            return scores(self, Path(self.models) / MODELS[factor]["version"])      # sessions × symbols
        if factor in STATEMENT_FACTORS:
            # the statements sit next to the chip files: the store follows the chip store's history
            if self.chips is None:
                return np.full((len(self.sessions), len(self.symbols)), np.nan)
            store = FundamentalStore(Path(self.chips._folder).parent, self.sessions, self.symbols,
                                     until=getattr(self.chips, "until", None))
            if not store.available():
                return np.full((len(self.sessions), len(self.symbols)), np.nan)
            return store.matrix(factor).T                           # back to sessions × symbols
        if factor in CHIP_FACTORS or factor in EVENT_FACTORS:
            if self.chips is None or not self.chips.available():
                return np.full((len(self.sessions), len(self.symbols)), np.nan)
            return self.chips.matrix(factor).T                      # back to sessions × symbols
        if factor in TIME_FACTORS:
            return self._time_factor(factor)
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
        if factor in TREND_QUALITY:
            return self._trend_quality(factor)
        if factor in RAW_V2:
            return self._raw_v2(factor)
        raise ValueError(factor)

    def _time_factor(self, factor: str) -> np.ndarray:
        """sessions × symbols; TIME_FACTORS, each from what was known at the session."""
        import bisect

        sessions = list(self.sessions)
        traded = np.isfinite(self.panel.close.T)                       # sessions × symbols
        if factor == "season_month":
            months = pd.PeriodIndex(sessions, freq="M")
            ends = self._prices.groupby(np.asarray(months)).last()      # month × symbols, adjusted
            monthly = ends.pct_change(fill_method=None)
            calendar = np.array([period.month for period in ends.index])
            seasonal = pd.DataFrame(np.nan, index=monthly.index, columns=monthly.columns)
            for month in range(1, 13):
                rows = monthly[calendar == month]
                seasonal.loc[rows.index] = rows.expanding(min_periods=2).mean().shift(1).to_numpy()   # earlier years
            return seasonal.reindex(np.asarray(months)).to_numpy()
        if factor == "dividend_ahead":
            values = np.where(traded, 0.0, np.nan)
            plans = (getattr(self.panel, "notes", None) or {}).get("dividend_plans") or {}
            for symbol, items in plans.items():
                row = self.row.get(symbol)
                if row is None:
                    continue
                for announced, ex_day, cash in items:
                    known = bisect.bisect_right(sessions, announced)    # the session after the announcement
                    ex_at = bisect.bisect_left(sessions, ex_day)
                    for position in range(max(known, ex_at - DIVIDEND_AHEAD), min(ex_at, len(sessions))):
                        close = self.panel.close[row, position]
                        if np.isfinite(close) and close > 0:
                            values[position, row] = cash / close
            return values
        # trust_quarter_end: yesterday's 20-session trust buying, + before a quarter's last session, - just after
        if self.chips is None or not self.chips.available():
            return np.full((len(sessions), len(self.symbols)), np.nan)
        trust = self.chips.matrix("trust_buy_20").T
        return trust * quarter_weights(sessions)[:, None]

    def _raw_v2(self, factor: str) -> np.ndarray:
        """sessions × symbols; RAW_V2, each from data up to the session."""
        p = self._prices
        if factor == "high_52w_count_60":
            at_high = (p >= p.rolling(252, min_periods=200).max()).astype(float).where(p.notna())
            return at_high.rolling(60, min_periods=40).sum().to_numpy()
        if factor == "range_position_60":
            low, high = p.rolling(60, min_periods=40).min(), p.rolling(60, min_periods=40).max()
            return ((p - low) / (high - low).where(high > low)).to_numpy()
        returns = p.pct_change(fill_method=None)
        if factor == "low_ivol_60":
            self.excluded(0)                                         # fills the idiosyncratic volatility cache
            return -self._cache["__ivol__"].T.astype(np.float64)
        if factor == "low_pv_corr_20":
            flow = pd.DataFrame(np.log(np.maximum(self.panel.turnover.T, 1.0))).diff()
            return (-returns.rolling(20, min_periods=15).corr(flow)).to_numpy()
        # overnight_reversal_12: minus the sum of ln(open / previous close) over sessions -251..-21, both
        # dividend-adjusted (an ex-rights open is not a gap)
        with np.errstate(divide="ignore", invalid="ignore"):
            adjusted_open = self.panel.open * (self.panel.adjusted / self.panel.close)
            previous = np.roll(self.panel.filled, 1, axis=1)
            previous[:, 0] = np.nan
            gaps = pd.DataFrame(np.log(adjusted_open / previous).T)
        moved = gaps.rolling(QUALITY_WINDOW, min_periods=QUALITY_WINDOW // 2).sum().shift(QUALITY_SKIP)
        return (-moved).to_numpy()

    def _trend_quality(self, factor: str) -> np.ndarray:
        """sessions × symbols; every value uses sessions -251..-21 only (TREND_QUALITY)."""
        p = self._prices
        returns = p.pct_change(fill_method=None)
        window, skip = QUALITY_WINDOW, QUALITY_SKIP
        if factor == "fip_12":
            up = (returns > 0).astype(float).where(returns.notna())
            down = (returns < 0).astype(float).where(returns.notna())
            balance = (up.rolling(window, min_periods=window // 2).mean()
                       - down.rolling(window, min_periods=window // 2).mean())
            direction = np.sign(p / p.shift(window) - 1)
            return (direction * balance).shift(skip).to_numpy()
        if factor == "imom_12":
            with np.errstate(divide="ignore", invalid="ignore"):
                intraday = pd.DataFrame(np.log(self.panel.close / self.panel.open).T)
            return intraday.rolling(window, min_periods=window // 2).sum().shift(skip).to_numpy()
        # resid_mom_12: the day's return minus its industry's equal-weighted average (TAIEX's, or every
        # stock's without it, when the industry has fewer than five stocks that day)
        values = returns.to_numpy()
        if self.market is not None:
            market = pd.Series(self.market).pct_change(fill_method=None).to_numpy()
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                market = np.nanmean(values, axis=1)
        residual = values - market[:, None]
        groups: dict[str, list[int]] = defaultdict(list)
        for column, symbol in enumerate(self.symbols):
            groups[self.industry(symbol)].append(column)
        for columns in groups.values():
            block = values[:, columns]
            known = np.isfinite(block).sum(axis=1)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                average = np.nanmean(block, axis=1)
            usable = known >= 5
            residual[np.ix_(usable, columns)] = block[usable] - average[usable, None]
        frame = pd.DataFrame(residual)
        total = frame.rolling(window, min_periods=window // 2).sum()
        spread = frame.rolling(window, min_periods=window // 2).std()
        return (total / spread.where(spread > 0)).shift(skip).to_numpy()

    def excluded(self, position: int) -> set[str]:
        """exclude "v1": the traded stocks in the top fifth by 60-session idiosyncratic volatility or the
        top tenth by their largest one-day gain in 21 sessions, at ``position``."""
        if "__ivol__" not in self._cache:
            returns = self._prices.pct_change(fill_method=None)
            if self.market is not None:
                market = pd.Series(self.market).pct_change(fill_method=None)
            else:
                market = returns.mean(axis=1)
            window, least = 60, 40
            mean_r = returns.rolling(window, min_periods=least).mean()
            mean_m = market.rolling(window, min_periods=least).mean()
            cross = returns.mul(market, axis=0).rolling(window, min_periods=least).mean()
            variance_m = (market ** 2).rolling(window, min_periods=least).mean() - mean_m ** 2
            variance_r = (returns ** 2).rolling(window, min_periods=least).mean() - mean_r ** 2
            covariance = cross - mean_r.mul(mean_m, axis=0)
            beta = covariance.div(variance_m.where(variance_m > 0), axis=0)
            residual = variance_r - (beta ** 2).mul(variance_m, axis=0)
            self._cache["__ivol__"] = np.ascontiguousarray(np.sqrt(residual.clip(lower=0)).to_numpy(dtype=np.float32).T)
            self._cache["__max21__"] = np.ascontiguousarray(returns.rolling(21).max().to_numpy(dtype=np.float32).T)
        base = np.isfinite(self.panel.close[:, position]) & (self.age[:, position] >= 60)
        output: set[str] = set()
        for key, share in (("__ivol__", 0.2), ("__max21__", 0.1)):
            column = self._cache[key][:, position]
            known = base & np.isfinite(column)
            if known.sum() < 10:
                continue
            threshold = np.quantile(column[known], 1 - share)
            output |= {self.symbols[row] for row in np.flatnonzero(known & (column >= threshold))}
        return output

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
        mask = ((self.panel.close[:, position] >= rule.min_price) & (self.turnover_20[:, position] >= rule.min_turnover)
                & (self.age[:, position] >= rule.min_history))
        if getattr(rule, "large_caps", 0):
            mask &= self.largest(rule.large_caps, position)
        return mask

    def largest(self, count: int, position: int) -> np.ndarray:
        """The ``count`` stocks with the largest market value at ``position`` (none without market values)."""
        values = self.matrix("market_cap")[:, position]
        known = np.isfinite(values)
        if known.sum() <= count:
            return known
        threshold = np.partition(values[known], -count)[-count]
        return known & (values >= threshold)

    def rising(self) -> np.ndarray:
        """symbols × sessions: the adjusted close above its TREND_WINDOW-session average (hold "trend")."""
        if "__rising__" not in self._cache:
            average = self._prices.rolling(TREND_WINDOW, min_periods=TREND_WINDOW).mean()
            self._cache["__rising__"] = np.ascontiguousarray((self._prices > average).to_numpy().T)
        return self._cache["__rising__"]

    def gone(self) -> dict[str, int]:
        """symbol -> the session (position) on which an unsold holding of it becomes worthless (engine 1.3.1,
        ``write_off_position``); ``delisted`` (code -> delisting dates) is set by ``load``."""
        if "__gone__" not in self._cache:
            traded = np.isfinite(self.panel.close)
            steps = np.arange(traded.shape[1])
            delisted = getattr(self, "delisted", None) or {}
            output: dict[str, int] = {}
            for row, symbol in enumerate(self.symbols):
                days = steps[traded[row]]
                if not len(days):
                    continue
                at = write_off_position(self.sessions, int(days[-1]), delisted.get(symbol.split(".")[0], ()))
                if at is not None:
                    output[symbol] = at
            self._cache["__gone__"] = output
        return self._cache["__gone__"]

    def market_locks(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """symbols × sessions: traded (a close that day), closed locked at limit-up, closed locked at
        limit-down — the exchange's limits on the reference price (the previous traded close over the day's
        ex-rights unit factor) ± 10% (± 7% before 2015-06-01), rounded inward to the tick (engine 1.3.0)."""
        if "__locks__" in self._cache:
            return self._cache["__locks__"]
        close = self.panel.close
        traded = np.isfinite(close)
        with np.errstate(divide="ignore", invalid="ignore"):
            growth = self.panel.adjusted / close
        previous_close = pd.DataFrame(close.T).ffill().shift(1).to_numpy().T
        previous_growth = pd.DataFrame(growth.T).ffill().shift(1).to_numpy().T
        with np.errstate(divide="ignore", invalid="ignore"):
            reference = previous_close / (growth / previous_growth)
        widened = np.array([day >= PRICE_LIMIT_WIDENED for day in self.sessions])[None, :]
        rate = np.where(widened, 0.10, 0.07)
        etf = np.array([symbol.split(".")[0].startswith("00") for symbol in self.symbols])[:, None]

        def tick(price: np.ndarray) -> np.ndarray:
            stock = np.select([price < 10, price < 50, price < 100, price < 500, price < 1_000],
                              [0.01, 0.05, 0.1, 0.5, 1.0], 5.0)
            return np.where(etf, np.where(price < 50, 0.01, 0.05), stock)

        up_raw, down_raw = reference * (1 + rate), reference * (1 - rate)
        with np.errstate(invalid="ignore"):
            up = np.floor(up_raw / tick(np.nan_to_num(up_raw)) + 1e-9) * tick(np.nan_to_num(up_raw))
            down = np.ceil(down_raw / tick(np.nan_to_num(down_raw)) - 1e-9) * tick(np.nan_to_num(down_raw))
            for symbol, days in (getattr(self, "official_limits", None) or {}).items():
                row = self.row.get(symbol)
                if row is None:
                    continue
                for day, (low, high) in days.items():       # 1.4.0: an ex-rights day: the exchange's limits
                    position = self.index.get(day)
                    if position is not None:
                        down[row, position], up[row, position] = low, high
            beyond = (close > up + 1e-6) | (close < down - 1e-6)   # 1.4.0: traded beyond them: no limit that day
            locked_up = traded & np.isfinite(up) & (close >= up - 1e-6) & ~beyond
            locked_down = traded & np.isfinite(down) & (close <= down + 1e-6) & ~beyond
        self._cache["__locks__"] = (traded, locked_up, locked_down)
        return self._cache["__locks__"]

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
    if check == "revenue":
        # 2026-10-10: once a month after every monthly revenue is public. The deadline is the 10th, moved to
        # the next session when it is not one; decide on the session after it.
        output: set[date] = set()
        by_month: dict[tuple[int, int], list[date]] = defaultdict(list)
        for day in sessions:
            if day.day >= 10:
                by_month[(day.year, day.month)].append(day)
        for days in by_month.values():
            if len(days) >= 2:
                output.add(sorted(days)[1])
        return output
    return {day for day, _amount in STANDARD_PLAN.schedule(sessions, sessions[0], sessions[-1])} if sessions else set()


ROUND_TRIP = 2 * 0.001425 + 0.003 + 2 * 0.002          # fee both ways, the sell tax, slippage both ways
DEVELOPMENT_END = date(2020, 9, 30)


def switch_mapping(fp: FactorPanel, rule: DailyRule):
    """(percentiles symbols × sessions, gain(percentile) -> expected next-20-session excess) for ``cost1``: the
    rule's score as a percentile among the stocks it ranks, and the average next-20-session return above the
    eligible median of each tenth of that percentile, on the development period's weekly sessions only,
    made non-decreasing (a better rank never expects less)."""
    from quant_platform.research.consensus import member_percentiles

    key = f"__switch__{rule.rule_hash}"
    if key in fp._cache:
        return fp._cache[key]
    percentiles = member_percentiles(fp, rule.model_copy(update={"switching": "none"}))
    prices = fp.panel.filled
    sums, counts = np.zeros(10), np.zeros(10)
    positions = [fp.index[day] for day in sorted(check_days([day for day in fp.sessions
                                                             if RECENT_START <= day <= DEVELOPMENT_END], "weekly"))]
    for position in positions:
        later = position + 20
        if later >= prices.shape[1] or fp.sessions[later] > DEVELOPMENT_END:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            returns = prices[:, later] / prices[:, position] - 1
        column = percentiles[:, position]
        known = np.isfinite(column) & np.isfinite(returns)
        if known.sum() < 20:
            continue
        excess = returns[known] - np.median(returns[known])
        bins = np.minimum((column[known] * 10).astype(int), 9)
        np.add.at(sums, bins, excess)
        np.add.at(counts, bins, 1)
    means = np.maximum.accumulate(np.where(counts > 0, sums / np.maximum(counts, 1), 0.0))

    def gain(percentile: float) -> float:
        if not np.isfinite(percentile):
            return float(means[0])
        return float(means[min(int(percentile * 10), 9)])

    fp._cache[key] = (percentiles, gain)
    return fp._cache[key]


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
    switch = switch_mapping(fp, rule) if getattr(rule, "switching", "none") != "none" else None
    if getattr(rule, "exit_model", "none") != "none" and getattr(fp, "models", None) is not None:
        from quant_platform.research.exits import EXIT_VERSION, ExitAgent

        exit_agent = ExitAgent(fp, Path(fp.models) / EXIT_VERSION)
        if rule.exit_model in TECH_EXITS:
            from quant_platform.research.tech_rl import TechAgent

            factor = TECH_EXITS[rule.exit_model]
            exit_agent = TechAgent(fp, Path(fp.models) / TECH_FACTOR_VERSION[factor], factor)
    locks = fp.market_locks() if hasattr(fp, "market_locks") else None
    gone = fp.gone() if hasattr(fp, "gone") else {}
    lag = 1 if getattr(rule, "execution", "same") == "next" else 0
    again: set[int] = set()               # 1.3.0: sessions to decide again (the market refused an order)
    for count, day in enumerate(sessions):
        if count + lag >= len(sessions):
            break                             # 1.4.0 "next": decided on the last day, filled after the period
        position = fp.index[day]
        when = sessions[count + lag]          # the session the orders fill (the output's key)
        act = fp.index[when]
        lost = [symbol for symbol in current if gone.get(symbol, len(fp.sessions)) <= position]
        if day not in checks and count not in again and not lost:
            continue
        if lost:                              # 1.3.1: delisted before it was sold — worthless, off the list
            current = [symbol for symbol in current if symbol not in lost]
        holding = list(current)
        if rule.market_filter != "none" and not fp.risk_on(position):
            current, bought, peak = [], {}, {}
            output[when] = []
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
        if getattr(rule, "hold", "days") == "trend":     # keep what is still rising, sell what is not
            rising = fp.rising()[:, position]
            kept = [symbol for symbol in current
                    if symbol in keep_zone or (symbol in eligible and rising[fp.row[symbol]])]
        else:
            kept = [symbol for symbol in current
                    if symbol in keep_zone or (symbol in eligible and count - bought[symbol] < rule.min_hold)]
        if switch is not None:                  # cost1: keep a leaving holding unless swapping pays a round trip
            percentiles, gain = switch
            leaving = sorted((symbol for symbol in current if symbol not in kept and symbol in eligible),
                             key=lambda symbol: percentiles[fp.row[symbol], position])
            waiting = [symbol for symbol in ranked if symbol not in current][: len(leaving)]
            for symbol, replacement in zip(leaving, waiting):
                if (gain(percentiles[fp.row[replacement], position]) - gain(percentiles[fp.row[symbol], position])
                        <= ROUND_TRIP):
                    kept.append(symbol)
        held = set(kept)
        limit = rule.industry_limit
        counts: dict[str, int] = defaultdict(int)
        if limit:
            for symbol in kept:
                counts[fp.industry(symbol)] += 1
        chosen = []
        vetoed = fp.news_vetoed(position) if getattr(rule, "news_veto", "none") == "v1" else set()
        if getattr(rule, "exclude", "none") == "v1":
            vetoed = vetoed | fp.excluded(position)       # no new buys; holdings keep their usual rules
        if exit_agent is not None and hasattr(exit_agent, "refuses"):
            vetoed = vetoed | exit_agent.refuses(position)  # tech1: the robot buys only what it wants
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
        if locks is not None:                 # 1.3.0: what the market lets the rule do at this close
            traded, locked_up, locked_down = locks
            refused, final = False, []
            for symbol in current:
                row = fp.row.get(symbol)
                if symbol not in holding and row is not None and (not traded[row, act] or locked_up[row, act]):
                    refused = True             # not bought: limit-up or no trade today
                    continue
                final.append(symbol)
            for symbol in holding:
                row = fp.row.get(symbol)
                if symbol not in current and row is not None and (not traded[row, act] or locked_down[row, act]):
                    refused = True             # not sold: limit-down or no trade today (a suspension too)
                    final.append(symbol)
            current = final
            if refused:
                again.add(count + 1)
        bought = {symbol: bought.get(symbol, count) for symbol in current}
        if rule.stop_loss or exit_agent is not None:
            peak = {symbol: peak[symbol] if symbol in peak else fp.price(symbol, position) for symbol in current}
        if exit_agent is not None:
            buy_price = {symbol: buy_price.get(symbol, fp.price(symbol, position)) for symbol in current}
        output[when] = list(current)
    return output


# --- simulation --------------------------------------------------------------------------------
def simulate_daily(data: LegacyData, rule: DailyRule | None, costs: CostModel, start: date, end: date,
                   ranks: dict[date, list[str]] | None = None, plan=None,
                   ledger: list[dict[str, object]] | None = None,
                   snapshots: dict | None = None, weights: dict[date, dict[str, float]] | None = None,
                   parked: set[date] | None = None, exposure: dict[date, float] | None = None) -> RunResult:
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
    applied = 1.0                                # share of the stock part invested (vol_scale; the rest is cash)
    minimum = max(rule.min_trade, 1.0) if rule else 1.0

    def price(symbol: str, day: date) -> float:
        series = data.closes.get(symbol, {})
        return series[day] if day in series else (data.last_close(symbol, day) or 0.0)

    blocked = False                              # an order the market refused today: try again tomorrow

    def refused(symbol: str, day: date, side: str) -> bool:
        """The market's rule: no trade on a session without a close, no buy at a locked limit-up close, no
        sell at a locked limit-down close."""
        nonlocal blocked
        close = data.closes.get(symbol, {}).get(day)
        locked = False
        if close is not None:
            low, high = price_limits(data, symbol, day)
            locked = (high is not None and side == "BUY" and close >= high - 1e-6) or \
                     (low is not None and side == "SELL" and close <= low + 1e-6)
        if close is None or locked:
            blocked = True
            if ledger is not None:
                reason = "沒有成交" if close is None else ("漲停買不到" if side == "BUY" else "跌停賣不掉")
                ledger.append({"day": day, "symbol": symbol, "side": "BLOCKED", "order": side, "reason": reason})
            return True
        return False

    gone = write_off_days(data)
    actions = getattr(data, "actions", None) or {}
    owed: list[tuple[date, float, str]] = []     # 1.4.0: (payment session, NT$, symbol) — dividends not yet paid
    short: set[str] = set()                      # 1.4.0: bought less than wanted today (the turnover cap)
    calendar = data.sessions
    where = {session: number for number, session in enumerate(calendar)}

    def payment_session(ex_day: date, paid: date | None) -> date:
        import bisect

        if paid is not None:
            place = bisect.bisect_left(calendar, paid)
        else:
            place = where.get(ex_day, bisect.bisect_left(calendar, ex_day)) + PAY_LAG
        return calendar[place] if place < len(calendar) else date.max

    def order_fee(shares: float, fill: float, day: date) -> int:
        """The fee per order part: the round lots and the odd lot are two orders, each with its minimum."""
        return _order_fee(costs, shares, fill, day)

    most = participation(rule)

    def capacity(symbol: str, day: date, fill: float) -> float:
        """Whole shares the session can take: ``most`` of the stock's turnover that day."""
        turnover = data.traded_value.get(symbol, {}).get(day)
        return float("inf") if not turnover or symbol == BENCHMARK else math.floor(most * turnover / fill)

    def cut(symbol: str, day: date, side: str) -> None:
        nonlocal blocked
        blocked = True
        if ledger is not None:
            ledger.append({"day": day, "symbol": symbol, "side": "BLOCKED", "order": side,
                           "reason": f"超過當天成交值 {most:.1%}，其餘下個交易日再{'買' if side == 'BUY' else '賣'}"})

    def buy(symbol: str, budget: float, day: date) -> None:
        nonlocal cash
        close = data.closes.get(symbol, {}).get(day)
        if budget <= 0 or refused(symbol, day, "BUY"):
            return
        fill = fill_price(close, "BUY", costs.slippage_bps, _tick(symbol))
        high = price_limits(data, symbol, day)[1]
        if high is not None:
            fill = min(fill, high)                 # 1.4.0: never above the day's limit-up price
        shares = affordable_shares(min(budget, cash), fill, costs)
        while shares > 0 and shares * fill + order_fee(shares, fill, day) > min(budget, cash) + 1e-9:
            shares -= 1
        room = capacity(symbol, day, fill)
        if shares > room:
            shares = int(room)
            short.add(symbol)
            cut(symbol, day, "BUY")
        if shares > 0:
            fee = order_fee(shares, fill, day)
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
        if units[symbol] <= 0 or refused(symbol, day, "SELL"):
            return
        shares, fill = units[symbol], fill_price(close, "SELL", costs.slippage_bps, _tick(symbol))
        low = price_limits(data, symbol, day)[0]
        if low is not None:
            fill = max(fill, low)                  # 1.4.0: never below the day's limit-down price
        if amount is not None:
            shares = min(shares, float(math.floor(amount / fill)))
            if shares <= 0:
                return
        room = capacity(symbol, day, fill)
        if shares > room:
            shares = float(room)
            cut(symbol, day, "SELL")
            if shares <= 0:
                return
        amount = shares * fill
        fee, tax = order_fee(shares, fill, day), costs.tax(amount, _tax_kind(symbol), "SELL")
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
        retry, blocked = blocked, False
        topping, short = short, set()
        if owed and owed[0][0] <= day:            # 1.4.0: dividends paid today
            owed.sort()
            while owed and owed[0][0] <= day:
                _when, amount, symbol = owed.pop(0)
                cash += amount
                if ledger is not None:
                    ledger.append({"day": day, "symbol": symbol, "side": "PAID", "amount": round(amount, 2)})
        for symbol in [held for held, count in units.items() if count > 0]:
            action = actions.get(symbol, {}).get(day) if actions else None
            count = units[symbol]
            if action is not None:                 # 1.4.0: an ex-rights / ex-dividend day, as a holder lives it
                if action.cash > 0:
                    gross = count * action.cash
                    premium = health_premium(gross, day)
                    owed.append((payment_session(day, action.paid), gross - premium, symbol))
                    if ledger is not None:
                        ledger.append({"day": day, "symbol": symbol, "side": "DIVIDEND", "cash": round(gross, 2),
                                       "premium": premium, "net": round(gross - premium, 2),
                                       "paid": payment_session(day, action.paid)})
                if action.shares > 0:
                    whole, _fraction = whole_shares(count, action.shares)   # the fraction: lost (the transfer fee)
                    units[symbol] = count + whole
                    if ledger is not None and whole:
                        ledger.append({"day": day, "symbol": symbol, "side": "ADJUST", "kind": "stock_dividend",
                                       "factor": (count + whole) / count, "shares": whole})
            else:
                factor = data.factors.get(symbol, {}).get(day, 1.0)
                if factor != 1.0:                  # a split or capital change: whole shares, the fraction lost
                    units[symbol] = float(math.floor(count * factor + 1e-9))
                    if ledger is not None:                 # "shares": the change, so ledgers of many accounts add up
                        ledger.append({"day": day, "symbol": symbol, "side": "ADJUST", "kind": "split",
                                       "factor": units[symbol] / count if count else factor,
                                       "shares": units[symbol] - count})
            if symbol in gone and gone[symbol] <= day:     # 1.3.1: delisted before it was sold: worthless
                if ledger is not None:
                    ledger.append({"day": day, "symbol": symbol, "side": "SELL", "shares": units[symbol],
                                   "price": 0.0, "fee": 0.0, "tax": 0.0, "reason": "下市，持股歸零"})
                units[symbol] = 0.0
        contribution = by_day.get(day, 0.0)
        cash += contribution
        if rule is None:
            if cash > 0:
                buy(BENCHMARK, cash, day)
        else:
            park = parked is not None and day in parked
            today = [] if park else (ranks.get(day) if ranks else None)
            again = today is None and retry and not park       # yesterday's refused orders, same picks
            if again:
                today = list(picks)
            if today is not None:
                for symbol in [held for held, count in units.items() if count > 0 and held != BENCHMARK and held not in today]:
                    sell_all(symbol, day)
                picks = today
                if not park and not again:
                    shares_of = (weights or {}).get(day) or {}
            if was_parked and not park:            # back from 0050: keep only the core in it
                total = cash + sum(count * price(symbol, day) for symbol, count in units.items() if count > 0)
                excess = units[BENCHMARK] * price(BENCHMARK, day) - total * rule.core
                if excess >= minimum:
                    sell_all(BENCHMARK, day, excess)
            level = exposure.get(day, applied) if exposure is not None else 1.0
            moved = level != applied
            if today is not None or contribution or park != was_parked or moved:
                total = cash + sum(count * price(symbol, day) for symbol, count in units.items() if count > 0)
                targets: dict[str, float] = {}
                if rule.core > 0 or park:
                    targets[BENCHMARK] = total * (1.0 if park else rule.core)
                for symbol in picks:
                    share = shares_of.get(symbol, 1 / len(picks)) if shares_of else 1 / len(picks)
                    targets[symbol] = targets.get(symbol, 0.0) + total * (1 - rule.core) * level * share
                if moved and level < applied:            # volatility up: trim every holding to its target
                    for symbol in picks:
                        excess = units[symbol] * price(symbol, day) - targets.get(symbol, 0.0)
                        if units[symbol] > 0 and excess >= minimum:
                            sell_all(symbol, day, excess)
                orders = []
                for symbol, target in targets.items():
                    held_value = units[symbol] * price(symbol, day)
                    gap = target - held_value
                    if gap > 0 and (held_value <= 0 or contribution or (park and symbol == BENCHMARK)
                                    or (moved and level > applied) or symbol in topping):
                        orders.append((gap, symbol))
                applied = level
                for gap, symbol in sorted(orders, reverse=True):
                    amount = min(gap, cash)
                    if amount >= minimum:
                        buy(symbol, amount, day)
        if rule is not None:
            was_parked = parked is not None and day in parked
        receivable = sum(amount for _when, amount, _symbol in owed)       # declared, not yet paid: still owned
        value = cash + receivable + sum(count * price(symbol, day) for symbol, count in units.items() if count > 0)
        result.values.append(value)
        result.flows.append(contribution)
        if snapshots is not None:
            snapshots[day] = (cash + receivable, {symbol: count for symbol, count in units.items() if count > 0})
    return result


VOL_WINDOW = 21          # sessions of the rule's own returns for its current volatility (about a month)
VOL_BASELINE = 750       # sessions whose median volatility is its usual level (about three years)
VOL_STEP = 0.25          # the invested share moves in quarters: fewer trades
VOL_BAND = 1.25          # fully invested until the volatility runs 25% above its usual level


def write_off_position(sessions: list[date], last: int, delisted) -> int | None:
    """Engine 1.3.1: the session (index into ``sessions``) on which an unsold holding becomes worthless for a
    stock whose last trade is ``sessions[last]`` — the first session on or after a delisting date (the
    exchange's list) later than that trade; else, when it never trades again, IDLE_LIMIT sessions after it;
    None while the data ends too soon to tell (it stays held at its last close, unsellable)."""
    import bisect

    after = [day for day in delisted if day > sessions[last]]
    if after:
        place = bisect.bisect_left(sessions, min(after))
        return place if place < len(sessions) else None
    return last + IDLE_LIMIT if last + IDLE_LIMIT < len(sessions) else None


def write_off_days(data: LegacyData) -> dict[str, date]:
    """symbol -> the session on which an unsold holding of it becomes worthless (``write_off_position`` on
    ``data``; the delisting dates in ``data.notes["delisted"]``)."""
    cached = data.__dict__.get("_write_off")
    if cached is not None:
        return cached
    index = {session: number for number, session in enumerate(data.sessions)}
    delisted = data.notes.get("delisted") or {}
    output: dict[str, date] = {}
    for symbol, series in data.closes.items():
        traded = [index[day] for day in series if day in index]
        if not traded:
            continue
        at = write_off_position(data.sessions, max(traded), delisted.get(symbol.split(".")[0], ()))
        if at is not None:
            output[symbol] = data.sessions[at]
    data.__dict__["_write_off"] = output
    return output


def participation(rule) -> float:
    """An order's share of the stock's turnover in the session it fills, at most (engine 1.4.0)."""
    return NEXT_DAY_PARTICIPATION if getattr(rule, "execution", "same") == "next" else SAME_DAY_PARTICIPATION


def actions_digest(data: LegacyData) -> str:
    """The corporate actions a run lives through (engine 1.4.0), up to RECENT_END."""
    cached = data.__dict__.get("_actions_digest")
    if cached is None:
        rows = sorted((symbol, day.isoformat(), round(action.cash, 6), round(action.shares, 6),
                       action.paid.isoformat() if action.paid else "", action.source)
                      for symbol, days in (getattr(data, "actions", None) or {}).items()
                      for day, action in days.items() if day <= RECENT_END)
        cached = data.__dict__["_actions_digest"] = hashlib.sha256(json.dumps(rows).encode()).hexdigest()
    return cached


def delisting_dates(history: str | Path) -> dict[str, list[date]]:
    """code -> its delisting dates (``actions.delisting_dates``; ``load_stock_data`` puts them in the notes)."""
    from quant_platform.research.actions import delisting_dates as dates

    return dates(Path(history))


def price_limits(data: LegacyData, symbol: str, day: date) -> tuple[float | None, float | None]:
    """(limit-down, limit-up) of ``symbol`` on ``day``: the reference price — the previous session's close
    divided by the day's ex-rights unit factor — ± 10% (± 7% before 2015-06-01), rounded inward to the tick;
    (None, None) without a previous close."""
    import bisect

    from quant_platform.research.costs import _to_tick

    series = data.closes.get(symbol, {})
    official = ((data.notes or {}).get("limits") or {}).get(symbol, {}).get(day)
    if official:                              # 1.4.0: an ex-rights day: the exchange's own limits
        low, high = official
    else:
        cache = data.__dict__.setdefault("_limit_days", {})          # sorted session dates per symbol
        days = cache.get(symbol)
        if days is None or len(days) != len(series):
            days = cache[symbol] = sorted(series)
        place = bisect.bisect_left(days, day)
        if place == 0:
            return None, None
        previous = series[days[place - 1]]
        reference = previous / data.factors.get(symbol, {}).get(day, 1.0)
        rate = 0.10 if day >= PRICE_LIMIT_WIDENED else 0.07
        tick = _tick(symbol)
        low, high = _to_tick(reference * (1 - rate), True, tick), _to_tick(reference * (1 + rate), False, tick)
    close = series.get(day)
    if close is not None and (close > high + 1e-6 or close < low - 1e-6):
        return None, None                     # 1.4.0: it traded beyond them — no limit that day (e.g. after a
                                              # capital reduction), or the limits were not these
    return low, high


def exposure_schedule(data: LegacyData, fp: FactorPanel, rule: DailyRule, costs: CostModel) -> dict[date, float] | None:
    """vol_scale "v1": for every session, the share of the stock part to hold — 1.25 times the usual
    volatility of the rule's own stock account (the median of its 21-session volatility over the past 750
    sessions) divided by its 21-session volatility now, at most 1, rounded down to a quarter: fully invested
    in ordinary times, half at twice the usual volatility, a quarter at four times; the rest stays in cash.
    Known at the close it is used on (the account without scaling, replayed from SHADOW_WARMUP). None when off."""
    if getattr(rule, "vol_scale", "none") == "none":
        return None
    shadow = rule.model_copy(update={"vol_scale": "none", "core": 0.0, "account_filter": "none"})
    sessions = fp.sessions
    start, end = sessions[min(SHADOW_WARMUP, len(sessions) - 1)], sessions[-1]
    ranks = daily_rankings(fp, shadow, start, end)
    run = simulate_daily(data, shadow, costs, start, end, ranks, weights=daily_weights(fp, shadow, ranks))
    units = pd.Series(unit_values(run.values, run.flows))
    volatility = units.pct_change(fill_method=None).rolling(VOL_WINDOW).std()
    usual = volatility.rolling(VOL_BASELINE, min_periods=VOL_WINDOW * 6).median()
    output = {}
    for day, now, normal in zip(run.days, volatility.to_numpy(), usual.to_numpy(), strict=True):
        if not (np.isfinite(now) and np.isfinite(normal)) or now <= 0:
            output[day] = 1.0
            continue
        output[day] = float(np.floor(min(1.0, VOL_BAND * normal / now) / VOL_STEP) * VOL_STEP)
    if getattr(rule, "execution", "same") == "next":          # 1.4.0: acted on at the next session
        later = dict(zip(run.days, run.days[1:]))
        output = {later[day]: level for day, level in output.items() if day in later}
    return output


def daily_weights(fp: FactorPanel, rule: DailyRule, ranks: dict[date, list[str]]) -> dict[date, dict[str, float]] | None:
    """R7b: each pick's share of the stock part, in proportion to 1 / its 60-session volatility (a pick
    without one takes the median of the others); None for equal amounts."""
    if rule.weighting == "equal":
        return None
    volatility = -fp.matrix("low_volatility_60")             # stored negated: the daily standard deviation
    lag = 1 if getattr(rule, "execution", "same") == "next" else 0
    output: dict[date, dict[str, float]] = {}
    for day, names in ranks.items():
        if not names:
            continue
        position = fp.index[day] - lag                        # 1.4.0: as known on the deciding session
        sigmas = np.array([volatility[fp.row[symbol], position] for symbol in names], dtype=float)
        valid = np.isfinite(sigmas) & (sigmas > VOL_FLOOR)    # 1.3.1: a flat price is not a calm stock
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
    below = {day for day, unit, mean in zip(run.days, units, average, strict=True) if np.isfinite(mean) and unit < mean}
    if getattr(rule, "execution", "same") == "next":          # 1.4.0: acted on at the next session
        later = dict(zip(run.days, run.days[1:]))
        below = {later[day] for day in below if day in later}
    return below


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

    exposure = exposure_schedule(data, fp, rule, costs)

    def account(start: date, end: date) -> RunResult:
        return simulate_daily(data, rule, costs, start, end, ranks, weights=weights, parked=parked, exposure=exposure)

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

    fp = FactorPanel(panel, Industries(load_industries(history)),
                     ChipStore(history, panel.sessions, panel.symbols, panel.close),
                     market_closes(history, panel.sessions), models_root(history))
    return data, fp


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
    delisted = sorted((code, day.isoformat()) for code, days in (data.notes.get("delisted") or {}).items()
                      for day in days if day <= RECENT_END)
    payload["delisted"] = hashlib.sha256(json.dumps(delisted).encode()).hexdigest()   # 1.3.1: the list it read
    payload["actions"] = actions_digest(data)                                          # 1.4.0: dividends as paid
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
    if any(name in CONSENSUS_FACTORS for name in factor_names):  # 2026-10-11: the members and their models
        from quant_platform.research.consensus import digest as consensus_digest

        used = [name for name in factor_names if name in CONSENSUS_FACTORS]
        payload["consensus"] = (consensus_digest(history) if used == ["t0_consensus"]     # as first registered
                                else {name: consensus_digest(history, name) for name in used})
    if "factor_momentum" in factor_names and "statements" not in payload:   # its base factors read the statements
        from quant_platform.research.fundamentals import digest as statements_digest

        payload["statements"] = statements_digest(history, RECENT_END)
    if "rl_timing" in factor_names:                             # 2026-10-14: the robot 2.0's trained weights
        from quant_platform.research.model import models_root
        from quant_platform.research.rl_timing import VERSION as ROBOT_VERSION
        from quant_platform.research.rl_timing import digest as robot_digest

        payload["robot"] = robot_digest(models_root(history) / ROBOT_VERSION)
    if any(name in TECH_FACTORS for name in factor_names):      # 2026-10-10: the technical robot's policies
        from quant_platform.research.tech_rl import digest as tech_digest
        from quant_platform.research.tech_rl import tech_dir

        used = [name for name in factor_names if name in TECH_FACTORS]
        payload["tech"] = (tech_digest(tech_dir(history)) if used == ["rl_tech"]       # as the 1.0.0 trials were registered
                           else {name: tech_digest(tech_dir(history, TECH_FACTOR_VERSION[name])) for name in used})
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


def vol_scale_batch() -> list:
    """2026-10-09: the best 100%-stock rules scaled by their own volatility (research/blend.py ``vol_scale_batch``)."""
    from quant_platform.research.blend import vol_scale_batch as scaled_batch

    return scaled_batch()


def low_drawdown_batch() -> list:
    """2026-10-09: 100% stocks with a shallower drawdown (research/blend.py ``low_drawdown_batch``)."""
    from quant_platform.research.blend import low_drawdown_batch as lowdd

    return lowdd()


def large_cap_batch() -> list:
    """2026-10-09: picking among the 100 largest stocks only (research/blend.py ``large_cap_batch``)."""
    from quant_platform.research.blend import large_cap_batch as large

    return large()


def large_cap_second_batch() -> list:
    """2026-10-09: three more large-cap rules (research/blend.py ``large_cap_second_batch``)."""
    from quant_platform.research.blend import large_cap_second_batch as second

    return second()


def evidence_batch() -> list[DailyRule]:
    """2026-10-10, registered before any of the new factors' strength was looked at: the six ideas ranked
    by the Taiwan-quant review (reports/台股量化 開源專案與實證.md), all 100% stocks, top 20, at most 3 in 10
    from one industry. (1) the revenue calendar: trend plus 3-month revenue growth, deciding once a month
    after every revenue is public, on the T0 candidate's frame; (2) trend plus its continuity, equal
    amounts (run only if continuity is not just calmness: see the design check in research_method);
    (3) intraday momentum on the T0 candidate's frame; (4) industry-residual momentum, equal amounts;
    (5) the full-market trend rule refusing lottery-like stocks; (6) a four-factor score like FinLab's
    published one (revenue, 6-month momentum, ROE, calmness) on the revenue calendar — calibrating how much
    a public backtest shrinks on this engine."""
    large = {"industry_cap": 0.3, "large_caps": 100}
    return [
        DailyRule(name="大型股（市值前 100）站上 200 日均線＋近 3 個月營收年增：前 20 名、同產業最多 3 成、營收公布後換股、依波動度配置",
                  factors={"trend_200": 1.0, "revenue_yoy_3m": 1.0}, check="revenue", weighting="inverse_vol", **large),
        DailyRule(name="大型股（市值前 100）站上 200 日均線＋趨勢連續性：前 20 名、同產業最多 3 成、每檔等額",
                  factors={"trend_200": 1.0, "fip_12": 1.0}, **large),
        DailyRule(name="大型股（市值前 100）日內動能：前 20 名、同產業最多 3 成、依波動度配置",
                  factors={"imom_12": 1.0}, weighting="inverse_vol", **large),
        DailyRule(name="大型股（市值前 100）產業殘差動能：前 20 名、同產業最多 3 成、每檔等額",
                  factors={"resid_mom_12": 1.0}, **large),
        DailyRule(name="每天 站上 200 日均線：前 20 名、同產業最多 3 成、依波動度配置、不買樂透型股票",
                  factors={"trend_200": 1.0}, industry_cap=0.3, weighting="inverse_vol", exclude="v1"),
        DailyRule(name="四因子 近 3 個月營收年增＋6 個月動能＋ROE＋60 日低波動：前 20 名、同產業最多 3 成、營收公布後換股",
                  factors={"revenue_yoy_3m": 1.0, "momentum_6": 1.0, "roe_ttm": 1.0, "low_volatility_60": 1.0},
                  check="revenue", industry_cap=0.3),
    ]


def tech_bot_batch() -> list[DailyRule]:
    """2026-10-10 (使用者：純技術分析的 RL 機器人): the robot itself — it buys only stocks it wants, best first
    (top 20, at most 3 in 10 from one industry), and alone decides every sale (the engine's own keep zone and
    holding period are set wide so they never sell first) — and its score alone in an ordinary rule (the
    usual keep zone and 20-session holding, no robot sales), to see what its exits add."""
    return [
        DailyRule(name="技術分析 RL 機器人：只看價格與成交量，自己決定買賣（前 20 名、同產業最多 3 成）",
                  factors={"rl_tech": 1.0}, exit_model="tech1", keep=5, min_hold=250, industry_cap=0.3),
        DailyRule(name="技術分析 RL 機器人分數：前 20 名、同產業最多 3 成（一般保留與持有規則）",
                  factors={"rl_tech": 1.0}, industry_cap=0.3),
    ]


def t0_equal_batch() -> list:
    """2026-10-10 (RL 3.0's result: equal shares of the T0 candidates beat its own allocation): one account
    split equally among every 100%-stock daily T0 candidate at the time it runs, the shares set when money
    arrives (each sleeve runs its own rule with its share of the owner's cash flow)."""
    from pathlib import Path as _Path

    from quant_platform.research.blend import BlendRule, Sleeve
    from quant_platform.research.rl_strategies import t0_candidates

    specs = t0_candidates(_Path("instance") / "research")
    if len(specs) < 2:
        return []
    share = 1 / len(specs)
    sleeves = tuple(Sleeve(rule=DailyRule.model_validate(spec), share=share) for _trial, _name, spec in specs)
    return [BlendRule(name=f"組合：全部 T0 候選平均分配（{len(specs)} 個策略、100% 個股）", sleeves=sleeves)]


def tech_bot_weekly_batch() -> list[DailyRule]:
    """2026-10-10: the robot's second version (tech-rl-1.1.0, weekly decisions on the next five sessions),
    the same two rules as ``tech_bot_batch`` deciding weekly."""
    return [
        DailyRule(name="技術分析 RL 機器人（每週）：只看價格與成交量，自己決定買賣（前 20 名、同產業最多 3 成）",
                  factors={"rl_tech_weekly": 1.0}, exit_model="tech2", keep=5, min_hold=250, industry_cap=0.3,
                  check="weekly"),
        DailyRule(name="技術分析 RL 機器人分數（每週）：前 20 名、同產業最多 3 成（一般保留與持有規則）",
                  factors={"rl_tech_weekly": 1.0}, industry_cap=0.3, check="weekly"),
    ]


def consensus_batch() -> list[DailyRule]:
    """2026-10-11 (全面研究「立即採用」第 1 項): the T0 consensus list, its top 20 and — registered together,
    not chosen after — its top 40 as the structural counterpart. Weekly like most members."""
    common = {"check": "weekly", "keep": 3, "min_hold": 20, "industry_cap": 0.3}
    return [
        DailyRule(name="T0 共識清單（25 個 T0 候選投票）：前 20 名、同產業最多 3 成、每週決策",
                  factors={"t0_consensus": 1.0}, top=20, **common),
        DailyRule(name="T0 共識清單（25 個 T0 候選投票）：前 40 名、同產業最多 3 成、每週決策",
                  factors={"t0_consensus": 1.0}, top=40, **common),
    ]


def dev_consensus_batch() -> list[DailyRule]:
    """2026-10-11: the clean test of the consensus method — the 180 development-period scan finalists voting,
    top 20 and top 40 registered together, weekly like ``consensus_batch``."""
    common = {"check": "weekly", "keep": 3, "min_hold": 20, "industry_cap": 0.3}
    return [
        DailyRule(name="開發期共識清單（180 個搜尋最後名單投票）：前 20 名、同產業最多 3 成、每週決策",
                  factors={"dev_consensus": 1.0}, top=20, **common),
        DailyRule(name="開發期共識清單（180 個搜尋最後名單投票）：前 40 名、同產業最多 3 成、每週決策",
                  factors={"dev_consensus": 1.0}, top=40, **common),
    ]


def model_f_batch() -> list[DailyRule]:
    """2026-10-12: gbm-2.0.0 as a rule — only if it passed its registered gate (research/model_ensemble.py)."""
    from quant_platform.research.model_ensemble import gate

    check = gate(Path("instance") / "research" / "models")
    print(f"gbm-2.0.0 門檻（2016-2020 前 20 名多賺）：{check}", flush=True)
    if not check["passed"]:
        return []
    return [DailyRule(name="機器學習集成（模型 F）：前 20 名、同產業最多 3 成、每週決策", factors={"ml_gbm_ens": 1.0},
                      check="weekly", industry_cap=0.3)]


def cost_switch_batch() -> list[DailyRule]:
    """2026-10-12: cost-aware switching on the two consensus frames that passed — the T0 consensus top 20
    (#2272) and the development-period consensus top 40 (#2275); each is one new trial."""
    common = {"check": "weekly", "keep": 3, "min_hold": 20, "industry_cap": 0.3, "switching": "cost1"}
    return [
        DailyRule(name="T0 共識清單（25 個 T0 候選投票）：前 20 名、同產業最多 3 成、每週決策、看成本才換股",
                  factors={"t0_consensus": 1.0}, top=20, **common),
        DailyRule(name="開發期共識清單（180 個搜尋最後名單投票）：前 40 名、同產業最多 3 成、每週決策、看成本才換股",
                  factors={"dev_consensus": 1.0}, top=40, **common),
    ]


def tech_model_batch() -> list[DailyRule]:
    """2026-10-13 (技術分析機器人第二版，第一步): the technical ensemble's top 20, weekly — passes only if it
    also beats #2090 (52-week high + RSI + MACD, the best purely technical T0 candidate) on the same account."""
    return [DailyRule(name="技術模型（只用價量技術因子的集成）：前 20 名、同產業最多 3 成、每週決策",
                      factors={"ml_tech_ens": 1.0}, check="weekly", industry_cap=0.3)]


def min_hold_batch() -> list[DailyRule]:
    """2026-10-13 (使用者：為什麼限制 20 個交易日才賣？要執行賣出策略怎麼辦): the four best rules with no
    minimum holding — a holding is sold as soon as it leaves the keep zone (top 3 × N), as it already is the
    moment it stops being eligible. The 20 sessions were the new design's default, never compared."""
    from quant_platform.research.consensus_members import MEMBERS_V1

    specs = {trial: spec for trial, _name, spec in MEMBERS_V1}
    rules = [consensus_batch()[0], dev_consensus_batch()[1], DailyRule.model_validate(specs[2107]),
             DailyRule.model_validate(specs[2168])]
    return [rule.model_copy(update={"min_hold": 0, "name": (rule.name + "、不設最短持有")[:80]}) for rule in rules]


def rerun_batch() -> list:
    """2026-10-13: every 100%-stock rule rated T0 候選 or T1 today, run again by engine 1.3.0 (the market's price
    limits, a refused order decided again the next session) — the same rules, new trials of their input."""
    import json as _json
    from pathlib import Path as _Path

    from quant_platform.research.blend import parse_spec
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.registry import TrialRegistry

    research = _Path("instance") / "research"
    latest = {}
    for record in TrialRegistry(research / "trials.jsonl").records():
        if record.kind == "candidate" and record.period == "recent" and                 not str(record.metrics.get("engine", "")).startswith(("daily-1.3", "daily-1.4")):
            latest[record.spec_hash] = record          # the tiers as rated before the market's rules: a fixed set
    rules = []
    for record in sorted(latest.values(), key=lambda record: record.trial_id):
        if tier(record.metrics)[0] not in ("T0 候選", "T1"):
            continue
        try:
            spec = _json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
        except (OSError, ValueError, KeyError):
            continue
        if uses_0050(spec):
            continue
        rule = parse_spec(spec)
        if getattr(rule, "kind", None) == "blend" and len(rule.sleeves) > 4:
            continue                           # the 17-way equal blend: 437 orders a month, not a strategy
        rules.append(rule)
    return rules


def rerun131_batch() -> list:
    """2026-10-13 evening: the same 64 rules on engine 1.3.1 (an unsold delisted holding is worthless), each
    three ways (使用者：超過 20 天才賣是策略，不是限制；還在漲就留著): the fixed 20-session
    minimum as chosen, no minimum, and hold "trend" (kept while above its 20-session average, sold once below)."""
    import json as _json
    from pathlib import Path as _Path

    from quant_platform.research.blend import parse_spec
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.registry import TrialRegistry

    base = rerun_batch()
    known = {rule.rule_hash for rule in base}
    research = _Path("instance") / "research"
    scanned = {}                              # the new-factor scan's finalists, first run on 1.3.0 only
    for record in TrialRegistry(research / "trials.jsonl").records():
        if record.kind == "candidate" and record.period == "recent" and record.metrics.get("engine") == "daily-1.3.0"                 and record.spec_hash not in known and record.spec_hash not in scanned:
            try:
                spec = _json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
            except (OSError, ValueError, KeyError):
                continue
            if not uses_0050(spec):
                scanned[record.spec_hash] = parse_spec(spec)
    output = []
    for rule in base + list(scanned.values()):
        output.append(rule)
        if getattr(rule, "kind", None) == "blend":
            continue
        if rule.min_hold and rule.rule_hash in known:
            output.append(rule.model_copy(update={"min_hold": 0, "name": (rule.name + "、不設最短持有")[:80]}))
        output.append(DailyRule.model_validate({**rule.model_dump(), "hold": "trend",
                                                "name": (rule.name + "、月線上續抱")[:80]}))
    unique: dict[str, object] = {}
    for rule in output:                      # a rule already without a minimum is in the set once
        unique.setdefault(rule.rule_hash, rule)
    return list(unique.values())


def rerun_models_batch() -> list:
    """2026-10-14 (使用者：上面的制度有沒有修正到所有策略，包含機器學習、RL): every rule with a trained model's score,
    the technical robot, the learned exit, or a blend of rules (the RL allocations among them) not already in
    ``rerun131_batch``, run again on engine 1.4.0 — whole shares, dividends as paid, the turnover cap."""
    import json as _json
    from pathlib import Path as _Path

    from quant_platform.research.blend import parse_spec
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.registry import TrialRegistry

    research = _Path("instance") / "research"
    known = {rule.rule_hash for rule in rerun131_batch()}
    latest = {}
    for record in TrialRegistry(research / "trials.jsonl").records():
        if record.kind == "candidate" and record.period == "recent":
            latest[record.spec_hash] = record
    learned = set(MODEL_FACTORS) | set(TECH_FACTORS)
    rules = []
    for record in sorted(latest.values(), key=lambda record: record.trial_id):
        if record.spec_hash in known:
            continue
        try:
            spec = _json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
        except (OSError, ValueError, KeyError):
            continue
        if uses_0050(spec):
            continue
        blend = spec.get("kind") == "blend" or "sleeves" in spec
        if not (blend or spec.get("exit_model", "none") != "none" or set(spec.get("factors") or {}) & learned):
            continue
        rule = parse_spec(spec)
        if blend and len(rule.sleeves) > 4:
            continue
        rules.append(rule)
    return rules


def factor_momentum_batch() -> list:
    """2026-10-14: one rule on factor momentum — the five factors with the best realised record of the last year,
    the top 20, at most 3 in 10 from one industry, decided weekly, holding by the trend. Nothing is tuned: the
    record's window, horizon and the five come from the literature (research/factor_timing.py)."""
    return [DailyRule(name="因子動能：最近一年最強的 5 個因子、前 20 名、同產業最多 3 成、每週決策、月線上續抱",
                      factors={"factor_momentum": 1.0}, top=20, check="weekly", industry_cap=0.3, hold="trend")]


def robot2_batch() -> list:
    """2026-10-14: the technical robot 2.0 and its two yardsticks, one rule each, otherwise the same: the top 20,
    at most 3 in 10 from one industry, decided weekly, holding by the trend."""
    common = {"top": 20, "check": "weekly", "industry_cap": 0.3, "hold": "trend"}
    return [DailyRule(name="技術 RL 機器人 2.0：20 個技術因子的權重由機器人決定、前 20 名、同產業最多 3 成、每週決策、月線上續抱",
                      factors={"rl_timing": 1.0}, **common),
            DailyRule(name="20 個技術因子平均：前 20 名、同產業最多 3 成、每週決策、月線上續抱",
                      factors={"tech_equal": 1.0}, **common),
            DailyRule(name="技術因子動能：最近一年最強的 5 個技術因子、前 20 名、同產業最多 3 成、每週決策、月線上續抱",
                      factors={"tech_momentum": 1.0}, **common)]


def rerun140_batch() -> list:
    """2026-10-13 night: the same 211 rules on engine 1.4.0 (whole shares, dividends as paid, the turnover cap),
    filled the same day in the after-hours sessions. Not the next session (使用者 2026-10-14：要在 14:30 前算出
    建議，隔天成交是另一種價格、另一種策略，不是限制): the platform decides right after the close instead."""
    return rerun131_batch()


def blends_batch() -> list:
    """2026-10-09: accounts split across strategy families (research/blend.py)."""
    from quant_platform.research.blend import blend_batch

    return blend_batch()


BATCHES = {"factors": factor_batch, "risk": risk_batch, "chips": chip_batch, "combos": combo_batch,
           "tpex": tpex_batch, "overlays": overlay_batch, "holdings": holdings_batch, "model": model_batch,
           "model-excess": model_excess_batch, "statements": statement_batch, "turnover": turnover_batch,
           "exits": exits_batch, "blends": blends_batch, "model-60": model_60_batch,
           "t0hunt": t0_hunt_batch, "lowdd": low_drawdown_batch,
           "volscale": vol_scale_batch, "largecap": large_cap_batch,
           "largecap2": large_cap_second_batch, "evidence": evidence_batch,
           "techbot": tech_bot_batch, "t0equal": t0_equal_batch,
           "techbot2": tech_bot_weekly_batch, "consensus": consensus_batch,
           "devconsensus": dev_consensus_batch, "modelf": model_f_batch,
           "costswitch": cost_switch_batch, "techmodel": tech_model_batch,
           "minhold": min_hold_batch, "rerun13": rerun_batch, "rerun131": rerun131_batch,
           "rerun140": rerun140_batch, "rerun_models": rerun_models_batch, "factormom": factor_momentum_batch,
           "robot2": robot2_batch}
