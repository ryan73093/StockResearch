"""2026-10-20 (使用者：前向都是賠錢、未實現不叫賺錢；持續優化每一個策略): meta-labelling (López de Prado 2018, ch. 3) on
the deep model's picks.

The best rules buy the deep model's top stocks and win by holding the few big winners while most sales are small
losses. A second model looks at each stock the deep model ranks near the top and estimates whether buying it then
beats 0050 over the next HORIZON sessions; a rule with ``risk_veto="meta1"`` skips the new buys it expects to lose.

- Events: every session, the eligible stocks in the day's top CANDIDATES by the primary score (PRIMARY, dl-2.1.0's).
- Label: the stock's return over the next HORIZON sessions above 0050's (distributions reinvested) — 1, else 0.
- Features: the primary score's percentile, the percentiles of model G's 57 factors, the stock's 60-session volatility
  percentile, and the market (TAIEX against its 200-session average, its 20-session return, the share of eligible
  stocks rising, TAIEX's 20-session volatility).
- Model: gradient-boosted trees (a classifier), walk-forward by year: year Y's model learns from the weekly events
  (every STEP sessions) whose label window ended before Y's first session.
- Veto: a candidate whose probability is below its year's training base rate (the share of training events that beat
  0050) — predicted worse than an average pick. Stored as the margin (probability − base rate) per (day, stock).
- Gate (registered before the run, research_method §7): over 2016–2020 out of sample, the weekly events vetoed beat 0050
  by at least GATE_GAP less on average than the ones kept, and the kept ones' win rate is at least GATE_WIN above all
  events'. Only then the rules run.
"""

from __future__ import annotations

import json
import pickle
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from quant_platform.research import files  # 2026-10-29: files written whole while read

VERSION = "meta-1.0.0"
PRIMARY = "ml_deep2_top"        # the deep model's top tenth (dl-2.1.0): the frontier's rules use it
CANDIDATES = 40                 # the day's top this many eligible stocks by the primary score
HORIZON = 40                    # sessions the label looks ahead
STEP = 5                        # weekly training events
FIRST_YEAR = 2016
LEAST = 400                     # training events a year's model needs
GATE_YEARS = (2016, 2017, 2018, 2019, 2020)
GATE_GAP = 0.01
GATE_WIN = 0.02
SNAPSHOT = "gbm-2.2.0"          # model G's features
PARAMS = {"max_iter": 200, "learning_rate": 0.05, "max_depth": 3, "min_samples_leaf": 100, "l2_regularization": 1.0}


def picks(fp, eligible: np.ndarray) -> np.ndarray:
    """symbols × sessions: the eligible stocks in the day's top CANDIDATES by PRIMARY."""
    score = fp.matrix(PRIMARY)
    output = np.zeros(score.shape, dtype=bool)
    for position in range(score.shape[1]):
        column = np.where(eligible[:, position], score[:, position], np.nan)
        known = np.flatnonzero(np.isfinite(column))
        if known.size >= CANDIDATES:
            output[known[np.argsort(-column[known], kind="stable")[:CANDIDATES]], position] = True
    return output


def features(fp, eligible: np.ndarray, pairs: np.ndarray, names: tuple[str, ...]) -> np.ndarray:
    """len(pairs) × features for (session, symbol) pairs: percentiles among the day's eligible stocks, then the market."""
    from quant_platform.research.model import _rank
    from quant_platform.research.model_deep2 import market_inputs

    columns = []
    for name in (PRIMARY, *names, "low_volatility_60"):
        ranks = _rank(np.where(eligible, fp.matrix(name), np.nan).T)               # sessions × symbols
        columns.append(ranks[pairs[:, 0], pairs[:, 1]])
    market = market_inputs(fp, eligible)
    index = getattr(fp, "market", None)
    if index is not None:
        volatility = pd.Series(np.asarray(index, dtype=float)).ffill().pct_change(fill_method=None)
        volatility = np.nan_to_num(volatility.rolling(20, min_periods=10).std().to_numpy() * 50)
    else:
        volatility = np.zeros(len(fp.sessions))
    return np.column_stack([*columns, market[pairs[:, 0]], volatility[pairs[:, 0]]]).astype(np.float32)


