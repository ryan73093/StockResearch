"""Strategy roles and how they grew (2026-10-16; 使用者：策略就是我的角色——每次優化之後我要在首頁看到每種角色成長了
多少、跟之前訓練相比強化了多少，不只是現在多少錢，評估指標也不要只有這些).

Every 100%-stock daily rule in the trial registry belongs to one role (``role``): machine learning with factors, the
T0 consensus list, the RL robots, multi-factor sets, single factors by family, and so on. A role's strength is its
best rule's account: NT$300,000 from 2015-06 and NT$10,000 every 5th, traded only by the exchange's rules, against the
same money in 0050 — each rule as its newest engine ran it.

Growth is read from the registry itself: the role as it stood just before its latest optimization — the latest run
(a batch of trials started together, the stamp in their report files) that added a new rule to the role — against
now, every rule as its newest engine ran it by then. Engine re-runs are not optimizations, but their corrections
(1.4.0's market rules, 1.5.0's capital reductions) show in the numbers, which can fall as well as rise.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from quant_platform.research.categories import FAMILY_NOTES, FAMILY_ORDER

CLASSIFICATION = "categories-2026-10-17"    # the cached roles are dropped when the classification changes
ORDER = ()                 # the pool's order (categories.FAMILY_ORDER); kept for callers
NOTES: dict[str, str] = {}
_STAMP = re.compile(r"(\d{8}-\d{6})")
_cache: dict[str, object] = {}


def role(spec: dict) -> str:
    """The rule's category, as the pool shows it (research/categories.py ``classify``) — one classification."""
    from quant_platform.research.categories import classify

    return classify(spec)["family"]


def _entries(research: Path) -> list[dict]:
    """Every daily-rule run of a 100%-stock rule: its role, time, run and metrics (cached by the registry's size)."""
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.registry import TrialRegistry

    path = research / "trials.jsonl"
    if not path.is_file():
        return []
    key = f"{path.stat().st_size}:{path.stat().st_mtime_ns}"
    if _cache.get("key") == key:
        return _cache["entries"]                                    # type: ignore[return-value]
    saved = research / "roles" / "spec_roles.json"          # spec hash → role, so the reports are read once
    roles: dict[str, str | None] = dict(_cache.get("roles") or {})
    if not roles and saved.is_file():
        try:
            stored = json.loads(saved.read_text(encoding="utf-8"))
            roles = stored.get("roles", {}) if stored.get("version") == CLASSIFICATION else {}
        except (OSError, ValueError, AttributeError):
            roles = {}
    known = len(roles)
    output = []
    for record in TrialRegistry(path).records():
        engine = str((record.metrics or {}).get("engine") or "")
        if record.kind != "candidate" or record.period != "recent" or not engine.startswith("daily-"):
            continue
        if record.spec_hash not in roles:
            try:
                spec = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
                roles[record.spec_hash] = None if uses_0050(spec) else role(spec)
            except (OSError, ValueError, KeyError, TypeError):
                roles[record.spec_hash] = None
        if roles[record.spec_hash] is None:
            continue
        found = _STAMP.search(record.report_file or "")
        output.append({"spec": record.spec_hash, "role": roles[record.spec_hash], "record": record,
                       "created": record.created_at, "run": found.group(1) if found else record.created_at[:10]})
    if len(roles) != known:
        try:
            saved.parent.mkdir(parents=True, exist_ok=True)
            partial = saved.with_suffix(".partial")
            partial.write_text(json.dumps({"version": CLASSIFICATION, "roles": roles}, ensure_ascii=False), encoding="utf-8")
            partial.replace(saved)
        except OSError:
            pass
    _cache.update(key=key, entries=output, roles=roles)
    return output


def _best(entries: list[dict], until: str | None = None) -> dict[str, dict]:
    """role → its best rule among every rule's newest-engine record created before ``until`` — the richest passing
    one, else the richest (categories.best_of, the pool's pick too)."""
    from quant_platform.research.categories import best_of
    from quant_platform.research.daily import tier
    from quant_platform.research.registry import newest

    known = [entry for entry in entries if until is None or entry["created"] < until]
    latest = newest([entry["record"] for entry in known])
    by_spec = {entry["spec"]: entry for entry in known}
    groups: dict[str, list[dict]] = defaultdict(list)
    for spec, record in latest.items():
        value = (record.metrics or {}).get("final_value")
        if value:
            groups[by_spec[spec]["role"]].append({"final_value": value, "tier": tier(record.metrics)[0], "record": record})
    return {name: {"record": best_of(rows)["record"], "role": name} for name, rows in groups.items()}


