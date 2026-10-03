"""The candidate pool: every rule ever tried, where it stands and why (研究選手池, 2026-10-03).

Built from the append-only records — the trial registry (ETF rules and stock rules), the
promotion ledger, the AI researcher's journal and the latest statistics — so the research page,
its JSON twin (for an assistant that answers questions from the website) and the weekly report
all describe the same state. Nothing here is written; the sources are.
"""

from __future__ import annotations

import json
from pathlib import Path

from quant_platform.research.promotion import PromotionLedger, STAGE_LABELS, TRACK_LABELS, drawdown_gate, window_gate
from quant_platform.research.registry import TrialRegistry, current_basis, distinct_rules
from quant_platform.research.reports import latest_stats

BASES = {"lump_sum": "LumpSumPlan", "dca": "ContributionPlan"}
BASIS_LABELS = {"lump_sum": "一次投入 30 萬、獲利再投入，對同樣 30 萬放 0050", "dca": "每月 5 日投入 1 萬，對 0050 定期定額"}
STATUS_ORDER = (
    "approved", "forward", "holdout_passed", "validation_passed", "holdout_failed", "validation_failed",
    "window_ok", "eliminated",
)
STATUS_LABELS = {
    "approved": "已核准", "forward": "前向模擬中", "holdout_passed": "最終驗證期通過", "validation_passed": "驗證期通過",
    "holdout_failed": "最終驗證期未通過", "validation_failed": "驗證期未通過",
    "window_ok": "開發期通過視窗、待驗證", "eliminated": "開發期淘汰",
}


def _window(metrics: dict, key: str) -> dict:
    return (metrics.get("windows") or {}).get(key) or {}


def _gate(metrics: dict) -> list[str]:
    return window_gate(metrics) + drawdown_gate(metrics)


def _activity(metrics: dict) -> dict[str, object]:
    """Fees and tax against the money put in (R3). Runs before 2026-10-03 lack ``contributed``; with
    the standard plan it is NT$10,000 for every month from the first contribution to the end."""
    contributed = metrics.get("contributed")
    plan = metrics.get("plan") or {}
    if contributed is None and metrics.get("start") and metrics.get("end") and plan.get("kind", "ContributionPlan") == "ContributionPlan":
        start, end = str(metrics["start"]), str(metrics["end"])
        months = (int(end[:4]) * 12 + int(end[5:7])) - (int(start[:4]) * 12 + int(start[5:7])) + 1
        contributed = months * float(plan.get("monthly_amount") or 10_000)
    costs = metrics.get("costs")
    share = metrics.get("cost_share")
    if share is None and costs is not None and contributed:
        share = round(costs / contributed, 6)
    return {"cost_share": share, "turnover": metrics.get("turnover"), "orders_per_month": metrics.get("orders_per_month")}


def _sources(research_dir: Path) -> dict[str, str]:
    """spec_hash → where the rule came from (批次名稱 or AI 研究員)."""
    from quant_platform.research.batches import BATCHES
    from quant_platform.research.stock_rules import BATCHES as STOCK_BATCHES

    origin: dict[str, str] = {}
    for name, batch in STOCK_BATCHES.items():
        for rule in batch():
            origin.setdefault(rule.rule_hash, f"個股批次 {name}")
    for name, batch in BATCHES.items():
        for spec in batch():
            origin.setdefault(spec.spec_hash, f"規則批次 {name}")
    journal = research_dir / "journal.jsonl"
    if journal.is_file():
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            for item in entry.get("accepted") or []:
                if item.get("spec_hash"):
                    origin.setdefault(item["spec_hash"], f"AI 研究員 {entry.get('round_id', '')}")
    return origin


def _best_records(records: list, period: str, plan_kind: str = "ContributionPlan") -> dict[str, object]:
    """One record per rule for the period and cash flow (monthly or lump sum): the newest that has
    rolling windows, else the newest. Lump-sum runs before engine 1.2.0 had monthly windows: skipped."""
    chosen: dict[str, object] = {}
    for record in records:
        if record.kind != "candidate" or record.period != period:
            continue
        if (record.metrics.get("plan") or {}).get("kind", "ContributionPlan") != plan_kind:
            continue
        if plan_kind == "LumpSumPlan" and str(record.metrics.get("engine") or "") < "stocks-1.2.0":
            continue
        current = chosen.get(record.spec_hash)
        has_windows = bool(_window(record.metrics, "3y").get("count"))
        if current is None or has_windows >= bool(_window(current.metrics, "3y").get("count")):
            chosen[record.spec_hash] = record
    return chosen


