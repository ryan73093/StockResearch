"""Research CLI (S3).

    python -m quant_platform.research backtest --spec baseline:ma_value [--benchmark baseline:benchmark_dca]
        [--monthly 10000] [--day 5] [--start 2004-02-11] [--end 2026-09-30] [--windows 36,60] [--cost-scale 1]
    python -m quant_platform.research baselines [--monthly 10000] [--day 5]
    python -m quant_platform.research schema

Reports are written to instance/research/reports/ and never overwrite an
earlier file (the name carries the report hash).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_platform.research.cashflow import ContributionPlan
from quant_platform.research.compare import compare_to_benchmark
from quant_platform.research.costs import CostModel
from quant_platform.research.market import DEFAULT_BASE, load_market
from quant_platform.research.spec import BASELINES, json_schema, load_spec

TAIPEI = ZoneInfo("Asia/Taipei")
REPORTS = Path("instance") / "research" / "reports"


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _line(report: dict) -> str:
    strategy, benchmark = report["strategy"], report["benchmark"]
    windows = "；".join(
        f"{key} 視窗 {item['count']} 個、勝率 {item['win_ratio']:.0%}、中位超額 {item['median_excess']:+.2%}、"
        f"最差 {item['worst']['excess']:+.2%}（{item['worst']['start']} 起）"
        for key, item in report["windows"].items() if item.get("count")
    )
    return (
        f"{strategy['spec']}：{strategy['start']}～{strategy['end']} 投入 {strategy['total_contributed']:,.0f} 元，"
        f"期末 {strategy['final_value']:,.0f} 元（基準 {benchmark['final_value']:,.0f}），"
        f"XIRR {strategy['xirr']:.2%}（基準 {benchmark['xirr']:.2%}），"
        f"最大回撤 {strategy['max_drawdown']:.1%}（基準 {benchmark['max_drawdown']:.1%}），"
        f"交易 {strategy['trades']} 筆、費稅 {strategy['fees'] + strategy['taxes']:,} 元。{windows}"
    )


def _save(report: dict, prefix: str) -> Path:
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{prefix}-{report['report_hash'][:12]}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="研究回測（相同現金流對照定期定額）")
    parser.add_argument("command", choices=("backtest", "baselines", "schema"))
    parser.add_argument("--spec", default="baseline:ma_value")
    parser.add_argument("--benchmark", default="baseline:benchmark_dca")
    parser.add_argument("--monthly", type=float, default=10_000)
    parser.add_argument("--day", type=int, default=5)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--windows", default="36,60")
    parser.add_argument("--cost-scale", type=float, default=1.0)
    parser.add_argument("--dividend-lag", type=int, default=25)
    parser.add_argument("--base", default=str(DEFAULT_BASE))
    args = parser.parse_args()

    if args.command == "schema":
        print(json.dumps(json_schema(), ensure_ascii=False, indent=2))
        return 0

    plan = ContributionPlan(monthly_amount=args.monthly, day_of_month=args.day)
    costs = CostModel().scaled(args.cost_scale) if args.cost_scale != 1 else CostModel()
    windows = tuple(int(item) for item in args.windows.split(",") if item)
    benchmark = load_spec(args.benchmark)
    specs = (
        [spec for name, spec in BASELINES.items() if name != "benchmark_dca"]
        if args.command == "baselines" else [load_spec(args.spec)]
    )
    stamp = datetime.now(TAIPEI).strftime("%Y%m%d-%H%M%S")
    for spec in specs:
        assets = sorted(set(spec.assets) | {spec.signal} | set(benchmark.assets))
        market = load_market(assets, args.base)
        report = compare_to_benchmark(
            spec, market, plan, costs, benchmark, _date(args.start), _date(args.end), windows,
            args.dividend_lag,
        )
        report["generated_at"] = stamp
        path = _save(report, f"{stamp}-{spec.spec_hash[:8]}")
        print(_line(report), flush=True)
        print(f"  報告：{path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
