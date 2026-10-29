"""R15 stage B (2026-10-06): the supervised baseline — every factor combined by a gradient-boosted model.

One model per year, walk-forward: the model that scores the sessions of year Y is trained only on weekly
samples whose 20-session label ended before Y's first session (no label overlaps the year it scores).
Features: each of the 30 factors as the stock's percentile among the eligible stocks that day (the same
eligibility as the rules); missing chip data stays missing (the trees route it). Label: the percentile
of the stock's next-20-session return (dividends reinvested) among the same stocks — a ranking target,
so market-wide moves do not dominate. The settings are fixed in advance; tuning them would be more trials.

The scores become a factor (``ml_gbm`` in research/daily.py MODEL_FACTORS): a rule holds the top N by
score with the usual keep zone, minimum holding, industry cap and account, and is judged by the same
gate. ``python -m quant_platform.research model`` trains, saves the yearly models and the out-of-sample
diagnostics under ``research/models/<version>/``.
"""

from __future__ import annotations

import hashlib
import json
import os
import pickle
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

MODEL_VERSION = "gbm-1.0.0"
# Each model factor and how its model is trained. 1.0.0 (2026-10-06 00:29): the percentile of the next 20
# sessions' return — its scores ranked well (IC +0.09) but favoured calm, rarely-last stocks and lost to
# 0050 as a top-20 rule. 1.1.0 (2026-10-06): the return itself above the day's median, the day's top and
# bottom 1% clipped, as research_method §9 planned (excess return), so a big winner weighs as much as it gains.
MODELS = {"ml_gbm": {"version": "gbm-1.0.0", "label": "rank"},
          "ml_gbm_excess": {"version": "gbm-1.1.0", "label": "excess"},
          # 2026-10-06: the same as 1.1.0 with the five statement factors (35 features)
          "ml_gbm_statements": {"version": "gbm-1.2.0", "label": "excess"},
          # 2026-10-09 (the AI-methods review, rank 1): the same as 1.2.0, learning the next 60 sessions' excess
          # return. Models that forecast 3 months and longer trade less and kept more after costs (Robeco 2023);
          # the 20-session model turns its holdings over about monthly. Registered once, no horizon search.
          "ml_gbm_60": {"version": "gbm-1.3.0", "label": "excess", "horizon": 60},
          # 2026-10-10 (使用者：月營收是落後指標、新版模型): the same as 1.2.0 without the three monthly revenue
          # factors and with the three trend-quality ones (continuity, intraday momentum, industry residual)
          "ml_gbm_quality": {"version": "gbm-1.4.0", "label": "excess", "features": "no_revenue_quality"},
          # 2026-10-12: the ensemble aimed at the top (research/model_ensemble.py), gbm-1.4.0's features
          "ml_gbm_ens": {"version": "gbm-2.0.0", "label": "ensemble", "features": "no_revenue_quality"},
          # 2026-10-13 (the technical robot, second version, step 1): the same ensemble on the robot's 20 price
          # and volume factors only — a supervised technical ranker the RL robot must at least match
          "ml_tech_ens": {"version": "gbm-2.1.0", "label": "ensemble", "features": "technical"},
          # 2026-10-16: the same ensemble on every rule factor but monthly revenue — the families added since
          # (lending and short selling, notices, lending fees, dividends and new shares, issuance, dealers) included
          # (2026-10-20: frozen at those 57 — the families added later are not in it)
          "ml_gbm_ext": {"version": "gbm-2.2.0", "label": "ensemble", "features": "extended"},
          # 2026-10-20 (使用者：持續優化每一個策略): model G's ensemble on every factor now — the insiders' notices to
          # sell and the buyback programs added (chips.DISCLOSURE_FACTORS); judged against gbm-2.2.0
          "ml_gbm_h": {"version": "gbm-2.3.0", "label": "ensemble", "features": "extended_h"},
          # 2026-10-20: model G refitted every month (its 57 features; _train_ensemble's "refit": "monthly"); judged
          # against gbm-2.2.0
          "ml_gbm_ext_monthly": {"version": "gbm-2.4.0", "label": "ensemble", "features": "extended", "refit": "monthly"},
          # 2026-10-22 (集保因子第四版; research_method §7): model H's ensemble with the four holder-bracket factors too
          # (chips.HOLDER_FACTORS, FinMind's history from 2010) — let the model weigh them; judged against gbm-2.3.0
          "ml_gbm_i": {"version": "gbm-2.5.0", "label": "ensemble", "features": "extended_i"},
          # 2026-10-22 (FinMind 付費資料; research_method §7): model I's ensemble with the paid tables' long-history
          # factors too (margin maintenance, unrestricted loans, block trades); not the brokers' concentration (from
          # 2021 only) nor margin_in_profit (it ranks as margin_safe does); judged against gbm-2.5.0
          "ml_gbm_j": {"version": "gbm-2.6.0", "label": "ensemble", "features": "extended_j"},
          # 2026-10-26 (使用者：付費的資料你練了沒): model J's ensemble with every paid table — the eight banks, the
          # brokers' concentration (from 2021; missing before), day trading, margin cost — not the branches (their
          # download ends about 10/30); judged against gbm-2.6.0 (J as retrained after the 10/26 leak fix)
          "ml_gbm_k": {"version": "gbm-2.7.0", "label": "ensemble", "features": "extended_k"},
          # 2026-10-28 (使用者 10/26：2015 到 2020 和 2020 到現在交易的風氣差很多，短歷史要加重; research_method §7「模型 L」):
          # model K's ensemble and features, each training week weighted by its age — half the weight every
          # ``half_life`` sessions (about three years) before the year (or month) it scores; judged on 2021-2025
          "ml_gbm_l": {"version": "gbm-2.8.0", "label": "ensemble", "features": "extended_k", "half_life": 750},
          # 2026-10-28 (research_method §7「模型 M」; model L failed: older weeks still carry signal): model K's features
          # and the market's state beside them (Gu, Kelly & Xiu 2020 interact stock characteristics with macro
          # predictors; trees find the interactions) - the same value for every stock of a day, as its percentile in
          # the market's own last three years; judged on 2021-2025 against model K
          "ml_gbm_m": {"version": "gbm-2.9.0", "label": "ensemble", "features": "extended_m"},
          # 2026-10-28 (research_method §7「模型 O」): model M with the earnings-news family (fundamentals.EARNINGS_FACTORS:
          # the standardized surprise, its acceleration, the streak of beats; Bernard & Thomas 1989, Novy-Marx 2015) —
          # rule factors outside FACTOR_LABELS, so no earlier version saw them; judged on 2021-2025 against model M
          "ml_gbm_o": {"version": "gbm-2.10.0", "label": "ensemble", "features": "extended_o"},
          # 2026-10-16 (使用者：deep learning 的項目有沒有研究？): a recurrent network over each stock's last 40 sessions
          # (research/model_deep.py)
          "ml_deep": {"version": "dl-1.0.0", "label": "deep"},
          # 2026-10-17 (使用者：深度學習繼續優化; docs/research_dl_exits_review.md D1): the second version
          # (research/model_deep2.py), two registered losses aimed at the top of the list
          "ml_deep2": {"version": "dl-2.0.0", "label": "deep2"},
          "ml_deep2_top": {"version": "dl-2.1.0", "label": "deep2"},
          # 2026-10-20: dl-2.1.0 refitted every month (model_deep2.MONTHLY)
          "ml_deep2_monthly": {"version": "dl-2.6.0", "label": "deep2"},
          # 2026-10-22 (集保因子第五版): dl-2.1.0 seeing model I's features (the holder brackets too)
          "ml_deep2_holders": {"version": "dl-2.7.0", "label": "deep2"},
          # 2026-10-28 (RL 第九版：直接強化學習): dl-2.1.0 learning the portfolio after turnover (model_deep2 "direct")
          "ml_deep_direct": {"version": "dl-4.0.0", "label": "deep2"},
          # 2026-10-20: dl-2.1.0 learning the top tenth of the volatility-adjusted excess (model_deep2.RISK_ADJUSTED)
          "ml_deep2_riskadj": {"version": "dl-2.4.0", "label": "deep2"},
          # 2026-10-20 (the technical robot's third version): dl-2.1.0 on prices and volumes only (model_deep2.TECHNICAL)
          "ml_tech_deep": {"version": "dl-2.5.0", "label": "deep2"},
          # 2026-10-28 (技術分析機器人第四版): dl-2.5.0 as it was, gated on 2021-2025 (model_deep2.RECENT_GATED)
          "ml_tech_deep_r": {"version": "dl-2.5.1", "label": "deep2"},
          # 2026-10-29 (技術分析機器人第七版): dl-2.5.1 learning the excess dl-2.1.0 does not explain (model_deep2.RESIDUAL_OF)
          "ml_tech_resid": {"version": "dl-2.5.2", "label": "deep2"},
          # 2026-10-28 (深度學習舊版本用 2021～2025 重判): the other versions the 2016-2020 gate stopped, as they were
          "ml_deep2_r": {"version": "dl-2.0.1", "label": "deep2"},
          "ml_deep2_wide_r": {"version": "dl-2.2.1", "label": "deep2", "universe": "all"},
          "ml_deep2_riskadj_r": {"version": "dl-2.4.1", "label": "deep2"},
          "ml_deep2_holders_r": {"version": "dl-2.7.1", "label": "deep2"},
          "ml_deep3_r": {"version": "dl-3.0.1", "label": "deep2"},
          # 2026-10-17 (docs/research_dl_exits_review.md D3): dl-2.1.0 with cross-stock attention and a market gate
          "ml_deep3": {"version": "dl-3.0.0", "label": "deep2"},
          # 2026-10-18 (S9-W15): dl-2.1.0 learning from every listed and TPEx stock that trades normally
          "ml_deep2_wide": {"version": "dl-2.2.0", "label": "deep2", "universe": "all"},
          # 2026-10-16 (使用者：深度學習能不能用到交易裡輔助這些策略？): the probability of a 15% fall within 20 sessions
          # (research/model_risk.py) — a risk overlay for rules (DailyRule.risk_veto), not a picking signal
          "dl_risk": {"version": "dl-risk-1.0.0", "label": "deep_risk"},
          # 2026-10-17 (docs/research_dl_exits_review.md R3): a fall against the stock's own swing, used only among
          # stocks of the same volatility and momentum (research/model_risk2.py; DailyRule.risk_veto "r3")
          "dl_risk2": {"version": "dl-risk-2.0.0", "label": "deep_risk2"}}
