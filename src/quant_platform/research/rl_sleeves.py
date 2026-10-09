"""R15 stage C3 (2026-10-09, 使用者：rl 怎麼可能只有這樣而已): an RL allocator across strategy families.

C1 let an agent choose only how much of one stock rule to hold against 0050, and it learned about half —
what a fixed half does without learning. Here the agent chooses between different families: every session,
after the close, it splits the account across the weekly model rule's stocks, the volatility-weighted trend
rule's stocks and 0050, picking one of eight fixed mixes. The two stock rules win and lose at partly
different times (their monthly gaps to 0050 correlate 0.75; half of their worst months differ), so there is
something to learn that a fixed share cannot do: lean to the family that is working.

The environment replays each family's own account with the research engine (fees, tax and dividends inside
its returns) as daily unit-value returns; moving money between families costs 0.5% of the amount moved,
and the reward also pays 2% of it (as rl-overlay-1.1.0) to discourage churning. Reward: that session's log
return above 0050's, minus half of any deepening of the account's drawdown. State: each family's 5/20/60-
session returns, 20-session volatility and 250-session drawdown, the gaps between them, the share of stocks
above their 200-day average, TAIEX against its 200-day average, the current mix and the account's drawdown.

Learning is C1's: PPO, random 250-session episodes with noise on the state, five seeds, walk-forward (the
policy for year Y learns only from sessions before Y). It is judged on 2017 on against every family alone,
the fixed mixes, and — the fair test for anything learned — the mix that was best on the training years,
chosen again each year. Settings fixed in advance.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

RL_SLEEVES_VERSION = "rl-sleeves-1.0.0"
# 1.1.0 (2026-10-09): 1.0.0 started every training episode at a quarter, a quarter and half in 0050 and,
# with moves charged, learned to stay there (its average mix 28/26/47 ≈ that fixed mix, which did slightly
# better). Each training episode now starts at a random mix of the menu, so staying put earns nothing.
VERSIONS = {"rl-sleeves-1.0.0": {"random_start": False}, "rl-sleeves-1.1.0": {"random_start": True}}
LATEST_SLEEVES = "rl-sleeves-1.1.0"
FAMILIES = ("model", "trend", "0050")
FAMILY_LABELS = {"model": "機器學習每週", "trend": "站上 200 日均線（依波動度）", "0050": "0050"}
MENU = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.5, 0.5, 0.0), (0.5, 0.0, 0.5), (0.0, 0.5, 0.5),
        (1 / 3, 1 / 3, 1 / 3), (0.25, 0.25, 0.5))
MOVE_COST = 0.005            # of the amount moved between families
SWITCH_PENALTY = 0.02        # in the reward only
DRAWDOWN_PENALTY = 0.5
FEATURE_NOISE = 0.1
FIRST_TEST_YEAR = 2017
SEEDS = (0, 1, 2, 3, 4)
CONFIG = {"iterations": 150, "envs": 64, "episode": 250, "epochs": 4, "minibatch": 2048, "lr": 3e-4,
          "gamma": 0.99, "lam": 0.95, "clip": 0.2, "hidden": 64, "entropy": 0.01, "value": 0.5}


def family_rules():
    from quant_platform.research.daily import DailyRule

    model = DailyRule(name="機器學習（含財報）：前 20 名、同產業最多 3 成、每週決策", factors={"ml_gbm_statements": 1.0},
                      industry_cap=0.3, check="weekly")
    trend = DailyRule(name="每天 站上 200 日均線：前 20 名、同產業最多 3 成、依波動度配置", factors={"trend_200": 1.0},
                      industry_cap=0.3, weighting="inverse_vol")
    return {"model": model, "trend": trend}


@dataclass
class Families:
    days: list[date]
    returns: np.ndarray      # sessions × families: each family's return of each session (0 on the first)
    features: np.ndarray     # sessions × features, known at each session's close
    names: list[str]


def _features(returns: np.ndarray, breadth: np.ndarray, taiex: np.ndarray) -> tuple[np.ndarray, list[str]]:
    columns: dict[str, pd.Series] = {}
    logs = {}
    for index, name in enumerate(FAMILIES):
        log = pd.Series(np.log1p(returns[:, index]))
        logs[name] = log
        value = np.exp(log.cumsum())
        columns[f"{name}_return_5"] = log.rolling(5).sum()
        columns[f"{name}_return_20"] = log.rolling(20).sum()
        columns[f"{name}_return_60"] = log.rolling(60).sum()
        columns[f"{name}_volatility_20"] = log.rolling(20).std()
        columns[f"{name}_drawdown_250"] = value / value.rolling(250, min_periods=1).max() - 1
    for first, second in (("model", "0050"), ("trend", "0050"), ("model", "trend")):
        for days in (20, 60):
            columns[f"gap_{first}_{second}_{days}"] = columns[f"{first}_return_{days}"] - columns[f"{second}_return_{days}"]
    columns["breadth_above_200"] = pd.Series(breadth)
    columns["taiex_vs_200"] = pd.Series(taiex)
    names = list(columns)
    return np.column_stack([np.asarray(columns[name], dtype=float) for name in names]), names


def build_families(data, fp, costs) -> Families:
    """Replay each family's stock account and 0050 from the shadow's warm-up on, as unit-value returns."""
    from quant_platform.research.daily import SHADOW_WARMUP, daily_rankings, daily_weights, simulate_daily
    from quant_platform.research.metrics import unit_values
    from quant_platform.research.model import eligibility

    sessions = fp.sessions
    start, end = sessions[min(SHADOW_WARMUP, len(sessions) - 1)], sessions[-1]
    series = []
    days = None
    for name in FAMILIES:
        if name == "0050":
            run = simulate_daily(data, None, costs, start, end)
        else:
            rule = family_rules()[name]
            ranks = daily_rankings(fp, rule, start, end)
            run = simulate_daily(data, rule, costs, start, end, ranks, weights=daily_weights(fp, rule, ranks))
        units = np.asarray(unit_values(run.values, run.flows))
        series.append(np.r_[0.0, units[1:] / units[:-1] - 1])
        days = list(run.days)
    returns = np.nan_to_num(np.column_stack(series))
    positions = [fp.index[day] for day in days]
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
    features, names = _features(returns, np.array(breadth), taiex)
    return Families(days, returns, features, names)


# --- the environment -----------------------------------------------------------------------------
def step(returns_next: np.ndarray, weights: np.ndarray, previous: np.ndarray, value: np.ndarray, peak: np.ndarray,
         switch_penalty: float = SWITCH_PENALTY) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """One session for every environment (rows): returns (reward, value, peak, drawdown). Moving money
    between families costs MOVE_COST of the amount moved (half the sum of the weight changes)."""
    moved = 0.5 * np.abs(weights - previous).sum(axis=1)
    growth = (weights * returns_next).sum(axis=1) - MOVE_COST * moved
    drawdown_before = value / peak - 1
    value = value * (1 + growth)
    peak = np.maximum(peak, value)
    drawdown = value / peak - 1
    bench = returns_next[:, FAMILIES.index("0050")]
    reward = (np.log1p(growth) - np.log1p(bench) - DRAWDOWN_PENALTY * np.maximum(0.0, drawdown_before - drawdown)
              - switch_penalty * moved)
    return reward, value, peak, drawdown


def _normaliser(features: np.ndarray, low: int, high: int) -> tuple[np.ndarray, np.ndarray]:
    window = features[low:high]
    mean = np.nanmean(window, axis=0)
    std = np.nanstd(window, axis=0)
    return np.nan_to_num(mean), np.where(np.isfinite(std) & (std > 0), std, 1.0)


def _state(features: np.ndarray, mean: np.ndarray, std: np.ndarray, weights: np.ndarray, drawdown: np.ndarray,
           noise: np.ndarray | None = None) -> np.ndarray:
    normal = np.clip(np.nan_to_num((features - mean) / std), -5, 5)
    if noise is not None:
        normal = normal + noise
    return np.column_stack([normal, weights * 2 - 1, drawdown * 5]).astype(np.float32)


def train_policy(families: Families, low: int, high: int, seed: int, config: dict | None = None):
    """PPO on random episodes inside [low, high); returns (policy network, normaliser)."""
    import torch

    config = {**CONFIG, **(config or {})}
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    mean, std = _normaliser(families.features, low, high)
    inputs = families.features.shape[1] + len(FAMILIES) + 1
    hidden = config["hidden"]
    policy = torch.nn.Sequential(torch.nn.Linear(inputs, hidden), torch.nn.Tanh(), torch.nn.Linear(hidden, hidden),
                                 torch.nn.Tanh(), torch.nn.Linear(hidden, len(MENU)))
    critic = torch.nn.Sequential(torch.nn.Linear(inputs, hidden), torch.nn.Tanh(), torch.nn.Linear(hidden, hidden),
                                 torch.nn.Tanh(), torch.nn.Linear(hidden, 1))
    parameters = list(policy.parameters()) + list(critic.parameters())
    optimiser = torch.optim.Adam(parameters, lr=config["lr"])
    envs, length = config["envs"], min(config["episode"], high - low - 2)
    if length < 20:
        raise ValueError("訓練期間太短")
    menu = np.array(MENU)
    for _iteration in range(config["iterations"]):
        starts = rng.integers(low, high - length - 1, size=envs)
        if config.get("random_start"):
            weights = menu[rng.integers(0, len(MENU), size=envs)]
        else:
            weights = np.tile(menu[-1], (envs, 1))              # 1.0.0: a quarter, a quarter, half in 0050
        value, peak, drawdown = np.ones(envs), np.ones(envs), np.zeros(envs)
        observations, actions, logps, rewards, values = [], [], [], [], []
        for offset in range(length):
            day = starts + offset
            noise = rng.normal(0, FEATURE_NOISE, size=(envs, families.features.shape[1]))
            state = _state(families.features[day], mean, std, weights, drawdown, noise)
            with torch.no_grad():
                distribution = torch.distributions.Categorical(logits=policy(torch.from_numpy(state)))
                action = distribution.sample()
                estimate = critic(torch.from_numpy(state)).squeeze(-1)
            chosen = menu[action.numpy()]
            reward, value, peak, drawdown = step(families.returns[day + 1], chosen, weights, value, peak,
                                                 config.get("switch_penalty", SWITCH_PENALTY))
            observations.append(state)
            actions.append(action.numpy())
            logps.append(distribution.log_prob(action).numpy())
            rewards.append(reward)
            values.append(estimate.numpy())
            weights = chosen
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
    return policy, (mean, std)


def run_policy(policies: list, families: Families, low: int, high: int, start: np.ndarray) -> np.ndarray:
    """The mix held from each session in [low, high): the seeds' greedy choices averaged."""
    import torch

    menu = np.array(MENU)
    chosen_all = np.empty((high - low, len(FAMILIES)))
    weights, drawdown, value, peak = start.copy(), 0.0, 1.0, 1.0
    for offset, day in enumerate(range(low, high)):
        picks = []
        for policy, (mean, std) in policies:
            state = _state(families.features[day: day + 1], mean, std, weights[None, :], np.array([drawdown]))
            with torch.no_grad():
                picks.append(menu[int(policy(torch.from_numpy(state)).argmax())])
        new = np.mean(picks, axis=0)
        if day + 1 < len(families.returns):
            growth = float(new @ families.returns[day + 1]) - MOVE_COST * 0.5 * float(np.abs(new - weights).sum())
            value *= 1 + growth
            peak = max(peak, value)
            drawdown = value / peak - 1
        chosen_all[offset] = new
        weights = new
    return chosen_all


