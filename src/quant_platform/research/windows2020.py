"""2026-10-27 (使用者 10/26：2015 到 2020 和 2020 到現在交易的風氣差很多，不要再用 2015～2020 作為篩選；短歷史賽道
加重; 10/27：問我幹嘛): the gate moved to the 2020-10 track (research/daily.py ``gate``). Its rolling windows start in
that track (``REGIME_WINDOWS``); results registered or graded before the windows kept that part have it run again here,
exactly, by the engine — only the windows from REGIME_WINDOWS — and each is checked against its result (the same final
value to the yuan) before it is used:

* the month's list (``rolling/<cutoff>.json``): the windows go into each graded row, which is graded again; a row whose
  result the engine no longer reproduces (its inputs changed since it was graded) is graded again in full;
* the registry: the windows go beside it (``windows_since_2020.jsonl``, read by ``TrialRegistry.records``); a record the
  engine no longer reproduces is listed in ``windows_since_2020-stale.json`` for a full rerun (``daily --name
  windows_stale``) instead.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from pathlib import Path

STALE = "windows_since_2020-stale.json"
_worker: dict = {}


def _init(base: str, broker: str) -> None:
    from quant_platform.research import daily
    from quant_platform.research.costs import broker_costs

    data, fp = daily.load(Path(base))
    _worker.update(base=str(base), panels={"twse": (data, fp)}, costs=broker_costs(broker), cache={})


def same(value: float, expected: float) -> bool:
    return abs(float(value) - float(expected)) <= max(1.0, 1e-7 * abs(float(expected)))


def _windows(item: dict) -> dict:
    """The windows of ``item`` (spec, end, the final value it must reproduce) starting in the 2020-10-26 track; in full
    (``item["full"]``) the whole report's metrics as the month's list grades them."""
    from quant_platform.research.blend import BlendRule, evaluate_blend, parse_spec
    from quant_platform.research.daily import REGIME_WINDOWS, evaluate
    from quant_platform.research.rolling import panel

    rule = parse_spec(item["spec"])
    run = evaluate_blend if isinstance(rule, BlendRule) else evaluate
    end = date.fromisoformat(item["end"])
    try:
        data, fp = panel(_worker, rule.universe)      # 2026-10-28: its own universe (TPEx rules had read the listed)
        if item.get("full"):
            report = run(data, fp, rule, _worker["costs"], _worker["cache"], end=end)
        else:
            report = run(data, fp, rule, _worker["costs"], _worker["cache"], end=end, windows_from=REGIME_WINDOWS)
    except Exception as error:  # noqa: BLE001 - one rule's failure is recorded, the rest go on
        return {"key": item["key"], "error": str(error)[:200]}
    final = report["strategy"]["final_value"]
    output = {"key": item["key"], "final": final, "same": same(final, item["final"]) if item.get("final") is not None else None,
              "windows": {key: (value.get("since_2020") or {"count": 0}) for key, value in report["windows"].items()},
              "since_2020_final_value": report["since_2020"]["strategy"]["final_value"],
              "since_2020_benchmark_final_value": report["since_2020"]["benchmark"]["final_value"]}
    if item.get("full"):
        from quant_platform.research.rolling import grade_metrics

        output["metrics"] = grade_metrics(report)
    return output


def _run(items: list[dict], base: str | Path, broker: str, workers: int, progress=None):
    with ProcessPoolExecutor(max_workers=max(1, workers), initializer=_init, initargs=(str(base), broker)) as pool:
        for done, row in enumerate(pool.map(_windows, items, chunksize=2), start=1):
            if progress:
                progress(done, len(items), row)
            yield row


def list_items(payload: dict, other_universe: bool = False) -> list[dict]:
    """The month's graded rows that lack the windows (or were carried from an earlier month: run in full).
    ``other_universe`` (2026-10-28): also every rule on another universe than the listed stocks, in full — the list had
    graded them on the listed stocks (research_method §7「重現性」)."""
    from quant_platform.research.rolling import _universe

    output = []
    for row in payload.get("graded") or []:
        metrics = row.get("metrics") or {}
        if row.get("error") or not row.get("spec"):
            continue
        windows = metrics.get("windows") or {}
        has = all(isinstance(windows.get(key), dict) and "since_2020" in windows[key] for key in ("1y", "3y"))
        if row.get("carried_from") or (other_universe and _universe(row) != "twse"):
            output.append({"key": row["rule_hash"], "spec": row["spec"], "end": payload["cutoff"], "full": True})
        elif not has:
            output.append({"key": row["rule_hash"], "spec": row["spec"], "end": payload["cutoff"],
                           "final": metrics.get("final_value")})
    return output


def apply_to_row(row: dict, found: dict) -> dict:
    """``row`` of the month's list with the windows ``found`` and graded again by the gate."""
    from quant_platform.research.daily import PASSING_TIERS, gate, tier

    if found.get("metrics"):
        metrics = found["metrics"]
        row = {key: value for key, value in row.items() if key != "carried_from"}
    else:
        metrics = dict(row["metrics"])
        metrics["windows"] = {key: ({**value, "since_2020": found["windows"][key]} if isinstance(value, dict) else value)
                              for key, value in (metrics.get("windows") or {}).items()}
        metrics["since_2020_final_value"] = found["since_2020_final_value"]
        metrics["since_2020_benchmark_final_value"] = found["since_2020_benchmark_final_value"]
    grade = tier(metrics)[0]
    return {**row, "metrics": metrics, "reasons": gate(metrics), "tier": grade, "passed": grade in PASSING_TIERS,
            "regraded": {"at": datetime.now().isoformat(timespec="seconds"), "why": "門檻改以 2020-10 以後為主（2026-10-27）",
                         "before": row.get("tier")}}


