"""R15 stage C1 (2026-10-06): a reinforcement-learning exposure overlay on the best trend rule.

The plan (research_method §9, R15 C) is not to learn stock picking from scratch — 2,700 sessions of one
market history are too few, and an agent would memorise which stock rose when — but to sit on top of a
rule that already works and learn one thing: how much of the account to keep in its stocks. Every
session, after the close, the agent picks the stock share of the account (0%, 50% or 100%); the rest is
in 0050. The stocks are the rule's own picks, unchanged ("站上 200 日均線、前 20 名、同產業最多 3 成、
依波動度配置", the best T1 rule).

The environment is the rule's own account replayed by the research engine (dividends, fees and the 0.3%
tax included) and 0050's, as daily returns: the agent's choice at session t earns
w·r_stocks(t+1) + (1−w)·r_0050(t+1), minus 0.5% of the account for every 100% moved. The reward is that
day's log return above 0050's minus half of any deepening of the account's drawdown (the gate asks for a
drawdown no more than 5 points deeper than 0050's). State: the rule's and 0050's returns over 5/20/60
sessions, 20-session volatility, 250-session drawdown, the gap between the two, the share of stocks above
their own 200-day average, TAIEX against its 200-day average, the current stock share and the account's
drawdown — all known at that close, normalised with the training years' statistics only.

Learning: PPO (policy and value networks of two 64-unit layers), 64 episodes at a time, each 250 sessions
from a random start in the training years with noise on the state, settings fixed in advance. Walk-forward:
the policy for year Y is trained only on sessions before Y and then run on Y; five seeds, their stock
shares averaged. Judged against the rule fully in stocks, half in 0050 and 0050 alone on the same
out-of-sample years (2017 on).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

RL_VERSION = "rl-overlay-1.0.0"
LEVELS = (0.0, 0.5, 1.0)
SWITCH_COST = 0.005
DRAWDOWN_PENALTY = 0.5
FEATURE_NOISE = 0.1
FIRST_TEST_YEAR = 2017
SEEDS = (0, 1, 2, 3, 4)
CONFIG = {"iterations": 150, "envs": 64, "episode": 250, "epochs": 4, "minibatch": 2048, "lr": 3e-4,
          "gamma": 0.99, "lam": 0.95, "clip": 0.2, "hidden": 64, "entropy": 0.01, "value": 0.5}
FEATURES = ("rule_return_5", "rule_return_20", "rule_return_60", "rule_volatility_20", "rule_drawdown_250",
            "0050_return_20", "0050_return_60", "0050_volatility_20", "0050_drawdown_250", "gap_20", "gap_60",
            "breadth_above_200", "taiex_vs_200")


def base_rule():
    from quant_platform.research.daily import DailyRule

    return DailyRule(name="每天 站上 200 日均線：前 20 名、同產業最多 3 成、依波動度配置", factors={"trend_200": 1.0},
                     industry_cap=0.3, weighting="inverse_vol")


@dataclass
class Overlay:
    days: list[date]
    stock: np.ndarray        # the rule's stock account, return of each session (0 on the first)
    bench: np.ndarray        # 0050, the same
    features: np.ndarray     # sessions × FEATURES, known at each session's close


def _features(stock: np.ndarray, bench: np.ndarray, breadth: np.ndarray, taiex: np.ndarray) -> np.ndarray:
    columns = {}
    for name, returns in (("rule", stock), ("0050", bench)):
        log = pd.Series(np.log1p(returns))
        value = np.exp(log.cumsum())
        columns[f"{name}_return_5"] = log.rolling(5).sum()
        columns[f"{name}_return_20"] = log.rolling(20).sum()
        columns[f"{name}_return_60"] = log.rolling(60).sum()
        columns[f"{name}_volatility_20"] = log.rolling(20).std()
        columns[f"{name}_drawdown_250"] = value / value.rolling(250, min_periods=1).max() - 1
    columns["gap_20"] = columns["rule_return_20"] - columns["0050_return_20"]
    columns["gap_60"] = columns["rule_return_60"] - columns["0050_return_60"]
    columns["breadth_above_200"] = pd.Series(breadth)
    columns["taiex_vs_200"] = pd.Series(taiex)
    return np.column_stack([np.asarray(columns[name], dtype=float) for name in FEATURES])


def build_overlay(data, fp, costs, rule=None) -> Overlay:
    """Replay the rule's stock account and 0050 with the research engine from the shadow's warm-up on."""
    from quant_platform.research.daily import SHADOW_WARMUP, daily_rankings, daily_weights, simulate_daily
    from quant_platform.research.metrics import unit_values
    from quant_platform.research.model import eligibility

    rule = (rule or base_rule()).model_copy(update={"core": 0.0, "account_filter": "none"})
    sessions = fp.sessions
    start, end = sessions[min(SHADOW_WARMUP, len(sessions) - 1)], sessions[-1]
    ranks = daily_rankings(fp, rule, start, end)
    run = simulate_daily(data, rule, costs, start, end, ranks, weights=daily_weights(fp, rule, ranks))
    benchmark = simulate_daily(data, None, costs, start, end)
    units = np.asarray(unit_values(run.values, run.flows))
    bench_units = np.asarray(unit_values(benchmark.values, benchmark.flows))
    stock = np.r_[0.0, units[1:] / units[:-1] - 1]
    bench = np.r_[0.0, bench_units[1:] / bench_units[:-1] - 1]
    positions = [fp.index[day] for day in run.days]
    trend, eligible = fp.matrix("trend_200"), eligibility(fp)
    breadth = []
    for position in positions:
        mask = eligible[:, position] & np.isfinite(trend[:, position])
        breadth.append(float((trend[mask, position] > 0).mean()) if mask.any() else np.nan)
    if fp.market is not None:
        average = pd.Series(fp.market).rolling(200).mean().to_numpy()
        taiex = np.array([fp.market[position] / average[position] - 1 for position in positions])
    else:
        taiex = np.full(len(positions), np.nan)
    return Overlay(list(run.days), stock, bench, _features(stock, bench, np.array(breadth), taiex))


# --- the environment -----------------------------------------------------------------------------
def step(stock_next: np.ndarray, bench_next: np.ndarray, share: np.ndarray, previous: np.ndarray,
         value: np.ndarray, peak: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """One session for every environment: returns (reward, value, peak, drawdown)."""
    growth = share * stock_next + (1 - share) * bench_next - SWITCH_COST * np.abs(share - previous)
    drawdown_before = value / peak - 1
    value = value * (1 + growth)
    peak = np.maximum(peak, value)
    drawdown = value / peak - 1
    reward = np.log1p(growth) - np.log1p(bench_next) - DRAWDOWN_PENALTY * np.maximum(0.0, drawdown_before - drawdown)
    return reward, value, peak, drawdown


def _normaliser(features: np.ndarray, low: int, high: int) -> tuple[np.ndarray, np.ndarray]:
    window = features[low:high]
    mean = np.nanmean(window, axis=0)
    std = np.nanstd(window, axis=0)
    return np.nan_to_num(mean), np.where(np.isfinite(std) & (std > 0), std, 1.0)


def _state(features: np.ndarray, mean: np.ndarray, std: np.ndarray, share: np.ndarray, drawdown: np.ndarray,
           noise: np.ndarray | None = None) -> np.ndarray:
    normal = np.clip(np.nan_to_num((features - mean) / std), -5, 5)
    if noise is not None:
        normal = normal + noise
    return np.column_stack([normal, share * 2 - 1, drawdown * 5]).astype(np.float32)


class _Networks:
    def __init__(self, inputs: int, hidden: int, seed: int) -> None:
        import torch

        torch.manual_seed(seed)
        self.policy = torch.nn.Sequential(torch.nn.Linear(inputs, hidden), torch.nn.Tanh(),
                                          torch.nn.Linear(hidden, hidden), torch.nn.Tanh(),
                                          torch.nn.Linear(hidden, len(LEVELS)))
        self.value = torch.nn.Sequential(torch.nn.Linear(inputs, hidden), torch.nn.Tanh(),
                                         torch.nn.Linear(hidden, hidden), torch.nn.Tanh(), torch.nn.Linear(hidden, 1))


def train_policy(overlay: Overlay, low: int, high: int, seed: int, config: dict | None = None):
    """PPO on random 250-session episodes inside [low, high); returns (policy network, normaliser)."""
    import torch

    config = {**CONFIG, **(config or {})}
    rng = np.random.default_rng(seed)
    mean, std = _normaliser(overlay.features, low, high)
    inputs = overlay.features.shape[1] + 2
    nets = _Networks(inputs, config["hidden"], seed)
    optimiser = torch.optim.Adam(list(nets.policy.parameters()) + list(nets.value.parameters()), lr=config["lr"])
    envs, length = config["envs"], min(config["episode"], high - low - 2)
    if length < 20:
        raise ValueError("訓練期間太短")
    levels = np.array(LEVELS)
    for _iteration in range(config["iterations"]):
        starts = rng.integers(low, high - length - 1, size=envs)
        share = np.ones(envs)
        value, peak, drawdown = np.ones(envs), np.ones(envs), np.zeros(envs)
        observations, actions, logps, rewards, values = [], [], [], [], []
        for offset in range(length):
            day = starts + offset
            noise = rng.normal(0, FEATURE_NOISE, size=(envs, overlay.features.shape[1]))
            state = _state(overlay.features[day], mean, std, share, drawdown, noise)
            with torch.no_grad():
                logits = nets.policy(torch.from_numpy(state))
                distribution = torch.distributions.Categorical(logits=logits)
                action = distribution.sample()
                estimate = nets.value(torch.from_numpy(state)).squeeze(-1)
            new_share = levels[action.numpy()]
            reward, value, peak, drawdown = step(overlay.stock[day + 1], overlay.bench[day + 1], new_share, share,
                                                 value, peak)
            observations.append(state)
            actions.append(action.numpy())
            logps.append(distribution.log_prob(action).numpy())
            rewards.append(reward)
            values.append(estimate.numpy())
            share = new_share
        rewards_ = np.array(rewards) * 100                      # daily log returns are small: scale for learning
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
                logits = nets.policy(flat_obs[part])
                distribution = torch.distributions.Categorical(logits=logits)
                ratio = torch.exp(distribution.log_prob(flat_actions[part]) - flat_logps[part])
                surrogate = torch.min(ratio * flat_adv[part],
                                      torch.clamp(ratio, 1 - config["clip"], 1 + config["clip"]) * flat_adv[part])
                value_loss = (nets.value(flat_obs[part]).squeeze(-1) - flat_returns[part]).pow(2).mean()
                loss = -surrogate.mean() + config["value"] * value_loss - config["entropy"] * distribution.entropy().mean()
                optimiser.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(list(nets.policy.parameters()) + list(nets.value.parameters()), 0.5)
                optimiser.step()
    return nets.policy, (mean, std)


def run_policy(policies: list, overlay: Overlay, low: int, high: int, start_share: float = 1.0) -> np.ndarray:
    """The stock share chosen at each session in [low, high): the seeds' greedy choices averaged."""
    import torch

    shares = np.empty(high - low)
    share, drawdown = start_share, 0.0
    value = peak = 1.0
    for offset, day in enumerate(range(low, high)):
        chosen = []
        for policy, (mean, std) in policies:
            state = _state(overlay.features[day: day + 1], mean, std, np.array([share]), np.array([drawdown]))
            with torch.no_grad():
                chosen.append(LEVELS[int(policy(torch.from_numpy(state)).argmax())])
        new_share = float(np.mean(chosen))
        if day + 1 < len(overlay.stock):
            growth = new_share * overlay.stock[day + 1] + (1 - new_share) * overlay.bench[day + 1] \
                - SWITCH_COST * abs(new_share - share)
            value *= 1 + growth
            peak = max(peak, value)
            drawdown = value / peak - 1
        shares[offset] = new_share
        share = new_share
    return shares


