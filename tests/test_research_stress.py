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
    found = stress.verdict([row])
    assert found["held_2008"] is False and found["held_count"] == 0       # back a day after 0050
    row["back"], row["days"] = row["base_back"], row["base_days"]
    found = stress.verdict([row])
    assert found["held_2008"] is True and found["held_all"] and found["worse_bears"] == 0
    assert stress.kind({"high": 1, "low": 30, "fall": -0.25}) == {"size": "大空頭", "speed": "急殺", "sessions": 29}
    assert stress.kind({"high": 1, "low": 200, "fall": -0.17})["size"] == "修正"


def test_a_way_of_holding_fewer_stocks_is_judged_on_the_median_strategy():
    """research_method §7 空頭減碼檢驗 (written before the run): helps = 2 more scenarios held and half held 2008; keeps
    = 80% of the 2015-06 money and the 2007 money still beating 0050."""
    def variant(held, crisis, final, recent):
        return {"held_count": held, "held_2008": crisis, "final": final, "recent_final": recent}

    rows = [{"variants": {"none": variant(3, False, 100, 50), "taiex_10m": variant(6, True, 90, 45)}},
            {"variants": {"none": variant(4, False, 80, 40), "taiex_10m": variant(6, False, 70, 30)}},
            {"variants": {"none": variant(4, False, 80, 40), "taiex_10m": {"skip": "本來就有"}}}]
    found = stress.overlay_verdict(rows, "taiex_10m", 50)
    assert found["tested"] == 2 and found["more_held"] == 2.5 and found["held_2008"] == 1
    assert found["kept"] == 0.825 and found["verdict"] == "值得做成規則"
    assert stress.overlay_verdict(rows, "taiex_10m", 85)["verdict"] == "空頭有幫助、代價太大"
    rows[0]["variants"]["taiex_10m"]["held_2008"] = False
    assert stress.overlay_verdict(rows, "taiex_10m", 50)["verdict"] == "沒有幫助"
    assert stress.overlay_verdict(rows, "stop_15", 50) == {"tested": 0}
    assert stress._has({"kind": "blend", "sleeves": [{"rule": {"stop_loss": 0.15}}, {"rule": {}}]}, {"stop_loss": 0.15})
    assert not stress._has({"market_filter": "none"}, {"market_filter": "taiex_200"})


def test_an_account_opened_on_the_high_counts_its_loss_against_the_money_put_in():
    from quant_platform.research.legacy_challenger import RunResult

    days = [date(2008, 1, 1) + timedelta(days=i) for i in range(5)]
    result = RunResult("x", days, [300_000, 240_000, 250_000, 320_000, 330_000], [0, 0, 10_000, 0, 0],
                       [(days[0], 300_000), (days[2], 10_000)])
    found = stress.entry(result, days[4])
    assert found["worst"] == -0.2 and found["worst_day"] == days[1].isoformat()
    assert found["even_days"] == 3                     # 320,000 against 310,000 put in, three days after the start
