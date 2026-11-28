"""2026-11-28 滾動共識名單 (research/rolling_vote.py): the month's 20 to-date-best rules vote with what they hold."""
import json
import types
from datetime import date

import numpy as np

from quant_platform.research import daily, rl_select, rolling_vote


def test_a_stock_has_a_vote_from_each_voter_holding_it_and_equal_votes_follow_the_voters_places():
    lists = {0: frozenset({1, 2}), 1: frozenset({2, 3}), 2: frozenset({3})}
    scores = np.array([5.0, 9.0, 1.0])                                  # rule 1 is placed first, then 0, then 2
    votes = rolling_vote.tally([0, 1, 2], scores, lists.__getitem__)
    assert int(votes[2]) == 2 and int(votes[3]) == 2 and int(votes[1]) == 1
    assert votes[2] > votes[3] > votes[1]                               # 2: the first and second voters; 3: first and third
    assert max(value - int(value) for value in votes.values()) < 0.01   # the ordering part never makes a vote


def _data(rules=40, months=30, seed=2):
    rng = np.random.default_rng(seed)
    base = rng.normal(0.01, 0.03, months)
    returns = base[None, :] + np.linspace(-0.01, 0.01, rules)[:, None] + rng.normal(0, 0.01, (rules, months))
    return {"months": [f"{2015 + (m + 5) // 12}-{1 + (m + 5) % 12:02d}" for m in range(months)], "base": base,
            "returns": returns, "trials": list(range(100, 100 + rules)), "names": [str(n) for n in range(rules)],
            "families": [f"f{n % 8}" for n in range(rules)], "guard": np.zeros(rules), "blend": np.zeros(rules)}


def test_the_voters_of_a_month_are_chosen_from_the_months_up_to_it_only(monkeypatch):
    monkeypatch.setattr(rl_select, "PICK", 5)
    monkeypatch.setattr(rolling_vote, "KEEP", 5)                        # the keep zone: the top five only
    data = _data()
    rules = [[(f"k{n}", 1.0)] for n in range(40)]
    have = {f"k{n}": {month: [n, n + 1] for month in data["months"]} for n in range(40)}
    found = rolling_vote.monthly(data, rules, have)
    assert min(found) == data["months"][rolling_vote.WARM - 1] == "2016-05" and len(found) == 30 - 11
    assert all(len(item["voters"]) == 5 for item in found.values())
    later = {**data, "returns": data["returns"].copy()}
    later["returns"][:, 20:] += np.linspace(0.5, -0.5, 40)[:, None]     # the months after the 20th turn the order over
    again = rolling_vote.monthly(later, rules, have)
    for month in data["months"][11:20]:
        assert again[month] == found[month]                             # nothing before moves
    assert again[data["months"][25]]["voters"] != found[data["months"][25]]["voters"]


def test_the_votes_are_in_force_from_the_next_months_first_session_and_enter_the_rules_inputs(tmp_path):
    folder = tmp_path / rolling_vote.VERSION
    folder.mkdir()
    (folder / rolling_vote.FILE).write_text(json.dumps({"months": {
        "2020-01": {"voters": [1], "votes": {"1101.TW": 2.001, "9999.TW": 1.0}},
        "2020-02": {"voters": [1], "votes": {"2330.TW": 1.002}}}}), encoding="utf-8")
    fp = types.SimpleNamespace(symbols=["1101.TW", "2330.TW"],
                               sessions=[date(2020, 1, 30), date(2020, 1, 31), date(2020, 2, 3), date(2020, 2, 27),
                                         date(2020, 3, 2), date(2020, 5, 4)])
    votes = rolling_vote.matrix(fp, tmp_path)
    assert votes.shape == (6, 2) and np.isnan(votes[:2]).all()          # January's vote is not known in January
    assert np.isclose(votes[2, 0], 2.001) and np.isnan(votes[2, 1]) and np.isclose(votes[3, 0], 2.001)
    assert np.isnan(votes[4, 0]) and np.isclose(votes[4, 1], 1.002)     # February's from March's first session
    assert np.isclose(votes[5, 1], 1.002)                               # kept until there is a later vote
    assert np.isnan(rolling_vote.matrix(fp, tmp_path / "none")).all() and np.isnan(rolling_vote.matrix(fp, None)).all()
    assert rolling_vote.digest(tmp_path) != "missing" and rolling_vote.digest(tmp_path / "none") == "missing"

    first, second = daily.rolling_vote_batch()
    assert (first.top, second.top) == (20, 40) and first.factors == {"rolling_consensus": 1.0}
    same = daily.consensus_batch()[0]
    assert (first.check, first.keep, first.min_hold, first.industry_cap) == (same.check, same.keep, same.min_hold,
                                                                             same.industry_cap)
    assert "rolling_consensus" in daily.RULE_FACTORS and daily.BATCHES["rollvote"] is daily.rolling_vote_batch


def test_a_rule_reading_the_votes_carries_the_votes_files_digest(tmp_path):
    from quant_platform.research.model import models_root

    found = daily.input_digests(tmp_path, ["rolling_consensus"], False)
    assert found["rolling_vote"] == "missing"
    folder = models_root(tmp_path) / rolling_vote.VERSION
    folder.mkdir(parents=True)
    (folder / rolling_vote.FILE).write_text(json.dumps({"months": {}}), encoding="utf-8")
    assert daily.input_digests(tmp_path, ["rolling_consensus"], False)["rolling_vote"] == rolling_vote.digest(models_root(tmp_path))
    assert "rolling_vote" not in daily.input_digests(tmp_path, ["momentum_3"], False)
