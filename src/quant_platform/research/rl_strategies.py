"""2026-10-10 (使用者同意): RL 3.0 — the account shared among the T0 candidates.

RL 1.x and 2.0 chose among two or three places (the model family, the trend family, 0050 or cash) and
ended level with a fixed mix: too few, too alike. Now there are many 100%-stock strategies that pass the
gate and move differently (the scans keep finalists below 0.9 daily correlation). Every session after the
close the agent sets the share of each T0 candidate and of cash. Moving money between strategies costs
MOVE_COST of the amount moved (one strategy's stocks sold, another's bought). The state: each strategy's
5-, 20- and 60-session return, 20-session volatility and fall from its peak; TAIEX against its 200-day
average and the share of stocks above theirs; the current shares. The reward: the account's log growth
above the strategies held in equal shares, less the drawdown deepening and the moving. PPO with a
Gaussian over the shares' logits (softmax). Walk-forward by year from 2018, three seeds averaged.

The strategies: every 100%-stock rule rated T0 候選 when it runs, replayed by the full engine on the
owner's account (2015-06..2026-09; unit values, each already paying its own trading costs). Most were
chosen with 2015-06..2020-09 data (the scans), so only 2021 on is clean out of sample; the report gives both.
Passes only if, out of sample, it beats equal shares (rebalanced monthly), the best single strategy in the
training years and 0050, with a drawdown within 0050's + 5 points. Never 0050 inside.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

VERSION = "rl-strategies-3.0.0"
MOVE_COST = 0.006
DRAWDOWN_PENALTY = 1.0
FIRST_YEAR = 2018
CLEAN_FROM = 2021
SEEDS = (0, 1, 2)
CONFIG = {"iterations": 150, "envs": 64, "episode": 120, "gamma": 0.98, "lam": 0.95, "lr": 3e-4, "clip": 0.2,
          "epochs": 4, "minibatch": 1024, "hidden": 64, "value": 0.5, "entropy": 0.001, "log_std": -0.5,
          "threads": 4}


@dataclass
class Strategies:
    days: list[date]
    names: list[str]
    trials: list[int]
    returns: np.ndarray          # sessions × strategies: from the previous close to this one (first row 0)
    bench: np.ndarray            # sessions: 0050's
    features: np.ndarray         # sessions × features (known at that close)


def _features(returns: np.ndarray, breadth: np.ndarray, taiex_gap: np.ndarray) -> np.ndarray:
    frame = pd.DataFrame(returns)
    growth = (1 + frame).cumprod()
    parts = [frame.rolling(5).sum(), frame.rolling(20).sum(), frame.rolling(60).sum(),
             frame.rolling(20).std() * np.sqrt(245), growth / growth.cummax() - 1]
    return np.column_stack([part.to_numpy() for part in parts] + [taiex_gap, breadth]).astype(np.float64)


def t0_candidates(research: Path) -> list[tuple[int, str, dict]]:
    """(trial, name, spec) of every 100%-stock daily rule whose latest record is T0 候選."""
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.daily import tier
    from quant_platform.research.registry import TrialRegistry

    latest = {}
    for record in TrialRegistry(research / "trials.jsonl").records():
        if record.kind == "candidate" and record.period == "recent":
            latest[record.spec_hash] = record
    output = []
    for record in latest.values():
        if tier(record.metrics)[0] != "T0 候選":
            continue
        try:
            spec = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
        except (OSError, ValueError, KeyError):
            continue
        if spec.get("kind") == "blend" or uses_0050(spec):
            continue
        output.append((record.trial_id, record.spec_name, spec))
    return sorted(output)


def build(data, fp, costs, specs: list[tuple[int, str, dict]]) -> Strategies:
    from quant_platform.research.daily import (
        RECENT_END,
        RECENT_START,
        DailyRule,
        account_parking,
        daily_rankings,
        daily_weights,
        exposure_schedule,
        simulate_daily,
    )
    from quant_platform.research.metrics import unit_values
    from quant_platform.research.stock_rules import SeedPlan

    days = [day for day in fp.sessions if RECENT_START <= day <= RECENT_END]
    columns = []
    for _trial, _name, spec in specs:
        rule = DailyRule.model_validate(spec)
        ranks = daily_rankings(fp, rule, RECENT_START, RECENT_END)
        run = simulate_daily(data, rule, costs, RECENT_START, RECENT_END, ranks, plan=SeedPlan(),
                             weights=daily_weights(fp, rule, ranks), parked=account_parking(data, fp, rule, costs),
                             exposure=exposure_schedule(data, fp, rule, costs))
        units = pd.Series(unit_values(run.values, run.flows), index=run.days).reindex(days).ffill().bfill()
        columns.append(units.pct_change().fillna(0.0).to_numpy())
    bench_run = simulate_daily(data, None, costs, RECENT_START, RECENT_END, plan=SeedPlan())
    bench = pd.Series(unit_values(bench_run.values, bench_run.flows), index=bench_run.days).reindex(days).ffill().bfill()
    returns = np.column_stack(columns)
    position = [fp.index[day] for day in days]
    trend = fp.matrix("trend_200")[:, position]
    with np.errstate(invalid="ignore"):
        breadth = np.nanmean(np.where(np.isfinite(trend), trend > 0, np.nan), axis=0)
    taiex = pd.Series(fp.market) if fp.market is not None else pd.Series(np.ones(len(fp.sessions)))
    gap = (taiex / taiex.rolling(200, min_periods=50).mean() - 1).to_numpy()[position]
    return Strategies(days, [name for _trial, name, _spec in specs], [trial for trial, _name, _spec in specs], returns,
                      bench.pct_change().fillna(0.0).to_numpy(), _features(returns, np.nan_to_num(breadth, nan=0.5),
                                                                           np.nan_to_num(gap)))


def _normaliser(features: np.ndarray, low: int, high: int):
    window = features[low:high]
    mean = np.nan_to_num(np.nanmean(window, axis=0))
    std = np.nanstd(window, axis=0)
    return mean, np.where(np.isfinite(std) & (std > 0), std, 1.0)


def _state(features: np.ndarray, mean: np.ndarray, std: np.ndarray, weights: np.ndarray) -> np.ndarray:
    normal = np.clip(np.nan_to_num((features - mean) / std), -5, 5)
    return np.column_stack([normal, weights * 2 - 1]).astype(np.float32)


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = np.exp(logits - logits.max(axis=-1, keepdims=True))
    return shifted / shifted.sum(axis=-1, keepdims=True)


def _drift(weights: np.ndarray, returns_next: np.ndarray) -> np.ndarray:
    """The shares after a session's moves (cash is the last share and earns nothing)."""
    grown = weights * np.concatenate([1 + returns_next, np.ones((*returns_next.shape[:-1], 1))], axis=-1)
    return grown / grown.sum(axis=-1, keepdims=True)