def path(overlay: Overlay, shares: np.ndarray, low: int) -> np.ndarray:
    """Unit values of an account holding ``shares[i]`` in the rule's stocks from session low+i's close."""
    values = [1.0]
    previous = shares[0]
    for offset in range(len(shares) - 1):
        day = low + offset
        share = shares[offset]
        growth = share * overlay.stock[day + 1] + (1 - share) * overlay.bench[day + 1] - SWITCH_COST * abs(share - previous)
        values.append(values[-1] * (1 + growth))
        previous = share
    return np.array(values)


def _summary(values: np.ndarray, years: float) -> dict[str, float]:
    peak = np.maximum.accumulate(values)
    return {"growth": round(float(values[-1] - 1), 4), "annual": round(float(values[-1] ** (1 / years) - 1), 4) if years else 0.0,
            "max_drawdown": round(float((values / peak - 1).min()), 4)}


def walk_forward(overlay: Overlay, out_dir: str | Path | None = None, seeds: tuple[int, ...] = SEEDS,
                 first_year: int = FIRST_TEST_YEAR, config: dict | None = None, job=None) -> dict[str, object]:
    """Train for each test year on the sessions before it, run it; return the report (and save it)."""
    ready = int(np.argmax(np.isfinite(overlay.features).all(axis=1)))       # every feature known from here
    years = sorted({day.year for day in overlay.days if day.year >= first_year})
    shares_all = []
    report_years = {}
    for number, year in enumerate(years):
        low = next(index for index, day in enumerate(overlay.days) if day.year == year)
        high = max(index for index, day in enumerate(overlay.days) if day.year == year) + 1
        if job:
            job.update(done=number, current=f"{year} 年（訓練 {overlay.days[ready]}～{overlay.days[low - 1]}）", force=True)
        policies = [train_policy(overlay, ready, low, seed + year, config) for seed in seeds]
        start_share = shares_all[-1] if shares_all else 1.0
        shares = run_policy(policies, overlay, low, high, start_share)
        shares_all.extend(shares.tolist())
        sessions = high - low
        report_years[str(year)] = {
            "sessions": sessions, "average_share": round(float(shares.mean()), 3),
            "switches": int((np.abs(np.diff(np.r_[start_share, shares])) > 1e-9).sum()),
            "rl": _summary(path(overlay, shares, low), sessions / 245),
            "rule": _summary(path(overlay, np.ones(sessions), low), sessions / 245),
            "half": _summary(path(overlay, np.full(sessions, 0.5), low), sessions / 245),
            "0050": _summary(path(overlay, np.zeros(sessions), low), sessions / 245),
        }
    first = next(index for index, day in enumerate(overlay.days) if day.year == years[0])
    shares_all = np.array(shares_all)
    total = len(shares_all) / 245
    report = {
        "version": RL_VERSION, "config": {**CONFIG, **(config or {})}, "levels": list(LEVELS), "switch_cost": SWITCH_COST,
        "drawdown_penalty": DRAWDOWN_PENALTY, "features": list(FEATURES), "seeds": list(seeds),
        "base_rule": base_rule().name, "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "test_from": overlay.days[first].isoformat(), "test_to": overlay.days[-1].isoformat(), "years": report_years,
        "overall": {
            "rl": _summary(path(overlay, shares_all, first), total),
            "rule": _summary(path(overlay, np.ones(len(shares_all)), first), total),
            "half": _summary(path(overlay, np.full(len(shares_all), 0.5), first), total),
            "0050": _summary(path(overlay, np.zeros(len(shares_all)), first), total),
            "average_share": round(float(shares_all.mean()), 3),
        },
        "shares": {overlay.days[first + index].isoformat(): round(float(value), 3) for index, value in enumerate(shares_all)},
    }
    if out_dir is not None:
        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
