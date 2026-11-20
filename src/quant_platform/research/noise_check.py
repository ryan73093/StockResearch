"""2026-11-20 (docs/research/results.md「回測對小雜訊的穩定度能不能預測前向」): the volume-bias steps showed a rule's
registered money moving by a tenth when every stock-day's turnover is off by about 1.3% — a stock on the eligibility line
or a rank's edge enters a day earlier and the whole path differs. Is a rule whose result holds under such noise one
that held up from 9/1, beyond what its beta says?

The rules passing on both monthly lists with the full chained forward record — up to half the sample the ones that
held up (both moneys not behind 0050), the rest at random from the others (a case-control sample: few held up, 9.6% of
a plain random draw); each run as usual and on NOISE_RUNS panels whose turnover is multiplied stock-day by stock-day by the measured 13:50
ratios divided by their median (noise with no bias, ``volume_bias.lagged_panel``), all in one process, nothing
registered. Written to research/diagnostics/noise-check-<time>.json with each rule's forward result for the judgment.
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
SAMPLE = 400
NOISE_RUNS = 2


def sample(research: Path, count: int = SAMPLE, seed: int = 20261120) -> list[tuple[int, str, object, dict]]:
    """(trial, rule hash, rule, chained forward) of ``count`` single rules passing on both lists with the longest
    chained record, at random within each kind in proportion to the kind's share."""
    from quant_platform.research.categories import classify
    from quant_platform.research.daily import DailyRule
    from quant_platform.research.stock_forward import StockForwardTracker

    lists = sorted((research / "rolling").glob("20??-??-??.json"))[-2:]
    graded = [{row["rule_hash"]: row for row in json.loads(path.read_text(encoding="utf-8"))["graded"]} for path in lists]
    now, before = graded[-1], graded[0]
    chained = StockForwardTracker(research).chained(datetime.now(TAIPEI).date())
    longest = max((value["sessions"] for value in chained.values()), default=0)
    kinds: dict[str, list[tuple[int, str, dict, dict]]] = {}
    for key, row in now.items():
        spec = row.get("spec") or {}
        forward = chained.get(key)
        if (not row.get("passed") or not before.get(key, {}).get("passed") or spec.get("kind") == "blend" or spec.get("free")
                or spec.get("universe", "twse") != "twse" or not forward or forward["sessions"] != longest):
            continue
        kinds.setdefault(str(classify(spec).get("family")), []).append((int(row["trial"]), key, spec, forward))
    everything = sorted((item for rows in kinds.values() for item in rows), key=lambda item: item[0])
    held = [item for item in everything if item[3]["excess"] >= 0 and item[3]["sold_excess"] >= 0]
    others = [item for item in everything if not (item[3]["excess"] >= 0 and item[3]["sold_excess"] >= 0)]
    rng = random.Random(seed)
    cases = rng.sample(held, min(len(held), count // 2))
    controls = rng.sample(others, min(len(others), count - len(cases)))
    picked = []
    for trial, key, spec, forward in sorted(cases + controls, key=lambda item: item[0]):
        try:
            picked.append((trial, key, DailyRule.model_validate(spec), forward))
        except ValueError:
            continue
    return picked


def run(history: Path, research: Path, costs, count: int = SAMPLE, job=None) -> dict[str, object]:
    from quant_platform.research.daily import ENGINE_VERSION, SEED_PLAN, evaluate, load, report_metrics, tier
    from quant_platform.research.downside import returns
    from quant_platform.research.volume_bias import lagged_panel, table_ratios

    data, fp = load(history)
    measured = table_ratios(research, history)
    noise = measured / float(np.median(measured))
    panels = [lagged_panel(history, data, noise, seed=number) for number in range(NOISE_RUNS)]
    rules = sample(research, count)
    rows = []
    for number, (trial, key, rule, forward) in enumerate(rules):
        if job:
            job.update(done=number, total=len(rules), current=rule.name[:40], force=True)
        usual_report = evaluate(data, fp, rule, costs)
        usual = report_metrics(usual_report, ENGINE_VERSION, SEED_PLAN)
        moved = [report_metrics(evaluate(data, panel, rule, costs), ENGINE_VERSION, SEED_PLAN) for panel in panels]
        months, mine, bench = returns(usual_report.get("curve") or {})
        keep = [index for index, month in enumerate(months) if month <= "2026-08"]
        mine, bench = mine[keep][-60:], bench[keep][-60:]
        beta = float(np.cov(mine, bench)[0, 1] / np.var(bench, ddof=1)) if len(bench) > 12 else None
        money = usual.get("since_2020_final_value") or 0.0
        ratios = [float(item.get("since_2020_final_value") or 0.0) / money for item in moved] if money else []
        rows.append({"trial": trial, "rule_hash": key, "name": rule.name, "tier": tier(usual)[0],
                     "tiers_noise": [tier(item)[0] for item in moved], "money": money, "ratios": ratios,
                     "beta": beta, "forward_excess": forward["excess"], "forward_sold": forward["sold_excess"],
                     "forward_beta": forward.get("beta"), "held_up": forward["excess"] >= 0 and forward["sold_excess"] >= 0})
    summary = {"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "rules": len(rows), "noise_runs": NOISE_RUNS,
               "noise_spread": {"p5": float(np.quantile(noise, 0.05)), "p95": float(np.quantile(noise, 0.95))},
               "rows": rows}
    folder = research / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"noise-check-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    summary["path"] = str(path)
    return summary


def judge(found: dict) -> dict[str, object]:
    """The registered judgment's numbers: stability = the worse of the noise runs' money against usual (min ratio); in
    each beta fifth the median stability of the rules that held up against the others', and how many fifths favour them."""
    rows = [row for row in found["rows"] if row["ratios"] and row["beta"] is not None]
    if len(rows) < 20:
        return {"rules": len(rows)}
    stability = np.array([min(row["ratios"]) for row in rows])
    beta = np.array([row["beta"] for row in rows])
    held = np.array([row["held_up"] for row in rows])
    groups, gaps = [], []
    for part in np.array_split(np.argsort(beta), 5):
        up, down = part[held[part]], part[~held[part]]
        if len(up) and len(down):
            gap = float(np.median(stability[up]) - np.median(stability[down]))
            gaps.append(gap)
        else:
            gap = None
        groups.append({"beta": float(beta[part].mean()), "held_up": int(len(up)), "others": int(len(down)),
                       "stability_held": float(np.median(stability[up])) if len(up) else None,
                       "stability_others": float(np.median(stability[down])) if len(down) else None, "gap": gap})
    return {"rules": len(rows), "held_up": int(held.sum()), "stability_median": float(np.median(stability)),
            "fifths_favouring": sum(1 for gap in gaps if gap > 0), "fifths_compared": len(gaps),
            "mean_gap": float(np.mean(gaps)) if gaps else None, "by_beta": groups}
