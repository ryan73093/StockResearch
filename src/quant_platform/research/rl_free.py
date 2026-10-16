"""RL robot 4.0 — a free agent (2026-10-16; 使用者：月線上續抱、每週決策是策略不是規則，不要給 RL 加一堆奇怪的限制；
RL 沒打贏 0050 之前不要停).

The robots 1.x to 3.0 were handed the platform's strategy choices as if they were rules — a decision every 20
sessions, the top 20 stocks at equal amounts, a keep zone, a monthly-average hold — and learned only factor
weights inside them, from 13 decisions a year. This one decides every session how much of the account each stock
gets and how much stays in cash; how many stocks, how long to hold and when to trade are its to learn. The only
rules are the exchange's and the costs: the engine executes its weights with whole shares and odd lots, limit-up
buys and limit-down sells refused, the minimum fees, the sell tax, the turnover cap, dividends as paid and an unsold
delisted stock worth nothing (``daily.simulate_daily(targets=...)``, a free rule).

Learning (direct reinforcement, Moody & Saffell 2001): a small network scores every eligible stock from its factor
percentiles (the robot 2.1's 47 factors and model scores, plus little lent-and-sold stock) and from its weight
yesterday after the day's moves (so it can learn to keep what it holds), a cash score from the market's state; the
weights are their softmax. The environment is differentiable: the reward of a session is the account's log return
to the next close — its weights times the stocks' returns, less the buy cost (fee, slippage) and the sell cost (fee,
tax, slippage) on what it traded — minus 0050's, and the gradient runs through 40-session stretches in order, the
weights carried from one session to the next. Every eligible stock and every session is a sample.

Walk-forward: for each test year the network learns only from sessions whose next close is before the year; the
last fifth of them is held out and the epoch with the best held-out reward is kept; three seeds, their weights
averaged. Weights below 0.1% are dropped. The rule (``rl_free``, free) is judged like every other: the same cash
flow into 0050, the same T0–T3 gates, from the first test session (2018).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

VERSION = "free-rl-4.0.0"
# 4.1.0 (2026-10-16): 4.0 learned to spread the account over about 800 stocks — free of costs in its training account,
# impossible in a NT$300,000 one. The training account is now the owner's: NT$300,000 then NT$10,000 on the 5th, every
# order paying at least the minimum fee (NT$20 an odd-lot order before 2020-10-26), an order whose minimum fee is over
# 2% not placed (the holding stays as it is, as the engine does), a holding worth less than one share impossible; the
# weights a sparsemax (any number of them exactly 0) instead of a softmax. What it learns is still its own.
VERSION_41 = "free-rl-4.1.0"
INITIAL, MONTHLY = 300_000.0, 10_000.0
HIDDEN = 32
EPOCHS = 40
CHUNK = 40
LEARNING_RATE = 1e-3
WEIGHT_DECAY = 1e-4
SEEDS = (0, 1, 2)
HELD_OUT = 0.2
FIRST_TEST_YEAR = 2018
SMALLEST = 0.001


def feature_names() -> list[str]:
    from quant_platform.research.rl_timing import ALL_VERSION, factor_names

    return [*factor_names(ALL_VERSION), "low_sbl_short"]


class Market:
    """Every session from the research start: features (session × symbol × factor percentiles among the eligible),
    who may be bought, next-close returns (−100% on a write-off), 0050's, the market's state."""

    def __init__(self, fp, benchmark: np.ndarray, costs) -> None:
        from quant_platform.research.daily import RECENT_START
        from quant_platform.research.model import eligibility

        names = feature_names()
        eligible = eligibility(fp)                                              # symbol × session
        sessions = len(fp.sessions)
        self.start = next(index for index, day in enumerate(fp.sessions) if day >= RECENT_START)
        self.days = list(fp.sessions)
        span = slice(self.start, sessions)
        count = sessions - self.start
        self.features = np.full((count, len(fp.symbols), len(names)), 0.5, dtype=np.float16)
        for index, name in enumerate(names):
            values = np.where(eligible, fp.matrix(name), np.nan)[:, span].T          # session × symbol
            ranks = pd.DataFrame(values).rank(axis=1, pct=True).to_numpy()
            self.features[:, :, index] = np.nan_to_num(ranks, nan=0.5).astype(np.float16)
        self.eligible = eligible[:, span].T.copy()                              # session × symbol
        prices = fp.panel.filled
        with np.errstate(divide="ignore", invalid="ignore"):
            moves = prices[:, 1:] / prices[:, :-1] - 1                          # symbol × (session → next)
        returns = np.zeros((len(fp.symbols), sessions))
        returns[:, :-1] = np.nan_to_num(moves, nan=0.0, posinf=0.0, neginf=0.0)
        for symbol, position in fp.gone().items():                             # written off at the next close
            row = fp.row.get(symbol)
            if row is not None and 0 < position < sessions:
                returns[row, position - 1] = -1.0
                returns[row, position:] = 0.0
        self.returns = returns[:, span].T.astype(np.float32)                   # session × symbol
        bench = np.zeros(sessions)
        bench[:-1] = benchmark[1:] / benchmark[:-1] - 1
        self.bench = np.nan_to_num(bench[span]).astype(np.float32)
        taiex = pd.Series(fp.market if fp.market is not None else np.full(sessions, np.nan))
        gap = (taiex / taiex.rolling(200, min_periods=50).mean() - 1).to_numpy()
        volatility = taiex.pct_change(fill_method=None).rolling(20, min_periods=10).std().to_numpy()
        trend = fp.matrix("trend_200")
        with np.errstate(invalid="ignore"):
            breadth = np.nanmean(np.where(eligible & np.isfinite(trend), trend > 0, np.nan), axis=0)
        self.state = np.stack([np.clip(np.nan_to_num(gap), -0.5, 0.5) * 4, np.nan_to_num(breadth, nan=0.5) * 2 - 1,
                               np.clip(np.nan_to_num(volatility), 0, 0.05) * 40 - 1], axis=1)[span].astype(np.float32)
        from quant_platform.research.costs import order_fee
        from quant_platform.research.stock_rules import SeedPlan

        dates = list(fp.sessions[self.start:])
        self.close = np.nan_to_num(fp.panel.close[:, span].T, nan=0.0).astype(np.float32)   # session × symbol, raw
        self.minimum_fee = np.array([order_fee(costs, 1, 100.0, day) for day in dates], dtype=np.float32)
        flows = dict(SeedPlan(INITIAL, MONTHLY).schedule(dates, dates[0], dates[-1]))
        self.monthly = np.array([0.0 if index == 0 else flows.get(day, 0.0) for index, day in enumerate(dates)],
                                dtype=np.float32)                         # the starting capital is set apart
        self.fee_rate = costs.fee_rate * costs.fee_discount
        self.cost_buy = costs.fee_rate * costs.fee_discount + costs.slippage_bps / 10_000
        self.cost_sell = self.cost_buy + dict(costs.tax_rates).get("stock", 0.003)
        self.names, self.symbols = names, list(fp.symbols)

    def session(self, day: date) -> int:
        return self.days.index(day) - self.start


def _policy(features: int, hidden: int):
    import torch

    class Policy(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.stock = torch.nn.Sequential(torch.nn.Linear(features + 1, hidden), torch.nn.Tanh(),
                                             torch.nn.Linear(hidden, 1))
            self.cash = torch.nn.Linear(3, 1)

        def forward(self, x, held, state, mask):
            score = self.stock(torch.cat([x, held[:, None] * 20.0], dim=1)).squeeze(1)
            score = score.masked_fill(~mask, float("-inf"))
            logits = torch.cat([score, self.cash(state)])
            weights = torch.softmax(logits, dim=0)
            return weights[:-1], weights[-1]

    return Policy()


def sparsemax(logits):
    """Martins & Astudillo (2016): the Euclidean projection on the simplex — weights that can be exactly 0."""
    import torch

    ordered, _ = torch.sort(logits, descending=True)
    running = ordered.cumsum(0)
    steps = torch.arange(1, len(logits) + 1, dtype=logits.dtype)
    support = (1 + steps * ordered) > running
    count = support.sum()
    threshold = (running[count - 1] - 1) / count
    return torch.clamp(logits - threshold, min=0.0)


def run_account(policy, market: Market, first: int, last: int, train: bool = False, optimiser=None,
                record: dict | None = None) -> np.ndarray:
    """4.1: the owner's account — the money, the minimum fees, the orders too small to place, whole shares."""
    import torch

    symbols = len(market.symbols)
    held = torch.zeros(symbols)
    wealth = INITIAL
    rewards, chunk = [], []
    for t in range(first, last):
        x = torch.from_numpy(market.features[t].astype(np.float32))
        mask = torch.from_numpy(market.eligible[t]) | (held.detach() > 1e-6)
        state = torch.from_numpy(market.state[t])
        with torch.set_grad_enabled(train):
            score = policy.stock(torch.cat([x, held[:, None] * 20.0], dim=1)).squeeze(1)
            score = torch.where(mask, score, torch.full_like(score, -1e4))
            target = sparsemax(torch.cat([score, policy.cash(state)]))[:-1]
            floor = 50.0 * float(market.minimum_fee[t])
            amount = (target - held).abs() * wealth
            placed = torch.sigmoid((amount - floor) / (0.25 * floor))          # too small to place: not traded
            weights = held + placed * (target - held)
            price = torch.from_numpy(market.close[t])
            possible = torch.sigmoid((weights * wealth - price) / (0.25 * price + 1e-6))   # less than one share
            weights = weights * torch.where(price > 0, possible, torch.zeros_like(possible))
            bought = torch.relu(weights - held)
            sold = torch.relu(held - weights)
            minimum = (placed * torch.relu(float(market.minimum_fee[t]) - market.fee_rate * amount)).sum() / wealth
            cost = bought.sum() * market.cost_buy + sold.sum() * market.cost_sell + minimum
            move = torch.from_numpy(market.returns[t])
            gross = (weights * move).sum()
            reward = torch.log1p(torch.clamp(gross - cost, min=-0.95)) - float(np.log1p(market.bench[t]))
        chunk.append(reward)
        rewards.append(float(reward.detach()))
        grown = float(1 + gross.detach() - cost.detach())
        after = wealth * grown
        added = float(market.monthly[t + 1]) if t + 1 < len(market.monthly) else 0.0
        held = weights * (1 + move) / (1 + gross - cost) * (after / (after + added))
        wealth = after + added
        if record is not None:
            keep = torch.nonzero(weights.detach() >= SMALLEST).squeeze(1).tolist()
            record[t] = {int(index): float(weights[index].detach()) for index in keep}
        if train and (len(chunk) == CHUNK or t == last - 1):
            loss = -torch.stack(chunk).mean()
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()
            chunk = []
            held = held.detach()
        elif not train:
            held = held.detach()
            chunk = []
    return np.array(rewards)


