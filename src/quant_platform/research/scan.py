"""2026-10-10 (使用者：沒測過上千上萬個方法不要說找不到 T0；不要用一樣的參數充數): a broad, two-stage
search over genuinely different 100%-stock strategies.

The space: every signal alone, every pair and every triple of 39 signals — the price, technical, chip,
statement and trend-quality factors (monthly revenue left out: the owner counts it as lagging; market
value left out: ranking on it buys the index's biggest names) and the four trained model scores — each in
two universes (every listed stock; the 100 largest) with two weightings (equal amounts; by calmness).
Every rule holds the top 20, keeps a holding while it ranks in the top 60 or for its first 20 sessions, at
most 3 in 10 from one industry; rules with a model score or a chip factor decide weekly (those scores move
every day), the rest daily. Never 0050.

Stage 1 (screen) uses the development period only (2015-06-01..2020-09-30): a fast vectorised account —
the picks' next-session returns (equal or calmness weights, redrawn daily), costs on every entry and exit
(fee, sell tax, slippage) — against 0050's own unit value. Results are not registered one by one; the scan
folder keeps every candidate's numbers and the count. Stage 2: candidates that beat 0050, keep the drawdown
within 0050's + 5 points and win at least half the 1-year windows, best first, taken greedily so that no
two finalists' daily returns correlate above 0.9, run through the full engine (``daily.run_trial``: both
periods, rolling windows, the gate; registered as trials).
"""

from __future__ import annotations

import json
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

SCAN_VERSION = "scan-1.0.0"
DEV_END = date(2020, 9, 30)
TOP, KEEP, MIN_HOLD, INDUSTRY_SHARE = 20, 3, 20, 0.3
LARGE = 100
CORRELATION_LIMIT = 0.9
LEFT_OUT = ("revenue_yoy", "revenue_yoy_3m", "revenue_accel", "market_cap")
SHORT = {
    "momentum_12_1": "12 月動能", "momentum_6": "6 月動能", "momentum_3": "3 月動能", "reversal_1": "1 月反轉",
    "reversal_5d": "5 日反轉", "low_volatility_60": "60 日低波", "low_volatility_250": "250 日低波",
    "low_max_return": "不追飆股", "high_52w": "近 52 週高", "dividend_yield": "殖利率", "liquidity": "成交值大",
    "volume_surge": "量能放大", "trend_200": "站上 200 日線", "ma_cross_20_60": "均線交叉", "rsi_14": "RSI",
    "macd_hist": "MACD", "kd_k": "KD", "bollinger_b": "布林位置", "breakout_55": "55 日突破",
    "foreign_holding": "外資持股", "foreign_holding_change": "外資持股增", "foreign_buy_20": "外資買超",
    "trust_buy_20": "投信買超", "margin_growth_20": "融資增加", "short_margin_ratio": "券資比",
    "earnings_yield": "本益比低", "book_to_price": "淨值比低", "roe_ttm": "ROE", "gross_margin": "毛利率",
    "operating_margin_change": "營益率增", "eps_growth": "EPS 成長", "low_debt": "負債低", "fip_12": "趨勢連續",
    "imom_12": "日內動能", "resid_mom_12": "殘差動能", "ml_gbm": "模型A", "ml_gbm_excess": "模型B",
    "ml_gbm_statements": "模型C", "ml_gbm_60": "模型D",
}


def signals() -> list[str]:
    from quant_platform.research.daily import FACTOR_LABELS, MODEL_FACTORS

    return [name for name in (*FACTOR_LABELS, *MODEL_FACTORS) if name not in LEFT_OUT]


def weekly(factors: tuple[str, ...]) -> bool:
    from quant_platform.research.chips import CHIP_FACTORS
    from quant_platform.research.daily import MODEL_FACTORS

    return any(name in MODEL_FACTORS or name in CHIP_FACTORS for name in factors)


def candidates(names: list[str] | None = None, largest: int = 3, smallest: int = 1) -> list[dict]:
    """Every set of ``smallest``..``largest`` signals × universe (all, large) × weighting (equal, calm)."""
    names = names or signals()
    output = []
    for size in range(smallest, largest + 1):
        for group in combinations(names, size):
            for universe in ("all", "large"):
                for weighting in ("equal", "inverse_vol"):
                    output.append({"factors": list(group), "universe": universe, "weighting": weighting,
                                   "check": "weekly" if weekly(group) else "daily"})
    return output


