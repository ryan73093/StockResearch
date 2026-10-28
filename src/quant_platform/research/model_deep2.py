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

LOSSES = {"dl-2.0.0": "listnet", "dl-2.1.0": "top10", "dl-3.0.0": "top10", "dl-2.2.0": "top10", "dl-2.4.0": "top10",
          "dl-2.5.0": "top10", "dl-2.6.0": "top10", "dl-2.7.0": "top10"}
# 2026-10-20 (使用者：技術分析機器人有沒有在優化？): the technical robot's third version — dl-2.1.0 seeing prices and
# volumes only: the sequence without the chip columns and the snapshot of the robot's 20 technical factors
# (tech_rl.TECH_FEATURES) instead of model G's; judged against the technical ensemble gbm-2.1.0 (PLAIN_AGAINST)
TECHNICAL = {"dl-2.5.0"}
# 2026-10-20 (使用者：持續優化每一個策略): dl-2.1.0 refitted every month instead of every year — each month's ten networks
# learn from every label that ended before the month's first session and score that month (the forward accounts'
# models are retrained monthly too); judged against dl-2.1.0 itself
MONTHLY = {"dl-2.6.0"}
PLAIN_AGAINST = {"dl-2.5.0": "gbm-2.1.0", "dl-2.6.0": "dl-2.1.0", "dl-2.7.0": "dl-2.1.0"}
# 2026-10-22 (集保因子第五版; research_method §7): dl-2.1.0 whose snapshot is model I's features (gbm-2.5.0: model G's
# and the holder brackets) instead of model G's; judged against dl-2.1.0 itself (PLAIN_AGAINST)
SNAPSHOT_OF = {"dl-2.7.0": "gbm-2.5.0"}
# 2026-10-20 (使用者：持續優化每一個策略; the factor correlations: dl-2.1.0 ran −0.70 with low volatility): dl-2.1.0 learning
# the day's top tenth of the excess return divided by the stock's own 60-session volatility (known on the day) — a
# volatile stock no longer reaches the top tenth for being volatile; still judged (held-out choice, gate) on the excess
RISK_ADJUSTED = {"dl-2.4.0"}
# 2026-10-18 (roadmap S9-W15): dl-2.2.0 is dl-2.1.0 learning from every listed and TPEx stock that trades normally
# (WIDE_*), its features normalised among them; the epochs are still chosen, and the years judged, on the rules'
# eligible listed stocks only — as dl-2.1.0. Trained on the "all" panel (MODELS[...]["universe"]).
WIDE = {"dl-2.2.0"}
WIDE_PRICE, WIDE_TURNOVER, WIDE_AGE = 1.0, 1_000_000, 120
# 2026-10-17 (docs/research_dl_exits_review.md D3): dl-3.0.0 is dl-2.1.0 with the day's stocks looking at each other
# (four-head self-attention across the day's eligible stocks) and the market's state gating the sequence inputs
# (MASTER-style); its gate is against dl-2.1.0 (D3_AGAINST)
ARCHITECTURES = {"dl-3.0.0": "attention"}
HEADS = 4
WIDTH = 64
D3_AGAINST = "dl-2.1.0"                 # dl-3.0.0's and dl-2.2.0's yardstick (AGAINST_OF)
AGAINST_OF = {"dl-3.0.0": D3_AGAINST, "dl-2.2.0": D3_AGAINST}
D3_MARGIN = 0.002
D3_COUNT = 4
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
# 2026-10-28 (使用者 10/26-27：2015～2020 不當門檻、短歷史加重，同類的地方直接照做; research_method §7「技術分析機器人
# 第四版」「深度學習舊版本用 2021～2025 重判」): every version the 2016-2020 gate stopped, again as a new version with
# all of the old one's settings (REGATED: new -> old), gated on 2021-2025 instead — trained first, the rest only past it
REGATED = {"dl-2.5.1": "dl-2.5.0", "dl-2.0.1": "dl-2.0.0", "dl-2.2.1": "dl-2.2.0", "dl-2.4.1": "dl-2.4.0",
           "dl-2.7.1": "dl-2.7.0", "dl-3.0.1": "dl-3.0.0"}
RECENT_GATED = set(REGATED)
RECENT_GATE_YEARS = (2021, 2022, 2023, 2024, 2025)
for _new, _old in REGATED.items():
    LOSSES[_new] = LOSSES[_old]
    for _group in (TECHNICAL, MONTHLY, RISK_ADJUSTED, WIDE):
        if _old in _group:
            _group.add(_new)
    for _table in (PLAIN_AGAINST, SNAPSHOT_OF, ARCHITECTURES, AGAINST_OF):
        if _old in _table:
            _table[_new] = _table[_old]


