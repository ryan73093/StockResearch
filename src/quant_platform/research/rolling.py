"""The monthly rolling forward test (2026-10-16; 使用者：訓練到那個月的前兩個月，後面兩個月拿來做前向測試；模型也要更新；
不要一切都以 2026-10-01 為基準).

Every month, from the latest data:

1. the cutoff is the last day of the month two months back (October 2026 → 2026-08-31; January 2027 → 2026-11-30);
2. the models are retrained on everything whose label ended by the cutoff (``model.train_until``; they score only the
   sessions after it);
3. every registered 100%-stock daily rule is run from 2015-06 to the cutoff (``daily.evaluate(end=cutoff)``) and graded
   by the same gate (both periods, the rolling windows, the drawdown) — nothing after the cutoff is read;
4. each rule that passes runs a fresh owner's account from the first session after the cutoff to the latest one
   (NT$300,000, NT$10,000 on every 5th), against the same money in 0050 — data the selection never saw. A rule that
   beats 0050 there is usable this month. Next month it all moves on by a month.

Written to ``instance/research/rolling/<cutoff>.json``; the holdout part is refreshed as sessions arrive
(``refresh``). Real-time forward records go on as they are (research/stock_forward.py), a second, slower check.
"""

from __future__ import annotations

import json
import os
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from quant_platform.research import files  # 2026-10-29: files written whole while read

ROLLING_VERSION = "rolling-1.0.0"
_worker: dict = {}


def cutoff_for(today: date) -> date:
    """The last day of the month two months before ``today``'s month."""
    first = date(today.year, today.month, 1)
    previous = first - timedelta(days=1)                      # the last day of last month
    return date(previous.year, previous.month, 1) - timedelta(days=1)


def folder(research: str | Path) -> Path:
    return Path(research) / "rolling"


def rules(research: str | Path) -> list[dict]:
    """Every registered 100%-stock daily rule (not a free robot), its newest spec once. 2026-10-18: blends of them
    too (sleeves of daily rules, no 0050 part) — the core-satellite account #4932 was a T0 候選 that no month graded."""
    from quant_platform.research.blend import BlendRule, parse_spec
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.registry import TrialRegistry, newest

    research = Path(research)
    path = research / "trials.jsonl"
    if not path.is_file():
        return []
    latest = newest(TrialRegistry(path).records(),
                    lambda record: record.kind == "candidate" and record.period == "recent"
                    and str((record.metrics or {}).get("engine", "")).startswith("daily-"))
    output = []
    for record in sorted(latest.values(), key=lambda record: record.trial_id):
        try:
            spec = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
            rule = parse_spec(spec)
        except (OSError, ValueError, KeyError, TypeError):
            continue                                          # an older spec
        members = [sleeve.rule for sleeve in rule.sleeves] if isinstance(rule, BlendRule) else [rule]
        if uses_0050(spec) or any(member.free for member in members):
            continue
        output.append({"trial": record.trial_id, "spec": spec})
    return output


def _init(base: str, broker: str) -> None:
    from quant_platform.research import daily
    from quant_platform.research.costs import broker_costs

    data, fp = daily.load(Path(base))
    _worker.update(base=str(base), panels={"twse": (data, fp)}, data=data, fp=fp, costs=broker_costs(broker), cache={})


def panel(worker: dict, universe: str):
    """(data, panel) of a rule's universe, loaded once per worker. 2026-10-28: the list had graded the listed-plus-TPEx
    rules on the listed stocks only (research_method §7「重現性」)."""
    from quant_platform.research import daily

    panels = worker.setdefault("panels", {})
    if universe not in panels:
        panels[universe] = daily.load(Path(worker["base"]), universe=universe)
    return panels[universe]


def grade_metrics(report: dict) -> dict:
    """What the month's list keeps of a rule's report up to the cutoff (2026-10-27: with the 2020-10 track's money —
    the gate's track)."""
    return {"start": report.get("start"),     # 2026-10-21: an account from after 2020-10 is graded on the short track
            "full_period_excess": report["full_period_excess"], "since_2020_excess": report["since_2020"]["excess"],
            "since_2020_final_value": report["since_2020"]["strategy"]["final_value"],
            "since_2020_benchmark_final_value": report["since_2020"]["benchmark"]["final_value"],
            "windows": report["windows"], "max_drawdown": report["strategy"]["max_drawdown"],
            "benchmark_max_drawdown": report["benchmark"]["max_drawdown"],
            "final_value": report["strategy"]["final_value"], "benchmark_final_value": report["benchmark"]["final_value"],
            "xirr": report["strategy"].get("xirr"), "benchmark_xirr": report["benchmark"].get("xirr")}