def step(returns_next: np.ndarray, weights: np.ndarray, previous: np.ndarray, value: np.ndarray, peak: np.ndarray,
         drawdown_penalty: float = DRAWDOWN_PENALTY):
    """One session for every environment: (reward, value, peak). ``weights`` include cash (last)."""
    moved = 0.5 * np.abs(weights - previous).sum(axis=1)
    growth = (weights[:, :-1] * returns_next).sum(axis=1) - MOVE_COST * moved
    equal = returns_next.mean(axis=1)
    before = value / peak - 1
    value = value * (1 + growth)
    peak = np.maximum(peak, value)
    after = value / peak - 1
    reward = np.log1p(growth) - np.log1p(equal) - drawdown_penalty * np.maximum(0.0, before - after)
    return reward, value, peak


def train_policy(strategies: Strategies, low: int, high: int, seed: int, config: dict | None = None):
    import torch

    config = {**CONFIG, **(config or {})}
    torch.set_num_threads(config["threads"])
    rng = np.random.default_rng(seed)
    torch.manual_seed(seed)
    count = strategies.returns.shape[1]
    mean, std = _normaliser(strategies.features, low, high)
    inputs = strategies.features.shape[1] + count + 1
    hidden = config["hidden"]
    policy = torch.nn.Sequential(torch.nn.Linear(inputs, hidden), torch.nn.Tanh(), torch.nn.Linear(hidden, hidden),
                                 torch.nn.Tanh(), torch.nn.Linear(hidden, count + 1))
    critic = torch.nn.Sequential(torch.nn.Linear(inputs, hidden), torch.nn.Tanh(), torch.nn.Linear(hidden, hidden),
                                 torch.nn.Tanh(), torch.nn.Linear(hidden, 1))
    log_std = torch.nn.Parameter(torch.full((count + 1,), float(config["log_std"])))
    parameters = list(policy.parameters()) + list(critic.parameters()) + [log_std]
    optimiser = torch.optim.Adam(parameters, lr=config["lr"])
    envs, length = config["envs"], min(config["episode"], high - low - 2)
    if length < 20:
        raise ValueError("訓練期間太短")
    equal = np.r_[np.full(count, 1 / count), 0.0]
    for _iteration in range(config["iterations"]):
        starts = rng.integers(low, high - length - 1, size=envs)
        weights = np.tile(equal, (envs, 1))
        value, peak = np.ones(envs), np.ones(envs)
        observations, actions, logps, rewards, values = [], [], [], [], []
        for offset in range(length):
            day = starts + offset
            state = _state(strategies.features[day], mean, std, weights)
            with torch.no_grad():
                distribution = torch.distributions.Normal(policy(torch.from_numpy(state)), log_std.exp())
                action = distribution.sample()
                estimate = critic(torch.from_numpy(state)).squeeze(-1)
            chosen = _softmax(action.numpy().astype(np.float64))
            following = strategies.returns[day + 1]
            reward, value, peak = step(following, chosen, weights, value, peak)
            observations.append(state)
            actions.append(action.numpy())
            logps.append(distribution.log_prob(action).sum(-1).numpy())
            rewards.append(reward)
            values.append(estimate.numpy())
            weights = _drift(chosen, following)
        rewards_ = np.array(rewards) * 100
        values_ = np.array(values)
        advantages = np.zeros_like(rewards_)
        running = np.zeros(envs)
        for t in reversed(range(length)):
            following = values_[t + 1] if t + 1 < length else np.zeros(envs)
            delta = rewards_[t] + config["gamma"] * following - values_[t]
            running = delta + config["gamma"] * config["lam"] * running
            advantages[t] = running
        targets = advantages + values_
        flat_obs = torch.from_numpy(np.concatenate(observations))
        flat_actions = torch.from_numpy(np.concatenate(actions))
        flat_logps = torch.from_numpy(np.concatenate(logps))
        flat_adv = torch.from_numpy(advantages.reshape(-1).astype(np.float32))
        flat_adv = (flat_adv - flat_adv.mean()) / (flat_adv.std() + 1e-8)
        flat_targets = torch.from_numpy(targets.reshape(-1).astype(np.float32))
        total = len(flat_actions)
        for _epoch in range(config["epochs"]):
            order = torch.from_numpy(rng.permutation(total))
            for begin in range(0, total, config["minibatch"]):
                part = order[begin: begin + config["minibatch"]]
                distribution = torch.distributions.Normal(policy(flat_obs[part]), log_std.exp())
                ratio = torch.exp(distribution.log_prob(flat_actions[part]).sum(-1) - flat_logps[part])
                surrogate = torch.min(ratio * flat_adv[part],
                                      torch.clamp(ratio, 1 - config["clip"], 1 + config["clip"]) * flat_adv[part])
                value_loss = (critic(flat_obs[part]).squeeze(-1) - flat_targets[part]).pow(2).mean()
                loss = (-surrogate.mean() + config["value"] * value_loss
                        - config["entropy"] * distribution.entropy().sum(-1).mean())
                optimiser.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(parameters, 0.5)
                optimiser.step()
    return policy, (mean, std)


