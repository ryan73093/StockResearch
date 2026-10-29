"""The deep-learning model (2026-10-16; 使用者：deep learning 的項目有沒有研究？).

Every model so far scored a stock from one day's snapshot of its factors (gradient-boosted trees, ridge, the
ensemble). This one reads each stock's last 40 sessions as a sequence — the day's return and turnover against the
market (their ranks among the eligible stocks that day) and twelve factor ranks (momentum, 52-week high, trend, RSI,
volatility, reversal, liquidity, foreign buying, margin growth, little lent-and-sold stock, EPS growth) — through a
small recurrent network (GRU, 32 units) and learns the rank of its next 20 sessions' return among the day's stocks.

Walk-forward like the other models (research/model.py): the model of year Y learns only weekly samples whose
20-session label ended before Y's first session; the last fifth of those weeks is held out and the epoch with the
best held-out rank correlation is kept; three seeds, averaged. Diagnostics per year as the other models' (IC, the
top fifth and top 20's gain). Scores for every session of every test year are kept in ``deep.npz`` and read by
``model.scores``. Registered gate (as models F and G): the 2016–2020 top-20 gain at least gbm-2.0.0's.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from quant_platform.research import files  # 2026-10-29: files written whole while read

VERSION = "dl-1.0.0"
FACTORS = ("momentum_3", "momentum_6", "high_52w", "trend_200", "rsi_14", "low_volatility_60", "reversal_1", "liquidity",
           "foreign_buy_20", "margin_growth_20", "low_sbl_short", "eps_growth")
LENGTH = 40
HIDDEN = 32
EPOCHS = 8
BATCH = 2048
LEARNING_RATE = 2e-3
SEEDS = (0, 1, 2)
HELD_OUT = 0.2


def sequences(fp, eligible: np.ndarray, positions=None) -> np.ndarray:
    """session × symbol × feature ranks (float16, 0.5 where unknown): return, turnover change, the factors.
    ``positions``: only the LENGTH sessions up to each of them (what scoring them reads), the others left 0.5.
    2026-10-29 (S9-W39): the forward days were scored after ranking every session since 2013; each session is ranked
    on its own, so its row is the same either way."""
    prices = fp.panel.filled
    with np.errstate(divide="ignore", invalid="ignore"):
        move = np.full(prices.shape, np.nan)
        move[:, 1:] = prices[:, 1:] / prices[:, :-1] - 1
        turnover = pd.DataFrame(fp.panel.turnover.T)
        change = (np.log1p(turnover) - np.log1p(turnover.rolling(20, min_periods=5).mean())).to_numpy().T
    columns = [move, change] + [fp.matrix(name) for name in FACTORS]
    output = np.full((len(fp.sessions), len(fp.symbols), len(columns)), 0.5, dtype=np.float16)
    rows = slice(None) if positions is None else np.unique(np.array(
        [step for position in positions for step in range(max(position - LENGTH + 1, 0), position + 1)], dtype=int))
    for index, values in enumerate(columns):
        ranks = pd.DataFrame(np.where(eligible[:, rows], values[:, rows], np.nan).T).rank(axis=1, pct=True).to_numpy()
        output[rows, :, index] = np.nan_to_num(ranks, nan=0.5).astype(np.float16)
    return output


def _network(features: int):
    import torch

    class Network(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.gru = torch.nn.GRU(features, HIDDEN, batch_first=True)
            self.head = torch.nn.Linear(HIDDEN, 1)

        def forward(self, x):
            _out, last = self.gru(x)
            return self.head(last[-1]).squeeze(-1)

    return Network()


def _windows(data: np.ndarray, pairs: np.ndarray):
    """batch × LENGTH × feature for (session, symbol) pairs, on the GPU when there is one (2026-10-16)."""
    import torch

    from quant_platform.research.gpu import device

    steps = pairs[:, 0][:, None] + np.arange(-LENGTH + 1, 1)[None, :]
    if isinstance(data, torch.Tensor):           # the sequences already on the device: index them there
        where = data.device
        return data[torch.as_tensor(steps, device=where), torch.as_tensor(pairs[:, 1][:, None], device=where), :].float()
    return torch.from_numpy(data[steps, pairs[:, 1][:, None], :].astype(np.float32)).to(device())


def _score(network, data: np.ndarray, pairs: np.ndarray) -> np.ndarray:
    import torch

    output = []
    with torch.no_grad():
        for start in range(0, len(pairs), BATCH * 4):
            output.append(network(_windows(data, pairs[start:start + BATCH * 4])).cpu().numpy())
    return np.concatenate(output) if output else np.zeros(0)


def train(fp, out_dir: str | Path, years: list[int] | None = None, job=None) -> dict[str, object]:
    import torch

    from quant_platform.research.model import (
        FIRST_YEAR,
        HORIZON,
        _spearman,
        eligibility,
        label_block,
        sample_columns,
        training_columns,
    )

    from quant_platform.research.gpu import device
    from quant_platform.research.gpu import name as device_name

    torch.set_num_threads(4)
    on = device()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    eligible = eligibility(fp)
    data = sequences(fp, eligible)
    data = torch.from_numpy(data).to(on)             # session × symbol × feature, float16, on the GPU (about 150 MB)
    samples = [column for column in sample_columns(len(fp.sessions)) if column >= LENGTH]
    ranks = label_block(fp, samples, eligible, "rank", HORIZON)                  # sample × symbol
    gains = label_block(fp, samples, eligible, "excess", HORIZON)
    where = {column: index for index, column in enumerate(samples)}
    years = years or list(range(FIRST_YEAR, fp.sessions[-1].year + 1))
    diagnostics, scores = {}, {}
    for number, year in enumerate(years):
        train_cols = [column for column in training_columns(fp, samples, year, HORIZON) if column in where]
        if len(train_cols) < 30:
            continue
        cut = train_cols[int(len(train_cols) * (1 - HELD_OUT))]
        pairs, held_pairs = [], []
        for column in train_cols:
            index = where[column]
            rows = np.flatnonzero(np.isfinite(ranks[index]) & eligible[:, column])
            (pairs if column < cut else held_pairs).extend((column, row) for row in rows)
        pairs, held_pairs = np.array(pairs), np.array(held_pairs)
        labels = np.array([ranks[where[column], row] for column, row in pairs], dtype=np.float32)
        held_labels = np.array([ranks[where[column], row] for column, row in held_pairs], dtype=np.float32)
        networks, curves = [], []
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
                    loss = torch.nn.functional.mse_loss(network(_windows(data, pairs[chosen])),
                                                        torch.from_numpy(labels[chosen]).to(on))
                    optimiser.zero_grad()
                    loss.backward()
                    optimiser.step()
                predicted = _score(network, data, held_pairs)
                held_ic = float(pd.Series(predicted).corr(pd.Series(held_labels), method="spearman"))
                curve.append(round(held_ic, 4))
                if held_ic > best[0]:
                    best = (held_ic, {key: value.clone() for key, value in network.state_dict().items()})
            network.load_state_dict(best[1])
            networks.append(network)
            curves.append(curve)
            files.save_beside(out / f"{year}-{seed}.pt", lambda path: torch.save(best[1], path))
        # every session of the year, every eligible stock: the seeds' scores averaged
        columns = [column for column, day in enumerate(fp.sessions) if day.year == year and column >= LENGTH]
        year_scores = np.full((len(columns), len(fp.symbols)), np.nan, dtype=np.float32)
        for slot, column in enumerate(columns):
            rows = np.flatnonzero(eligible[:, column])
            if rows.size:
                pairs_now = np.stack([np.full(rows.size, column), rows], axis=1)
                year_scores[slot, rows] = np.mean([_score(network, data, pairs_now) for network in networks], axis=0)
        for slot, column in enumerate(columns):
            scores[fp.sessions[column].isoformat()] = year_scores[slot]
        ics, top20 = [], []
        for column in [column for column in samples if fp.sessions[column].year == year]:
            index = where[column]
            slot = columns.index(column) if column in columns else None
            if slot is None or not np.isfinite(ranks[index]).any():
                continue
            predicted = year_scores[slot]
            ic = _spearman(predicted, ranks[index])
            if ic is not None:
                ics.append(ic)
            ranked = np.isfinite(predicted) & np.isfinite(ranks[index])
            if ranked.sum() >= 50:
                best_rows = np.argsort(-np.where(ranked, predicted, -np.inf))[:20]
                top20.append(float(np.nanmean(gains[index][best_rows]) - np.nanmean(gains[index][ranked])))
        diagnostics[str(year)] = {"train_samples": int(len(pairs)), "held_samples": int(len(held_pairs)),
                                  "weeks": len(ics), "ic": round(float(np.mean(ics)), 4) if ics else None,
                                  "top20_gain": round(float(np.mean(top20)), 4) if top20 else None,
                                  "held_curves": curves}
        if job:
            job.update(done=number + 1, total=len(years),
                       current=f"{year}：樣本外 IC {diagnostics[str(year)]['ic']}、前 20 名多 {diagnostics[str(year)]['top20_gain']}")
    days = sorted(scores)
    files.save_beside(out / "deep.npz", lambda path: np.savez_compressed(
        path, days=np.array(days), symbols=np.array(fp.symbols),
        scores=np.stack([scores[day] for day in days]).astype(np.float16) if days else np.zeros((0, 0))))
    meta = {"version": VERSION, "label": "deep", "features": ["return", "turnover_change", *FACTORS],
            "length": LENGTH, "hidden": HIDDEN, "epochs": EPOCHS, "seeds": list(SEEDS), "held_out": HELD_OUT,
            "horizon": HORIZON, "years": diagnostics, "device": device_name(),
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds")}
    files.write_text(out / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1))
    return meta


def scores(fp, folder: str | Path) -> np.ndarray:
    """sessions × symbols from ``deep.npz`` (NaN where none); the sessions after the last one kept there (the
    forward days) scored by the latest year's networks (2026-10-16: they had no score, and a rule holding by the
    model would have sold everything)."""
    output = np.full((len(fp.sessions), len(fp.symbols)), np.nan, dtype=np.float32)
    path = Path(folder) / "deep.npz"
    if not path.is_file():
        return output
    from datetime import date

    stored = np.load(path, allow_pickle=False)
    columns = {symbol: fp.row[symbol] for symbol in stored["symbols"].tolist() if symbol in fp.row}
    keep = [index for index, symbol in enumerate(stored["symbols"].tolist()) if symbol in columns]
    rows = [columns[symbol] for symbol in np.array(stored["symbols"].tolist())[keep]]
    days = stored["days"].tolist()
    for day, values in zip(days, stored["scores"]):
        position = fp.index.get(date.fromisoformat(day))
        if position is not None:
            output[position, rows] = values[keep].astype(np.float32)
    if days:
        last = date.fromisoformat(days[-1])
        later = [position for position, day in enumerate(fp.sessions) if day > last and position >= LENGTH]
        if later:
            _score_later(fp, Path(folder), later, output)
    return _monthly(fp, Path(folder), output)


def _monthly(fp, folder: Path, output: np.ndarray) -> np.ndarray:
    """2026-10-21: every session after a monthly cutoff takes that cutoff's scores (model_risk.train_until); a
    session after the scored ones is scored with that cutoff's networks."""
    from datetime import date

    target = folder / "monthly"
    files = sorted(target.glob("*.npz")) if target.is_dir() else []
    if not files:
        return output
    for path in files:
        cutoff = date.fromisoformat(path.stem)
        stored = np.load(path, allow_pickle=False)
        columns = {symbol: fp.row[symbol] for symbol in stored["symbols"].tolist() if symbol in fp.row}
        keep = [index for index, symbol in enumerate(stored["symbols"].tolist()) if symbol in columns]
        rows = [columns[symbol] for symbol in np.array(stored["symbols"].tolist())[keep]]
        for day, values in zip(stored["days"].tolist(), stored["scores"]):
            position = fp.index.get(date.fromisoformat(day))
            if position is not None and fp.sessions[position] > cutoff:
                output[position, rows] = values[keep].astype(np.float32)
    latest = files[-1]
    cutoff = date.fromisoformat(latest.stem)
    scored = set(np.load(latest, allow_pickle=False)["days"].tolist())
    missing = [position for position, day in enumerate(fp.sessions)
               if day > cutoff and day.isoformat() not in scored and position >= LENGTH]
    if missing and sorted(target.glob(f"{cutoff.isoformat()}-*.pt")):
        _score_later(fp, folder, missing, output, prefix=f"monthly/{cutoff.isoformat()}")
    return output


