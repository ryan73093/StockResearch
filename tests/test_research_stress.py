"""2026-10-24 (使用者：測這些策略在每一次大空頭撐不撐得過，2008 年的金融海嘯撐得過嗎): research/stress.py."""
from datetime import date, timedelta

import numpy as np

from quant_platform.research import stress


def test_a_bear_is_a_fall_of_a_fifth_and_ends_back_at_its_high():
    units = np.array([1.0, 1.2, 1.1, 0.9, 0.95, 1.25, 1.3, 1.2, 1.05, 1.0])
    found = stress.bears([date(2008, 1, 1) + timedelta(days=i) for i in range(10)], units, depth=0.2)
    assert len(found) == 2
    assert found[0]["high"] == 1 and found[0]["low"] == 3 and found[0]["back"] == 5
    assert abs(found[0]["fall"] - (0.9 / 1.2 - 1)) < 1e-9
    assert found[1]["high"] == 6 and found[1]["back"] is None                  # 1.3 → 1.0 and never back


def test_a_strategy_is_judged_over_0050s_fall_and_back_from_its_own_low():
    days = [date(2007, 10, 1) + timedelta(days=i) for i in range(8)]
    base = np.array([1.0, 0.9, 0.7, 0.6, 0.8, 1.0, 1.1, 1.2])
    mine = np.array([1.0, 1.05, 0.6, 0.5, 0.7, 0.9, 1.0, 1.1])         # higher the day after the high: not "back"
    falls = stress.bears(days, base, depth=0.2)
    row = stress.judge(days, mine, base, falls)[0]
    assert row["fall"] == -0.5 and row["base_fall"] == -0.4 and abs(row["gap"] + 0.1) < 1e-9
    assert row["back"] == days[6].isoformat() and row["base_back"] == days[5].isoformat()
    held = stress.verdict([row])
    assert held["held_2008"] is False                      # back a day after 0050 (and exactly 10 points worse)
    row["back"], row["days"] = row["base_back"], row["base_days"]
    assert stress.verdict([row])["held_2008"] is True and stress.verdict([row])["worse_bears"] == 0


def test_the_window_shows_each_strategy_through_2008(tmp_path):
    import json

    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    folder = tmp_path / "research" / "stress"
    folder.mkdir(parents=True)
    bear = {"high": "2007-10-29", "low": "2008-11-20", "base_fall": -0.55, "fall": -0.40, "worst": -0.42, "gap": 0.15,
            "base_back": "2011-01-28", "back": "2010-03-01", "base_days": 1187, "days": 854}
    row = {"trial": 6490, "spec_hash": "h", "name": "深度學習", "why": "取捨前緣", "tier": "T0 候選", "final_value": 1,
           "final": 5_000_000, "base_final": 2_473_000, "contributed": 2_500_000, "max_drawdown": -0.45,
           "base_max_drawdown": -0.55, "bears": [bear], "held_2008": True, "worse_bears": 0}
    account = {"bears": [{"high": "2007-10-29", "low": "2008-11-20", "fall": -0.55, "back": "2011-01-28"}],
               "base_final": 2_473_000, "rows": [row, {"trial": 1, "spec_hash": "x", "name": "壞", "why": "w", "error": "boom"}]}
    (folder / "stress-20261024-180000.json").write_text(json.dumps({
        "generated_at": "2026-10-24T18:00:00", "bear": 0.2, "accounts": {"from_2007": account, "from_2007_top": account}}),
        encoding="utf-8")
    app = create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}")))
    body = app.test_client().get("/research/stress").get_data(as_text=True)
    assert "撐過" in body and "-40%" in body and "少跌 15.0 點" in body and "2010-03" in body and "沒跑成：boom" in body