def run_policy(policies: list, strategies: Strategies, low: int, high: int, start: np.ndarray) -> np.ndarray:
    """The shares set at each close in [low, high): the seeds' greedy shares averaged."""
    import torch

    output = np.empty((high - low, strategies.returns.shape[1] + 1))
    weights = start.copy()
    for offset, day in enumerate(range(low, high)):
        shares = []
        for policy, (mean, std) in policies:
            state = _state(strategies.features[day: day + 1], mean, std, weights[None, :])
            with torch.no_grad():
                shares.append(_softmax(policy(torch.from_numpy(state)).numpy().astype(np.float64))[0])
        chosen = np.mean(shares, axis=0)
        output[offset] = chosen
        if day + 1 < len(strategies.returns):
            weights = _drift(chosen[None, :], strategies.returns[day + 1][None, :])[0]
    return output


def path(strategies: Strategies, shares: np.ndarray, low: int, start: np.ndarray) -> np.ndarray:
    """Unit values of an account set to ``shares[i]`` at session low+i's close, paying for every move."""
    values, previous = [1.0], start
    for offset in range(len(shares) - 1):
        chosen = shares[offset]
        moved = 0.5 * float(np.abs(chosen - previous).sum())
        following = strategies.returns[low + offset + 1]
        growth = float(chosen[:-1] @ following) - MOVE_COST * moved
        values.append(values[-1] * (1 + growth))
        previous = _drift(chosen[None, :], following[None, :])[0]
    return np.array(values)


def monthly_equal(strategies: Strategies, low: int, high: int) -> np.ndarray:
    """Equal shares set again on each month's first session (the simple rule RL 3.0 must beat)."""
    count = strategies.returns.shape[1]
    equal = np.r_[np.full(count, 1 / count), 0.0]
    shares, current = [], equal.copy()
    for offset, day in enumerate(range(low, high)):
        if offset == 0 or strategies.days[day].month != strategies.days[day - 1].month:
            current = equal.copy()
        shares.append(current)
        if day + 1 < len(strategies.returns):
            current = _drift(current[None, :], strategies.returns[day + 1][None, :])[0]
    return np.array(shares)