# 2026-10-28 (research_method §7「RL 第九版：直接強化學習」, written before the run): dl-4.0.0 is dl-2.1.0 learning the
# portfolio itself instead of a ranking — Moody & Saffell (2001) direct reinforcement (the trading objective after costs
# is the training signal), Zhang, Zohren & Roberts (2020) on portfolios, AlphaStock (Wang et al. 2019) on a
# cross-section: each day's weights are the softmax of the scores over the day's stocks; the loss is minus their
# 20-session excess over DIRECT_WEEKS, less DIRECT_COST for each unit of turnover against the same stocks' weights a week
# (model.STEP sessions) later, plus a penalty once the weights hold fewer than DIRECT_SPREAD stocks' worth (1 / Σw²).
# The epoch kept is the best held-out top 20 after the same cost; its gate (2021-2025) is against dl-2.1.0 after cost.
DIRECT_COST = 0.005                     # a full swap of the list, sold and bought (RL 第四、五版 the same)
DIRECT_WEEKS = 4                        # the label's 20 sessions are four weekly decisions
DIRECT_SPREAD = 20                      # the weights hold at least 20 stocks' worth (the rules hold 10 to 20)
DIRECT_AGAINST = "dl-2.1.0"
DIRECT_BETTER = 3                       # years of five better than dl-2.1.0 after cost
LOSSES["dl-4.0.0"] = "direct"
RECENT_GATED.add("dl-4.0.0")


def gate_years(version: str) -> tuple[int, ...]:
    return RECENT_GATE_YEARS if version in RECENT_GATED else GATE_YEARS
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


def sequence_inputs(fp, eligible: np.ndarray, chips: bool = True) -> np.ndarray:
    """session × symbol × SEQUENCE (float16): the day's z-scores (see the module text); without the CHIPS columns
    when ``chips`` is false (TECHNICAL)."""
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
    columns += [fp.matrix(name) for name in CHIPS] if chips else []
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


def _network(sequence: int, snapshot: int, architecture: str = "gru"):
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

        def forward(self, steps, now, market, groups=None):
            _out, last = self.gru(steps)
            return self.head(torch.cat([last[-1], now, market], dim=1)).squeeze(-1)

    class Attention(torch.nn.Module):
        """D3: the market's state gates the sequence inputs; each stock's encoding then attends to the day's others."""

        def __init__(self) -> None:
            super().__init__()
            self.gate = torch.nn.Sequential(torch.nn.Linear(len(MARKET), sequence), torch.nn.Sigmoid())
            self.gru = torch.nn.GRU(sequence, HIDDEN, batch_first=True)
            self.encode = torch.nn.Sequential(torch.nn.Linear(HIDDEN + snapshot, WIDTH), torch.nn.ReLU())
            self.attention = torch.nn.MultiheadAttention(WIDTH, HEADS, dropout=DROPOUT, batch_first=True)
            self.norm = torch.nn.LayerNorm(WIDTH)
            self.head = torch.nn.Sequential(torch.nn.Linear(WIDTH + len(MARKET), HEAD[1]), torch.nn.ReLU(),
                                            torch.nn.Dropout(DROPOUT), torch.nn.Linear(HEAD[1], 1))

        def forward(self, steps, now, market, groups=None):
            _out, last = self.gru(steps * 2 * self.gate(market)[:, None, :])      # a gate of 0.5 leaves them
            encoded = self.encode(torch.cat([last[-1], now], dim=1))
            mixed = []
            for start, end in groups or [(0, len(encoded))]:                    # one day's stocks at a time
                day = encoded[start:end].unsqueeze(0)
                seen, _weights = self.attention(day, day, day, need_weights=False)
                mixed.append(self.norm(day + seen).squeeze(0))
            return self.head(torch.cat([torch.cat(mixed), market], dim=1)).squeeze(-1)

    return Attention() if architecture == "attention" else Network()


def _runs(sessions: np.ndarray) -> list[tuple[int, int]]:
    """The (start, end) of each run of one session in ``sessions`` (pairs come grouped by day)."""
    if not len(sessions):
        return []
    cuts = np.flatnonzero(np.diff(sessions)) + 1
    from itertools import pairwise

    return list(pairwise([0, *cuts.tolist(), len(sessions)]))


