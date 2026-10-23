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
