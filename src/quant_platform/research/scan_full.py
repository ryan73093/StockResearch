"""2026-10-26 (使用者：2015 到 2020 和 2020 到現在交易的風氣已經差了很多，你要嘛就全部都測過一遍，不要再用 2015～2020 作為一開始
的篩選；你也沒有上億的策略；過門檻的怎麼可能只有 26 個；付費的資料你練了沒；對我做最嚴格最完整最全面最細節的計算，不要做半套；
我就是要你找出上億個): the full-period search, written down before it runs (research_method §7「全段大搜尋」).

- Signals: every searchable signal (``scan.signals``: factors and the models that passed their gates) and the ones the
  earlier searches left out that are signals — the paid tables (holders, margin, loans, block trades, brokers, the
  eight banks, day trading) and the corporate notices (buybacks, insiders, pledges). Not searched: the monthly revenue
  (使用者：落後指標), the market value (使用者：不要變相複製 0050), the risk models (vetoes, not rankings), the
  branches (their download ends about 10/30; a round of their own then).
- Phase a: every set of 1 to 4 signals; phase b: every set of 5 of the 50 signals passing phase a most often. Each set
  × two stock pools × two weightings × daily or weekly × three holding styles: about 112 million different settings.
- Stage 1 on the GPU over 2015-06 to the month's cutoff (the monthly list's months), in PARTS processes side by side:
  a setting passes when it beats 0050 over the whole of it AND since 2020-10, falls at most DRAWDOWN_ROOM further than
  0050 and wins at least half of the one-year windows (``passes``).
- Stage 2: the passing settings by their excess since 2020-10, the best POOL simulated again, taken greedily while their
  daily excess correlates at most CORRELATION with one already taken or with an earlier search's finalist, up to
  FINALISTS a phase; each run in the full engine (WORKERS processes) and registered; the passing ones' half-guard
  twins too. Then the month's list grades them, their accounts start the day after the cutoff.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime
from pathlib import Path

import numpy as np

VERSION = "scan-full-1.0.0"
RECENT = date(2020, 10, 1)
DRAWDOWN_ROOM = 0.15
CORRELATION = 0.9
FINALISTS = 1500
POOL = 60_000
TOP_SIGNALS = 50
PARTS = 2
WORKERS = 2
SIZES = {"a": [1, 2, 3, 4], "b": [5]}
EXTRA = ("big_holder_pct", "big_holder_up_4w", "low_pledge_loan_20", "margin_in_profit", "block_premium_20",
         "block_volume_20", "broker_concentration_5", "govbank_net_20", "govbank_dip_buy_20", "low_daytrade_5",
         "buyback_60", "low_insider_sell_60", "low_pledge", "pledge_heavy")
NOT_SEARCHED = {"revenue_yoy": "月營收是落後指標（使用者 2026-10-10）", "revenue_yoy_3m": "月營收是落後指標",
                "revenue_accel": "月營收是落後指標", "market_cap": "不要變相複製 0050（使用者 2026-10-09）",
                "dl_risk": "風險模型用來否決，不是排名", "dl_risk2": "風險模型用來否決，不是排名",
                "branch_informed_20": "分點資料約 10/30 才下載完，下載完另跑一輪"}


def signals() -> list[str]:
    from quant_platform.research import scan

    base = scan.signals()
    return base + [name for name in EXTRA if name not in base]


def folder(research: Path, cutoff: date, phase: str) -> Path:
    return Path(research) / "scans" / f"{VERSION}-{cutoff.isoformat()}-{phase}"


def metrics_block(returns: np.ndarray, bench: np.ndarray, days: list[date], entries: np.ndarray) -> list[dict]:
    """``scan.metrics`` for a block of candidates at once (candidate × session returns), plus ``recent``: the yearly
    excess over 0050 from the first session of RECENT on."""
    count, width = returns.shape
    years = (days[-1] - days[0]).days / 365.25
    value = np.cumprod(1 + returns, axis=1)
    base = np.cumprod(1 + bench[:width])
    drawdown = np.min(value / np.maximum.accumulate(value, axis=1) - 1, axis=1)
    bench_drawdown = float(np.min(base / np.maximum.accumulate(base) - 1))
    excess = (value[:, -1] / base[-1]) ** (1 / years) - 1
    starts = np.arange(0, width - 245, 21)
    if len(starts):
        relative = value[:, starts + 245] / value[:, starts] - (base[starts + 245] / base[starts])[None, :]
        win, worst = np.mean(relative > 0, axis=1), np.min(relative, axis=1)
    else:
        win = worst = np.full(count, np.nan)
    middle = width // 2
    first = ((value[:, middle] / 1.0) / (base[middle] / 1.0)) ** (1 / max(middle / 245, 1e-9)) - 1
    second = (((value[:, width - 1] / value[:, middle - 1]) / (base[width - 1] / base[middle - 1]))
              ** (1 / max((width - 1 - middle) / 245, 1e-9)) - 1)
    split = next(index for index, day in enumerate(days) if day >= RECENT)           # its return is column ``split``
    recent_years = (days[-1] - days[split]).days / 365.25
    before = value[:, split - 1] if split else np.ones(count)
    bench_before = base[split - 1] if split else 1.0
    recent = ((value[:, -1] / before) / (base[-1] / bench_before)) ** (1 / recent_years) - 1
    annual = value[:, -1] ** (1 / years) - 1
    return [{"excess": round(float(excess[row]), 5), "annual": round(float(annual[row]), 5),
             "drawdown": round(float(drawdown[row]), 4), "bench_drawdown": round(bench_drawdown, 4),
             "win_1y": round(float(win[row]), 4) if len(starts) else None,
             "worst_1y": round(float(worst[row]), 4) if len(starts) else None,
             "first_half": round(float(first[row]), 5), "second_half": round(float(second[row]), 5),
             "recent": round(float(recent[row]), 5), "entries_per_month": round(float(entries[row]) / (years * 12), 2)}
            for row in range(count)]


def passes(row: dict) -> bool:
    """Beats 0050 over the whole period and since 2020-10, falls at most DRAWDOWN_ROOM further, wins half the years."""
    return (row["excess"] > 0 and row["recent"] > 0 and row["drawdown"] >= row["bench_drawdown"] - DRAWDOWN_ROOM
            and (row["win_1y"] or 0) >= 0.5)


def _progress_path(out: Path, part: int, parts: int) -> Path:
    return out / f"progress-{part}-of-{parts}.json"


def screen_part(arrays_folder: Path, names: list[str], sizes: list[int], costs, out: Path, part: int, parts: int,
                job=None, accounts=None, universes=None) -> dict[str, object]:
    """Stage 1 for the blocks numbered ``part`` modulo ``parts`` (another process takes the others); passing rows to
    ``screen-p<part>-<block>.jsonl``, every SAMPLE_EVERY-th candidate's daily excess to ``sample-<block>.excess.npy``
    (the effective trials); goes on from its own progress file after a stop."""
    from quant_platform.research.scan import load_arrays
    from quant_platform.research.scan_large import HOLDS, SAMPLE_EVERY, UNIVERSES, _with_room, blocks, total

    universes = tuple(universes or UNIVERSES)
    out.mkdir(parents=True, exist_ok=True)
    arrays = load_arrays(arrays_folder)
    if accounts is None:
        from quant_platform.research.scan_gpu import Device
        from quant_platform.research.scan_gpu import accounts as gpu_accounts

        device = Device(arrays)

        def accounts(chunk):
            return gpu_accounts(device, chunk, costs)
    bench = arrays.bench
    path = _progress_path(out, part, parts)
    progress = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
        "version": VERSION, "part": part, "parts": parts, "names": names, "sizes": sizes,
        "total": total(names, sizes, HOLDS, universes), "screened": 0, "passed": 0, "next_block": 0,
        "started_at": datetime.now().isoformat(timespec="seconds")}
    began, counted = time.time(), progress["screened"]
    index = 0
    for number, chunk in blocks(names, sizes, HOLDS, universes=universes):
        if number < progress["next_block"] or number % parts != part:
            index += len(chunk)
            continue
        returns, entries = _with_room(accounts, chunk, job)
        rows = metrics_block(np.asarray(returns, dtype=np.float64), bench, arrays.days, np.asarray(entries))
        lines = [json.dumps({**item, **row, "block": number}, ensure_ascii=False)
                 for item, row in zip(chunk, rows, strict=True) if passes(row)]
        if lines:
            (out / f"screen-p{part}-{number:07d}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
        sampled = [offset for offset in range(len(chunk)) if (index + offset) % SAMPLE_EVERY == 0]
        if sampled:
            excess = np.asarray(returns)[sampled] - bench[None, : np.asarray(returns).shape[1]]
            np.save(out / f"sample-{number:07d}.excess.npy", excess.astype(np.float16))
        index += len(chunk)
        progress.update(screened=progress["screened"] + len(chunk), passed=progress["passed"] + len(lines),
                        next_block=number + 1, updated_at=datetime.now().isoformat(timespec="seconds"))
        rate = (progress["screened"] - counted) / max(1e-9, time.time() - began) * 60
        progress["per_minute"] = round(rate)
        progress["eta_hours"] = round((progress["total"] / parts - progress["screened"]) / max(rate, 1) / 60, 1)
        partial = path.with_suffix(".partial")
        partial.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")
        partial.replace(path)
        if job:
            job.update(done=progress["screened"], total=progress["total"] // parts,
                       current=f"第一階段（第 {part + 1}／{parts} 份）：{progress['screened']:,} 個，過初篩 "
                               f"{progress['passed']:,}，每分鐘 {progress['per_minute']:,} 個，約 {progress['eta_hours']} 小時")
    progress["finished_at"] = datetime.now().isoformat(timespec="seconds")
    path.write_text(json.dumps(progress, ensure_ascii=False, indent=1), encoding="utf-8")
    return progress


def passing_rows(out: Path):
    for path in sorted(out.glob("screen-p*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                yield json.loads(line)


def best_rows(out: Path, pool: int = POOL) -> tuple[list[dict], int]:
    """(the ``pool`` passing rows with the most excess since 2020-10, then the whole period's; how many passed) —
    streamed, the passing rows can be millions."""
    import heapq

    counted = 0

    def keyed():
        nonlocal counted
        for row in passing_rows(out):
            counted += 1
            yield row
    best = heapq.nlargest(pool, keyed(), key=lambda row: (row["recent"], row["excess"]))
    return best, counted


def frequent(out: Path, count: int) -> list[str]:
    """The ``count`` signals in the most passing settings (phase b's signals, from phase a)."""
    seen: Counter = Counter()
    for path in sorted(out.glob("screen-p*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                seen.update(json.loads(line)["factors"])
    return [name for name, _count in seen.most_common(count)]


KEY = ("universe", "weighting", "check", "hold")


def simulate(device, items: list[dict], costs, bench: np.ndarray) -> np.ndarray:
    """Each item's daily excess over 0050 (rows in the items' order), on the GPU by shared key."""
    from quant_platform.research.scan_gpu import BATCH
    from quant_platform.research.scan_gpu import accounts as gpu_accounts

    output = np.zeros((len(items), len(bench) - 1), dtype=np.float32)
    groups: dict[tuple, list[int]] = {}
    for position, item in enumerate(items):
        groups.setdefault(tuple(item.get(key, "days") for key in KEY), []).append(position)
    for positions in groups.values():
        for start in range(0, len(positions), BATCH):
            part = positions[start:start + BATCH]
            returns, _entries = gpu_accounts(device, [items[position] for position in part], costs)
            output[part] = (returns - bench[None, : returns.shape[1]]).astype(np.float32)
    return output


def choose(arrays, rows: list[dict], seeds: list[dict], costs, count: int = FINALISTS, pool: int = POOL,
           device=None, simulate_fn=None) -> list[dict]:
    """The passing rows by their excess since 2020-10 (then the whole period's), the first ``pool`` simulated again,
    taken greedily while their daily excess correlates at most CORRELATION with any taken one or ``seeds``."""
    import torch

    ranked = sorted(rows, key=lambda row: (-row["recent"], -row["excess"]))[:pool]      # ``rows``: best_rows' already
    run = simulate_fn or (lambda items: simulate(device, items, costs, arrays.bench))
    seeds = [seed for seed in seeds if set(seed["factors"]) <= set(arrays.names)]

    def unit(matrix: np.ndarray):
        tensor = torch.from_numpy(matrix.astype(np.float32))
        if torch.cuda.is_available():
            tensor = tensor.cuda()
        centered = tensor - tensor.mean(1, keepdim=True)
        norm = centered.norm(dim=1, keepdim=True)
        return centered / torch.where(norm > 0, norm, torch.ones_like(norm)), (norm[:, 0] > 0)
    taken_rows: list[dict] = []
    held = None
    if seeds:
        held, _alive = unit(run(seeds))
    for start in range(0, len(ranked), 4096):
        part = ranked[start:start + 4096]
        vectors, alive = unit(run([{key: row[key] for key in ("factors", *KEY) if key in row} for row in part]))
        for offset, row in enumerate(part):
            if len(taken_rows) >= count:
                return taken_rows
            if not bool(alive[offset]):
                continue
            vector = vectors[offset]
            if held is not None and bool((held @ vector > CORRELATION).any()):
                continue
            taken_rows.append(row)
            held = vector[None, :] if held is None else torch.cat([held, vector[None, :]])
    return taken_rows


_worker: dict = {}


def _init(base: str, broker: str) -> None:
    from quant_platform.research import daily
    from quant_platform.research.costs import broker_costs
    from quant_platform.research.registry import TrialRegistry

    data, fp = daily.load(Path(base))
    research = Path("instance") / "research"
    _worker.update(data=data, fp=fp, costs=broker_costs(broker), cache={}, fingerprint=daily.fingerprint(Path(base)),
                   registry=TrialRegistry(research / "trials.jsonl"), reports=research / "reports", base=Path(base))


def _run(spec: dict) -> dict:
    from quant_platform.research import daily
    from quant_platform.research.blend import parse_spec

    rule = parse_spec(spec)
    try:
        record, report = daily.run_trial(rule, _worker["base"], _worker["registry"], _worker["reports"],
                                         _worker["costs"], _worker["data"], _worker["fp"], _worker["fingerprint"],
                                         _worker["cache"])
    except Exception as error:  # noqa: BLE001 - one rule's failure is recorded, the rest go on
        return {"name": rule.name, "error": str(error)[:200]}
    return {"trial_id": record.trial_id, "name": rule.name, "rule_hash": rule.rule_hash,
            "full_excess": report["full_period_excess"], "since_2020": report["since_2020"]["excess"],
            "drawdown": report["strategy"]["max_drawdown"], "tier": daily.tier(record.metrics)[0]}


def engine(rules: list, base: Path, broker: str, workers: int = WORKERS, job=None, label: str = "") -> list[dict]:
    """Each rule in the full engine, ``workers`` processes side by side (each loads the data once); registered."""
    output = []
    with ProcessPoolExecutor(max_workers=max(1, workers), initializer=_init, initargs=(str(base), broker)) as pool:
        for done, row in enumerate(pool.map(_run, [rule.canonical() for rule in rules], chunksize=2), start=1):
            output.append(row)
            if job:
                job.update(done=done, total=len(rules), current=f"{label}第二階段（{done}／{len(rules)}）：{row.get('name', '')[:40]}"
                           f"{'：' + row['tier'] if row.get('tier') else ''}")
    return output


def stage_one(research: Path, cutoff: date, phase: str, broker: str, parts: int = PARTS, job=None) -> list[dict]:
    """Every part of a phase's stage 1, each in its own process (``scan-full --phase X --part k/n``); waited for."""
    out = folder(research, cutoff, phase)
    done = [_progress_path(out, part, parts) for part in range(parts)]
    if all(path.is_file() and json.loads(path.read_text(encoding="utf-8")).get("finished_at") for path in done):
        return [json.loads(path.read_text(encoding="utf-8")) for path in done]
    processes = [subprocess.Popen([sys.executable, "-m", "quant_platform.research", "scan-full", "--phase", phase,
                                   "--part", f"{part}/{parts}", "--broker", broker], env=os.environ.copy())
                 for part in range(parts)]
    while any(process.poll() is None for process in processes):
        if job:
            states = [json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {} for path in done]
            screened = sum(state.get("screened", 0) for state in states)
            total = states[0].get("total", 0) if states and states[0] else 0
            job.update(done=screened, total=total, current=f"第一階段（{parts} 個程序）：{screened:,}／{total:,} 個，過初篩 "
                       f"{sum(state.get('passed', 0) for state in states):,}，約 "
                       f"{max((state.get('eta_hours') or 0) for state in states)} 小時", force=True)
        time.sleep(60)
    codes = [process.returncode for process in processes]
    if any(codes):
        raise RuntimeError(f"第一階段的程序沒有正常結束：{codes}")
    return [json.loads(path.read_text(encoding="utf-8")) for path in done]


def _prepare(base: Path, costs, arrays_folder: Path) -> None:
    from quant_platform.research import daily, scan

    data, fp = daily.load(base)
    scan.prepare(data, fp, costs, arrays_folder, names=signals())


def run_phase(base: Path, research: Path, costs, broker: str, phase: str, cutoff: date, job=None) -> dict[str, object]:
    """A phase: arrays (phase a makes them), stage 1 in parallel parts, the finalists, the engine, the summary."""
    from quant_platform.research import daily, scan
    from quant_platform.research.pool import record_sources
    from quant_platform.research.registry import TrialRegistry
    from quant_platform.research.scan_gpu import Device

    started = time.time()
    scan.DEV_END = cutoff
    out = folder(research, cutoff, phase)
    out.mkdir(parents=True, exist_ok=True)
    arrays_folder = folder(research, cutoff, "a") / "arrays"
    if not (arrays_folder / "meta.json").is_file():
        if job:
            job.update(current="準備 2015-06 到截止日的陣列（全部訊號的每日排名）", force=True)
        _prepare(base, costs, arrays_folder)          # the loaded data (about 4 GB) is let go before the long parts
    arrays = scan.load_arrays(arrays_folder)
    if phase == "a":
        names = [name for name in signals() if name in arrays.names]
    else:
        names = frequent(folder(research, cutoff, "a"), TOP_SIGNALS)
    plan = {"version": VERSION, "phase": phase, "names": names, "sizes": SIZES[phase], "cutoff": cutoff.isoformat()}
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    states = stage_one(research, cutoff, phase, broker, job=job)
    rows, passed = best_rows(out)
    if job:
        job.update(current=f"挑出彼此不相似的（{passed:,} 個過初篩，先看 2020-10 以後最好的 {len(rows):,} 個）", force=True)
    device = Device(arrays)
    chosen = choose(arrays, rows, scan.earlier_finalists(research), costs, device=device)
    rules = [scan.to_rule({**row, "named_hold": True}) for row in chosen]
    record_sources(research, rules, f"全段大搜尋 {out.name}")
    results = engine(rules, base, broker, job=job, label="全段大搜尋")
    for row, result in zip(chosen, results, strict=True):
        result.update(screen_excess=row["excess"], screen_recent=row["recent"], hold=row.get("hold"))
    passing = [rule for rule, result in zip(rules, results, strict=True) if result.get("tier") in daily.PASSING_TIERS]
    registry = TrialRegistry(research / "trials.jsonl")
    twins = daily.half_twins(passing, {record.spec_hash for record in registry.records()})
    if twins:
        record_sources(research, twins, f"全段大搜尋 {out.name}（減碼一半版本）")
    twin_results = engine(twins, base, broker, job=job, label="減碼一半版本") if twins else []
    screened = sum(state["screened"] for state in states)
    summary = {"version": VERSION, "phase": phase, "development": [str(arrays.days[0]), cutoff.isoformat()],
               "screen": {"recent_from": RECENT.isoformat(), "drawdown_room": DRAWDOWN_ROOM, "correlation": CORRELATION,
                          "pool": POOL, "finalists": FINALISTS},
               "signals": names, "sizes": SIZES[phase], "candidates": screened, "passed_screen": passed,
               "effective_trials": scan.effective_trials(out, pattern="sample-*.excess.npy", screened=screened),
               "finalists": len(chosen), "results": results, "half_twins": twin_results,
               "tiers": dict(Counter(result.get("tier") for result in results)),
               "holds_of_pool": dict(Counter(row.get("hold") for row in rows)),
               "minutes": round((time.time() - started) / 60 + sum(
                   (datetime.fromisoformat(state["finished_at"]) - datetime.fromisoformat(state["started_at"])).total_seconds()
                   / 60 for state in states) / max(1, len(states)), 1)}
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
