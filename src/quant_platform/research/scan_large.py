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


def keys(holds=HOLDS, universes=UNIVERSES, weightings=WEIGHTINGS) -> list[tuple[str, str, str, str]]:
    return [(universe, weighting, check, hold) for universe in universes for weighting in weightings
            for check in CHECKS for hold in holds]


def total(names: list[str], sizes: list[int], holds=HOLDS, universes=UNIVERSES, required=None,
          weightings=WEIGHTINGS) -> int:
    """Every set of ``sizes`` signals (2026-10-22: with ``required``, only the sets holding at least one of them)."""
    others = len(set(names) - set(required or ())) if required else 0
    return sum(comb(len(names), size) - (comb(others, size) if required else 0) for size in sizes) * len(
        keys(holds, universes, weightings))


def frequent(folder: Path, top: int) -> list[str]:
    """2026-10-20: the ``top`` signals appearing most often in a finished run's first-stage passes (development period
    only), for a larger next round (5 signals a set) on fewer signals."""
    from collections import Counter

    counts: Counter = Counter()
    for path in Path(folder).glob("screen-s*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                counts.update(json.loads(line)["factors"])
    return [name for name, _count in counts.most_common(top)]


def blocks(names: list[str], sizes: list[int], holds=HOLDS, block: int = 2048, universes=UNIVERSES, required=None,
           weightings=WEIGHTINGS):
    """(block number, candidates sharing one key) in a fixed order — the same numbers on every run; 2026-10-22: with
    ``required``, only the sets holding at least one of those signals (a round about new information)."""
    number = 0
    wanted = set(required or ())
    for size in sizes:
        for universe, weighting, check, hold in keys(holds, universes, weightings):
            batch = []
            for group in combinations(names, size):
                if wanted and not wanted.intersection(group):
                    continue
                batch.append({"factors": list(group), "universe": universe, "weighting": weighting, "check": check,
                              "hold": hold, "named_hold": True})
                if len(batch) == block:
                    yield number, batch
                    number += 1
                    batch = []
            if batch:
                yield number, batch
                number += 1


OOM_RETRIES = 30
OOM_WAIT = 120


def _with_room(accounts, chunk, job=None):
    """``accounts(chunk)``, waiting for the GPU's memory when another process holds it (2026-10-20 22:4x: the month's
    rolling run retrained the deep model on the GPU and the first round stopped on "CUDA error: out of memory"); the
    block is tried again after OOM_WAIT seconds, at most OOM_RETRIES times. Any other error stops the run as before."""
    for attempt in range(OOM_RETRIES + 1):
        try:
            return accounts(chunk)
        except Exception as error:  # noqa: BLE001 - torch's error types differ by version; matched by text
            if "out of memory" not in str(error).lower() or attempt == OOM_RETRIES:
                raise
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:  # noqa: BLE001
                pass
            if job:
                job.update(current=f"顯示卡記憶體不足（另一個程序在用），{OOM_WAIT} 秒後重試這一批（第 {attempt + 1} 次）",
                           force=True)
            time.sleep(OOM_WAIT)
    raise RuntimeError("unreachable")


def screen_stream(arrays_folder: Path, names: list[str], sizes: list[int], costs, out: Path, holds=HOLDS, job=None,
                  accounts=None, universes=UNIVERSES, required=None, weightings=WEIGHTINGS) -> dict[str, object]:
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
        "version": VERSION, "names": names, "sizes": sizes, "holds": list(holds), "universes": list(universes),
        "weightings": list(weightings),
        "required": list(required or ()), "total": total(names, sizes, holds, universes, required, weightings),
        "screened": 0, "passed": 0, "next_block": 0, "started_at": datetime.now().isoformat(timespec="seconds")}
    universes = tuple(progress.get("universes") or UNIVERSES)      # a resumed run keeps its own
    required = progress.get("required") or None
    weightings = tuple(progress.get("weightings") or WEIGHTINGS)
    began, counted = time.time(), progress["screened"]
    index = 0
    for number, chunk in blocks(names, sizes, holds, universes=universes, required=required, weightings=weightings):
        if number < progress["next_block"]:
            index += len(chunk)
            continue
        returns, entries = _with_room(accounts, chunk, job)
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
        folder: Path | None = None, universes=UNIVERSES, only: list[str] | None = None,
        required: list[str] | None = None, pool: int = 600, distinct: bool = False,
        weightings=WEIGHTINGS) -> dict[str, object]:
    """Stage 1 streamed (``folder``: an unfinished run to go on with), then stage 2 as the scan's; the summary.
    ``only``: the signals to combine (default every scan signal); a resumed run keeps the ones it started with.
    2026-11-20 (docs/research/results.md「大搜尋第二階段只看前 600 個」): ``pool`` — how many of the passing
    candidates stage 2 looks through for ``count`` finalists apart from the earlier ones; ``distinct`` — one row per
    signal set (its steadiest setting) first. A finished folder run again keeps its earlier summary
    (summary-<its stamp>.json)."""
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
    started_with = folder / "progress.json"
    if started_with.is_file():
        begun = json.loads(started_with.read_text(encoding="utf-8"))
        names = begun["names"]
        # 2026-11-20: a resumed run keeps its own sizes and holding styles too (stage2again's first step was started
        # without them: the job said 1～3 signals and the summary recorded them though stage 1 had screened 3 and 4)
        sizes = list(begun.get("sizes") or sizes)
        holds = tuple(begun.get("holds") or holds)
    else:
        names = [name for name in (only or signals()) if name in arrays.names]
        names += [name for name in required or () if name in arrays.names and name not in names]
    progress = screen_stream(folder / "arrays", names, sizes, costs, folder, holds, job, universes=universes,
                             required=[name for name in required or () if name in names] or None,
                             weightings=weightings)
    rows = [json.loads(line) for path in sorted(folder.glob("screen-s*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    seeds = earlier_finalists(research)
    if distinct:
        rows = steadiest_per_set(rows)
    chosen = finalists(arrays, rows, costs, count, pool=pool, seeds=seeds, stable=True)
    earlier_summary = folder / "summary.json"
    if earlier_summary.is_file():           # 2026-11-20: stage 2 again on a finished search — the first one kept
        kept_stamp = json.loads(earlier_summary.read_text(encoding="utf-8")).get("stamp", "earlier")
        earlier_summary.replace(folder / f"summary-{kept_stamp}.json")
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
    twins = daily.grade_half_twins([to_rule(row) for row, item in zip(chosen, results) if item["tier"] in daily.PASSING_TIERS],
                                   base, registry, research / "reports", costs, data, fp, fingerprint, cache, stamp,
                                   f"大規模搜尋 {folder.name}（減碼一半版本）", job)
    summary = {"version": VERSION, "stamp": stamp, "development": [str(arrays.days[0]), str(DEV_END)],
               "stage2": {"pool": pool, "distinct_sets": distinct, "rows": len(rows), "earlier": len(seeds),
                          "pool_ran_out": len(chosen) < count},
               "signals": names, "sizes": sizes, "holds": list(holds), "universes": list(progress.get("universes") or universes),
               "candidates": progress["screened"],
               "passed_screen": sum(passes(row) for row in rows), "effective_trials": effective,
               "earlier_finalists": len(seeds), "finalists": len(chosen),
               "minutes": round((time.time() - started) / 60, 1), "results": results,
               "tiers": dict(Counter(item["tier"] for item in results)),
               "holds_of_passed": dict(Counter(row.get("hold") for row in rows)), "half_twins": twins}
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    (research / "scans" / "bigscan-current.txt").unlink(missing_ok=True)                   # finished
    return summary


LOW_BETA = 0.8              # 2026-11-19: the daily beta to 0050 over the development period a "low-beta" set may have
RERANK_POOL = 3000


def beta_and_gap(excess: np.ndarray, bench: np.ndarray, stretches: list[tuple[int, int]]) -> tuple[np.ndarray, np.ndarray]:
    """Each row of stored daily excess returns: its beta to the 0050 daily returns ``bench``, and over 0050's falls
    ``stretches`` (downside.falls) the worst of "the set's change less 0050's" (0 when there is no fall)."""
    base = np.asarray(bench[: excess.shape[1]], dtype=np.float64)
    returns = excess.astype(np.float64) + base[None, :]
    centred = base - base.mean()
    beta = (returns - returns.mean(axis=1, keepdims=True)) @ centred / max(float(centred @ centred), 1e-12)
    gap = np.zeros(len(returns))
    for number, (peak, trough) in enumerate(stretches):
        mine = np.prod(1 + returns[:, peak:trough], axis=1) - 1
        theirs = float(np.prod(1 + base[peak:trough]) - 1)
        gap = mine - theirs if number == 0 else np.minimum(gap, mine - theirs)
    return beta, gap


def steadiest_per_set(rows: list[dict]) -> list[dict]:
    """2026-11-19 (lowbeta's first search gave 4 finalists of 73,624 rows: the 3,000 steadiest were the same few
    signal sets in their 24 settings, each a near copy of the other): one row per signal set — its steadiest setting."""
    from quant_platform.research.scan import stability

    best: dict[tuple[str, ...], dict] = {}
    for row in rows:
        key = tuple(sorted(row["factors"]))
        if key not in best or stability(row) > stability(best[key]):
            best[key] = row
    return list(best.values())


def low_beta_rows(folder: Path, bench: np.ndarray, limit: float = LOW_BETA, pool: int = RERANK_POOL, job=None,
                  distinct: bool = False) -> tuple[list[dict], dict[str, int]]:
    """The passing rows of a finished streamed search (both halves ahead of 0050) whose development-period beta is at
    most ``limit`` and that fell no further than CALM_GAP beyond 0050 in any of its falls — the ``pool`` steadiest —
    and the counts (rows read, stable, low beta, low beta and calm)."""
    from quant_platform.research.downside import falls
    from quant_platform.research.scan import CALM_GAP, passes, stability

    stretches = falls(np.asarray(bench, dtype=np.float64))
    counts = {"rows": 0, "stable": 0, "low_beta": 0, "kept": 0}
    kept: list[dict] = []
    files = sorted(folder.glob("screen-s*.jsonl"))
    for number, path in enumerate(files):
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        excess = np.load(path.with_name(path.name.replace(".jsonl", ".excess.npy")))
        if len(rows) != len(excess):
            continue
        beta, gap = beta_and_gap(excess, bench, stretches)
        counts["rows"] += len(rows)
        for row, row_beta, row_gap in zip(rows, beta, gap, strict=True):
            if not passes(row, True):
                continue
            counts["stable"] += 1
            if row_beta > limit:
                continue
            counts["low_beta"] += 1
            if row_gap < -CALM_GAP:
                continue
            counts["kept"] += 1
            kept.append({**row, "beta": round(float(row_beta), 3), "screen_fall_gap": round(float(row_gap), 4)})
        if len(kept) > 20 * pool:
            kept = sorted(steadiest_per_set(kept) if distinct else kept, key=stability, reverse=True)[:pool]
        if job and number % 200 == 0:
            job.update(done=number, total=len(files), current=f"{folder.name}：讀 {counts['rows']:,} 個、低連動又不多跌 {counts['kept']:,} 個")
    if distinct:
        kept = steadiest_per_set(kept)
        counts["sets"] = len(kept)
    return sorted(kept, key=stability, reverse=True)[:pool], counts


def rerank(base: Path, research: Path, costs, folders: list[Path], count: int = 80, limit: float = LOW_BETA, job=None,
           distinct: bool = False, pool: int = RERANK_POOL) -> dict[str, object]:
    """2026-11-19 (docs/research/results.md「大搜尋改用低連動重挑」): the candidates the earlier streamed searches
    passed, chosen again — low beta to 0050 and no further down than 0050 in its falls, over the development period —
    ``count`` per search, apart from each other and from every earlier finalist; then the engine as a search's stage 2."""
    from collections import Counter

    from quant_platform.research import daily
    from quant_platform.research.registry import TrialRegistry
    from quant_platform.research.scan import earlier_finalists, finalists, load_arrays, to_rule

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = research / "scans" / f"lowbeta-1.0.0-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    seeds = earlier_finalists(research)
    chosen, searched = [], {}
    for folder in folders:
        arrays = load_arrays(folder / "arrays")
        rows, counts = low_beta_rows(folder, arrays.bench, limit, pool=pool, job=job, distinct=distinct)
        picked = finalists(arrays, rows, costs, count, pool=pool, seeds=seeds, stable=True, calm=True)
        searched[folder.name] = {**counts, "finalists": len(picked)}
        print(f"{folder.name}：{counts}，選出 {len(picked)} 個", flush=True)
        seeds = seeds + [row for row in picked if set(row["factors"]) <= set(arrays.names)]
        chosen += [(folder.name, row) for row in picked]
        (out / "chosen.json").write_text(json.dumps({"searched": searched, "chosen": [row for _name, row in chosen]},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
        del arrays
    data, fp = daily.load(base)
    fingerprint = daily.fingerprint(base)
    registry = TrialRegistry(research / "trials.jsonl")
    cache: dict = {}
    results = []
    for index, (source, row) in enumerate(chosen):
        rule = to_rule(row)
        if job:
            job.update(done=index, total=len(chosen), current=f"引擎（{index + 1}／{len(chosen)}）：{rule.name}", force=True)
        record, report = daily.run_trial(rule, base, registry, research / "reports", costs, data, fp, fingerprint, cache, stamp)
        results.append({"trial_id": record.trial_id, "name": rule.name, "from": source, "hold": row.get("hold"),
                        "beta": row.get("beta"), "screen_fall_gap": row.get("screen_fall_gap"),
                        "screen_excess": row["excess"], "full_excess": report["full_period_excess"],
                        "since_2020": report["since_2020"]["excess"], "drawdown": report["strategy"]["max_drawdown"],
                        "tier": daily.tier(record.metrics)[0]})
    summary = {"version": "lowbeta-1.1.0" if distinct else "lowbeta-1.0.0", "stamp": stamp, "limit": limit,
               "distinct_sets": distinct, "pool": pool, "searched": searched, "finalists": len(chosen),
               "results": results, "tiers": dict(Counter(item["tier"] for item in results))}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
