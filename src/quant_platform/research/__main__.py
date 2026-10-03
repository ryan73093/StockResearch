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
from datetime import date, datetime
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


def _saved_tolerance() -> tuple[float, int] | None:
    """(acceptable drawdown, version) of the latest plan, read-only; None when there is no plan."""
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
                "SELECT max_drawdown_tolerance, version FROM investment_plans ORDER BY version DESC LIMIT 1"
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return (float(row[0]), int(row[1])) if row else None


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


def _legacy(args) -> int:
    """The legacy ML challenger evaluation (read-only on the application database)."""
    from quant_platform.config.settings import Settings
    from quant_platform.research.legacy_challenger import evaluate, load_legacy_data, save_report

    url = Settings.from_env().database_url
    if not url.startswith("sqlite:///"):
        raise SystemExit("只支援 SQLite 資料庫")
    data = load_legacy_data(Path(url.removeprefix("sqlite:///")), Path(args.base) / "raw", args.experiment)
    print(f"實驗 #{args.experiment}（{data.notes['model']}／{data.notes['label']}）：{len(data.predictions)} 個交易日的預測、"
          f"{data.notes['symbols']} 檔、官方除權息事件 {data.notes['official_events']} 筆；"
          f"晚於當天 14:30 才可得而略過 {data.notes['late_predictions']} 筆", flush=True)
    report = evaluate(data)
    path = save_report(report, RESEARCH / "legacy")
    quality = report["signal_quality"]
    print(f"訊號：{quality.get('days')} 天、平均排序相關 {quality.get('mean_rank_ic')}（t={quality.get('rank_ic_t')}）、"
          f"前 5 名比平均多 {quality.get('top5_minus_average_5d')}（5 日、未扣成本）")
    sensitivity = report["sensitivity"]
    rows = report["results"] + [{**row, "broker": "（敏感度）"} for row in sensitivity["results"]]
    print(f"敏感度：{sensitivity['name']}（{sensitivity['symbols']} 檔，{sensitivity['broker']}）")
    for row in rows:
        windows = row.get("windows_3y") or {}
        print(f"{row['broker']:12s} {row['name']}：XIRR {row['xirr']}、期末 {row['final_value']:,.0f}／投入 "
              f"{row['contributed']:,.0f}、最大回撤 {row['max_drawdown']}、交易 {row['trades']} 筆、費稅 "
              f"{row['fees'] + row['taxes']:,}"
              + (f"；全期超額 {row['full_excess']:+.2%}、3 年視窗 {windows.get('count')} 個、勝率 "
                 f"{windows.get('win_ratio')}、中位 {windows.get('median_excess')}、最差 {windows.get('worst_excess')}"
                 f"；隨機選股平均 {row['random_control']['full_excess_mean']:+.2%}"
                 f"（{row['random_control']['full_excess_min']:+.2%}～{row['random_control']['full_excess_max']:+.2%}，"
                 f"平均交易 {row['random_control']['trades_mean']:,.0f} 筆、費稅 {row['random_control']['costs_mean']:,.0f}）、"
                 f"選最差 {row['worst_control']['full_excess']:+.2%}、模型貢獻 {row['model_contribution']:+.2%}"
                 if "full_excess" in row else ""))
    print(report["verdict"])
    print(f"報告：{path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="研究回測（相同現金流對照定期定額）")
    parser.add_argument(
        "command", choices=("baselines", "trial", "batch", "trials", "stats", "schema", "agent", "promote", "legacy", "stocks",
                            "forward"),
    )
    parser.add_argument("--date", help="forward：記錄哪一天（預設今天；補記的會標示為補記）")
    parser.add_argument("--passed", action="store_true", help="stocks：只跑開發期已通過視窗與回撤門檻的規則")
    parser.add_argument("--family", default="etf", choices=("etf", "stocks"), help="stats：ETF 規則或個股規則")
    parser.add_argument("--experiment", type=int, default=241, help="legacy：舊版模型實驗編號")
    parser.add_argument("--lump-sum", type=float, default=0, help="stocks：一次投入的金額（0＝每月投入）")
    parser.add_argument("--screen", action="store_true", help="stocks：只算全期間、不算滾動視窗（大掃描第一階段）")
    parser.add_argument("--top", type=int, default=0, help="stocks：只跑登錄檔中全期超額最高的前 N 個規則（第二階段）")
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
    if args.command == "legacy":
        return _legacy(args)
    if args.command == "stocks":
        from quant_platform.research.stock_rules import BATCHES as STOCK_BATCHES
        from quant_platform.research.stock_rules import (
            STANDARD_PLAN, WARMUP_YEARS, LumpSumPlan, Panel, describe, load_stock_data, run_stock_trial,
        )

        if args.period not in ("development", "validation", "holdout"):
            raise SystemExit("stocks：--period 只能是 development、validation 或 holdout")
        start, end = PERIODS[args.period]
        base = Path(args.base)
        costs = broker_costs(args.broker)
        data = load_stock_data(base, start.year - WARMUP_YEARS, end.year)
        print(f"個股規則（{args.period}）：{data.notes['symbols']} 檔上市股票、{len(data.sessions)} 個交易日；"
              f"成本：{BROKERS[args.broker].name}", flush=True)
        panel = Panel(data)
        stamp = datetime.now(TAIPEI).strftime("%Y%m%d-%H%M%S")
        stock_plan = LumpSumPlan(args.lump_sum) if args.lump_sum else STANDARD_PLAN
        print("現金流：" + (f"一次投入 {args.lump_sum:,.0f} 元" if args.lump_sum else "每月 5 日投入 10,000 元"), flush=True)
        if args.name not in STOCK_BATCHES:
            raise SystemExit(f"未知批次：{args.name}；可用：{', '.join(STOCK_BATCHES)}")
        rules = STOCK_BATCHES[args.name]()
        if args.passed:
            from quant_platform.research.pool import _best_records, _gate

            stock_records = [record for record in registry.records() if record.data_fingerprint.startswith("stocks:")]
            passed = {spec_hash for spec_hash, record in _best_records(stock_records, "development").items()
                      if (record.metrics.get("windows") or {}).get("3y", {}).get("count") and not _gate(record.metrics)}
            rules = [rule for rule in rules if rule.rule_hash in passed]
            print(f"開發期已通過門檻的規則：{len(rules)} 個", flush=True)
        if args.top:
            # Second stage: the rules whose screening run (no windows) did best, by full-period excess.
            best: dict[str, float] = {}
            for record in registry.records():
                if record.period == "development" and record.data_fingerprint.startswith("stocks:"):
                    best[record.spec_hash] = max(best.get(record.spec_hash, float("-inf")),
                                                 record.metrics.get("full_period_excess") or float("-inf"))
            rules = sorted((rule for rule in rules if rule.rule_hash in best), key=lambda r: -best[r.rule_hash])[:args.top]
        windows = () if args.screen else (36, 60)
        for rule in rules:
            try:
                record, report = run_stock_trial(rule, args.period, base, registry, RESEARCH / "reports", costs,
                                                 data=data, panel=panel, generated_at=stamp, plan=stock_plan,
                                                 window_months=windows)
            except ResearchGateError as exc:
                print(f"{rule.name}：研究規則不允許 — {exc}", file=sys.stderr)
                continue
            print(describe(report), flush=True)
            print(f"  試驗 #{record.trial_id}；報告：{RESEARCH / 'reports' / record.report_file}")
        return 0

    if args.command == "promote":
        from quant_platform.research.forward import STANDARD_PLAN
        from quant_platform.research.promotion import PromotionPipeline

        saved = _saved_tolerance()
        pipeline = PromotionPipeline(
            RESEARCH, STANDARD_PLAN,
            drawdown_tolerance=saved[0] if saved else None, plan_version=saved[1] if saved else None,
        )
        print("進攻型賽道：" + (f"可承受回撤 {saved[0]:.0%}（計畫第 {saved[1]} 版）" if saved else "未啟用（還沒有投資計畫）"),
              flush=True)
        events = pipeline.advance(load_market(available_assets(args.base), args.base))
        for event in events:
            print(f"{event.name}：{event.stage} {event.outcome} {event.evidence.get('reasons') or ''}", flush=True)
        overview = pipeline.overview()
        print(f"開發期 {overview['candidates']} 個設定，{overview['window_ok']} 個過視窗門檻，"
              f"{overview['eligible']} 個全部關卡通過；新事件 {len(events)} 筆")
        return 0

    if args.command == "forward":
        from quant_platform.research.stock_forward import StockForwardTracker, reconcile

        day = date.fromisoformat(args.date) if args.date else datetime.now(TAIPEI).date()
        tracker = StockForwardTracker(RESEARCH)
        written = tracker.record(day)
        print(f"{day}：寫入 {len(written)} 筆個股規則前向紀錄", flush=True)
        for row in tracker.summary():
            print(f"{row['name']}（{row['since']} 起）：{row['sessions']} 個交易日、投入 {row['contributed']:,.0f}、"
                  f"資產 {row['value']:,.0f}、0050 {row['benchmark_value']:,.0f}、持股 {len(row['holdings'])} 檔、"
                  f"費稅 {row['costs']:,}；對帳 {'正常' if not row['problems'] else '；'.join(row['problems'])}")
        return 0 if not reconcile(tracker.records()) else 1

    if args.command == "stats" and args.family == "stocks":
        from quant_platform.research.stock_rules import WARMUP_YEARS, stock_fingerprint

        start, end = PERIODS[args.period]
        basis = stock_fingerprint(args.base, start.year - WARMUP_YEARS, end.year, until=end)
        report = significance(registry, RESEARCH / "reports", args.period, fingerprint=basis)
        if not report["candidates"]:
            print(f"{args.period} 沒有目前資料版本的個股規則試驗")
            return 1
        path = save_stats(report, RESEARCH / "stats", f"stocks-{args.period}", datetime.now(TAIPEI))
        best = sorted(report["candidates"], key=lambda item: -(item["dsr"]["deflated_sharpe"] or 0))[:10]
        for item in best:
            print(f"#{item['trial_id']} {item['name']}：月超額平均 {item['bootstrap']['mean']:+.3%}"
                  f"（95% 區間 {item['bootstrap']['low']:+.3%}～{item['bootstrap']['high']:+.3%}），"
                  f"DSR {item['dsr']['deflated_sharpe']:.2f}（試驗數 {item['dsr']['trials']}）")
        if report.get("pbo"):
            print(f"PBO {report['pbo']['pbo']:.2f}（{report['pbo']['combinations']} 種切分、{len(report['candidates'])} 個規則）")
        print(f"已寫入 {path}")
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
            f"資料版本 {basis[:12]}：目前 {report['current_trials']} 筆、舊版 {report['older_trials']} 筆紀錄；"
            f"多重檢定以 {report['trials']} 個不同設定計（同一設定重跑只算一次）"
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