def to_rule(candidate: dict):
    from quant_platform.research.daily import DailyRule

    parts = [f"掃描：{'＋'.join(SHORT[name] for name in candidate['factors'])}：前 20 名、同產業最多 3 成"]
    if candidate["universe"] == "large":
        parts.append(f"市值前 {LARGE}")
    if candidate["weighting"] == "inverse_vol":
        parts.append("依波動度配置")
    if candidate["check"] == "weekly":
        parts.append("每週決策")
    return DailyRule(name="、".join(parts), factors={name: 1.0 for name in candidate["factors"]}, top=TOP,
                     check=candidate["check"], keep=KEEP, min_hold=MIN_HOLD, industry_cap=INDUSTRY_SHARE,
                     weighting=candidate["weighting"], large_caps=LARGE if candidate["universe"] == "large" else 0)


# --- stage 1 arrays ------------------------------------------------------------------------------
@dataclass
class Arrays:
    names: list[str]
    percentiles: np.ndarray     # universe (all, large) × signal × symbol × session, float16, NaN = not ranked
    next_returns: np.ndarray    # symbol × session: the return from this session's close to the next
    calm: np.ndarray            # symbol × session: 1 / 60-session volatility (median where unknown)
    industry: np.ndarray        # symbol: industry number
    weekly_days: np.ndarray     # session: a weekly check day
    bench: np.ndarray           # session: 0050's return from this close to the next
    days: list[date]