def rule_rows(research_dir: str | Path, basis: str = "lump_sum") -> list[dict[str, object]]:
    base = Path(research_dir)
    kind = BASES[basis]
    registry = TrialRegistry(base / "trials.jsonl")
    records = registry.records()
    ledger = PromotionLedger(base / "promotions.jsonl")
    origin = _sources(base)
    dsr = {}
    for family in ("development", "stocks-development"):
        stats = latest_stats(base / "stats", family) or {}
        dsr.update({item["trial_id"]: item["dsr"]["deflated_sharpe"] for item in stats.get("candidates") or []})
    from quant_platform.research.stock_forward import StockForwardTracker

    forward_since = {item["rule_hash"]: item["since"] for item in StockForwardTracker(base).tracked()}
    etf_current, _older = current_basis(records, "development")
    etf_basis = {record.spec_hash for record in etf_current}
    development = _best_records(records, "development", kind)
    validation = _best_records(records, "validation", kind)
    holdout = _best_records(records, "holdout", kind)
    rows = []
    for spec_hash, dev in development.items():
        stock = dev.data_fingerprint.startswith("stocks:")
        if not stock and spec_hash not in etf_basis:
            continue  # ETF rules whose result is from a superseded data basis and never re-run
        dev_gate = _gate(dev.metrics)
        val = validation.get(spec_hash)
        val_gate = _gate(val.metrics) if val else None
        hold = holdout.get(spec_hash)
        hold_gate = _gate(hold.metrics) if hold else None
        stage, outcome = ledger.state(spec_hash)
        if stage == "approved":
            status = "approved"
        elif stage == "forward":
            status = "forward"
        elif dev_gate:
            status = "eliminated"          # a later-period run of an eliminated rule stays an illustration
        elif hold is not None:
            status = "holdout_passed" if not hold_gate else "holdout_failed"
        elif val is not None:
            status = "validation_passed" if not val_gate else "validation_failed"
        else:
            status = "window_ok"
        three, five = _window(dev.metrics, "3y"), _window(dev.metrics, "5y")
        rows.append({
            "spec_hash": spec_hash, "name": dev.spec_name, "family": "個股規則" if stock else "ETF 規則",
            "source": origin.get(spec_hash, "其他"), "status": status, "status_label": STATUS_LABELS[status],
            "track": TRACK_LABELS.get(ledger.track(spec_hash), "一般") if stage else None,
            "stage_label": STAGE_LABELS.get(stage, stage) if stage else None, "outcome": outcome,
            "forward_since": forward_since.get(spec_hash),
            "development": {
                "trial_id": dev.trial_id, "xirr": dev.metrics.get("xirr"), "benchmark_xirr": dev.metrics.get("benchmark_xirr"),
                "final_value": dev.metrics.get("final_value"), "benchmark_final_value": dev.metrics.get("benchmark_final_value"),
                "max_drawdown": dev.metrics.get("max_drawdown"),
                "benchmark_max_drawdown": dev.metrics.get("benchmark_max_drawdown"),
                "excess": dev.metrics.get("full_period_excess"),
                "win_3y": three.get("win_ratio"), "median_3y": three.get("median_excess"), "worst_3y": three.get("worst_excess"),
                "win_5y": five.get("win_ratio"), "median_5y": five.get("median_excess"),
                "costs": dev.metrics.get("costs"), "trades": dev.metrics.get("trades"),
                **_activity(dev.metrics),
                "dsr": dsr.get(dev.trial_id), "reasons": dev_gate, "report": dev.report_file,
            },
            "validation": None if val is None else {
                "trial_id": val.trial_id, "xirr": val.metrics.get("xirr"), "benchmark_xirr": val.metrics.get("benchmark_xirr"),
                "final_value": val.metrics.get("final_value"), "benchmark_final_value": val.metrics.get("benchmark_final_value"),
                "max_drawdown": val.metrics.get("max_drawdown"), "excess": val.metrics.get("full_period_excess"),
                "win_3y": _window(val.metrics, "3y").get("win_ratio"),
                "median_3y": _window(val.metrics, "3y").get("median_excess"),
                "worst_3y": _window(val.metrics, "3y").get("worst_excess"),
                **_activity(val.metrics),
                "reasons": val_gate, "report": val.report_file,
            },
            "holdout": None if hold is None else {
                "trial_id": hold.trial_id, "xirr": hold.metrics.get("xirr"), "excess": hold.metrics.get("full_period_excess"),
                "benchmark_xirr": hold.metrics.get("benchmark_xirr"), "final_value": hold.metrics.get("final_value"),
                "benchmark_final_value": hold.metrics.get("benchmark_final_value"),
                "win_3y": _window(hold.metrics, "3y").get("win_ratio"), "reasons": hold_gate, "report": hold.report_file,
            },
        })
    rows.sort(key=lambda row: (STATUS_ORDER.index(row["status"]), -(row["development"]["median_3y"] or -9)))
    return rows


