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
