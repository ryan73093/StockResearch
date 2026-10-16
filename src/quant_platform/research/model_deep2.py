"""The deep-learning model, second version (2026-10-17; 使用者：深度學習繼續優化; docs/research_dl_exits_review.md §2.6 D1).

dl-1.0.0 ranked the whole market a little (IC about 0.075) but its top 20 gained only +0.2% over the median in 20
sessions (gbm-2.0.0 +1.15%). The literature review found five design faults, each fixed here:

1. the label was the return's rank, learned with a squared error — rewarding "the middle in order", not the top. Here
   the label is the next 20 sessions' return above the day's median with the day's 1% tails clipped (as gbm-1.1.0),
   and two registered losses aim at the top: dl-2.0.0 a Huber point estimate (δ 5%) plus a ListNet term whose target
   puts most weight on the day's best (softmax of the excess over half the day's spread); dl-2.1.0 whether the stock
   ends in the day's top tenth (weighted cross-entropy, positives × 9);
2. the epoch was chosen by the held-out IC — here by the held-out top 20's gain over the median, the use it is for;
3. every input was a rank among the day's stocks, losing size and the market's level — here each sequence input is
   the day's z-score among the eligible stocks, clipped at ±5: the day's return above the median, the open-to-close
   move, the overnight gap, the close against its 20-, 60- and 200-session averages, turnover against its own
   60-session average, and five chip flows (foreign, trust and dealer buying, margin growth, lent-and-sold stock —
   the panel's, a session late as every chip factor). The network reads 60 sessions of them; the day's snapshot of
   every rule factor but monthly revenue (gbm-2.2.0's features, as ranks) and the market's state (TAIEX against its
   200-session average, its 20-session return, the day's share of rising stocks) join the GRU's output in the head;
4. weekly samples only — here every session is a sample (about five times as many);
5. three seeds — here ten, each seed's scores turned into ranks and averaged (the top of the list moves most with the
   seed).

Network: GRU (64 units, one layer) → with the snapshot and the market → MLP (64, 32; dropout 0.1).

Walk-forward as every model: year Y's networks learn only sessions whose 20-session label ended before Y's first
session; the last fifth of those sessions is held out, the 20 sessions before it left out of the training (purge);
the epoch is chosen on the held-out top-20 gain (at most 20 epochs, stopping after 4 without a better one).

Registered gate (written before the results, research_method §7): over 2016–2020 the average yearly top-20 gain
(weekly samples, the same ``model._top20`` as every model) at least gbm-2.2.0's, and at least four of the five
years at +0.6% or more (one round trip's cost). The development years are trained first; the other years only when
the gate is passed. Only then do rules use it (``daily.deep2_batch``).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

LOSSES = {"dl-2.0.0": "listnet", "dl-2.1.0": "top10"}
CHIPS = ("foreign_buy_20", "trust_buy_20", "dealer_buy_20", "margin_growth_20", "low_sbl_short")
SEQUENCE = ("excess_return", "intraday", "overnight", "ma_20", "ma_60", "ma_200", "turnover_60", *CHIPS)
MARKET = ("taiex_trend_200", "taiex_return_20", "rising_share")
SNAPSHOT_VERSION = "gbm-2.2.0"        # the snapshot: its features (every rule factor but monthly revenue)
AGAINST = "gbm-2.2.0"                 # the gate's yardstick
LENGTH = 60
HIDDEN = 64
HEAD = (64, 32)
DROPOUT = 0.1
EPOCHS = 20
PATIENCE = 4
SEEDS = tuple(range(10))
HELD_OUT = 0.2
DAYS_PER_BATCH = 8
LEARNING_RATE = 1e-3
WEIGHT_DECAY = 1e-5
HUBER = 0.05
TEMPERATURE = 0.5                     # the ListNet target: softmax(excess ÷ (0.5 × the day's spread))
TOP_SHARE = 0.1
CLIP = 5.0
GATE_YEARS = (2016, 2017, 2018, 2019, 2020)
GATE_FLOOR = 0.006
GATE_COUNT = 4


def _zscore(values: np.ndarray, eligible: np.ndarray) -> np.ndarray:
    """sessions × symbols: each session's z-score among its eligible stocks, clipped at ±CLIP, 0 where unknown."""
    import warnings

    masked = np.where(eligible, values, np.nan).T
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(masked, axis=1, keepdims=True)
        spread = np.nanstd(masked, axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = (masked - mean) / np.where(spread > 0, spread, np.nan)
    return np.nan_to_num(np.clip(z, -CLIP, CLIP), nan=0.0)


def _average(prices: np.ndarray, sessions: int) -> np.ndarray:
    return pd.DataFrame(prices.T).rolling(sessions, min_periods=max(5, sessions // 2)).mean().to_numpy().T


def sequence_inputs(fp, eligible: np.ndarray) -> np.ndarray:
    """session × symbol × SEQUENCE (float16): the day's z-scores (see the module text)."""
    import warnings

    panel = fp.panel
    prices = panel.filled
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)                     # a session with no eligible stock
        move = np.full(prices.shape, np.nan)
        move[:, 1:] = prices[:, 1:] / prices[:, :-1] - 1
        median = np.nanmedian(np.where(eligible, move, np.nan), axis=0, keepdims=True)
        unit = panel.adjusted / panel.close                         # the adjustment of the day
        opens = panel.open * unit
        intraday = panel.adjusted / opens - 1
        overnight = np.full(prices.shape, np.nan)
        overnight[:, 1:] = opens[:, 1:] / prices[:, :-1] - 1
        averages = {sessions: np.log(prices / _average(prices, sessions)) for sessions in (20, 60, 200)}
        flow = np.log1p(np.nan_to_num(panel.turnover, nan=0.0))
        turnover = flow - pd.DataFrame(flow.T).rolling(60, min_periods=20).mean().to_numpy().T
    columns = [move - median, intraday, overnight, averages[20], averages[60], averages[200], turnover]
    columns += [fp.matrix(name) for name in CHIPS]
    output = np.zeros((len(fp.sessions), len(fp.symbols), len(columns)), dtype=np.float16)
    for index, values in enumerate(columns):
        output[:, :, index] = _zscore(values, eligible).astype(np.float16)
    return output


def snapshot_inputs(fp, eligible: np.ndarray, names: tuple[str, ...]) -> np.ndarray:
    """session × symbol × names (float16): each factor's rank among the day's eligible stocks, centred (0 unknown)."""
    output = np.zeros((len(fp.sessions), len(fp.symbols), len(names)), dtype=np.float16)
    for index, name in enumerate(names):
        ranks = pd.DataFrame(np.where(eligible, fp.matrix(name), np.nan).T).rank(axis=1, pct=True).to_numpy()
        output[:, :, index] = np.nan_to_num(ranks - 0.5, nan=0.0).astype(np.float16)
    return output


def market_inputs(fp, eligible: np.ndarray) -> np.ndarray:
    """session × MARKET (float32): TAIEX against its 200-session average, its 20-session return (zero without the
    index), the day's share of rising eligible stocks (centred); scaled to about ±1."""
    prices = fp.panel.filled
    output = np.zeros((len(fp.sessions), len(MARKET)), dtype=np.float32)
    market = getattr(fp, "market", None)
    if market is not None:
        index = pd.Series(np.asarray(market, dtype=float)).ffill()
        output[:, 0] = np.nan_to_num((index / index.rolling(200, min_periods=100).mean() - 1).to_numpy() * 5)
        output[:, 1] = np.nan_to_num((index / index.shift(20) - 1).to_numpy() * 5)
    with np.errstate(invalid="ignore", divide="ignore"):
        rising = np.full(prices.shape, np.nan)
        rising[:, 1:] = (prices[:, 1:] > prices[:, :-1]).astype(float)
        rising = np.where(eligible & np.isfinite(prices), rising, np.nan)
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            output[:, 2] = np.nan_to_num((np.nanmean(rising, axis=0) - 0.5) * 2)
    return output


def _network(sequence: int, snapshot: int):
    import torch

    class Network(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.gru = torch.nn.GRU(sequence, HIDDEN, batch_first=True)
            layers, width = [], HIDDEN + snapshot + len(MARKET)
            for size in HEAD:
                layers += [torch.nn.Linear(width, size), torch.nn.ReLU(), torch.nn.Dropout(DROPOUT)]
                width = size
            self.head = torch.nn.Sequential(*layers, torch.nn.Linear(width, 1))

        def forward(self, steps, now, market):
            _out, last = self.gru(steps)
            return self.head(torch.cat([last[-1], now, market], dim=1)).squeeze(-1)

    return Network()


class Inputs:
    """The three input blocks on the device, and the batches of (session, symbol) pairs drawn from them."""

    def __init__(self, fp, eligible: np.ndarray, names: tuple[str, ...]) -> None:
        import torch

        from quant_platform.research.gpu import device

        self.on = device()
        self.sequence = torch.from_numpy(sequence_inputs(fp, eligible)).to(self.on)
        self.snapshot = torch.from_numpy(snapshot_inputs(fp, eligible, names)).to(self.on)
        self.market = torch.from_numpy(market_inputs(fp, eligible)).to(self.on)

    def batch(self, pairs: np.ndarray):
        import torch

        sessions = torch.as_tensor(pairs[:, 0], device=self.on)
        symbols = torch.as_tensor(pairs[:, 1], device=self.on)
        steps = sessions[:, None] + torch.arange(-LENGTH + 1, 1, device=self.on)[None, :]
        return (self.sequence[steps, symbols[:, None], :].float(), self.snapshot[sessions, symbols, :].float(),
                self.market[sessions])

    def score(self, network, pairs: np.ndarray, size: int = 16384) -> np.ndarray:
        import torch

        output = []
        network.eval()
        with torch.no_grad():
            for start in range(0, len(pairs), size):
                output.append(network(*self.batch(pairs[start:start + size])).float().cpu().numpy())
        network.train()
        return np.concatenate(output) if output else np.zeros(0, dtype=np.float32)


def _loss(kind: str, predicted, target, groups: list[tuple[int, int]]):
    """The registered losses: ``listnet`` Huber + ListNet per day (top-weighted), ``top10`` the day's top tenth."""
    import torch

    functional = torch.nn.functional
    if kind == "top10":
        positive = torch.zeros_like(target)
        for start, end in groups:
            day = target[start:end]
            positive[start:end] = (day >= torch.quantile(day, 1 - TOP_SHARE)).float()
        return functional.binary_cross_entropy_with_logits(
            predicted, positive, pos_weight=torch.tensor((1 - TOP_SHARE) / TOP_SHARE, device=predicted.device))
    point = functional.huber_loss(predicted, target, delta=HUBER)
    listwise = []
    for start, end in groups:
        day, guess = target[start:end], predicted[start:end]
        spread = torch.clamp(day.std(), min=1e-4)
        listwise.append(-(torch.softmax(day / (TEMPERATURE * spread), 0) * torch.log_softmax(guess, 0)).sum())
    return point + torch.stack(listwise).mean()


def _gain(scores: np.ndarray, gains: np.ndarray) -> float | None:
    """One day's top 20 (by the scores) against all the day's stocks, in 20-session excess (as ``model._top20``)."""
    if scores.size < 50:
        return None
    return float(gains[np.argsort(-scores)[:20]].mean() - gains.mean())


def _fit(inputs: Inputs, kind: str, seed: int, train_days, held_days, rows, labels) -> tuple[dict, list[float]]:
    """One seed: the network with the best held-out top-20 gain, and the curve."""
    import torch

    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    network = _network(inputs.sequence.shape[2], inputs.snapshot.shape[2]).to(inputs.on)
    optimiser = torch.optim.AdamW(network.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
    held_pairs = np.concatenate([np.stack([np.full(rows[day].size, day), rows[day]], axis=1) for day in held_days])
    best, curve, waited = (-np.inf, None), [], 0
    for _epoch in range(EPOCHS):
        order = rng.permutation(len(train_days))
        for start in range(0, len(order), DAYS_PER_BATCH):
            chosen = [train_days[index] for index in order[start:start + DAYS_PER_BATCH]]
            groups, pairs, target, at = [], [], [], 0
            for day in chosen:
                count = rows[day].size
                groups.append((at, at + count))
                at += count
                pairs.append(np.stack([np.full(count, day), rows[day]], axis=1))
                target.append(labels[day][rows[day]])
            loss = _loss(kind, network(*inputs.batch(np.concatenate(pairs))),
                         torch.from_numpy(np.concatenate(target).astype(np.float32)).to(inputs.on), groups)
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()
        predicted = inputs.score(network, held_pairs)
        gains, at = [], 0
        for day in held_days:
            count = rows[day].size
            value = _gain(predicted[at:at + count], labels[day][rows[day]])
            at += count
            if value is not None:
                gains.append(value)
        held = float(np.mean(gains)) if gains else -np.inf
        curve.append(round(held, 5))
        if held > best[0]:
            best, waited = (held, {key: value.clone() for key, value in network.state_dict().items()}), 0
        else:
            waited += 1
            if waited >= PATIENCE:
                break
    return best[1], curve


def _rank_average(values: list[np.ndarray]) -> np.ndarray:
    return np.mean([pd.Series(value).rank(pct=True).to_numpy() for value in values], axis=0)


def gate_check(meta: dict, against: dict) -> dict[str, object]:
    """The registered gate on two metas' yearly top-20 gains (see the module text)."""
    mine = {year: (meta.get("years") or {}).get(str(year), {}).get("top20_gain") for year in GATE_YEARS}
    theirs = [(against.get("years") or {}).get(str(year), {}).get("top20_gain") for year in GATE_YEARS]
    known = [value for value in mine.values() if value is not None]
    theirs = [value for value in theirs if value is not None]
    average = float(np.mean(known)) if len(known) == len(GATE_YEARS) else None
    yardstick = float(np.mean(theirs)) if theirs else None
    above = sum(1 for value in known if value >= GATE_FLOOR)
    return {"version": meta.get("version"), "against": against.get("version"), "years": list(GATE_YEARS),
            "top20_gain": average, "against_top20_gain": yardstick, "years_above_floor": above,
            "floor": GATE_FLOOR, "passed": average is not None and yardstick is not None and average >= yardstick
            and above >= GATE_COUNT}


def gate(models_root: str | Path, version: str) -> dict[str, object]:
    root = Path(models_root)
    paths = root / version / "meta.json", root / AGAINST / "meta.json"
    if not all(path.is_file() for path in paths):
        return {"version": version, "passed": False, "reason": "not trained"}
    return gate_check(*(json.loads(path.read_text(encoding="utf-8")) for path in paths))


def train(fp, out_dir: str | Path, version: str, years: list[int] | None = None, job=None) -> dict[str, object]:
    """Fit ten seeds per year, the development years first; the other years only past the gate (module text)."""
    import torch

    from quant_platform.research.gpu import name as device_name
    from quant_platform.research.model import (
        FIRST_YEAR,
        HORIZON,
        _top20,
        eligibility,
        label_block,
        sample_columns,
        trained_features,
    )

    torch.set_num_threads(4)
    kind = LOSSES[version]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    eligible = eligibility(fp)
    names = trained_features(out.parent / SNAPSHOT_VERSION, SNAPSHOT_VERSION)
    inputs = Inputs(fp, eligible, names)
    start = LENGTH + 200
    columns = list(range(start, len(fp.sessions)))
    excess = label_block(fp, columns, eligible, "excess", HORIZON)                 # column × symbol
    labels = {column: excess[slot] for slot, column in enumerate(columns)}
    rows = {column: np.flatnonzero(np.isfinite(labels[column]) & eligible[:, column]) for column in columns}
    weekly = set(sample_columns(len(fp.sessions)))
    ranks = {column: pd.Series(labels[column]).rank(pct=True).to_numpy() for column in columns}
    years = years or list(range(FIRST_YEAR, fp.sessions[-1].year + 1))
    order = [year for year in GATE_YEARS if year in years] + [year for year in years if year not in GATE_YEARS]
    meta_path = out / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    meta.update({"version": version, "label": "deep2", "loss": kind, "features": list(names), "sequence": list(SEQUENCE),
                 "market": list(MARKET), "length": LENGTH, "hidden": HIDDEN, "head": list(HEAD), "seeds": list(SEEDS),
                 "epochs": EPOCHS, "patience": PATIENCE, "held_out": HELD_OUT, "horizon": HORIZON,
                 "device": device_name()})
    meta.setdefault("years", {})
    stored = _load_scores(out, fp.symbols)
    against_path = out.parent / AGAINST / "meta.json"
    against = json.loads(against_path.read_text(encoding="utf-8")) if against_path.is_file() else {}
    for number, year in enumerate(order):
        if year not in GATE_YEARS and all(str(item) in meta["years"] for item in GATE_YEARS if item in years):
            check = gate_check(meta, against)
            meta["gate"] = check
            if not check["passed"]:
                meta["stopped"] = f"{GATE_YEARS[0]}～{GATE_YEARS[-1]} 沒過門檻，其他年度不訓練"
                break
        first = next((index for index, day in enumerate(fp.sessions) if day.year >= year), len(fp.sessions))
        known = [column for column in columns if column + HORIZON < first and rows[column].size >= 50]
        if len(known) < 120:
            continue
        cut = int(len(known) * (1 - HELD_OUT))
        held_days = known[cut:]
        train_days = [column for column in known[:cut] if column < held_days[0] - HORIZON]   # purged
        networks, curves = [], []
        for seed in SEEDS:
            state, curve = _fit(inputs, kind, seed, train_days, held_days, rows, labels)
            network = _network(inputs.sequence.shape[2], inputs.snapshot.shape[2]).to(inputs.on)
            network.load_state_dict(state)
            networks.append(network)
            curves.append(curve)
            torch.save(state, out / f"{year}-{seed}.pt")
            if job:
                job.update(current=f"{version}：{year} 年第 {seed + 1}/{len(SEEDS)} 個種子，保留期前 20 名多 {max(curve):+.4f}")
        session_columns = [column for column, day in enumerate(fp.sessions) if day.year == year and column >= LENGTH]
        for column in session_columns:
            live = np.flatnonzero(eligible[:, column])
            values = np.full(len(fp.symbols), np.nan, dtype=np.float32)
            if live.size:
                pairs = np.stack([np.full(live.size, column), live], axis=1)
                values[live] = _rank_average([inputs.score(network, pairs) for network in networks])
            stored[fp.sessions[column].isoformat()] = values
        ics, top20 = [], []
        for column in [column for column in session_columns if column in weekly and column in labels]:
            ic, gain = _top20(stored[fp.sessions[column].isoformat()], ranks[column], labels[column])
            if ic is not None:
                ics.append(ic)
            if gain is not None:
                top20.append(gain)
        meta["years"][str(year)] = {"train_days": len(train_days), "held_days": len(held_days), "weeks": len(top20),
                                    "ic": round(float(np.mean(ics)), 4) if ics else None,
                                    "top20_gain": round(float(np.mean(top20)), 5) if top20 else None,
                                    "held_curves": curves}
        _save_scores(out, stored, fp.symbols)
        meta["trained_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        if job:
            item = meta["years"][str(year)]
            job.update(done=number + 1, total=len(order),
                       current=f"{year}：樣本外 IC {item['ic']}、前 20 名多 {item['top20_gain']}")
    if all(str(year) in meta["years"] for year in GATE_YEARS):
        meta["gate"] = gate_check(meta, against)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def _load_scores(folder: Path, symbols: list[str]) -> dict[str, np.ndarray]:
    """The kept scores by day, aligned to ``symbols`` (NaN for a symbol the file does not have)."""
    path = folder / "deep.npz"
    if not path.is_file():
        return {}
    stored = np.load(path, allow_pickle=False)
    where = {symbol: index for index, symbol in enumerate(stored["symbols"].tolist())}
    take = np.array([where.get(symbol, -1) for symbol in symbols])
    output = {}
    for day, values in zip(stored["days"].tolist(), stored["scores"]):
        row = np.full(len(symbols), np.nan, dtype=np.float32)
        row[take >= 0] = values[take[take >= 0]].astype(np.float32)
        output[day] = row
    return output


def _save_scores(folder: Path, kept: dict[str, np.ndarray], symbols: list[str]) -> None:
    days = sorted(kept)
    partial = folder / "deep.partial.npz"
    with open(partial, "wb") as handle:
        np.savez_compressed(handle, days=np.array(days), symbols=np.array(symbols),
                            scores=np.stack([kept[day] for day in days]).astype(np.float16) if days else np.zeros((0, 0)))
    partial.replace(folder / "deep.npz")


def scores(fp, folder: str | Path) -> np.ndarray:
    """sessions × symbols from ``deep.npz``; the sessions after the last one kept (the forward days) scored by the
    latest year's ten networks, their ranks averaged."""
    folder = Path(folder)
    output = np.full((len(fp.sessions), len(fp.symbols)), np.nan, dtype=np.float32)
    kept = _load_scores(folder, fp.symbols)
    for day, values in kept.items():
        position = fp.index.get(date.fromisoformat(day))
        if position is not None:
            output[position] = values
    if not kept:
        return output
    last = max(kept)
    later = [position for position, day in enumerate(fp.sessions) if day.isoformat() > last and position >= LENGTH]
    saved = sorted(folder.glob("*-*.pt"))
    if later and saved:
        import torch

        from quant_platform.research.model import eligibility

        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        eligible = eligibility(fp)
        inputs = Inputs(fp, eligible, tuple(meta["features"]))
        year = max(int(path.stem.split("-")[0]) for path in saved)
        networks = []
        for path in [path for path in saved if path.stem.startswith(f"{year}-")]:
            network = _network(inputs.sequence.shape[2], inputs.snapshot.shape[2]).to(inputs.on)
            network.load_state_dict(torch.load(path, weights_only=True, map_location=inputs.on))
            networks.append(network)
        for position in later:
            live = np.flatnonzero(eligible[:, position])
            if live.size:
                pairs = np.stack([np.full(live.size, position), live], axis=1)
                output[position, live] = _rank_average([inputs.score(network, pairs) for network in networks])
    return output


def _top(scores: np.ndarray, count: int = 20) -> set[int]:
    known = np.flatnonzero(np.isfinite(scores))
    return set(known[np.argsort(-scores[known])[:count]].tolist()) if known.size >= count else set()


def diagnose(fp, models_root: str | Path, version: str = "dl-2.1.0", against: str = AGAINST) -> dict[str, object]:
    """Stage 0 of the review (§2.4; free, not a trial): per year, on the weekly samples — the two models' rank
    correlation, how many of their top 20 are the same, the top-20 gain of each and of the 50/50 rank blend, and
    the share of each model's top 20 replaced from one week to the next (what the rule's costs come from)."""
    from quant_platform.research.model import (
        HORIZON,
        _spearman,
        eligibility,
        label_block,
        sample_columns,
    )
    from quant_platform.research.model import scores as model_scores

    root = Path(models_root)
    mine, theirs = model_scores(fp, root / version), model_scores(fp, root / against)
    eligible = eligibility(fp)
    columns = sample_columns(len(fp.sessions))
    gains = label_block(fp, columns, eligible, "excess", HORIZON)
    years: dict[str, dict[str, list[float]]] = {}
    previous: dict[str, set[int]] = {}
    for slot, column in enumerate(columns):
        both = np.isfinite(mine[column]) & np.isfinite(theirs[column]) & eligible[:, column]
        if both.sum() < 50:
            continue
        a = np.where(both, mine[column], np.nan)
        b = np.where(both, theirs[column], np.nan)
        blend = (pd.Series(a).rank(pct=True) + pd.Series(b).rank(pct=True)).to_numpy() / 2
        item = years.setdefault(str(fp.sessions[column].year), {key: [] for key in (
            "correlation", "overlap", "gain_mine", "gain_theirs", "gain_blend", "turnover_mine", "turnover_theirs")})
        correlation = _spearman(a, b)
        if correlation is not None:
            item["correlation"].append(correlation)
        tops = {"mine": _top(a), "theirs": _top(b), "blend": _top(blend)}
        item["overlap"].append(len(tops["mine"] & tops["theirs"]) / 20)
        outcome = gains[slot]
        if np.isfinite(outcome[both]).sum() >= 50:
            known = both & np.isfinite(outcome)
            for name in ("mine", "theirs", "blend"):
                chosen = [row for row in tops[name] if np.isfinite(outcome[row])]
                if chosen:
                    item[f"gain_{name}"].append(float(outcome[chosen].mean() - outcome[known].mean()))
        for name in ("mine", "theirs"):
            if previous.get(name):
                item[f"turnover_{name}"].append(1 - len(tops[name] & previous[name]) / 20)
            previous[name] = tops[name]
    table = {year: {key: round(float(np.mean(values)), 4) if values else None for key, values in item.items()}
             for year, item in sorted(years.items())}
    summary = {key: round(float(np.mean([row[key] for year, row in table.items() if int(year) in GATE_YEARS
                                        and row[key] is not None])), 4)
               for key in ("correlation", "overlap", "gain_mine", "gain_theirs", "gain_blend", "turnover_mine",
                           "turnover_theirs")}
    result = {"version": version, "against": against, "development": summary, "years": table,
              "built_at": datetime.now(UTC).isoformat(timespec="seconds")}
    (root / version / "diagnostics.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
