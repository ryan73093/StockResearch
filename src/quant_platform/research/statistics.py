"""Significance tests for strategies selected from many trials (S4-W03).

Inputs are monthly active returns: the strategy's unit-value return minus the
DCA benchmark's under the same cash flows.

- Deflated Sharpe Ratio (Bailey & López de Prado, 2014): the probability that
  the observed Sharpe ratio beats the best Sharpe ratio expected from
  ``n_trials`` strategies with no skill, adjusted for skewness, kurtosis and
  sample length.
- Probability of Backtest Overfitting via CSCV (Bailey, Borwein, López de
  Prado & Zhu, 2017): how often the in-sample best configuration ranks below
  the median out of sample.
- Moving-block bootstrap confidence interval of the mean active return, which
  keeps the autocorrelation of monthly returns.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
from scipy.stats import kurtosis, norm, skew

EULER_GAMMA = 0.5772156649015329


def sharpe(returns: list[float] | np.ndarray) -> float:
    values = np.asarray(returns, dtype=float)
    if values.size < 2:
        return 0.0
    deviation = values.std(ddof=1)
    return float(values.mean() / deviation) if deviation > 0 else 0.0


def expected_max_sharpe(n_trials: int, sharpe_variance: float) -> float:
    """SR0: expected maximum Sharpe ratio of ``n_trials`` skill-less strategies."""
    if n_trials <= 1 or sharpe_variance <= 0:
        return 0.0
    return math.sqrt(sharpe_variance) * (
        (1 - EULER_GAMMA) * norm.ppf(1 - 1 / n_trials)
        + EULER_GAMMA * norm.ppf(1 - 1 / (n_trials * math.e))
    )


def deflated_sharpe(
    returns: list[float] | np.ndarray, n_trials: int, sharpe_variance: float
) -> dict[str, float]:
    values = np.asarray(returns, dtype=float)
    observations = values.size
    ratio = sharpe(values)
    benchmark = expected_max_sharpe(n_trials, sharpe_variance)
    gamma3 = float(skew(values)) if observations > 2 else 0.0
    gamma4 = float(kurtosis(values, fisher=False)) if observations > 3 else 3.0
    denominator = 1 - gamma3 * ratio + (gamma4 - 1) / 4 * ratio**2
    if observations < 2 or denominator <= 0:
        probability = float("nan")
    else:
        probability = float(norm.cdf((ratio - benchmark) * math.sqrt(observations - 1) / math.sqrt(denominator)))
    return {
        "sharpe": ratio,
        "expected_max_sharpe": benchmark,
        "deflated_sharpe": probability,
        "observations": observations,
        "trials": n_trials,
        "skewness": gamma3,
        "kurtosis": gamma4,
    }


def probability_of_backtest_overfitting(matrix: np.ndarray, blocks: int = 16) -> dict[str, object]:
    """CSCV on a T × N matrix of per-period returns (rows = periods, columns = trials)."""
    data = np.asarray(matrix, dtype=float)
    periods, trials = data.shape
    if trials < 2:
        raise ValueError("PBO 需要至少 2 個試驗")
    blocks = min(blocks, periods)
    blocks -= blocks % 2
    if blocks < 2:
        raise ValueError("期間太短，無法切分")
    edges = np.linspace(0, periods, blocks + 1).astype(int)
    parts = [np.arange(edges[index], edges[index + 1]) for index in range(blocks)]
    logits = []
    for chosen in itertools.combinations(range(blocks), blocks // 2):
        inside = np.concatenate([parts[index] for index in chosen])
        outside = np.concatenate([parts[index] for index in range(blocks) if index not in chosen])
        in_scores = np.array([sharpe(data[inside, column]) for column in range(trials)])
        out_scores = np.array([sharpe(data[outside, column]) for column in range(trials)])
        best = int(np.argmax(in_scores))
        rank = float((out_scores < out_scores[best]).sum() + 1) / (trials + 1)
        logits.append(math.log(rank / (1 - rank)))
    array = np.array(logits)
    return {
        "pbo": float((array <= 0).mean()),
        "combinations": len(logits),
        "blocks": blocks,
        "median_logit": float(np.median(array)),
    }


def block_bootstrap_mean(
    returns: list[float] | np.ndarray,
    block: int = 6,
    samples: int = 2000,
    confidence: float = 0.95,
    seed: int = 20261001,
) -> dict[str, float]:
    values = np.asarray(returns, dtype=float)
    count = values.size
    if count == 0:
        raise ValueError("沒有報酬序列")
    block = max(1, min(block, count))
    generator = np.random.default_rng(seed)
    starts_needed = math.ceil(count / block)
    means = np.empty(samples)
    for index in range(samples):
        starts = generator.integers(0, count - block + 1, size=starts_needed)
        sample = np.concatenate([values[start:start + block] for start in starts])[:count]
        means[index] = sample.mean()
    tail = (1 - confidence) / 2
    return {
        "mean": float(values.mean()),
        "low": float(np.quantile(means, tail)),
        "high": float(np.quantile(means, 1 - tail)),
        "share_above_zero": float((means > 0).mean()),
        "block": block,
        "samples": samples,
        "seed": seed,
    }