def run(policy, market: Market, first: int, last: int, train: bool = False, optimiser=None,
        record: dict | None = None) -> np.ndarray:
    """The policy from session ``first`` to ``last`` (exclusive; indexes from the research start), all cash at the
    start; returns the daily rewards. ``train``: a gradient step every CHUNK sessions. ``record``: weights kept."""
    import torch

    symbols = len(market.symbols)
    held = torch.zeros(symbols)
    cash = torch.tensor(1.0)
    rewards, chunk = [], []
    for t in range(first, last):
        x = torch.from_numpy(market.features[t].astype(np.float32))
        mask = torch.from_numpy(market.eligible[t]) | (held.detach() > 1e-6)
        state = torch.from_numpy(market.state[t])
        with torch.set_grad_enabled(train):
            weights, cash_weight = policy(x, held, state, mask)
            bought = torch.relu(weights - held).sum()
            sold = torch.relu(held - weights).sum()
            cost = bought * market.cost_buy + sold * market.cost_sell
            move = torch.from_numpy(market.returns[t])
            gross = (weights * move).sum()
            reward = torch.log1p(torch.clamp(gross - cost, min=-0.95)) - float(np.log1p(market.bench[t]))
        chunk.append(reward)
        rewards.append(float(reward.detach()))
        grown = 1 + gross - cost
        held = weights * (1 + move) / grown                                     # yesterday's weights after the move
        cash = cash_weight / grown
        if record is not None:
            keep = torch.nonzero(weights.detach() >= SMALLEST).squeeze(1).tolist()
            record[t] = {int(index): float(weights[index].detach()) for index in keep}
        if train and (len(chunk) == CHUNK or t == last - 1):
            loss = -torch.stack(chunk).mean()
            optimiser.zero_grad()
            loss.backward()
            optimiser.step()
            chunk = []
            held, cash = held.detach(), cash.detach()
        elif not train:
            held, cash = held.detach(), cash.detach()
            chunk = []
    return np.array(rewards)


