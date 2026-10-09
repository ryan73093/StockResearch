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
          "ml_gbm_60": {"version": "gbm-1.3.0", "label": "excess", "horizon": 60}}
VERSIONS = {spec["version"]: spec for spec in MODELS.values()}
LATEST = "gbm-1.2.0"
HORIZON = 20                # sessions the label looks ahead
STEP = 5                    # weekly training samples
WARMUP = 252                # sessions before the factors are complete
FIRST_YEAR = 2015
PARAMS = {"max_iter": 300, "learning_rate": 0.05, "max_leaf_nodes": 31, "min_samples_leaf": 200,
          "l2_regularization": 1.0, "random_state": 0}


def features() -> tuple[str, ...]:
    from quant_platform.research.daily import FACTOR_LABELS

    return tuple(FACTOR_LABELS)


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


def trained_features(folder: str | Path) -> tuple[str, ...]:
    """The features a saved version was trained with (its meta.json); the current list if none."""
    path = Path(folder) / "meta.json"
    if path.is_file():
        try:
            return tuple(json.loads(path.read_text(encoding="utf-8"))["features"])
        except (ValueError, KeyError):
            pass
    return features()


def feature_block(fp, columns: list[int], eligible: np.ndarray, names: tuple[str, ...] | None = None) -> np.ndarray:
    """len(columns) × symbols × features (the given names, or every factor now)."""
    names = names or features()
    block = np.empty((len(columns), len(fp.symbols), len(names)), dtype=np.float32)
    mask = eligible[:, columns].T
    for index, name in enumerate(names):
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
          job=None, label: str = "rank", version: str = MODEL_VERSION, horizon: int = HORIZON) -> dict[str, object]:
    """Fit and save one model per year; returns the out-of-sample diagnostics. A version keeps the feature
    list it was first trained with (a later year of the same version uses the same features)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = trained_features(out)
    eligible = eligibility(fp)
    samples = sample_columns(len(fp.sessions))
    x = feature_block(fp, samples, eligible, names)
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


def train_year(history: str | Path, version: str, year: int, job=None) -> dict[str, object]:
    """The model that scores ``year``, trained on everything up to that year's first session (the yearly
    retrain, 2026-10-06); the version's other years stay as they are."""
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
                 version=version, horizon=VERSIONS[version].get("horizon", HORIZON))


def digest(out_dir: str | Path) -> str:
    """The saved models' hash (part of a model rule's trial input)."""
    folder = Path(out_dir)
    output = hashlib.sha256()
    for path in sorted(folder.glob("*.pkl")) if folder.is_dir() else []:
        output.update(path.name.encode())
        output.update(hashlib.sha256(path.read_bytes()).digest())
    return output.hexdigest()


def scores(fp, out_dir: str | Path) -> np.ndarray:
    """sessions × symbols: each session scored by the model of its year (the latest model after the last
    trained year); NaN where the stock is not eligible or no model exists yet."""
    folder = Path(out_dir)
    output = np.full((len(fp.sessions), len(fp.symbols)), np.nan, dtype=np.float32)
    models = {int(path.stem): path for path in folder.glob("*.pkl")} if folder.is_dir() else {}
    if not models:
        return output
    eligible = eligibility(fp)
    names = trained_features(folder)              # gbm-1.0.0 and 1.1.0 learned 30 factors; more came later
    by_year: dict[int, list[int]] = {}
    for column, day in enumerate(fp.sessions):
        usable = [year for year in models if year <= day.year]
        if usable:
            by_year.setdefault(max(usable), []).append(column)
    for year, columns in by_year.items():
        model = pickle.loads(models[year].read_bytes())
        for start in range(0, len(columns), 60):
            part = columns[start: start + 60]
            block = feature_block(fp, part, eligible, names)
            live = np.isfinite(block).any(axis=2) & eligible[:, part].T
            flat = block[live]
            if len(flat):
                predicted = np.full(live.shape, np.nan, dtype=np.float32)
                predicted[live] = model.predict(flat)
                output[part] = predicted
    return output
