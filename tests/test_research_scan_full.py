"""2026-10-26 (使用者：不要再用 2015～2020 篩、全部都測過一遍、要真的上億個、付費資料要進去): the full-period search
(research/scan_full.py)."""
import json
from datetime import date, timedelta
from math import comb

import numpy as np
import pytest

from quant_platform.research import scan, scan_full, scan_large


def sessions(count, start=date(2019, 1, 1)):
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def test_the_block_metrics_are_the_scans_and_the_excess_since_2020_10():
    rng = np.random.default_rng(3)
    days = sessions(800)
    returns = rng.normal(0.0006, 0.012, (5, len(days) - 1))
    bench = np.append(rng.normal(0.0004, 0.01, len(days) - 1), 0.0)
    years = (days[-1] - days[0]).days / 365.25
    rows = scan_full.metrics_block(returns, bench, days, np.full(5, 40))
    for row, series in zip(rows, returns):
        alone = scan.metrics(series, bench, years, 40)
        for key in ("excess", "annual", "drawdown", "bench_drawdown", "win_1y", "worst_1y", "first_half", "second_half",
                    "entries_per_month"):
            assert row[key] == pytest.approx(alone[key], abs=2e-5), key
        split = next(index for index, day in enumerate(days) if day >= scan_full.RECENT)
        mine = np.prod(1 + series[split:])
        theirs = np.prod(1 + bench[split:len(series)])
        by_hand = (mine / theirs) ** (365.25 / (days[-1] - days[split]).days) - 1
        assert row["recent"] == pytest.approx(by_hand, abs=2e-5)


def test_a_setting_passes_only_ahead_of_0050_overall_and_since_2020_10():
    row = {"excess": 0.02, "recent": 0.01, "drawdown": -0.40, "bench_drawdown": -0.30, "win_1y": 0.6}
    assert scan_full.passes(row)
    assert not scan_full.passes({**row, "recent": -0.001})            # 2015～2020 alone is not enough
    assert not scan_full.passes({**row, "excess": -0.001})
    assert not scan_full.passes({**row, "drawdown": -0.46})           # more than 15 points deeper than 0050
    assert not scan_full.passes({**row, "win_1y": 0.45})


def test_the_signals_take_in_the_paid_tables_and_not_what_the_owner_ruled_out():
    names = scan_full.signals()
    for paid in ("big_holder_pct", "margin_safe", "low_pledge_loan_20", "block_volume_20", "govbank_net_20",
                 "broker_concentration_5", "low_daytrade_5"):
        assert paid in names
    for left in ("revenue_yoy", "market_cap", "dl_risk"):
        assert left not in names
    assert len(names) == len(set(names))


def test_two_parts_screen_every_block_once_and_go_on_after_a_stop(monkeypatch, tmp_path):
    days = sessions(600)
    arrays = type("A", (), {"days": days, "bench": np.zeros(len(days))})()
    monkeypatch.setattr(scan, "load_arrays", lambda folder: arrays)
    monkeypatch.setattr(scan_large, "SAMPLE_EVERY", 5)
    names = [f"s{index}" for index in range(5)]

    def accounts(chunk):                       # with s0: 0.1% a day ahead of 0050; the others behind
        return (np.array([[0.001 if "s0" in item["factors"] else -0.001] * (len(days) - 1) for item in chunk]),
                np.full(len(chunk), 10))
    calls = []

    def stopping(chunk):
        calls.append(len(chunk))
        if len(calls) == 2:
            raise KeyboardInterrupt
        return accounts(chunk)
    out = tmp_path / "run"
    with pytest.raises(KeyboardInterrupt):
        scan_full.screen_part(tmp_path, names, [1, 2], None, out, 0, 2, accounts=stopping)
    first = scan_full.screen_part(tmp_path, names, [1, 2], None, out, 0, 2, accounts=accounts)
    second = scan_full.screen_part(tmp_path, names, [1, 2], None, out, 1, 2, accounts=accounts)
    assert first["screened"] + second["screened"] == scan_large.total(names, [1, 2]) == (5 + comb(5, 2)) * 24
    rows = list(scan_full.passing_rows(out))
    assert len(rows) == first["passed"] + second["passed"] == (1 + 4) * 24
    assert all("s0" in row["factors"] and row["recent"] > 0 for row in rows)
    blocks = sorted(row["block"] for row in rows)
    assert len({(tuple(row["factors"]), row["universe"], row["weighting"], row["check"], row["hold"]) for row in rows}) == len(rows)
    assert {block % 2 for block in blocks} == {0, 1}
    best, passed = scan_full.best_rows(out, pool=3)
    assert passed == len(rows) and len(best) == 3
    assert scan_full.frequent(out, 1) == ["s0"]


def test_the_finalists_skip_near_copies_of_each_other_and_of_earlier_ones():
    arrays = type("A", (), {"names": ["a", "b", "c", "d"], "bench": np.zeros(301)})()
    rng = np.random.default_rng(0)
    base = {key: rng.normal(0, 0.01, 300) for key in "abcd"}
    series = {("a",): base["a"], ("a", "b"): base["a"] + rng.normal(0, 0.0005, 300),   # a near copy of a
              ("c",): base["c"], ("d",): base["d"]}

    def simulate(items):
        return np.array([series[tuple(item["factors"])] for item in items])
    rows = [{"factors": list(key), "universe": "all", "weighting": "equal", "check": "daily", "hold": "days",
             "recent": recent, "excess": 0.01} for key, recent in ((("a",), 0.05), (("a", "b"), 0.04), (("c",), 0.03),
                                                                  (("d",), 0.02))]
    chosen = scan_full.choose(arrays, rows, [], None, count=10, simulate_fn=simulate)
    assert [row["factors"] for row in chosen] == [["a"], ["c"], ["d"]]
    seed = {"factors": ["c"], "universe": "all", "weighting": "equal", "check": "daily", "hold": "days"}
    chosen = scan_full.choose(arrays, rows, [seed], None, count=10, simulate_fn=simulate)
    assert [row["factors"] for row in chosen] == [["a"], ["d"]]                     # c was searched before
    assert len(scan_full.choose(arrays, rows, [], None, count=1, simulate_fn=simulate)) == 1