def backfill_list(research: str | Path, base: str | Path, cutoff: date, broker: str = "cathay", workers: int = 2,
                  progress=None, other_universe: bool = False) -> dict[str, int]:
    """The month's list: every graded row with the windows of the 2020-10-26 track, graded again (``other_universe``:
    see list_items)."""
    from quant_platform.research.rolling import folder, save

    path = folder(research) / f"{cutoff.isoformat()}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = list_items(payload, other_universe)
    by_key = {row.get("rule_hash"): index for index, row in enumerate(payload["graded"])}
    rerun = [item for item in items if not item.get("full")]
    found = {row["key"]: row for row in _run(items, base, broker, workers, progress)}
    redo = [{**item, "full": True} for item in rerun if found.get(item["key"], {}).get("same") is False]
    for row in _run(redo, base, broker, workers, progress) if redo else ():
        found[row["key"]] = row
    counts = {"rows": len(items), "full": len(redo) + len(items) - len(rerun), "errors": 0, "changed": 0}
    for key, row in found.items():
        if row.get("error") or key not in by_key:
            counts["errors"] += 1
            continue
        index = by_key[key]
        before = payload["graded"][index].get("tier")
        payload["graded"][index] = apply_to_row(payload["graded"][index], row)
        counts["changed"] += payload["graded"][index]["tier"] != before
    payload["gate"] = "2020-10"
    save(research, cutoff, payload)
    return counts


def registry_items(research: str | Path) -> list[dict]:
    """The latest result of every 100%-stock rule on the current engine whose windows lack the 2020-10-26 part."""
    from quant_platform.research.blend import parse_spec
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.daily import ENGINE_VERSION
    from quant_platform.research.registry import TrialRegistry, newest

    research = Path(research)
    current = ENGINE_VERSION.split("+")[0]
    latest = newest(TrialRegistry(research / "trials.jsonl").records(),
                    lambda record: record.kind == "candidate" and record.period == "recent")
    output = []
    for record in sorted(latest.values(), key=lambda record: record.trial_id):
        metrics = record.metrics or {}
        if str(metrics.get("engine") or "").split("+")[0] != current:
            continue
        windows = metrics.get("windows") or {}
        if all(isinstance(windows.get(key), dict) and "since_2020" in windows[key] for key in ("1y", "3y")):
            continue
        try:
            spec = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
            parse_spec(spec)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if uses_0050(spec):
            continue
        output.append({"key": record.input_hash, "trial": record.trial_id, "spec": spec, "end": metrics["end"],
                       "final": metrics.get("final_value")})
    return output


def backfill_registry(research: str | Path, base: str | Path, broker: str = "cathay", workers: int = 2,
                      progress=None) -> dict[str, int]:
    """The registry's latest results: the windows beside it, appended as each is checked (a stop loses nothing)."""
    from quant_platform.research.registry import SUPPLEMENT

    research = Path(research)
    target = research / SUPPLEMENT
    stale_path = research / STALE
    stale = json.loads(stale_path.read_text(encoding="utf-8")) if stale_path.is_file() else []
    known = {row["input_hash"] for row in stale}
    items = [item for item in registry_items(research) if item["key"] not in known]
    trials = {item["key"]: item["trial"] for item in items}
    counts = {"rows": len(items), "added": 0, "stale": 0, "errors": 0}
    for row in _run(items, base, broker, workers, progress):
        if row.get("error"):
            counts["errors"] += 1
            continue
        if not row["same"]:
            counts["stale"] += 1
            if row["key"] not in known:
                stale.append({"input_hash": row["key"], "trial": trials[row["key"]], "final_now": row["final"]})
                stale_path.write_text(json.dumps(stale, ensure_ascii=False, indent=1), encoding="utf-8")
            continue
        with target.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"input_hash": row["key"], "trial": trials[row["key"]], "windows": row["windows"],
                                     "at": datetime.now().isoformat(timespec="seconds")}, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        counts["added"] += 1
    return counts


def stale_rules(research: str | Path) -> list:
    """The rules whose registered result the engine no longer reproduces (``backfill_registry``), to run again —
    2026-10-28: not those already registered again since (a later trial of the same settings)."""
    from quant_platform.research.blend import parse_spec
    from quant_platform.research.registry import TrialRegistry

    research = Path(research)
    path = research / STALE
    if not path.is_file():
        return []
    wanted = {row["trial"] for row in json.loads(path.read_text(encoding="utf-8"))}
    records = TrialRegistry(research / "trials.jsonl").records()
    last = {}
    for record in records:
        last[record.spec_hash] = max(last.get(record.spec_hash, 0), record.trial_id)
    output = []
    for record in records:
        if record.trial_id in wanted and last[record.spec_hash] == record.trial_id:
            spec = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
            output.append(parse_spec(spec))
    return output


def missing(research: str | Path, cutoff: date | None = None) -> dict[str, int]:
    """How many latest results and month's rows still lack the gate's windows (the daily check)."""
    from quant_platform.research.rolling import folder

    research = Path(research)
    output = {"registry": len(registry_items(research)) if (research / "trials.jsonl").is_file() else 0, "list": 0}
    path = folder(research) / f"{cutoff.isoformat()}.json" if cutoff else None
    if path and path.is_file():
        output["list"] = len(list_items(json.loads(path.read_text(encoding="utf-8"))))
    return output
