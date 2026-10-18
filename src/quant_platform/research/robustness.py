"""2026-10-18 (使用者：繼續; the best T0 candidates, are they sturdy?): the strongest 100%-stock daily T0 candidates run
again with every cost doubled (fee, minimum fee and slippage; the transaction tax is the law) and, for those filled
the same evening, filled at the next session's close instead (``execution="next"``). A diagnostic, not a trial: the
runs are not registered and choose nothing; they say whether a candidate's edge survives worse execution than the
engine assumes. The legacy design had the same two checks (research/promotion.py); the daily engine had none.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

COST_FACTOR = 2.0


def candidates(research: Path, top: int) -> list[tuple[object, dict]]:
    """The ``top`` T0 candidates by end value (newest engine, single listed-stock rules, no 0050): record and spec."""
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.daily import ENGINE_VERSION, tier
    from quant_platform.research.registry import TrialRegistry, newest

    records = [record for record in TrialRegistry(research / "trials.jsonl").records()
               if record.kind == "candidate" and record.period == "recent"
               and str(record.metrics.get("engine", "")) == ENGINE_VERSION]
    chosen = []
    for record in newest(records).values():
        if tier(record.metrics)[0] != "T0 候選":
            continue
        try:
            spec = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
        except (OSError, ValueError, KeyError):
            continue
        if spec.get("kind") == "blend" or spec.get("core") or uses_0050(spec) or spec.get("universe", "twse") != "twse":
            continue
        chosen.append((record, spec))
    chosen.sort(key=lambda item: -(item[0].metrics.get("final_value") or 0))
    return chosen[:top]


def run(base: Path, research: Path, costs, top: int = 10, job=None) -> dict[str, object]:
    from quant_platform.research.daily import (
        ENGINE_VERSION,
        SEED_PLAN,
        DailyRule,
        evaluate,
        load,
        report_metrics,
        tier,
    )

    chosen = candidates(research, top)
    data, fp = load(base)
    plain, dearer = {}, {}                      # 0050's runs, one cache per cost model
    rows = []
    for number, (record, spec) in enumerate(chosen):
        rule = DailyRule.model_validate(spec)
        if job:
            job.update(done=number, total=len(chosen), current=f"{record.spec_name[:40]}", force=True)
        row = {"trial": record.trial_id, "rule_hash": record.spec_hash, "name": record.spec_name,
               "base": _summary(record.metrics, tier)}
        doubled = report_metrics(evaluate(data, fp, rule, costs.scaled(COST_FACTOR), dearer), ENGINE_VERSION, SEED_PLAN)
        row["costs_doubled"] = _summary(doubled, tier)
        if rule.execution == "same":
            late = rule.model_copy(update={"execution": "next"})
            row["next_session"] = _summary(report_metrics(evaluate(data, fp, late, costs, plain), ENGINE_VERSION,
                                                          SEED_PLAN), tier)
        rows.append(row)
    result = {"engine": ENGINE_VERSION, "cost_factor": COST_FACTOR, "rows": rows,
              "built_at": datetime.now(UTC).isoformat(timespec="seconds")}
    folder = research / "robustness"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{datetime.now(UTC):%Y%m%d-%H%M%S}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                                                   encoding="utf-8")
    return result


def latest(research: Path) -> dict | None:
    """The newest check (research/robustness/<stamp>.json), for the research page."""
    folder = Path(research) / "robustness"
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    for path in reversed(files):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
    return None


def _summary(metrics: dict, tier) -> dict[str, object]:
    return {"final_value": round(metrics.get("final_value") or 0), "since_2020_final_value":
            round(metrics.get("since_2020_final_value") or 0), "benchmark_final_value": round(metrics.get("benchmark_final_value") or 0),
            "since_2020_benchmark_final_value": round(metrics.get("since_2020_benchmark_final_value") or 0),
            "max_drawdown": metrics.get("max_drawdown"), "cost_share": metrics.get("cost_share"),
            "tier": tier(metrics)[0], "reason": tier(metrics)[1]}
