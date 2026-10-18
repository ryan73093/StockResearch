"""2026-10-18 (使用者：市場不好時不要比 0050 跌得多): the falling-market profile by hand."""
import numpy as np
import pytest

from quant_platform.research import downside


def test_the_profile_by_hand():
    # no money added after the start: values are the units
    rule = [100, 110, 88, 99, 120]          # +10%, −20%, +12.5%, +21.2%
    base = [100, 105, 84, 92.4, 100]        # +5%, −20%, +10%, +8.2%
    curve = {f"2020-{month:02d}": [r, b, 100.0] for month, (r, b) in enumerate(zip(rule, base, strict=True), start=1)}
    months, mine, theirs = downside.returns(curve)
    assert months[0] == "2020-02" and mine == pytest.approx([0.1, -0.2, 0.125, 120 / 99 - 1])
    found = downside.profile(curve)
    assert found["down_months"] == 1 and found["down_rule"] == pytest.approx(-0.2) and found["down_worse_share"] == 0.0
    worst = found["worst_fall"]                      # 0050 fell 20% from 2020-02 to 2020-03; the rule fell 20% too
    assert worst["base"] == pytest.approx(-0.2) and worst["gap"] == pytest.approx(0.0) and worst["months"] == "2020-02～2020-03"
    # the rule against 0050: 1.1/1.05 at best, then 0.88/0.84 = the same ratio — never behind
    assert found["most_behind"] == pytest.approx(0.0, abs=1e-9)


def test_falls_need_ten_percent():
    assert downside.falls(np.array([-0.05, -0.04, 0.2])) == []
    assert downside.falls(np.array([0.1, -0.06, -0.06, 0.2])) == [(1, 3)]


def test_a_rule_falling_more_than_a_point_further_than_0050_is_marked(monkeypatch):
    """2026-10-18 (使用者：1；備註高風險): marked, not left out."""
    rows = [{"rule_hash": "calm", "worst_fall": {"gap": -0.005, "months": "x", "rule": -0.2, "base": -0.195}},
            {"rule_hash": "rough", "worst_fall": {"gap": -0.089, "months": "2018-08～2018-12", "rule": -0.222, "base": -0.133}},
            {"rule_hash": "none", "worst_fall": None}]
    monkeypatch.setattr(downside, "table", lambda research: rows)
    downside._CACHE["key"] = ("test",)
    downside._NOTES.clear()
    assert set(downside.notes("anywhere")) == {"rough"}


def test_the_frontier_is_the_passing_rules_nothing_beats_on_both():
    """2026-10-18: more money and a smaller worst gap to 0050 in its falls — the rules nothing beats on both."""
    def row(key, money, gap, tier="T0 候選"):
        return {"rule_hash": key, "final_value": money, "worst_fall": {"gap": gap}, "tier": tier}
    rows = [row("rich", 3_900, -0.098, "T0 候選（高風險）"), row("middle", 2_876, -0.035), row("calm", 2_722, -0.011),
            row("beaten", 2_400, -0.028), row("calmest", 2_000, -0.005), row("t1", 4_200, 0.02, "T1")]
    assert downside.frontier(rows, ("T0 候選", "T0 候選（高風險）")) == {"rich", "middle", "calm", "calmest"}
