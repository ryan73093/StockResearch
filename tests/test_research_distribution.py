"""2026-10-26 (使用者：x 軸是最後的錢、y 軸是數量，投入的金額在中間，每個策略在 x 軸的哪一點): the end money's spread
(research/distribution.py)."""
import json
import math

from quant_platform.research import distribution


def _rules(research, values, bench=3_000_000):
    (research / "reports").mkdir(parents=True)
    rows = []
    for number, value in enumerate(values):
        name = f"r{number}.json"
        (research / "reports" / name).write_text(json.dumps(
            {"strategy": {"contributed": 1_660_000}, "since_2020": {"strategy": {"contributed": 1_020_000}}}),
            encoding="utf-8")
        rows.append({"trial_id": number, "name": f"規則{number}", "tier": "T3", "spec_hash": f"h{number}", "report_file": name,
                     "start": "2015-06-01", "final_value": value * 2, "benchmark_final_value": bench * 2,
                     "since_2020_final_value": value, "since_2020_benchmark_final_value": bench,
                     "category": {"family": "趨勢動能"}})
    rows.append({"trial_id": 99, "name": "一半 0050", "uses_0050": True, "final_value": 9e9, "report_file": "x"})
    return rows


def test_each_rule_sits_on_its_side_and_the_money_put_in_is_the_middle(tmp_path):
    rows = _rules(tmp_path, [500_000, 2_000_000, 2_500_000, 5_000_000, 12_000_000])
    found = distribution.view(tmp_path, rows)
    recent = found["recent"]
    assert recent["invested"] == 1_020_000 and recent["bench"] == 3_000_000 and found["full"]["invested"] == 1_660_000
    assert recent["counts"] == {"lost": 1, "behind": 2, "ahead": 2}                   # the half-0050 rule is not in it
    assert [item["value"] for item in recent["items"]][0] == 12_000_000
    edges = [item["low"] for item in recent["bins"]] + [recent["bins"][-1]["high"]]
    middle = len(edges) // 2
    assert math.isclose(edges[middle], 102.0)                                         # 萬元, in the middle
    assert math.isclose(edges[middle] / edges[0], edges[-1] / edges[middle])           # as far left as right
    assert sum(item["count"] for item in recent["bins"]) == 5
    assert any("規則4" in item["title"] for item in recent["bins"])
    svg = distribution.charts(found)["recent"]
    assert "投入 102 萬" in svg and "0050 300 萬" in svg
    # the dashboard's: bars, and the one category's row (five rules: each a point) on each of the four drawings
    assert svg.count("<circle") == 5 * 4 and svg.count('class="frow"') == 4
    # the window's: every rule also a point under the bars on the mid and wide drawings
    assert distribution.charts(found, with_dots=True)["recent"].count("<circle") == 5 * 3 + 5 * 2


def test_the_window_lists_both_sides(tmp_path):
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app
    from quant_platform.research import pool

    research = tmp_path / "research"
    rows = _rules(research, [500_000, 5_000_000])
    pool_rows = {"daily": rows}
    original = pool.pool_view
    pool.pool_view = lambda *args, **kwargs: pool_rows
    try:
        client = create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}",
                                                     scheduler_in_web=False))).test_client()
        body = client.get("/research/distribution?track=recent").get_data(as_text=True)
    finally:
        pool.pool_view = original
    assert "錢最多的 40 個" in body and "賠錢的 1 個" in body and "規則1" in body and "趨勢動能" in body