def _grade(item: dict, cutoff: str) -> dict:
    from quant_platform.research.blend import BlendRule, evaluate_blend, parse_spec
    from quant_platform.research.daily import PASSING_TIERS, evaluate, gate, tier

    rule = parse_spec(item["spec"])
    run = evaluate_blend if isinstance(rule, BlendRule) else evaluate
    try:
        data, fp = panel(_worker, rule.universe)
        report = run(data, fp, rule, _worker["costs"], _worker["cache"], end=date.fromisoformat(cutoff))
    except Exception as error:  # noqa: BLE001 - one rule's failure is recorded, the month goes on
        return {"trial": item["trial"], "name": rule.name, "error": str(error)[:200]}
    metrics = grade_metrics(report)
    reasons = gate(metrics)
    return {"trial": item["trial"], "name": rule.name, "rule_hash": rule.rule_hash, "spec": item["spec"],
            # 2026-10-18 (使用者：回撤上限放寬並標註高風險): T0 候選（高風險） passes too, marked by its tier
            "metrics": metrics, "reasons": reasons, "tier": tier(metrics)[0], "passed": tier(metrics)[0] in PASSING_TIERS}


def select(base: str | Path, research: str | Path, cutoff: date, broker: str = "cathay", workers: int = 2,
           progress=None, skip: set[str] | None = None) -> list[dict]:
    """Every rule graded on 2015-06..cutoff, in ``workers`` processes (each loads the data once); 2026-10-18: but
    the rules in ``skip`` (already graded this month — only the new ones are added). 2026-10-20 (the audit): ``skip``
    holds rule hashes, not trial numbers — a trial's spec graded under another hash is graded again."""
    from quant_platform.research.blend import parse_spec

    items = [item for item in rules(research) if parse_spec(item["spec"]).rule_hash not in (skip or set())]
    output = []
    with ProcessPoolExecutor(max_workers=max(1, workers), initializer=_init, initargs=(str(base), broker)) as pool:
        for done, row in enumerate(pool.map(_grade, items, [cutoff.isoformat()] * len(items), chunksize=4), start=1):
            output.append(row)
            if progress:
                progress(done, len(items), row)
    return output


HOPELESS = 0.7          # 2026-10-21: under 70% of 0050's account — a month cannot lift it over 0050


def carried(research: str | Path, cutoff: date, engine: str) -> list[dict]:
    """2026-10-21 (使用者：每個月都要跑那麼久，我時間很多是不是): the rules not run again this month — last month's T3
    whose account ended under ``HOPELESS`` of the same money in 0050 (it would have to beat 0050 by more than 40% in
    one month; 789 of the 2,041 rules graded at 2026-08-31) — with last month's grade, marked ``carried_from``.
    None in a quarter's month (a cutoff at the end of March, June, September, December: every rule again), nor when
    the engine changed since."""
    if cutoff.month % 3 == 0:
        return []
    earlier = []
    for path in folder(research).glob("*.json") if folder(research).is_dir() else []:
        try:
            if date.fromisoformat(path.stem) < cutoff:
                earlier.append(path)
        except ValueError:
            continue
    if not earlier:
        return []
    previous = json.loads(max(earlier).read_text(encoding="utf-8"))
    if previous.get("engine") != engine:
        return []
    output = []
    for row in previous.get("graded") or []:
        metrics = row.get("metrics") or {}
        # 2026-10-27: on the gate's track (from 2020-10; an account after it, its own) — a row graded before the
        # 2020-10 track's money was kept is run again
        short = str(metrics.get("start") or "") >= "2020-10-26"
        value = metrics.get("final_value" if short else "since_2020_final_value")
        bench = metrics.get("benchmark_final_value" if short else "since_2020_benchmark_final_value")
        if row.get("error") or row.get("tier") != "T3" or not bench or value is None:
            continue
        if value / bench < HOPELESS:
            output.append({**row, "carried_from": row.get("carried_from") or previous["cutoff"]})
    return output


