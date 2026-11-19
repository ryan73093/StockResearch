"""2026-10-23 (research_method §7「分點」): research/branches.py — the branches chosen month by month only from
what was known before the month, the flippers apart from the informed, the day files read in order."""
import gzip
import json
from datetime import date

import numpy as np
import pytest

from quant_platform.research import branches


def test_a_month_chooses_only_from_the_months_before_and_a_flipper_is_never_informed():
    tally = branches.Tally(3)
    rows = np.zeros(300, dtype=int)                     # 300 large trades, outcomes known in month 0
    good = np.where(np.arange(300) % 2, 0.03, 0.01)     # mean +2%, t about 35
    plain = np.where(np.arange(300) % 2, 0.01, -0.01)   # mean 0
    values = np.column_stack([good, plain, good])
    ids = np.array([0, 1, 2])
    tally.add("n", rows, ids, np.ones_like(values))
    tally.add("s", rows, ids, values)
    tally.add("q", rows, ids, values ** 2)
    days = np.zeros(120, dtype=int)
    bought = np.zeros((120, 3))
    bought[:, 2] = 1.0                                  # branch 2 buys big 120 days and sells 90% back the next day
    tally.add("flip_buy", days, ids, bought)
    tally.add("flip_back", days, ids, bought * 0.9)
    tally.add("flip_days", days, ids, (bought > 0).astype(float))
    informed, flippers, detail = branches.choose(tally, 3)
    assert not informed[0].any() and not flippers[0].any()          # nothing known before month 0
    assert informed[1].tolist() == [True, False, False]              # 2 is as good, but a flipper
    assert flippers[1].tolist() == [False, False, True]
    assert informed[2].tolist() == [True, False, False] and detail[1]["informed"] == 1
    assert detail[1]["top_informed"][0][0] == 0 and detail[1]["top_flippers"][0][:2] == (2, 0.9)


def test_too_few_trades_or_a_weak_record_is_not_chosen():
    tally = branches.Tally(2)
    values = np.where(np.arange(100) % 2, 0.03, 0.01)[:, None]       # strong, but only 100 trades
    tally.add("n", np.zeros(100, dtype=int), np.array([5]), np.ones_like(values))
    tally.add("s", np.zeros(100, dtype=int), np.array([5]), values)
    tally.add("q", np.zeros(100, dtype=int), np.array([5]), values ** 2)
    informed, _flippers, _detail = branches.choose(tally, 6)
    assert not informed.any()


def test_the_tally_grows_with_new_branches():
    tally = branches.Tally(1)
    tally.add("n", np.zeros(2, dtype=int), np.array([3000]), np.ones((2, 1)))
    assert tally.width > 3000 and tally.data["n"][0, 3000] == 2


def test_a_stocks_day_files_are_read_in_session_order_with_global_branch_ids(tmp_path):
    folder = tmp_path / "2330"
    folder.mkdir()
    for day, rows in (("2026-10-02", [["A", 1, 2, 100.0, 200.0], ["B", 3, 0, 300.0, 0.0]]),
                      ("2026-10-01", [["B", 1, 1, 10.0, 20.0]]), ("2026-10-03", [])):
        (folder / f"{day}.json.gz").write_bytes(gzip.compress(json.dumps({"date": day, "rows": rows}).encode()))
    index = {date(2026, 10, 1): 7, date(2026, 10, 2): 8}             # 10-03 is not a session here: skipped
    names = {"Z": 0}
    columns, ids, bought, sold = branches.read_stock(folder, index, names)
    assert columns.tolist() == [7, 8]
    assert [name for name, number in sorted(names.items(), key=lambda item: item[1])] == ["Z", "B", "A"]   # as met
    assert ids.tolist() == [names["B"], names["A"]]
    assert bought[0].tolist() == [10.0, 0.0] and sold[0].tolist() == [20.0, 0.0]       # 10-01: B only
    assert bought[1].tolist() == [300.0, 100.0] and sold[1].tolist() == [0.0, 200.0]


def test_the_outcome_starts_the_session_after_and_is_measured_against_the_average():
    close = np.vstack([np.linspace(10, 20, 40), np.full(40, 10.0), np.linspace(10, 5, 40)])
    found = branches.outcomes(close, lambda column: np.ones(3, dtype=bool), range(0, 40), horizon=5)
    column = 3
    ahead = close[:, column + 1 + 5] / close[:, column + 1] - 1
    assert found["excess"][:, column] == pytest.approx(ahead - ahead.mean())
    assert np.isnan(found["excess"][:, 40 - 6]).all()                 # its outcome is past the end
    one = close[:, column + 1] / close[:, column] - 1
    assert found["next1"][:, column] == pytest.approx(one - one.mean())


