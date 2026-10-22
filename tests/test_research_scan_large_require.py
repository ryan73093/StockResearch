"""2026-10-22: a round about new information — only the sets holding at least one required signal."""
from math import comb

from quant_platform.research import scan_large


def test_only_the_sets_with_a_required_signal_are_made_and_counted():
    names = ["a", "b", "c", "d", "e"]
    made = [item for _number, chunk in scan_large.blocks(names, [2, 3], holds=("days",), universes=("all",),
                                                          required=["e"]) for item in chunk]
    keys = len(scan_large.keys(("days",), ("all",)))
    assert all("e" in item["factors"] for item in made)
    assert len(made) == (comb(5, 2) - comb(4, 2) + comb(5, 3) - comb(4, 3)) * keys
    assert scan_large.total(names, [2, 3], ("days",), ("all",), ["e"]) == len(made)
    assert scan_large.total(names, [2], ("days",), ("all",)) == comb(5, 2) * keys
