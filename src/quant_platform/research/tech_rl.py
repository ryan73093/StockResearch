"""2026-10-10 (使用者：訓練一個純技術分析流派、完全技術分析的 RL 機器人): a reinforcement-learning trader that
sees nothing but price and volume.

Every stock is its own small game with one shared player. After each close the agent sees the stock's
technical picture — 20 price and volume factors as percentiles among the day's eligible stocks (trend,
momentum, oscillators, bands, breakouts, volatility, volume, intraday and industry-residual momentum) — the
market's own technical state (TAIEX against its 200-day average, the share of stocks above theirs) and its
position (holding or not, sessions held, gain since buying, fall from the peak since buying), and chooses to
be in the stock or out of it. Being in earns the stock's next-session log return above the day's average
eligible stock (it learns which stocks, not whether the market rises); getting in pays fee and slippage,
getting out also the sell tax. No fundamentals, no chips, no news, no trained factor models.

Proximal policy optimisation with one network for all stocks — a thousand stocks over the years are many
games, unlike an account-level agent that sees a single history. Walk-forward: the policies that act in
year Y (three seeds, averaged) learn only from games that end before Y's first session.

In a rule the score ``rl_tech`` is the agent's chance of wanting to be in a stock it does not hold; with
``exit_model="tech1"`` the engine buys only stocks the agent wants (best first, top 20, industry cap) and the
agent alone decides when each holding is sold (research/daily.py ``daily_rankings``). Judged by the same
gate as every rule.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

TECH_VERSION = "tech-rl-1.0.0"
TECH_FEATURES = ("trend_200", "high_52w", "momentum_3", "momentum_6", "momentum_12_1", "reversal_1", "reversal_5d",
                 "rsi_14", "kd_k", "macd_hist", "bollinger_b", "ma_cross_20_60", "breakout_55", "low_volatility_60",
                 "low_max_return", "volume_surge", "liquidity", "imom_12", "fip_12", "resid_mom_12")
POSITION = ("held", "held_sessions", "gain_since_buy", "fall_from_peak")
MARKET = ("taiex_vs_200", "breadth")
COST_IN, COST_OUT = 0.0035, 0.0065           # fee 0.1425% + slippage 0.2%; selling adds the 0.3% tax
FIRST_YEAR = 2016
SEEDS = (0, 1, 2)
CONFIG = {"iterations": 120, "envs": 512, "episode": 60, "gamma": 0.98, "lam": 0.95, "lr": 3e-4, "clip": 0.2,
          "epochs": 4, "minibatch": 4096, "hidden": 64, "value": 0.5, "entropy": 0.01, "threads": 4, "step": 1}
# 1.0.0 decided every session on the next session's excess: the reward was mostly noise and the robot
# learned to stay out (training reward about 0, out-of-sample excess ±0.02% a session). 1.1.0 (2026-10-10)
# decides once a week on the next five sessions' excess (half-year games of 26 decisions) and trains on
# about twice as many decisions; the rule built on it decides weekly too.
VERSIONS = {"tech-rl-1.0.0": {},
            "tech-rl-1.1.0": {"step": 5, "episode": 26, "iterations": 300, "envs": 1024, "gamma": 0.9}}


def tech_dir(history: str | Path, version: str = TECH_VERSION) -> Path:
    return Path(history).parent / "models" / version


class Board:
    """Everything the games need, computed once for a factor panel: percentiles (sessions × symbols ×
    features, 0.5 where unknown), the market state, eligibility, adjusted prices and next-session excess."""

    def __init__(self, fp) -> None:
        from quant_platform.research.model import _rank, eligibility

        self.fp = fp
        self.sessions = len(fp.sessions)
        eligible = eligibility(fp)                                        # symbols × sessions
        self.eligible = eligible.T.copy()                                 # sessions × symbols
        self.features = np.full((self.sessions, len(fp.symbols), len(TECH_FEATURES)), 0.5, dtype=np.float32)
        for index, name in enumerate(TECH_FEATURES):
            values = np.where(eligible, fp.matrix(name), np.nan).T
            self.features[:, :, index] = np.nan_to_num(_rank(values), nan=0.5)
        prices = fp.panel.filled.T                                        # sessions × symbols
        self.prices = prices.astype(np.float64)
        with np.errstate(divide="ignore", invalid="ignore"):
            moves = prices[1:] / prices[:-1] - 1
        moves = np.where(self.eligible[:-1], moves, np.nan)
        import warnings

        warnings.filterwarnings("ignore", "Mean of empty slice", RuntimeWarning)       # a session with no stock
        with np.errstate(invalid="ignore"):
            average = np.nanmean(np.where(np.isfinite(moves), moves, np.nan), axis=1, keepdims=True)
            excess = np.log1p(np.clip(moves, -0.5, 1.0)) - np.log1p(np.nan_to_num(average))
        self.excess = np.zeros((self.sessions, len(fp.symbols)), dtype=np.float32)
        self.excess[:-1] = np.nan_to_num(excess, nan=0.0)
        trend = fp.matrix("trend_200").T
        with np.errstate(invalid="ignore"):
            breadth = np.nanmean(np.where(self.eligible & np.isfinite(trend), trend > 0, np.nan), axis=1)
        if fp.market is not None:
            taiex = pd.Series(fp.market)
            gap = (taiex / taiex.rolling(200, min_periods=50).mean() - 1).to_numpy()
        else:
            gap = np.zeros(self.sessions)
        self.market = np.column_stack([np.clip(np.nan_to_num(gap), -0.5, 0.5) * 4,
                                       np.nan_to_num(breadth, nan=0.5) * 2 - 1]).astype(np.float32)

    def observe(self, days: np.ndarray, symbols: np.ndarray, held: np.ndarray, held_sessions: np.ndarray,
                gain: np.ndarray, fall: np.ndarray) -> np.ndarray:
        position = np.column_stack([held.astype(np.float32) * 2 - 1, np.clip(held_sessions / 60, 0, 2),
                                    np.clip(np.nan_to_num(gain), -0.5, 0.5) * 2,
                                    np.clip(np.nan_to_num(fall), -0.5, 0) * 2]).astype(np.float32)
        return np.concatenate([self.features[days, symbols] * 2 - 1, self.market[days], position], axis=1)


def shared_board(fp) -> Board:
    """One board per factor panel (the score and the exit agent read the same one)."""
    board = getattr(fp, "_tech_board", None)
    if board is None:
        board = Board(fp)
        fp._tech_board = board
    return board


def inputs() -> int:
    return len(TECH_FEATURES) + len(MARKET) + len(POSITION)


def _networks(hidden: int):
    import torch

    policy = torch.nn.Sequential(torch.nn.Linear(inputs(), hidden), torch.nn.Tanh(), torch.nn.Linear(hidden, hidden),
                                 torch.nn.Tanh(), torch.nn.Linear(hidden, 2))
    critic = torch.nn.Sequential(torch.nn.Linear(inputs(), hidden), torch.nn.Tanh(), torch.nn.Linear(hidden, hidden),
                                 torch.nn.Tanh(), torch.nn.Linear(hidden, 1))
    return policy, critic


def train_policy(board: Board, low: int, high: int, seed: int, config: dict | None = None):
    """PPO on games (a stock from an eligible session, ``episode`` sessions) inside [low, high)."""
    import torch

    config = {**CONFIG, **(config or {})}
    torch.set_num_threads(config["threads"])
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    policy, critic = _networks(config["hidden"])
    parameters = list(policy.parameters()) + list(critic.parameters())
    optimiser = torch.optim.Adam(parameters, lr=config["lr"])
    length, envs, stride = config["episode"], config["envs"], config.get("step", 1)
    starts_day, starts_symbol = np.nonzero(board.eligible[low: max(low + 1, high - length * stride - 1)])
    if len(starts_day) == 0:
        raise ValueError("訓練期間沒有合格股票")
    starts_day = starts_day + low
    history = []
    for _iteration in range(config["iterations"]):
        pick = rng.integers(0, len(starts_day), size=envs)
        day, symbol = starts_day[pick].copy(), starts_symbol[pick].copy()
        held = np.zeros(envs, dtype=bool)
        entry, peak, held_n = np.ones(envs), np.ones(envs), np.zeros(envs)
        observations, actions, logps, rewards, values = [], [], [], [], []
        for _offset in range(length):
            price = board.prices[day, symbol]
            gain = np.where(held, price / entry - 1, 0.0)
            fall = np.where(held, price / peak - 1, 0.0)
            state = board.observe(day, symbol, held, held_n, gain, fall)
            with torch.no_grad():
                distribution = torch.distributions.Categorical(logits=policy(torch.from_numpy(state)))
                action = distribution.sample()
                estimate = critic(torch.from_numpy(state)).squeeze(-1)
            want = (action.numpy() == 1) & board.eligible[day, symbol]
            cost = np.where(want & ~held, COST_IN, 0.0) + np.where(~want & held, COST_OUT, 0.0)
            earned = sum(board.excess[np.minimum(day + offset, high - 2), symbol] for offset in range(stride))
            reward = np.where(want, earned, 0.0) - cost
            entry = np.where(want & ~held, price, entry)
            peak = np.where(want & ~held, price, peak)
            held_n = np.where(want, np.where(held, held_n + stride, stride), 0)
            held = want
            observations.append(state)
            actions.append(action.numpy())
            logps.append(distribution.log_prob(action).numpy())
            rewards.append(reward)
            values.append(estimate.numpy())
            day = np.minimum(day + stride, high - 1)
            peak = np.where(held, np.maximum(peak, board.prices[day, symbol]), peak)
        rewards_ = np.array(rewards) * 100                       # daily log excess is small: scale for learning
        history.append(float(np.array(rewards).sum(axis=0).mean()))
        values_ = np.array(values)
        advantages = np.zeros_like(rewards_)
        running = np.zeros(envs)
        for t in reversed(range(length)):
            following = values_[t + 1] if t + 1 < length else np.zeros(envs)
            delta = rewards_[t] + config["gamma"] * following - values_[t]
            running = delta + config["gamma"] * config["lam"] * running
            advantages[t] = running
        returns = advantages + values_
        flat_obs = torch.from_numpy(np.concatenate(observations))
        flat_actions = torch.from_numpy(np.concatenate(actions))
        flat_logps = torch.from_numpy(np.concatenate(logps))
        flat_adv = torch.from_numpy(advantages.reshape(-1).astype(np.float32))
        flat_adv = (flat_adv - flat_adv.mean()) / (flat_adv.std() + 1e-8)
        flat_returns = torch.from_numpy(returns.reshape(-1).astype(np.float32))
        count = len(flat_actions)
        for _epoch in range(config["epochs"]):
            order = torch.from_numpy(rng.permutation(count))
            for begin in range(0, count, config["minibatch"]):
                part = order[begin: begin + config["minibatch"]]
                distribution = torch.distributions.Categorical(logits=policy(flat_obs[part]))
                ratio = torch.exp(distribution.log_prob(flat_actions[part]) - flat_logps[part])
                surrogate = torch.min(ratio * flat_adv[part],
                                      torch.clamp(ratio, 1 - config["clip"], 1 + config["clip"]) * flat_adv[part])
                value_loss = (critic(flat_obs[part]).squeeze(-1) - flat_returns[part]).pow(2).mean()
                loss = -surrogate.mean() + config["value"] * value_loss - config["entropy"] * distribution.entropy().mean()
                optimiser.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(parameters, 0.5)
                optimiser.step()
    return policy, history


def wants(policies: list, states: np.ndarray) -> np.ndarray:
    """The seeds' average chance of choosing to be in."""
    import torch

    with torch.no_grad():
        tensor = torch.from_numpy(states)
        chances = [torch.softmax(policy(tensor), dim=-1)[:, 1].numpy() for policy in policies]
    return np.mean(chances, axis=0)


