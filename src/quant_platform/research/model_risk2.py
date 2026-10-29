"""The vol-neutral drop-risk model (2026-10-17; 使用者：繼續分析; docs/research_dl_exits_review.md §3.2 R3).

dl-risk-1.0.0 predicted a 15% fall well (AUC 0.738) but mostly learned volatility: the stocks most likely to fall 15%
are the ones most likely to rise 15%, and vetoing its riskiest tenth cut the winners (16 rules worse). This model
learns a fall measured against the stock's own swing — the lowest close of the next 20 sessions at least ``SIGMAS``
times its usual 20-session move below today's close (60-session daily standard deviation × √20) — and is used only
among stocks of the same volatility and momentum: the veto (risk_veto "r3") takes no new buy in the top tenth of
its group, a group being one volatility tenth × one 6-month momentum fifth of the day's eligible stocks.

Network and walk-forward as dl-risk-1.0.0 (research/model_risk.py: the GRU over 40 sessions, three seeds, the
year's model learning only samples that ended before the year).

Registered gate (written before the results, research_method §7): over 2016–2020 the model's AUC inside each
volatility tenth, averaged, at least 0.55; and the stocks it would veto (the top tenth of their group) gained less
than their groups' average over the next 20 sessions in at least 4 of the 5 years — it vetoes losers, not only
stocks that swing. Only then do rules use it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from quant_platform.research import files  # 2026-10-29: files written whole while read

VERSION = "dl-risk-2.0.0"
SIGMAS = 2.0
VETO_SHARE = 0.10
GATE_YEARS = (2016, 2017, 2018, 2019, 2020)
GATE_AUC = 0.55
GATE_COUNT = 4
AUC_ROWS = 20               # a volatility tenth with fewer known stocks that session gives no AUC


def labels(fp, columns: list[int], eligible: np.ndarray, horizon: int) -> np.ndarray:
    """len(columns) × symbols: 1 when the lowest close of the next ``horizon`` sessions is at least SIGMAS × the
    stock's 60-session daily standard deviation × √horizon below this close, 0 when not, NaN past the data, for a
    stock not eligible or without a standard deviation."""
    prices = fp.panel.filled
    sigma = -fp.matrix("low_volatility_60")                   # stored negated: the daily standard deviation
    output = np.full((len(columns), len(fp.symbols)), np.nan, dtype=np.float32)
    for slot, column in enumerate(columns):
        if column + horizon >= prices.shape[1]:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            low = np.nanmin(prices[:, column + 1: column + horizon + 1], axis=1) / prices[:, column] - 1
            bar = -SIGMAS * sigma[:, column] * np.sqrt(horizon)
        known = eligible[:, column] & np.isfinite(low) & np.isfinite(bar) & (sigma[:, column] > 0)
        output[slot] = np.where(known, (low <= bar).astype(np.float32), np.nan)
    return output


def groups(volatility: np.ndarray, momentum: np.ndarray, known: np.ndarray) -> np.ndarray:
    """symbols: the group of each known stock — its volatility tenth × 5 + its momentum fifth among the known
    stocks of the day (−1 for the others)."""
    import pandas as pd

    output = np.full(len(volatility), -1, dtype=int)
    rows = np.flatnonzero(known & np.isfinite(volatility) & np.isfinite(momentum))
    if rows.size < 50:
        return output
    def bucket(values: np.ndarray, count: int) -> np.ndarray:        # equal-sized slices of the day's order
        order = pd.Series(values).rank(method="first").to_numpy() - 1
        return np.minimum((order * count / len(values)).astype(int), count - 1)

    tenth, fifth = bucket(volatility[rows], 10), bucket(momentum[rows], 5)
    output[rows] = tenth * 5 + fifth
    return output


def top_of_groups(score: np.ndarray, group: np.ndarray, share: float = VETO_SHARE) -> np.ndarray:
    """symbols: True for the ``share`` of each group with the highest score (at least one; a group of fewer than 5
    stocks, or whose scores are all the same, none)."""
    output = np.zeros(len(score), dtype=bool)
    for label in np.unique(group[group >= 0]):
        rows = np.flatnonzero((group == label) & np.isfinite(score))
        if rows.size < 5 or np.all(score[rows] == score[rows][0]):
            continue
        count = max(1, round(share * rows.size))
        output[rows[np.argsort(-score[rows], kind="stable")[:count]]] = True
    return output


def gate_check(diagnostics: dict) -> dict[str, object]:
    years = [diagnostics.get(str(year)) or {} for year in GATE_YEARS]
    aucs = [item.get("auc_within_volatility") for item in years if item.get("auc_within_volatility") is not None]
    losers = sum(1 for item in years if item.get("vetoed_excess") is not None and item["vetoed_excess"] < 0)
    auc = float(np.mean(aucs)) if len(aucs) == len(GATE_YEARS) else None
    return {"version": VERSION, "years": list(GATE_YEARS), "auc_within_volatility": auc, "years_vetoed_lost": losers,
            "passed": auc is not None and auc >= GATE_AUC and losers >= GATE_COUNT}


def diagnose(fp, folder: Path) -> dict[str, dict[str, float | None]]:
    """Per year on the weekly samples: the AUC inside each volatility tenth (averaged), and the average next-20-session
    gain of the vetoed stocks above their group's average."""
    from quant_platform.research.model import HORIZON, eligibility, label_block, sample_columns
    from quant_platform.research.model_deep import scores as kept_scores
    from quant_platform.research.model_risk import auc

    score = kept_scores(fp, folder)
    eligible = eligibility(fp)
    columns = sample_columns(len(fp.sessions))
    truth = labels(fp, columns, eligible, HORIZON)
    gains = label_block(fp, columns, eligible, "excess", HORIZON)
    volatility = -fp.matrix("low_volatility_60")
    momentum = fp.matrix("momentum_6")
    by_year: dict[str, dict[str, list[float]]] = {}
    for slot, column in enumerate(columns):
        today = score[column]
        known = eligible[:, column] & np.isfinite(today)
        if known.sum() < 50:
            continue
        item = by_year.setdefault(str(fp.sessions[column].year), {"auc": [], "vetoed": []})
        group = groups(volatility[:, column], momentum[:, column], known)
        tenth = np.where(group >= 0, group // 5, -1)
        for label in range(10):
            rows = np.flatnonzero((tenth == label) & np.isfinite(truth[slot]))
            value = auc(today[rows], truth[slot][rows]) if rows.size >= AUC_ROWS else None
            if value is not None:
                item["auc"].append(value)
        vetoed = top_of_groups(np.where(known, today, np.nan), group)
        outcome = gains[slot]
        differences = []
        for label in np.unique(group[vetoed]):
            rows = np.flatnonzero((group == label) & np.isfinite(outcome))
            picked = rows[vetoed[rows]]
            if picked.size and rows.size:
                differences.append(float(outcome[picked].mean() - outcome[rows].mean()))
        if differences:
            item["vetoed"].append(float(np.mean(differences)))
    return {year: {"auc_within_volatility": round(float(np.mean(item["auc"])), 4) if item["auc"] else None,
                   "vetoed_excess": round(float(np.mean(item["vetoed"])), 5) if item["vetoed"] else None}
            for year, item in sorted(by_year.items())}


def train(fp, out_dir: str | Path, years: list[int] | None = None, job=None) -> dict[str, object]:
    """dl-risk-1.0.0's network on this label; then the gate's diagnostics, kept in meta.json."""
    from quant_platform.research.model_risk import train as train_risk

    out = Path(out_dir)
    meta = train_risk(fp, out, years, job, version=VERSION, label_function=labels, target="drop_vol")
    found = diagnose(fp, out)
    for year, item in found.items():
        meta["years"].setdefault(year, {}).update(item)
    meta.update(label="deep_risk2", sigmas=SIGMAS, gate=gate_check(found))
    files.write_text(out / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


def gate(models_root: str | Path) -> dict[str, object]:
    path = Path(models_root) / VERSION / "meta.json"
    if not path.is_file():
        return {"version": VERSION, "passed": False, "reason": "not trained"}
    return json.loads(path.read_text(encoding="utf-8")).get("gate") or {"version": VERSION, "passed": False}
