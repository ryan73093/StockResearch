"""2026-10-20 (使用者：沒看到 1 億個策略不要跟我說有在研究；持有 20 天、月線上續抱都是策略): the search at scale.

The two-stage search (research/scan.py) keeps every candidate in memory and every candidate's daily excess on disk —
fine for a million, not for tens of millions. This streams it: the candidates are made block by block, a block shares
universe × weighting × decision days × holding style (so the GPU runs it at once, ``scan_gpu.accounts``), and of each
block only the candidates that pass the first stage are kept with their daily excess (``screen-s*.jsonl`` and
``.excess.npy``), plus every SAMPLE_EVERY-th candidate's excess for counting effective trials (``sample-*.excess.npy``).
Every block's progress is written (``progress.json``), so a run stopped by a reboot goes on from the next block.

The holding style is a dimension of the search, like the others: a fixed MIN_HOLD sessions ("days"), trend ("trend")
or none ("free"). Stage 2 is the scan's own: the finalists by stability, kept apart from each other and from earlier
scans' finalists, each run in the full engine and registered.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from itertools import combinations
from math import comb
from pathlib import Path

import numpy as np

VERSION = "scan-large-1.0.0"
HOLDS = ("days", "trend", "free")
UNIVERSES = ("all", "large")
WEIGHTINGS = ("equal", "inverse_vol")
CHECKS = ("daily", "weekly")
SAMPLE_EVERY = 500


def keys(holds=HOLDS) -> list[tuple[str, str, str, str]]:
    return [(universe, weighting, check, hold) for universe in UNIVERSES for weighting in WEIGHTINGS
            for check in CHECKS for hold in holds]


def total(names: list[str], sizes: list[int], holds=HOLDS) -> int:
    return sum(comb(len(names), size) for size in sizes) * len(keys(holds))


def blocks(names: list[str], sizes: list[int], holds=HOLDS, block: int = 2048):
    """(block number, candidates sharing one key) in a fixed order — the same numbers on every run."""
    number = 0
    for size in sizes:
        for universe, weighting, check, hold in keys(holds):
            batch = []
            for group in combinations(names, size):
                batch.append({"factors": list(group), "universe": universe, "weighting": weighting, "check": check,
                              "hold": hold, "named_hold": True})
                if len(batch) == block:
                    yield number, batch
                    number += 1
                    batch = []
            if batch:
                yield number, batch
                number += 1


def screen_stream(arrays_folder: Path, names: list[str], sizes: list[int], costs, out: Path, holds=HOLDS, job=None,
                  accounts=None) -> dict[str, object]:
    """Stage 1 over every candidate, block by block (see the module text); returns the progress record."""
    from quant_platform.research.scan import load_arrays, metrics, passes

    out.mkdir(parents=True, exist_ok=True)
    arrays = load_arrays(arrays_folder)
    if accounts is None:
        from quant_platform.research.scan_gpu import Device
        from quant_platform.research.scan_gpu import accounts as gpu_accounts

        device = Device(arrays)

        def accounts(chunk):
            return gpu_accounts(device, chunk, costs)
    years = (arrays.days[-1] - arrays.days[0]).days / 365.25
    bench = arrays.bench
    path = out / "progress.json"
    progress = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
        "version": VERSION, "names": names, "sizes": sizes, "holds": list(holds), "total": total(names, sizes, holds),
        "screened": 0, "passed": 0, "next_block": 0, "started_at": datetime.now().isoformat(timespec="seconds")}
    began, counted = time.time(), progress["screened"]
    index = 0
    for number, chunk in blocks(names, sizes, holds):
        if number < progress["next_block"]:
            index += len(chunk)
            continue
        returns, entries = accounts(chunk)
        excess = (returns - bench[None, : returns.shape[1]]).astype(np.float16)
        kept, sampled, lines = [], [], []
        for offset, (item, row_returns, row_entries) in enumerate(zip(chunk, returns, entries, strict=True)):
            row = {**item, **metrics(row_returns, bench, years, int(row_entries))}
            if passes(row):
                kept.append(offset)
                lines.append(json.dumps(row, ensure_ascii=False))
            if (index + offset) % SAMPLE_EVERY == 0:
                sampled.append(offset)
        if kept:
            (out / f"screen-s{number:06d}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
            np.save(out / f"screen-s{number:06d}.excess.npy", excess[kept])
        if sampled:
            np.save(out / f"sample-{number:06d}.excess.npy", excess[sampled])
        index += len(chunk)
        progress.update(screened=progress["screened"] + len(chunk), passed=progress["passed"] + len(kept),
                        next_block=number + 1, updated_at=datetime.now().isoformat(timespec="seconds"))
        rate = (progress["screened"] - counted) / max(1e-9, time.time() - began) * 60
        progress["per_minute"] = round(rate)
        progress["eta_hours"] = round((progress["total"] - progress["screened"]) / max(rate, 1) / 60, 1)
        partial = path.with_suffix(".partial")
        partial.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")
        partial.replace(path)
        if job:
            job.update(done=progress["screened"], total=progress["total"],
                       current=f"第一階段：{progress['screened']:,}／{progress['total']:,}（過快篩 {progress['passed']:,}，"
                               f"每分鐘 {progress['per_minute']:,} 個，約 {progress['eta_hours']} 小時）")
    progress["finished_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")
    return progress


def run(base: Path, research: Path, costs, sizes: list[int], holds=HOLDS, count: int = 80, job=None,
        folder: Path | None = None) -> dict[str, object]:
    """Stage 1 streamed (``folder``: an unfinished run to go on with), then stage 2 as the scan's; the summary."""
    from collections import Counter

    from quant_platform.research import daily
    from quant_platform.research.registry import TrialRegistry
    from quant_platform.research.scan import (
        DEV_END, earlier_finalists, effective_trials, finalists, load_arrays, passes, prepare, signals, to_rule,
    )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = Path(folder) if folder else research / "scans" / f"{VERSION}-{stamp}"
    folder.mkdir(parents=True, exist_ok=True)
    (research / "scans" / "bigscan-current.txt").write_text(str(folder.resolve()), encoding="utf-8")   # to go on
    if job:
        job.update(current="載入行情與因子", force=True)
    data, fp = daily.load(base)
    fingerprint = daily.fingerprint(base)
    started = time.time()
    if (folder / "arrays").is_dir():
        arrays = load_arrays(folder / "arrays")
    else:
        arrays = prepare(data, fp, costs, folder / "arrays")
    names = [name for name in signals() if name in arrays.names]
    progress = screen_stream(folder / "arrays", names, sizes, costs, folder, holds, job)
    rows = [json.loads(line) for path in sorted(folder.glob("screen-s*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    seeds = earlier_finalists(research)
    chosen = finalists(arrays, rows, costs, count, seeds=seeds, stable=True)
    effective = effective_trials(folder, pattern="sample-*.excess.npy", screened=progress["screened"])
    registry = TrialRegistry(research / "trials.jsonl")
    cache: dict = {}
    results = []
    for index, row in enumerate(chosen):
        rule = to_rule(row)
        if job:
            job.update(current=f"第二階段（{index + 1}／{len(chosen)}）：{rule.name}", force=True)
        record, report = daily.run_trial(rule, base, registry, research / "reports", costs, data, fp, fingerprint, cache,
                                         stamp)
        results.append({"trial_id": record.trial_id, "name": rule.name, "hold": row.get("hold"),
                        "screen_excess": row["excess"], "full_excess": report["full_period_excess"],
                        "since_2020": report["since_2020"]["excess"], "drawdown": report["strategy"]["max_drawdown"],
                        "tier": daily.tier(record.metrics)[0]})
    summary = {"version": VERSION, "stamp": stamp, "development": [str(arrays.days[0]), str(DEV_END)],
               "signals": names, "sizes": sizes, "holds": list(holds), "candidates": progress["screened"],
               "passed_screen": sum(passes(row) for row in rows), "effective_trials": effective,
               "earlier_finalists": len(seeds), "finalists": len(chosen),
               "minutes": round((time.time() - started) / 60, 1), "results": results,
               "tiers": dict(Counter(item["tier"] for item in results)),
               "holds_of_passed": dict(Counter(row.get("hold") for row in rows))}
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (research / "scans" / "bigscan-current.txt").unlink(missing_ok=True)                   # finished
    return summary