def test_the_search_samples_end_money_and_0050_on_the_same_flows(tmp_path):
    """The account of a sampled setting: NT$300,000 at the start and NT$10,000 from each later month's 5th; the
    search's samples binned with 0050's money on the same flows, counts scaled by the sampling."""
    from datetime import date, timedelta

    import numpy as np

    days, day = [], date(2020, 9, 1)
    while len(days) < 300:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    returns = np.full((1, len(days) - 1), 0.001)
    end, put_in = distribution.account_ends(returns, days, 0)
    months = len({(d.year, d.month) for d in days[1:-1] if (d.year, d.month) != (days[0].year, days[0].month)})
    assert put_in == 300_000 + 10_000 * months
    assert end[0] > put_in
    flat, _ = distribution.account_ends(np.zeros((1, len(days) - 1)), days, 0)
    assert flat[0] == put_in                                         # no returns: what was put in

    out = tmp_path / "scan-full-1.0.0-2026-08-31-a"
    (out / "arrays").mkdir(parents=True)
    import json as _json

    bench = np.append(np.full(len(days) - 1, 0.0005), 0.0)
    np.save(out / "arrays" / "bench.npy", bench)
    for name, value in (("next_returns", np.zeros((2, len(days)), dtype=np.float32)), ("calm", np.ones((2, len(days)), dtype=np.float32)),
                        ("industry", np.zeros(2, dtype=np.int32)), ("weekly_days", np.ones(len(days), dtype=bool)),
                        ("percentiles", np.zeros((2, 1, 2, len(days)), dtype=np.float16))):
        np.save(out / "arrays" / f"{name}.npy", value)
    (out / "arrays" / "meta.json").write_text(_json.dumps({"names": ["s"], "days": [d.isoformat() for d in days]}), encoding="utf-8")
    # one sample a little behind 0050 every day (still making money), one ahead of it
    np.save(out / "sample-0000000.excess.npy", np.array([np.full(len(days) - 1, -0.0004), np.full(len(days) - 1, 0.001)],
                                                        dtype=np.float16))
    assert distribution.update_spread(out)["samples"] == 2
    assert distribution.update_spread(out)["samples"] == 2                          # a file is read once
    assert distribution.search_view(tmp_path / "nothing") is None
    scans = tmp_path / "research" / "scans"
    scans.mkdir(parents=True)
    out.rename(scans / out.name)
    found = distribution.search_view(tmp_path / "research")
    recent = found["recent"]
    assert recent["samples"] == 2 and recent["estimated"] == 2 * 500
    assert recent["share"]["ahead"] == 0.5 and recent["share"]["behind"] == 0.5 and recent["share"]["lost"] == 0


def test_each_category_is_a_row_with_its_median_and_middle_half(tmp_path):
    """2026-10-27 (使用者：深度學習、RL、多因子、趨勢動能這些類別的分佈在哪一個位置，連標都沒標)."""
    rows = _rules(tmp_path, [500_000, 2_000_000, 2_500_000, 5_000_000, 12_000_000])
    rows[0]["category"] = rows[3]["category"] = {"family": "深度學習"}
    recent = distribution.view(tmp_path, rows)["recent"]
    families = recent["families"]
    assert [row["family"] for row in families] == ["深度學習", "趨勢動能"]                 # FAMILY_ORDER
    deep, trend = families
    assert deep["count"] == 2 and math.isclose(deep["median"], 275.0)                    # (50 + 500) / 2 萬
    assert math.isclose(deep["q1"], 162.5) and math.isclose(deep["q3"], 387.5)
    assert (deep["lost"], deep["behind"], deep["ahead"]) == (1, 0, 1)
    assert trend["count"] == 3 and math.isclose(trend["median"], 250.0) and trend["best"]["value"] == 12_000_000
    assert sum(deep["bins"]) == 2 and sum(trend["bins"]) == 3 and len(deep["bins"]) == len(recent["bins"])
    svg = distribution.charts({"recent": recent})["recent"]
    assert 'data-spread-family="深度學習" data-spread-track="recent"' in svg and "深度學習・2 個" in svg
    assert "中位數 275 萬、中間一半 162～388 萬" in svg                                   # the row's title
    alone = distribution.focus(recent, "深度學習")
    assert [item["value"] for item in alone["items"]] == [5_000_000, 500_000] and alone["families"] == []
    assert sum(item["count"] for item in alone["bins"]) == 2 and alone["counts"] == {"lost": 1, "behind": 0, "ahead": 1}
    assert [item["low"] for item in alone["bins"]] == [item["low"] for item in recent["bins"]]   # the same axis
    assert distribution.focus(recent, "RL 機器人") is None