class Inputs:
    """The three input blocks on the device, and the batches of (session, symbol) pairs drawn from them."""

    def __init__(self, fp, eligible: np.ndarray, names: tuple[str, ...], chips: bool = True) -> None:
        import torch

        from quant_platform.research.gpu import device

        self.on = device()
        self.sequence = torch.from_numpy(sequence_inputs(fp, eligible, chips)).to(self.on)
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
        """Scores in the pairs' order; the chunks hold whole days (a day's stocks attend to each other in D3)."""
        import torch

        output = []
        network.eval()
        runs = _runs(pairs[:, 0]) if len(pairs) else []
        with torch.no_grad():
            index = 0
            while index < len(runs):
                first = runs[index][0]
                last = index
                while last + 1 < len(runs) and runs[last + 1][1] - first <= size:
                    last += 1
                chunk = pairs[first: runs[last][1]]
                groups = [(start - first, end - first) for start, end in runs[index: last + 1]]
                output.append(network(*self.batch(chunk), groups).float().cpu().numpy())
                index = last + 1
        network.train()
        return np.concatenate(output) if output else np.zeros(0, dtype=np.float32)


def _loss(kind: str, predicted, target, groups: list[tuple[int, int]], symbols: np.ndarray | None = None):
    """The registered losses: ``listnet`` Huber + ListNet per day (top-weighted), ``top10`` the day's top tenth,
    ``direct`` (dl-4.0.0) the portfolio's excess after turnover — the groups come in pairs, a day and the day a week
    later, and ``symbols`` (one per row) lines the two days' weights up."""
    import torch

    functional = torch.nn.functional
    if kind == "direct":
        index = torch.as_tensor(symbols, device=predicted.device)
        size = int(symbols.max()) + 1
        gains, spread, held = [], [], []
        for start, end in groups:
            weight = torch.softmax(predicted[start:end], 0)
            gains.append((weight * target[start:end]).sum())
            spread.append(torch.relu((weight * weight).sum() - 1 / DIRECT_SPREAD))
            held.append(torch.zeros(size, device=predicted.device, dtype=weight.dtype).index_add(0, index[start:end], weight))
        turnover = torch.stack([0.5 * (held[number + 1] - held[number]).abs().sum() for number in range(0, len(held) - 1, 2)])
        return 100 * (-(torch.stack(gains).mean() / DIRECT_WEEKS - DIRECT_COST * turnover.mean()) + torch.stack(spread).mean())
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


