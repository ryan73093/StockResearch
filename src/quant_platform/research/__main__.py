"""Research CLI (S3, S4-W01/W02).

    python -m quant_platform.research baselines [--period full] [--monthly 10000] [--day 5]
    python -m quant_platform.research trial --spec path.json --period development|validation|holdout
    python -m quant_platform.research trials            # list and verify the registry
    python -m quant_platform.research schema            # StrategySpec JSON Schema

Every run is registered in instance/research/trials.jsonl (append-only hash
chain) and its report saved under instance/research/reports/.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_platform.research.batches import BATCHES
from quant_platform.research.cashflow import ContributionPlan
from quant_platform.research.costs import BROKERS, broker_costs
from quant_platform.research.market import DEFAULT_BASE, available_assets, load_market
from quant_platform.research.periods import PERIODS, ResearchGateError, period_basis, run_trial
from quant_platform.research.registry import TrialRegistry
from quant_platform.research.significance import save_stats, significance
from quant_platform.research.spec import BASELINES, json_schema, load_spec

TAIPEI = ZoneInfo("Asia/Taipei")
RESEARCH = Path("instance") / "research"


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


def _saved_plan() -> tuple[float, int] | None:
    """Latest plan version, read-only from the application database."""
    import sqlite3
    from contextlib import closing

    from quant_platform.config.settings import Settings

    url = Settings.from_env().database_url
    if not url.startswith("sqlite:///"):
        return None
    path = Path(url.removeprefix("sqlite:///")).resolve()
    if not path.is_file():
        return None
    with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as db:
        try:
            row = db.execute(
                "SELECT monthly_amount, salary_day FROM investment_plans ORDER BY version DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return (float(row[0]), int(row[1])) if row else None


def _agent(args) -> int:
    """One night of the AI researcher now (or only the prompt with --dry-run)."""
    from quant_platform.config.settings import Settings
    from quant_platform.research.agent.researcher import build_agent

    from quant_platform.research.agent.llm import LLMError

    now = datetime.now(TAIPEI)
    agent = build_agent(Settings.from_env(), RESEARCH)
    if args.check:
        try:
            result = agent.check()
        except LLMError as exc:
            print(f"連線檢查失敗：{exc}", file=sys.stderr)
            return 1
        usage = result["usage"]
        print(f"連線檢查成功：{result['payload']}；輸入 {usage.get('input_tokens')}、輸出 {usage.get('output_tokens')} tokens；"
              f"本月費用 US${result['budget']['spent_usd']:.6f}／上限 US${result['budget']['budget_usd']:.2f}")
        return 0
    if not args.dry_run and not (now.hour >= 19 or now.hour < 7):
        print("研究只在夜間（19:00–07:00）執行（需求 §8）；白天可用 --dry-run 或 --check", file=sys.stderr)
        return 3
    market = load_market(available_assets(args.base), args.base)
    if args.dry_run:
        instructions, user_input = agent.build_prompt(market)
        print(instructions)
        print(user_input)
        print(f"（提示約 {len(instructions) + len(user_input):,} 字）")
        return 0
    for entry in agent.run_night(market):
        print(f"{entry['round_id']}：{entry.get('status')}　{entry.get('hypothesis') or entry.get('error') or ''}", flush=True)
        for item in entry.get("accepted") or []:
            print(f"  #{item['trial_id']} {item['name']}：3 年勝率 {item.get('win_3y')}、中位 {item.get('median_3y')}")
        for item in entry.get("rejected") or []:
            print(f"  拒絕 {item.get('name')}：{item.get('reason')}")
        if entry.get("budget"):
            print(f"  本月費用 US${entry['budget']['spent_usd']:.4f}／上限 US${entry['budget']['budget_usd']:.2f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="研究回測（相同現金流對照定期定額）")
    parser.add_argument(
        "command", choices=("baselines", "trial", "batch", "trials", "stats", "schema", "agent", "promote"),
    )
    parser.add_argument("--dry-run", action="store_true", help="agent：只印出提示內容，不呼叫模型")
    parser.add_argument("--check", action="store_true", help="agent：極小的連線檢查呼叫（金鑰、模型、JSON 格式）")
    parser.add_argument("--name", default="first", help="batch：批次名稱")
    parser.add_argument("--spec")
    parser.add_argument("--period", default="full", choices=tuple(PERIODS))
    parser.add_argument("--monthly", type=float, default=10_000)
    parser.add_argument("--day", type=int, default=5)
    parser.add_argument("--windows", default="36,60")
    parser.add_argument("--cost-scale", type=float, default=1.0)
    parser.add_argument("--broker", default="conservative", choices=tuple(BROKERS), help="券商手續費設定")
    parser.add_argument("--dividend-lag", type=int, default=25)
    parser.add_argument("--execution-lag", type=int, default=0, help="穩健性：晚幾個交易日成交")
    parser.add_argument("--base", default=str(DEFAULT_BASE))
    parser.add_argument("--use-plan", action="store_true", help="用網站上最新版投資計畫的每月金額與薪資日")
    args = parser.parse_args()
    if args.use_plan:
        saved = _saved_plan()
        if saved is None:
            raise SystemExit("還沒有投資計畫；先在網站「計畫」頁建立")
        args.monthly, args.day = saved
        print(f"使用投資計畫：每月 {args.monthly:,.0f} 元、每月 {args.day} 日", flush=True)
    registry = TrialRegistry(RESEARCH / "trials.jsonl")

    if args.command == "schema":
        print(json.dumps(json_schema(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "trials":
        for record in registry.records():
            metrics = record.metrics
            print(
                f"#{record.trial_id:<4d} {record.created_at} {record.kind:9s} {record.period:11s} "
                f"{record.spec_name}：超額 {metrics.get('full_period_excess')}、XIRR {metrics.get('xirr')}",
            )
        problems = registry.verify()
        print("雜湊鏈完整" if not problems else "\n".join(problems))
        return 0 if not problems else 1

    if args.command == "agent":
        return _agent(args)
    if args.command == "promote":
        from quant_platform.research.forward import STANDARD_PLAN
        from quant_platform.research.promotion import PromotionPipeline

        pipeline = PromotionPipeline(RESEARCH, STANDARD_PLAN)
        events = pipeline.advance(load_market(available_assets(args.base), args.base))
        for event in events:
            print(f"{event.name}：{event.stage} {event.outcome} {event.evidence.get('reasons') or ''}", flush=True)
        overview = pipeline.overview()
        print(f"開發期 {overview['candidates']} 個設定，{overview['window_ok']} 個過視窗門檻，"
              f"{overview['eligible']} 個全部關卡通過；新事件 {len(events)} 筆")
        return 0

    if args.command == "stats":
        basis = period_basis(load_market(available_assets(args.base), args.base), args.period)
        report = significance(registry, RESEARCH / "reports", args.period, fingerprint=basis)
        if not report["candidates"]:
            print(f"{args.period} 在目前資料版本 {basis[:12]} 沒有候選試驗（舊版 {report['older_trials']} 筆）")
            return 1
        path = save_stats(report, RESEARCH / "stats", args.period, datetime.now(TAIPEI))
        for item in report["candidates"]:
            print(
                f"#{item['trial_id']} {item['name']}：月超額平均 {item['bootstrap']['mean']:+.3%}"
                f"（95% 區間 {item['bootstrap']['low']:+.3%}～{item['bootstrap']['high']:+.3%}），"
                f"DSR {item['dsr']['deflated_sharpe']:.2f}（試驗數 {item['dsr']['trials']}）"
            )
        if report.get("pbo"):
            print(f"PBO {report['pbo']['pbo']:.2f}（{report['pbo']['combinations']} 種切分）")
        print(
            f"資料版本 {basis[:12]}：目前 {report['current_trials']} 筆；舊版 {report['older_trials']} 筆不列入，"
            f"但計入多重檢定試驗數 {report['trials']}"
        )
        print(f"報告：{path}")
        return 0

    plan = ContributionPlan(monthly_amount=args.monthly, day_of_month=args.day)
    costs = broker_costs(args.broker)
    if args.cost_scale != 1:
        costs = costs.scaled(args.cost_scale)
    fee_terms = "原價" if costs.fee_discount >= 1 else f"{costs.fee_discount * 10:g} 折"
    print(f"成本：{BROKERS[args.broker].name}（手續費{fee_terms}、最低 {costs.minimum_fee} 元、滑價 {costs.slippage_bps:g} bps）",
          flush=True)
    windows = tuple(int(item) for item in args.windows.split(",") if item)
    benchmark = BASELINES["benchmark_dca"]
    if args.command == "baselines":
        runs = [("baseline", spec) for name, spec in BASELINES.items() if name != "benchmark_dca"]
    elif args.command == "batch":
        if args.name not in BATCHES:
            raise SystemExit(f"未知批次：{args.name}；可用：{', '.join(BATCHES)}")
        runs = [("candidate", spec) for spec in BATCHES[args.name]()]
    else:
        if not args.spec:
            raise SystemExit("trial 需要 --spec")
        runs = [("candidate", load_spec(args.spec))]
    stamp = datetime.now(TAIPEI).strftime("%Y%m%d-%H%M%S")
    # One market with every catalog series: all trials of a period then record
    # the same data fingerprint (periods.period_basis).
    market = load_market(available_assets(args.base), args.base)
    for kind, spec in runs:
        missing = sorted((set(spec.assets) | {spec.signal} | set(benchmark.assets)) - set(market.closes))
        if missing:
            print(f"{spec.name}：缺少資料 {', '.join(missing)}", file=sys.stderr)
            return 3
        try:
            outcome = run_trial(
                kind=kind, spec=spec, period=args.period, market=market, plan=plan,
                registry=registry, reports_dir=RESEARCH / "reports", costs=costs, benchmark=benchmark,
                window_months=windows, dividend_lag_days=args.dividend_lag,
                execution_lag=args.execution_lag, generated_at=stamp,
            )
        except ResearchGateError as exc:
            print(f"{spec.name}：研究規則不允許 — {exc}", file=sys.stderr)
            return 3
        print(_line(outcome.report), flush=True)
        print(
            f"  試驗 #{outcome.record.trial_id}{'（沿用既有紀錄）' if outcome.reused else ''}；報告：{outcome.report_path}",
            flush=True,
        )
    print(f"已登錄試驗 {registry.count()} 筆（候選 {registry.count('candidate')} 筆）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
