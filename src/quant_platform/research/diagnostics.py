"""2026-10-11 (全面研究「立即採用」第 5 項): crowding, size and overlap diagnostics for the T0 candidates.

Not new trials — the same rules replayed on the owner's account and described:
- where the money sits: the holdings' market-value percentile among the day's eligible stocks, the share of
  the account in the smallest fifth, the holdings' margin-growth percentile (10 of the 25 T0 candidates rank
  on margin growth: a retail-chasing tilt is their likely shared tail risk), TSMC's share;
- a robustness replay with the smallest fifth of eligible stocks (by market value that day) left out of every
  ranking: if a rule's excess over 0050 vanishes, it lives on small caps (Avramov et al.; Research Affiliates);
- how much the T0 candidates hold the same stocks (average weekly Jaccard overlap).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

SMALL_SHARE = 0.2


def _cap_percentiles(fp) -> np.ndarray:
    """symbols × sessions: market-value percentile among the stocks with a market value that day."""
    if "__cap_pct__" not in fp._cache:
        caps = pd.DataFrame(fp.matrix("market_cap").T)                          # sessions × symbols
        fp._cache["__cap_pct__"] = caps.rank(axis=1, pct=True).to_numpy().T
    return fp._cache["__cap_pct__"]


def replay(data, fp, rule, costs, start, end, without_small: bool = False):
    """(run, snapshots) of the rule on the owner's account; with ``without_small`` the smallest fifth by
    market value is not eligible on any day (the rule is unchanged, only the stocks it may rank)."""
    from quant_platform.research.daily import account_parking, daily_rankings, daily_weights, exposure_schedule, simulate_daily
    from quant_platform.research.stock_rules import SeedPlan

    original = fp.eligible
    if without_small:
        percentiles = _cap_percentiles(fp)
        fp.eligible = lambda rule_, position: original(rule_, position) & (np.nan_to_num(percentiles[:, position]) > SMALL_SHARE)
    try:
        ranks = daily_rankings(fp, rule, start, end)
        snapshots: dict = {}
        run = simulate_daily(data, rule, costs, start, end, ranks, plan=SeedPlan(), weights=daily_weights(fp, rule, ranks),
                             parked=account_parking(data, fp, rule, costs), exposure=exposure_schedule(data, fp, rule, costs),
                             snapshots=snapshots)
    finally:
        fp.eligible = original
    return run, snapshots


def describe(data, fp, snapshots: dict) -> dict[str, float]:
    caps = _cap_percentiles(fp)
    margin = fp.matrix("margin_growth_20")
    margin_pct = pd.DataFrame(margin.T).rank(axis=1, pct=True).to_numpy().T
    cap_avg, small, margin_avg, tsmc = [], [], [], []
    for day, (cash, units) in snapshots.items():
        position = fp.index.get(day)
        if position is None:
            continue
        values = {symbol: count * data.closes[symbol].get(day, np.nan) for symbol, count in units.items()}
        values = {symbol: value for symbol, value in values.items() if np.isfinite(value) and symbol in fp.row}
        total = cash + sum(values.values())
        if total <= 0 or not values:
            continue
        rows = np.array([fp.row[symbol] for symbol in values])
        weights = np.array(list(values.values())) / total
        cap = caps[rows, position]
        known = np.isfinite(cap)
        if known.any():
            cap_avg.append(float(np.average(cap[known], weights=weights[known])))
            small.append(float(weights[known & (cap <= SMALL_SHARE)].sum()))
        tilt = margin_pct[rows, position]
        if np.isfinite(tilt).any():
            margin_avg.append(float(np.average(np.nan_to_num(tilt, nan=0.5), weights=weights)))
        tsmc.append(values.get("2330.TW", 0.0) / total)
    mean = lambda items: round(float(np.mean(items)), 4) if items else None   # noqa: E731
    return {"cap_percentile": mean(cap_avg), "smallest_fifth_share": mean(small),
            "margin_growth_percentile": mean(margin_avg), "tsmc_share": mean(tsmc)}


def weekly_holdings(snapshots: dict) -> dict:
    output, seen = {}, set()
    for day in sorted(snapshots):
        week = day.isocalendar()[:2]
        if week in seen:
            continue
        seen.add(week)
        output[week] = {symbol for symbol, count in snapshots[day][1].items() if count > 0}
    return output


def overlap(first: dict, second: dict) -> float | None:
    values = []
    for week in set(first) & set(second):
        union = first[week] | second[week]
        if union:
            values.append(len(first[week] & second[week]) / len(union))
    return round(float(np.mean(values)), 3) if values else None


def run(base: Path, research: Path, costs, trials: list[tuple[int, str, object]], job=None) -> dict:
    from quant_platform.research import daily
    from quant_platform.research.stock_rules import SeedPlan

    data, fp = daily.load(base)
    start, end = daily.RECENT_START, daily.RECENT_END
    bench = daily.simulate_daily(data, None, costs, start, end, plan=SeedPlan())
    bench_2020 = daily.simulate_daily(data, None, costs, daily.REGIME_START, end, plan=SeedPlan())
    rows, holdings = [], {}
    for index, (trial, name, rule) in enumerate(trials):
        if job:
            job.update(done=index, current=name, force=True)
        result, snapshots = replay(data, fp, rule, costs, start, end)
        small_free, _snapshots = replay(data, fp, rule, costs, start, end, without_small=True)
        since, _ = replay(data, fp, rule, costs, daily.REGIME_START, end, without_small=True)
        holdings[trial] = weekly_holdings(snapshots)
        rows.append({"trial": trial, "name": name, **describe(data, fp, snapshots),
                     "excess": round(daily._excess(result, bench), 4),
                     "excess_without_smallest_fifth": round(daily._excess(small_free, bench), 4),
                     "since_2020_without_smallest_fifth": round(daily._excess(since, bench_2020), 4),
                     "max_drawdown": result.summary()["max_drawdown"],
                     "max_drawdown_without_smallest_fifth": small_free.summary()["max_drawdown"]})
    ids = [row["trial"] for row in rows]
    matrix = {str(a): {str(b): overlap(holdings[a], holdings[b]) for b in ids if b != a} for a in ids}
    report = {"built_at": datetime.now().isoformat(timespec="seconds"), "small_share": SMALL_SHARE, "rules": rows,
              "overlap": matrix,
              "average_overlap": round(float(np.nanmean([value for item in matrix.values() for value in item.values()
                                                         if value is not None])), 3) if len(ids) > 1 else None}
    folder = research / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"t0-{datetime.now():%Y%m%d-%H%M%S}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                                     encoding="utf-8")
    return report
