"""2026-10-16 (使用者：策略是我的角色，每次優化之後我要知道成長了多少): strategy roles and their growth."""
from types import SimpleNamespace

from quant_platform.research import roles


def metrics(final, engine="daily-1.5.0"):
    return {"engine": engine, "final_value": final, "benchmark_final_value": 9_800_000.0, "xirr": 0.3,
            "benchmark_xirr": 0.25, "full_period_excess": 1.0, "since_2020_excess": 0.5, "max_drawdown": -0.3,
            "benchmark_max_drawdown": -0.34,
            "windows": {"1y": {"count": 100, "win_ratio": 0.6}, "3y": {"count": 80, "win_ratio": 0.7, "median_excess": 0.1}}}


def entry(spec, role, trial, created, run, final, engine="daily-1.5.0"):
    record = SimpleNamespace(spec_hash=spec, trial_id=trial, spec_name=f"#{trial}", created_at=created, metrics=metrics(final, engine))
    return {"spec": spec, "role": role, "record": record, "created": created, "run": run}


def test_roles_by_method():
    assert roles.role({"factors": {"ml_gbm_ens": 1.0, "trend_200": 1.0}}) == "機器學習"     # the pool's families
    assert roles.role({"factors": {"rl_blocks": 1.0}}) == "RL 機器人"
    assert roles.role({"factors": {"block_equal": 1.0}}) == "RL 機器人"
    assert roles.role({"factors": {"ml_deep": 1.0}}) == "深度學習"
    assert roles.role({"factors": {"ml_gbm_ens": -1.0}}) == "反向（學著賠）"
    assert roles.role({"factors": {"high_52w": 1.0, "eps_growth": 1.0}}) == "多因子"
    assert roles.role({"factors": {"low_sbl_short": 1.0}}) == "籌碼"
    assert roles.role({"factors": {"dividend_raise": 1.0}}) == "公司事件"
    assert roles.role({"kind": "blend", "sleeves": []}) == "組合帳戶"


def test_growth_is_against_the_role_before_its_latest_new_rule(monkeypatch, tmp_path):
    rows = [entry("a", "多因子", 1, "2026-10-10T10:00", "20261010-100000", 12_000_000.0),
            entry("b", "多因子", 2, "2026-10-12T10:00", "20261012-100000", 15_000_000.0),
            entry("a", "多因子", 3, "2026-10-14T10:00", "20261014-100000", 11_000_000.0, "daily-1.5.1"),   # a rerun
            entry("c", "機器學習", 4, "2026-10-12T11:00", "20261012-100000", 9_000_000.0)]
    monkeypatch.setattr(roles, "_entries", lambda _research: rows)
    found = {row["role"]: row for row in roles.summary(tmp_path)["roles"]}
    multi = found["多因子"]
    assert multi["now"]["final"] == 15_000_000.0 and multi["before"]["final"] == 12_000_000.0   # b was the new rule
    assert abs(multi["change"] - 0.25) < 1e-9 and multi["rules"] == 2
    assert multi["first"]["final"] == 12_000_000.0
    assert found["機器學習"]["before"] is None and found["機器學習"]["change"] is None              # its first run
