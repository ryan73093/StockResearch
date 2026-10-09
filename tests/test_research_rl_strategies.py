"""2026-10-10: RL 3.0, the account shared among strategies (research/rl_strategies.py)."""

from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import rl_strategies as rs


def test_moving_money_and_cash_by_hand():
    following = np.array([[0.10, -0.10]])
    weights = np.array([[0.5, 0.0, 0.5]])                 # half in the first strategy, half in cash
    previous = np.array([[0.0, 0.5, 0.5]])
    reward, value, peak = rs.step(following, weights, previous, np.ones(1), np.ones(1))
    growth = 0.5 * 0.10 - rs.MOVE_COST * 0.5               # half the account moved
    assert value == pytest.approx([1 + growth]) and peak == pytest.approx([1 + growth])
    assert reward == pytest.approx([np.log1p(growth) - np.log1p(0.0)])     # equal shares earned 0 that day
    assert rs._drift(np.array([0.5, 0.0, 0.5]), np.array([0.10, -0.10])) == pytest.approx([0.55 / 1.05, 0, 0.5 / 1.05])


def regimes(sessions=2600, length=80, seed=3):
    """Two strategies take turns: each earns for `length` sessions while the other loses — their own recent
    returns tell which one is working. Equal shares earn about nothing."""
    rng = np.random.default_rng(seed)
    sign = np.where((np.arange(sessions) // length) % 2 == 0, 1.0, -1.0)
    first = 0.004 * sign + rng.normal(0, 0.004, sessions)
    second = -0.004 * sign + rng.normal(0, 0.004, sessions)
    returns = np.column_stack([first, second])
    returns[0] = 0.0
    days = [date(2015, 6, 1) + timedelta(days=index * 365 // 245) for index in range(sessions)]
    features = rs._features(returns, np.full(sessions, 0.5), np.zeros(sessions))
    return rs.Strategies(days, ["甲", "乙"], [1, 2], returns, rng.normal(0.0003, 0.008, sessions), features)


def test_the_agent_follows_the_strategy_that_is_working(tmp_path):
    small = {"iterations": 40, "envs": 32, "episode": 100, "minibatch": 512, "threads": 1}
    report = rs.walk_forward(regimes(), tmp_path, seeds=(0,), first_year=2019, config=small)
    overall = report["overall"]
    assert overall["rl"]["annual"] > overall["equal"]["annual"] + 0.10
    assert set(report["acceptance"]) == {"beats_equal", "beats_best_single", "beats_0050", "drawdown_within_5", "passed"}
    assert (tmp_path / "report.json").is_file() and "現金" in report["average_shares"]


def test_the_research_page_shows_rl3_and_the_robot(tmp_path):
    import json

    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    small = {"iterations": 2, "envs": 8, "episode": 40, "minibatch": 256, "threads": 1}
    rs.walk_forward(regimes(sessions=1400), tmp_path / "research" / "rl" / rs.VERSION, seeds=(0,), first_year=2019,
                    config=small)
    robot = tmp_path / "research" / "models" / "tech-rl-1.0.0"
    robot.mkdir(parents=True)
    (robot / "meta.json").write_text(json.dumps({"version": "tech-rl-1.0.0", "features": ["trend_200"], "seeds": [0],
                                                 "years": {"2017": {"trained_until": "2016-12-30",
                                                                    "training_reward_first": 0.01,
                                                                    "training_reward_last": 0.05,
                                                                    "excess_per_in_session": 0.0004, "share_in": 0.2,
                                                                    "average_hold": 18.5, "entries": 1234}}}),
                                     encoding="utf-8")
    client = create_app(build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))).test_client()
    body = client.get("/research?tab=ml").get_data(as_text=True)
    assert "強化學習 3.0：在 T0 候選之間分配" in body and "平均分配（每月重設）" in body
    assert "技術分析 RL 機器人" in body and "+0.040%" in body and "tech-rl-1.0.0</strong>" not in body