MONTHLY_REVENUE = ("revenue_yoy", "revenue_yoy_3m", "revenue_accel")
VERSIONS = {spec["version"]: spec for spec in MODELS.values()}
LATEST = "gbm-1.2.0"
HORIZON = 20                # sessions the label looks ahead
STEP = 5                    # weekly training samples
WARMUP = 252                # sessions before the factors are complete
FIRST_YEAR = 2015
PARAMS = {"max_iter": 300, "learning_rate": 0.05, "max_leaf_nodes": 31, "min_samples_leaf": 200,
          "l2_regularization": 1.0, "random_state": 0}


def features() -> tuple[str, ...]:
    """The model's inputs: every rule factor except the 2026-10-10 trend-quality ones, which a later model
    version would add on purpose (a saved version keeps its own list, ``trained_features``)."""
    from quant_platform.research.daily import (
        ALERT_FACTORS, CORPORATE_FACTORS, DEALER_FACTORS, DISCLOSURE_FACTORS, FACTOR_LABELS, INDUSTRY_FACTORS,
        HOLDER_FACTORS, ISSUANCE_FACTORS, LENDING_FACTORS, PAID_FACTORS, PLEDGE_FACTORS, RAW_V2, SHORT_FACTORS,
        TIME_FACTORS, TREND_QUALITY,
    )

    # 2026-10-20: the later families (the insiders' notices, the buybacks, the pledges, the industries) stay out of the
    # shared list too
    later = {**TREND_QUALITY, **RAW_V2, **TIME_FACTORS, **SHORT_FACTORS, **ALERT_FACTORS, **LENDING_FACTORS,
             **CORPORATE_FACTORS, **ISSUANCE_FACTORS, **DEALER_FACTORS, **DISCLOSURE_FACTORS, **PLEDGE_FACTORS,
             **INDUSTRY_FACTORS, **HOLDER_FACTORS, **PAID_FACTORS}
    return tuple(name for name in FACTOR_LABELS if name not in later)