def load_policies(folder: Path) -> dict[int, list]:
    import torch

    output: dict[int, list] = {}
    meta_path = folder / "meta.json"
    hidden = CONFIG["hidden"]
    if meta_path.is_file():
        hidden = json.loads(meta_path.read_text(encoding="utf-8")).get("config", {}).get("hidden", hidden)
    for path in sorted(folder.glob("*-seed*.pt")) if folder.is_dir() else []:
        year = int(path.stem.split("-")[0])
        policy, _critic = _networks(hidden)
        policy.load_state_dict(torch.load(path, weights_only=True))
        policy.eval()
        output.setdefault(year, []).append(policy)
    return output


def _year_columns(sessions, years: list[int]) -> dict[int, list[int]]:
    by_year: dict[int, list[int]] = {}
    for column, day in enumerate(sessions):
        usable = [year for year in years if year <= day.year]
        if usable:
            by_year.setdefault(max(usable), []).append(column)
    return by_year


def scores(fp, folder: str | Path, board: Board | None = None) -> np.ndarray:
    """sessions × symbols: the chance the agent of the session's year wants to be in a stock it does not
    hold (the latest agent after the last trained year); NaN where not eligible or no agent exists yet."""
    folder = Path(folder)
    output = np.full((len(fp.sessions), len(fp.symbols)), np.nan, dtype=np.float32)
    policies = load_policies(folder)
    if not policies:
        return output
    board = board or shared_board(fp)
    for year, columns in _year_columns(fp.sessions, sorted(policies)).items():
        days, symbols = np.nonzero(board.eligible[columns])
        if not len(days):
            continue
        days = np.array(columns)[days]
        zeros = np.zeros(len(days))
        chance = wants(policies[year], board.observe(days, symbols, zeros.astype(bool), zeros, zeros, zeros))
        output[days, symbols] = chance
    return output


