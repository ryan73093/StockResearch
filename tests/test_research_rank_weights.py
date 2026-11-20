"""2026-11-20 (使用者：繼續，還有什麼): picks weighted by their place in the deciding session's ranking."""
from datetime import date
from types import SimpleNamespace

import pytest

from quant_platform.research import daily
from quant_platform.research.daily import DailyRule


def stub(rankings, risk_on=True):
    days = sorted(rankings)
    return SimpleNamespace(index={day: number for number, day in enumerate(days)},
                           ranked=lambda rule, position: rankings[days[position]],
                           risk_on=lambda position, *args: risk_on)


def test_the_best_ranked_pick_gets_twice_the_last_in_a_straight_line():
    day = date(2026, 11, 20)
    fp = stub({day: ["A", "B", "C", "D", "E"]})
    rule = DailyRule(name="t", factors={"momentum_3": 1.0}, weighting="rank")
    weights = daily.daily_weights(fp, rule, {day: ["C", "A", "E"]})[day]           # held in another order
    assert weights["A"] == pytest.approx(2 / 4.5) and weights["C"] == pytest.approx(1.5 / 4.5)
    assert weights["E"] == pytest.approx(1 / 4.5) and sum(weights.values()) == pytest.approx(1.0)
    assert weights["A"] / weights["E"] == pytest.approx(daily.RANK_TILT)


def test_a_holding_outside_the_ranking_counts_as_the_last_and_one_pick_takes_all():
    day = date(2026, 11, 20)
    fp = stub({day: ["A", "B"]})
    rule = DailyRule(name="t", factors={"momentum_3": 1.0}, weighting="rank")
    weights = daily.daily_weights(fp, rule, {day: ["X", "B"]})[day]
    assert weights["B"] == pytest.approx(2 / 3) and weights["X"] == pytest.approx(1 / 3)
    assert daily.daily_weights(fp, rule, {day: ["B"]})[day] == {"B": 1.0}


def test_as_known_on_the_deciding_session_when_filled_the_next():
    first, second = date(2026, 11, 19), date(2026, 11, 20)
    fp = stub({first: ["A", "B"], second: ["B", "A"]})
    rule = DailyRule(name="t", factors={"momentum_3": 1.0}, weighting="rank", execution="next")
    weights = daily.daily_weights(fp, rule, {second: ["A", "B"]})[second]
    assert weights["A"] > weights["B"]                                              # the 11/19 ranking


def test_the_rank_weighting_is_named():
    from quant_platform.research.categories import classify

    spec = DailyRule(name="t", factors={"momentum_3": 1.0}, weighting="rank").canonical()
    assert "依分數高低配錢" in str(classify(spec))
