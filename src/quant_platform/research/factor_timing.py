"""Factor momentum: which factors to use, decided by time (2026-10-14; 使用者：因子裡面有考慮到時間的部分嗎？).

Every rule so far uses the same factors on every day. Here the factors take turns: each base factor's recent
record is the spread between the stocks it ranks in its top fifth and those in its bottom fifth over the next
20 sessions (dividend-adjusted, among the eligible stocks). On a session the record counts only spreads already
realised — formed at least 20 sessions earlier — over about a year (250 sessions, 120 at least). The score is
the average percentile, among the eligible stocks, of the TOP factors with the best positive records; with no
positive record the score is unknown. Factor momentum (Gupta & Kelly 2019; Ehsani & Linnainmaa 2022): factors
that paid lately tend to keep paying. Nothing is fitted — the window, the horizon and TOP are fixed here.

The base factors: every rule factor except monthly revenue (the owner counts it as lagging), market value and
the trained models' scores.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

HORIZON = 20              # sessions of the forward return a factor is judged on
WINDOW, LEAST = 250, 120  # sessions of realised spreads that make a record
TOP = 5                   # factors used on a session
LEFT_OUT = ("revenue_yoy", "revenue_yoy_3m", "revenue_accel", "market_cap")


def base_factors() -> list[str]:
    from quant_platform.research.daily import FACTOR_LABELS

    return [name for name in FACTOR_LABELS if name not in LEFT_OUT]


def _percentiles(values: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    """symbols × sessions percentiles among the eligible stocks with a value."""
    masked = np.where(eligible & np.isfinite(values), values, np.nan)
    return pd.DataFrame(masked).rank(axis=0, pct=True).to_numpy(dtype=np.float32)


def records(fp, names: list[str] | None = None) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """(sessions × factors: each factor's realised record on each session, factor -> its percentiles)."""
    from quant_platform.research.model import eligibility

    names = names or base_factors()
    key = ("__factor_records__", tuple(names), HORIZON, WINDOW, LEAST)
    if key in fp._cache:
        return fp._cache[key]
    eligible = eligibility(fp)
    prices = fp.panel.filled
    forward = np.full(prices.shape, np.nan, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        forward[:, :-HORIZON] = prices[:, HORIZON:] / prices[:, :-HORIZON] - 1
    forward = np.where(eligible, forward, np.nan)
    spreads, ranks = {}, {}
    for name in names:
        ranked = _percentiles(fp.matrix(name), eligible)
        ranks[name] = ranked
        top, bottom = ranked >= 0.8, ranked <= 0.2
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            high = np.nanmean(np.where(top, forward, np.nan), axis=0)
            low = np.nanmean(np.where(bottom, forward, np.nan), axis=0)
        spreads[name] = high - low                         # known HORIZON sessions after its session
    frame = pd.DataFrame(spreads)
    realised = frame.shift(HORIZON).rolling(WINDOW, min_periods=LEAST).mean()
    fp._cache[key] = (realised, ranks)
    fp._cache[("__factor_spreads__",) + key[1:]] = frame.shift(HORIZON)
    return realised, ranks


def known_spreads(fp, names: list[str]) -> pd.DataFrame:
    """sessions × factors: each factor's top-fifth-minus-bottom-fifth spread as known on each session (the spread of
    the session HORIZON sessions before, whose forward return has just been realised)."""
    key = ("__factor_spreads__", tuple(names), HORIZON, WINDOW, LEAST)
    if key not in fp._cache:
        records(fp, names)
    return fp._cache[key]


def scores(fp, names: list[str] | None = None) -> np.ndarray:
    """sessions × symbols: the mean percentile of the session's TOP factors by record (positive records only)."""
    realised, ranks = records(fp, names)
    names = list(realised.columns)
    output = np.full((len(fp.sessions), len(fp.symbols)), np.nan, dtype=np.float32)
    values = realised.to_numpy()
    for position in range(len(fp.sessions)):
        row = values[position]
        usable = np.flatnonzero(np.isfinite(row) & (row > 0))
        if usable.size == 0:
            continue
        picked = usable[np.argsort(-row[usable], kind="stable")][:TOP]
        with np.errstate(invalid="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            output[position] = np.nanmean(np.stack([ranks[names[index]][:, position] for index in picked]), axis=0)
    return output


def chosen(fp, names: list[str] | None = None) -> pd.DataFrame:
    """sessions × rank: which factors were used on each session (for the report)."""
    realised, _ranks = records(fp, names)
    rows = []
    for position, row in enumerate(realised.to_numpy()):
        usable = [index for index in np.argsort(-np.nan_to_num(row, nan=-np.inf)) if np.isfinite(row[index]) and row[index] > 0]
        rows.append([realised.columns[index] for index in usable[:TOP]])
    return pd.DataFrame(rows, index=list(fp.sessions))
