"""2026-11-13 (problem register P7): the factors and models the forward accounts read, watched every week —
research/decay.py. The verdict was written before the first run (the module text)."""
import json
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from quant_platform.research import decay


def _series(early_mean, late_mean, early=300, late=26, swing=0.1):
    """Weekly correlations swinging ±swing around a mean: the earlier weeks, then the recent ones."""
    return ([early_mean + swing * (-1) ** week for week in range(early)]
            + [late_mean + swing * (-1) ** week for week in range(late)])


@pytest.mark.parametrize("early, late, verdict", [
    (0.05, 0.05, "正常"),
    (0.05, 0.02, "正常"),          # lower, within two standard errors
    (0.05, -0.04, "變弱"),         # 0.09 below: 2.3 standard errors
    (0.05, -0.10, "反過來了"),      # against the direction by 2.5 standard errors
    (-0.05, 0.10, "反過來了"),      # a factor working reversed, now the other way
    (-0.05, -0.05, "正常"),
    (0.005, -0.10, "長期就弱"),     # never had an edge: no judgment of a change
])
def test_the_verdict_written_before_the_first_run(early, late, verdict):
    found = decay.judge(_series(early, late), [0.001] * 326)
    assert found["verdict"] == verdict
    assert found["direction"] == (1 if early >= 0 else -1) and found["weeks"] == 326


def test_overlapping_weeks_count_as_fewer():
    """Four weekly rankings share their 20 sessions: 26 weeks are 6.5 independent ones."""
    assert decay.independent(26) == pytest.approx(6.5) and decay.independent(2) == 1.0
    found = decay.judge(_series(0.05, -0.04), [0.002] * 300 + [-0.001] * 26)
    error = 0.1 * np.sqrt(26 / 25) / np.sqrt(6.5)
    assert found["below"] == pytest.approx(0.09 / error, abs=0.02)
    assert found["long_top_year"] == pytest.approx(0.002 * 252 / 20) and found["recent_top_year"] == pytest.approx(-0.001 * 252 / 20)


def test_too_few_weeks_are_not_judged():
    assert decay.judge([0.1] * 60, [0.0] * 60) == {"verdict": "資料不夠", "weeks": 60}


def test_the_factors_are_those_the_live_accounts_read(tmp_path):
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    tracked = [
        {"rule_hash": "a", "kind": "daily", "rule": {"factors": {"high_52w": 1.0, "ml_gbm_k": 1.0}}},
        {"rule_hash": "b", "kind": "daily", "rule": {"factors": {"high_52w": 1.0}, "bear_factors": {"low_vol": 1.0}}},
        {"rule_hash": "c", "kind": "blend", "rule": {"sleeves": [{"rule": {"factors": {"ml_deep2_top": 1.0}}, "share": 0.5},
                                                                {"rule": {"factors": {"high_52w": 1.0}}, "share": 0.5}]}},
        {"rule_hash": "d", "kind": "daily", "stopped": "2026-11-02", "rule": {"factors": {"momentum_3": 1.0}}},
    ]
    (folder / "tracked.json").write_text(json.dumps(tracked), encoding="utf-8")
    assert decay.used(tmp_path) == {"high_52w": 3, "ml_gbm_k": 1, "low_vol": 1, "ml_deep2_top": 1}
    assert decay.used(tmp_path / "none") == {}


def _panel(sessions, symbols=80, perfect=("good",), reversed_=("bad",)):
    """Prices on a random walk; ``good`` ranks by the next 20 sessions' return, ``bad`` by its reverse."""
    rng = np.random.default_rng(7)
    filled = 100 * np.cumprod(1 + rng.normal(0, 0.02, size=(symbols, len(sessions))), axis=1)
    ahead = np.full_like(filled, np.nan)
    ahead[:, :-20] = filled[:, 20:] / filled[:, :-20] - 1
    matrices = {name: ahead for name in perfect} | {name: -ahead for name in reversed_}

    def matrix(name):
        if name not in matrices:
            raise KeyError(name)
        return matrices[name]

    return SimpleNamespace(sessions=sessions, panel=SimpleNamespace(filled=filled), matrix=matrix,
                           eligible=lambda rule, position: np.ones(symbols, dtype=bool))