def prepare(data, fp, costs, folder: Path) -> Arrays:
    """Write the development period's arrays to ``folder`` (memory-mapped by the workers)."""
    from quant_platform.research.daily import RECENT_START, DailyRule, check_days, simulate_daily
    from quant_platform.research.metrics import unit_values
    from quant_platform.research.stock_rules import SeedPlan

    folder.mkdir(parents=True, exist_ok=True)
    positions = [index for index, day in enumerate(fp.sessions) if RECENT_START <= day <= DEV_END]
    first, last = positions[0], positions[-1] + 1          # one more session for the last return
    days = list(fp.sessions[first:last + 1])
    base = DailyRule(name="資格", factors={"trend_200": 1.0})
    eligible = np.column_stack([fp.eligible(base, position) for position in range(first, last + 1)])
    large = eligible & np.column_stack([fp.largest(LARGE, position) for position in range(first, last + 1)])
    names = signals()
    percentiles = np.lib.format.open_memmap(folder / "percentiles.npy", mode="w+", dtype=np.float16,
                                            shape=(2, len(names), len(fp.symbols), len(days)))
    for index, name in enumerate(names):
        values = fp.matrix(name)[:, first:last + 1].astype(np.float64)
        for slot, mask in enumerate((eligible, large)):
            frame = pd.DataFrame(np.where(mask, values, np.nan))
            percentiles[slot, index] = frame.rank(axis=0, pct=True).to_numpy(dtype=np.float16)
    percentiles.flush()
    prices = fp.panel.filled[:, first:last + 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        moves = prices[:, 1:] / prices[:, :-1] - 1               # column i: session i's close to the next
    next_returns = np.zeros((len(fp.symbols), len(days)), dtype=np.float32)
    next_returns[:, :-1] = np.nan_to_num(moves, nan=0.0, posinf=0.0, neginf=0.0)
    volatility = -fp.matrix("low_volatility_60")[:, first:last + 1].astype(np.float64)
    known = np.isfinite(volatility) & (volatility > 0)
    fill = np.nanmedian(np.where(known, volatility, np.nan)) if known.any() else 0.02
    calm = (1 / np.where(known, volatility, fill)).astype(np.float32)
    groups = {group: number for number, group in enumerate(sorted({fp.industry(symbol) for symbol in fp.symbols}))}
    industry = np.array([groups[fp.industry(symbol)] for symbol in fp.symbols], dtype=np.int32)
    weekly_set = check_days(days, "weekly")
    weekly_days = np.array([day in weekly_set for day in days])
    run = simulate_daily(data, None, costs, days[0], days[-1], plan=SeedPlan())
    units = pd.Series(unit_values(run.values, run.flows), index=run.days).reindex(days).ffill().to_numpy()
    bench = np.zeros(len(days), dtype=np.float64)
    bench[:-1] = units[1:] / units[:-1] - 1
    np.save(folder / "next_returns.npy", next_returns)
    np.save(folder / "calm.npy", calm)
    np.save(folder / "industry.npy", industry)
    np.save(folder / "weekly_days.npy", weekly_days)
    np.save(folder / "bench.npy", np.nan_to_num(bench))
    (folder / "meta.json").write_text(json.dumps({"names": names, "days": [day.isoformat() for day in days]},
                                                 ensure_ascii=False), encoding="utf-8")
    return load_arrays(folder)


def load_arrays(folder: Path) -> Arrays:
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    return Arrays(names=meta["names"], percentiles=np.load(folder / "percentiles.npy", mmap_mode="r"),
                  next_returns=np.load(folder / "next_returns.npy"), calm=np.load(folder / "calm.npy"),
                  industry=np.load(folder / "industry.npy"), weekly_days=np.load(folder / "weekly_days.npy"),
                  bench=np.load(folder / "bench.npy"), days=[date.fromisoformat(day) for day in meta["days"]])


# --- stage 1 account -----------------------------------------------------------------------------
def score_of(arrays: Arrays, candidate: dict) -> np.ndarray:
    slot = 0 if candidate["universe"] == "all" else 1
    total = None
    for name in candidate["factors"]:
        values = np.asarray(arrays.percentiles[slot, arrays.names.index(name)], dtype=np.float32)
        total = values if total is None else total + values
    return total


def account(score: np.ndarray, checks: np.ndarray, industry: np.ndarray, next_returns: np.ndarray,
            calm: np.ndarray | None, cost_in: float, cost_out: float, record: bool = False):
    """Daily returns after costs of the top-20 rule on ``score`` (symbols × sessions, NaN = not ranked),
    deciding on ``checks``; with ``record`` also the holdings after each session."""
    sessions = score.shape[1]
    limit = max(1, int(INDUSTRY_SHARE * TOP))
    held: list[int] = []
    bought: dict[int, int] = {}
    previous: dict[int, float] = {}
    returns = np.zeros(sessions - 1)
    entries = 0
    history = []
    for t in range(sessions - 1):
        if checks[t]:
            column = score[:, t]
            valid = np.flatnonzero(~np.isnan(column))
            if valid.size == 0:
                held = []
            else:
                order = valid[np.argsort(-column[valid], kind="stable")].tolist()
                zone = set(order[: TOP * KEEP])
                kept = [row for row in held
                        if row in zone or (not np.isnan(column[row]) and t - bought[row] < MIN_HOLD)]
                taken = set(kept)
                counts = Counter(int(industry[row]) for row in kept)
                for row in order:
                    if len(taken) >= TOP:
                        break
                    if row in taken:
                        continue
                    group = int(industry[row])
                    if counts[group] >= limit:
                        continue
                    counts[group] += 1
                    taken.add(row)
                    kept.append(row)
                held = kept
            bought = {row: bought.get(row, t) for row in held}
        if record:
            history.append(list(held))
        if not held:
            weights: dict[int, float] = {}
        elif calm is None:
            weights = {row: 1 / len(held) for row in held}
        else:
            raw = np.array([calm[row, t] for row in held], dtype=float)
            weights = {row: float(value) for row, value in zip(held, raw / raw.sum())}
        cost = sum(weight * cost_in for row, weight in weights.items() if row not in previous)
        cost += sum(weight * cost_out for row, weight in previous.items() if row not in weights)
        entries += sum(1 for row in weights if row not in previous)
        returns[t] = sum(weight * next_returns[row, t] for row, weight in weights.items()) - cost
        previous = weights
    return (returns, entries, history) if record else (returns, entries)


def metrics(returns: np.ndarray, bench: np.ndarray, years: float, entries: int) -> dict:
    value = np.cumprod(1 + returns)
    base = np.cumprod(1 + bench[: len(returns)])
    drawdown = float(np.min(value / np.maximum.accumulate(value) - 1))
    bench_drawdown = float(np.min(base / np.maximum.accumulate(base) - 1))
    excess = float((value[-1] / base[-1]) ** (1 / years) - 1)
    wins = [value[start + 245] / value[start] > base[start + 245] / base[start]
            for start in range(0, len(value) - 245, 21)]
    return {"excess": round(excess, 5), "annual": round(float(value[-1] ** (1 / years) - 1), 5),
            "drawdown": round(drawdown, 4), "bench_drawdown": round(bench_drawdown, 4),
            "win_1y": round(float(np.mean(wins)), 4) if wins else None,
            "entries_per_month": round(entries / (years * 12), 2)}


def passes(row: dict) -> bool:
    return (row["excess"] > 0 and row["drawdown"] >= row["bench_drawdown"] - 0.05
            and (row["win_1y"] or 0) >= 0.5)


def evaluate(arrays: Arrays, candidate: dict, costs, record: bool = False):
    from quant_platform.research.costs import DEFAULT_SLIPPAGE_BPS

    slippage = getattr(costs, "slippage_bps", DEFAULT_SLIPPAGE_BPS) / 10_000
    fee = costs.fee_rate * costs.fee_discount
    tax = dict(costs.tax_rates).get("stock", 0.003)
    checks = arrays.weekly_days if candidate["check"] == "weekly" else np.ones(len(arrays.days), dtype=bool)
    calm = arrays.calm if candidate["weighting"] == "inverse_vol" else None
    outcome = account(score_of(arrays, candidate), checks, arrays.industry, arrays.next_returns, calm,
                      fee + slippage, fee + tax + slippage, record)
    years = (arrays.days[-1] - arrays.days[0]).days / 365.25
    return metrics(outcome[0], arrays.bench, years, outcome[1]), outcome


def _work(folder: str, chunk: list[dict], out: str, costs) -> int:
    arrays = load_arrays(Path(folder))
    with open(out, "a", encoding="utf-8") as handle:
        for candidate in chunk:
            row, _outcome = evaluate(arrays, candidate, costs)
            handle.write(json.dumps({**candidate, **row}, ensure_ascii=False) + "\n")
            handle.flush()
    return len(chunk)


def screen(arrays_folder: Path, items: list[dict], costs, workers: int, out_folder: Path, progress=None) -> list[dict]:
    """Stage 1 over ``items`` in ``workers`` processes (0 = here); every result is written as it is made."""
    out_folder.mkdir(parents=True, exist_ok=True)
    if workers <= 0:
        output = []
        arrays = load_arrays(arrays_folder)
        for index, candidate in enumerate(items):
            row, _outcome = evaluate(arrays, candidate, costs)
            output.append({**candidate, **row})
            if progress and index % 200 == 0:
                progress(index)
        return output
    chunks = [items[index::workers] for index in range(workers)]
    paths = [out_folder / f"screen-{index}.jsonl" for index in range(workers)]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_work, str(arrays_folder), chunk, str(path), costs) for chunk, path in zip(chunks, paths)]
        while True:
            done, pending = wait(futures, timeout=20)
            if progress:
                progress(sum(_count_lines(path) for path in paths))
            if not pending:
                break
        for future in futures:
            future.result()
    rows = []
    for path in paths:
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    return rows