def walk_forward(market: Market, out_dir: Path, job=None, version: str = VERSION) -> dict[str, object]:
    import torch

    torch.set_num_threads(4)                  # other pipelines run alongside
    step = run_account if version == VERSION_41 else run

    years = sorted({day.year for day in market.days[market.start:] if day.year >= FIRST_TEST_YEAR})
    targets: dict[str, dict[str, float]] = {}
    yearly = {}
    for number, year in enumerate(years):
        test = [index for index, day in enumerate(market.days[market.start:]) if day.year == year]
        cutoff = test[0]                                    # sessions before it have their next close by then
        fit_end = cutoff - 1
        held_from = int(fit_end * (1 - HELD_OUT))
        recorded: list[dict[int, dict[int, float]]] = []
        curves_train, curves_held, best_epochs, held_best, test_rewards = [], [], [], [], []
        for seed in SEEDS:
            torch.manual_seed(seed)
            policy = _policy(len(market.names), HIDDEN)
            optimiser = torch.optim.Adam(policy.parameters(), lr=LEARNING_RATE, weight_decay=WEIGHT_DECAY)
            best = (-np.inf, 0, None)
            curve_train, curve_held = [], []
            for epoch in range(EPOCHS):
                fitted = step(policy, market, 0, held_from, train=True, optimiser=optimiser)
                path = step(policy, market, 0, fit_end)                        # the held-out fifth, its own path
                held_reward = float(path[held_from:].mean())
                curve_train.append(round(float(fitted.mean()), 6))
                curve_held.append(round(held_reward, 6))
                if held_reward > best[0]:
                    best = (held_reward, epoch + 1, {key: value.clone() for key, value in policy.state_dict().items()})
            policy.load_state_dict(best[2])
            record: dict[int, dict[int, float]] = {}
            rewards = step(policy, market, 0, test[-1] + 1, record=record)
            recorded.append({t: record[t] for t in test})
            test_rewards.append(rewards[test])
            curves_train.append(curve_train)
            curves_held.append(curve_held)
            best_epochs.append(best[1])
            held_best.append(best[0])
        holdings, cash_share = [], []
        for t in test:
            merged: dict[int, float] = {}
            for record in recorded:
                for index, weight in record[t].items():
                    merged[index] = merged.get(index, 0.0) + weight / len(recorded)
            merged = {index: weight for index, weight in merged.items() if weight >= SMALLEST}
            targets[market.days[market.start + t].isoformat()] = {market.symbols[index]: round(weight, 5)
                                                                  for index, weight in merged.items()}
            holdings.append(len(merged))
            cash_share.append(1 - sum(merged.values()))
        robot = float(np.mean(test_rewards, axis=0).sum())
        yearly[str(year)] = {
            "sessions": len(test), "train_sessions": held_from, "held_sessions": fit_end - held_from,
            "held_from": market.days[market.start + held_from].isoformat(),
            "held_reward": round(float(np.mean(held_best)), 6), "best_epochs": best_epochs,
            "curve_train": [round(float(value), 6) for value in np.mean(curves_train, axis=0)],
            "curve_held": [round(float(value), 6) for value in np.mean(curves_held, axis=0)],
            "robot_excess": round(robot, 4), "holdings": round(float(np.mean(holdings)), 1),
            "cash": round(float(np.mean(cash_share)), 3)}
        if job:
            job.update(done=number + 1, total=len(years),
                       current=f"{year}：比 0050 {robot:+.3f}（訓練環境）、平均持有 {np.mean(holdings):.0f} 檔")
    meta = {"version": version, "factors": market.names, "yearly": yearly,
            "config": {"hidden": HIDDEN, "epochs": EPOCHS, "chunk": CHUNK, "learning_rate": LEARNING_RATE,
                       "weight_decay": WEIGHT_DECAY, "seeds": list(SEEDS), "held_out": HELD_OUT,
                       "cost_buy": market.cost_buy, "cost_sell": market.cost_sell, "smallest": SMALLEST},
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds")}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "targets.json").write_text(json.dumps(targets), encoding="utf-8")
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def weights(fp, folder: str | Path) -> np.ndarray:
    """sessions × symbols: the agent's weight for every stock (NaN where it gave none: before its first test
    session or without a trained agent)."""
    output = np.full((len(fp.sessions), len(fp.symbols)), np.nan, dtype=np.float32)
    path = Path(folder) / "targets.json"
    if not path.is_file():
        return output
    targets = json.loads(path.read_text(encoding="utf-8"))
    for day, goal in targets.items():
        position = fp.index.get(date.fromisoformat(day))
        if position is None:
            continue
        output[position] = 0.0
        for symbol, weight in goal.items():
            row = fp.row.get(symbol)
            if row is not None:
                output[position, row] = weight
    return output


def digest(folder: str | Path) -> str:
    import hashlib

    path = Path(folder) / "targets.json"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