def ai_rounds(research_dir: str | Path) -> list[dict[str, object]]:
    journal = Path(research_dir) / "journal.jsonl"
    if not journal.is_file():
        return []
    rounds = []
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        rounds.append({
            "round_id": entry.get("round_id"), "at": str(entry.get("at") or entry.get("started_at") or "")[:16],
            "status": entry.get("status"), "hypothesis": entry.get("hypothesis"), "analysis": entry.get("analysis"),
            "accepted": [item.get("name") for item in entry.get("accepted") or []],
            "rejected": [f"{item.get('name')}：{item.get('reason')}" for item in entry.get("rejected") or []],
            "cost_usd": (entry.get("budget") or {}).get("spent_usd"),
        })
    return list(reversed(rounds))


def pool_view(research_dir: str | Path, top: int = 20, basis: str = "lump_sum") -> dict[str, object]:
    """What the pool page shows: counts, the lists an owner asks about, and every rule, on one basis
    (lump sum, the owner's strategy account since 2026-10-04, or the monthly plan)."""
    rows = rule_rows(research_dir, basis)
    counts = {status: sum(1 for row in rows if row["status"] == status) for status in STATUS_ORDER}
    families = {family: sum(1 for row in rows if row["family"] == family) for family in ("ETF 規則", "個股規則")}
    ranked = [row for row in rows if row["development"]["win_3y"] is not None]
    high_win = sorted(ranked, key=lambda row: (-row["development"]["win_3y"], -(row["development"]["median_3y"] or 0)))[:top]
    both = [row for row in rows if row["validation"] and not row["development"]["reasons"] and not row["validation"]["reasons"]]
    from quant_platform.research.stock_forward import StockForwardTracker

    stats = latest_stats(Path(research_dir) / "stats", "development") or {}
    stock_stats = latest_stats(Path(research_dir) / "stats", "stocks-development") or {}
    attempts = distinct_rules([record for record in TrialRegistry(Path(research_dir) / "trials.jsonl").records()
                               if record.kind == "candidate" and record.period == "development"])
    best_dsr = max((item["dsr"]["deflated_sharpe"] or 0, item["name"])
                   for item in (stats.get("candidates") or []) + (stock_stats.get("candidates") or [])
                   or [{"dsr": {"deflated_sharpe": 0}, "name": ""}])
    return {
        "rules": rows, "counts": counts, "families": families, "total": len(rows),
        "attempts": attempts, "best_dsr": best_dsr, "pbo": (stats.get("pbo") or {}).get("pbo"),
        "stock_pbo": (stock_stats.get("pbo") or {}).get("pbo"),
        "high_win": high_win, "both_periods": both, "ai_rounds": ai_rounds(research_dir),
        "forward_stocks": StockForwardTracker(research_dir).summary(),
        "status_labels": STATUS_LABELS, "basis": basis, "basis_label": BASIS_LABELS[basis], "bases": BASIS_LABELS,
    }