def _count_lines(path: Path) -> int:
    try:
        with open(path, "rb") as handle:
            return sum(1 for _line in handle)
    except OSError:
        return 0


def finalists(arrays: Arrays, rows: list[dict], costs, count: int, pool: int = 600,
              seeds: list[dict] | None = None) -> list[dict]:
    """The passing candidates, best development-period excess first, skipping any whose daily returns
    correlate above CORRELATION_LIMIT with one already taken — or with a ``seeds`` candidate (an earlier
    scan's finalists: a new scan adds strategies, not near-copies of ones already run)."""
    ranked = sorted((row for row in rows if passes(row)), key=lambda row: -row["excess"])[:pool]
    earlier = []
    for seed in seeds or []:
        _metrics, (returns, _entries) = evaluate(arrays, seed, costs)
        if np.std(returns) > 0:
            earlier.append((seed, returns))
    taken: list[tuple[dict, np.ndarray]] = []
    for row in ranked:
        if len(taken) >= count:
            break
        candidate = {key: row[key] for key in ("factors", "universe", "weighting", "check")}
        _metrics, (returns, _entries) = evaluate(arrays, candidate, costs)
        if np.std(returns) == 0:
            continue
        if any(np.corrcoef(returns, other)[0, 1] > CORRELATION_LIMIT for _row, other in earlier + taken):
            continue
        taken.append((row, returns))
    return [row for row, _returns in taken]