def _view(record) -> dict[str, object]:
    from quant_platform.research.daily import tier

    metrics = record.metrics or {}
    windows = metrics.get("windows") or {}
    grade, _why = tier(metrics)
    drawdown = metrics.get("max_drawdown")
    xirr = metrics.get("xirr")
    return {"trial": record.trial_id, "spec": record.spec_hash, "name": record.spec_name, "engine": metrics.get("engine"), "tier": grade,
            "start": metrics.get("start"), "final": metrics.get("final_value"),
            "bench_final": metrics.get("benchmark_final_value"), "contributed": metrics.get("contributed"),
            "xirr": xirr, "bench_xirr": metrics.get("benchmark_xirr"),
            "excess_full": metrics.get("full_period_excess"), "excess_recent": metrics.get("since_2020_excess"),
            "drawdown": drawdown, "bench_drawdown": metrics.get("benchmark_max_drawdown"),
            "calmar": round(xirr / abs(drawdown), 2) if xirr is not None and drawdown else None,
            "win_1y": (windows.get("1y") or {}).get("win_ratio"), "win_3y": (windows.get("3y") or {}).get("win_ratio"),
            "worst_1y": (windows.get("1y") or {}).get("worst_excess"),
            "orders": metrics.get("orders_per_month"), "cost_share": metrics.get("cost_share"),
            "turnover": metrics.get("turnover")}


def now_day() -> str:
    from datetime import datetime

    return datetime.now().date().isoformat()


def summary(research_dir: str | Path) -> dict[str, object]:
    """Every role: its best now, before its latest run, the change, its grades and a few earlier points."""
    research = Path(research_dir)
    entries = _entries(research)
    if not entries:
        return {"roles": [], "latest_run": None}
    memo: dict[str | None, dict[str, dict]] = {}

    def as_of(until: str | None = None) -> dict[str, dict]:
        if until not in memo:
            memo[until] = _best(entries, until)
        return memo[until]

    now = as_of()
    started: dict[str, str] = {}
    first_seen: dict[str, dict] = {}
    for entry in entries:                             # a run's first record; a rule's first run
        started[entry["run"]] = min(started.get(entry["run"], entry["created"]), entry["created"])
        if entry["spec"] not in first_seen or entry["created"] < first_seen[entry["spec"]]["created"]:
            first_seen[entry["spec"]] = entry
    by_role: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_role[entry["role"]].append(entry)
    from quant_platform.research.daily import tier
    from quant_platform.research.registry import newest

    rows = []
    for name, best in now.items():
        mine = by_role[name]
        newest_rule = max((first_seen[entry["spec"]] for entry in mine), key=lambda entry: entry["created"])
        last_run = newest_rule["run"]                             # the latest run that added a rule to the role
        before = as_of(started[last_run]).get(name)
        days = sorted({first_seen[entry["spec"]]["created"][:10] for entry in mine} | {now_day()})
        history = []
        for day in days:
            point = as_of(f"{day}T99").get(name)
            if point:
                history.append({"day": day, "final": (point["record"].metrics or {}).get("final_value")})
        grades: dict[str, int] = defaultdict(int)
        latest = newest([entry["record"] for entry in mine])
        for record in latest.values():
            grades[tier(record.metrics or {})[0]] += 1
        view = _view(best["record"])
        previous = _view(before["record"]) if before else None
        change = (view["final"] / previous["final"] - 1) if previous and previous["final"] else None
        rows.append({"role": name, "note": FAMILY_NOTES.get(name, ""), "now": view, "before": previous, "change": change,
                     "first": history[0] if history else None,
                     "growth_total": (view["final"] / history[0]["final"] - 1) if history and history[0]["final"] else None,
                     "last_run": started[last_run], "rules": len(latest), "grades": dict(grades),
                     "history": history[-12:]})
    rank = {name: index for index, name in enumerate(FAMILY_ORDER)}
    rows.sort(key=lambda row: (-(row["now"]["final"] or 0)))
    for row in rows:
        row["order"] = rank.get(row["role"], len(FAMILY_ORDER))
    return {"roles": rows, "latest_run": max(started.values()) if started else None,
            "benchmark": rows[0]["now"]["bench_final"] if rows else None}
