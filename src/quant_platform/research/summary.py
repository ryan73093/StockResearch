"""Plain-language summary of a research round (S4-W05).

Groups the candidate trials of a period by research direction (the part of
the spec name before "："), shows the best configuration of each group and
how many pass the development-period part of the promotion gate, and states
a verdict that respects the multiple-testing results. When nothing passes,
the summary says so.
"""

from __future__ import annotations

from pathlib import Path

from quant_platform.research.registry import TrialRegistry, current_basis

WIN_RATIO_GATE = 0.60
DSR_GATE = 0.95
PBO_GATE = 0.20


def _direction(name: str) -> str:
    head = name.split("：", 1)[0]
    for prefix in ("均線", "回撤"):
        if head.startswith(prefix):
            return prefix
    return head


def _three_year(record) -> dict[str, object]:
    return (record.metrics.get("windows") or {}).get("3y") or {}


def passes_development_gate(record) -> bool:
    window = _three_year(record)
    win, median = window.get("win_ratio"), window.get("median_excess")
    return win is not None and median is not None and win >= WIN_RATIO_GATE and median > 0


def round_summary(registry_path: str | Path, period: str, stats: dict | None) -> dict[str, object] | None:
    records, older = current_basis(TrialRegistry(registry_path).records(), period)
    if not records:
        return None
    if stats and stats.get("basis") != records[0].data_fingerprint:
        stats = None  # computed on another data basis; the verdict waits for a new run
    groups: dict[str, list] = {}
    for record in records:
        groups.setdefault(_direction(record.spec_name), []).append(record)
    rows = []
    for direction, items in groups.items():
        best = max(items, key=lambda record: _three_year(record).get("median_excess") or float("-inf"))
        window = _three_year(best)
        rows.append({
            "direction": direction,
            "trials": len(items),
            "passing": sum(1 for record in items if passes_development_gate(record)),
            "best": best.spec_name,
            "best_win_ratio": window.get("win_ratio"),
            "best_median": window.get("median_excess"),
            "best_worst": window.get("worst_excess"),
        })
    rows.sort(key=lambda row: row["best_median"] if row["best_median"] is not None else float("-inf"), reverse=True)
    passing = [record for record in records if passes_development_gate(record)]
    best_dsr = None
    pbo = None
    if stats:
        values = [item["dsr"]["deflated_sharpe"] for item in stats.get("candidates") or [] if item["dsr"]["deflated_sharpe"] == item["dsr"]["deflated_sharpe"]]
        best_dsr = max(values) if values else None
        pbo = (stats.get("pbo") or {}).get("pbo")
    attempts = (stats or {}).get("trials") or len(records) + len(older)
    reasons = []
    if not passing:
        reasons.append(f"沒有設定在開發期同時達到 3 年勝率 ≥ {WIN_RATIO_GATE:.0%} 且中位超額 > 0")
    if best_dsr is not None and best_dsr < DSR_GATE:
        reasons.append(f"扣除 {attempts} 次試驗的多重檢定後，最佳 DSR {best_dsr:.2f} 未達 {DSR_GATE}")
    if pbo is not None and pbo > PBO_GATE:
        reasons.append(f"過度擬合機率 PBO {pbo:.0%} 高於 {PBO_GATE:.0%}")
    if stats is None:
        verdict = "統計檢定尚未完成，結論待定。"
    elif reasons:
        verdict = "目前沒有設定能證明勝過定期定額：" + "；".join(reasons) + "。"
    else:
        verdict = f"{len(passing)} 個設定通過開發期門檻與檢定，下一步進驗證期（2017–2021）。"
    return {
        "period": period,
        "trials": len(records),
        "older_trials": len(older),
        "attempts": attempts,
        "passing": len(passing),
        "rows": rows,
        "best_dsr": best_dsr,
        "pbo": pbo,
        "verdict": verdict,
        "gates": {"win_ratio": WIN_RATIO_GATE, "dsr": DSR_GATE, "pbo": PBO_GATE},
    }