def test_each_week_ranks_against_the_next_20_sessions():
    sessions = [date(2015, 6, 1) + timedelta(days=day) for day in range(120)]
    fp = _panel(sessions)
    days, ics, tops = decay.weekly(fp, "good", [0, 5, 10, 99])
    assert days == [sessions[0].isoformat(), sessions[5].isoformat(), sessions[10].isoformat(), sessions[99].isoformat()]
    assert ics == pytest.approx([1.0] * 4) and all(top > 0 for top in tops)
    assert decay.weekly(fp, "bad", [0])[1] == pytest.approx([-1.0])


def test_a_weekly_measure_judges_every_used_factor_and_writes_it_down(tmp_path, monkeypatch):
    from quant_platform.research import daily

    sessions = []
    day = date(2015, 5, 1)
    while len(sessions) < 520:                 # about two years of sessions: 98 weekly rankings after 2015-06
        if day.weekday() < 5:
            sessions.append(day)
        day += timedelta(days=1)
    fp = _panel(sessions)
    monkeypatch.setattr(daily, "load", lambda history: (None, fp))
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    tracked = [{"rule_hash": "a", "kind": "daily", "rule": {"factors": {"good": 1.0, "bad": -1.0, "broken": 1.0}}}]
    (folder / "tracked.json").write_text(json.dumps(tracked), encoding="utf-8")
    found = decay.measure(tmp_path, tmp_path)
    assert found["ranked_through"] == sessions[-21].isoformat() and found["returns_through"] == sessions[-1].isoformat()
    good, bad, broken = (found["factors"][name] for name in ("good", "bad", "broken"))
    assert good["weeks"] >= 90 and good["last"] == sessions[-21].isoformat() and good["verdict"] == "正常"
    assert bad["direction"] == -1 and bad["verdict"] == "正常"
    assert broken["verdict"] == "沒辦法算" and "KeyError" in broken["error"]
    assert decay.latest(tmp_path)["at"] == found["at"]
    assert [row["factor"] for row in decay.rows(found)][0] == "broken" and decay.flagged(found) == []


def test_the_weekly_job_runs_on_saturday_in_a_child(tmp_path):
    from apscheduler.schedulers.background import BackgroundScheduler

    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.scheduler import child, runner

    assert "measure_decay" in child.HEAVY
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    scheduler = BackgroundScheduler(timezone=container.settings.scheduler_timezone)
    runner._add_maintenance_jobs(scheduler, container)
    job = scheduler.get_job("decay_watch")
    assert job is not None and getattr(job.func, "in_child", False) and job.func.__name__ == "measure_decay"
    fields = {field.name: str(field) for field in job.trigger.fields}
    assert (fields["day_of_week"], fields["hour"], fields["minute"]) == ("sat", "7", "30")


def test_the_factors_tab_lists_the_watch_flagged_first(tmp_path):
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    client = create_app(container).test_client()
    assert "還沒有結果；每週六 07:30 算一次" in client.get("/research?tab=factors").get_data(as_text=True)
    folder = tmp_path / "research" / "decay"
    folder.mkdir(parents=True)
    factors = {
        "high_52w": {"label": "接近 52 週高點", "accounts": 793, "verdict": "正常", "direction": 1, "long_ic": 0.031,
                     "long_t": 4.2, "recent_ic": 0.025, "recent_t": 1.1, "below": 0.3, "long_top_year": 0.052,
                     "recent_top_year": 0.041},
        "ml_gbm_k": {"label": "模型K：機器學習", "accounts": 120, "verdict": "變弱", "direction": 1, "long_ic": 0.045,
                     "long_t": 6.0, "recent_ic": -0.012, "recent_t": -0.6, "below": 2.7, "long_top_year": 0.08,
                     "recent_top_year": -0.03},
    }
    (folder / "decay-20261114-073000.json").write_text(json.dumps(
        {"at": "2026-11-14T07:30:00+08:00", "ranked_through": "2026-10-16", "recent_weeks": 26, "factors": factors},
        ensure_ascii=False), encoding="utf-8")
    page = client.get("/research?tab=factors").get_data(as_text=True)
    assert "最近還準不準" in page and "排名到 2026-10-16" in page and "變弱或反過來了 1 個" in page
    assert page.index("模型K：機器學習") < page.index("接近 52 週高點") and "+0.045" in page and "2.7" in page
    assert "−3.0%" in page or "-3.0%" in page
