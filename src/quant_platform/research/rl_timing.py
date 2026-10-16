"""The technical RL robot, second design (2026-10-14; 使用者：是不是 RL 的機制有問題？為什麼會學到不交易比較簡單？
懲罰機制還是目標設計有問題？).

The first robot (research/tech_rl.py) played one game per stock: be in or out, paid the stock's excess over the
day's average stock. Out was worth exactly 0, in was worth about 0 before costs, so with a real signal of a few
hundredths of a percent a day and about 1% to get in and out, staying out was its best answer. The objective was
wrong, not the penalty: no opportunity cost for standing aside, a choice nobody holding a portfolio makes, and a
horizon of one session (then five) for signals that work over months.

This robot is always fully invested and never decides whether to trade, only how. Every 20 sessions it sees the
market's technical state — each of the 20 technical factors' realised record over the last year (the top fifth's
next-20-session return minus the bottom fifth's, counting only spreads already realised, research/factor_timing.py),
TAIEX against its 200-session average, the share of stocks above theirs and TAIEX's 20-session volatility — and
chooses weights over the 20 technical factors (softmax of a linear policy's outputs). The account holds the 20
eligible stocks with the highest weighted percentile, equal amounts. Its reward is the account's log return over
the next 20 sessions minus 0050's over the same sessions, less the cost of the stocks it swapped. A penalty keeps
the weights near equal unless the record argues otherwise (the reference policy is "use every factor alike").

Walk-forward: the weights used in year Y come from policies (three seeds, averaged) trained only on decisions whose
reward was realised before Y's first session. Score ``rl_timing``: each session's weighted percentile with the
weights of the latest decision. Judged like every rule, against "every technical factor alike" (``tech_equal``)
and factor momentum on the same 20 factors (``tech_momentum``) — the robot counts only if it beats both.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

VERSION = "tech-rl-2.0.0"
# 2.1.0 (2026-10-14): the same robot over every factor with a full history — price, technical, chip, statement and
# trend-quality factors and the trained models' walk-forward scores (2.0.0 beat its two technical yardsticks under
# the engine from 2018, 547 against 482 and 464 (NT$10k), but not 0050's 654: price and volume alone were not enough).
# Left out: monthly revenue (lagging, the owner), market value, the families of 2026-10-14 (time: weak; lending,
# day trading, alerts: their data was still coming) and the meta factors.
ALL_VERSION = "factor-rl-2.1.0"
STEP = 20                    # sessions between decisions, and the reward's horizon
TOP = 20
COST_SWAP = 0.0035 + 0.0065  # one stock out and one in: fees, slippage, the sell tax (as tech_rl)
SIGMA = 0.5                  # exploration on the policy's outputs
PENALTY = 0.05               # pulls the outputs to 0, i.e. equal weights
EPOCHS, ROLLOUTS, LEARNING_RATE = 300, 64, 0.02
SEEDS = (0, 1, 2)
FIRST_TEST_YEAR = 2018
# 3.0.0 (2026-10-16; 使用者：多因子都能有 T0，RL 找不到？是學習策略有問題，還是給它的因子不夠多？): 2.0 and 2.1 learned
# the training years and not the next one — 2.1's training reward rose to +3% to +4% over 0050 every 20 sessions while
# its nine test years summed to −37% (equal weights −40%); its policy had about 2,300 numbers to fit from 41 to 139
# decisions a year, and its weights jumped from year to year (63% on one factor in 2021, 62% on another in 2026). More
# factors made it worse, not better. The third robot changes the learning, not the game:
# - eight blocks instead of 47 factors — each family's strongest signal by its top fifth in 2015-06..2020-10 (the
#   development period only): the 52-week high, above the 200-session average, KD, three-month momentum, margin
#   growth, little lent-and-sold stock, EPS growth, and model F (the walk-forward ensemble); about 100 numbers;
# - four times the decisions: the same 20-session game started on four offsets (0, 5, 10, 15 sessions);
# - the last fifth of each year's training decisions held out: training stops at the epoch the held-out reward is
#   best, and when even that does not beat the eight blocks alike the robot uses them alike for the year (abstains);
# - a stronger pull to equal weights, five seeds, and every year's learning curves kept (the research page shows them).
BLOCK_VERSION = "block-rl-3.0.0"
BLOCKS = ("high_52w", "trend_200", "kd_k", "momentum_3", "margin_growth_20", "low_sbl_short", "eps_growth", "ml_gbm_ens")
PHASES = (0, 5, 10, 15)
HELD_OUT = 0.2
BLOCK_PENALTY = 0.2
BLOCK_SEEDS = (0, 1, 2, 3, 4)
CHECK_EVERY = 10


def factor_names(version: str = VERSION) -> list[str]:
    from quant_platform.research.tech_rl import TECH_FEATURES

    if version == BLOCK_VERSION:
        return list(BLOCKS)
    if version == ALL_VERSION:
        from quant_platform.research.chips import (
            ALERT_FACTORS,
            CORPORATE_FACTORS,
            DEALER_FACTORS,
            ISSUANCE_FACTORS,
            LENDING_FACTORS,
            SHORT_FACTORS,
        )
        from quant_platform.research.daily import FACTOR_LABELS, MODEL_FACTORS, TIME_FACTORS

        left_out = {"revenue_yoy", "revenue_yoy_3m", "revenue_accel", "market_cap",
                    *TIME_FACTORS, *SHORT_FACTORS, *ALERT_FACTORS, *LENDING_FACTORS, *CORPORATE_FACTORS,
                    *ISSUANCE_FACTORS, *DEALER_FACTORS, "ml_gbm_ext"}
        return [name for name in (*FACTOR_LABELS, *MODEL_FACTORS) if name not in left_out]
    return list(TECH_FEATURES)


class Timeline:
    """The decisions: their positions, states, factor percentiles, forward returns and 0050's."""

    def __init__(self, fp, benchmark: np.ndarray, version: str = VERSION, offset: int = 0) -> None:
        from quant_platform.research.factor_timing import records
        from quant_platform.research.model import eligibility

        names = factor_names(version)
        realised, ranks = records(fp, names)
        eligible = eligibility(fp)
        sessions = len(fp.sessions)
        values = realised.to_numpy()
        ready = np.flatnonzero(np.isfinite(values).sum(axis=1) >= len(names) // 2)   # most factors have a record
        start = int(ready[0]) if len(ready) else sessions
        self.positions = list(range(start + offset, sessions - STEP, STEP))
        self.days = [fp.sessions[position] for position in self.positions]
        market = fp.market if fp.market is not None else np.full(sessions, np.nan)
        taiex = pd.Series(market)
        gap = (taiex / taiex.rolling(200, min_periods=50).mean() - 1).to_numpy()
        volatility = taiex.pct_change(fill_method=None).rolling(20, min_periods=10).std().to_numpy()
        trend = fp.matrix("trend_200")
        prices = fp.panel.filled
        states, percentiles, forward, bench = [], [], [], []
        for position in self.positions:
            with np.errstate(invalid="ignore"):
                breadth = np.nanmean(np.where(eligible[:, position] & np.isfinite(trend[:, position]),
                                              trend[:, position] > 0, np.nan))
            states.append(np.concatenate([np.clip(np.nan_to_num(values[position]), -0.2, 0.2) * 10,
                                          [np.clip(np.nan_to_num(gap[position]), -0.5, 0.5) * 4,
                                           np.nan_to_num(breadth, nan=0.5) * 2 - 1,
                                           np.clip(np.nan_to_num(volatility[position]), 0, 0.05) * 40 - 1]]))
            stack = np.stack([ranks[name][:, position] for name in names])         # factor × symbol
            percentiles.append(np.nan_to_num(stack, nan=0.5).astype(np.float32))
            with np.errstate(divide="ignore", invalid="ignore"):
                move = prices[:, position + STEP] / prices[:, position] - 1
            forward.append(np.where(eligible[:, position] & np.isfinite(move), move, np.nan))
            bench.append(benchmark[position + STEP] / benchmark[position] - 1)
        self.names = names
        self.states = np.array(states, dtype=np.float32)
        self.percentiles = np.array(percentiles, dtype=np.float32)             # decision × factor × symbol
        self.forward = np.array(forward, dtype=np.float64)                     # decision × symbol
        self.bench = np.array(bench, dtype=np.float64)

    def picks(self, weights: np.ndarray, decision: int) -> np.ndarray:
        """rollout × TOP: the stocks with the highest weighted percentile among those with a forward return."""
        score = weights @ self.percentiles[decision]                            # rollout × symbol
        score = np.where(np.isfinite(self.forward[decision])[None, :], score, -np.inf)
        return np.argpartition(-score, TOP, axis=1)[:, :TOP]

    def rewards(self, weights: np.ndarray, decisions: list[int]) -> np.ndarray:
        """decision × rollout: log return over the next STEP sessions minus 0050's, less the swaps' cost."""
        output = np.zeros((len(decisions), weights.shape[1]))
        previous = None
        for index, decision in enumerate(decisions):
            chosen = self.picks(weights[index], decision)
            gross = np.nanmean(self.forward[decision][chosen], axis=1)
            swapped = (np.array([TOP - len(np.intersect1d(now, before)) for now, before in zip(chosen, previous)])
                       if previous is not None else np.full(len(chosen), TOP))
            cost = swapped / TOP * COST_SWAP
            output[index] = np.log1p(np.clip(gross - cost, -0.9, None)) - np.log1p(self.bench[decision])
            previous = chosen
        return output


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - logits.max(axis=-1, keepdims=True)
    exponent = np.exp(shifted)
    return exponent / exponent.sum(axis=-1, keepdims=True)


def train(timeline: Timeline, decisions: list[int], seed: int) -> tuple[np.ndarray, np.ndarray, list[float]]:
    """A linear policy (weights W, bias b: state -> one output per factor) by REINFORCE with a per-decision baseline
    and the pull to equal weights; returns (W, b, the mean training reward per epoch)."""
    import torch

    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    states = torch.from_numpy(timeline.states[decisions])
    count = len(timeline.names)
    weight = torch.zeros((states.shape[1], count), requires_grad=True)
    bias = torch.zeros(count, requires_grad=True)
    optimiser = torch.optim.Adam([weight, bias], lr=LEARNING_RATE)
    history = []
    for _epoch in range(EPOCHS):
        mean = states @ weight + bias                                           # decision × factor
        noise = rng.normal(0, SIGMA, (len(decisions), ROLLOUTS, count)).astype(np.float32)
        actions = mean.detach().numpy()[:, None, :] + noise
        rewards = timeline.rewards(_softmax(actions), decisions)                # decision × rollout
        history.append(float(rewards.mean()))
        advantage = torch.from_numpy(((rewards - rewards.mean(axis=1, keepdims=True))
                                      / (rewards.std(axis=1, keepdims=True) + 1e-8)).astype(np.float32))
        log_prob = -(((torch.from_numpy(actions) - mean[:, None, :]) ** 2).sum(-1)) / (2 * SIGMA ** 2)
        loss = -(advantage * log_prob).mean() + PENALTY * (mean ** 2).mean()
        optimiser.zero_grad()
        loss.backward()
        optimiser.step()
    return weight.detach().numpy(), bias.detach().numpy(), history


def walk_forward(fp, benchmark: np.ndarray, out_dir: Path, job=None, version: str = VERSION) -> dict[str, object]:
    """Train per test year, write the weights of every test decision and the yearly comparison."""
    timeline = Timeline(fp, benchmark, version)
    years = sorted({day.year for day in timeline.days if day.year >= FIRST_TEST_YEAR})
    weights: dict[str, list[float]] = {}
    yearly = {}
    equal = np.full((1, len(timeline.names)), 1 / len(timeline.names))
    for number, year in enumerate(years):
        first_session = next(day for day in fp.sessions if day.year == year)
        train_at = [index for index, position in enumerate(timeline.positions)
                    if fp.sessions[min(position + STEP, len(fp.sessions) - 1)] < first_session]
        test_at = [index for index, day in enumerate(timeline.days) if day.year == year]
        if len(train_at) < 12 or not test_at:
            continue
        outputs = []
        curves = []
        for seed in SEEDS:
            weight, bias, curve = train(timeline, train_at, seed)
            outputs.append(timeline.states[test_at] @ weight + bias)
            curves.append(curve)
        chosen = _softmax(np.mean(outputs, axis=0))                             # decision × factor
        for index, row in zip(test_at, chosen):
            weights[timeline.days[index].isoformat()] = [round(float(value), 6) for value in row]
        robot = timeline.rewards(chosen[:, None, :], test_at)[:, 0]
        alike = timeline.rewards(np.repeat(equal[None, :, :], len(test_at), axis=0), test_at)[:, 0]
        yearly[str(year)] = {"decisions": len(test_at), "train_decisions": len(train_at),
                             "robot_excess": round(float(robot.sum()), 4), "equal_excess": round(float(alike.sum()), 4),
                             "training_reward_first": round(float(np.mean([curve[0] for curve in curves])), 5),
                             "training_reward_last": round(float(np.mean([np.mean(curve[-20:]) for curve in curves])), 5),
                             "heaviest": [[name, round(float(share), 4)] for name, share in
                                          sorted(zip(timeline.names, chosen.mean(axis=0)), key=lambda item: -item[1])[:3]]}
        if job:
            job.update(done=number + 1, current=f"{year}：機器人 {robot.sum():+.3f}、平均權重 {alike.sum():+.3f}")
    meta = {"version": version, "factors": timeline.names, "weights": weights, "yearly": yearly,
            "config": {"step": STEP, "top": TOP, "cost_swap": COST_SWAP, "sigma": SIGMA, "penalty": PENALTY,
                       "epochs": EPOCHS, "rollouts": ROLLOUTS, "learning_rate": LEARNING_RATE, "seeds": list(SEEDS)},
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds")}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def _rewards_of(pairs: list, logits: np.ndarray) -> np.ndarray:
    """decision × rollout rewards of ``logits`` (decision × rollout × factor) over (timeline, decisions) pairs."""
    output, start = [], 0
    for timeline, decisions in pairs:
        part = logits[start:start + len(decisions)]
        output.append(timeline.rewards(_softmax(part), decisions))
        start += len(decisions)
    return np.concatenate(output) if output else np.zeros((0, logits.shape[1]))


def train_held_out(train_pairs: list, held_pairs: list, seed: int, epochs: int | None = None,
                   penalty: float | None = None) -> dict[str, object]:
    """The linear policy by REINFORCE on ``train_pairs``, kept at the epoch whose held-out reward (no exploration) is
    best; returns the kept weights and the curves (every CHECK_EVERY epochs)."""
    import torch

    epochs = epochs or EPOCHS
    penalty = BLOCK_PENALTY if penalty is None else penalty

    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    states = torch.from_numpy(np.concatenate([timeline.states[decisions] for timeline, decisions in train_pairs]))
    held_states = np.concatenate([timeline.states[decisions] for timeline, decisions in held_pairs])
    count = len(train_pairs[0][0].names)
    weight = torch.zeros((states.shape[1], count), requires_grad=True)
    bias = torch.zeros(count, requires_grad=True)
    optimiser = torch.optim.Adam([weight, bias], lr=LEARNING_RATE)
    curve_train, curve_held = [], []
    best = (-np.inf, 0, weight.detach().numpy().copy(), bias.detach().numpy().copy())
    for epoch in range(epochs):
        mean = states @ weight + bias
        noise = rng.normal(0, SIGMA, (states.shape[0], ROLLOUTS, count)).astype(np.float32)
        actions = mean.detach().numpy()[:, None, :] + noise
        rewards = _rewards_of(train_pairs, actions)
        advantage = torch.from_numpy(((rewards - rewards.mean(axis=1, keepdims=True))
                                      / (rewards.std(axis=1, keepdims=True) + 1e-8)).astype(np.float32))
        log_prob = -(((torch.from_numpy(actions) - mean[:, None, :]) ** 2).sum(-1)) / (2 * SIGMA ** 2)
        loss = -(advantage * log_prob).mean() + penalty * (mean ** 2).mean()
        optimiser.zero_grad()
        loss.backward()
        optimiser.step()
        if epoch % CHECK_EVERY == CHECK_EVERY - 1:
            w, b = weight.detach().numpy(), bias.detach().numpy()
            held = float(_rewards_of(held_pairs, (held_states @ w + b)[:, None, :]).mean())
            curve_train.append(round(float(rewards.mean()), 5))
            curve_held.append(round(held, 5))
            if held > best[0]:
                best = (held, epoch + 1, w.copy(), b.copy())
    return {"weight": best[2], "bias": best[3], "held_reward": best[0], "best_epoch": best[1],
            "train": curve_train, "held": curve_held}


def walk_forward_blocks(fp, benchmark: np.ndarray, out_dir: Path, job=None) -> dict[str, object]:
    """The robot 3.0: per test year, trained on the decisions (all four offsets) realised before the year, the last
    fifth of them held out; the test decisions are offset 0's, as the rule decides."""
    timelines = [Timeline(fp, benchmark, BLOCK_VERSION, offset) for offset in PHASES]
    main = timelines[0]
    names = main.names
    equal = np.full(len(names), 1 / len(names))
    years = sorted({day.year for day in main.days if day.year >= FIRST_TEST_YEAR})
    weights: dict[str, list[float]] = {}
    yearly = {}
    for number, year in enumerate(years):
        first_session = next(day for day in fp.sessions if day.year == year)
        before = [[index for index, position in enumerate(timeline.positions)
                   if fp.sessions[min(position + STEP, len(fp.sessions) - 1)] < first_session] for timeline in timelines]
        dated = sorted(timeline.days[index] for timeline, indexes in zip(timelines, before) for index in indexes)
        test_at = [index for index, day in enumerate(main.days) if day.year == year]
        if len(dated) < 40 or not test_at:
            continue
        cut = dated[int(len(dated) * (1 - HELD_OUT))]
        train_pairs = [(timeline, [index for index in indexes if timeline.days[index] < cut])
                       for timeline, indexes in zip(timelines, before)]
        held_pairs = [(timeline, [index for index in indexes if timeline.days[index] >= cut])
                      for timeline, indexes in zip(timelines, before)]
        train_pairs = [pair for pair in train_pairs if pair[1]]
        held_pairs = [pair for pair in held_pairs if pair[1]]
        runs = [train_held_out(train_pairs, held_pairs, seed) for seed in BLOCK_SEEDS]
        held_states = np.concatenate([timeline.states[decisions] for timeline, decisions in held_pairs])
        held_equal = float(_rewards_of(held_pairs, np.repeat(np.zeros((1, 1, len(names))), len(held_states), axis=0)).mean())
        logits = np.mean([main.states[test_at] @ run["weight"] + run["bias"] for run in runs], axis=0)
        held_robot = float(np.mean([run["held_reward"] for run in runs]))
        abstain = held_robot <= held_equal
        chosen = np.repeat(equal[None, :], len(test_at), axis=0) if abstain else _softmax(logits)
        for index, row in zip(test_at, chosen):
            weights[main.days[index].isoformat()] = [round(float(value), 6) for value in row]
        robot = main.rewards(chosen[:, None, :], test_at)[:, 0]
        alike = main.rewards(np.repeat(equal[None, None, :], len(test_at), axis=0), test_at)[:, 0]
        yearly[str(year)] = {
            "decisions": len(test_at), "train_decisions": sum(len(pair[1]) for pair in train_pairs),
            "held_decisions": sum(len(pair[1]) for pair in held_pairs), "held_from": cut.isoformat(),
            "held_robot": round(held_robot, 5), "held_equal": round(held_equal, 5), "abstained": bool(abstain),
            "best_epochs": [run["best_epoch"] for run in runs],
            "curve_train": [round(float(value), 5) for value in np.mean([run["train"] for run in runs], axis=0)],
            "curve_held": [round(float(value), 5) for value in np.mean([run["held"] for run in runs], axis=0)],
            "robot_excess": round(float(robot.sum()), 4), "equal_excess": round(float(alike.sum()), 4),
            "heaviest": [[name, round(float(share), 4)] for name, share in
                         sorted(zip(names, chosen.mean(axis=0)), key=lambda item: -item[1])[:3]]}
        if job:
            job.update(done=number + 1, total=len(years),
                       current=f"{year}：{'不押（用平均）' if abstain else '機器人'} {robot.sum():+.3f}、平均 {alike.sum():+.3f}")
    meta = {"version": BLOCK_VERSION, "factors": names, "weights": weights, "yearly": yearly,
            "config": {"step": STEP, "top": TOP, "cost_swap": COST_SWAP, "sigma": SIGMA, "penalty": BLOCK_PENALTY,
                       "epochs": EPOCHS, "rollouts": ROLLOUTS, "learning_rate": LEARNING_RATE, "seeds": list(BLOCK_SEEDS),
                       "phases": list(PHASES), "held_out": HELD_OUT, "check_every": CHECK_EVERY},
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds")}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


def scores(fp, folder: str | Path, kind: str = "robot", version: str = VERSION) -> np.ndarray:
    """sessions × symbols. ``robot``: each session's percentiles weighted by the latest decision's weights (NaN
    before the first test decision or without a trained robot); ``equal``: the version's factors alike."""
    from quant_platform.research.model import _rank, eligibility

    names = factor_names(version)
    eligible = eligibility(fp)
    percentiles = np.stack([_rank(np.where(eligible, fp.matrix(name), np.nan).T)
                            for name in names])                                 # factor × session × symbol
    if kind == "equal":
        with np.errstate(invalid="ignore"):
            return np.nanmean(percentiles, axis=0)
    path = Path(folder) / "meta.json"
    output = np.full((len(fp.sessions), len(fp.symbols)), np.nan, dtype=np.float32)
    if not path.is_file():
        return output
    meta = json.loads(path.read_text(encoding="utf-8"))
    decided = sorted((date.fromisoformat(day), np.array(values)) for day, values in meta["weights"].items())
    if not decided:
        return output
    index = 0
    current = None
    for position, day in enumerate(fp.sessions):
        while index < len(decided) and decided[index][0] <= day:
            current = decided[index][1]
            index += 1
        if current is None:
            continue
        filled = np.nan_to_num(percentiles[:, position, :], nan=0.5)
        output[position] = np.where(np.isfinite(percentiles[:, position, :]).any(axis=0), current @ filled, np.nan)
    return output


def digest(folder: str | Path) -> str:
    """The trained robot as part of a run's input (its meta.json)."""
    import hashlib

    path = Path(folder) / "meta.json"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"


def benchmark_units(data, sessions) -> np.ndarray:
    """0050's value of one unit with its distributions reinvested, on ``sessions`` (carried over gaps)."""
    from quant_platform.research.legacy_challenger import BENCHMARK

    closes = data.closes.get(BENCHMARK, {})
    factors = data.factors.get(BENCHMARK, {})
    units, values = 1.0, []
    for day in sessions:
        units *= factors.get(day, 1.0)
        close = closes.get(day)
        values.append(units * close if close else np.nan)
    return pd.Series(values).ffill().to_numpy()
