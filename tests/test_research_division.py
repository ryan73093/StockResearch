"""2026-10-20: the division of labour on the strategy roles page (research/division.py)."""
import pandas as pd

from quant_platform.research import division, registry, stock_forward


def test_the_division_by_market_state_and_the_holdings_in_common(monkeypatch, tmp_path):
    (tmp_path / "trials.jsonl").write_text("", encoding="utf-8")

    class Record:
        def __init__(self, trial):
            self.trial_id, self.report_file, self.spec_hash, self.metrics = trial, f"{trial}.json", f"h{trial}", {}

    class Registry:
        def __init__(self, path):
            pass

        def records(self):
            return [Record(trial) for _name, trial, _job in division.REPRESENTATIVES[:2]]

    months = [f"2020-{month:02d}" for month in range(1, 13)] + [f"2021-{month:02d}" for month in range(1, 13)]
    bench = pd.Series([0.05, -0.05, 0.0] * 8, index=months)

    def months_of(_research, report_file):
        attack = report_file.startswith("4941")                  # the attacker doubles 0050's moves, the other halves them
        return pd.DataFrame({"rule": bench * (2 if attack else 0.5), "bench": bench})

    class Tracker:
        def __init__(self, research):
            pass

        def summary(self):
            return [{"rule_hash": "h4941", "stopped": None, "holdings": [{"code": "2330"}, {"code": "2317"}]},
                    {"rule_hash": "h5040", "stopped": None, "holdings": [{"code": "2330"}]}]

    monkeypatch.setattr(registry, "TrialRegistry", Registry)
    monkeypatch.setattr(division, "_months", months_of)
    monkeypatch.setattr(stock_forward, "StockForwardTracker", Tracker)
    division._cache.clear()
    found = division.diagnose(tmp_path)
    assert [role["name"] for role in found["roles"]] == ["深度學習", "機器學習"]
    up, flat, down = found["states"][:3]
    assert up["months"] == down["months"] == flat["months"] == 8
    assert up["cells"]["深度學習"]["mean"] == 0.05 and up["cells"]["機器學習"]["mean"] == -0.025
    assert down["cells"]["深度學習"]["mean"] == -0.05 and down["cells"]["機器學習"]["mean"] == 0.025
    assert down["cells"]["機器學習"]["win"] == 1.0
    assert [row["state"].split("・")[0] for row in found["known"]] == ["開發期 2015-06～2020-09"] * 2 + ["2020-10 起"] * 2
    assert found["overlap"][0]["values"]["機器學習"] == 1 and found["overlap"][0]["count"] == 2
