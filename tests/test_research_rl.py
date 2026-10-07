"""R15 stage C1 (2026-10-06): the RL exposure overlay."""

from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import rl


def test_one_session_by_hand():
    reward, value, peak, drawdown = rl.step(np.array([0.10, -0.10]), np.array([0.0, 0.0]), np.array([1.0, 0.5]),
                                            np.array([1.0, 1.0]), np.array([1.0, 1.0]), np.array([1.0, 1.0]))
    # all in stocks: +10%, no switch; half: −5% minus 0.5% × 0.5 moved = −5.25%, its drawdown deepens by 5.25%
    assert value == pytest.approx([1.10, 0.9475]) and peak == pytest.approx([1.10, 1.0])
    assert drawdown == pytest.approx([0.0, -0.0525])
    assert reward == pytest.approx([np.log(1.10), np.log(0.9475) - 0.5 * 0.0525])


def regimes(sessions=2400, length=100, edge=0.003, seed=3):
    """The rule's stocks beat 0050 by `edge` a day for `length` sessions, then lose by as much."""
    rng = np.random.default_rng(seed)
    sign = np.where((np.arange(sessions) // length) % 2 == 0, 1.0, -1.0)
    bench = rng.normal(0.0003, 0.004, sessions)
    stock = bench + sign * edge + rng.normal(0, 0.004, sessions)
    stock[0] = bench[0] = 0.0
    days = [date(2014, 1, 1) + timedelta(days=index * 365 // 245) for index in range(sessions)]
    features = rl._features(stock, bench, np.full(sessions, 0.5), np.zeros(sessions))
    return rl.Overlay(days, stock, bench, features)


def test_features_never_look_ahead():
    overlay = regimes()
    changed = overlay.stock.copy()
    changed[1500:] = 0.05                                       # a different future
    later = rl._features(changed, overlay.bench, np.full(len(changed), 0.5), np.zeros(len(changed)))
    assert np.allclose(np.nan_to_num(later[:1500]), np.nan_to_num(overlay.features[:1500]))


def test_the_agent_learns_to_leave_a_losing_rule_and_beats_holding_it_out_of_sample():
    overlay = regimes()
    small = {"iterations": 40, "envs": 32, "episode": 150, "minibatch": 1024}
    report = rl.walk_forward(overlay, seeds=(0, 1), first_year=2021, config=small)
    overall = report["overall"]
    # holding the rule nets about nothing (good and bad stretches cancel); switching to 0050 in the bad ones wins
    assert overall["rl"]["growth"] > overall["rule"]["growth"] + 0.10 and overall["rl"]["growth"] > overall["0050"]["growth"]
    assert 0.2 < overall["average_share"] < 0.9 and report["years"] and report["shares"]


def test_the_switch_penalty_comes_from_the_reward_not_the_account():
    plain = rl.step(np.array([0.0]), np.array([0.0]), np.array([0.0]), np.array([1.0]), np.array([1.0]), np.array([1.0]))
    penalised = rl.step(np.array([0.0]), np.array([0.0]), np.array([0.0]), np.array([1.0]), np.array([1.0]),
                        np.array([1.0]), switch_penalty=0.02)
    assert penalised[1] == pytest.approx(plain[1])                       # the account pays 0.5% either way
    assert penalised[0] == pytest.approx(plain[0] - 0.02)
    assert rl.VERSIONS[rl.LATEST_RL]["switch_penalty"] == 0.02