def models_root(history: str | Path) -> Path:
    return Path(history).parent / "models"


def model_dir(history: str | Path, version: str = MODEL_VERSION) -> Path:
    return models_root(history) / version


def eligibility(fp) -> np.ndarray:
    """symbols × sessions: the rules' default eligibility (price, 20-session turnover, listing age)."""
    from quant_platform.research.daily import DailyRule

    rule = DailyRule(name="資格", factors={"trend_200": 1.0})
    with np.errstate(invalid="ignore"):
        return ((fp.panel.close >= rule.min_price) & (fp.turnover_20 >= rule.min_turnover)
                & (fp.age >= rule.min_history))


def _rank(values: np.ndarray) -> np.ndarray:
    """Percentile within each row (NaN stays NaN), rows = sessions."""
    return pd.DataFrame(values).rank(axis=1, pct=True).to_numpy(dtype=np.float32)


def version_features(version: str | None) -> tuple[str, ...]:
    """The features a new version starts with: the shared list, or its own (gbm-1.4.0: every rule factor
    except monthly revenue, the trend-quality ones included)."""
    from quant_platform.research.daily import (
        ALERT_FACTORS,
        CORPORATE_FACTORS,
        DEALER_FACTORS,
        DISCLOSURE_FACTORS,
        FACTOR_LABELS,
        HOLDER_FACTORS,
        INDUSTRY_FACTORS,
        PAID_FACTORS,
        ISSUANCE_FACTORS,
        LENDING_FACTORS,
        PLEDGE_FACTORS,
        RAW_V2,
        SHORT_FACTORS,
        TIME_FACTORS,
    )

    # 2026-10-20: each version's list frozen at what it was trained on — G (2026-10-16) before the insiders' notices,
    # the buybacks, the pledges and the industries; H with the notices, buybacks and pledges
    kind = VERSIONS.get(version, {}).get("features") if version else None
    if kind == "extended":
        return tuple(name for name in FACTOR_LABELS if name not in MONTHLY_REVENUE and name not in DISCLOSURE_FACTORS
                     and name not in PLEDGE_FACTORS and name not in INDUSTRY_FACTORS and name not in HOLDER_FACTORS
                     and name not in PAID_FACTORS)
    if kind == "extended_o":           # 2026-10-28: M's list and the earnings-news family
        from quant_platform.research.fundamentals import EARNINGS_FACTORS

        return tuple(name for name in FACTOR_LABELS if name not in MONTHLY_REVENUE and name not in INDUSTRY_FACTORS
                     and name != "branch_informed_20") + tuple(EARNINGS_FACTORS) + MARKET_FEATURES
    if kind == "extended_m":           # 2026-10-28: K's list and the market's state
        return tuple(name for name in FACTOR_LABELS if name not in MONTHLY_REVENUE and name not in INDUSTRY_FACTORS
                     and name != "branch_informed_20") + MARKET_FEATURES
    if kind == "extended_k":           # 2026-10-26: every factor but the revenue, the industries and the branches
        return tuple(name for name in FACTOR_LABELS if name not in MONTHLY_REVENUE and name not in INDUSTRY_FACTORS
                     and name != "branch_informed_20")
    if kind == "extended_j":           # 2026-10-22: I's list and the paid tables' long-history factors
        return tuple(name for name in FACTOR_LABELS if name not in MONTHLY_REVENUE and name not in INDUSTRY_FACTORS
                     and name not in ("margin_in_profit", "broker_concentration_5", "govbank_net_20", "govbank_dip_buy_20",
                                      "branch_informed_20", "low_daytrade_5"))
    if kind == "extended_i":           # 2026-10-22: H's list and the holder brackets (not the paid tables yet)
        return tuple(name for name in FACTOR_LABELS if name not in MONTHLY_REVENUE and name not in INDUSTRY_FACTORS
                     and name not in PAID_FACTORS)
    if kind == "extended_h":
        return tuple(name for name in FACTOR_LABELS if name not in MONTHLY_REVENUE and name not in INDUSTRY_FACTORS
                     and name not in HOLDER_FACTORS and name not in PAID_FACTORS)
    if version and VERSIONS.get(version, {}).get("features") == "technical":
        from quant_platform.research.tech_rl import TECH_FEATURES

        return tuple(TECH_FEATURES)
    if version and VERSIONS.get(version, {}).get("features") == "no_revenue_quality":
        return tuple(name for name in FACTOR_LABELS if name not in MONTHLY_REVENUE and name not in RAW_V2
                     and name not in TIME_FACTORS and name not in SHORT_FACTORS and name not in ALERT_FACTORS
                     and name not in LENDING_FACTORS and name not in CORPORATE_FACTORS
                     and name not in ISSUANCE_FACTORS and name not in DEALER_FACTORS
                     and name not in DISCLOSURE_FACTORS and name not in PLEDGE_FACTORS
                     and name not in INDUSTRY_FACTORS and name not in HOLDER_FACTORS and name not in PAID_FACTORS)
    return features()