def _score_later(fp, folder: Path, positions: list[int], output: np.ndarray, prefix: str | None = None) -> None:
    """``prefix``: the networks to use (monthly/<cutoff>, 2026-10-21); else the latest year's."""
    import torch

    from quant_platform.research.model import eligibility

    if prefix:
        chosen = sorted(folder.glob(f"{prefix}-*.pt"))
    else:
        saved = sorted(folder.glob("*-*.pt"))
        if not saved:
            return
        year = max(int(path.stem.split("-")[0]) for path in saved)
        chosen = [path for path in saved if path.stem.startswith(f"{year}-")]
    if not chosen:
        return
    networks = []
    eligible = eligibility(fp)
    data = sequences(fp, eligible, positions)
    from quant_platform.research.gpu import device

    for path in chosen:
        network = _network(data.shape[2]).to(device())
        network.load_state_dict(torch.load(path, weights_only=True, map_location=device()))
        network.eval()
        networks.append(network)
    meta = folder / "meta.json"
    drop = meta.is_file() and json.loads(meta.read_text(encoding="utf-8")).get("target") in ("drop", "drop_vol")   # risk
    for position in positions:
        rows = np.flatnonzero(eligible[:, position])
        if rows.size:
            pairs = np.stack([np.full(rows.size, position), rows], axis=1)
            value = np.mean([_score(network, data, pairs) for network in networks], axis=0)
            output[position, rows] = 1 / (1 + np.exp(-value)) if drop else value
