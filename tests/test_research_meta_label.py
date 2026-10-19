"""2026-10-20: the meta-label model on the deep model's picks (research/meta_label.py)."""
import json
from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import meta_label
from quant_platform.research.daily import DailyRule, FactorPanel
from quant_platform.research.legacy_challenger import LegacyData
from quant_platform.research.stock_rules import Panel


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def market(days, closes, factors=None):
    return LegacyData(sessions=days, closes=closes,
                      traded_value={symbol: {day: 5_000_000_000.0 for day in series} for symbol, series in closes.items()},
                      factors=factors or {}, predictions={})


def test_the_benchmark_the_labels_and_the_picks_by_hand(monkeypatch):
    days = weekdays(date(2019, 1, 1), date(2020, 6, 30))
    closes = {"1101.TW": {day: 10 * 1.01 ** n for n, day in enumerate(days)},
              "1102.TW": {day: 20.0 for day in days},
              "1103.TW": {day: 30 * 0.99 ** n for n, day in enumerate(days)},
              "0050.TW": {day: 100 * 1.005 ** n for n, day in enumerate(days)}}
    paid = {"0050.TW": {days[10]: 1.02}}                         # a distribution reinvested on its ex-day
    fp = FactorPanel(Panel(market(days, closes, paid)))
    bench = fp.panel.benchmark
    assert bench[9] == pytest.approx(100 * 1.005 ** 9) and bench[10] == pytest.approx(100 * 1.005 ** 10 * 1.02)
    assert "0050.TW" not in fp.symbols
    monkeypatch.setattr(meta_label, "HORIZON", 10)
    rise, flat = fp.symbols.index("1101.TW"), fp.symbols.index("1102.TW")
    pairs = np.array([[20, rise], [20, flat], [len(days) - 5, rise]])
    won, excess = meta_label.labels(fp, pairs)
    assert excess[0] == pytest.approx(1.01 ** 10 - 1.005 ** 10) and won[0] == 1.0
    assert excess[1] == pytest.approx(-(1.005 ** 10 - 1)) and won[1] == 0.0
    assert np.isnan(won[2]) and np.isnan(excess[2])                                # past the data
    monkeypatch.setattr(meta_label, "PRIMARY", "momentum_3")
    monkeypatch.setattr(meta_label, "CANDIDATES", 2)
    eligible = np.ones((len(fp.symbols), len(days)), dtype=bool)
    chosen = meta_label.picks(fp, eligible)
    last = len(days) - 1
    assert chosen[rise, last] and chosen[flat, last] and not chosen[fp.symbols.index("1103.TW"), last]
    assert not chosen[:, 10].any()                                                  # momentum_3 unknown yet


def planted():
    """Sixty stocks; each has a lasting drift and its last 3 months show it, so momentum tells who beats 0050."""
    rng = np.random.default_rng(3)
    days = weekdays(date(2014, 1, 1), date(2018, 12, 31))
    closes = {}
    for number in range(60):
        drift = (number - 30) / 30 * 0.002
        price, series = 20.0, {}
        for day in days:
            if day.month == 1 and day.day <= 7:
                drift = rng.normal(0, 0.002)                    # a new drift every year
            price *= 1 + drift + rng.normal(0, 0.01)
            series[day] = max(price, 10.5)
        closes[f"{1101 + number}.TW"] = series
    closes["0050.TW"] = {day: 100.0 for day in days}
    return FactorPanel(Panel(market(days, closes)))


def test_each_year_learns_only_from_events_ended_before_it_and_the_veto_reads_the_margins(monkeypatch, tmp_path):
    fp = planted()
    monkeypatch.setattr(meta_label, "PRIMARY", "momentum_3")
    monkeypatch.setattr(meta_label, "CANDIDATES", 20)
    monkeypatch.setattr(meta_label, "LEAST", 100)
    monkeypatch.setattr(meta_label, "GATE_YEARS", (2016, 2017))
    snapshot = tmp_path / "gbm-2.2.0"
    snapshot.mkdir()
    (snapshot / "meta.json").write_text(json.dumps({"features": ["momentum_6", "trend_200"]}), encoding="utf-8")
    seen = []
    real = meta_label.labels

    def spy(fp_, pairs):
        seen.append(pairs)
        return real(fp_, pairs)
    monkeypatch.setattr(meta_label, "labels", spy)
    meta = meta_label.train(fp, tmp_path / meta_label.VERSION, years=[2016, 2017, 2018])
    assert set(meta["years"]) == {"2016", "2017", "2018"}
    assert meta["features"][:3] == ["momentum_3", "momentum_6", "trend_200"]
    for year, item in meta["years"].items():
        first = next(index for index, day in enumerate(fp.sessions) if day.year == int(year))
        weekly = seen[0][seen[0][:, 0] % meta_label.STEP == 0]
        learnable = weekly[weekly[:, 0] + meta_label.HORIZON < first]
        assert item["train_events"] <= len(learnable)                               # purged: labels end before the year
        assert item["kept"] + item["vetoed"] == item["events"]
    check = meta["gate"]
    assert check["kept"] == sum(meta["years"][year]["kept"] for year in ("2016", "2017"))
    margins = meta_label.scores(fp, tmp_path / meta_label.VERSION)
    position = next(index for index, day in enumerate(fp.sessions) if day.year == 2017)
    vetoed = {fp.symbols[row] for row in np.flatnonzero(np.isfinite(margins[position]) & (margins[position] < 0))}
    fp.models = tmp_path
    assert fp.meta_vetoed(position) == vetoed
    assert np.isfinite(margins[position]).sum() == 20 and np.isnan(margins[: position - 300]).all()
    assert meta_label.gate(tmp_path)["kept"] == check["kept"]


def test_the_gate_by_hand():
    def year(kept, vetoed, excess_kept, excess_vetoed, won_kept, won_all):
        return {"kept": kept, "vetoed": vetoed, "events": kept + vetoed, "sum_excess_kept": excess_kept * kept,
                "sum_excess_vetoed": excess_vetoed * vetoed, "sum_won_kept": won_kept * kept,
                "sum_won_all": won_all * (kept + vetoed)}

    good = {"version": "meta-1.0.0", "years": {str(y): year(100, 100, 0.03, 0.01, 0.55, 0.50) for y in range(2016, 2021)}}
    assert meta_label.gate_check(good)["passed"]
    close = {"version": "meta-1.0.0", "years": {str(y): year(100, 100, 0.03, 0.025, 0.55, 0.50) for y in range(2016, 2021)}}
    assert not meta_label.gate_check(close)["passed"]                          # vetoed only 0.5 points worse
    flat = {"version": "meta-1.0.0", "years": {str(y): year(100, 100, 0.03, 0.01, 0.51, 0.50) for y in range(2016, 2021)}}
    assert not meta_label.gate_check(flat)["passed"]                           # win rate only 1 point higher
    missing = {"version": "meta-1.0.0", "years": {"2016": year(100, 100, 0.03, 0.01, 0.55, 0.50)}}
    assert meta_label.gate_check(missing)["passed"] is False


def test_the_veto_is_a_rule_option_and_part_of_its_input():
    rule = DailyRule(name="x", factors={"ml_deep2_top": 1.0}, risk_veto="meta1")
    assert rule.canonical()["risk_veto"] == "meta1"
    with pytest.raises(ValueError):
        DailyRule(name="x", factors={"ml_deep2_top": 1.0}, risk_veto="meta9")