def _fit(inputs: Inputs, kind: str, seed: int, train_days, held_days, rows, labels,
         architecture: str = "gru", judged: tuple | None = None) -> tuple[dict, list[float]]:
    """One seed: the network with the best held-out top-20 gain, and the curve. ``judged`` (rows, labels): the
    stocks and labels the held-out days are judged on, when not the ones learned from (dl-2.2.0)."""
    import torch

    from quant_platform.research.model import STEP

    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    learned = set(train_days)
    anchors = [day for day in train_days if day + STEP in learned] if kind == "direct" else None   # dl-4.0.0's pairs
    network = _network(inputs.sequence.shape[2], inputs.snapshot.shape[2], architecture).to(inputs.on)
    optimiser = torch.optim.AdamW(network.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
    held_rows, held_labels = judged or (rows, labels)
    held_pairs = np.concatenate([np.stack([np.full(held_rows[day].size, day), held_rows[day]], axis=1)
                                 for day in held_days])
    best, curve, waited = (-np.inf, None), [], 0
    for _epoch in range(EPOCHS):
        if anchors is not None:          # half the anchors an epoch, each with its day a week later: as many days
            order = rng.permutation(len(anchors))[:len(anchors) // 2]
            half = DAYS_PER_BATCH // 2
            batches = [[day for index in order[start:start + half] for day in (anchors[index], anchors[index] + STEP)]
                       for start in range(0, len(order), half)]
        else:
            order = rng.permutation(len(train_days))
            batches = [[train_days[index] for index in order[start:start + DAYS_PER_BATCH]]
                       for start in range(0, len(order), DAYS_PER_BATCH)]
        for chosen in batches:
            groups, pairs, target, at = [], [], [], 0
            for day in chosen:
                count = rows[day].size
                groups.append((at, at + count))
                at += count
                pairs.append(np.stack([np.full(count, day), rows[day]], axis=1))
                target.append(labels[day][rows[day]])
            stacked = np.concatenate(pairs)
            loss = _loss(kind, network(*inputs.batch(stacked), groups),
                         torch.from_numpy(np.concatenate(target).astype(np.float32)).to(inputs.on), groups, stacked[:, 1])
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()
        predicted = inputs.score(network, held_pairs)
        gains, tops, at = [], {}, 0
        for day in held_days:
            count = held_rows[day].size
            value = _gain(predicted[at:at + count], held_labels[day][held_rows[day]])
            if kind == "direct" and value is not None:
                tops[day] = set(held_rows[day][np.argsort(-predicted[at:at + count])[:20]].tolist())
            at += count
            if value is not None:
                gains.append(value)
        held = float(np.mean(gains)) if gains else -np.inf
        turns = [1 - len(tops[day] & tops[day + STEP]) / 20 for day in tops if day + STEP in tops]
        if turns:                        # dl-4.0.0: the held-out top 20 after the same cost
            held -= DIRECT_COST * DIRECT_WEEKS * float(np.mean(turns))
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


def gate_check(meta: dict, against: dict, years: tuple[int, ...] = GATE_YEARS) -> dict[str, object]:
    """The registered gate on two metas' yearly top-20 gains (see the module text); ``years`` (2026-10-28) the gate's."""
    mine = {year: (meta.get("years") or {}).get(str(year), {}).get("top20_gain") for year in years}
    theirs = [(against.get("years") or {}).get(str(year), {}).get("top20_gain") for year in years]
    known = [value for value in mine.values() if value is not None]
    theirs = [value for value in theirs if value is not None]
    average = float(np.mean(known)) if len(known) == len(years) else None
    yardstick = float(np.mean(theirs)) if theirs else None
    above = sum(1 for value in known if value >= GATE_FLOOR)
    return {"version": meta.get("version"), "against": against.get("version"), "years": list(years),
            "top20_gain": average, "against_top20_gain": yardstick, "years_above_floor": above,
            "floor": GATE_FLOOR, "passed": average is not None and yardstick is not None and average >= yardstick
            and above >= GATE_COUNT}


def wide_mask(fp) -> np.ndarray:
    """symbols × sessions: every stock of the panel that trades normally — dl-2.2.0 learns from these."""
    with np.errstate(invalid="ignore"):
        return (fp.panel.close >= WIDE_PRICE) & (fp.turnover_20 >= WIDE_TURNOVER) & (fp.age >= WIDE_AGE)


def gate_check_d3(meta: dict, against: dict, years: tuple[int, ...] = GATE_YEARS) -> dict[str, object]:
    """D3's registered gate: over 2016–2020 (``years``) the average top-20 gain at least D3_MARGIN above dl-2.1.0's,
    and better than it in at least D3_COUNT of the five years."""
    mine = [(meta.get("years") or {}).get(str(year), {}).get("top20_gain") for year in years]
    theirs = [(against.get("years") or {}).get(str(year), {}).get("top20_gain") for year in years]
    complete = all(value is not None for value in mine + theirs)
    average = float(np.mean(mine)) if complete else None
    yardstick = float(np.mean(theirs)) if complete else None
    better = sum(1 for a, b in zip(mine, theirs, strict=True) if a is not None and b is not None and a > b)
    return {"version": meta.get("version"), "against": against.get("version"), "years": list(years),
            "top20_gain": average, "against_top20_gain": yardstick, "years_better": better, "margin": D3_MARGIN,
            "passed": complete and average >= yardstick + D3_MARGIN and better >= D3_COUNT}


def turnover_by_year(folder: Path, years) -> dict[int, float]:
    """dl-4.0.0's gate: each year's average share of the kept scores' top 20 replaced from one session to the session
    model.STEP later (weekly), from deep.npz."""
    from quant_platform.research.model import STEP

    path = Path(folder) / "deep.npz"
    if not path.is_file():
        return {}
    stored = np.load(path, allow_pickle=False)
    days, values = stored["days"].tolist(), stored["scores"]
    output = {}
    for year in years:
        tops = []
        for position in [index for index, day in enumerate(days) if day.startswith(str(year))][::STEP]:
            row = values[position].astype(np.float32)
            live = np.flatnonzero(np.isfinite(row))
            tops.append(set(live[np.argsort(-row[live])[:20]].tolist()) if live.size >= 50 else None)
        turns = [1 - len(first & second) / 20 for first, second in zip(tops, tops[1:]) if first and second]
        if turns:
            output[year] = float(np.mean(turns))
    return output


def gate_check_direct(meta: dict, against: dict, mine: dict, theirs: dict,
                      years: tuple[int, ...] = RECENT_GATE_YEARS) -> dict[str, object]:
    """dl-4.0.0's registered gate: each year's top-20 gain less DIRECT_COST × DIRECT_WEEKS × its weekly top-20 turnover
    (``mine``, ``theirs``: turnover_by_year); over ``years`` the average at least dl-2.1.0's, and better than it in at
    least DIRECT_BETTER of them."""
    def net(item: dict, turnover: dict) -> list[float | None]:
        gains = [(item.get("years") or {}).get(str(year), {}).get("top20_gain") for year in years]
        return [None if gain is None or year not in turnover else gain - DIRECT_COST * DIRECT_WEEKS * turnover[year]
                for gain, year in zip(gains, years, strict=True)]

    own, other = net(meta, mine), net(against, theirs)
    complete = all(value is not None for value in own + other)
    better = sum(1 for a, b in zip(own, other, strict=True) if a is not None and b is not None and a > b)
    return {"version": meta.get("version"), "against": against.get("version"), "years": list(years),
            "net_gain": float(np.mean(own)) if complete else None,
            "against_net_gain": float(np.mean(other)) if complete else None,
            "turnover": {str(year): round(mine[year], 4) for year in years if year in mine},
            "against_turnover": {str(year): round(theirs[year], 4) for year in years if year in theirs},
            "years_better": better, "passed": complete and np.mean(own) >= np.mean(other) and better >= DIRECT_BETTER}


def _judge(version: str, meta: dict, root: Path) -> dict[str, object]:
    """The version's registered gate on its meta (dl-3.0.0 against dl-2.1.0; dl-2.5.0 against gbm-2.1.0; dl-4.0.0
    against dl-2.1.0 after cost; the others against gbm-2.2.0)."""
    if LOSSES.get(version) == "direct":
        path = root / DIRECT_AGAINST / "meta.json"
        other = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        years = gate_years(version)
        return gate_check_direct(meta, other, turnover_by_year(root / version, years),
                                 turnover_by_year(root / DIRECT_AGAINST, years), years)
    against = AGAINST_OF.get(version) or PLAIN_AGAINST.get(version, AGAINST)
    path = root / against / "meta.json"
    other = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    years = gate_years(version)
    return gate_check_d3(meta, other, years) if version in AGAINST_OF else gate_check(meta, other, years)


def gate(models_root: str | Path, version: str) -> dict[str, object]:
    root = Path(models_root)
    against = DIRECT_AGAINST if LOSSES.get(version) == "direct" else (AGAINST_OF.get(version)
                                                                      or PLAIN_AGAINST.get(version, AGAINST))
    paths = root / version / "meta.json", root / against / "meta.json"
    if not all(path.is_file() for path in paths):
        return {"version": version, "passed": False, "reason": "not trained"}
    return _judge(version, json.loads(paths[0].read_text(encoding="utf-8")), root)


def _prepare(fp, out: Path, version: str):
    """The inputs, labels and sample rows a version learns from (train, train_until)."""
    from quant_platform.research.model import HORIZON, eligibility, label_block, trained_features

    kind = LOSSES[version]
    architecture = ARCHITECTURES.get(version, "gru")
    eligible = eligibility(fp)
    if version in WIDE:          # judged and scored on the eligible listed stocks only, as dl-2.1.0 (not TPEx ".TWO")
        eligible = eligible & np.array([symbol.endswith(".TW") for symbol in fp.symbols])[:, None]
    learned = wide_mask(fp) if version in WIDE else eligible      # the stocks it learns from (and normalises among)
    if version in TECHNICAL:
        from quant_platform.research.tech_rl import TECH_FEATURES

        names = tuple(TECH_FEATURES)
    else:
        snapshot = SNAPSHOT_OF.get(version, SNAPSHOT_VERSION)
        names = trained_features(out.parent / snapshot, snapshot)
    inputs = Inputs(fp, learned, names, chips=version not in TECHNICAL)
    start = LENGTH + 200
    columns = list(range(start, len(fp.sessions)))
    excess = label_block(fp, columns, eligible, "excess", HORIZON)                 # column × symbol, judged on
    labels = {column: excess[slot] for slot, column in enumerate(columns)}
    rows = {column: np.flatnonzero(np.isfinite(labels[column]) & eligible[:, column]) for column in columns}
    judged = None
    if version in RISK_ADJUSTED:
        deviation = -fp.matrix("low_volatility_60")                               # stored negated: the daily std
        judged = (rows, labels)
        labels = {column: np.where(deviation[:, column] > 0, labels[column] / np.where(deviation[:, column] > 0,
                                                                                    deviation[:, column], 1.0), np.nan)
                  for column in columns}
        rows = {column: np.flatnonzero(np.isfinite(labels[column]) & eligible[:, column]) for column in columns}
    if version in WIDE:
        wide = label_block(fp, columns, learned, "excess", HORIZON)
        judged = (rows, labels)
        labels = {column: wide[slot] for slot, column in enumerate(columns)}
        rows = {column: np.flatnonzero(np.isfinite(labels[column]) & learned[:, column]) for column in columns}
    return kind, architecture, names, eligible, inputs, columns, labels, rows, judged


MONTHLY_DIR = "monthly"


def train_until(fp, out_dir: str | Path, version: str, cutoff, job=None) -> dict[str, object]:
    """2026-10-21 (使用者 2026-10-16：每月滾動，模型也要更新): the version's ten seeds refitted on every sample whose label
    ended by ``cutoff`` (the month's rolling cutoff), as the yearly fit does for a year; saved as
    monthly/<cutoff>-<seed>.pt, the sessions after the cutoff scored with them (monthly/<cutoff>.npz). ``scores``
    uses them for every session after the cutoff. The yearly networks and deep.npz stay as they are."""
    import torch

    from quant_platform.research.model import HORIZON

    torch.set_num_threads(4)
    out = Path(out_dir)
    target = out / MONTHLY_DIR
    target.mkdir(parents=True, exist_ok=True)
    kind, architecture, _names, eligible, inputs, columns, labels, rows, judged = _prepare(fp, out, version)
    first = next((index for index, day in enumerate(fp.sessions) if day > cutoff), len(fp.sessions))
    known = [column for column in columns if column + HORIZON < first and rows[column].size >= 50
             and (judged is None or judged[0][column].size >= 50)]
    cut = int(len(known) * (1 - HELD_OUT))
    held_days = known[cut:]
    train_days = [column for column in known[:cut] if column < held_days[0] - HORIZON]           # purged
    networks, curves = [], []
    for seed in SEEDS:
        state, curve = _fit(inputs, kind, seed, train_days, held_days, rows, labels, architecture, judged)
        network = _network(inputs.sequence.shape[2], inputs.snapshot.shape[2], architecture).to(inputs.on)
        network.load_state_dict(state)
        networks.append(network)
        curves.append(curve)
        torch.save(state, target / f"{cutoff.isoformat()}-{seed}.pt")
        if job:
            job.update(current=f"{version}：重訓到 {cutoff}，第 {seed + 1}/{len(SEEDS)} 個種子，保留期前 20 名多 {max(curve):+.4f}",
                       force=True)
    kept = {}
    for column in range(first, len(fp.sessions)):
        live = np.flatnonzero(eligible[:, column])
        values = np.full(len(fp.symbols), np.nan, dtype=np.float32)
        if live.size:
            pairs = np.stack([np.full(live.size, column), live], axis=1)
            values[live] = _rank_average([inputs.score(network, pairs) for network in networks])
        kept[fp.sessions[column].isoformat()] = values
    days = sorted(kept)
    with open(target / f"{cutoff.isoformat()}.npz", "wb") as handle:
        np.savez_compressed(handle, days=np.array(days), symbols=np.array(fp.symbols),
                            scores=np.stack([kept[day] for day in days]).astype(np.float16) if days else np.zeros((0, 0)))
    return {"version": version, "cutoff": cutoff.isoformat(), "train_days": len(train_days), "held_days": len(held_days),
            "label_until": fp.sessions[known[-1] + HORIZON].isoformat() if known else None,
            "held_best": [round(max(curve), 5) for curve in curves], "scored_days": len(days),
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds")}


def train(fp, out_dir: str | Path, version: str, years: list[int] | None = None, job=None,
          only_months: list[int] | None = None, gate: bool = True) -> dict[str, object]:
    """Fit ten seeds per year, the development years first; the other years only past the gate (module text).
    2026-10-21 (使用者：每個月都要跑那麼久，我時間很多是不是): ``only_months`` refits only those months of a MONTHLY
    version (the new month, once), the year's other months kept as they were."""
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
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    kind, architecture, names, eligible, inputs, columns, labels, rows, judged = _prepare(fp, out, version)
    weekly = set(sample_columns(len(fp.sessions)))
    report_labels = judged[1] if judged else labels
    ranks = {column: pd.Series(report_labels[column]).rank(pct=True).to_numpy() for column in columns}
    years = years or list(range(FIRST_YEAR, fp.sessions[-1].year + 1))
    gated = gate_years(version)
    order = [year for year in gated if year in years] + [year for year in years if year not in gated]
    meta_path = out / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    meta.update({"version": version, "label": "deep2", "loss": kind, "architecture": architecture,
                 "learned_from": "listed and TPEx, trading normally" if version in WIDE else "eligible listed",
                 "features": list(names),
                 "sequence": [name for name in SEQUENCE if version not in TECHNICAL or name not in CHIPS],
                 "market": list(MARKET), "length": LENGTH, "hidden": HIDDEN, "head": list(HEAD), "seeds": list(SEEDS),
                 "epochs": EPOCHS, "patience": PATIENCE, "held_out": HELD_OUT, "horizon": HORIZON,
                 "device": device_name()})
    meta.setdefault("years", {})
    stored = _load_scores(out, fp.symbols)
    for number, year in enumerate(order):
        # 2026-10-24: gate=False — the stress test retrains a version that already passed its gate (research/stress.py)
        if gate and year not in gated and all(str(item) in meta["years"] for item in gated if item in years):
            check = _judge(version, meta, out.parent)
            meta["gate"] = check
            if not check["passed"]:
                meta["stopped"] = f"{gated[0]}～{gated[-1]} 沒過門檻，其他年度不訓練"
                break
        session_columns = [column for column, day in enumerate(fp.sessions) if day.year == year and column >= LENGTH]
        # the periods refitted: the year (one fit) or each month of it (MONTHLY); the last period's networks are kept
        months = sorted({fp.sessions[column].month for column in session_columns}) if version in MONTHLY else [None]
        if only_months and version in MONTHLY:
            months = [month for month in months if month in only_months]
        train_days, held_days, curves = [], [], []
        for month in months:
            period = [column for column in session_columns if month is None or fp.sessions[column].month == month]
            first = period[0] if month is not None and period else next(
                (index for index, day in enumerate(fp.sessions) if day.year >= year), len(fp.sessions))
            known = [column for column in columns if column + HORIZON < first and rows[column].size >= 50
                     and (judged is None or judged[0][column].size >= 50)]
            if len(known) < 120:
                continue
            cut = int(len(known) * (1 - HELD_OUT))
            held_days = known[cut:]
            train_days = [column for column in known[:cut] if column < held_days[0] - HORIZON]   # purged
            networks, curves = [], []
            for seed in SEEDS:
                state, curve = _fit(inputs, kind, seed, train_days, held_days, rows, labels, architecture, judged)
                network = _network(inputs.sequence.shape[2], inputs.snapshot.shape[2], architecture).to(inputs.on)
                network.load_state_dict(state)
                networks.append(network)
                curves.append(curve)
                torch.save(state, out / f"{year}-{seed}.pt")
                if job:
                    when = f"{year} 年" + (f" {month} 月" if month is not None else "")
                    job.update(current=f"{version}：{when}第 {seed + 1}/{len(SEEDS)} 個種子，保留期前 20 名多 {max(curve):+.4f}")
            for column in period:
                live = np.flatnonzero(eligible[:, column])
                values = np.full(len(fp.symbols), np.nan, dtype=np.float32)
                if live.size:
                    pairs = np.stack([np.full(live.size, column), live], axis=1)
                    values[live] = _rank_average([inputs.score(network, pairs) for network in networks])
                stored[fp.sessions[column].isoformat()] = values
        if not train_days:
            continue
        ics, top20 = [], []
        for column in [column for column in session_columns if column in weekly and column in report_labels]:
            ic, gain = _top20(stored[fp.sessions[column].isoformat()], ranks[column], report_labels[column])
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
        meta["gate"] = _judge(version, meta, out.parent)
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
        inputs = Inputs(fp, eligible, tuple(meta["features"]),
                        chips=len(meta.get("sequence") or SEQUENCE) == len(SEQUENCE))
        year = max(int(path.stem.split("-")[0]) for path in saved)
        networks = []
        for path in [path for path in saved if path.stem.startswith(f"{year}-")]:
            network = _network(inputs.sequence.shape[2], inputs.snapshot.shape[2],
                               meta.get("architecture", "gru")).to(inputs.on)
            network.load_state_dict(torch.load(path, weights_only=True, map_location=inputs.on))
            networks.append(network)
        for position in later:
            live = np.flatnonzero(eligible[:, position])
            if live.size:
                pairs = np.stack([np.full(live.size, position), live], axis=1)
                output[position, live] = _rank_average([inputs.score(network, pairs) for network in networks])
    return _monthly(fp, folder, output)


def _monthly(fp, folder: Path, output: np.ndarray) -> np.ndarray:
    """2026-10-21: every session after a monthly cutoff takes that cutoff's scores (train_until); the latest cutoff
    before a session wins. A session after the scored ones is scored with that cutoff's networks."""
    target = folder / MONTHLY_DIR
    files = sorted(target.glob("*.npz")) if target.is_dir() else []
    if not files:
        return output
    for path in files:
        cutoff = date.fromisoformat(path.stem)
        stored = np.load(path, allow_pickle=False)
        where = {symbol: index for index, symbol in enumerate(stored["symbols"].tolist())}
        take = np.array([where.get(symbol, -1) for symbol in fp.symbols])
        for day, values in zip(stored["days"].tolist(), stored["scores"]):
            position = fp.index.get(date.fromisoformat(day))
            if position is None or fp.sessions[position] <= cutoff:
                continue
            row = np.full(len(fp.symbols), np.nan, dtype=np.float32)
            row[take >= 0] = values[take[take >= 0]].astype(np.float32)
            output[position] = row
    latest = date.fromisoformat(files[-1].stem)
    scored = set(np.load(files[-1], allow_pickle=False)["days"].tolist())
    missing = [position for position, day in enumerate(fp.sessions)
               if day > latest and day.isoformat() not in scored and position >= LENGTH]
    saved = sorted(target.glob(f"{latest.isoformat()}-*.pt"))
    if missing and saved:
        import torch

        from quant_platform.research.model import eligibility

        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
        eligible = eligibility(fp)
        inputs = Inputs(fp, eligible, tuple(meta["features"]),
                        chips=len(meta.get("sequence") or SEQUENCE) == len(SEQUENCE))
        networks = []
        for path in saved:
            network = _network(inputs.sequence.shape[2], inputs.snapshot.shape[2],
                               meta.get("architecture", "gru")).to(inputs.on)
            network.load_state_dict(torch.load(path, weights_only=True, map_location=inputs.on))
            networks.append(network)
        for position in missing:
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


def leak_check(history: Path, data, models_root: str | Path, version: str, days: list) -> dict[str, object]:
    """2026-10-17 (the cost-aware dl-2.1.0 rule grew to 3,659 萬 against 0050's 982 萬: check before believing it):
    for each day, the data cut at that day, the inputs built again from it, the networks of that day's year scoring
    the day's eligible stocks — compared stock by stock with the score kept from the full history (``deep.npz``).
    Any difference beyond the float16 the scores are kept in means the full-history score used something later."""
    import torch

    from quant_platform.research.leakcheck import truncated_panel
    from quant_platform.research.model import eligibility

    folder = Path(models_root) / version
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    results = {}
    for day in days:
        _cut, fp = truncated_panel(history, data, day)
        position = fp.index[day]
        kept = _load_scores(folder, fp.symbols).get(day.isoformat())
        eligible = eligibility(fp)
        inputs = Inputs(fp, eligible, tuple(meta["features"]))
        networks = []
        for path in sorted(folder.glob(f"{day.year}-*.pt")):
            network = _network(inputs.sequence.shape[2], inputs.snapshot.shape[2],
                               meta.get("architecture", "gru")).to(inputs.on)
            network.load_state_dict(torch.load(path, weights_only=True, map_location=inputs.on))
            networks.append(network)
        live = np.flatnonzero(eligible[:, position])
        if kept is None or not networks or not live.size:
            results[day.isoformat()] = {"checked": 0, "differ": 0, "reason": "no kept score or no networks"}
            continue
        pairs = np.stack([np.full(live.size, position), live], axis=1)
        again = _rank_average([inputs.score(network, pairs) for network in networks])
        before = kept[live]
        known = np.isfinite(before)
        gap = np.abs(again[known] - before[known])
        results[day.isoformat()] = {"checked": int(known.sum()), "differ": int((gap > 2e-3).sum()),
                                    "largest_gap": round(float(gap.max()), 5) if gap.size else None,
                                    "not_kept": int((~known).sum())}
    report = {"version": version, "days": [day.isoformat() for day in days], "results": results,
              "leak": any(item["differ"] for item in results.values()),
              "built_at": datetime.now(UTC).isoformat(timespec="seconds")}
    (folder / "leakcheck.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
