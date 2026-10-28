"""2026-10-10 (使用者：沒測過上千上萬個方法不要說找不到 T0；不要用一樣的參數充數): a broad, two-stage
search over genuinely different 100%-stock strategies.

The space: every signal alone, every pair and every triple of 39 signals — the price, technical, chip,
statement and trend-quality factors (monthly revenue left out: the owner counts it as lagging; market
value left out: ranking on it buys the index's biggest names) and the four trained model scores — each in
two universes (every listed stock; the 100 largest) with two weightings (equal amounts; by calmness).
Every rule holds the top 20, keeps a holding while it ranks in the top 60 or for its first 20 sessions, at
most 3 in 10 from one industry; rules with a model score or a chip factor decide weekly (those scores move
every day), the rest daily. Never 0050.

Stage 1 (screen) uses the development period only (2015-06-01..2020-09-30): a fast vectorised account —
the picks' next-session returns (equal or calmness weights, redrawn daily), costs on every entry and exit
(fee, sell tax, slippage) — against 0050's own unit value. Results are not registered one by one; the scan
folder keeps every candidate's numbers and the count. Stage 2: candidates that beat 0050, keep the drawdown
within 0050's + 5 points and win at least half the 1-year windows, best first, taken greedily so that no
two finalists' daily returns correlate above 0.9, run through the full engine (``daily.run_trial``: both
periods, rolling windows, the gate; registered as trials).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

# 1.1.0 (2026-10-11, the full review's lesson 2): the development-period excess did not predict 2020-10 on
# (rank correlation 0.23, -0.01, 0.02 over three rounds of finalists), so finalists are chosen by stability —
# both halves of the development period beat 0050, best by the weaker half — and kept apart by the correlation
# of their daily returns *above 0050* (at most 0.7; plain returns all share the market). Every candidate's
# daily excess is kept for counting effective trials.
# 1.2.0 (2026-10-14; 使用者：超過 20 天才賣是策略不是限制，還在漲就留著): every new candidate holds by the trend —
# a holding out of the top 60 stays while its adjusted close is above its 20-session average and goes once it is
# not (DailyRule hold "trend"); the screen's account does the same with ``Arrays.rising``. Older scans' rows carry
# no "hold" and keep the fixed 20 sessions.
# 1.2.1 (2026-10-15, with daily engine 1.4.2): an equal screen score goes to the more liquid stock (20-session
# turnover, ``Arrays.liquidity``), not the lower stock code.
# 1.3.0 (2026-10-16, with daily engine 1.4.3): a trend holding below its average and out of the zone is sold every
# session, not only on the weekly decision days; and the decision frequency is a candidate dimension — every set is
# tried deciding daily and weekly (sets with a chip or model factor used to be weekly only, never tried daily).
SCAN_VERSION = "scan-1.3.0"
CALM_GAP = 0.01             # 2026-10-18: --calm, at most this much further than 0050 in each of its 10% falls
EXCESS_CORRELATION_LIMIT = 0.7
DEV_END = date(2020, 9, 30)
TOP, KEEP, MIN_HOLD, INDUSTRY_SHARE = 20, 3, 20, 0.3
LARGE = 100
CORRELATION_LIMIT = 0.9
# overlays; 2026-10-19 the insiders' notices and the buybacks failed their registered event study (research_method §7):
# features for the models (model H), not signals for the search
LEFT_OUT = ("revenue_yoy", "revenue_yoy_3m", "revenue_accel", "market_cap", "dl_risk", "dl_risk2", "low_insider_sell_60",
            "buyback_60", "pledge_heavy", "low_pledge",     # 2026-10-20: the pledges wait for their event study
            # 2026-10-21: the holder brackets and the paid FinMind tables waited for their strength analysis; 10-22 the
            # strong ones (fewer holders, small holders leaving, margin maintenance) are in, the weak, the duplicate
            # (margin_in_profit ranks as margin_safe does) and the short-history one stay out
            "big_holder_pct", "big_holder_up_4w", "margin_in_profit", "low_pledge_loan_20", "block_premium_20",
            "block_volume_20", "broker_concentration_5",
            "govbank_net_20", "govbank_dip_buy_20",      # 2026-10-22: wait for their strength analysis (short history)
            "branch_informed_20", "low_daytrade_5")      # 2026-10-23: the branches, the same
SHORT = {
    "momentum_12_1": "12 月動能", "momentum_6": "6 月動能", "momentum_3": "3 月動能", "reversal_1": "1 月反轉",
    "reversal_5d": "5 日反轉", "low_volatility_60": "60 日低波", "low_volatility_250": "250 日低波",
    "low_max_return": "不追飆股", "high_52w": "近 52 週高", "dividend_yield": "殖利率", "liquidity": "成交值大",
    "volume_surge": "量能放大", "trend_200": "站上 200 日線", "ma_cross_20_60": "均線交叉", "rsi_14": "RSI",
    "macd_hist": "MACD", "kd_k": "KD", "bollinger_b": "布林位置", "breakout_55": "55 日突破",
    "foreign_holding": "外資持股", "foreign_holding_change": "外資持股增", "foreign_buy_20": "外資買超",
    "trust_buy_20": "投信買超", "margin_growth_20": "融資增加", "short_margin_ratio": "券資比",
    "earnings_yield": "本益比低", "book_to_price": "淨值比低", "roe_ttm": "ROE", "gross_margin": "毛利率",
    "operating_margin_change": "營益率增", "eps_growth": "EPS 成長", "low_debt": "負債低", "fip_12": "趨勢連續",
    "imom_12": "日內動能", "resid_mom_12": "殘差動能", "ml_gbm": "模型A", "ml_gbm_excess": "模型B",
    "ml_gbm_statements": "模型C", "ml_gbm_60": "模型D", "ml_gbm_quality": "模型E", "ml_gbm_ens": "模型F", "ml_tech_ens": "技術模型", "ml_gbm_ext": "模型G", "ml_gbm_h": "模型H", "ml_gbm_i": "模型I", "ml_gbm_j": "模型J", "ml_gbm_k": "模型K", "ml_gbm_l": "模型L", "ml_gbm_m": "模型M", "ml_deep2_holders": "深度2集保", "ml_deep2_volneutral": "深度2同波動", "ml_deep2_riskadj": "深度2波動調整", "ml_tech_deep": "技術機器人3", "ml_tech_deep_r": "技術機器人4", "ml_deep2_r": "深度模型2近年", "ml_deep2_wide_r": "深度2加上櫃近年",
    "ml_deep2_riskadj_r": "深度2波動調整近年", "ml_deep2_holders_r": "深度2集保近年", "ml_deep3_r": "深度模型3近年", "ml_deep2_monthly": "深度2月訓", "ml_gbm_ext_monthly": "模型G月訓", "low_insider_sell_60": "內部人不賣", "buyback_60": "庫藏股", "low_pledge": "質押低", "pledge_heavy": "質押過半",
         "industry_mom_6": "產業動能", "industry_mom_1": "產業短期動能", "industry_laggard_5": "同業落後補漲",
         "big_holder_pct": "千張大戶比例", "big_holder_up_4w": "大戶增加", "holders_down_4w": "股東人數減少",
         "retail_down_4w": "散戶減少", "margin_safe": "融資維持率高", "margin_in_profit": "融資沒套牢",
         "low_pledge_loan_20": "借貸沒增加", "block_premium_20": "鉅額溢價", "block_volume_20": "鉅額量",
         "broker_concentration_5": "主力買超", "govbank_net_20": "官股買超", "govbank_dip_buy_20": "官股逢跌買", "branch_informed_20": "分點跟著買", "low_daytrade_5": "隔日沖少", "ml_deep": "深度模型", "ml_deep2": "深度模型2", "ml_deep2_top": "深度模型2前一成", "ml_deep3": "深度模型3", "ml_deep2_wide": "深度模型2加上櫃", "rl_tech": "技術機器人",
    "high_52w_count_60": "新高次數", "range_position_60": "區間位置", "low_ivol_60": "特質低波",
    "low_pv_corr_20": "量價背離", "overnight_reversal_12": "隔夜反轉", "rl_tech_weekly": "技術機器人週",
    "season_month": "同月季節性", "dividend_ahead": "即將除息", "trust_quarter_end": "投信季底作帳",
    "low_sbl_short": "借券賣出低", "sbl_cover_20": "借券回補", "low_day_trade_20": "當沖比例低",
    "low_lending_fee_60": "借券費率低", "low_lending_20": "新借券少", "lending_fee_not_rising": "借券費率未升",
    "dividend_raise": "股利調升", "no_cash_issue": "沒有現增", "low_issuance_250": "股本沒膨脹",
    "dealer_buy_20": "自營商自行買超", "dealer_hedge_20": "自營商避險買超",
    "low_attention_20": "不過熱", "low_disposition_60": "未被處置",
    "eps_surprise": "盈餘驚喜", "eps_accel": "盈餘加速", "eps_streak": "盈餘連續成長",
}


def signals(models_root: Path | None = None) -> list[str]:
    from quant_platform.research.daily import FACTOR_LABELS, MODEL_FACTORS

    failed = failed_models(models_root or Path("instance") / "research" / "models")
    return [name for name in (*FACTOR_LABELS, *MODEL_FACTORS) if name not in LEFT_OUT and name not in failed]


def failed_models(models_root: Path) -> set[str]:
    """2026-10-17: the model scores whose model was trained and failed its registered gate — no rule, no search (the
    10-16 searches had dl-1.0.0 in, which had failed; one finalist, #4737, T3). A model not trained is not left out
    here (its scores are empty)."""
    from quant_platform.research.model_deep2 import gate as deep2_gate
    from quant_platform.research.model_ensemble import gate as top_gate

    checks = {"ml_gbm_ens": lambda: top_gate(models_root),
              "ml_gbm_ext": lambda: top_gate(models_root, "gbm-2.2.0", "gbm-2.0.0"),
              "ml_gbm_h": lambda: top_gate(models_root, "gbm-2.3.0", "gbm-2.2.0"),
              "ml_gbm_i": lambda: top_gate(models_root, "gbm-2.5.0", "gbm-2.3.0"),
              "ml_gbm_j": lambda: top_gate(models_root, "gbm-2.6.0", "gbm-2.5.0"),
              "ml_gbm_k": lambda: top_gate(models_root, "gbm-2.7.0", "gbm-2.6.0"),
              "ml_gbm_l": lambda: top_gate(models_root, "gbm-2.8.0", "gbm-2.7.0", (2021, 2022, 2023, 2024, 2025)),
              "ml_gbm_m": lambda: top_gate(models_root, "gbm-2.9.0", "gbm-2.7.0", (2021, 2022, 2023, 2024, 2025)),
              "ml_gbm_ext_monthly": lambda: top_gate(models_root, "gbm-2.4.0", "gbm-2.2.0"),
              "ml_deep2_riskadj": lambda: deep2_gate(models_root, "dl-2.4.0"),
              "ml_tech_deep": lambda: deep2_gate(models_root, "dl-2.5.0"),
              "ml_tech_deep_r": lambda: deep2_gate(models_root, "dl-2.5.1"),
              "ml_deep2_r": lambda: deep2_gate(models_root, "dl-2.0.1"),
              "ml_deep2_wide_r": lambda: deep2_gate(models_root, "dl-2.2.1"),
              "ml_deep2_riskadj_r": lambda: deep2_gate(models_root, "dl-2.4.1"),
              "ml_deep2_holders_r": lambda: deep2_gate(models_root, "dl-2.7.1"),
              "ml_deep3_r": lambda: deep2_gate(models_root, "dl-3.0.1"),
              "ml_deep2_monthly": lambda: deep2_gate(models_root, "dl-2.6.0"),
              "ml_deep2_holders": lambda: deep2_gate(models_root, "dl-2.7.0"),
              "ml_deep": lambda: top_gate(models_root, "dl-1.0.0", "gbm-2.0.0"),
              "ml_deep2": lambda: deep2_gate(models_root, "dl-2.0.0"),
              "ml_deep2_top": lambda: deep2_gate(models_root, "dl-2.1.0"),
              "ml_deep3": lambda: deep2_gate(models_root, "dl-3.0.0"),
              "ml_deep2_wide": lambda: deep2_gate(models_root, "dl-2.2.0")}
    output = set()
    for name, check in checks.items():
        try:
            result = check()
        except (OSError, ValueError, KeyError):
            continue
        trained = (result.get("top20_gain") is not None and result.get("against_top20_gain") is not None
                   and "reason" not in result)          # both the model and its yardstick trained: a judged gate
        if trained and not result.get("passed"):
            output.add(name)
    return output


def weekly(factors: tuple[str, ...]) -> bool:
    from quant_platform.research.chips import CHIP_FACTORS
    from quant_platform.research.daily import MODEL_FACTORS

    return any(name in MODEL_FACTORS or name in CHIP_FACTORS for name in factors)


def candidates(names: list[str] | None = None, largest: int = 3, smallest: int = 1,
               require: str | list[str] | None = None, hold: str = "trend") -> list[dict]:
    """Every set of ``smallest``..``largest`` signals × universe (all, large) × weighting (equal, calm);
    with ``require`` only the sets that contain it — or, given a list, at least one of them (new signals
    against everything else)."""
    names = names or signals()
    wanted = set([require] if isinstance(require, str) else (require or []))
    output = []
    for size in range(smallest, largest + 1):
        for group in combinations(names, size):
            if wanted and not wanted & set(group):
                continue
            for universe in ("all", "large"):
                for weighting in ("equal", "inverse_vol"):
                    for check in ("daily", "weekly"):        # 1.3.0: both, whatever the signals
                        output.append({"factors": list(group), "universe": universe, "weighting": weighting,
                                       "check": check, "hold": hold})
    return output


def to_rule(candidate: dict):
    from quant_platform.research.daily import DailyRule

    parts = [f"掃描：{'＋'.join(SHORT[name] for name in candidate['factors'])}：前 20 名、同產業最多 3 成"]
    if candidate["universe"] == "large":
        parts.append(f"市值前 {LARGE}")
    if candidate["weighting"] == "inverse_vol":
        parts.append("依波動度配置")
    if candidate["check"] == "weekly":
        parts.append("每週決策")
    hold = candidate.get("hold", "days")
    # 2026-10-20 (使用者：持有 20 天、月線上續抱都是策略): three holding styles, each named — "days" holds every buy a
    # fixed MIN_HOLD sessions, "trend" keeps it while above its 20-session average, "free" sells once out of the zone
    if hold == "trend":
        parts.append("月線上續抱")
    elif hold == "free":
        parts.append("不設最短持有")
    elif candidate.get("named_hold"):
        parts.append(f"持有 {MIN_HOLD} 天")
    return DailyRule(name="、".join(parts)[:80], factors={name: 1.0 for name in candidate["factors"]}, top=TOP,
                     check=candidate["check"], keep=KEEP, min_hold=0 if hold == "free" else MIN_HOLD,
                     industry_cap=INDUSTRY_SHARE, weighting=candidate["weighting"],
                     large_caps=LARGE if candidate["universe"] == "large" else 0,
                     hold="days" if hold == "free" else hold)


# --- stage 1 arrays ------------------------------------------------------------------------------
@dataclass
class Arrays:
    names: list[str]
    percentiles: np.ndarray     # universe (all, large) × signal × symbol × session, float16, NaN = not ranked
    next_returns: np.ndarray    # symbol × session: the return from this session's close to the next
    calm: np.ndarray            # symbol × session: 1 / 60-session volatility (median where unknown)
    industry: np.ndarray        # symbol: industry number
    weekly_days: np.ndarray     # session: a weekly check day
    bench: np.ndarray           # session: 0050's return from this close to the next
    days: list[date]
    rising: np.ndarray | None = None   # symbol × session: above its 20-session average (1.2.0; None in older scans)
    liquidity: np.ndarray | None = None  # symbol × session: 20-session turnover, the tie-break (1.2.1)


def prepare(data, fp, costs, folder: Path, names: list[str] | None = None) -> Arrays:
    """Write the development period's arrays to ``folder`` (memory-mapped by the workers); ``names``: the signals
    (default every scan signal; 2026-10-26 the full-period search adds the paid tables')."""
    from quant_platform.research.daily import RECENT_START, DailyRule, check_days, simulate_daily
    from quant_platform.research.metrics import unit_values
    from quant_platform.research.stock_rules import SeedPlan

    folder.mkdir(parents=True, exist_ok=True)
    positions = [index for index, day in enumerate(fp.sessions) if RECENT_START <= day <= DEV_END]
    first, last = positions[0], positions[-1] + 1          # one more session for the last return
    days = list(fp.sessions[first:last + 1])
    base = DailyRule(name="資格", factors={"trend_200": 1.0})
    eligible = np.column_stack([fp.eligible(base, position) for position in range(first, last + 1)])
    large = eligible & np.column_stack([fp.largest(LARGE, position) for position in range(first, last + 1)])
    names = list(names) if names else signals()
    percentiles = np.lib.format.open_memmap(folder / "percentiles.npy", mode="w+", dtype=np.float16,
                                            shape=(2, len(names), len(fp.symbols), len(days)))
    for index, name in enumerate(names):
        values = fp.matrix(name)[:, first:last + 1].astype(np.float64)
        for slot, mask in enumerate((eligible, large)):
            frame = pd.DataFrame(np.where(mask, values, np.nan))
            percentiles[slot, index] = frame.rank(axis=0, pct=True).to_numpy(dtype=np.float16)
    percentiles.flush()
    prices = fp.panel.filled[:, first:last + 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        moves = prices[:, 1:] / prices[:, :-1] - 1               # column i: session i's close to the next
    next_returns = np.zeros((len(fp.symbols), len(days)), dtype=np.float32)
    next_returns[:, :-1] = np.nan_to_num(moves, nan=0.0, posinf=0.0, neginf=0.0)
    volatility = -fp.matrix("low_volatility_60")[:, first:last + 1].astype(np.float64)
    known = np.isfinite(volatility) & (volatility > 0)
    fill = np.nanmedian(np.where(known, volatility, np.nan)) if known.any() else 0.02
    calm = (1 / np.where(known, volatility, fill)).astype(np.float32)
    groups = {group: number for number, group in enumerate(sorted({fp.industry(symbol) for symbol in fp.symbols}))}
    industry = np.array([groups[fp.industry(symbol)] for symbol in fp.symbols], dtype=np.int32)
    weekly_set = check_days(days, "weekly")
    weekly_days = np.array([day in weekly_set for day in days])
    run = simulate_daily(data, None, costs, days[0], days[-1], plan=SeedPlan())
    units = pd.Series(unit_values(run.values, run.flows), index=run.days).reindex(days).ffill().to_numpy()
    bench = np.zeros(len(days), dtype=np.float64)
    bench[:-1] = units[1:] / units[:-1] - 1
    np.save(folder / "next_returns.npy", next_returns)
    np.save(folder / "calm.npy", calm)
    np.save(folder / "industry.npy", industry)
    np.save(folder / "weekly_days.npy", weekly_days)
    np.save(folder / "bench.npy", np.nan_to_num(bench))
    np.save(folder / "rising.npy", fp.rising()[:, first:last + 1].astype(np.uint8))
    np.save(folder / "liquidity.npy", np.nan_to_num(fp.turnover_20[:, first:last + 1]).astype(np.float32))
    (folder / "meta.json").write_text(json.dumps({"names": names, "days": [day.isoformat() for day in days]},
                                                 ensure_ascii=False), encoding="utf-8")
    return load_arrays(folder)


def load_arrays(folder: Path) -> Arrays:
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    return Arrays(names=meta["names"], percentiles=np.load(folder / "percentiles.npy", mmap_mode="r"),
                  next_returns=np.load(folder / "next_returns.npy"), calm=np.load(folder / "calm.npy"),
                  industry=np.load(folder / "industry.npy"), weekly_days=np.load(folder / "weekly_days.npy"),
                  bench=np.load(folder / "bench.npy"), days=[date.fromisoformat(day) for day in meta["days"]],
                  rising=np.load(folder / "rising.npy") if (folder / "rising.npy").is_file() else None,
                  liquidity=np.load(folder / "liquidity.npy") if (folder / "liquidity.npy").is_file() else None)


# --- stage 1 account -----------------------------------------------------------------------------
def score_of(arrays: Arrays, candidate: dict) -> np.ndarray:
    slot = 0 if candidate["universe"] == "all" else 1
    total = None
    for name in candidate["factors"]:
        values = np.asarray(arrays.percentiles[slot, arrays.names.index(name)], dtype=np.float32)
        total = values if total is None else total + values
    return total


def account(score: np.ndarray, checks: np.ndarray, industry: np.ndarray, next_returns: np.ndarray,
            calm: np.ndarray | None, cost_in: float, cost_out: float, record: bool = False,
            rising: np.ndarray | None = None, liquidity: np.ndarray | None = None, minimum: int = MIN_HOLD):
    """Daily returns after costs of the top-20 rule on ``score`` (symbols × sessions, NaN = not ranked),
    deciding on ``checks``; with ``record`` also the holdings after each session. With ``rising`` (hold
    "trend") a holding out of the zone stays while it is rising, instead of for its first MIN_HOLD sessions."""
    sessions = score.shape[1]
    limit = max(1, int(INDUSTRY_SHARE * TOP))
    held: list[int] = []
    bought: dict[int, int] = {}
    previous: dict[int, float] = {}
    returns = np.zeros(sessions - 1)
    entries = 0
    history = []
    for t in range(sessions - 1):
        if checks[t]:
            column = score[:, t]
            valid = np.flatnonzero(~np.isnan(column))
            if valid.size == 0:
                held = []
            else:
                if liquidity is None:
                    order = valid[np.argsort(-column[valid], kind="stable")].tolist()
                else:                             # 1.2.1: an equal score goes to the more liquid stock
                    order = valid[np.lexsort((-liquidity[valid, t], -column[valid]))].tolist()
                zone = set(order[: TOP * KEEP])
                if rising is not None:
                    kept = [row for row in held if row in zone or (not np.isnan(column[row]) and rising[row, t])]
                else:
                    kept = [row for row in held
                            if row in zone or (not np.isnan(column[row]) and t - bought[row] < minimum)]
                taken = set(kept)
                counts = Counter(int(industry[row]) for row in kept)
                for row in order:
                    if len(taken) >= TOP:
                        break
                    if row in taken:
                        continue
                    group = int(industry[row])
                    if counts[group] >= limit:
                        continue
                    counts[group] += 1
                    taken.add(row)
                    kept.append(row)
                held = kept
            bought = {row: bought.get(row, t) for row in held}
        elif rising is not None and held:         # 1.3.0: a trend holding out of the zone and falling goes today
            column = score[:, t]
            valid = np.flatnonzero(~np.isnan(column))
            if liquidity is None:
                order = valid[np.argsort(-column[valid], kind="stable")].tolist()
            else:
                order = valid[np.lexsort((-liquidity[valid, t], -column[valid]))].tolist()
            zone = set(order[: TOP * KEEP])
            held = [row for row in held if row in zone or (not np.isnan(column[row]) and rising[row, t])]
        if record:
            history.append(list(held))
        if not held:
            weights: dict[int, float] = {}
        elif calm is None:
            weights = {row: 1 / len(held) for row in held}
        else:
            raw = np.array([calm[row, t] for row in held], dtype=float)
            weights = {row: float(value) for row, value in zip(held, raw / raw.sum())}
        cost = sum(weight * cost_in for row, weight in weights.items() if row not in previous)
        cost += sum(weight * cost_out for row, weight in previous.items() if row not in weights)
        entries += sum(1 for row in weights if row not in previous)
        returns[t] = sum(weight * next_returns[row, t] for row, weight in weights.items()) - cost
        previous = weights
    return (returns, entries, history) if record else (returns, entries)


def metrics(returns: np.ndarray, bench: np.ndarray, years: float, entries: int) -> dict:
    value = np.cumprod(1 + returns)
    base = np.cumprod(1 + bench[: len(returns)])
    drawdown = float(np.min(value / np.maximum.accumulate(value) - 1))
    bench_drawdown = float(np.min(base / np.maximum.accumulate(base) - 1))
    excess = float((value[-1] / base[-1]) ** (1 / years) - 1)
    relative = [value[start + 245] / value[start] - base[start + 245] / base[start]
                for start in range(0, len(value) - 245, 21)]
    middle = len(value) // 2
    halves = []
    for low, high in ((0, middle), (middle, len(value) - 1)):
        span = max((high - low) / 245, 1e-9)
        start_value = value[low - 1] if low else 1.0
        start_base = base[low - 1] if low else 1.0
        halves.append(float(((value[high] / start_value) / (base[high] / start_base)) ** (1 / span) - 1))
    return {"excess": round(excess, 5), "annual": round(float(value[-1] ** (1 / years) - 1), 5),
            "drawdown": round(drawdown, 4), "bench_drawdown": round(bench_drawdown, 4),
            "win_1y": round(float(np.mean([item > 0 for item in relative])), 4) if relative else None,
            "worst_1y": round(float(min(relative)), 4) if relative else None,
            "first_half": round(halves[0], 5), "second_half": round(halves[1], 5),
            "entries_per_month": round(entries / (years * 12), 2)}


def passes(row: dict, stable: bool = False) -> bool:
    ok = row["excess"] > 0 and row["drawdown"] >= row["bench_drawdown"] - 0.05 and (row["win_1y"] or 0) >= 0.5
    if stable:                                   # 1.1.0: both halves of the development period beat 0050
        ok = ok and (row.get("first_half") or 0) > 0 and (row.get("second_half") or 0) > 0
    return ok


def stability(row: dict) -> tuple[float, float]:
    """1.1.0's order: the weaker half's excess, then the worst 1-year window."""
    return (min(row.get("first_half") or 0, row.get("second_half") or 0), row.get("worst_1y") or -9)


def evaluate(arrays: Arrays, candidate: dict, costs, record: bool = False):
    from quant_platform.research.costs import DEFAULT_SLIPPAGE_BPS

    slippage = getattr(costs, "slippage_bps", DEFAULT_SLIPPAGE_BPS) / 10_000
    fee = costs.fee_rate * costs.fee_discount
    tax = dict(costs.tax_rates).get("stock", 0.003)
    checks = arrays.weekly_days if candidate["check"] == "weekly" else np.ones(len(arrays.days), dtype=bool)
    calm = arrays.calm if candidate["weighting"] == "inverse_vol" else None
    trend = candidate.get("hold") == "trend"
    if trend and arrays.rising is None:
        raise ValueError("這次掃描的陣列沒有月線資料（scan 1.2.0 以前），不能評估月線上續抱")
    outcome = account(score_of(arrays, candidate), checks, arrays.industry, arrays.next_returns, calm,
                      fee + slippage, fee + tax + slippage, record, arrays.rising if trend else None, arrays.liquidity,
                      0 if candidate.get("hold") == "free" else MIN_HOLD)
    years = (arrays.days[-1] - arrays.days[0]).days / 365.25
    return metrics(outcome[0], arrays.bench, years, outcome[1]), outcome


def _work(folder: str, chunk: list[dict], out: str, costs) -> int:
    arrays = load_arrays(Path(folder))
    excess = np.empty((len(chunk), len(arrays.days) - 1), dtype=np.float16)
    with open(out, "a", encoding="utf-8") as handle:
        for index, candidate in enumerate(chunk):
            row, outcome = evaluate(arrays, candidate, costs)
            excess[index] = outcome[0] - arrays.bench[: len(outcome[0])]
            handle.write(json.dumps({**candidate, **row}, ensure_ascii=False) + "\n")
            handle.flush()
    np.save(Path(out).with_suffix(".excess.npy"), excess)        # daily excess, same order as the lines
    return len(chunk)


def screen(arrays_folder: Path, items: list[dict], costs, workers: int, out_folder: Path, progress=None,
           gpu: bool | None = None) -> list[dict]:
    """Stage 1 over ``items`` in ``workers`` processes (0 = here); every result is written as it is made. On the GPU
    when there is one (2026-10-16, research/scan_gpu.py: thousands of candidates at once; ``gpu=False`` forces the
    CPU)."""
    if gpu is None:
        from quant_platform.research.gpu import device

        gpu = device().type == "cuda"
    if gpu and items:
        from quant_platform.research.scan_gpu import screen_gpu

        return screen_gpu(arrays_folder, items, costs, out_folder, progress)
    out_folder.mkdir(parents=True, exist_ok=True)
    if workers <= 0:                             # here, one chunk (the same files as a worker writes)
        path = out_folder / "screen-0.jsonl"
        path.unlink(missing_ok=True)
        _work(str(arrays_folder), items, str(path), costs)
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    chunks = [items[index::workers] for index in range(workers)]
    paths = [out_folder / f"screen-{index}.jsonl" for index in range(workers)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_work, str(arrays_folder), chunk, str(path), costs) for chunk, path in zip(chunks, paths)]
        while True:
            done, pending = wait(futures, timeout=20)
            if progress:
                progress(sum(_count_lines(path) for path in paths))
            if not pending:
                break
        for future in futures:
            future.result()
    rows = []
    for path in paths:
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def novelty(fp, new: list[str], limit: float = 0.7) -> dict[str, dict]:
    """The registered novelty gate (no returns looked at): each new signal's average weekly rank correlation
    with every existing signal over the development period; kept only if none reaches ``limit``."""
    from quant_platform.research.daily import RECENT_START, DailyRule, check_days

    base = DailyRule(name="資格", factors={"trend_200": 1.0})
    weeks = [fp.index[day] for day in sorted(check_days([day for day in fp.sessions
                                                         if RECENT_START <= day <= DEV_END], "weekly"))]
    existing = [name for name in signals() if name not in new]
    output = {}
    for name in new:
        values = fp.matrix(name)
        worst, against = 0.0, None
        for other in existing:
            others = fp.matrix(other)
            correlations = []
            for position in weeks[::4]:
                mask = fp.eligible(base, position) & np.isfinite(values[:, position]) & np.isfinite(others[:, position])
                if mask.sum() < 30:
                    continue
                correlations.append(pd.Series(values[mask, position]).rank().corr(pd.Series(others[mask, position]).rank()))
            if correlations and abs(float(np.mean(correlations))) > abs(worst):
                worst, against = float(np.mean(correlations)), other
        output[name] = {"max_correlation": round(worst, 3), "with": against, "kept": abs(worst) < limit}
    return output


def effective_trials(folder: Path, sample: int = 10_000, seed: int = 0, pattern: str = "screen-*.excess.npy",
                     screened: int | None = None) -> dict[str, object]:
    """How many independent tries a scan's first stage amounts to (for the deflated Sharpe ratio): on a random
    sample of its candidates' daily excess over 0050, the principal components that explain 95% of the
    variance and the groups left when candidates correlated above 0.9 are merged (greedy). Both are lower
    bounds for the whole scan (more candidates, more groups); the total screened is the upper bound."""
    parts = [np.load(path, mmap_mode="r") for path in sorted(folder.glob(pattern))]
    if not parts:
        return {}
    sizes = np.cumsum([0] + [len(part) for part in parts])
    total = int(sizes[-1])
    rng = np.random.default_rng(seed)
    pick = np.sort(rng.choice(total, size=min(sample, total), replace=False))
    block = np.stack([np.asarray(parts[int(np.searchsorted(sizes, index, side="right") - 1)]
                                 [index - sizes[np.searchsorted(sizes, index, side="right") - 1]], dtype=np.float32)
                      for index in pick])
    block = block[np.std(block, axis=1) > 0]
    standard = (block - block.mean(axis=1, keepdims=True)) / block.std(axis=1, keepdims=True)
    singular = np.linalg.svd(standard, compute_uv=False)
    share = np.cumsum(singular ** 2) / np.sum(singular ** 2)
    components = int(np.searchsorted(share, 0.95) + 1)
    heads = np.empty_like(standard)
    groups = 0
    for row in standard:
        if groups == 0 or float(np.max(heads[:groups] @ row)) / len(row) <= 0.9:
            heads[groups] = row
            groups += 1
    return {"screened": int(screened or total), "sample": int(len(block)), "components_95": components,
            "groups_090": groups}


def sample_effective(folder: Path, costs, sample: int = 10_000, workers: int = 6, seed: int = 0) -> dict[str, object]:
    """For a scan made before 1.1.0 (no daily excess kept): replay a random sample of its first-stage
    candidates on its own arrays, keep their daily excess, and count effective trials on them; written to
    the folder's effective.json."""
    rows = [json.loads(line) for path in sorted(folder.glob("screen-*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(rows), size=min(sample, len(rows)), replace=False)
    items = [{key: rows[index][key] for key in ("factors", "universe", "weighting", "check", "hold") if key in rows[index]}
             for index in pick]                         # 2026-10-16 (audit): the hold too, else trend sets ran fixed
    out = folder / "sample"
    out.mkdir(exist_ok=True)
    for path in out.glob("screen-*"):
        path.unlink()
    screen(folder / "arrays", items, costs, workers, out)
    found = effective_trials(out, sample=len(items), seed=seed, screened=len(rows))
    (folder / "effective.json").write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    return found


def all_effective(research: Path) -> dict[str, int]:
    """Every scan's effective-trial counts added up (the lower bound groups and the upper bound screened)."""
    groups = screened = scans = 0
    for folder in sorted((research / "scans").glob("scan-*")) if (research / "scans").is_dir() else []:
        found = None
        if (folder / "effective.json").is_file():
            found = json.loads((folder / "effective.json").read_text(encoding="utf-8"))
        elif (folder / "summary.json").is_file():
            found = json.loads((folder / "summary.json").read_text(encoding="utf-8")).get("effective_trials")
        if found:
            groups += int(found.get("groups_090") or 0)
            screened += int(found.get("screened") or 0)
            scans += 1
    return {"scans": scans, "groups_090": groups, "screened": screened}


def _count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as handle:
            return sum(1 for _line in handle)
    except OSError:
        return 0


def worst_fall_gap(returns: np.ndarray, bench: np.ndarray) -> float | None:
    """2026-10-18 (使用者：市場不好時不要比 0050 跌得多): over every stretch the 0050 daily returns ``bench`` fell 10%
    or more from a high, the strategy's change less 0050's — the worst one (None when 0050 never fell that far)."""
    from quant_platform.research.downside import falls

    bench = np.asarray(bench[: len(returns)], dtype=np.float64)
    returns = np.asarray(returns, dtype=np.float64)
    worst = None
    for peak, trough in falls(bench):
        mine = float(np.prod(1 + returns[peak:trough]) - 1)
        theirs = float(np.prod(1 + bench[peak:trough]) - 1)
        worst = mine - theirs if worst is None else min(worst, mine - theirs)
    return worst


def finalists(arrays: Arrays, rows: list[dict], costs, count: int, pool: int = 600,
              seeds: list[dict] | None = None, stable: bool = False, calm: bool = False) -> list[dict]:
    """The passing candidates, best development-period excess first, skipping any whose daily returns
    correlate above CORRELATION_LIMIT with one already taken — or with a ``seeds`` candidate (an earlier
    scan's finalists: a new scan adds strategies, not near-copies of ones already run). 2026-10-18: ``calm`` —
    also skipping any that fell more than CALM_GAP further than 0050 in one of 0050's 10% falls of the
    development period (2018's end, 2020's spring)."""
    if stable:                                   # 1.1.0: stability first, apart by the excess correlation
        ranked = sorted((row for row in rows if passes(row, True)), key=stability, reverse=True)[:pool]
    else:
        ranked = sorted((row for row in rows if passes(row)), key=lambda row: -row["excess"])[:pool]
    limit = EXCESS_CORRELATION_LIMIT if stable else CORRELATION_LIMIT

    def apart(returns: np.ndarray) -> np.ndarray:
        return returns - arrays.bench[: len(returns)] if stable else returns
    earlier = []
    for seed in seeds or []:
        if not set(seed["factors"]) <= set(arrays.names):   # a signal this scan's arrays do not have (a newer model)
            continue
        _metrics, (returns, _entries) = evaluate(arrays, seed, costs)
        if np.std(returns) > 0:
            earlier.append((seed, apart(returns)))
    taken: list[tuple[dict, np.ndarray]] = []
    for row in ranked:
        if len(taken) >= count:
            break
        candidate = {key: row[key] for key in ("factors", "universe", "weighting", "check", "hold") if key in row}
        _metrics, (returns, _entries) = evaluate(arrays, candidate, costs)
        if np.std(returns) == 0:
            continue
        if calm:
            gap = worst_fall_gap(returns, arrays.bench)
            if gap is not None and gap < -CALM_GAP:
                continue
            row["worst_fall_gap"] = round(gap, 4) if gap is not None else None
        returns = apart(returns)
        # 1.1.0, 2026-10-13: against earlier scans only a near copy is skipped (0.9 — with 200 earlier finalists
        # every new set sits within 0.7 of one of them); among this scan's own finalists the 0.7 limit holds
        if any(np.corrcoef(returns, other)[0, 1] > max(limit, CORRELATION_LIMIT) for _row, other in earlier):
            continue
        if any(np.corrcoef(returns, other)[0, 1] > limit for _row, other in taken):
            continue
        taken.append((row, returns))
    return [row for row, _returns in taken]


def earlier_finalists(research: Path) -> list[dict]:
    """Every registered scan rule (named 掃描：…) as a candidate, to keep new finalists different."""
    output, seen = [], set()
    path = research / "trials.jsonl"
    if not path.is_file():
        return output
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if not str(record.get("spec_name", "")).startswith("掃描："):
            continue
        try:
            spec = json.loads((research / "reports" / record["report_file"]).read_text(encoding="utf-8"))["spec"]
        except (OSError, ValueError, KeyError):
            continue
        key = record["spec_hash"]
        if key in seen:
            continue
        seen.add(key)
        output.append({"factors": list(spec["factors"]), "universe": "large" if spec.get("large_caps") else "all",
                       "weighting": spec.get("weighting", "equal"), "check": spec.get("check", "daily"),
                       "hold": spec.get("hold", "days")})
    return output


def run_scan(base: Path, research: Path, costs, workers: int = 6, count: int = 80, largest: int = 3,
             names: list[str] | None = None, job=None, smallest: int = 1, require: str | None = None,
             resume: Path | None = None, pool: int = 600, stable: bool = True, prefilter: bool = False,
             calm: bool = False) -> dict:
    """Both stages; returns the summary written next to the screen results. With ``resume`` (an earlier
    scan's folder whose first stage finished) only the second stage runs, on that folder's arrays and rows."""
    from quant_platform.research import daily
    from quant_platform.research.registry import TrialRegistry

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = Path(resume) if resume else research / "scans" / f"{SCAN_VERSION}-{stamp}"
    if job:
        job.update(current="載入行情與因子", force=True)
    data, fp = daily.load(base)
    fingerprint = daily.fingerprint(base)
    started = time.time()
    if resume:
        arrays = load_arrays(folder / "arrays")
        rows = [json.loads(line) for path in sorted(folder.glob("screen-*.jsonl"))
                for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        items = rows
        sizes = sorted({len(row["factors"]) for row in rows}) or [smallest, largest]
        smallest, largest = sizes[0], sizes[-1]
        if job:
            job.update(done=len(rows), total=len(rows) + count, current=f"接著跑第二階段（第一階段 {len(rows):,} 個已完成）",
                       force=True)
    else:
        arrays = prepare(data, fp, costs, folder / "arrays")
        kept_signals = None
        if prefilter:      # 2026-10-16 (GPU): every signal alone first; the sets only of those that beat 0050 alone in
            singles = candidates(names, 1, 1, None)         # some variant over the development period (no later data)
            alone = screen(folder / "arrays", singles, costs, workers, folder / "singles")
            kept_signals = sorted({name for row in alone if row["excess"] > 0 for name in row["factors"]})
            (folder / "prefilter.json").write_text(json.dumps({"singles": len(singles), "kept": kept_signals},
                                                              ensure_ascii=False, indent=1), encoding="utf-8")
            if job:
                job.update(current=f"單一訊號篩選：{len(kept_signals)} 個訊號單獨贏過 0050（開發期）", force=True)
        items = candidates(kept_signals or names, largest, smallest, require)
        if job:
            job.update(done=0, total=len(items) + count, current=f"第一階段：{len(items):,} 個候選（2015-06～2020-09）",
                       force=True)
        rows = screen(folder / "arrays", items, costs, workers, folder,
                      progress=(lambda done: job.update(done=done, current=f"第一階段：{done:,}／{len(items):,}"))
                      if job else None)
    pd.DataFrame([{**row, "factors": "+".join(row["factors"])} for row in rows]).to_parquet(folder / "screen.parquet")
    seeds = earlier_finalists(research)
    chosen = finalists(arrays, rows, costs, count, pool=pool, seeds=seeds, stable=stable, calm=calm)
    effective = effective_trials(folder) if any(folder.glob("screen-*.excess.npy")) else None
    registry = TrialRegistry(research / "trials.jsonl")
    cache: dict = {}
    results = []
    for index, row in enumerate(chosen):
        rule = to_rule(row)
        if job:
            job.update(done=len(items) + index, current=f"第二階段：{rule.name}", force=True)
        record, report = daily.run_trial(rule, base, registry, research / "reports", costs, data, fp, fingerprint, cache,
                                         stamp)
        reasons = record.metrics.get("reasons") or []
        results.append({"trial_id": record.trial_id, "name": rule.name, "screen_excess": row["excess"],
                        "screen_drawdown": row["drawdown"], "full_excess": report["full_period_excess"],
                        "since_2020": report["since_2020"]["excess"], "drawdown": report["strategy"]["max_drawdown"],
                        "reasons": reasons, "tier": daily.tier(record.metrics)[0]})
    twins = daily.grade_half_twins([to_rule(row) for row, item in zip(chosen, results) if item["tier"] in daily.PASSING_TIERS],
                                   base, registry, research / "reports", costs, data, fp, fingerprint, cache, stamp,
                                   f"大規模搜尋 {folder.name}（減碼一半版本）", job)
    summary = {"version": SCAN_VERSION, "stamp": stamp, "development": [str(arrays.days[0]), str(DEV_END)],
               "signals": arrays.names, "sizes": [smallest, largest], "require": require, "calm": calm,
               "prefilter": json.loads((folder / "prefilter.json").read_text(encoding="utf-8"))
               if (folder / "prefilter.json").is_file() else None,
               "selection": "stability" if stable else "excess", "effective_trials": effective,
               "earlier_finalists": len(seeds),
               "candidates": len(items), "passed_screen": sum(passes(row) for row in rows),
               "finalists": len(chosen), "minutes": round((time.time() - started) / 60, 1), "results": results,
               "tiers": dict(Counter(item["tier"] for item in results)), "half_twins": twins}
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