def path(families: Families, mixes: np.ndarray, low: int, start: np.ndarray | None = None) -> np.ndarray:
    """Unit values of an account holding ``mixes[i]`` from session low+i's close, paying for every move."""
    values = [1.0]
    previous = mixes[0] if start is None else start
    for offset in range(len(mixes) - 1):
        mix = mixes[offset]
        growth = float(mix @ families.returns[low + offset + 1]) - MOVE_COST * 0.5 * float(np.abs(mix - previous).sum())
        values.append(values[-1] * (1 + growth))
        previous = mix
    return np.array(values)


def _summary(values: np.ndarray, years: float) -> dict[str, float]:
    peak = np.maximum.accumulate(values)
    return {"growth": round(float(values[-1] - 1), 4),
            "annual": round(float(values[-1] ** (1 / years) - 1), 4) if years else 0.0,
            "max_drawdown": round(float((values / peak - 1).min()), 4)}


def best_fixed(families: Families, low: int, high: int) -> int:
    """The menu mix with the highest growth on [low, high) held throughout (what hindsight on the
    training years would pick)."""
    scores = []
    for mix in np.array(MENU):
        values = path(families, np.tile(mix, (high - low, 1)), low)
        peak = np.maximum.accumulate(values)
        scores.append(np.log(values[-1]) - DRAWDOWN_PENALTY * float(-(values / peak - 1).min()))
    return int(np.argmax(scores))


