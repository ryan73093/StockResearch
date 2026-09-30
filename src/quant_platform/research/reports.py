"""Read saved research reports for the dashboard (S3)."""

from __future__ import annotations

import json
from pathlib import Path


PERIOD_LABELS = {"full": "全期間", "development": "開發期", "validation": "驗證期", "holdout": "保留期"}


def latest_reports(directory: str | Path, limit: int = 10) -> list[dict[str, object]]:
    """Newest report per strategy spec and period, newest first; unreadable files are skipped."""
    folder = Path(directory)
    if not folder.is_dir():
        return []
    latest: dict[tuple[str, str], dict[str, object]] = {}
    for path in folder.glob("*.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            key = (report["strategy"]["spec_hash"], str(report.get("period", "full")))
            stamp = str(report.get("generated_at", ""))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if key not in latest or stamp > str(latest[key].get("generated_at", "")):
            latest[key] = {**report, "file": path.name}
    ordered = sorted(latest.values(), key=lambda item: str(item.get("generated_at", "")), reverse=True)
    return ordered[:limit]


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
