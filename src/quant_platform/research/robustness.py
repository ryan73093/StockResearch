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
# 2026-10-22 (開盤賣做不做得到): a rule filling in the opening auction runs again with that fill capped at 0.2% of the
# day's value instead of 0.5% — about the share of the opening auction the closing one gets (the opening auction is
# ~2.8% of the day, the closing 4–9%)
OPEN_TIGHT = 0.002


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


def chosen_trials(research: Path, trials: list[int]) -> list[tuple[object, dict]]:
    """2026-10-21: named trials of any tier, single rules or blends (the frontier's #5983, #6086, #6087)."""
    from quant_platform.research.registry import TrialRegistry

    records = {record.trial_id: record for record in TrialRegistry(research / "trials.jsonl").records()
               if record.kind == "candidate" and record.period == "recent"}
    output = []
    for trial in trials:
        record = records.get(trial)
        if record is None:
            continue
        spec = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
        output.append((record, spec))
    return output


def opens_auction(rule) -> bool:
    """Whether a rule (or a blend's sleeve) fills anything in the opening auction."""
    sleeves = getattr(rule, "sleeves", None)
    rules = [sleeve.rule for sleeve in sleeves] if sleeves is not None else [rule]
    return any(getattr(item, "execution", "same") in ("open", "split") for item in rules)


def run(base: Path, research: Path, costs, top: int = 10, job=None, trials: list[int] | None = None) -> dict[str, object]:
    from quant_platform.research.blend import BlendRule, Sleeve, evaluate_blend, parse_spec
    from quant_platform.research.daily import (
        ENGINE_VERSION,
        SEED_PLAN,
        evaluate,
        load,
        report_metrics,
        tier,
    )

    chosen = chosen_trials(research, trials) if trials else candidates(research, top)
    data, fp = load(base)
    plain, dearer = {}, {}                      # 0050's runs, one cache per cost model

    def measured(rule, cost_model, cache):
        if isinstance(rule, BlendRule):
            return report_metrics(evaluate_blend(data, fp, rule, cost_model, cache), ENGINE_VERSION, SEED_PLAN)
        return report_metrics(evaluate(data, fp, rule, cost_model, cache), ENGINE_VERSION, SEED_PLAN)

    rows = []
    for number, (record, spec) in enumerate(chosen):
        rule = parse_spec(spec)
        if job:
            job.update(done=number, total=len(chosen), current=f"{record.spec_name[:40]}", force=True)
        row = {"trial": record.trial_id, "rule_hash": record.spec_hash, "name": record.spec_name,
               "base": _summary(record.metrics, tier)}
        row["costs_doubled"] = _summary(measured(rule, costs.scaled(COST_FACTOR), dearer), tier)
        if isinstance(rule, BlendRule):         # every sleeve filled the next session's close where it was same-day
            late = BlendRule(name=rule.name, core=rule.core, sleeves=tuple(
                Sleeve(rule=sleeve.rule.model_copy(update={"execution": "next"}) if sleeve.rule.execution == "same"
                       else sleeve.rule, share=sleeve.share) for sleeve in rule.sleeves))
            changed = any(sleeve.rule.execution == "same" for sleeve in rule.sleeves)
        else:
            late = rule.model_copy(update={"execution": "next"})
            changed = rule.execution == "same"
        if changed:
            row["next_session"] = _summary(measured(late, costs, plain), tier)
        if opens_auction(rule):
            from quant_platform.research import daily

            kept = daily.OPEN_PARTICIPATION
            daily.OPEN_PARTICIPATION = OPEN_TIGHT
            try:
                row["open_tight"] = _summary(measured(rule, costs, plain), tier)
            finally:
                daily.OPEN_PARTICIPATION = kept
        rows.append(row)
    result = {"engine": ENGINE_VERSION, "cost_factor": COST_FACTOR, "open_tight": OPEN_TIGHT, "rows": rows,
              "built_at": datetime.now(UTC).isoformat(timespec="seconds")}
    folder = research / "robustness"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{datetime.now(UTC):%Y%m%d-%H%M%S}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1),
                                                                   encoding="utf-8")
    return result


def latest(research: Path) -> dict | None:
    """The checks for the research page (research/robustness/<stamp>.json): the newest file's, with the rows of the
    earlier files on the same engine that it did not run again (2026-10-22: a check of three rules no longer hides
    the eight checked before), by the original end value."""
    folder = Path(research) / "robustness"
    files = sorted(folder.glob("*.json")) if folder.is_dir() else []
    found = []
    for path in reversed(files):
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            continue
    if not found:
        return None
    newest = dict(found[0])
    rows, seen = [], set()
    for result in found:
        if result.get("engine") != newest.get("engine"):
            continue
        for row in result.get("rows") or []:
            if row.get("trial") not in seen:
                seen.add(row.get("trial"))
                rows.append(row)
    rows.sort(key=lambda row: -((row.get("base") or {}).get("final_value") or 0))
    newest["rows"] = rows
    return newest


def _summary(metrics: dict, tier) -> dict[str, object]:
    return {"final_value": round(metrics.get("final_value") or 0), "since_2020_final_value":
            round(metrics.get("since_2020_final_value") or 0), "benchmark_final_value": round(metrics.get("benchmark_final_value") or 0),
            "since_2020_benchmark_final_value": round(metrics.get("since_2020_benchmark_final_value") or 0),
            "max_drawdown": metrics.get("max_drawdown"), "cost_share": metrics.get("cost_share"),
            "tier": tier(metrics)[0], "reason": tier(metrics)[1]}
