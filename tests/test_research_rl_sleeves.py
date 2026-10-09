"""R15 C3 (2026-10-09): the RL allocator across strategy families; 2.0.0 with cash in place of 0050."""

from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import rl_sleeves as rs


def test_one_session_by_hand():
    returns = np.array([[0.10, -0.10, 0.0], [0.10, -0.10, 0.0]])
    bench = np.array([0.0, 0.0])
    weights = np.array([[1.0, 0.0, 0.0], [0.5, 0.5, 0.0]])
    previous = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    reward, value, peak, drawdown = rs.step(returns, weights, previous, np.ones(2), np.ones(2), bench, switch_penalty=0.02)
    # all in the model family, no move: +10%. Half and half from all the third place: 0%, minus 0.5% of the 100% moved
    assert value == pytest.approx([1.10, 0.995]) and peak == pytest.approx([1.10, 1.0])
    assert drawdown == pytest.approx([0.0, -0.005])
    assert reward == pytest.approx([np.log(1.10), np.log(0.995) - 0.5 * 0.005 - 0.02])
    heavier = rs.step(returns, weights, previous, np.ones(2), np.ones(2), bench, switch_penalty=0.02, drawdown_penalty=1.0)
    assert heavier[0][1] == pytest.approx(np.log(0.995) - 1.0 * 0.005 - 0.02)          # 2.0.0 weighs drawdowns twice


def regimes(sessions=2400, length=100, edge=0.003, seed=4, third="0050"):
    """The model family beats 0050 by ``edge`` a day for ``length`` sessions while the trend family loses as
    much, then the other way round: holding either, or both, nets about nothing."""
    rng = np.random.default_rng(seed)
    sign = np.where((np.arange(sessions) // length) % 2 == 0, 1.0, -1.0)
    bench = rng.normal(0.0003, 0.004, sessions)
    model = bench + sign * edge + rng.normal(0, 0.004, sessions)
    trend = bench - sign * edge + rng.normal(0, 0.004, sessions)
    bench[0] = 0.0
    returns = np.column_stack([model, trend, bench if third == "0050" else np.zeros(sessions)])
    returns[0] = 0.0
    days = [date(2014, 1, 1) + timedelta(days=index * 365 // 245) for index in range(sessions)]
    features, names = rs._features(returns, bench, np.full(sessions, 0.5), np.zeros(sessions))
    return rs.Families(days, returns, features, names, bench, third)


def test_features_never_look_ahead():
    families = regimes()
    changed = families.returns.copy()
    changed[1500:] = 0.05
    later, _ = rs._features(changed, families.bench, np.full(len(changed), 0.5), np.zeros(len(changed)))
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
    assert report["years"] and report["mixes"] and set(overall["average"]) == {"model", "trend", "0050"}


def test_version_2_steps_into_cash_when_both_families_crash():
    """2.0.0: the third place is cash; when both families fall together every other stretch, stepping out
    beats every mix that stays in stocks and the simple volatility rule's path is reported beside it."""
    rng = np.random.default_rng(5)
    sessions, length = 2400, 100
    sign = np.where((np.arange(sessions) // length) % 2 == 0, 1.0, -1.0)
    bench = rng.normal(0.0003, 0.004, sessions)
    model = 0.004 * sign + rng.normal(0, 0.004, sessions)            # both rise, then both fall
    trend = 0.004 * sign + rng.normal(0, 0.004, sessions)
    bench[0] = 0.0
    returns = np.column_stack([model, trend, np.zeros(sessions)])
    returns[0] = 0.0
    days = [date(2014, 1, 1) + timedelta(days=index * 365 // 245) for index in range(sessions)]
    features, names = rs._features(returns, bench, np.full(sessions, 0.5), np.zeros(sessions))
    families = rs.Families(days, returns, features, names, bench, "cash")
    small = {"iterations": 40, "envs": 32, "episode": 150, "minibatch": 1024}
    report = rs.walk_forward(families, seeds=(0, 1), first_year=2021, config=small, version="rl-sleeves-2.0.0")
    overall = report["overall"]
    assert report["families"][2] == "現金" and report["drawdown_penalty"] == 1.0 and set(overall["average"]) == {"model", "trend", "cash"}
    assert overall["rl"]["growth"] > overall["halves"]["growth"] + 0.10 and overall["rl"]["max_drawdown"] > overall["halves"]["max_drawdown"]
    assert "vol_scaled" in overall and set(report["acceptance"]) == {"beats_0050", "drawdown_within_5", "beats_simple_rule", "passed"}


def test_the_report_shows_on_the_research_page(tmp_path):
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    tiny = {"iterations": 1, "envs": 4, "episode": 40, "minibatch": 128}
    rs.walk_forward(regimes(sessions=900, third="cash"), tmp_path / "research" / "rl" / "rl-sleeves-2.0.0", seeds=(0,),
                    first_year=2016, config=tiny, version="rl-sleeves-2.0.0")
    client = create_app(build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))).test_client()
    body = client.get("/research?tab=ml").get_data(as_text=True)
    assert "在策略家族間分配" in body and "rl-sleeves-2.0.0" in body and "減碼到現金" in body and "現金" in body


def test_versions():
    assert rs.VERSIONS["rl-sleeves-1.0.0"]["random_start"] is False and rs.VERSIONS["rl-sleeves-1.1.0"]["third"] == "0050"
    assert rs.VERSIONS[rs.LATEST_SLEEVES] == {"random_start": True, "third": "cash", "drawdown_penalty": 1.0}
    families = regimes(sessions=600)
    tiny = {"iterations": 1, "envs": 8, "episode": 40, "minibatch": 128}
    report = rs.walk_forward(families, seeds=(0,), first_year=2016, config=tiny, version="rl-sleeves-1.1.0")
    assert report["version"] == "rl-sleeves-1.1.0" and report["config"]["random_start"] is True
