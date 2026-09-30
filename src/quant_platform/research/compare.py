"""Strategy versus the DCA benchmark under the same cash flows (S3-W04).

Main metric (REQUIREMENTS §8): over rolling 3- and 5-year windows that start
every month, how often and by how much the strategy ends with more money than
the benchmark given identical contributions. Excess is expressed as a share
of the money contributed in the window.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import date

from quant_platform.research.cashflow import ContributionPlan
from quant_platform.research.costs import CostModel
from quant_platform.research.engine import SimulationResult, common_start, simulate
from quant_platform.research.market import MarketData
from quant_platform.research.spec import BASELINES, StrategySpec


def _add_months(day: date, months: int) -> date:
    total = day.year * 12 + day.month - 1 + months
    return date(total // 12, total % 12 + 1, 1)


def rolling_windows(
    spec: StrategySpec,
    benchmark: StrategySpec,
    market: MarketData,
    plan: ContributionPlan,
    costs: CostModel,
    start: date,
    end: date,
    months: int,
    dividend_lag_days: int = 25,
) -> dict[str, object]:
    windows = []
    window_start = date(start.year, start.month, 1)
    while True:
        window_end = _add_months(window_start, months)
        if window_end > end:
            break
        strategy = simulate(spec, market, plan, costs, max(window_start, start), window_end, dividend_lag_days)
        base = simulate(benchmark, market, plan, costs, max(window_start, start), window_end, dividend_lag_days)
        contributed = strategy.total_contributed or 1.0
        windows.append({
            "start": strategy.start.isoformat(),
            "end": strategy.end.isoformat(),
            "excess": (strategy.final_value - base.final_value) / contributed,
            "strategy_xirr": strategy.xirr,
            "benchmark_xirr": base.xirr,
            "strategy_drawdown": strategy.max_drawdown,
            "benchmark_drawdown": base.max_drawdown,
        })
        window_start = _add_months(window_start, 1)
    if not windows:
        return {"months": months, "count": 0}
    excesses = [item["excess"] for item in windows]
    worst = min(windows, key=lambda item: item["excess"])
    best = max(windows, key=lambda item: item["excess"])
    return {
        "months": months,
        "count": len(windows),
        "win_ratio": round(sum(1 for value in excesses if value > 0) / len(windows), 4),
        "median_excess": round(statistics.median(excesses), 6),
        "mean_excess": round(statistics.fmean(excesses), 6),
        "worst": {"start": worst["start"], "end": worst["end"], "excess": round(worst["excess"], 6)},
        "best": {"start": best["start"], "end": best["end"], "excess": round(best["excess"], 6)},
        "deeper_drawdown_than_benchmark": round(
            sum(1 for item in windows if item["strategy_drawdown"] < item["benchmark_drawdown"]) / len(windows), 4
        ),
    }


def compare_to_benchmark(
    spec: StrategySpec,
    market: MarketData,
    plan: ContributionPlan,
    costs: CostModel | None = None,
    benchmark: StrategySpec | None = None,
    start: date | None = None,
    end: date | None = None,
    window_months: tuple[int, ...] = (36, 60),
    dividend_lag_days: int = 25,
) -> dict[str, object]:
    costs = costs or CostModel()
    benchmark = benchmark or BASELINES["benchmark_dca"]
    first = max(common_start(spec, market), common_start(benchmark, market))
    start = max(start or first, first)
    strategy_run = simulate(spec, market, plan, costs, start, end, dividend_lag_days)
    benchmark_run = simulate(benchmark, market, plan, costs, strategy_run.start, strategy_run.end, dividend_lag_days)
    report = {
        "strategy": strategy_run.summary(),
        "benchmark": benchmark_run.summary(),
        "full_period_excess": round(
            (strategy_run.final_value - benchmark_run.final_value) / (strategy_run.total_contributed or 1.0), 6
        ),
        "windows": {
            f"{months // 12}y": rolling_windows(
                spec, benchmark, market, plan, costs, strategy_run.start, strategy_run.end,
                months, dividend_lag_days,
            )
            for months in window_months
        },
        "plan": plan.as_dict(),
        "costs": costs.as_dict(),
        "data_fingerprint": market.fingerprint,
        "dividend_lag_days": dividend_lag_days,
        "strategy_output_hash": strategy_run.output_hash,
        "benchmark_output_hash": benchmark_run.output_hash,
    }
    report["report_hash"] = hashlib.sha256(json.dumps(report, sort_keys=True).encode("utf-8")).hexdigest()
    return report


def summarize_run(result: SimulationResult) -> dict[str, object]:
    return {**result.summary(), "output_hash": result.output_hash}