def _summary(values: np.ndarray, sessions: int) -> dict[str, float]:
    years = max(sessions / 245, 1e-9)
    return {"growth": round(float(values[-1] - 1), 4), "annual": round(float(values[-1] ** (1 / years) - 1), 4),
            "max_drawdown": round(float(np.min(values / np.maximum.accumulate(values) - 1)), 4)}


def walk_forward(strategies: Strategies, out_dir: Path | None = None, seeds: tuple[int, ...] = SEEDS,
                 first_year: int = FIRST_YEAR, config: dict | None = None, job=None) -> dict:
    count = strategies.returns.shape[1]
    equal = np.r_[np.full(count, 1 / count), 0.0]
    years = sorted({day.year for day in strategies.days if day.year >= first_year})
    start_of = {year: next(index for index, day in enumerate(strategies.days) if day.year == year) for year in years}
    rl_parts, equal_parts, best_parts, bench_parts, yearly, weights_all = [], [], [], [], {}, []
    shares_start = equal.copy()
    for number, year in enumerate(years):
        low = start_of[year]
        high = start_of[years[number + 1]] if number + 1 < len(years) else len(strategies.days)
        if job:
            job.update(done=number, current=f"{year} 年的分配（{count} 個策略）", force=True)
        policies = [train_policy(strategies, 60, low, seed + year * 10, config) for seed in seeds]
        shares = run_policy(policies, strategies, low, high, shares_start)
        rl = path(strategies, shares, low, shares_start)
        flat = path(strategies, monthly_equal(strategies, low, high), low, equal)
        training = (1 + strategies.returns[60:low]).prod(axis=0)
        best = int(np.argmax(training))
        single = np.zeros(count + 1)
        single[best] = 1.0
        best_path = path(strategies, np.tile(single, (high - low, 1)), low, single)
        bench = np.cumprod(np.r_[1.0, 1 + strategies.bench[low + 1: high]])
        yearly[str(year)] = {"rl": _summary(rl, high - low), "equal": _summary(flat, high - low),
                             "best_in_training": {**_summary(best_path, high - low), "name": strategies.names[best]},
                             "0050": _summary(bench, high - low),
                             "average_shares": {name: round(float(value), 3) for name, value in
                                                zip([*strategies.names, "現金"], shares.mean(axis=0))}}
        for parts, values in ((rl_parts, rl), (equal_parts, flat), (best_parts, best_path), (bench_parts, bench)):
            parts.append(values[1:] / values[:-1] - 1)
        weights_all.append(shares)
        shares_start = _drift(shares[-1][None, :], strategies.returns[min(high, len(strategies.returns) - 1)][None, :])[0]

    def chain(parts, since=None):
        joined = np.concatenate(parts) if parts else np.zeros(0)
        return np.cumprod(np.r_[1.0, 1 + joined])

    total = sum(len(part) for part in rl_parts)
    overall = {"rl": _summary(chain(rl_parts), total), "equal": _summary(chain(equal_parts), total),
               "best_in_training": _summary(chain(best_parts), total), "0050": _summary(chain(bench_parts), total)}
    clean_index = [index for index, year in enumerate(years) if year >= CLEAN_FROM]
    clean_total = sum(len(rl_parts[index]) for index in clean_index)
    clean = {key: _summary(chain([parts[index] for index in clean_index]), clean_total)
             for key, parts in (("rl", rl_parts), ("equal", equal_parts), ("best_in_training", best_parts),
                                ("0050", bench_parts))} if clean_index else {}
    rl, flat, best, bench = overall["rl"], overall["equal"], overall["best_in_training"], overall["0050"]
    acceptance = {"beats_equal": rl["annual"] > flat["annual"], "beats_best_single": rl["annual"] > best["annual"],
                  "beats_0050": rl["annual"] > bench["annual"],
                  "drawdown_within_5": rl["max_drawdown"] >= bench["max_drawdown"] - 0.05}
    acceptance["passed"] = all(acceptance.values())
    shares_all = np.concatenate(weights_all) if weights_all else np.zeros((0, count + 1))
    report = {"version": VERSION, "strategies": [{"trial": trial, "name": name} for trial, name in
                                                  zip(strategies.trials, strategies.names)],
              "period": [strategies.days[start_of[years[0]]].isoformat(), strategies.days[-1].isoformat()],
              "clean_from": CLEAN_FROM, "seeds": list(seeds), "config": {**CONFIG, **(config or {})},
              "move_cost": MOVE_COST, "drawdown_penalty": DRAWDOWN_PENALTY, "overall": overall, "clean": clean,
              "years": yearly, "acceptance": acceptance,
              "average_shares": {name: round(float(value), 3) for name, value in
                                 zip([*strategies.names, "現金"], shares_all.mean(axis=0))} if len(shares_all) else {},
              "built_at": datetime.now(UTC).isoformat(timespec="seconds")}
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        (Path(out_dir) / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report
