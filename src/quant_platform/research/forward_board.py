"""2026-10-13 (使用者：前向策略的起跑時間基準？能否監控前向是否相較其他策略轉強、給調整建議): the
common-start board.

Each tracked strategy starts forward observation the day it qualified, with its own NT$300,000, so a rule
added on 10-04 and one added on 10-12 cannot be compared. The board replays every active strategy from one
common start — the first session after the research period ended (2026-10-01: no data from then on chose any
rule) — on the owner's account, against the same money in 0050, and holds each against what its own backtest
expects: its mean monthly excess × the months elapsed, with a 90% band of ±1.645 standard deviations × √months.
States: 強於預期 above the band, 符合預期 inside, 弱於預期 below, 警示 under the 1% line (−2.326 sd). The rank
and its change over five sessions show who is getting stronger. Written every trading day after the forward
record (``board.json``; a line per day in ``board_history.jsonl``). A short stretch says little — the band is
wide for weeks; the board is for spotting a strategy that is clearly off its course, not for picking winners
from a few days.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import numpy as np

COMMON_START = date(2026, 10, 1)
BAND, ALERT = 1.645, 2.326
SESSIONS_PER_MONTH = 21


def expectation(research: Path, rule_hash: str) -> dict[str, float] | None:
    """The backtest's mean and standard deviation of the monthly excess over 0050 (its report)."""
    from quant_platform.research.registry import TrialRegistry

    latest = None
    for record in TrialRegistry(research / "trials.jsonl").records():
        if record.spec_hash == rule_hash and record.kind == "candidate":
            latest = record
    if latest is None:
        return None
    try:
        report = json.loads((research / "reports" / latest.report_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    months = [float(value) for value in (report.get("monthly_active_returns") or {}).values()]
    if len(months) < 12:
        return None
    return {"trial": latest.trial_id, "mean": float(np.mean(months)), "sd": float(np.std(months, ddof=1))}


def state(excess: float, months: float, expected: dict | None) -> tuple[str, float | None, float | None, float | None]:
    """(state, expected excess, band low, band high) for a cumulative forward excess over ``months``."""
    if expected is None or months <= 0:
        return "沒有回測可比", None, None, None
    centre = expected["mean"] * months
    width = expected["sd"] * np.sqrt(months)
    low, high = centre - BAND * width, centre + BAND * width
    if excess < centre - ALERT * width:
        label = "警示"
    elif excess < low:
        label = "弱於預期"
    elif excess > high:
        label = "強於預期"
    else:
        label = "符合預期"
    return label, round(centre, 4), round(low, 4), round(high, 4)


ADVICE = {
    "警示": "表現落在回測幾乎不會出現的範圍：先查持股是否擠在同一族群、資料或成交是否異常；一個月後仍在警示線下就停止追蹤、回頭檢討規則。",
    "弱於預期": "低於回測的一般範圍，但還在可能的波動內：照規則繼續，不要因短期弱勢改參數；看是否和其他策略同時轉弱（市場因素）。",
    "符合預期": "照規則繼續。",
    "強於預期": "高於回測的一般範圍：不要因短期強勢加碼或改規則；確認不是單一兩檔股票撐起來的。",
    "沒有回測可比": "這個策略沒有可比的回測月超額，只看它和 0050 的差距。",
}


def build(research: Path, history: Path, today: date, costs=None) -> dict[str, object] | None:
    from quant_platform.research.blend import BlendAccount, BlendRule
    from quant_platform.research.costs import broker_costs
    from quant_platform.research.daily import (
        RECENT_START,
        ChipStore,
        DailyRule,
        FactorPanel,
        Industries,
        account_parking,
        daily_rankings,
        daily_weights,
        exposure_schedule,
        load_industries,
        market_closes,
        simulate_daily,
    )
    from quant_platform.research.legacy_challenger import BENCHMARK
    from quant_platform.research.metrics import max_drawdown, unit_values
    from quant_platform.research.model import models_root
    from quant_platform.research.stock_forward import StockForwardTracker
    from quant_platform.research.stock_rules import WARMUP_YEARS, Panel, SeedPlan, load_stock_data

    costs = costs or broker_costs("cathay")
    items = [item for item in StockForwardTracker(research).tracked()
             if not item.get("stopped") and item.get("kind") in ("daily", "blend")
             and (item.get("rule") or {}).get("universe", "twse") == "twse"]
    if not items or today < COMMON_START:
        return None
    data = load_stock_data(history, RECENT_START.year - WARMUP_YEARS, today.year)
    days = [day for day in data.sessions if COMMON_START <= day <= today and day in data.closes.get(BENCHMARK, {})]
    if not days:
        return None
    last = days[-1]
    panel = Panel(data)
    fp = FactorPanel(panel, Industries(load_industries(history)), ChipStore(history, panel.sessions, panel.symbols, panel.close),
                     market_closes(history, panel.sessions), models_root(history))
    plan = SeedPlan()
    bench = simulate_daily(data, None, costs, COMMON_START, last, plan=plan)
    bench_units = np.array(unit_values(bench.values, bench.flows))
    months = len(days) / SESSIONS_PER_MONTH
    rows = []
    for item in items:
        ledger: list = []
        try:
            if item.get("kind") == "blend":
                rule = BlendRule.model_validate(item["rule"])
                run = BlendAccount(data, fp, rule, costs, COMMON_START, last).run(COMMON_START, last, plan, ledger=ledger)
            else:
                rule = DailyRule.model_validate(item["rule"])
                ranks = daily_rankings(fp, rule, COMMON_START, last)
                run = simulate_daily(data, rule, costs, COMMON_START, last, ranks, plan, ledger=ledger,
                                     weights=daily_weights(fp, rule, ranks), parked=account_parking(data, fp, rule, costs),
                                     exposure=exposure_schedule(data, fp, rule, costs))
        except Exception as error:                    # one bad item does not stop the board
            rows.append({"rule_hash": item["rule_hash"], "name": item["name"], "error": str(error)[:200]})
            continue
        units = np.array(unit_values(run.values, run.flows))
        excess = float(units[-1] / bench_units[-1] - 1)
        expected = expectation(research, item["rule_hash"])
        label, centre, low, high = state(excess, months, expected)
        drawdown, _peak, _trough = max_drawdown(list(units))
        rows.append({
            "rule_hash": item["rule_hash"], "name": item["name"], "trial": (expected or {}).get("trial"),
            "since": item["since"], "value": round(run.values[-1], 2), "benchmark_value": round(bench.values[-1], 2),
            "contributed": round(run.contributed, 2), "return": round(float(units[-1] - 1), 4),
            "benchmark_return": round(float(bench_units[-1] - 1), 4), "excess": round(excess, 4),
            "max_drawdown": round(float(drawdown), 4), "state": label, "expected": centre, "band_low": low,
            "band_high": high, "advice": ADVICE[label],
            "buys": sum(1 for entry in ledger if entry["side"] == "BUY"),
            "sells": sum(1 for entry in ledger if entry["side"] == "SELL")})
    ranked = sorted((row for row in rows if "excess" in row), key=lambda row: -row["excess"])
    for place, row in enumerate(ranked, start=1):
        row["rank"] = place
    folder = research / "forward" / "stocks"
    history_path = folder / "board_history.jsonl"
    earlier = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()] \
        if history_path.is_file() else []
    earlier = [entry for entry in earlier if entry.get("date") != last.isoformat()]
    before = earlier[-5] if len(earlier) >= 5 else (earlier[0] if earlier else None)
    for row in ranked:
        previous = (before or {}).get("ranks", {}).get(row["rule_hash"])
        row["rank_change"] = (previous - row["rank"]) if previous else None      # positive: moved up
    board = {"built_at": datetime.now().isoformat(timespec="seconds"), "common_start": COMMON_START.isoformat(),
             "date": last.isoformat(), "sessions": len(days), "months": round(months, 2), "rows": rows,
             "counts": {label: sum(1 for row in rows if row.get("state") == label) for label in ADVICE}}
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "board.json").write_text(json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8")
    with history_path.open("w", encoding="utf-8") as handle:
        for entry in [*earlier, {"date": last.isoformat(), "ranks": {row["rule_hash"]: row["rank"] for row in ranked},
                                 "excess": {row["rule_hash"]: row["excess"] for row in ranked}}]:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return board


def load(research: Path) -> dict[str, object] | None:
    path = research / "forward" / "stocks" / "board.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except ValueError:
        return None
