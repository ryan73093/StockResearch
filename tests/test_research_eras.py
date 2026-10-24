"""2026-10-23 (使用者：每個時期同樣的策略是否還適用；哪些前期賺很大、後期不賺): research/eras.py and the data kinds a
rule reads (research/dashboard.py)."""
import json

import pytest

from quant_platform.research import dashboard, eras


def test_a_period_is_the_years_compounded_and_annualized_against_0050():
    yearly = {"2015": {"strategy": 0.10, "benchmark": 0.05, "months": 7}, "2016": {"strategy": 0.20, "benchmark": 0.0, "months": 12},
              "2024": {"strategy": 0.30, "benchmark": 0.60, "months": 12}}
    found = eras.periods(yearly)
    first = found["2015-06～2016"]
    assert first["months"] == 19 and first["rule"] == pytest.approx(1.32 ** (12 / 19) - 1)
    assert first["excess"] == pytest.approx(1.32 ** (12 / 19) - 1.05 ** (12 / 19))
    assert found["2017～2019"]["excess"] is None                            # no year in it
    assert found["2024～2026-09"]["excess"] == pytest.approx(-0.30)
    assert found["late"]["excess"] == pytest.approx(-0.30) and found["early"]["excess"] == first["excess"]


def test_the_summary_counts_the_early_winners_that_still_win():
    def row(early, late):
        periods = {label: {"excess": late if first >= 2020 else early} for label, first, _last in eras.PERIODS}
        periods["early"], periods["late"] = {"excess": early}, {"excess": late}
        return {"periods": periods}
    rows = [row(0.08, 0.06), row(0.07, -0.02), row(0.01, 0.03), row(-0.02, 0.09)]
    found = eras.summarize(rows)
    assert found["early_winners"] == 3 and found["still_winning"] == 2
    assert found["big_early"] == 2 and found["big_still"] == 1 and found["big_faded"] == 1
    assert found["periods"]["2024～2026-09"]["beat"] == pytest.approx(0.75)
    assert eras.spearman([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)


def test_a_rule_reads_its_vetoes_sleeves_and_models_features(tmp_path):
    (tmp_path / "gbm-2.6.0").mkdir()
    (tmp_path / "gbm-2.6.0" / "meta.json").write_text(json.dumps({"features": ["margin_safe", "trend_200"]}), encoding="utf-8")
    spec = {"kind": "blend", "sleeves": [{"rule": {"factors": {"ml_gbm_j": 1.0}}, "share": 0.5},
                                        {"rule": {"factors": {"eps_growth": 1.0}, "exclude": "h2"}, "share": 0.5}]}
    found, versions = dashboard.sources(spec, tmp_path)
    assert versions == {"gbm-2.6.0"}
    assert {"paid", "holders", "statements"} <= found                    # model J's margin, the veto's holders
    plain, none = dashboard.sources({"factors": {"trend_200": 1.0}}, tmp_path)
    assert plain == set() and none == set()


def test_a_rule_trading_the_next_session_counts_and_the_short_track_does_not(tmp_path, monkeypatch):
    """2026-10-24: the first version left out every rule starting 2015-06-02 (the next session's trades) as if it were
    the short track; the short track is daily.short_track (2020-10-26 on)."""
    from quant_platform.research import pool

    (tmp_path / "reports").mkdir()
    curve = {"2015-06": [300_000, 300_000, 300_000], "2015-12": [330_000, 310_000, 300_000],
             "2021-06": [500_000, 400_000, 300_000], "2021-12": [600_000, 420_000, 300_000]}
    for name in ("a", "b"):
        (tmp_path / "reports" / f"{name}.json").write_text(json.dumps({"curve": curve}), encoding="utf-8")
    rows = [{"spec_hash": "a", "name": "a", "tier": "T0 候選", "report_file": "a.json", "start": "2015-06-02",
             "category": {"family": "深度學習"}, "final_value": 1.0},
            {"spec_hash": "b", "name": "b", "tier": "T3", "report_file": "b.json", "start": "2021-07-05",
             "category": {"family": "籌碼"}, "final_value": 1.0}]
    monkeypatch.setattr(pool, "pool_view", lambda research, basis="seed": {"daily": rows})
    eras._CACHE.clear()
    found = eras.analyze(tmp_path)
    assert [row["spec_hash"] for row in found["rows"]] == ["a"]
