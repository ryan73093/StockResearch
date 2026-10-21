"""2026-10-21 (使用者：2015 到現在其實也很久了，你應該要去評估這麼久的資料跟短歷史的權重要用哪一個): how much of a
rule's history predicts its next 12 months against 0050 — window lengths, long-short blends and exponential decay —
measured on every registered rule's monthly gaps to the same money in 0050 (docs/research_method.md §7「長歷史與短歷史
的權重」; the design and the criteria were written before the run).

Every score at a cutoff uses only the months up to it; the outcome is the sum of the next HORIZON months' gaps. Each
cutoff's rank correlation (Spearman) between score and outcome across the rules; the top tenth's outcome and how many
of them beat 0050. The reliability of a short history: the last 60 months' mean correlation over the whole history's,
at the cutoffs with more than LONG_ENOUGH months behind them (closest to today's 5 years against 11)."""

from __future__ import annotations

import json
import math
import statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

TAIPEI = ZoneInfo("Asia/Taipei")
FIRST_MONTH = "2015-07"                 # a rule in the study has a gap for this month (it started by then)
WINDOWS = (12, 24, 36, 48, 60, 84, None)
SHORT = 60                              # the short history: 5 years, as the data from 2021-06 gives today
BLENDS = (0.0, 0.25, 0.5, 0.75, 1.0)    # weight on the whole history; the rest on the last SHORT months
HALF_LIVES = (12, 24, 36, 60, None)
HORIZON = 12
STEP = 3
FIRST_CUTOFF, LAST_CUTOFF = "2017-08", "2025-08"
LONG_ENOUGH = 84
BETTER_BY, BETTER_SHARE, RELIABLE = 0.03, 0.6, 0.8          # the criteria written first


def methods() -> list[str]:
    return ([f"window:{'all' if months is None else months}" for months in WINDOWS]
            + [f"blend:{weight}" for weight in BLENDS] + [f"decay:{'none' if half is None else half}" for half in HALF_LIVES])


def score(past: np.ndarray, method: str) -> np.ndarray:
    """rules × months up to the cutoff (NaN where a rule has none) → each rule's score by ``method``."""
    kind, value = method.split(":")
    with np.errstate(invalid="ignore"):
        if kind == "window":
            part = past if value == "all" else past[:, -int(value):]
            return np.nanmean(part, axis=1)
        if kind == "blend":
            weight = float(value)
            return weight * np.nanmean(past, axis=1) + (1 - weight) * np.nanmean(past[:, -SHORT:], axis=1)
        ages = np.arange(past.shape[1])[::-1]                       # 0 for the cutoff's month
        weights = np.ones(past.shape[1]) if value == "none" else 0.5 ** (ages / float(value))
        present = np.isfinite(past)
        total = np.where(present, weights, 0.0).sum(axis=1)
        return np.where(total > 0, np.nansum(np.where(present, past, 0.0) * weights, axis=1) / np.where(total > 0, total, 1), np.nan)


def spearman(left: np.ndarray, right: np.ndarray) -> float | None:
    if len(left) < 10:
        return None
    value = pd.Series(left).rank().corr(pd.Series(right).rank())
    return None if value is None or not math.isfinite(value) else float(value)


def load(research: str | Path) -> tuple[list[str], list[str], np.ndarray]:
    """The newest report of every registered 100%-stock daily rule and blend (research/rolling.py's list): names, the
    months, the gaps (rules × months; NaN before a rule starts). Only rules with a gap in FIRST_MONTH."""
    from quant_platform.research.rolling import rules
    from quant_platform.research.registry import TrialRegistry, newest

    research = Path(research)
    wanted = {item["trial"] for item in rules(research)}
    latest = newest(TrialRegistry(research / "trials.jsonl").records(),
                    lambda record: record.kind == "candidate" and record.period == "recent"
                    and str((record.metrics or {}).get("engine", "")).startswith("daily-"))
    series: dict[str, dict[str, float]] = {}
    for record in latest.values():
        if record.trial_id not in wanted:
            continue
        try:
            report = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        gaps = report.get("monthly_active_returns") or {}
        if FIRST_MONTH in gaps:
            series[f"#{record.trial_id} {report.get('spec', {}).get('name', '')}"[:80]] = gaps
    months = sorted({month for gaps in series.values() for month in gaps})
    names = sorted(series)
    matrix = np.array([[series[name].get(month, np.nan) for month in months] for name in names], dtype=float)
    return names, months, matrix


