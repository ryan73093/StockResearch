"""Read saved research reports for the dashboard (S3)."""

from __future__ import annotations

import json
from pathlib import Path


PERIOD_LABELS = {"full": "全期間", "development": "開發期", "validation": "驗證期", "holdout": "最終驗證期"}


def latest_reports(
    directory: str | Path, limit: int = 10, period: str | None = None, kind: str | None = None
) -> list[dict[str, object]]:
    """Newest report per strategy spec and period, newest first; unreadable files are skipped.

    ``period`` and ``kind`` filter before ``limit`` applies, so a large batch of
    candidate reports cannot push the baselines out."""
    folder = Path(directory)
    if not folder.is_dir():
        return []
    latest: dict[tuple[str, str], dict[str, object]] = {}
    for path in folder.glob(f"{period}-*.json" if period else "*.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            key = (report["strategy"]["spec_hash"], str(report.get("period", "full")))
            stamp = str(report.get("generated_at", ""))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if (period and key[1] != period) or (kind and report.get("kind", "baseline") != kind):
            continue
        if key not in latest or stamp > str(latest[key].get("generated_at", "")):
            latest[key] = {**report, "file": path.name}
    ordered = sorted(latest.values(), key=lambda item: str(item.get("generated_at", "")), reverse=True)
    return ordered[:limit]


def trial_ranking(registry_path: str | Path, period: str, limit: int = 10) -> dict[str, object]:
    """Candidate trials of one period on the current data basis, ranked by the 3-year median excess."""
    from quant_platform.research.registry import TrialRegistry, current_basis

    registry = TrialRegistry(registry_path)
    records, older = current_basis(registry.records(), period)

    def score(record) -> float:
        window = (record.metrics.get("windows") or {}).get("3y") or {}
        value = window.get("median_excess")
        return float(value) if value is not None else float("-inf")

    ranked = sorted(records, key=score, reverse=True)[:limit]
    return {
        "period": period,
        "period_label": PERIOD_LABELS.get(period, period),
        "total": len(records),
        "older": len(older),
        "basis": records[0].data_fingerprint[:12] if records else None,
        "rows": [
            {
                "trial_id": record.trial_id,
                "name": record.spec_name,
                "excess": record.metrics.get("full_period_excess"),
                "xirr": record.metrics.get("xirr"),
                "benchmark_xirr": record.metrics.get("benchmark_xirr"),
                "drawdown": record.metrics.get("max_drawdown"),
                "three_year": (record.metrics.get("windows") or {}).get("3y"),
                "five_year": (record.metrics.get("windows") or {}).get("5y"),
            }
            for record in ranked
        ],
        "chain_ok": not registry.verify(),
    }


def stock_rule_rows(registry_path: str | Path, period: str, plan_kind: str = "SeedPlan") -> list[dict[str, object]]:
    """Stock-rule trials of one period and cash flow (research/stock_rules.py), best 3-year median
    first. The owner's strategy account is a lump sum (2026-10-04); runs before engine 1.2.0 had monthly
    windows and are left out of the lump-sum list."""
    from quant_platform.research.pool import _activity
    from quant_platform.research.registry import TrialRegistry

    records = [record for record in TrialRegistry(registry_path).records()
               if record.kind == "candidate" and record.period == period
               and record.data_fingerprint.startswith("stocks:")
               and (record.metrics.get("plan") or {}).get("kind", "ContributionPlan") == plan_kind
               and (plan_kind != "LumpSumPlan" or str(record.metrics.get("engine") or "") >= "stocks-1.2.0")]
    latest: dict[str, object] = {}
    for record in records:                       # one row per rule: its newest run
        latest[record.spec_hash] = record

    def score(record) -> float:
        value = ((record.metrics.get("windows") or {}).get("3y") or {}).get("median_excess")
        return float(value) if value is not None else float("-inf")

    return [
        {
            "trial_id": record.trial_id, "name": record.spec_name, "excess": record.metrics.get("full_period_excess"),
            "xirr": record.metrics.get("xirr"), "benchmark_xirr": record.metrics.get("benchmark_xirr"),
            "drawdown": record.metrics.get("max_drawdown"), "benchmark_drawdown": record.metrics.get("benchmark_max_drawdown"),
            "costs": record.metrics.get("costs"), "trades": record.metrics.get("trades"),
            "cost_share": _activity(record.metrics)["cost_share"],
            "final_value": record.metrics.get("final_value"), "benchmark_final_value": record.metrics.get("benchmark_final_value"),
            "three_year": (record.metrics.get("windows") or {}).get("3y"),
            "five_year": (record.metrics.get("windows") or {}).get("5y"),
        }
        for record in sorted(latest.values(), key=score, reverse=True)
    ]


def latest_stats(stats_dir: str | Path, period: str) -> dict[str, object] | None:
    folder = Path(stats_dir)
    files = sorted(folder.glob(f"{period}-*.json")) if folder.is_dir() else []
    for path in reversed(files):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def report_rows(reports: list[dict[str, object]]) -> list[dict[str, object]]:
    """Flatten reports into the values the research page shows."""
    rows = []
    for report in reports:
        strategy, benchmark = report["strategy"], report["benchmark"]
        windows = report.get("windows") or {}

        def window(key: str) -> dict[str, object] | None:
            item = windows.get(key) or {}
            return item if item.get("count") else None

        rows.append({
            "name": strategy["spec"],
            "period_label": PERIOD_LABELS.get(str(report.get("period", "full")), str(report.get("period"))),
            "kind": report.get("kind", "baseline"),
            "period": f"{strategy['start']}～{strategy['end']}",
            "contributed": strategy["total_contributed"],
            "final": strategy["final_value"],
            "benchmark_final": benchmark["final_value"],
            "excess": report.get("full_period_excess"),
            "xirr": strategy["xirr"],
            "benchmark_xirr": benchmark["xirr"],
            "drawdown": strategy["max_drawdown"],
            "benchmark_drawdown": benchmark["max_drawdown"],
            "costs": strategy["fees"] + strategy["taxes"],
            "trades": strategy["trades"],
            "three_year": window("3y"),
            "five_year": window("5y"),
            "generated_at": report.get("generated_at"),
            "report_hash": str(report.get("report_hash", ""))[:12],
            "monthly": (report.get("plan") or {}).get("monthly_amount"),
        })
    return rows