class TechAgent:
    """The exit side in the engine: sell a holding when the agent, seeing its position, wants out; refuse
    new buys of stocks it does not want."""

    def __init__(self, fp, folder: str | Path, factor: str = "rl_tech") -> None:
        self.fp = fp
        self.policies = load_policies(Path(folder))
        self.board = shared_board(fp) if self.policies else None
        self.years = sorted(self.policies)
        self._score = fp.matrix(factor).T if self.policies else None        # sessions × symbols

    def _year(self, position: int) -> int | None:
        year = self.fp.sessions[position].year
        usable = [item for item in self.years if item <= year]
        return max(usable) if usable else None

    def refuses(self, position: int) -> set[str]:
        if self._score is None:
            return set()
        column = self._score[position]
        return {self.fp.symbols[row] for row in np.flatnonzero(np.isfinite(column) & (column < 0.5))}

    def sells(self, position: int, current: list[str], held_for: dict[str, int], buy_price: dict[str, float],
              peak: dict[str, float], ranked: list[str]) -> list[str]:
        year = self._year(position)
        if year is None or not current:
            return []
        rows = np.array([self.fp.row[symbol] for symbol in current])
        price = self.board.prices[position, rows]
        bought = np.array([buy_price.get(symbol, np.nan) for symbol in current])
        high = np.array([peak.get(symbol, np.nan) for symbol in current])
        with np.errstate(divide="ignore", invalid="ignore"):
            gain = np.nan_to_num(price / bought - 1)
            fall = np.nan_to_num(price / high - 1)
        sessions = np.array([held_for.get(symbol, 0) for symbol in current], dtype=float)
        states = self.board.observe(np.full(len(rows), position), rows, np.ones(len(rows), dtype=bool), sessions,
                                    gain, fall)
        chance = wants(self.policies[year], states)
        return [symbol for symbol, value in zip(current, chance) if value < 0.5]