def trained_features(folder: str | Path, version: str | None = None) -> tuple[str, ...]:
    """The features a saved version was trained with (its meta.json); the version's starting list if none."""
    path = Path(folder) / "meta.json"
    if path.is_file():
        try:
            return tuple(json.loads(path.read_text(encoding="utf-8"))["features"])
        except (ValueError, KeyError):
            pass
    return version_features(version)


MARKET_FEATURES = ("market:trend", "market:volatility", "market:breadth", "market:dispersion", "market:maintenance")
MARKET_WINDOW = 750         # sessions a market reading is placed against


def market_state(fp, name: str) -> np.ndarray:
    """2026-10-28 (gbm-2.9.0): one market reading per session, as its percentile among the last MARKET_WINDOW sessions'
    (known that day): TAIEX against its 200-session average, its 20-session volatility, the share of stocks up over 20
    sessions, the spread of the stocks' 20-session returns, the market's margin maintenance ratio (from 2018)."""
    import pandas as pd

    cache = fp.__dict__.setdefault("_market_state", {})
    if name in cache:
        return cache[name]
    market = pd.Series(np.asarray(fp.market, dtype=float))
    prices = fp.panel.filled
    with np.errstate(all="ignore"):
        moved = prices / np.roll(prices, 20, axis=1) - 1
    moved[:, :20] = np.nan
    if name == "market:trend":
        raw = market / market.rolling(200, min_periods=200).mean() - 1
    elif name == "market:volatility":
        raw = market.pct_change(fill_method=None).rolling(20).std()
    elif name == "market:breadth":
        with np.errstate(all="ignore"):
            raw = pd.Series(np.nanmean(np.where(np.isfinite(moved), moved > 0, np.nan), axis=0))
    elif name == "market:dispersion":
        with np.errstate(all="ignore"):
            raw = pd.Series(np.nanstd(moved, axis=0))
    else:                               # market:maintenance
        raw = pd.Series(np.nan, index=range(len(fp.sessions)))
        path = Path(fp.models).parent / "issues" / "maintenance.parquet" if getattr(fp, "models", None) else None
        if path is not None and path.is_file():
            table = pd.read_parquet(path)
            by_day = {str(day)[:10]: float(value) for day, value in zip(table["date"], table["ratio"])}
            raw = pd.Series([by_day.get(day.isoformat(), np.nan) for day in fp.sessions]).ffill(limit=5)
    output = raw.rolling(MARKET_WINDOW, min_periods=120).rank(pct=True).to_numpy(dtype=np.float32)
    cache[name] = output
    return output


def feature_block(fp, columns: list[int], eligible: np.ndarray, names: tuple[str, ...] | None = None) -> np.ndarray:
    """len(columns) × symbols × features (the given names, or every factor now); a market reading (MARKET_FEATURES)
    is the same for every eligible stock of the day."""
    names = names or features()
    block = np.empty((len(columns), len(fp.symbols), len(names)), dtype=np.float32)
    mask = eligible[:, columns].T
    for index, name in enumerate(names):
        if name.startswith("market:"):
            block[:, :, index] = np.where(mask, market_state(fp, name)[columns][:, None], np.nan)
            continue
        values = np.where(mask, fp.matrix(name)[:, columns].T, np.nan)
        block[:, :, index] = _rank(values)
    return block


def label_block(fp, columns: list[int], eligible: np.ndarray, kind: str = "rank", horizon: int = HORIZON) -> np.ndarray:
    """len(columns) × symbols: the next ``horizon`` sessions' return (NaN past the data) as its percentile among
    the day's eligible stocks ("rank"), or above the day's median with the day's 1% tails clipped ("excess")."""
    prices = fp.panel.filled
    later = [column + horizon for column in columns]
    valid = [column < prices.shape[1] for column in later]
    output = np.full((len(columns), len(fp.symbols)), np.nan, dtype=np.float32)
    rows = [index for index, ok in enumerate(valid) if ok]
    if rows:
        start = prices[:, [columns[index] for index in rows]].T
        end = prices[:, [later[index] for index in rows]].T
        with np.errstate(invalid="ignore", divide="ignore"):
            returns = np.where(eligible[:, [columns[index] for index in rows]].T, end / start - 1, np.nan)
        if kind == "rank":
            output[rows] = _rank(returns)
        else:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)          # a day with no stock: all NaN
                low = np.nanpercentile(returns, 1, axis=1, keepdims=True)
                high = np.nanpercentile(returns, 99, axis=1, keepdims=True)
                median = np.nanmedian(returns, axis=1, keepdims=True)
            output[rows] = np.clip(returns, low, high) - median
    return output


def sample_columns(sessions: int) -> list[int]:
    return list(range(WARMUP, sessions, STEP))