def earlier_finalists(research: Path) -> list[dict]:
    """Every registered scan rule (named 掃描：…) as a candidate, to keep new finalists different."""
    output, seen = [], set()
    path = research / "trials.jsonl"
    if not path.is_file():
        return output
    for line in path.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        if not str(record.get("spec_name", "")).startswith("掃描："):
            continue
        try:
            spec = json.loads((research / "reports" / record["report_file"]).read_text(encoding="utf-8"))["spec"]
        except (OSError, ValueError, KeyError):
            continue
        key = record["spec_hash"]
        if key in seen:
            continue
        seen.add(key)
        output.append({"factors": list(spec["factors"]), "universe": "large" if spec.get("large_caps") else "all",
                       "weighting": spec.get("weighting", "equal"), "check": spec.get("check", "daily")})
    return output


def run_scan(base: Path, research: Path, costs, workers: int = 6, count: int = 80, largest: int = 3,
             names: list[str] | None = None, job=None, smallest: int = 1) -> dict:
    """Both stages; returns the summary written next to the screen results."""
    from quant_platform.research import daily
    from quant_platform.research.registry import TrialRegistry

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = research / "scans" / f"{SCAN_VERSION}-{stamp}"
    if job:
        job.update(current="載入行情與因子", force=True)
    data, fp = daily.load(base)
    fingerprint = daily.fingerprint(base)
    arrays = prepare(data, fp, costs, folder / "arrays")
    items = candidates(names, largest, smallest)
    if job:
        job.update(done=0, total=len(items) + count, current=f"第一階段：{len(items):,} 個候選（2015-06～2020-09）",
                   force=True)
    started = time.time()
    rows = screen(folder / "arrays", items, costs, workers, folder,
                  progress=(lambda done: job.update(done=done, current=f"第一階段：{done:,}／{len(items):,}"))
                  if job else None)
    pd.DataFrame([{**row, "factors": "+".join(row["factors"])} for row in rows]).to_parquet(folder / "screen.parquet")
    seeds = earlier_finalists(research)
    chosen = finalists(arrays, rows, costs, count, seeds=seeds)
    registry = TrialRegistry(research / "trials.jsonl")
    cache: dict = {}
    results = []
    for index, row in enumerate(chosen):
        rule = to_rule(row)
        if job:
            job.update(done=len(items) + index, current=f"第二階段：{rule.name}", force=True)
        record, report = daily.run_trial(rule, base, registry, research / "reports", costs, data, fp, fingerprint, cache,
                                         stamp)
        reasons = record.metrics.get("reasons") or []
        results.append({"trial_id": record.trial_id, "name": rule.name, "screen_excess": row["excess"],
                        "screen_drawdown": row["drawdown"], "full_excess": report["full_period_excess"],
                        "since_2020": report["since_2020"]["excess"], "drawdown": report["strategy"]["max_drawdown"],
                        "reasons": reasons, "tier": daily.tier(record.metrics)[0]})
    summary = {"version": SCAN_VERSION, "stamp": stamp, "development": [str(arrays.days[0]), str(DEV_END)],
               "signals": arrays.names, "sizes": [smallest, largest], "earlier_finalists": len(seeds),
               "candidates": len(items), "passed_screen": sum(passes(row) for row in rows),
               "finalists": len(chosen), "minutes": round((time.time() - started) / 60, 1), "results": results,
               "tiers": dict(Counter(item["tier"] for item in results))}
    (folder / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary
