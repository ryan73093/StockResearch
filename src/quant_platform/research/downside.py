"""2026-10-18 (使用者：回撤不想比 0050 大，是為了避免市場不好時比 0050 跌得多，讓投資人失去信心、亂了手腳甚至做出錯誤
決策): how each passing rule behaves when 0050 falls — a description, not a trial (nothing is chosen with it).

From each rule's registered report, its month-end values and 0050's on the same money (30 萬 then 1 萬 on the 5th),
turned into month-by-month returns net of the month's contribution:

- the months 0050 fell: the rule's average return in them against 0050's, and the share of them the rule fell more;
- every stretch 0050 fell 10% or more from a high (high to low, month ends): the rule's change over the same months
  against 0050's — the worst of these (most points behind 0050);
- the deepest the rule fell behind 0050: the largest drop of its value relative to 0050's (rule ÷ 0050, from its best
  to its worst), i.e. how far behind an investor comparing with 0050 would have felt at the worst point.

Month ends only (the reports keep months); a fall inside a month and back by its end is not seen.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

FALL = 0.10                 # a stretch of 0050 falling at least this much from a high counts as a bad market
# 2026-10-18 (使用者：1；前面不是說要備註高風險嗎): a rule whose worst such stretch fell more than this much further
# than 0050 is marked 高風險 — marked, not left out
RISK_GAP = 0.01
_CACHE: dict[str, object] = {}
_NOTES: dict[str, object] = {}


def returns(curve: dict[str, list[float]]) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Months and the rule's and 0050's month returns net of the month's contribution (the curve: month → [rule's
    value, 0050's value, money put in so far])."""
    months = list(curve)
    rule, base = [], []
    for previous, month in zip(months, months[1:]):
        value, before = curve[month], curve[previous]
        flow = value[2] - before[2]
        rule.append((value[0] - flow) / before[0] - 1 if before[0] else 0.0)
        base.append((value[1] - flow) / before[1] - 1 if before[1] else 0.0)
    return months[1:], np.array(rule), np.array(base)


def falls(base: np.ndarray, depth: float = FALL) -> list[tuple[int, int]]:
    """(high, low) month indexes of every stretch 0050 fell at least ``depth`` from a high (on its unit value)."""
    units = np.concatenate([[1.0], np.cumprod(1 + base)])
    stretches, peak, trough = [], 0, 0
    for index in range(1, len(units)):
        if units[index] >= units[peak]:
            if units[trough] / units[peak] - 1 <= -depth:
                stretches.append((peak, trough))
            peak, trough = index, index
        elif units[index] < units[trough]:
            trough = index
    if units[trough] / units[peak] - 1 <= -depth:
        stretches.append((peak, trough))
    return stretches


def profile(curve: dict[str, list[float]]) -> dict[str, object]:
    months, rule, base = returns(curve)
    if not len(base):
        return {}
    down = base < 0
    rule_units = np.concatenate([[1.0], np.cumprod(1 + rule)])
    base_units = np.concatenate([[1.0], np.cumprod(1 + base)])
    worst = None
    for peak, trough in falls(base):
        gap = (rule_units[trough] / rule_units[peak] - 1) - (base_units[trough] / base_units[peak] - 1)
        label = f"{months[peak - 1] if peak else months[0]}～{months[trough - 1]}"
        if worst is None or gap < worst["gap"]:
            worst = {"gap": round(float(gap), 4), "months": label,
                     "rule": round(float(rule_units[trough] / rule_units[peak] - 1), 4),
                     "base": round(float(base_units[trough] / base_units[peak] - 1), 4)}
    relative = rule_units / base_units
    behind = float((relative / np.maximum.accumulate(relative) - 1).min())
    return {"down_months": int(down.sum()),
            "down_rule": round(float(rule[down].mean()), 4) if down.any() else None,
            "down_base": round(float(base[down].mean()), 4) if down.any() else None,
            "down_worse_share": round(float((rule[down] < base[down]).mean()), 3) if down.any() else None,
            "worst_fall": worst, "most_behind": round(behind, 4)}


def frontier(rows: list[dict], passing: tuple[str, ...]) -> set[str]:
    """2026-10-18 (使用者：繼續研究有什麼更好的策略): the passing rules no other passing rule beats on both — more money
    at the end and a smaller worst gap to 0050 in its falls (at least as good on one, better on the other)."""
    listed = [(row["rule_hash"], row.get("final_value") or 0.0, (row.get("worst_fall") or {}).get("gap", 0.0))
              for row in rows if row.get("tier") in passing]
    return {key for key, money, gap in listed
            if not any(other_money >= money and other_gap >= gap and (other_money > money or other_gap > gap)
                       for other, other_money, other_gap in listed if other != key)}


def notes(research: str | Path) -> dict[str, dict[str, object]]:
    """rule hash → the falling-market mark of every listed rule whose worst 0050 fall it fell more than RISK_GAP
    further than 0050 (its gap and months), kept with the table."""
    rows = table(research)
    key = _CACHE.get("key")
    if _NOTES.get("key") != key:
        _NOTES.update(key=key, notes={row["rule_hash"]: row["worst_fall"] for row in rows
                                     if row.get("worst_fall") and row["worst_fall"]["gap"] < -RISK_GAP})
    return dict(_NOTES.get("notes") or {})


def table(research: str | Path) -> list[dict[str, object]]:
    """Every passing rule (newest engine; T0 候選, T0 候選（高風險）, T0) and the T1 rules, by end value, with its
    profile; kept until the trial registry changes."""
    from quant_platform.research.daily import ENGINE_VERSION, PASSING_TIERS, tier
    from quant_platform.research.registry import TrialRegistry, newest

    research = Path(research)
    path = research / "trials.jsonl"
    if not path.is_file():
        return []
    key = (str(path.resolve()), path.stat().st_mtime_ns)
    if _CACHE.get("key") == key:
        return list(_CACHE["rows"])
    records = [record for record in TrialRegistry(path).records()
               if record.kind == "candidate" and record.period == "recent"
               and str(record.metrics.get("engine", "")).startswith(ENGINE_VERSION.split("+")[0])]
    rows = []
    for record in newest(records).values():
        grade = tier(record.metrics)[0]
        if grade not in PASSING_TIERS and grade != "T1":
            continue
        try:
            report = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found = profile(report.get("curve") or {})
        if not found:
            continue
        rows.append({"trial": record.trial_id, "rule_hash": record.spec_hash, "name": record.spec_name, "tier": grade,
                     "final_value": record.metrics.get("final_value"), "max_drawdown": record.metrics.get("max_drawdown"),
                     "benchmark_max_drawdown": record.metrics.get("benchmark_max_drawdown"), **found})
    rows.sort(key=lambda row: -(row["final_value"] or 0))
    edge = frontier(rows, PASSING_TIERS)
    for row in rows:
        row["frontier"] = row["rule_hash"] in edge
    _CACHE.update(key=key, rows=rows)
    return list(rows)
