"""2026-10-28 (research_method §7「重現性」): the deep models' networks and scores had never been part of a rule's input
hash, so the risk model's monthly refit changed September under an unchanged hash and 222 registered results stayed
stale for a week; the list had also graded the listed-plus-TPEx rules on the listed stocks. Every Sunday (the audit
pipeline) a random sample of the pool's latest results is run again by the engine, each on its own universe, and its
final money compared with the registered one: other money means an input the hash leaves out, or one that changed and
was not run again — either way the result shown is not the engine's.
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
SAMPLE = 30


def pool(research: str | Path) -> list:
    """The latest result of every 100%-stock rule on the current engine (not a free robot): what the pool shows."""
    from quant_platform.research.blend import BlendRule, parse_spec
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.daily import ENGINE_VERSION
    from quant_platform.research.registry import TrialRegistry, newest

    research = Path(research)
    current = ENGINE_VERSION.split("+")[0]
    latest = newest(TrialRegistry(research / "trials.jsonl").records(),
                    lambda record: record.kind == "candidate" and record.period == "recent")
    output = []
    for record in sorted(latest.values(), key=lambda record: record.trial_id):
        if str((record.metrics or {}).get("engine") or "").split("+")[0] != current:
            continue
        try:
            spec = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
            rule = parse_spec(spec)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        members = [sleeve.rule for sleeve in rule.sleeves] if isinstance(rule, BlendRule) else [rule]
        if uses_0050(spec) or any(member.free for member in members):
            continue
        output.append((record, rule))
    return output


def run(history: str | Path, research: str | Path, sample: int = SAMPLE, seed: int | None = None,
        broker: str = "cathay", progress=None) -> Path:
    """``sample`` of the pool's latest results run again; saved as audit/reproduce-<time>.json."""
    from quant_platform.research.blend import BlendRule, evaluate_blend
    from quant_platform.research.costs import broker_costs
    from quant_platform.research.daily import evaluate
    from quant_platform.research.rolling import panel
    from quant_platform.research.windows2020 import same

    research = Path(research)
    everything = pool(research)
    chosen = random.Random(date.today().toordinal() if seed is None else seed).sample(everything,
                                                                                       min(sample, len(everything)))
    worker, costs, cache, rows = {"base": str(history), "panels": {}}, broker_costs(broker), {}, []
    for done, (record, rule) in enumerate(sorted(chosen, key=lambda item: item[1].universe), start=1):
        row = {"trial": record.trial_id, "name": record.spec_name, "registered": record.metrics["final_value"]}
        try:
            data, fp = panel(worker, rule.universe)
            run_rule = evaluate_blend if isinstance(rule, BlendRule) else evaluate
            report = run_rule(data, fp, rule, costs, cache, end=date.fromisoformat(record.metrics["end"]))
            row.update(now=report["strategy"]["final_value"], same=same(report["strategy"]["final_value"],
                                                                         record.metrics["final_value"]))
        except Exception as error:  # noqa: BLE001 - one rule's failure is recorded, the rest go on
            row.update(error=str(error)[:200], same=False)
        rows.append(row)
        if progress:
            progress(done, len(chosen), row)
    folder = research / "audit"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"reproduce-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "pool": len(everything),
                                "compared": len(rows), "differing": sum(not row["same"] for row in rows),
                                "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def latest(research: str | Path) -> tuple[Path | None, dict]:
    folder = Path(research) / "audit"
    found = sorted(folder.glob("reproduce-*.json")) if folder.is_dir() else []
    if not found:
        return None, {}
    try:
        return found[-1], json.loads(found[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return found[-1], {}