def holdout(data, fp, rows: list[dict], cutoff: date, costs, end: date | None = None) -> list[dict]:
    """Each passing rule's (or blend's) fresh owner's account from the first session after ``cutoff`` to ``end``
    (the latest)."""
    from quant_platform.research.blend import BlendAccount, BlendRule, parse_spec
    from quant_platform.research.daily import (
        account_parking, daily_rankings, daily_weights, exposure_schedule, simulate_daily,
    )
    from quant_platform.research.metrics import unit_values
    from quant_platform.research.stock_rules import SeedPlan

    sessions = [day for day in fp.sessions if day > cutoff and (end is None or day <= end)]
    if not sessions:
        return []
    first, last = sessions[0], sessions[-1]
    bench = simulate_daily(data, None, costs, first, last, plan=SeedPlan())
    bench_units = unit_values(bench.values, bench.flows)
    output = []
    for row in rows:
        rule = parse_spec(row["spec"])
        if isinstance(rule, BlendRule):
            run = BlendAccount(data, fp, rule, costs, first, last).run(first, last, plan=SeedPlan())
        else:
            ranks = daily_rankings(fp, rule, first, last)
            run = simulate_daily(data, rule, costs, first, last, ranks, plan=SeedPlan(),
                                 weights=daily_weights(fp, rule, ranks), parked=account_parking(data, fp, rule, costs),
                                 exposure=exposure_schedule(data, fp, rule, costs))
        units = unit_values(run.values, run.flows)
        peak, drawdown = 0.0, 0.0
        for value in units:
            peak = max(peak, value)
            drawdown = min(drawdown, value / peak - 1 if peak else 0.0)
        output.append({"trial": row["trial"], "rule_hash": row["rule_hash"], "name": row["name"],
                       "start": first.isoformat(), "end": last.isoformat(), "sessions": len(sessions),
                       "value": round(run.final_value, 0), "benchmark_value": round(bench.final_value, 0),
                       "contributed": round(run.contributed, 0),
                       "excess": round((run.final_value - bench.final_value) / run.contributed, 6) if run.contributed else None,
                       "return": round(units[-1] / units[0] - 1, 6) if units and units[0] else None,
                       "benchmark_return": round(bench_units[-1] / bench_units[0] - 1, 6) if bench_units and bench_units[0] else None,
                       "drawdown": round(drawdown, 4), "trades": run.trades,
                       "usable": run.final_value >= bench.final_value})
    output.sort(key=lambda item: -(item["excess"] or -9))
    return output


def unique_rows(rows: list[dict]) -> list[dict]:
    """2026-10-28: one graded row a rule — the last one (the newest grading). The same rule registered twice under two
    names (#4316 and #5037, the consensus lists) had two rows; the gate's windows reached only the later one."""
    last = {str(row.get("rule_hash")): index for index, row in enumerate(rows) if row.get("rule_hash")}
    return [row for index, row in enumerate(rows) if not row.get("rule_hash") or last[str(row["rule_hash"])] == index]


def save(research: str | Path, cutoff: date, payload: dict) -> Path:
    if payload.get("graded"):
        payload = {**payload, "graded": unique_rows(payload["graded"])}
    target = folder(research) / f"{cutoff.isoformat()}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    files.replace(partial, target, wait=300.0)       # 2026-10-29: a reader holding it
    return target


def latest(research: str | Path) -> dict | None:
    """The newest month's result (the page reads this)."""
    found = sorted(folder(research).glob("*.json")) if folder(research).is_dir() else []
    if not found:
        return None
    return json.loads(found[-1].read_text(encoding="utf-8"))