def test_the_window_shows_every_scenario_and_the_entries_on_the_high(tmp_path):
    import json

    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    folder = tmp_path / "research" / "stress"
    folder.mkdir(parents=True)
    bear = {"high": "2007-10-29", "low": "2008-11-20", "base_fall": -0.55, "fall": -0.40, "worst": -0.42, "gap": 0.15,
            "base_back": "2011-01-28", "back": "2010-03-01", "base_days": 1187, "days": 854, "held": True}
    row = {"trial": 6490, "spec_hash": "h", "name": "深度學習", "why": "取捨前緣", "tier": "T0 候選", "final_value": 1,
           "final": 5_000_000, "base_final": 2_473_000, "contributed": 2_500_000, "max_drawdown": -0.45,
           "base_max_drawdown": -0.55, "bears": [bear], "tops": [{"worst": -0.35, "worst_day": "2008-11-20", "even_days": 700,
                                                                  "base_worst": -0.5, "base_even_days": 1100}],
           "held_2008": True, "held_count": 1, "held_all": True, "worse_bears": 0, "scenario_count": 1}
    scenario = {"high": "2007-10-29", "low": "2008-11-20", "fall": -0.55, "size": "大空頭", "speed": "慢跌", "sessions": 270,
                "back": "2011-01-28", "base_days": 1187, "held": 1, "tested": 1,
                "least": {"trial": 6490, "fall": -0.40}, "most": {"trial": 6490, "fall": -0.40}}
    (folder / "stress-20261024-180000.json").write_text(json.dumps({
        "generated_at": "2026-10-24T18:00:00", "bear": 0.15, "big": 0.2, "fast": 40, "scenarios": [scenario],
        "base_tops": [{"worst": -0.5, "worst_day": "2008-11-20", "even_days": 1100}], "base_final": 2_473_000,
        "rows": [row, {"trial": 1, "spec_hash": "x", "name": "壞", "why": "w", "error": "boom"}]}), encoding="utf-8")
    app = create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}")))
    body = app.test_client().get("/research/stress").get_data(as_text=True)
    assert "每一次空頭（1 次）" in body and "大空頭" in body and "每次都撐過" in body and "-40%" in body
    assert "3.2 年" in body and "23 個月回本" in body and "沒跑成：boom" in body


def test_the_window_shows_the_overlay_check(tmp_path):
    """research_method §7 空頭減碼檢驗: each way of holding fewer stocks, every scenario and every strategy."""
    import json

    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    folder = tmp_path / "research" / "stress"
    folder.mkdir(parents=True)
    plain = {"final": 130_000_000, "recent_final": 31_000_000, "held_count": 4, "held_2008": False,
             "bears": [{"fall": -0.7, "gap": -0.15, "days": 2000, "held": False}]}
    calm = {"final": 44_000_000, "recent_final": 11_900_000, "held_count": 6, "held_2008": True,
            "bears": [{"fall": -0.45, "gap": 0.1, "days": 900, "held": True}]}
    (folder / "protect-20261024-230000.json").write_text(json.dumps({
        "generated_at": "2026-10-24T23:00:00", "base_final": 24_730_000, "base_recent_final": 9_820_000,
        "overlays": {"taiex_200": {"label": "加權指數跌破 200 日均線就不持股", "off": 0.31, "switches": 88},
                     "vol_v1": {"label": "波動大時減碼到現金"}},
        "scenarios": [{"high": "2007-10-29", "low": "2008-11-20", "fall": -0.55, "held": {"none": 0, "taiex_200": 0, "vol_v1": 1}}],
        "verdicts": {"taiex_200": {"tested": 0}, "vol_v1": stress.overlay_verdict(
            [{"variants": {"none": plain, "vol_v1": calm}}], "vol_v1", 24_730_000)},
        "rows": [{"trial": 6490, "spec_hash": "h", "name": "深度學習", "why": "取捨前緣",
                  "variants": {"none": plain, "taiex_200": {"error": "boom"}, "vol_v1": calm}},
                 {"trial": 1, "spec_hash": "x", "name": "壞", "why": "w", "error": "bad"}]}), encoding="utf-8")
    view = stress.protect_view(stress.latest(tmp_path / "research", "protect"))
    assert [way["key"] for way in view["ways"]] == ["taiex_200", "vol_v1"] and view["ways"][1]["held"] == 6.0
    assert view["ways"][1]["verdict"] == "空頭有幫助、代價太大" and view["ways"][0]["failed"] == 1
    app = create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}")))
    body = app.test_client().get("/research/stress").get_data(as_text=True)
    assert "大空頭來時減碼，有沒有用" in body and "31%" in body and "進出 88 次" in body
    assert "空頭有幫助、代價太大" in body and "沒跑成：bad" in body and "依波動減碼" in body