def study(months: list[str], gaps: np.ndarray) -> dict[str, object]:
    """Every method at every cutoff (STEP months apart, FIRST_CUTOFF to LAST_CUTOFF): the rank correlation with the
    next HORIZON months, and the top tenth's outcome; then the summary and the criteria's verdicts."""
    cutoffs = [index for index, month in enumerate(months) if FIRST_CUTOFF <= month <= LAST_CUTOFF
               and (index - months.index(FIRST_CUTOFF)) % STEP == 0 and index + HORIZON < len(months)]
    rows = []
    for cut in cutoffs:
        future = gaps[:, cut + 1:cut + 1 + HORIZON]
        outcome = np.where(np.isfinite(future).all(axis=1), future.sum(axis=1), np.nan)
        for method in methods():
            values = score(gaps[:, :cut + 1], method)
            mask = np.isfinite(values) & np.isfinite(outcome)
            correlation = spearman(values[mask], outcome[mask])
            if correlation is None:
                continue
            chosen = values[mask] >= np.quantile(values[mask], 0.9)
            rows.append({"cutoff": months[cut], "history": cut + 1, "method": method, "ic": correlation,
                         "rules": int(mask.sum()), "top_outcome": float(outcome[mask][chosen].mean()),
                         "top_beat": float((outcome[mask][chosen] > 0).mean()), "all_outcome": float(outcome[mask].mean())})
    baseline = {row["cutoff"]: row["ic"] for row in rows if row["method"] == "window:all"}
    summary = {}
    for method in methods():
        mine = [row for row in rows if row["method"] == method]
        if not mine:
            continue
        ics = [row["ic"] for row in mine]
        deviation = statistics.stdev(ics) if len(ics) > 1 else 0.0
        long_rows = [row for row in mine if row["history"] > LONG_ENOUGH]
        summary[method] = {
            "cutoffs": len(mine), "ic": round(statistics.fmean(ics), 4),
            "t": round(statistics.fmean(ics) / (deviation / math.sqrt(len(ics))), 2) if deviation else None,
            "better_than_all": round(sum(row["ic"] > baseline[row["cutoff"]] for row in mine) / len(mine), 3),
            "ic_long_history": round(statistics.fmean([row["ic"] for row in long_rows]), 4) if long_rows else None,
            "top_outcome": round(statistics.fmean([row["top_outcome"] for row in mine]), 4),
            "top_beat": round(statistics.fmean([row["top_beat"] for row in mine]), 3),
            "all_outcome": round(statistics.fmean([row["all_outcome"] for row in mine]), 4)}
    whole = summary.get("window:all", {})
    best = max((method for method in summary if method != "window:all"), key=lambda method: summary[method]["ic"],
               default=None)
    adopt = bool(best and summary[best]["ic"] - whole.get("ic", 0.0) >= BETTER_BY
                 and summary[best]["better_than_all"] >= BETTER_SHARE)
    short, full = (summary.get(f"window:{SHORT}") or {}).get("ic_long_history"), whole.get("ic_long_history")
    reliability = round(short / full, 3) if short is not None and full else None
    return {"rows": rows, "summary": summary, "best": best,
            "verdict_main": (f"{best} 比全部等權好 {summary[best]['ic'] - whole.get('ic', 0):+.3f}、"
                             f"{summary[best]['better_than_all']:.0%} 的時間點較好：" + ("過門檻，建議主賽道改用（要使用者同意）"
                                                                                  if adopt else "沒過門檻，維持全部等權"))
            if best else "沒有結果",
            "reliability": reliability,
            "verdict_short": (f"最近 {SHORT} 個月的可信度比 {reliability}："
                              + ("≥ 0.8，短歷史賽道可行（要使用者同意）" if reliability is not None and reliability >= RELIABLE
                                 else "< 0.8，短歷史資料只做描述與前向觀察")) if reliability is not None else "算不出可信度比"}


def run(research: str | Path, out_dir: str | Path) -> Path:
    names, months, gaps = load(research)
    found = study(months, gaps)
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "rules": len(names),
              "months": [months[0], months[-1]] if months else [], "horizon": HORIZON, "step": STEP,
              "criteria": {"better_by": BETTER_BY, "better_share": BETTER_SHARE, "reliable": RELIABLE,
                           "long_enough": LONG_ENOUGH}, **found}
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"window-weight-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
