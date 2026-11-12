"""How much trying a refused top-up again the next session changes the results (2026-11-13; a diagnostic, not registered).

The guard's first action in the forward period (TAIEX below its 200-day average on 2026-11-06 only) left the guarded
accounts with cash for days: the half bought back on 11/09 met 121 stocks closing at limit-up, the top-ups of the held
ones were refused — right — and never tried again, although the trading rules say a blocked order is tried again the
next session (docs/research/results.md「2026-11-13 空頭防護第一次在前向動作」). Whether the engine should do it is the
owner's decision: changing it runs the pool again and recomputes the forward accounts. This measures what it would
change, on the rules the forward accounts of the month follow: each rule run twice from 2015-06 to RECENT_END on the
engine's own path (``daily.evaluate``), without and with ``retry_topups``; the run without it is checked against the
result registered for the rule (equal unless the rule's inputs changed after it was registered).
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
FOLDER = "diagnostics"
GUARDED = "taiex_half"


def pick(items: list[dict], per_group: int) -> list[dict]:
    """``per_group`` of the guarded rules and as many of the others, spread evenly over the rule hashes (the same
    choice every time)."""
    chosen = []
    for guarded in (True, False):
        group = sorted((item for item in items if ((item.get("rule") or {}).get("vol_scale") == GUARDED) == guarded),
                       key=lambda item: item["rule_hash"])
        if len(group) <= per_group:
            chosen += group
            continue
        step = len(group) / per_group
        chosen += [group[int(number * step)] for number in range(per_group)]
    return chosen


def candidates(research: Path) -> list[dict]:
    """The month's forward accounts of single daily rules (one per rule), as tracked."""
    tracked = json.loads((Path(research) / "forward" / "stocks" / "tracked.json").read_text(encoding="utf-8"))
    live = [item for item in tracked if not item.get("stopped") and item.get("kind") == "daily"]
    if not live:
        return []
    newest = max(item["since"] for item in live)
    seen, output = set(), []
    for item in live:
        if item["since"] == newest and item["rule_hash"] not in seen:
            seen.add(item["rule_hash"])
            output.append(item)
    return output


def registered(research: Path, hashes: set[str], engine: str) -> dict[str, dict]:
    """The newest result registered on ``engine`` for each rule (period "recent")."""
    from quant_platform.research.registry import TrialRegistry

    found: dict[str, tuple[str, dict]] = {}
    for record in TrialRegistry(Path(research) / "trials.jsonl").records():
        if record.spec_hash not in hashes or record.period != "recent" or record.kind != "candidate":
            continue
        if str(record.metrics.get("engine", "")) != engine:
            continue
        stamp = str(record.created_at)
        if record.spec_hash not in found or stamp > found[record.spec_hash][0]:
            found[record.spec_hash] = (stamp, record.metrics)
    return {key: value[1] for key, value in found.items()}


def _numbers(metrics: dict, tier) -> dict[str, object]:
    return {"counted_value": metrics.get("counted_value"), "sold_value": metrics.get("sold_value"),
            "max_drawdown": metrics.get("max_drawdown"), "since_2020_excess": metrics.get("since_2020_excess"),
            "trades": metrics.get("trades"), "tier": tier(metrics)[0]}


def summarize(rows: list[dict]) -> dict[str, object]:
    """By group (guarded or not): the median change of the money counted and of the drawdown, how many rules gain,
    how many change tier; how many runs without the option equal the registered result."""
    output = {}
    for name, guarded in (("guarded", True), ("plain", False)):
        group = [row for row in rows if row["guarded"] == guarded]
        changes = [row["with"]["counted_value"] / row["without"]["counted_value"] - 1 for row in group
                   if row["with"].get("counted_value") and row["without"].get("counted_value")]
        drawdowns = [row["with"]["max_drawdown"] - row["without"]["max_drawdown"] for row in group
                     if row["with"].get("max_drawdown") is not None and row["without"].get("max_drawdown") is not None]
        output[name] = {
            "rules": len(group),
            "median_change": statistics.median(changes) if changes else None,
            "mean_change": statistics.mean(changes) if changes else None,
            "min_change": min(changes) if changes else None, "max_change": max(changes) if changes else None,
            "gained": sum(change > 0 for change in changes), "lost": sum(change < 0 for change in changes),
            "median_drawdown_change": statistics.median(drawdowns) if drawdowns else None,
            "tier_changed": [(row["name"][:60], row["without"]["tier"], row["with"]["tier"]) for row in group
                             if row["without"]["tier"] != row["with"]["tier"]],
        }
    checked = [row for row in rows if row.get("registered_counted") is not None]
    output["reproduced"] = sum(abs(row["without"]["counted_value"] - row["registered_counted"]) <= 1.0
                               for row in checked if row["without"].get("counted_value") is not None)
    output["checked"] = len(checked)
    return output


def run(base: Path, research: Path, costs, per_group: int = 40, job=None) -> dict[str, object]:
    from quant_platform.research.daily import ENGINE_VERSION, SEED_PLAN, DailyRule, evaluate, load, report_metrics, tier

    chosen = pick(candidates(research), per_group)
    known = registered(research, {item["rule_hash"] for item in chosen}, ENGINE_VERSION)
    data, fp = load(base)
    fp._memo = {}                     # a rule's rankings once for its two runs (daily.remember)
    cache: dict = {}
    rows = []
    for number, item in enumerate(chosen):
        rule = DailyRule.model_validate(item["rule"])
        if job:
            job.update(done=number, total=len(chosen), current=item["name"][:40], force=True)
        without = report_metrics(evaluate(data, fp, rule, costs, cache), ENGINE_VERSION, SEED_PLAN)
        with_retry = report_metrics(evaluate(data, fp, rule, costs, cache, retry_topups=True), ENGINE_VERSION, SEED_PLAN)
        rows.append({"rule_hash": item["rule_hash"], "name": item["name"], "guarded": rule.vol_scale == GUARDED,
                     "execution": rule.execution, "without": _numbers(without, tier), "with": _numbers(with_retry, tier),
                     "registered_counted": (known.get(item["rule_hash"]) or {}).get("counted_value")})
    result = {"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "engine": ENGINE_VERSION,
              "per_group": per_group, "summary": summarize(rows), "rows": rows}
    folder = Path(research) / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"retry-topups-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result