def test_a_stock_read_once_is_kept_in_one_file_until_a_day_is_added(tmp_path):
    """2026-10-23: the 720,000 small day files took about 30 s a stock; the cache holds one stock in one file."""
    folder = tmp_path / "raw" / "finmind" / "branches" / "2330"
    folder.mkdir(parents=True)

    def day(name, rows):
        (folder / f"{name}.json.gz").write_bytes(gzip.compress(json.dumps({"date": name, "rows": rows}).encode()))
    day("2026-10-01", [["A", 1, 2, 100.0, 200.0]])
    cache = branches.cache_path(folder)
    assert cache == tmp_path / "branches" / "2330.npz"
    first = branches.load_stock(folder, cache)
    assert cache.is_file() and first[0] == ["2026-10-01"] and first[1] == ["A"]
    (folder / "2026-10-01.json.gz").write_bytes(b"not read again")       # the cache answers while the count is the same
    assert branches.load_stock(folder, cache)[2].tolist() == [[100.0]]
    day("2026-10-01", [["A", 1, 2, 100.0, 200.0]])
    day("2026-10-02", [["B", 1, 0, 50.0, 0.0]])                        # a new day: read again
    days, names, bought, sold = branches.load_stock(folder, cache)
    assert days == ["2026-10-01", "2026-10-02"] and names == ["A", "B"] and bought.tolist() == [[100.0, 0.0], [0.0, 50.0]]
    loaded = list(branches.stocks(folder.parent, ["2330"]))
    assert loaded[0][0] == "2330" and loaded[0][1][0] == days


def test_a_new_day_reads_only_its_file_and_matches_reading_them_all(tmp_path):
    """2026-11-02 (the nightly paid download adds a day to 555 stocks): only the days after the cache's last are read,
    the same as reading every file again (as the cache keeps it, float32); a day older than the cache's last, or a
    changed file count before it, reads them all."""
    folder = tmp_path / "raw" / "finmind" / "branches" / "2330"
    folder.mkdir(parents=True)

    def day(name, rows):
        (folder / f"{name}.json.gz").write_bytes(gzip.compress(json.dumps({"date": name, "rows": rows}).encode()))
    day("2026-10-01", [["A", 1, 2, 100.1, 200.2]])
    day("2026-10-02", [["B", 1, 0, 50.3, 0.0], ["A", 0, 1, 0.0, 33.3]])
    cache = branches.cache_path(folder)
    branches.load_stock(folder, cache)
    (folder / "2026-10-01.json.gz").write_bytes(b"not read again")
    day("2026-10-05", [["C", 2, 0, 70.7, 0.0], ["A", 1, 0, 9.9, 0.0]])
    days, names, bought, sold = branches.load_stock(folder, cache)                  # 10-01 not read: it would fail
    assert days == ["2026-10-01", "2026-10-02", "2026-10-05"] and names == ["A", "B", "C"]
    day("2026-10-01", [["A", 1, 2, 100.1, 200.2]])
    whole = branches.load_stock(folder, None)                                         # every file, no cache
    assert whole[0] == days and whole[1] == names
    assert np.array_equal(bought, whole[2].astype(np.float32).astype(float))
    assert np.array_equal(sold, whole[3].astype(np.float32).astype(float))
    day("2026-09-30", [["D", 1, 0, 1.0, 0.0]])                                        # an older day: all read again
    again = branches.load_stock(folder, cache)
    assert again[0][0] == "2026-09-30" and again[1][0] == "D"


def test_the_signals_cover_the_last_day_every_stock_has(tmp_path):
    folder = tmp_path / "branches"
    for code, last in (("2330", "2026-10-23"), ("2317", "2026-10-22")):
        (folder / code).mkdir(parents=True)
        for name in ("2026-10-21", "2026-10-22", last):
            (folder / code / f"{name}.json.gz").write_bytes(b"")
    assert branches.through(folder) == date(2026, 10, 22)                # 2317's 10-23 not stored yet


def test_a_stocks_reports_count_only_from_the_year_after_it_was_first_among_the_top(tmp_path):
    """2026-11-19: the download list is every stock among the top 300 by turnover in ANY year to now; a stock covered
    in 2022 only because it became a top stock in 2024 told the backtest its future by having a value at all."""
    import pandas as pd

    from quant_platform.research.history import finmind_daily

    folder = tmp_path / "stocks" / "twse"
    folder.mkdir(parents=True)
    for year, money in ((2021, {"1101": 9.0, "1102": 1.0, "1103": 2.0}), (2022, {"1101": 9.0, "1102": 8.0, "1103": 2.0}),
                        (2023, {"1101": 1.0, "1102": 8.0, "1103": 9.0})):
        pd.DataFrame({"code": list(money), "turnover": list(money.values())}).to_parquet(folder / f"{year}.parquet")
    tops = finmind_daily.top_years(tmp_path, date(2021, 6, 30), top=1)
    assert tops == {2021: {"1101"}, 2022: {"1101"}, 2023: {"1103"}}
    assert finmind_daily.branch_codes(tmp_path, date(2021, 6, 30), top=1) == ["1101", "1103"]     # what is downloaded
    assert finmind_daily.usable_from(tmp_path, date(2021, 6, 30), top=1) == {"1101": 2022, "1103": 2024}
    years = np.array([2021, 2022, 2022, 2023, 2024])
    columns = np.array([0, 1, 3, 4])
    assert branches.known_then(columns, years, 2022).tolist() == [False, True, True, True]
    assert branches.known_then(columns, years, 2024).tolist() == [False, False, False, True]     # 1103: not before 2024
    assert not branches.known_then(columns, years, None).any()