def from_records(records: list[dict], rows: list[dict], cutoff: date) -> tuple[list[dict], list[dict]]:
    """2026-10-21 (使用者：每個月都要跑那麼久，我時間很多是不是): the month's forward test read from the forward accounts'
    own records — research/stock_forward.py keeps the same account (the rule from the first session after the cutoff,
    NT$300,000 and NT$10,000 every 5th, against the same money in 0050) and runs it to the day at 15:30 — instead of
    running every passing rule again at 22:30 (about 10 minutes every trading night). Returns the rows read and the
    passing rules with no account recorded through the newest day (the month's first evening: run them)."""
    from quant_platform.research.metrics import unit_values

    newest = max((str(record.get("date")) for record in records), default="")
    series: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        since = str(record.get("since") or "")
        if since > cutoff.isoformat():
            series.setdefault((record["rule_hash"], since), []).append(record)
    output, missing = [], []
    for row in rows:
        starts = sorted(since for rule_hash, since in series if rule_hash == row["rule_hash"])
        days = sorted(series[(row["rule_hash"], starts[0])], key=lambda record: record["date"]) if starts else []
        if not days or days[-1]["date"] != newest:
            missing.append(row)
            continue
        values = [float(record["value"]) for record in days]
        paid = [float(record["contributed"]) for record in days]
        units = unit_values(values, [paid[0]] + [later - earlier for earlier, later in zip(paid, paid[1:])])
        bench = [float(record["benchmark_value"]) for record in days]
        bench_units = unit_values(bench, [paid[0]] + [later - earlier for earlier, later in zip(paid, paid[1:])])
        peak, drawdown = 0.0, 0.0
        for value in units:
            peak = max(peak, value)
            drawdown = min(drawdown, value / peak - 1 if peak else 0.0)
        last = days[-1]
        output.append({"trial": row["trial"], "rule_hash": row["rule_hash"], "name": row["name"],
                       "start": days[0]["date"], "end": last["date"], "sessions": len(days),
                       "value": round(float(last["value"]), 0), "benchmark_value": round(float(last["benchmark_value"]), 0),
                       "contributed": round(float(last["contributed"]), 0),
                       "excess": round((float(last["value"]) - float(last["benchmark_value"])) / float(last["contributed"]), 6)
                       if last["contributed"] else None,
                       "return": round(units[-1] / units[0] - 1, 6) if units[0] else None,
                       "benchmark_return": round(bench_units[-1] / bench_units[0] - 1, 6) if bench_units[0] else None,
                       "drawdown": round(drawdown, 4), "trades": int(last.get("trades_total") or 0),
                       "usable": float(last["value"]) >= float(last["benchmark_value"]), "from_records": True})
    return output, missing


def _universe(row: dict) -> str:
    spec = row["spec"] if isinstance(row["spec"], dict) else json.loads(row["spec"])
    return "twse" if spec.get("kind") == "blend" else spec.get("universe", "twse")


def refresh(research: str | Path, load, costs, cutoff: date | None = None, records: list[dict] | None = None) -> dict | None:
    """The holdout of the month's passing rules again, through the latest session: 2026-10-21, read from the forward
    accounts' records where they are up to date (``from_records``); only the others are run (``load(universe)`` →
    data, fp; 2026-10-28: each on its own universe's stocks)."""
    path = folder(research) / f"{cutoff.isoformat()}.json" if cutoff else None
    payload = json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else latest(research)
    if not payload:
        return None
    month_cutoff = date.fromisoformat(payload["cutoff"])
    passed = [row for row in payload["graded"] if row.get("passed")]
    rows, missing = from_records(records or [], passed, month_cutoff)
    through = max((row["end"] for row in rows), default=None)
    for universe in sorted({_universe(row) for row in missing}):
        data, fp = load(universe)
        rows += holdout(data, fp, [row for row in missing if _universe(row) == universe], month_cutoff, costs)
        through = fp.sessions[-1].isoformat()
    rows.sort(key=lambda item: -(item["excess"] or -9))
    payload["holdout"] = rows
    payload["holdout_run"] = len(missing)
    payload["holdout_refreshed_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    payload["data_through"] = through
    save(research, month_cutoff, payload)
    return payload


def pending(research: str | Path) -> dict | None:
    """The month started and not graded yet (a marker, no result): what the pages say meanwhile."""
    started = sorted(folder(research).glob("*.started")) if folder(research).is_dir() else []
    months = [path.stem for path in started if not path.stem.startswith("holdout-")]
    if not months:
        return None
    return {"cutoff": months[-1], "holdout_start": None, "data_through": None, "graded": 0, "passed": 0, "errors": 0,
            "usable": [], "holdout": [], "models": []}


def summary(payload: dict | None) -> dict | None:
    """What the pages show: the month, how many rules were graded and passed, the usable ones."""
    if not payload:
        return None
    graded = payload.get("graded") or []
    hold = payload.get("holdout") or []
    return {"cutoff": payload["cutoff"], "holdout_start": hold[0]["start"] if hold else None,
            "data_through": payload.get("data_through"), "graded": len(graded),
            "passed": sum(1 for row in graded if row.get("passed")), "errors": sum(1 for row in graded if row.get("error")),
            "usable": [row for row in hold if row["usable"]], "holdout": hold, "models": payload.get("models") or []}
