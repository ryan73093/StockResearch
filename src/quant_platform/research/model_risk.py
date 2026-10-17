"""The deep-learning risk model (2026-10-16; 使用者：既然深度學習可以協助預測，能不能用到交易裡輔助上面這些策略？).

The return model (research/model_deep.py, dl-1.0.0) ranked the whole market a little but did not find better top
picks. Risk is more predictable than return, and every strategy here suffers from the same thing — twenty strong
stocks falling together. This model reads the same 40-session sequences (the GRU of model_deep) and learns, for each
eligible stock and session, whether its price falls 15% or more below today's close at some point in the next 20
sessions (``DROP``, ``HORIZON``): a probability.

Walk-forward like every model: the networks of year Y learn only weekly samples whose 20 sessions ended before Y's
first session; the last fifth held out picks the epoch (by its AUC); three seeds averaged. On the GPU.

Registered gate (written before the results, research_method §7): over 2016–2020 the model's AUC for the drop must
beat the plain yardstick — the higher the 60-session volatility, the riskier — by at least 0.02. Only then do rules use
it: ``DailyRule.risk_veto`` "dl1" buys no stock in the day's top tenth of risk, "dl1x" also sells a holding once it is
in the top twentieth (every session).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

VERSION = "dl-risk-1.0.0"
DROP = 0.15
GATE_YEARS = (2016, 2017, 2018, 2019, 2020)
GATE_MARGIN = 0.02


def labels(fp, columns: list[int], eligible: np.ndarray, horizon: int) -> np.ndarray:
    """len(columns) × symbols: 1 when the lowest close of the next ``horizon`` sessions is DROP or more below this
    close, 0 when not, NaN past the data or for a stock not eligible."""
    prices = fp.panel.filled
    output = np.full((len(columns), len(fp.symbols)), np.nan, dtype=np.float32)
    for slot, column in enumerate(columns):
        if column + horizon >= prices.shape[1]:
            continue
        with np.errstate(invalid="ignore", divide="ignore"):
            low = np.nanmin(prices[:, column + 1: column + horizon + 1], axis=1) / prices[:, column] - 1
        known = eligible[:, column] & np.isfinite(low)
        output[slot] = np.where(known, (low <= -DROP).astype(np.float32), np.nan)
    return output


def auc(score: np.ndarray, label: np.ndarray) -> float | None:
    """The probability a random drop scores above a random non-drop (ties count half)."""
    import pandas as pd

    known = np.isfinite(score) & np.isfinite(label)
    score, label = score[known], label[known]
    positive = int(label.sum())
    negative = len(label) - positive
    if positive == 0 or negative == 0:
        return None
    ranks = pd.Series(score).rank().to_numpy()
    return float((ranks[label == 1].sum() - positive * (positive + 1) / 2) / (positive * negative))


def train(fp, out_dir: str | Path, years: list[int] | None = None, job=None, version: str = VERSION,
          label_function=None, target: str = "drop") -> dict[str, object]:
    """2026-10-17: ``label_function`` and ``target`` let dl-risk-2.0.0 (research/model_risk2.py) train the same
    network on its own label."""
    import pandas as pd
    import torch

    from quant_platform.research.gpu import device
    from quant_platform.research.gpu import name as device_name
    from quant_platform.research.model import FIRST_YEAR, HORIZON, eligibility, sample_columns, training_columns
    from quant_platform.research.model_deep import (
        BATCH, EPOCHS, HELD_OUT, LEARNING_RATE, LENGTH, SEEDS, _network, _score, _windows, sequences,
    )

    torch.set_num_threads(4)
    on = device()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    eligible = eligibility(fp)
    data = torch.from_numpy(sequences(fp, eligible)).to(on)
    samples = [column for column in sample_columns(len(fp.sessions)) if column >= LENGTH]
    drops = (label_function or labels)(fp, samples, eligible, HORIZON)
    volatility = -fp.matrix("low_volatility_60")                                  # the yardstick: riskier = calmer less
    slot_of = {column: index for index, column in enumerate(samples)}
    years = years or list(range(FIRST_YEAR, fp.sessions[-1].year + 1))
    diagnostics, kept = {}, {}
    for number, year in enumerate(years):
        train_cols = [column for column in training_columns(fp, samples, year, HORIZON) if column in slot_of]
        if len(train_cols) < 30:
            continue
        cut = train_cols[int(len(train_cols) * (1 - HELD_OUT))]
        pairs, held = [], []
        for column in train_cols:
            rows = np.flatnonzero(np.isfinite(drops[slot_of[column]]))
            (pairs if column < cut else held).extend((column, row) for row in rows)
        pairs, held = np.array(pairs), np.array(held)
        targets = np.array([drops[slot_of[column], row] for column, row in pairs], dtype=np.float32)
        held_targets = np.array([drops[slot_of[column], row] for column, row in held], dtype=np.float32)
        weight = torch.tensor((len(targets) - targets.sum()) / max(targets.sum(), 1.0), device=on)   # drops are rare
        networks = []
        curves = []
        for seed in SEEDS:
            torch.manual_seed(seed)
            rng = np.random.default_rng(seed)
            network = _network(data.shape[2]).to(on)
            optimiser = torch.optim.Adam(network.parameters(), lr=LEARNING_RATE)
            best, curve = (-np.inf, None), []
            for _epoch in range(EPOCHS):
                order = rng.permutation(len(pairs))
                for start in range(0, len(order), BATCH):
                    chosen = order[start:start + BATCH]
                    loss = torch.nn.functional.binary_cross_entropy_with_logits(
                        network(_windows(data, pairs[chosen])), torch.from_numpy(targets[chosen]).to(on), pos_weight=weight)
                    optimiser.zero_grad()
                    loss.backward()
                    optimiser.step()
                held_auc = auc(_score(network, data, held), held_targets) or 0.0
                curve.append(round(held_auc, 4))
                if held_auc > best[0]:
                    best = (held_auc, {key: value.clone() for key, value in network.state_dict().items()})
            network.load_state_dict(best[1])
            networks.append(network)
            curves.append(curve)
            torch.save(best[1], out / f"{year}-{seed}.pt")
        columns = [column for column, day in enumerate(fp.sessions) if day.year == year and column >= LENGTH]
        for column in columns:
            rows = np.flatnonzero(eligible[:, column])
            values = np.full(len(fp.symbols), np.nan, dtype=np.float32)
            if rows.size:
                now = np.stack([np.full(rows.size, column), rows], axis=1)
                values[rows] = 1 / (1 + np.exp(-np.mean([_score(network, data, now) for network in networks], axis=0)))
            kept[fp.sessions[column].isoformat()] = values
        tested = [column for column in samples if fp.sessions[column].year == year]
        model_score = np.concatenate([kept[fp.sessions[column].isoformat()] for column in tested]) if tested else np.zeros(0)
        truth = np.concatenate([drops[slot_of[column]] for column in tested]) if tested else np.zeros(0)
        plain = np.concatenate([volatility[:, column] for column in tested]) if tested else np.zeros(0)
        diagnostics[str(year)] = {"train_samples": int(len(pairs)), "held_samples": int(len(held)),
                                  "base_rate": round(float(np.nanmean(truth)), 4) if truth.size else None,
                                  "auc": None if not truth.size else (round(value, 4) if (value := auc(model_score, truth)) else None),
                                  "auc_volatility": None if not truth.size else (round(value, 4) if (value := auc(plain, truth)) else None),
                                  "held_curves": curves}
        if job:
            item = diagnostics[str(year)]
            job.update(done=number + 1, total=len(years),
                       current=f"{year}：大跌比例 {item['base_rate']}、AUC {item['auc']}（波動 {item['auc_volatility']}）")
    days = sorted(kept)
    np.savez_compressed(out / "deep.npz", days=np.array(days), symbols=np.array(fp.symbols),
                        scores=np.stack([kept[day] for day in days]).astype(np.float16) if days else np.zeros((0, 0)))
    meta = {"version": version, "label": "deep_risk", "target": target, "drop": DROP, "horizon": HORIZON,
            "length": LENGTH, "seeds": list(SEEDS), "epochs": EPOCHS, "held_out": HELD_OUT, "years": diagnostics,
            "device": device_name(), "trained_at": datetime.now(UTC).isoformat(timespec="seconds")}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    del pd
    return meta


def gate(models_root: str | Path) -> dict[str, object]:
    """The registered gate: the 2016–2020 average AUC at least GATE_MARGIN above the volatility yardstick's."""
    path = Path(models_root) / VERSION / "meta.json"
    if not path.is_file():
        return {"version": VERSION, "passed": False, "reason": "not trained"}
    years = json.loads(path.read_text(encoding="utf-8")).get("years") or {}
    mine = [years[str(year)]["auc"] for year in GATE_YEARS if years.get(str(year), {}).get("auc") is not None]
    plain = [years[str(year)]["auc_volatility"] for year in GATE_YEARS
             if years.get(str(year), {}).get("auc_volatility") is not None]
    model_auc = float(np.mean(mine)) if mine else None
    plain_auc = float(np.mean(plain)) if plain else None
    return {"version": VERSION, "years": list(GATE_YEARS), "auc": model_auc, "auc_volatility": plain_auc,
            "margin": GATE_MARGIN,
            "passed": model_auc is not None and plain_auc is not None and model_auc >= plain_auc + GATE_MARGIN}