def labels(fp, pairs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(beat 0050: 1/0, NaN past the data; the excess over 0050) over the next HORIZON sessions from each pair's close."""
    prices = fp.panel.filled
    bench = getattr(fp.panel, "benchmark", None)
    end = pairs[:, 0] + HORIZON
    valid = end < prices.shape[1]
    excess = np.full(len(pairs), np.nan)
    if bench is not None and valid.any():
        start, stop = pairs[valid, 0], end[valid]
        with np.errstate(invalid="ignore", divide="ignore"):
            stock = prices[pairs[valid, 1], stop] / prices[pairs[valid, 1], start] - 1
            market = bench[stop] / bench[start] - 1
        excess[valid] = stock - market
    won = np.where(np.isfinite(excess), (excess > 0).astype(float), np.nan)
    return won, excess


def train(fp, out_dir: str | Path, years: list[int] | None = None, job=None) -> dict[str, object]:
    """One classifier per year (pickled), every candidate's margin stored (meta.npz), the gate in meta.json."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.metrics import roc_auc_score

    from quant_platform.research.model import eligibility, trained_features

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    eligible = eligibility(fp)
    names = trained_features(models_root_of(out) / SNAPSHOT, SNAPSHOT)
    chosen = picks(fp, eligible)
    pairs = np.argwhere(chosen.T)                                              # (session, symbol), by session
    x = features(fp, eligible, pairs, names)
    won, excess = labels(fp, pairs)
    weekly = pairs[:, 0] % STEP == 0
    margin = np.full(len(pairs), np.nan, dtype=np.float32)
    years = years or list(range(FIRST_YEAR, fp.sessions[-1].year + 1))
    yearly: dict[str, dict] = {}
    session_year = np.array([day.year for day in fp.sessions])
    for number, year in enumerate(years):
        first = next((index for index, day in enumerate(fp.sessions) if day.year >= year), len(fp.sessions))
        learn = weekly & (pairs[:, 0] + HORIZON < first) & np.isfinite(won)
        test = session_year[pairs[:, 0]] == year if year != years[-1] else session_year[pairs[:, 0]] >= year
        if learn.sum() < LEAST or not test.any():
            continue
        model = HistGradientBoostingClassifier(**PARAMS, random_state=0).fit(x[learn], won[learn])
        base = float(won[learn].mean())
        files.write_bytes(out / f"{year}.pkl", pickle.dumps({"model": model, "base": base}))
        margin[test] = model.predict_proba(x[test])[:, 1] - base
        judged = test & weekly & np.isfinite(won)
        kept, vetoed = judged & (margin >= 0), judged & (margin < 0)
        item = {"train_events": int(learn.sum()), "base_rate": round(base, 4), "events": int(judged.sum()),
                "kept": int(kept.sum()), "vetoed": int(vetoed.sum())}
        if judged.sum() >= 50 and len(set(won[judged])) == 2:
            item.update(auc=round(float(roc_auc_score(won[judged], margin[judged])), 4),
                        win_all=round(float(won[judged].mean()), 4),
                        win_kept=round(float(won[kept].mean()), 4) if kept.any() else None,
                        excess_kept=round(float(excess[kept].mean()), 5) if kept.any() else None,
                        excess_vetoed=round(float(excess[vetoed].mean()), 5) if vetoed.any() else None,
                        sum_excess_kept=float(excess[kept].sum()), sum_excess_vetoed=float(excess[vetoed].sum()),
                        sum_won_kept=float(won[kept].sum()), sum_won_all=float(won[judged].sum()))
        yearly[str(year)] = item
        if job:
            job.update(done=number + 1, total=len(years),
                       current=f"{year}：AUC {item.get('auc')}、留下的勝率 {item.get('win_kept')}（全部 {item.get('win_all')}）")
    days = np.array([fp.sessions[position].isoformat() for position in pairs[:, 0]])
    codes = np.array([fp.symbols[row] for row in pairs[:, 1]])
    known = np.isfinite(margin)
    files.save_beside(out / "meta.npz", lambda path: np.savez_compressed(
        path, day=days[known], symbol=codes[known], margin=margin[known]))
    meta = {"version": VERSION, "label": "meta", "primary": PRIMARY, "candidates": CANDIDATES, "horizon": HORIZON,
            "step": STEP, "features": [PRIMARY, *names, "low_volatility_60", "taiex_trend_200", "taiex_return_20",
                                       "rising_share", "taiex_volatility_20"],
            "params": PARAMS, "years": yearly, "trained_at": datetime.now(UTC).isoformat(timespec="seconds")}
    meta["gate"] = gate_check(meta)
    files.write_text(out / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


def train_until(fp, out_dir: str | Path, cutoff) -> dict[str, object]:
    """2026-10-21 (使用者 2026-10-16：每月滾動，模型也要更新): the classifier refitted on every weekly event whose
    40-session label ended by ``cutoff``; saved as monthly/<cutoff>.pkl, ``scores`` uses it for every session after
    the cutoff. The yearly models and meta.npz stay."""
    from sklearn.ensemble import HistGradientBoostingClassifier

    from quant_platform.research.model import eligibility, trained_features

    out = Path(out_dir)
    target = out / "monthly"
    target.mkdir(parents=True, exist_ok=True)
    eligible = eligibility(fp)
    names = trained_features(models_root_of(out) / SNAPSHOT, SNAPSHOT)
    pairs = np.argwhere(picks(fp, eligible).T)
    x = features(fp, eligible, pairs, names)
    won, _excess = labels(fp, pairs)
    last = max(index for index, day in enumerate(fp.sessions) if day <= cutoff)
    learn = (pairs[:, 0] % STEP == 0) & (pairs[:, 0] + HORIZON <= last) & np.isfinite(won)
    model = HistGradientBoostingClassifier(**PARAMS, random_state=0).fit(x[learn], won[learn])
    base = float(won[learn].mean())
    files.write_bytes(target / f"{cutoff.isoformat()}.pkl", pickle.dumps({"model": model, "base": base}))
    return {"version": VERSION, "cutoff": cutoff.isoformat(), "train_events": int(learn.sum()), "base_rate": round(base, 4),
            "label_until": fp.sessions[int(pairs[learn][:, 0].max()) + HORIZON].isoformat(),
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds")}


def models_root_of(folder: Path) -> Path:
    return Path(folder).parent


def gate_check(meta: dict) -> dict[str, object]:
    """The registered gate over GATE_YEARS, events pooled."""
    items = [(meta.get("years") or {}).get(str(year)) or {} for year in GATE_YEARS]
    if not all("sum_excess_kept" in item for item in items):
        return {"version": meta.get("version"), "passed": False, "reason": "not every gate year trained"}
    kept = sum(item["kept"] for item in items)
    vetoed = sum(item["vetoed"] for item in items)
    events = sum(item["events"] for item in items)
    excess_kept = sum(item["sum_excess_kept"] for item in items) / kept if kept else None
    excess_vetoed = sum(item["sum_excess_vetoed"] for item in items) / vetoed if vetoed else None
    win_kept = sum(item["sum_won_kept"] for item in items) / kept if kept else None
    win_all = sum(item["sum_won_all"] for item in items) / events if events else None
    passed = (excess_kept is not None and excess_vetoed is not None and win_kept is not None and win_all is not None
              and excess_kept - excess_vetoed >= GATE_GAP and win_kept - win_all >= GATE_WIN)
    return {"version": meta.get("version"), "years": list(GATE_YEARS), "events": events, "kept": kept, "vetoed": vetoed,
            "excess_kept": None if excess_kept is None else round(excess_kept, 5),
            "excess_vetoed": None if excess_vetoed is None else round(excess_vetoed, 5),
            "win_kept": None if win_kept is None else round(win_kept, 4),
            "win_all": None if win_all is None else round(win_all, 4),
            "gap": GATE_GAP, "win_gap": GATE_WIN, "passed": bool(passed)}


def gate(models_root: str | Path) -> dict[str, object]:
    path = Path(models_root) / VERSION / "meta.json"
    if not path.is_file():
        return {"version": VERSION, "passed": False, "reason": "not trained"}
    return gate_check(json.loads(path.read_text(encoding="utf-8")))


def scores(fp, folder: str | Path) -> np.ndarray:
    """sessions × symbols: each candidate's margin (probability − its year's base rate; below 0 = vetoed), NaN for the
    others; the sessions after the stored ones scored by the latest year's model."""
    from quant_platform.research.model import eligibility, trained_features

    folder = Path(folder)
    output = np.full((len(fp.sessions), len(fp.symbols)), np.nan, dtype=np.float32)
    path = folder / "meta.npz"
    if not path.is_file():
        return output
    stored = np.load(path)
    rows = {symbol: row for row, symbol in enumerate(fp.symbols)}
    last = ""
    for day, symbol, value in zip(stored["day"], stored["symbol"], stored["margin"], strict=True):
        position, row = fp.index.get(date.fromisoformat(str(day))), rows.get(str(symbol))
        if position is not None and row is not None:
            output[position, row] = value
        last = max(last, str(day))
    later = [position for position, day in enumerate(fp.sessions) if day.isoformat() > last]
    saved = sorted(folder.glob("*.pkl"), key=lambda item: int(item.stem))
    if later and saved:
        eligible = eligibility(fp)
        chosen = picks(fp, eligible)
        pairs = np.array([(position, row) for position in later for row in np.flatnonzero(chosen[:, position])],
                         dtype=int).reshape(-1, 2)
        if len(pairs):
            names = trained_features(folder.parent / SNAPSHOT, SNAPSHOT)
            fitted = pickle.loads(saved[-1].read_bytes())
            values = fitted["model"].predict_proba(features(fp, eligible, pairs, names))[:, 1] - fitted["base"]
            output[pairs[:, 0], pairs[:, 1]] = values
    # 2026-10-21: every session after a monthly cutoff from that cutoff's classifier (train_until), the latest winning
    monthly = sorted((folder / "monthly").glob("*.pkl")) if (folder / "monthly").is_dir() else []
    if monthly:
        eligible = eligibility(fp)
        chosen = picks(fp, eligible)
        names = trained_features(folder.parent / SNAPSHOT, SNAPSHOT)
        for path in monthly:
            cutoff = date.fromisoformat(path.stem)
            after = [position for position, day in enumerate(fp.sessions) if day > cutoff]
            pairs = np.array([(position, row) for position in after for row in np.flatnonzero(chosen[:, position])],
                             dtype=int).reshape(-1, 2)
            if len(pairs):
                fitted = pickle.loads(path.read_bytes())
                output[after] = np.nan
                values = fitted["model"].predict_proba(features(fp, eligible, pairs, names))[:, 1] - fitted["base"]
                output[pairs[:, 0], pairs[:, 1]] = values
    return output


def digest(folder: str | Path) -> str:
    import hashlib

    path = Path(folder) / "meta.json"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