def test_the_forward_accounts_by_their_counted_result_and_ahead_of_0050_first():
    """2026-10-27 (使用者：獲益的那些分佈圖我也要看): an account behind the money put in but ahead of 0050 (which lost
    more) is ahead of 0050, not with the losers. 2026-10-29 (使用者：我拿到的才叫賺): each account by the money counted
    (stock_forward.kept), not everything sold at the close."""
    pool = [{"spec_hash": "a", "trial_id": 1, "name": "甲", "tier": "T0 候選", "category": {"family": "深度學習"}},
            {"spec_hash": "b", "trial_id": 2, "name": "乙", "tier": "T1", "category": {"family": "RL 機器人"}}]
    def account(key, back, **extra):
        return {"rule_hash": key, "since": "2026-09-01", "date": "2026-10-27", "contributed": 320_000.0, "name": key,
                "kept": {"cash_back": back, "bench_back": 311_233.0},
                "sell_all": {"cash_back": back + 50_000.0, "bench_back": 311_233.0}, **extra}      # not counted
    summary = [account("a", 330_000.0), account("b", 315_000.0), account("c", 300_000.0, kind="blend"),
               account("d", 900_000.0, stopped="2026-10-27"), {**account("e", 1.0), "since": "2026-08-01"}]
    track = distribution.forward_view(pool, summary, "2026-09-01")["forward"]
    assert track["invested"] == 320_000 and track["bench"] == 311_233 and track["noun"] == "個帳戶"
    assert [(item["spec_hash"], item["side"]) for item in track["items"]] == [("a", "ahead"), ("b", "ahead"), ("c", "lost")]
    assert [row["family"] for row in track["families"]] == ["深度學習", "RL 機器人", "組合帳戶"]
    assert track["label"] == "前向帳戶（9/1 起、10/27 實際結果）"
    assert "每個帳戶最後有多少錢的分佈" in distribution.charts({"forward": track})["forward"]
    assert distribution.forward_view(pool, [], "2026-09-01") == {}


def test_the_window_shows_one_category_on_its_own(tmp_path):
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app
    from quant_platform.research import pool

    research = tmp_path / "research"
    rows = _rules(research, [500_000, 5_000_000, 6_000_000])
    rows[2]["category"] = {"family": "深度學習"}
    original = pool.pool_view
    pool.pool_view = lambda *args, **kwargs: {"daily": rows}
    try:
        client = create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}",
                                                     scheduler_in_web=False))).test_client()
        whole = client.get("/research/distribution?track=recent").get_data(as_text=True)
        alone = client.get("/research/distribution?track=recent&family=深度學習").get_data(as_text=True)
        forward = client.get("/research/distribution?track=forward").get_data(as_text=True)
    finally:
        pool.pool_view = original
    assert "每一類策略落在哪裡" in whole and 'data-spread-family="深度學習"' in whole
    assert "・深度學習" in alone and "規則2" in alone and "規則1" not in alone and "每一類策略落在哪裡" not in alone
    assert "chip--on" in alone and "還沒有前向帳戶" in forward


def test_the_daily_check_finds_a_category_without_its_row(tmp_path, monkeypatch):
    from quant_platform.research import pool

    rows = _rules(tmp_path, [500_000, 5_000_000])
    rows[1]["category"] = {"family": "RL 機器人"}
    monkeypatch.setattr(pool, "pool_view", lambda *args, **kwargs: {"daily": rows})
    assert distribution.unmarked(tmp_path) == []
    monkeypatch.setattr(distribution, "_rows", lambda key, track: [])                 # a drawing without the rows
    assert distribution.unmarked(tmp_path) == ["2020-10 起（短賽道）：RL 機器人", "2020-10 起（短賽道）：趨勢動能",
                                               "2015-06 起（全段）：RL 機器人", "2015-06 起（全段）：趨勢動能"]