def test_an_earlier_list_graded_again_after_a_fix_takes_only_those_rules():
    """2026-11-19: --only-new on the August list would add every rule registered since; ``only`` keeps it to the rules
    being graded again."""
    from quant_platform.research import rolling

    items = [("a", {"n": 1}), ("b", {"n": 2}), ("c", {"n": 3})]
    assert rolling.wanted(items, {"a"}, None) == [{"n": 2}, {"n": 3}]
    assert rolling.wanted(items, {"a"}, {"b"}) == [{"n": 2}]
    assert rolling.wanted(items, None, set()) == []


def test_low_beta_rerank_reads_beta_and_the_worst_fall_from_stored_excess():
    """2026-11-19: the earlier searches kept each passing candidate's daily excess; its beta to 0050 and how much
    further it fell in 0050's falls come from that, without screening again."""
    from quant_platform.research import scan_large
    from quant_platform.research.downside import falls

    bench = np.array([0.01, -0.06, -0.06, 0.02, 0.01, -0.01, 0.03, 0.02])
    half = 0.5 * bench                                   # beta 0.5: falls half as far
    double = 2.0 * bench
    excess = np.stack([half - bench, double - bench, np.zeros(len(bench))]).astype(np.float16)
    stretches = falls(bench)
    assert stretches
    beta, gap = scan_large.beta_and_gap(excess, bench, stretches)
    assert np.allclose(beta, [0.5, 2.0, 1.0], atol=0.02)
    assert gap[0] > 0 > gap[1] and abs(gap[2]) < 1e-3


def test_one_row_per_signal_set_its_steadiest_setting():
    """2026-11-19: the steadiest low-beta rows were a few signal sets in their 24 settings, near copies of each other."""
    from quant_platform.research import scan_large

    rows = [{"factors": ["a", "b"], "hold": "days", "first_half": 0.02, "second_half": 0.05, "worst_1y": -0.1},
            {"factors": ["b", "a"], "hold": "free", "first_half": 0.04, "second_half": 0.05, "worst_1y": -0.2},
            {"factors": ["a", "c"], "hold": "days", "first_half": 0.01, "second_half": 0.01, "worst_1y": -0.1}]
    kept = scan_large.steadiest_per_set(rows)
    assert sorted((tuple(sorted(row["factors"])), row["hold"]) for row in kept) == [(("a", "b"), "free"), (("a", "c"), "days")]


def test_the_full_search_chosen_again_by_recent_beta():
    """2026-11-19 (third version): one row per signal set by score; the lowest fifth by the last sessions' beta, apart
    from each other."""
    from quant_platform.research import scan_full

    rows = [{"factors": ["a", "b"], "score": 0.3, "recent": 0.1, "hold": "days"},
            {"factors": ["b", "a"], "score": 0.5, "recent": 0.1, "hold": "free"},
            {"factors": ["c"], "score": 0.4, "recent": 0.1, "hold": "days"}]
    kept = scan_full.best_per_set(iter(rows), 5)
    assert [(tuple(sorted(row["factors"])), row["hold"]) for row in kept] == [(("a", "b"), "free"), (("c",), "days")]
    rng = np.random.default_rng(1)
    bench = rng.normal(0, 0.01, 300)
    assert abs(scan_full.recent_beta(0.5 * bench, bench, 200) - 0.5) < 1e-9
    noise = [rng.normal(0, 0.01, 300) for _ in range(10)]
    scored = [({"factors": [f"s{index}"], "score": 1 - index / 10}, noise[index].astype(np.float32), 0.5 + index / 10)
              for index in range(10)]
    scored[1] = (scored[1][0], scored[0][1], 0.55)                # the second lowest beta: a copy of the first
    chosen = scan_full.low_beta_choice(scored, [], 5, share=0.3)
    assert [row["factors"][0] for row in chosen] == ["s0", "s2"]   # beta 0.5, 0.55 (a copy: skipped), 0.7
