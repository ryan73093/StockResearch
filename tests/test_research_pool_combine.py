"""2026-11-14 (使用者：回測加前向綜合評估 但前向權重較重, D-045): the score the best rules are ordered by."""
from quant_platform.research import pool


def _row(key, tier="T0 候選", since=0.5, uses=False):
    return {"spec_hash": key, "tier": tier, "since_2020_excess": since, "uses_0050": uses}


def test_the_forward_record_weighs_more_than_the_backtest():
    rows = [_row("back", since=0.9), _row("front", since=0.1), _row("mid", since=0.5)]
    chained = {"back": {"sessions": 40, "excess": -0.05, "sold_excess": -0.04},
               "front": {"sessions": 40, "excess": 0.03, "sold_excess": 0.02},
               "mid": {"sessions": 40, "excess": 0.0, "sold_excess": 0.01}}
    pool.combine(rows, chained)
    score = {row["spec_hash"]: row["combined"] for row in rows}
    assert score == {"back": 0.3, "front": 0.7, "mid": 0.5}
    assert rows[1]["chain"] == {"sessions": 40, "excess": 0.03, "sold_excess": 0.02}


def test_the_worse_of_the_two_moneys_is_the_forward_part():
    rows = [_row("a"), _row("b")]
    pool.combine(rows, {"a": {"sessions": 9, "excess": 0.10, "sold_excess": -0.02},
                        "b": {"sessions": 9, "excess": 0.01, "sold_excess": 0.01}})
    assert rows[1]["combined"] > rows[0]["combined"]


def test_a_rule_with_no_chained_record_has_only_its_backtest_part():
    rows = [_row("new", since=0.9), _row("old", since=0.1), _row("t1", tier="T1", since=0.5),
            _row("lost", tier="T3"), _row("etf", uses=True)]
    pool.combine(rows, {"old": {"sessions": 3, "excess": -0.2}})
    by = {row["spec_hash"]: row for row in rows}
    assert by["new"]["combined"] == 0.3 and "chain" not in by["new"]
    assert by["old"]["combined"] == 0.7 and by["old"]["chain"]["sold_excess"] == -0.2
    assert by["t1"]["combined"] == 0.15
    assert "combined" not in by["lost"] and "combined" not in by["etf"]


def test_equal_values_share_a_place():
    assert pool._places({"a": 1.0, "b": 1.0, "c": 2.0}) == {"a": 0.25, "b": 0.25, "c": 1.0}
    assert pool._places({"a": 1.0}) == {"a": 1.0}
