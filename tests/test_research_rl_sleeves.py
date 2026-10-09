"""R15 C3 (2026-10-09): the RL allocator across strategy families."""

from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import rl_sleeves as rs


def test_one_session_by_hand():
    returns = np.array([[0.10, -0.10, 0.0], [0.10, -0.10, 0.0]])
    weights = np.array([[1.0, 0.0, 0.0], [0.5, 0.5, 0.0]])
    previous = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    reward, value, peak, drawdown = rs.step(returns, weights, previous, np.ones(2), np.ones(2), switch_penalty=0.02)
    # all in the model family, no move: +10%. Half and half from all 0050: 0%, minus 0.5% of the 100% moved
    assert value == pytest.approx([1.10, 0.995]) and peak == pytest.approx([1.10, 1.0])
    assert drawdown == pytest.approx([0.0, -0.005])
    assert reward == pytest.approx([np.log(1.10), np.log(0.995) - 0.5 * 0.005 - 0.02])


def regimes(sessions=2400, length=100, edge=0.003, seed=4):
    """The model family beats 0050 by ``edge`` a day for ``length`` sessions while the trend family loses as
    much, then the other way round: holding either, or both, nets about nothing."""
    rng = np.random.default_rng(seed)
    sign = np.where((np.arange(sessions) // length) % 2 == 0, 1.0, -1.0)
    bench = rng.normal(0.0003, 0.004, sessions)
    model = bench + sign * edge + rng.normal(0, 0.004, sessions)
    trend = bench - sign * edge + rng.normal(0, 0.004, sessions)
    returns = np.column_stack([model, trend, bench])
    returns[0] = 0.0
    days = [date(2014, 1, 1) + timedelta(days=index * 365 // 245) for index in range(sessions)]
    features, names = rs._features(returns, np.full(sessions, 0.5), np.zeros(sessions))
    return rs.Families(days, returns, features, names)


def test_features_never_look_ahead():
    families = regimes()
    changed = families.returns.copy()
    changed[1500:] = 0.05
    later, _ = rs._features(changed, np.full(len(changed), 0.5), np.zeros(len(changed)))
    assert np.allclose(np.nan_to_num(later[:1500]), np.nan_to_num(families.features[:1500]))


def test_hindsight_picks_the_best_fixed_mix():
    families = regimes()
    families.returns[:, 0] += 0.001                    # the model family now wins on average
    assert rs.MENU[rs.best_fixed(families, 300, 1200)] == (1.0, 0.0, 0.0)


def test_the_agent_follows_the_family_that_is_working_and_beats_every_fixed_mix():
    families = regimes()
    small = {"iterations": 40, "envs": 32, "episode": 150, "minibatch": 1024}
    report = rs.walk_forward(families, seeds=(0, 1), first_year=2021, config=small)
    overall = report["overall"]
    assert overall["rl"]["growth"] > overall["best_fixed_result"]["growth"] + 0.10
    assert overall["rl"]["growth"] > max(overall["model"]["growth"], overall["trend"]["growth"], overall["thirds"]["growth"])
    assert report["years"] and report["mixes"] and set(overall["average"]) == set(rs.FAMILIES)


def test_the_report_shows_on_the_research_page(tmp_path):
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    tiny = {"iterations": 1, "envs": 4, "episode": 40, "minibatch": 128}
    rs.walk_forward(regimes(sessions=900), tmp_path / "research" / "rl" / rs.RL_SLEEVES_VERSION, seeds=(0,),
                    first_year=2016, config=tiny)
    client = create_app(build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))).test_client()
    body = client.get("/research?tab=ml").get_data(as_text=True)
    assert "在策略家族間分配" in body and "訓練期最好的固定組合" in body and rs.RL_SLEEVES_VERSION in body


def test_version_1_1_starts_training_episodes_anywhere():
    assert rs.VERSIONS[rs.LATEST_SLEEVES] == {"random_start": True}
    assert rs.VERSIONS["rl-sleeves-1.0.0"] == {"random_start": False}
    families = regimes(sessions=600)
    tiny = {"iterations": 1, "envs": 8, "episode": 40, "minibatch": 128}
    report = rs.walk_forward(families, seeds=(0,), first_year=2016, config=tiny, version="rl-sleeves-1.1.0")
    assert report["version"] == "rl-sleeves-1.1.0" and report["config"]["random_start"] is True
