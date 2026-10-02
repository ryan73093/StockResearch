"""Significance of candidate trials in one period (S4-W03).

The number of trials in the Deflated Sharpe Ratio is the number of different
specs ever registered for the period (registry.distinct_rules): failures
count, a rule that only ran on an older data basis counts, and a rule re-run
after the data changed counts once (owner's decision 2026-10-02). The DSR of
each candidate and the PBO use one record per spec on the current data basis
(registry.one_per_rule): results on corrected-away data are not evidence.
PBO compares the candidates' monthly active returns on the months they share.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from quant_platform.research.registry import TrialRegistry, current_basis, distinct_rules, one_per_rule
from quant_platform.research.statistics import (
    block_bootstrap_mean,
    deflated_sharpe,
    probability_of_backtest_overfitting,
    sharpe,
)


def save_stats(report: dict[str, object], stats_dir: str | Path, period: str, now) -> Path:
    """Write ``<period>-<Taipei time>.json`` (reports.latest_stats reads the newest)."""
    from zoneinfo import ZoneInfo

    folder = Path(stats_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{period}-{now.astimezone(ZoneInfo('Asia/Taipei')):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def significance(
    registry: TrialRegistry, reports_dir: str | Path, period: str, fingerprint: str | None = None
) -> dict[str, object]:
    current, older = current_basis(registry.records(), period, fingerprint=fingerprint)
    attempts = distinct_rules(current + older)  # every different rule ever tried in this period
    series: dict[int, dict[str, float]] = {}
    names: dict[int, str] = {}
    for record in one_per_rule(current):
        path = Path(reports_dir) / record.report_file
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        months = report.get("monthly_active_returns") or {}
        if months:
            series[record.trial_id] = months
            names[record.trial_id] = record.spec_name
    output: dict[str, object] = {
        "period": period,
        "trials": attempts,
        "counting": "spec_hash",
        "records": len(current) + len(older),
        "current_trials": len(current),
        "older_trials": len(older),
        "basis": current[0].data_fingerprint if current else fingerprint,
        "candidates": [],
    }
    if not series:
        return output
    sharpes = [sharpe(list(values.values())) for values in series.values()]
    variance = float(np.var(sharpes, ddof=1)) if len(sharpes) > 1 else 0.0
    for trial_id, values in series.items():
        returns = list(values.values())
        output["candidates"].append({
            "trial_id": trial_id,
            "name": names[trial_id],
            "months": len(returns),
            "dsr": deflated_sharpe(returns, n_trials=attempts, sharpe_variance=variance),
            "bootstrap": block_bootstrap_mean(returns),
        })
    common = sorted(set.intersection(*(set(values) for values in series.values())))
    if len(series) >= 2 and len(common) >= 16:
        matrix = np.array([[series[trial_id][month] for trial_id in series] for month in common])
        output["pbo"] = probability_of_backtest_overfitting(matrix, blocks=16)
        output["pbo"]["months"] = len(common)
    return output