def walk_forward(families: Families, out_dir: str | Path | None = None, seeds: tuple[int, ...] = SEEDS,
                 first_year: int = FIRST_TEST_YEAR, config: dict | None = None, job=None,
                 version: str = RL_SLEEVES_VERSION) -> dict[str, object]:
    """Train for each test year on the sessions before it, run it; the report (saved when ``out_dir``)."""
    menu = np.array(MENU)
    ready = int(np.argmax(np.isfinite(families.features).all(axis=1)))
    years = sorted({day.year for day in families.days if day.year >= first_year})
    mixes_all, fixed_all, report_years = [], [], {}
    start = menu[-1]
    for number, year in enumerate(years):
        low = next(index for index, day in enumerate(families.days) if day.year == year)
        high = max(index for index, day in enumerate(families.days) if day.year == year) + 1
        if job:
            job.update(done=number, current=f"{year} 年（訓練 {families.days[ready]}～{families.days[low - 1]}）", force=True)
        policies = [train_policy(families, ready, low, seed + year, {**VERSIONS[version], **(config or {})})
                    for seed in seeds]
        mixes = run_policy(policies, families, low, high, start)
        chosen = best_fixed(families, ready, low)
        fixed = np.tile(menu[chosen], (high - low, 1))
        sessions, span = high - low, (high - low) / 245
        report_years[str(year)] = {
            "sessions": sessions, "average": {name: round(float(mixes[:, index].mean()), 3) for index, name in enumerate(FAMILIES)},
            "moves": int((np.abs(np.diff(np.vstack([start, mixes]), axis=0)).sum(axis=1) > 1e-9).sum()),
            "best_fixed": [round(value, 3) for value in MENU[chosen]],
            "rl": _summary(path(families, mixes, low, start), span),
            "best_fixed_result": _summary(path(families, fixed, low), span),
            **{name: _summary(path(families, np.tile(np.eye(len(FAMILIES))[index], (sessions, 1)), low), span)
               for index, name in enumerate(FAMILIES)},
            "thirds": _summary(path(families, np.tile(menu[6], (sessions, 1)), low), span),
            "quarters_half_0050": _summary(path(families, np.tile(menu[7], (sessions, 1)), low), span),
        }
        mixes_all.append(mixes)
        fixed_all.append(fixed)
        start = mixes[-1]
    first = next(index for index, day in enumerate(families.days) if day.year == years[0])
    mixes_all, fixed_all = np.vstack(mixes_all), np.vstack(fixed_all)
    count, span = len(mixes_all), len(mixes_all) / 245
    report = {
        "version": version, "config": {**CONFIG, **VERSIONS[version], **(config or {})}, "menu": [list(mix) for mix in MENU],
        "families": [FAMILY_LABELS[name] for name in FAMILIES], "move_cost": MOVE_COST, "switch_penalty": SWITCH_PENALTY,
        "drawdown_penalty": DRAWDOWN_PENALTY, "features": families.names, "seeds": list(seeds),
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "test_from": families.days[first].isoformat(), "test_to": families.days[-1].isoformat(), "years": report_years,
        "overall": {
            "rl": _summary(path(families, mixes_all, first, menu[-1]), span),
            "best_fixed_result": _summary(path(families, fixed_all, first), span),
            **{name: _summary(path(families, np.tile(np.eye(len(FAMILIES))[index], (count, 1)), first), span)
               for index, name in enumerate(FAMILIES)},
            "thirds": _summary(path(families, np.tile(menu[6], (count, 1)), first), span),
            "quarters_half_0050": _summary(path(families, np.tile(menu[7], (count, 1)), first), span),
            "average": {name: round(float(mixes_all[:, index].mean()), 3) for index, name in enumerate(FAMILIES)},
        },
        "mixes": {families.days[first + index].isoformat(): [round(float(value), 3) for value in row]
                  for index, row in enumerate(mixes_all)},
    }
    if out_dir is not None:
        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