def simulate_agent(board: Board, policies: list, low: int, high: int, stride: int = 1) -> dict[str, float]:
    """Out of sample, every eligible stock at once, the agent deciding in or out every ``stride`` sessions
    greedily: the average excess of a session it is in, the share of stock-sessions in, holding length and
    entries."""
    count = board.excess.shape[1]
    held = np.zeros(count, dtype=bool)
    entry, peak, held_n = np.ones(count), np.ones(count), np.zeros(count)
    earned, in_sessions, entries, exits_, lengths = 0.0, 0, 0, 0, []
    for day in range(low, high, stride):
        live = board.eligible[day]
        rows = np.flatnonzero(live | held)
        if not len(rows):
            continue
        price = board.prices[day, rows]
        gain = np.where(held[rows], price / entry[rows] - 1, 0.0)
        fall = np.where(held[rows], price / peak[rows] - 1, 0.0)
        chance = wants(policies, board.observe(np.full(len(rows), day), rows, held[rows], held_n[rows], gain, fall))
        want = (chance >= 0.5) & live[rows]
        new = want & ~held[rows]
        gone = ~want & held[rows]
        entries += int(new.sum())
        exits_ += int(gone.sum())
        lengths.extend(held_n[rows][gone].tolist())
        entry[rows[new]], peak[rows[new]] = price[new], price[new]
        held_n[rows] = np.where(want, held_n[rows] + stride, 0)
        held[rows] = want
        span = range(day, min(day + stride, high))
        earned += (float(sum(board.excess[index, rows][want].sum() for index in span))
                   - COST_IN * int(new.sum()) - COST_OUT * int(gone.sum()))
        in_sessions += int(want.sum()) * len(span)
        later = min(day + stride, board.sessions - 1)
        peak[rows] = np.where(want, np.maximum(peak[rows], board.prices[later, rows]), peak[rows])
    stock_sessions = int(board.eligible[low:high].sum())
    return {"excess_per_in_session": round(earned / in_sessions, 6) if in_sessions else None,
            "share_in": round(in_sessions / stock_sessions, 4) if stock_sessions else None,
            "entries": entries, "average_hold": round(float(np.mean(lengths)), 1) if lengths else None}


