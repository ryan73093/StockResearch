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
        early = (np.prod(1 + series[:split]) / np.prod(1 + bench[:split])) ** (365.25 / (days[split] - days[0]).days) - 1
        assert row["early"] == pytest.approx(early, abs=2e-5)
        assert row["score"] == pytest.approx(0.25 * row["early"] + 0.75 * row["recent"], abs=2e-5)
        value, base = np.cumprod(1 + series), np.cumprod(1 + bench[:len(series)])
        starts = range(split, len(series) - 245, 21)
        wins = [value[s + 245] / value[s] - base[s + 245] / base[s] > 0 for s in starts]
        assert row["recent_win_1y"] == pytest.approx(np.mean(wins), abs=1e-4)


def test_2020_10_on_is_the_gate_and_2015_2020_only_a_quarter_of_the_score():
    """2026-10-26 23:10 (使用者：2015 到 2020 甚至你要降低，短歷史賽道你應該要加重權重的去看)."""
    row = {"excess": -0.02, "early": -0.08, "recent": 0.01, "recent_win_1y": 0.6, "drawdown": -0.40,
           "bench_drawdown": -0.30, "win_1y": 0.3}
    assert scan_full.passes(row)                                      # behind 0050 before 2020-10: no gate
    assert not scan_full.passes({**row, "recent": -0.001})            # 2015～2020 alone is never enough
    assert not scan_full.passes({**row, "recent_win_1y": 0.45})
    assert not scan_full.passes({**row, "drawdown": -0.46})           # more than 15 points deeper than 0050


def test_the_signals_take_in_the_paid_tables_and_not_what_the_owner_ruled_out():
    names = scan_full.signals()
    for paid in ("big_holder_pct", "margin_safe", "low_pledge_loan_20", "block_volume_20", "govbank_net_20",
                 "broker_concentration_5", "low_daytrade_5"):
        assert paid in names
    for left in ("revenue_yoy", "market_cap", "dl_risk"):
        assert left not in names
    assert len(names) == len(set(names))


def test_two_parts_screen_every_block_once_and_go_on_after_a_stop(monkeypatch, tmp_path):
    days = sessions(1100)                      # 2019-01 to 2023-03: one-year windows starting after 2020-10 exist
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
             "recent": recent, "excess": 0.01, "score": recent} for key, recent in ((("a",), 0.05), (("a", "b"), 0.04), (("c",), 0.03),
                                                                  (("d",), 0.02))]
    chosen = scan_full.choose(arrays, rows, [], None, count=10, simulate_fn=simulate)
    assert [row["factors"] for row in chosen] == [["a"], ["c"], ["d"]]
    seed = {"factors": ["c"], "universe": "all", "weighting": "equal", "check": "daily", "hold": "days"}
    chosen = scan_full.choose(arrays, rows, [seed], None, count=10, simulate_fn=simulate)
    assert [row["factors"] for row in chosen] == [["a"], ["d"]]                     # c was searched before
    assert len(scan_full.choose(arrays, rows, [], None, count=1, simulate_fn=simulate)) == 1


def test_a_refused_replace_is_retried_and_phase_b_waits_for_the_whole_phase_a(tmp_path, monkeypatch):
    """2026-10-27 02:50: Windows refused replacing a progress file another process had open; both parts of phase a
    stopped, and the pipeline went on to phase b with signals picked from 9% of phase a."""
    from pathlib import Path

    monkeypatch.setattr(scan_full.time, "sleep", lambda seconds: None)
    original, refused = Path.replace, []

    def replace(self, target):
        if len(refused) < 2:
            refused.append(target)
            raise PermissionError(5, "存取被拒")
        return original(self, target)
    monkeypatch.setattr(Path, "replace", replace)
    first = scan_full._progress_path(tmp_path, 0, 2)
    partial = first.with_suffix(".partial")
    partial.write_text(json.dumps({"screened": 5, "finished_at": "2026-10-28T10:00:00"}), encoding="utf-8")
    scan_full._replace(partial, first)
    assert len(refused) == 2 and json.loads(first.read_text(encoding="utf-8"))["screened"] == 5
    assert not scan_full.phase_done(tmp_path)                                      # the other part has not finished
    scan_full._progress_path(tmp_path, 1, 2).write_text(json.dumps({"screened": 4}), encoding="utf-8")
    assert not scan_full.phase_done(tmp_path)
    scan_full._progress_path(tmp_path, 1, 2).write_text(json.dumps({"screened": 4, "finished_at": "x"}), encoding="utf-8")
    assert scan_full.phase_done(tmp_path)