def training_columns(fp, samples: list[int], year: int, horizon: int = HORIZON) -> list[int]:
    """The samples whose label window ends before the year's first session."""
    first = next((index for index, day in enumerate(fp.sessions) if day.year >= year), len(fp.sessions))
    return [column for column in samples if column + horizon < first]


def _fit(features_: np.ndarray, labels: np.ndarray):
    from sklearn.ensemble import HistGradientBoostingRegressor

    rows = np.isfinite(labels)
    x = features_[rows].copy()
    # a factor with no value at all in the training years (chip data starts later) is held at the middle:
    # the trees cannot bin an empty column, and a constant one is simply never used
    x[:, ~np.isfinite(x).any(axis=0)] = 0.5
    model = HistGradientBoostingRegressor(**PARAMS)
    model.fit(x, labels[rows])
    return model, int(rows.sum())


def _spearman(left: np.ndarray, right: np.ndarray) -> float | None:
    mask = np.isfinite(left) & np.isfinite(right)
    if mask.sum() < 20:
        return None
    return float(pd.Series(left[mask]).rank().corr(pd.Series(right[mask]).rank()))


def train(fp, out_dir: str | Path, data_fingerprint: str = "", years: list[int] | None = None,
          job=None, label: str = "rank", version: str = MODEL_VERSION, horizon: int = HORIZON,
          months: list[int] | None = None, gate: bool = True) -> dict[str, object]:
    """Fit and save one model per year; returns the out-of-sample diagnostics. A version keeps the feature
    list it was first trained with (a later year of the same version uses the same features)."""
    if label == "deep":                      # 2026-10-16: the recurrent network (research/model_deep.py)
        from quant_platform.research.model_deep import train as train_deep

        return train_deep(fp, out_dir, years, job)
    if label == "deep2":                     # 2026-10-17: the second version (research/model_deep2.py)
        from quant_platform.research.model_deep2 import train as train_deep2

        return train_deep2(fp, out_dir, version, years, job, only_months=months, gate=gate)
    if label == "deep_risk2":                # 2026-10-17: the vol-neutral fall (research/model_risk2.py)
        from quant_platform.research.model_risk2 import train as train_risk2

        return train_risk2(fp, out_dir, years, job)
    if label == "deep_risk":                 # 2026-10-16: the drop probability (research/model_risk.py)
        from quant_platform.research.model_risk import train as train_risk

        return train_risk(fp, out_dir, years, job)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = trained_features(out, version)
    eligible = eligibility(fp)
    samples = sample_columns(len(fp.sessions))
    x = feature_block(fp, samples, eligible, names)
    if label == "ensemble":
        return _train_ensemble(fp, out, x, samples, eligible, names, data_fingerprint, years, job, version)
    y = label_block(fp, samples, eligible, label, horizon)
    ranks = y if label == "rank" else label_block(fp, samples, eligible, "rank", horizon)
    gains = label_block(fp, samples, eligible, "excess", horizon)
    last_year = fp.sessions[-1].year
    years = years or list(range(FIRST_YEAR, last_year + 1))
    diagnostics = {}
    for number, year in enumerate(years):
        if job:
            job.update(done=number, current=f"{year} 年的模型", force=True)
        train_cols = training_columns(fp, samples, year, horizon)
        if not train_cols:
            continue
        positions = [samples.index(column) for column in train_cols]
        model, rows = _fit(x[positions].reshape(-1, x.shape[2]), y[positions].reshape(-1))
        (out / f"{year}.pkl").write_bytes(pickle.dumps(model, protocol=5))
        # out of sample: the year's own weekly samples (labels known by now)
        tests = [index for index, column in enumerate(samples) if fp.sessions[column].year == year]
        ics, tops, fifth_gains, top20_gains = [], [], [], []
        for index in tests:
            if not np.isfinite(ranks[index]).any():
                continue
            live = np.isfinite(x[index]).any(axis=1)
            if live.sum() < 20:
                continue
            predicted = np.full(len(fp.symbols), np.nan)
            predicted[live] = model.predict(x[index][live])
            ic = _spearman(predicted, ranks[index])
            if ic is not None:
                ics.append(ic)
            ranked = np.isfinite(predicted) & np.isfinite(ranks[index])
            if ranked.sum() >= 50:
                cut = np.quantile(predicted[ranked], 0.8)
                tops.append(float(np.nanmean(ranks[index][ranked & (predicted >= cut)]) - np.nanmean(ranks[index][ranked])))
                fifth_gains.append(float(np.nanmean(gains[index][ranked & (predicted >= cut)])
                                         - np.nanmean(gains[index][ranked])))
                best = np.argsort(-np.where(ranked, predicted, -np.inf))[:20]
                top20_gains.append(float(np.nanmean(gains[index][best]) - np.nanmean(gains[index][ranked])))
        diagnostics[str(year)] = {
            "train_rows": rows, "train_until": fp.sessions[train_cols[-1] + horizon].isoformat(),
            "weeks": len(ics), "ic": round(float(np.mean(ics)), 4) if ics else None,
            "ic_positive": round(float(np.mean([value > 0 for value in ics])), 3) if ics else None,
            "top_fifth_rank_gap": round(float(np.mean(tops)), 4) if tops else None,
            # 20-session return above the day's median (clipped), the top fifth and the top 20 by score
            "top_fifth_gain": round(float(np.mean(fifth_gains)), 4) if fifth_gains else None,
            "top20_gain": round(float(np.mean(top20_gains)), 4) if top20_gains else None,
        }
    meta_path = out / "meta.json"
    earlier = {}
    if meta_path.is_file():                  # a year added later keeps the other years' diagnostics
        try:
            earlier = json.loads(meta_path.read_text(encoding="utf-8")).get("years") or {}
        except ValueError:
            earlier = {}
    meta = {"version": version, "label": label, "params": PARAMS, "features": list(names), "horizon": horizon,
            "step": STEP, "data": data_fingerprint, "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "years": dict(sorted({**earlier, **diagnostics}.items()))}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def _top20(predicted: np.ndarray, ranks: np.ndarray, gains: np.ndarray) -> tuple[float | None, float | None]:
    """(IC, top-20 gain) of one week's scores against its 20-session outcome."""
    ic = _spearman(predicted, ranks)
    ranked = np.isfinite(predicted) & np.isfinite(ranks)
    if ranked.sum() < 50:
        return ic, None
    best = np.argsort(-np.where(ranked, predicted, -np.inf))[:20]
    return ic, float(np.nanmean(gains[best]) - np.nanmean(gains[ranked]))


def _train_ensemble(fp, out: Path, x: np.ndarray, samples: list[int], eligible: np.ndarray, names, data_fingerprint,
                    years, job, version) -> dict[str, object]:
    """gbm-2.0.0: the yearly ensembles (research/model_ensemble.py) with the ensemble's and every member
    kind's out-of-sample IC and top-20 gain."""
    from quant_platform.research.model_ensemble import KINDS, fit_year

    y20 = label_block(fp, samples, eligible, "excess", HORIZON)
    rank20 = label_block(fp, samples, eligible, "rank", HORIZON)
    y60 = label_block(fp, samples, eligible, "excess", 60)
    years = years or list(range(FIRST_YEAR, fp.sessions[-1].year + 1))
    diagnostics = {}
    # 2026-10-20 (gbm-2.4.0): refitted every month — each month's ensemble learns the labels ended before the month's
    # first session and is kept as monthly/<the last session before it>.pkl (scores() takes the latest usable model)
    monthly = VERSIONS.get(version, {}).get("refit") == "monthly"
    for number, year in enumerate(years):
        if job:
            job.update(done=number, current=f"{year} 年的集成模型（{len(KINDS)} 類成員）", force=True)
        firsts = ({month: next(index for index, day in enumerate(fp.sessions) if day.year == year and day.month == month)
                   for month in sorted({day.month for day in fp.sessions if day.year == year})} if monthly else {})
        periods = list(firsts.items()) if monthly else [(None, None)]
        fitted: dict[object, object] = {}
        cols20: list[int] = []
        for month, first in periods:
            if month is None:
                cols20, cols60 = training_columns(fp, samples, year, HORIZON), training_columns(fp, samples, year, 60)
            else:
                cols20 = [column for column in samples if column + HORIZON < first]
                cols60 = [column for column in samples if column + 60 < first]
            if not cols20 or not cols60:
                continue
            rows20 = [samples.index(column) for column in cols20]
            rows60 = [samples.index(column) for column in cols60]
            half_life = VERSIONS.get(version, {}).get("half_life")
            ages = None
            if half_life:                    # 2026-10-28 (gbm-2.8.0): the older weeks count less
                now = first if first is not None else next(index for index, day in enumerate(fp.sessions) if day.year == year)
                ages = {row: 0.5 ** ((now - samples[row]) / half_life) for row in set(rows20) | set(rows60)}
            ensemble = fit_year(x, y20, rank20, y60, rows20, rows60, PARAMS, weights=ages)
            if month is None:
                (out / f"{year}.pkl").write_bytes(pickle.dumps(ensemble, protocol=5))
            else:
                (out / MONTHLY).mkdir(parents=True, exist_ok=True)
                (out / MONTHLY / f"{fp.sessions[first - 1].isoformat()}.pkl").write_bytes(pickle.dumps(ensemble, protocol=5))
                if job:
                    job.update(current=f"{year} 年 {month} 月的集成模型（每月重訓）", force=True)
            fitted[month] = ensemble
        if not fitted:
            continue
        tests = [index for index, column in enumerate(samples) if fp.sessions[column].year == year
                 and (None if not monthly else fp.sessions[column].month) in fitted]
        found: dict[str, list] = {"ensemble": [], **{kind: [] for kind in KINDS}}
        for index in tests:
            ensemble = fitted[None if not monthly else fp.sessions[samples[index]].month]
            if not np.isfinite(rank20[index]).any():
                continue
            live = np.isfinite(x[index]).any(axis=1)
            if live.sum() < 20:
                continue
            kinds = ensemble.kind_scores(x[index][None], live[None])
            with np.errstate(invalid="ignore"):
                whole = np.nanmean(np.stack([value[0] for value in kinds.values()]), axis=0)
            found["ensemble"].append(_top20(whole, rank20[index], y20[index]))
            for kind, value in kinds.items():
                found[kind].append(_top20(value[0], rank20[index], y20[index]))

        def summary(pairs):
            ics = [ic for ic, _gain in pairs if ic is not None]
            gains = [gain for _ic, gain in pairs if gain is not None]
            return {"ic": round(float(np.mean(ics)), 4) if ics else None,
                    "top20_gain": round(float(np.mean(gains)), 4) if gains else None}

        diagnostics[str(year)] = {"train_rows": int(np.isfinite(y20[rows20]).sum()),
                                  "train_until": fp.sessions[cols20[-1] + HORIZON].isoformat(),
                                  "weeks": len(found["ensemble"]), **summary(found["ensemble"]),
                                  "kinds": {kind: summary(found[kind]) for kind in KINDS}}
    meta = {"version": version, "label": "ensemble", "params": PARAMS, "features": list(names), "horizon": HORIZON,
            "kinds": list(KINDS), "step": STEP, "data": data_fingerprint,
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds"), "years": diagnostics}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def train_year(history: str | Path, version: str, year: int, job=None, months: list[int] | None = None) -> dict[str, object]:
    """The model that scores ``year``, trained on everything up to that year's first session (the yearly
    retrain, 2026-10-06); the version's other years stay as they are. ``months``: a monthly deep version's new
    month only (2026-10-21)."""
    from quant_platform.research.daily import (
        RECENT_START,
        ChipStore,
        FactorPanel,
        Industries,
        load_industries,
        market_closes,
    )
    from quant_platform.research.stock_rules import WARMUP_YEARS, Panel, load_stock_data, stock_fingerprint

    history = Path(history)
    first = RECENT_START.year - WARMUP_YEARS
    data = load_stock_data(history, first, year)
    panel = Panel(data)
    fp = FactorPanel(panel, Industries(load_industries(history)), ChipStore(history, panel.sessions, panel.symbols, panel.close),
                     market_closes(history, panel.sessions))
    fingerprint = stock_fingerprint(history, first, year)
    return train(fp, model_dir(history, version), fingerprint, [year], job=job, label=VERSIONS[version]["label"],
                 version=version, horizon=VERSIONS[version].get("horizon", HORIZON), months=months)


MONTHLY = "monthly"        # 2026-10-16: the monthly retrains, ``monthly/<cutoff>.pkl`` (research/rolling.py)


def train_until(fp, out_dir: str | Path, cutoff, label: str, version: str, horizon: int = HORIZON) -> dict[str, object]:
    """2026-10-16 (使用者：模型都不用更新？): one model learning every weekly sample whose label ended by ``cutoff``
    (the last session on or before it), saved as ``monthly/<cutoff>.pkl``; ``scores`` uses it for the sessions after
    the cutoff. The yearly models stay as they are."""
    out = Path(out_dir)
    target = out / MONTHLY
    target.mkdir(parents=True, exist_ok=True)
    names = trained_features(out, version)
    eligible = eligibility(fp)
    samples = sample_columns(len(fp.sessions))
    last = max(index for index, day in enumerate(fp.sessions) if day <= cutoff)

    def rows(span: int) -> list[int]:
        return [index for index, column in enumerate(samples) if column + span <= last]

    x = feature_block(fp, samples, eligible, names)
    if label == "ensemble":
        from quant_platform.research.model_ensemble import fit_year

        y20 = label_block(fp, samples, eligible, "excess", HORIZON)
        rank20 = label_block(fp, samples, eligible, "rank", HORIZON)
        y60 = label_block(fp, samples, eligible, "excess", 60)
        model = fit_year(x, y20, rank20, y60, rows(HORIZON), rows(60), PARAMS)
        count = len(rows(HORIZON))
    else:
        y = label_block(fp, samples, eligible, label, horizon)
        chosen = rows(horizon)
        model, count = _fit(x[chosen].reshape(-1, x.shape[2]), y[chosen].reshape(-1))
    (target / f"{cutoff.isoformat()}.pkl").write_bytes(pickle.dumps(model, protocol=5))
    return {"version": version, "cutoff": cutoff.isoformat(), "samples": count,
            "label_until": fp.sessions[last].isoformat(), "trained_at": datetime.now(UTC).isoformat(timespec="seconds")}


def _model_files(folder: Path) -> dict[object, Path]:
    """The models a version keeps: the yearly ones (key: the year) and the monthly ones (key: the cutoff date)."""
    from datetime import date as _date

    output: dict[object, Path] = {}
    if not folder.is_dir():
        return output
    for path in folder.glob("*.pkl"):
        if path.stem.isdigit():
            output[int(path.stem)] = path
    for path in (folder / MONTHLY).glob("*.pkl") if (folder / MONTHLY).is_dir() else []:
        try:
            output[_date.fromisoformat(path.stem)] = path
        except ValueError:
            continue
    return output


def _learned_until(key):
    """The last day a model's labels could reach: a yearly model of Y, the day before Y; a monthly one, its cutoff."""
    from datetime import date as _date, timedelta as _timedelta

    return key if isinstance(key, _date) else _date(key, 1, 1) - _timedelta(days=1)


def digest(out_dir: str | Path) -> str:
    """The saved models' hash (part of a model rule's trial input)."""
    folder = Path(out_dir)
    output = hashlib.sha256()
    monthly = sorted((folder / MONTHLY).glob("*.pkl")) if (folder / MONTHLY).is_dir() else []
    for path in (sorted(folder.glob("*.pkl")) if folder.is_dir() else []) + monthly:
        output.update(path.name.encode())
        output.update(hashlib.sha256(path.read_bytes()).digest())
    # 2026-10-28: the deep models keep networks and scores (.pt, .npz, and their monthly refits), not pickles — they
    # had been left out, so the risk model's refit to 8/31 changed September's scores under an unchanged input hash
    # and the registry kept answering with the old result (research_method §7「重現性」). A folder of pickles only
    # hashes as before.
    deep = [path for pattern in ("*.pt", "*.npz") for place in (folder, folder / MONTHLY) if place.is_dir()
            for path in place.glob(pattern)]
    for path in sorted(deep, key=lambda path: path.relative_to(folder).as_posix()):
        output.update(path.relative_to(folder).as_posix().encode())
        output.update(hashlib.sha256(path.read_bytes()).digest())
    return output.hexdigest()


SCORE_CACHE = "score-cache"          # 2026-10-29: a block's scores, by the digest of everything they come from
SCORE_CACHE_DAYS = 21                 # a block not used for this long is removed (computed again when needed)


def _code_digest() -> bytes:
    """The code a block's scores come from (this file, the ensemble's) and the libraries computing them."""
    cached = globals().get("_CODE_DIGEST")
    if cached is None:
        import sklearn

        from quant_platform.research import model_ensemble

        output = hashlib.blake2b(digest_size=20)
        for module in (__file__, model_ensemble.__file__):
            output.update(Path(module).read_bytes())
        output.update(f"{np.__version__} {pd.__version__} {sklearn.__version__}".encode())
        cached = globals()["_CODE_DIGEST"] = output.digest()
    return cached


def block_key(fp, part: list[int], eligible: np.ndarray, names: tuple[str, ...], model_digest: bytes) -> str:
    """2026-10-29 (S9-W39, 使用者：是你的程式太肥；不要為了省效能最後又給我錯的資料): the digest of everything a block of
    sessions' scores is computed from — the model file, the code and libraries, the stocks and sessions, the features'
    values before they are ranked and the eligible stocks. Equal digests: the same inputs, the same scores."""
    output = hashlib.blake2b(digest_size=20)
    output.update(model_digest)
    output.update(_code_digest())
    output.update(json.dumps([list(names), list(fp.symbols), [fp.sessions[column].isoformat() for column in part]],
                             ensure_ascii=False).encode())
    output.update(np.ascontiguousarray(eligible[:, part]).tobytes())
    for name in names:
        values = market_state(fp, name)[part] if name.startswith("market:") else fp.matrix(name)[:, part]
        array = np.ascontiguousarray(values)
        output.update(f"{name} {array.dtype} {array.shape}".encode())
        output.update(array.tobytes())
    return output.hexdigest()


def _cached_block(folder: Path, key: str) -> np.ndarray | None:
    path = folder / SCORE_CACHE / f"{key}.npy"
    try:
        found = np.load(path, allow_pickle=False)
    except (OSError, ValueError):
        return None
    try:
        os.utime(path)                      # in use: not removed as unused
    except OSError:
        pass
    return found


def _keep_block(folder: Path, key: str, values: np.ndarray) -> None:
    from quant_platform.research import files

    target = folder / SCORE_CACHE
    try:
        target.mkdir(parents=True, exist_ok=True)
        partial = target / f"{key}.{os.getpid()}.partial.npy"
        np.save(partial, values, allow_pickle=False)
        files.replace(partial, target / f"{key}.npy")
    except OSError:
        pass                                 # not kept: computed again next time


def _drop_unused_blocks(folder: Path) -> None:
    import time as _time

    limit = _time.time() - SCORE_CACHE_DAYS * 86400
    for path in (folder / SCORE_CACHE).glob("*.npy") if (folder / SCORE_CACHE).is_dir() else []:
        try:
            if path.stat().st_mtime < limit:
                path.unlink()
        except OSError:
            continue


def scores(fp, out_dir: str | Path) -> np.ndarray:
    """sessions × symbols: each session scored by the model of its year (the latest model after the last
    trained year); NaN where the stock is not eligible or no model exists yet."""
    folder = Path(out_dir)
    if (folder / "deep.npz").is_file():      # 2026-10-16: the deep model keeps its scores, not pickled models
        meta = folder / "meta.json"
        if meta.is_file() and json.loads(meta.read_text(encoding="utf-8")).get("label") == "deep2":
            from quant_platform.research.model_deep2 import scores as deep2_scores

            return deep2_scores(fp, folder)
        from quant_platform.research.model_deep import scores as deep_scores

        return deep_scores(fp, folder)
    output = np.full((len(fp.sessions), len(fp.symbols)), np.nan, dtype=np.float32)
    models = _model_files(folder)
    if not models:
        return output
    eligible = eligibility(fp)
    names = trained_features(folder, folder.name)  # gbm-1.0.0 and 1.1.0 learned 30 factors; more came later
    by_year: dict[object, list[int]] = {}
    for column, day in enumerate(fp.sessions):
        # the model whose labels reach latest, all before this session (a yearly one, or a monthly retrain)
        usable = [key for key in models if _learned_until(key) < day]
        if usable:
            by_year.setdefault(max(usable, key=_learned_until), []).append(column)
    # 2026-10-29 (S9-W39): every run had scored every session since 2013 again — about 80 seconds a model, in the
    # after-close orders, the forward record, the board and every research step. A block's scores are kept under the
    # digest of everything they come from (``block_key``) and taken again only for the same digest; a new session, a
    # new stock, a changed value or a changed model or code is another digest and is computed.
    _drop_unused_blocks(folder)
    for year, columns in by_year.items():
        raw = models[year].read_bytes()
        model, model_digest = None, hashlib.blake2b(raw, digest_size=20).digest()
        for start in range(0, len(columns), 60):
            part = columns[start: start + 60]
            key = block_key(fp, part, eligible, names, model_digest)
            kept = _cached_block(folder, key)
            if kept is not None and kept.shape == (len(part), len(fp.symbols)):
                output[part] = kept
                continue
            if model is None:
                model = pickle.loads(raw)
            block = feature_block(fp, part, eligible, names)
            live = np.isfinite(block).any(axis=2) & eligible[:, part].T
            if hasattr(model, "predict_days"):             # gbm-2.0.0: the day's cross-section at once
                output[part] = model.predict_days(block, live)
            else:
                flat = block[live]
                if len(flat):
                    predicted = np.full(live.shape, np.nan, dtype=np.float32)
                    predicted[live] = model.predict(flat)
                    output[part] = predicted
            _keep_block(folder, key, output[part])
    return output
