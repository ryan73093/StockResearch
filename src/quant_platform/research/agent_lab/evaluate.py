"""The cohort's board. Every row's account is the platform's own engine (D-017: ``daily_rankings`` and
``simulate_daily`` — the next close's fill, the fees, the tax, the minimum order) on the owner's cash flow (D-002), all
from the same first session: the one after the first day every model variant decided live. Each row is given both
kinds of money (D-020, D-045: what is counted — gains once sold, every loss — and both sides sold that day), its
difference from the row it is compared with (the daily differences resampled in blocks, since sixty sessions of
correlated accounts say little), how it behaved, and where its picks stand among random agents.

Before SESSIONS_TO_JUDGE live sessions a row has numbers and no verdict. A replay store can be put through the same
board (``run``): it is labelled and never judged."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from quant_platform.research.agent_lab import VERSION, runner, variants
from quant_platform.research.agent_lab.environment import placebo

TAIPEI = ZoneInfo("Asia/Taipei")
SESSIONS_TO_JUDGE = 60
BLOCK, RESAMPLES = 5, 2000


def scores(fp, made: list[dict]) -> np.ndarray:
    """symbols × sessions: each decision's stocks ranked on the session after it, carried until the next decision's
    (as research/llm_agent.py ``scores``, in the panel's own layout)."""
    output = np.full((len(fp.symbols), len(fp.sessions)), np.nan, dtype=np.float32)
    by_day = {item["date"]: list((item.get("llm") or {}).get("hold") or []) for item in made}
    current: list[str] | None = None
    for position in range(1, len(fp.sessions)):
        day = fp.sessions[position - 1].isoformat()
        if day in by_day:
            current = by_day[day]
        if current is None:
            continue
        for place, code in enumerate(current):
            row = fp.row.get(f"{code}.TW")
            if row is not None:
                output[row, position] = len(current) - place
    return np.ascontiguousarray(output)


def block_interval(differences: np.ndarray, block: int = BLOCK, resamples: int = RESAMPLES, seed: int = 11) -> tuple[float, float] | None:
    """The 5%～95% range of the summed daily differences when blocks of consecutive days are drawn again."""
    count = len(differences)
    if count < 2 * block:
        return None
    rng = np.random.default_rng(seed)
    starts = np.arange(count - block + 1)
    totals = []
    for _ in range(resamples):
        chosen = rng.choice(starts, size=int(np.ceil(count / block)))
        sample = np.concatenate([differences[start: start + block] for start in chosen])[:count]
        totals.append(float(sample.sum()))
    return float(np.percentile(totals, 5)), float(np.percentile(totals, 95))


def behaviour(made: list[dict]) -> dict[str, object]:
    holds = [list((item.get("llm") or {}).get("hold") or []) for item in made]
    bought = [len(set(after) - set(before)) for before, after in zip(holds[:-1], holds[1:])]
    steps = [len((item.get("llm") or {}).get("trace") or []) for item in made if (item.get("llm") or {}).get("trace") is not None]
    overlaps = []                # the same design run several times a day (``vote``): how alike the runs' lists are
    for item in made:
        lists = [set(run.get("hold") or []) for run in (item.get("llm") or {}).get("runs") or [] if not run.get("fallback")]
        pairs = [(one, other) for index, one in enumerate(lists) for other in lists[index + 1:] if one | other]
        overlaps += [len(one & other) / len(one | other) for one, other in pairs]
    extra = {"run_overlap": round(float(np.mean(overlaps)), 2)} if overlaps else {}
    return {**extra, "decisions": len(made), "fallbacks": sum(1 for item in made if (item.get("llm") or {}).get("fallback")),
            "average_held": round(float(np.mean([len(hold) for hold in holds])), 1) if holds else 0.0,
            "bought_per_decision": round(float(np.mean(bought)), 2) if bought else 0.0,
            "average_steps": round(float(np.mean(steps)), 1) if steps else 0.0,
            "cost_usd": round(sum(float((item.get("llm") or {}).get("cost_usd") or 0) for item in made), 4)}


def first_common_day(research: Path, run: str | None) -> str | None:
    """The first day every variant has a decision of its own (in a replay: every variant the replay covers). A day
    kept from the day before — no answer, no credit at the model's — is not one: an account that never chose holds
    nothing, and comparing it would be comparing cash (2026-11-20: the model account ran out of credit)."""
    firsts = []
    for variant in variants.COHORT:
        made = [item for item in runner.records(research, variant.key, run) if not (item.get("llm") or {}).get("fallback")]
        if not made:
            if run and not runner.records(research, variant.key, run):      # a replay may cover some of the designs only
                continue
            return None
        firsts.append(min(item["date"] for item in made))
    return max(firsts) if firsts else None


def judge_day(research: Path, first_decision: str, sessions: int = SESSIONS_TO_JUDGE) -> str | None:
    """The day the accounts reach ``sessions`` sessions: counted on the exchange's calendar from the session after the
    first decision (D-008: dates are counted, not estimated)."""
    from datetime import timedelta

    from quant_platform.market_calendar.store import MarketCalendarStore

    try:
        calendar = MarketCalendarStore(Path(research).parent).calendar("TW")
        day, count = date.fromisoformat(first_decision), 0
        while count < sessions:
            day += timedelta(days=1)
            if calendar.is_trading_day(day):
                count += 1
        return day.isoformat()
    except Exception:  # noqa: BLE001 - no calendar: the page says the rule instead of a date
        return None


def board(base: str | Path, research: str | Path, run: str | None = None, broker: str = "cathay", loaded=None,
          now: datetime | None = None) -> dict[str, object]:
    from quant_platform.research import daily
    from quant_platform.research.costs import broker_costs
    from quant_platform.research.metrics import max_drawdown, unit_values
    from quant_platform.research.stock_rules import SeedPlan

    base, research = Path(base), Path(research)
    output: dict[str, object] = {"version": VERSION, "run": run, "replayed": bool(run),
                                 "made_at": (now or datetime.now(TAIPEI)).isoformat(timespec="seconds"),
                                 "sessions_to_judge": SESSIONS_TO_JUDGE, "rows": []}
    first_day = first_common_day(research, run)
    if first_day is None:
        output["note"] = "還有設計沒有做過任何決定，沒有可以比的帳戶。"
        return output
    variants.register()
    data, fp = loaded or daily.load(base)
    later = [day for day in fp.sessions if day > date.fromisoformat(first_day)]
    output["first_decision"] = first_day
    if len(later) < 2:
        output["note"] = f"第一次決定是 {first_day}，隔天收盤才成交；至少要再一個交易日才有可以比的數字。"
        return output
    start, last = later[0], later[-1]
    costs = broker_costs(broker)
    plan = SeedPlan(calendar=list(data.sessions))
    bench = daily.simulate_daily(data, None, costs, start, last, plan=plan)
    bench_units = np.array(unit_values(bench.values, bench.flows))
    bench_moves = bench_units[1:] / bench_units[:-1] - 1
    output.update(start=start.isoformat(), through=last.isoformat(), sessions=len(later),
                  benchmark={"value": round(bench.values[-1]), "sold": round(bench.sold_value or 0), "contributed": round(bench.contributed)})
    entries: list[tuple[str, str, str, list[dict] | None]] = [
        (variant.key, variant.label, variant.factor, runner.records(research, variant.key, run)) for variant in variants.COHORT]
    entries = [entry for entry in entries if entry[3]]
    if not run:
        entries += [(name, label, name, None) for name, label in variants.OUTSIDE]
    units_of: dict[str, np.ndarray] = {}
    rows: dict[str, dict] = {}
    for key, label, factor, made in entries:
        if made is not None:
            made = [item for item in made if item["date"] >= first_day]
            fp._cache[factor] = scores(fp, made)
        row: dict[str, object] = {"key": key, "label": label}
        try:
            rule = daily.DailyRule(name=f"AI 代理人實驗室：{label}"[:80], factors={factor: 1.0}, top=20, keep=1, min_hold=0,
                                   industry_cap=0.3)
            ledger: list = []
            ranks = daily.daily_rankings(fp, rule, start, last)
            result = daily.simulate_daily(data, rule, costs, start, last, ranks, plan, ledger=ledger)
        except Exception as error:  # noqa: BLE001 - one row that cannot be computed is shown as such
            row["error"] = str(error)[:160]
            rows[key] = row
            continue
        units = np.array(unit_values(result.values, result.flows))
        units_of[key] = units
        moves = units[1:] / units[:-1] - 1
        drawdown, _peak, _trough = max_drawdown(list(units))
        bench_drawdown, _p, _t = max_drawdown(list(bench_units))
        beta = None
        if len(moves) >= 10 and float(np.var(bench_moves)) > 0:
            beta = round(float(np.cov(moves, bench_moves)[0, 1] / np.var(bench_moves)), 2)
        row.update(value=round(result.values[-1]), counted=round(result.counted_value or 0), sold=round(result.sold_value or 0),
                   contributed=round(result.contributed), fees_and_tax=int(result.fees + result.taxes),
                   orders=len(ledger), max_drawdown=round(float(drawdown), 4), benchmark_drawdown=round(float(bench_drawdown), 4),
                   beta=beta, counted_ahead_of_0050=round((result.counted_value or 0) - (bench.sold_value or 0)),
                   sold_ahead_of_0050=round((result.sold_value or 0) - (bench.sold_value or 0)))
        if made is not None:
            row["behaviour"] = behaviour(made)
            if key != "twin":
                row["placebo"] = placebo(fp, made, fp.index[last])
        rows[key] = row
    for variant in variants.COHORT:
        row, other = rows.get(variant.key), rows.get(variant.against)
        if not row or "error" in row:
            continue
        row["question"], row["against"] = variant.question, variant.against
        if variant.against == "0050" or not other or "error" in other:
            continue
        mine, theirs = units_of[variant.key], units_of[variant.against]
        differences = (mine[1:] / mine[:-1]) - (theirs[1:] / theirs[:-1])
        row["versus"] = {"counted": row["counted"] - other["counted"], "sold": row["sold"] - other["sold"],
                         "daily_difference_sum": round(float(differences.sum()), 4),
                         "interval": block_interval(differences)}
    live = len(later) if not run else 0
    for row in rows.values():
        if "error" in row:
            continue
        if run:
            row["verdict"] = "補做，不判定"
        elif live < SESSIONS_TO_JUDGE:
            row["verdict"] = f"觀察中（{live}／{SESSIONS_TO_JUDGE} 個交易日）"
        else:
            both = row["counted_ahead_of_0050"] >= 0 and row["sold_ahead_of_0050"] >= 0
            versus = row.get("versus")
            ahead = versus is None or (versus["counted"] >= 0 and versus["sold"] >= 0)
            row["verdict"] = ("好：兩種錢都不輸比較對象，也不輸 0050" if both and ahead else
                              "有用但不夠：贏比較對象、輸 0050" if ahead else "沒用：輸比較對象")
    output["rows"] = list(rows.values())
    return output


def write(research: str | Path, result: dict[str, object]) -> Path:
    folder = runner.root(research) / ("replay" if result.get("run") else "")
    path = (folder / str(result["run"]) / "board.json") if result.get("run") else runner.root(research) / "board.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def text(result: dict[str, object]) -> str:
    lines = []
    if result.get("note"):
        return str(result["note"])
    bench = result["benchmark"]
    lines.append(f"{'補做（不是證據）' if result.get('replayed') else '前向'}：{result['start']}～{result['through']}（{result['sessions']} 個交易日），"
                 f"投入 {bench['contributed']:,} 元；0050 今天賣掉 {bench['sold']:,} 元")
    for row in result["rows"]:
        if "error" in row:
            lines.append(f"  {row['label']}：算不出來（{row['error']}）")
            continue
        part = (f"  {row['label']}：拿到的錢 {row['counted']:,}、都賣掉 {row['sold']:,}（比 0050 {row['counted_ahead_of_0050']:+,}／"
                f"{row['sold_ahead_of_0050']:+,}），費稅 {row['fees_and_tax']:,}、{row['orders']} 筆")
        if row.get("beta") is not None:
            part += f"，連動 {row['beta']}"
        if row.get("versus"):
            part += f"；對 {row['against']}：{row['versus']['counted']:+,}／{row['versus']['sold']:+,}"
        if row.get("placebo"):
            part += f"；選股排在隨機代理人的第 {100 * row['placebo']['percentile']:.0f} 百分位"
        lines.append(part + f"｜{row['verdict']}")
    return "\n".join(lines)
