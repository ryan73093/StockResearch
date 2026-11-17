"""2026-11-17 (the 16:00 check 「前向帳戶對帳 0 問題」: 293 accounts on 11-16 and 11-17): the lists a forward account
decided were kept and its sizes were not — every replay sized the recorded days again with the day's data."""
from datetime import date

import pytest

from quant_platform.research import daily, decisions
from quant_platform.research.decisions import DecisionStore, sizing

START, D1, D2, D3 = date(2026, 11, 9), date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12)
EARLY = date(2026, 1, 5)


@pytest.fixture()
def sized(monkeypatch):
    """What the three computations say "now" — a test changes it, as data arriving later does."""
    now = {"weights": {D1: {"a": 0.6, "b": 0.4}, D2: {"a": 0.5, "c": 0.5}},
           "exposure": {EARLY: 1.0, D1: 0.5, D2: 1.0, D3: 1.0}, "parked": {EARLY, D1}}
    monkeypatch.setattr(daily, "daily_weights", lambda fp, rule, ranks: now["weights"])
    monkeypatch.setattr(daily, "exposure_schedule", lambda data, fp, rule, costs: now["exposure"])
    monkeypatch.setattr(daily, "account_parking", lambda data, fp, rule, costs: now["parked"])
    return now


def _store(tmp_path, ranks, through):
    store = DecisionStore(tmp_path)
    store.keep("acct", ranks, None, through)
    return store


def test_the_days_recorded_keep_their_sizes_when_the_data_changes(tmp_path, sized):
    ranks = {D1: ["a", "b"], D2: ["a", "c"]}
    store = _store(tmp_path, ranks, D2)
    first = sizing(None, None, None, None, ranks, START, store, "acct", D2)
    assert first == (sized["weights"], sized["parked"], sized["exposure"])
    store.save()
    kept = DecisionStore(tmp_path).sized("acct")
    assert kept["through"] == "2026-11-11" and kept["parked"] == ["2026-11-10"]            # only the account's own days
    assert list(kept["exposure"]) == ["2026-11-10", "2026-11-11"]

    # the next day: the data now sizes the recorded days differently, and decides a new one
    sized["weights"] = {D1: {"a": 0.7, "b": 0.3}, D2: {"a": 0.4, "c": 0.6}, D3: {"a": 0.2, "c": 0.8}}
    sized["exposure"] = {EARLY: 1.0, D1: 1.0, D2: 0.5, D3: 0.5}
    sized["parked"] = {EARLY, D2, D3}
    ranks = {**ranks, D3: ["a", "c"]}
    store = DecisionStore(tmp_path)
    store.keep("acct", ranks, None, D3)
    weights, parked, exposure = sizing(None, None, None, None, ranks, START, store, "acct", D3)
    assert weights == {D1: {"a": 0.6, "b": 0.4}, D2: {"a": 0.5, "c": 0.5}, D3: {"a": 0.2, "c": 0.8}}
    assert exposure == {EARLY: 1.0, D1: 0.5, D2: 1.0, D3: 0.5}
    assert parked == {EARLY, D1, D3}
    assert sized["weights"][D1] == {"a": 0.7, "b": 0.3}                 # what is computed is shared: left as it was
    store.save()
    again = DecisionStore(tmp_path)
    assert again.sized("acct")["through"] == "2026-11-12"
    assert again.fixed("acct")["days"][D3] == ["a", "c"]                # the lists are still there


def test_the_lists_kept_again_do_not_drop_the_sizes(tmp_path, sized):
    ranks = {D1: ["a", "b"], D2: ["a", "c"]}
    store = _store(tmp_path, ranks, D2)
    sizing(None, None, None, None, ranks, START, store, "acct", D2)
    store.save()
    store = DecisionStore(tmp_path)
    store.keep("acct", ranks, None, D2)                                 # a replay that keeps the lists and no sizes
    store.save()
    assert DecisionStore(tmp_path).sized("acct")["weights"]["2026-11-10"] == {"a": 0.6, "b": 0.4}


def test_nothing_is_kept_without_a_store_or_for_what_a_rule_does_not_use(tmp_path, sized):
    ranks = {D1: ["a", "b"]}
    assert sizing(None, None, None, None, ranks, START) == (sized["weights"], sized["parked"], sized["exposure"])
    sized["weights"], sized["exposure"], sized["parked"] = None, None, None       # equal amounts, no scaling, no parking
    store = _store(tmp_path, ranks, D1)
    assert sizing(None, None, None, None, ranks, START, store, "acct", D1) == (None, None, None)
    assert store.sized("acct") == {"through": "2026-11-10", "weights": {}, "exposure": {}, "parked": []}
    assert sizing(None, None, None, None, ranks, START, store, "acct", D1) == (None, None, None)


def test_a_kept_size_for_another_list_is_not_used(tmp_path, sized):
    """The account was decided again (other stocks on the day): the old shares do not fit the new list."""
    ranks = {D1: ["a", "b"]}
    store = _store(tmp_path, ranks, D1)
    sizing(None, None, None, None, ranks, START, store, "acct", D1)
    sized["weights"] = {D1: {"a": 0.3, "z": 0.7}}
    weights, _parked, _exposure = sizing(None, None, None, None, {D1: ["a", "z"]}, START, store, "acct", D1)
    assert weights[D1] == {"a": 0.3, "z": 0.7}


def test_an_earlier_through_is_not_moved_back(tmp_path, sized):
    ranks = {D1: ["a", "b"], D2: ["a", "c"]}
    store = _store(tmp_path, ranks, D2)
    sizing(None, None, None, None, ranks, START, store, "acct", D2)
    sizing(None, None, None, None, ranks, START, store, "acct", D1)
    assert store.sized("acct")["through"] == "2026-11-11"
    assert decisions.sizing is sizing
