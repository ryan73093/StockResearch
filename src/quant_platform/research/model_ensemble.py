"""2026-10-12 (全面研究「立即採用」第 4 項): gbm-2.0.0 — an ensemble aimed at the top of the list.

The account earns only on its top 20 (about the top 3% of some 600 eligible stocks), while a regression loss
weighs a mistake in the middle as much as one at the top; and ensembles are the design change that replicates
best (Cakici et al. in 46 markets, Qlib's DoubleEnsemble, nearly every Kaggle finance winner). Same features,
label dates, expanding yearly training and "label ended before the year" rule as gbm-1.4.0 (36 factors, no
monthly revenue), only the model layer changes, so a difference is the model's. Four kinds of member each year:

  (a) the gradient-boosted regression on the next 20 sessions' excess return, 5 seeds, each split seeing a
      random half of the features;
  (b) a gradient-boosted classifier of "in the week's top fifth by the next 20 sessions' return" — the top,
      not the whole order (scikit-learn has no LambdaRank; this is the extreme-sample route of JPX's 5th
      place, without adding a package);
  (c) ridge regression on the same percentiles (the linear baseline Qlib keeps finding hard to beat);
  (d) the 60-session excess-return regression (gbm-1.3.0's label), 3 seeds — horizons combined, not replaced.

Each week every member's predictions become percentiles among the eligible stocks; seeds of one kind are
averaged first, then the four kinds equally. Registered gate (before any rule): over the development years
2016-2020, the ensemble's top-20 gain (their next-20-session return above the eligible median) must be at least
gbm-1.4.0's; otherwise no rule or scan is run on it.
"""

from __future__ import annotations

import numpy as np

KINDS = ("regression", "top_fifth", "ridge", "regression_60")
SEEDS = {"regression": (0, 1, 2, 3, 4), "top_fifth": (0,), "ridge": (0,), "regression_60": (0, 1, 2)}
GATE_YEARS = (2016, 2017, 2018, 2019, 2020)


def _percentile_rows(values: np.ndarray, live: np.ndarray) -> np.ndarray:
    """days × symbols: each day's live values as percentiles (0..1), NaN elsewhere."""
    import pandas as pd

    return pd.DataFrame(np.where(live, values, np.nan)).rank(axis=1, pct=True).to_numpy()


class Ensemble:
    """One year's members; ``predict_days`` scores whole days (percentiles need the day's cross-section)."""

    def __init__(self, members: dict[str, list]) -> None:
        self.members = members

    def kind_scores(self, block: np.ndarray, live: np.ndarray) -> dict[str, np.ndarray]:
        days, symbols, width = block.shape
        flat = block[live]
        output = {}
        for kind, models in self.members.items():
            ranked = []
            for model in models:
                values = np.full((days, symbols), np.nan)
                if len(flat):
                    x = np.nan_to_num(flat, nan=0.5) if kind == "ridge" else flat
                    values[live] = (model.predict_proba(x)[:, 1] if kind == "top_fifth" else model.predict(x))
                ranked.append(_percentile_rows(values, live))
            output[kind] = np.nanmean(np.stack(ranked), axis=0) if ranked else np.full((days, symbols), np.nan)
        return output

    def predict_days(self, block: np.ndarray, live: np.ndarray) -> np.ndarray:
        parts = list(self.kind_scores(block, live).values())
        with np.errstate(invalid="ignore"):
            return np.where(live, np.nanmean(np.stack(parts), axis=0), np.nan)


def fit_year(x: np.ndarray, y20: np.ndarray, rank20: np.ndarray, y60: np.ndarray, rows20: list[int],
             rows60: list[int], params: dict, weights: dict[int, float] | None = None) -> Ensemble:
    """``weights`` (2026-10-28, gbm-2.8.0): a weight per training week (row), every stock of the week the same; none
    = all equal, as before."""
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge

    def flat(rows, labels):
        features = x[rows].reshape(-1, x.shape[2])
        target = labels[rows].reshape(-1)
        weight = np.repeat([weights[row] for row in rows], x.shape[1]) if weights else None
        keep = np.isfinite(target) & np.isfinite(features).any(axis=1)
        features = features[keep].copy()
        features[:, ~np.isfinite(features).any(axis=0)] = 0.5          # a factor with no value yet: the middle
        return features, target[keep], (weight[keep] if weight is not None else None)

    members: dict[str, list] = {kind: [] for kind in KINDS}
    features, target, weight = flat(rows20, y20)
    for seed in SEEDS["regression"]:
        members["regression"].append(HistGradientBoostingRegressor(**{**params, "random_state": seed},
                                                                   max_features=0.5).fit(features, target,
                                                                                         sample_weight=weight))
    ranks, ranks_target, rank_weight = flat(rows20, rank20)
    members["top_fifth"].append(HistGradientBoostingClassifier(**{**params, "random_state": 0})
                                .fit(ranks, (ranks_target >= 0.8).astype(int), sample_weight=rank_weight))
    members["ridge"].append(Ridge(alpha=10.0).fit(np.nan_to_num(features, nan=0.5), target, sample_weight=weight))
    long_features, long_target, long_weight = flat(rows60, y60)
    for seed in SEEDS["regression_60"]:
        members["regression_60"].append(HistGradientBoostingRegressor(**{**params, "random_state": seed},
                                                                      max_features=0.5).fit(long_features, long_target,
                                                                                            sample_weight=long_weight))
    return Ensemble(members)


def gate(models_root, version: str = "gbm-2.0.0", against: str = "gbm-1.4.0", years=GATE_YEARS) -> dict[str, object]:
    """The registered gate: the development years' average top-20 gain of the ensemble against gbm-1.4.0's
    (``years``: 2026-10-28, gbm-2.8.0 is judged on 2021-2025)."""
    import json
    from pathlib import Path

    def average(name: str) -> float | None:
        path = Path(models_root) / name / "meta.json"
        if not path.is_file():
            return None
        found = json.loads(path.read_text(encoding="utf-8")).get("years") or {}
        values = [found[str(year)].get("top20_gain") for year in wanted if str(year) in found]
        values = [value for value in values if value is not None]
        return float(np.mean(values)) if values else None

    wanted = tuple(years)
    mine, theirs = average(version), average(against)
    return {"version": version, "against": against, "years": list(wanted), "top20_gain": mine,
            "against_top20_gain": theirs, "passed": mine is not None and theirs is not None and mine >= theirs}
