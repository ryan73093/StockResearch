import math

import numpy as np
import pytest

from quant_platform.research.statistics import (
    block_bootstrap_mean,
    deflated_sharpe,
    expected_max_sharpe,
    probability_of_backtest_overfitting,
    sharpe,
)


def test_expected_max_sharpe_matches_the_published_example():
    # Bailey & López de Prado (2014): ~3.26 for 1,000 trials with unit variance.
    assert expected_max_sharpe(1000, 1.0) == pytest.approx(3.2546, abs=0.01)
    assert expected_max_sharpe(1, 1.0) == 0.0


def test_deflated_sharpe_by_hand_for_a_single_trial():
    returns = np.array([0.02, -0.01] * 30 + [0.02])  # mean 0.00508, symmetric-ish
    result = deflated_sharpe(returns, n_trials=1, sharpe_variance=0.0)

    ratio = sharpe(returns)
    denominator = 1 - result["skewness"] * ratio + (result["kurtosis"] - 1) / 4 * ratio**2
    expected = 0.5 * (1 + math.erf(ratio * math.sqrt(len(returns) - 1) / math.sqrt(denominator) / math.sqrt(2)))
    assert result["deflated_sharpe"] == pytest.approx(expected)
    many = deflated_sharpe(returns, n_trials=200, sharpe_variance=0.05)
    assert many["deflated_sharpe"] < result["deflated_sharpe"]  # more trials, less confidence


def test_pbo_separates_skill_from_noise():
    generator = np.random.default_rng(7)
    noise = generator.normal(0, 0.02, size=(240, 20))
    assert 0.2 < probability_of_backtest_overfitting(noise, blocks=8)["pbo"] < 0.8

    skilled = noise.copy()
    skilled[:, 0] += 0.02
    result = probability_of_backtest_overfitting(skilled, blocks=8)
    assert result["pbo"] < 0.05 and result["combinations"] == math.comb(8, 4)


def test_block_bootstrap_is_deterministic_and_centred():
    flat = block_bootstrap_mean([0.001] * 60, samples=200)
    assert flat["low"] == pytest.approx(0.001) and flat["high"] == pytest.approx(0.001)

    generator = np.random.default_rng(3)
    positive = generator.normal(0.01, 0.02, size=120)
    first = block_bootstrap_mean(positive, samples=500)
    second = block_bootstrap_mean(positive, samples=500)
    assert first == second
    assert first["low"] < first["mean"] < first["high"] and first["share_above_zero"] > 0.95


def _pbo_one_trial_at_a_time(matrix, blocks=16):
    """The PBO as written before 2026-10-29: one sharpe() call per trial and half."""
    import itertools
    import math

    from quant_platform.research.statistics import sharpe

    data = np.asarray(matrix, dtype=float)
    periods, trials = data.shape
    blocks = min(blocks, periods)
    blocks -= blocks % 2
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
    return {"pbo": float((array <= 0).mean()), "combinations": len(logits), "blocks": blocks,
            "median_logit": float(np.median(array))}


def _bootstrap_one_sample_at_a_time(returns, block=6, samples=2000, confidence=0.95, seed=20261001):
    """The block bootstrap as written before 2026-10-29: each sample put together and averaged on its own."""
    import math

    values = np.asarray(returns, dtype=float)
    count = values.size
    block = max(1, min(block, count))
    generator = np.random.default_rng(seed)
    starts_needed = math.ceil(count / block)
    means = np.empty(samples)
    for index in range(samples):
        starts = generator.integers(0, count - block + 1, size=starts_needed)
        means[index] = np.concatenate([values[start:start + block] for start in starts])[:count].mean()
    tail = (1 - confidence) / 2
    return {"mean": float(values.mean()), "low": float(np.quantile(means, tail)),
            "high": float(np.quantile(means, 1 - tail)), "share_above_zero": float((means > 0).mean()),
            "block": block, "samples": samples, "seed": seed}


def test_the_sharpe_ratios_of_all_trials_at_once_are_those_of_one_at_a_time_bit_for_bit():
    """2026-10-29 (S9-W39; 使用者：不要為了省效能最後又給我錯的資料): row by row the same numbers as sharpe(), for lengths
    under 8, from 8 to 128 and over 128 (numpy's three ways of summing), a flat row, and a Fortran-ordered slice made
    contiguous first."""
    from quant_platform.research.statistics import row_sharpes, sharpe

    generator = np.random.default_rng(29)
    for length in (2, 3, 7, 8, 9, 31, 32, 63, 64, 127, 128, 129, 200, 300):
        data = generator.normal(0.004, 0.05, size=(length, 40))
        data[:, 3] = 0.01                                              # no deviation: 0
        index = np.sort(generator.choice(length, size=max(2, length // 2), replace=False))
        rows = np.ascontiguousarray(np.ascontiguousarray(data.T)[:, index])
        expected = np.array([sharpe(data[index, column]) for column in range(data.shape[1])])
        assert np.array_equal(row_sharpes(rows), expected), length
        assert np.array_equal(row_sharpes(np.ascontiguousarray(data.T)),
                              np.array([sharpe(data[:, column]) for column in range(data.shape[1])])), length


def test_the_pbo_and_the_bootstrap_are_what_they_were():
    from quant_platform.research.statistics import block_bootstrap_mean, probability_of_backtest_overfitting

    generator = np.random.default_rng(1029)
    for periods, trials in ((63, 25), (135, 12), (40, 7)):
        matrix = generator.normal(0.003, 0.04, size=(periods, trials))
        matrix[:, 2] += 0.01
        assert probability_of_backtest_overfitting(matrix, blocks=16) == _pbo_one_trial_at_a_time(matrix, blocks=16)
    for length, block in ((135, 6), (63, 6), (5, 6), (7, 3), (300, 12), (1, 6)):
        returns = generator.normal(0.002, 0.03, size=length)
        assert block_bootstrap_mean(returns, block=block) == _bootstrap_one_sample_at_a_time(returns, block=block)