def train(fp, out_dir: str | Path, data_fingerprint: str = "", years: list[int] | None = None, job=None,
          config: dict | None = None, seeds: tuple[int, ...] = SEEDS, version: str = TECH_VERSION) -> dict[str, object]:
    """Train and save the seeds' policies for every year; returns the meta with out-of-sample diagnostics."""
    import torch

    config = {**CONFIG, **VERSIONS.get(version, {}), **(config or {})}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    board = Board(fp)
    first_session = next(index for index in range(board.sessions) if board.eligible[index].any())
    years = years or list(range(FIRST_YEAR, fp.sessions[-1].year + 1))
    diagnostics = {}
    for number, year in enumerate(years):
        start = next((index for index, day in enumerate(fp.sessions) if day.year >= year), board.sessions)
        end = next((index for index, day in enumerate(fp.sessions) if day.year > year), board.sessions)
        if start - first_session < config["episode"] * config["step"] * 3:
            continue
        policies, curves = [], []
        for seed in seeds:
            if job:
                job.update(done=number, current=f"{year} 年的機器人（種子 {seed}）", force=True)
            policy, curve = train_policy(board, first_session, start, seed + year * 100, config)
            torch.save(policy.state_dict(), out / f"{year}-seed{seed}.pt")
            policies.append(policy)
            curves.append(curve)
        check = (simulate_agent(board, policies, start, max(start + 1, end - 1), config["step"])
                 if start < board.sessions - 1 else {})
        diagnostics[str(year)] = {"trained_until": fp.sessions[start - 1].isoformat(),
                                  "training_reward_first": round(float(np.mean([curve[0] for curve in curves])), 5),
                                  "training_reward_last": round(float(np.mean([np.mean(curve[-10:]) for curve in curves])), 5),
                                  **check}
    meta = {"version": version, "features": list(TECH_FEATURES), "market": list(MARKET), "position": list(POSITION),
            "costs": {"in": COST_IN, "out": COST_OUT}, "config": config, "seeds": list(seeds), "data": data_fingerprint,
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds"), "years": diagnostics}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def digest(folder: str | Path) -> str:
    import hashlib

    output = hashlib.sha256()
    for path in sorted(Path(folder).glob("*.pt")) if Path(folder).is_dir() else []:
        output.update(path.name.encode())
        output.update(hashlib.sha256(path.read_bytes()).digest())
    return output.hexdigest()
