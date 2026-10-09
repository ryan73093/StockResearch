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


def _stocks(args, registry: TrialRegistry) -> int:
    """Stock rules on one period; the run shows on the website (研究 › 執行中的程式)."""
    from quant_platform.research.jobs import JobLog

    label = {"development": "開發期", "validation": "驗證期", "holdout": "最終驗證"}.get(args.period, args.period)
    flow = "啟動資金＋每月" if args.initial else ("一次投入" if args.lump_sum else "每月投入")
    name = f"個股規則 {label}・{args.name}・{flow}" + ("・快篩" if args.screen else "") + (f"・前 {args.top} 名" if args.top else "")
    with JobLog(RESEARCH).start(name, "python -m quant_platform.research " + " ".join(sys.argv[1:])) as job:
        return _stocks_body(args, registry, job)


def _stocks_body(args, registry: TrialRegistry, job) -> int:
    from quant_platform.research.stock_rules import BATCHES as STOCK_BATCHES
    from quant_platform.research.stock_rules import (
        STANDARD_PLAN, WARMUP_YEARS, LumpSumPlan, Panel, SeedPlan, describe, load_stock_data, run_stock_trial,
    )

    if args.period not in ("development", "validation", "holdout"):
        raise SystemExit("stocks：--period 只能是 development、validation 或 holdout")
    if args.period == "holdout" and args.name != "qualified":
        # The final validation runs once per rule: never a whole batch by accident.
        raise SystemExit("最終驗證只跑兩段期間都贏的規則：用 --name qualified")
    start, end = PERIODS[args.period]
    base = Path(args.base)
    costs = broker_costs(args.broker)
    job.update(current="載入資料", force=True)
    data = load_stock_data(base, start.year - WARMUP_YEARS, end.year)
    print(f"個股規則（{args.period}）：{data.notes['symbols']} 檔上市股票、{len(data.sessions)} 個交易日；"
          f"成本：{BROKERS[args.broker].name}", flush=True)
    panel = Panel(data)
    stamp = datetime.now(TAIPEI).strftime("%Y%m%d-%H%M%S")
    if args.initial:
        stock_plan = SeedPlan(args.initial, args.monthly)
        print(f"現金流：啟動資金 {args.initial:,.0f} 元，之後每月 5 日投入 {args.monthly:,.0f} 元", flush=True)
    elif args.lump_sum:
        stock_plan = LumpSumPlan(args.lump_sum)
        print(f"現金流：一次投入 {args.lump_sum:,.0f} 元", flush=True)
    else:
        stock_plan = STANDARD_PLAN
        print("現金流：每月 5 日投入 10,000 元", flush=True)
    if args.name == "qualified":
        from quant_platform.research.stock_forward import qualifying_rules

        rules = [rule for rule, _reason in qualifying_rules(RESEARCH, type(stock_plan).__name__)]
        print(f"兩段期間都贏的規則：{len(rules)} 個", flush=True)
    elif args.name not in STOCK_BATCHES:
        raise SystemExit(f"未知批次：{args.name}；可用：{', '.join(STOCK_BATCHES)}、qualified")
    else:
        rules = STOCK_BATCHES[args.name]()
    if args.passed:
        from quant_platform.research.pool import _best_records, _gate

        stock_records = [record for record in registry.records() if record.data_fingerprint.startswith("stocks:")]
        passed = {spec_hash for spec_hash, record in _best_records(stock_records, "development",
                                                                   type(stock_plan).__name__).items()
                  if (record.metrics.get("windows") or {}).get("3y", {}).get("count") and not _gate(record.metrics)}
        rules = [rule for rule in rules if rule.rule_hash in passed]
        print(f"開發期已通過門檻的規則：{len(rules)} 個", flush=True)
    if args.top:
        # Second stage: the rules whose screening run (no windows) did best, by full-period excess.
        best: dict[str, float] = {}
        kind = type(stock_plan).__name__
        for record in registry.records():
            same_plan = (record.metrics.get("plan") or {}).get("kind", "ContributionPlan") == kind
            if kind == "LumpSumPlan" and str(record.metrics.get("engine") or "") < "stocks-1.2.0":
                same_plan = False   # earlier lump-sum runs had monthly windows
            if same_plan and record.period == "development" and record.data_fingerprint.startswith("stocks:"):
                best[record.spec_hash] = max(best.get(record.spec_hash, float("-inf")),
                                             record.metrics.get("full_period_excess") or float("-inf"))
        rules = sorted((rule for rule in rules if rule.rule_hash in best), key=lambda r: -best[r.rule_hash])[:args.top]
    windows = () if args.screen else (36, 60)
    job.update(total=len(rules), done=0, current="", force=True)
    written = 0
    for index, rule in enumerate(rules):
        job.update(done=index, current=rule.name)
        try:
            record, report = run_stock_trial(rule, args.period, base, registry, RESEARCH / "reports", costs,
                                             data=data, panel=panel, generated_at=stamp, plan=stock_plan,
                                             window_months=windows)
        except ResearchGateError as exc:
            print(f"{rule.name}：研究規則不允許 — {exc}", file=sys.stderr)
            continue
        written += 1
        print(describe(report), flush=True)
        print(f"  試驗 #{record.trial_id}；報告：{RESEARCH / 'reports' / record.report_file}")
    job.update(done=len(rules), force=True)
    job.payload["summary"] = f"{len(rules)} 個規則、{written} 筆結果"
    return 0


def _daily(args, registry: TrialRegistry) -> int:
    """Daily-decision rules on the recent market (S9-W02); the run shows on the website."""
    from quant_platform.research import daily as daily_research
    from quant_platform.research.jobs import JobLog

    if args.name not in daily_research.BATCHES:
        raise SystemExit(f"未知批次：{args.name}；可用：{', '.join(daily_research.BATCHES)}")
    rules = daily_research.BATCHES[args.name]()
    costs = broker_costs(args.broker)
    command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
    with JobLog(RESEARCH).start(f"每天決策規則・{args.name}（2015-06 起、啟動資金 30 萬＋每月 1 萬）", command,
                                total=len(rules)) as job:
        loaded: dict[str, tuple] = {}
        stamp = datetime.now(TAIPEI).strftime("%Y%m%d-%H%M%S")
        passed = 0
        for index, rule in enumerate(rules):
            if rule.universe not in loaded:          # each universe once (R6: TPEx stocks too)
                job.update(current=f"載入 2013 年起全市場行情（{'上市＋上櫃' if rule.universe == 'all' else '上市'}）",
                           force=True)
                loaded[rule.universe] = (*daily_research.load(Path(args.base), rule.universe),
                                         daily_research.fingerprint(Path(args.base), rule.universe), {})
            data, fp, fingerprint, cache = loaded[rule.universe]
            job.update(done=index, current=rule.name)
            if getattr(rule, "kind", None) == "blend":    # 2026-10-09: one account across strategy families
                from quant_platform.research.blend import run_blend_trial

                record, report = run_blend_trial(rule, args.base, registry, RESEARCH / "reports", costs, data, fp,
                                                 fingerprint, cache)
            else:
                record, report = daily_research.run_trial(rule, args.base, registry, RESEARCH / "reports", costs, data,
                                                          fp, fingerprint, cache, stamp)
            reasons = record.metrics.get("reasons") or []
            passed += not reasons
            verdict = "過門檻" if not reasons else "；".join(reasons)
            print(f"{rule.name}：2015-06 起比 0050 {report['full_period_excess']:+.1%}、"
                  f"2020-10 起 {report['since_2020']['excess']:+.1%}；{verdict}", flush=True)
        job.update(done=len(rules), force=True)
        job.payload["summary"] = f"{len(rules)} 個規則、{passed} 個過門檻"
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="研究回測（相同現金流對照定期定額）")
    parser.add_argument(
        "command", choices=("baselines", "trial", "batch", "trials", "stats", "schema", "agent", "promote", "legacy", "stocks",
                            "forward", "factors", "daily", "snapshot", "model", "rl", "exits", "execution", "rl-sleeves", "researcher", "news-events", "scan"),
    )
    parser.add_argument("--date", help="forward：記錄哪一天（預設今天；補記的會標示為補記）")
    parser.add_argument("--passed", action="store_true", help="stocks：只跑開發期已通過視窗與回撤門檻的規則")
    parser.add_argument("--family", default="etf", choices=("etf", "stocks", "daily"), help="stats：ETF、個股或每天決策規則")
    parser.add_argument("--rl-version", default="rl-overlay-1.1.0", choices=("rl-overlay-1.0.0", "rl-overlay-1.1.0"),
                        help="rl：版本（1.1.0 換部位在獎勵裡多扣 2%%）")
    parser.add_argument("--sleeves-version", default="rl-sleeves-2.0.0",
                        choices=("rl-sleeves-1.0.0", "rl-sleeves-1.1.0", "rl-sleeves-2.0.0"),
                        help="rl-sleeves：版本（1.1.0 隨機起點；2.0.0 第三個位置是現金、回撤懲罰加倍）")
    parser.add_argument("--model-version", default="gbm-1.2.0", choices=("gbm-1.0.0", "gbm-1.1.0", "gbm-1.2.0", "gbm-1.3.0"),
                        help="model：要訓練的模型版本（標籤寫在 research/model.py MODELS）")
    parser.add_argument("--universe", default="twse", choices=("twse", "all"),
                        help="stats --family daily：上市（twse）或上市＋上櫃（all）的資料版本")
    parser.add_argument("--recent", action="store_true", help="factors：新設計（2015-06 起、每週排名、看 20 個交易日）")
    parser.add_argument("--experiment", type=int, default=241, help="legacy：舊版模型實驗編號")
    parser.add_argument("--lump-sum", type=float, default=0, help="stocks：一次投入的金額（0＝每月投入）")
    parser.add_argument("--initial", type=float, default=0,
                        help="stocks：啟動資金，之後每月照 --monthly 投入（使用者的帳戶；0＝沒有啟動資金）")
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
    parser.add_argument("--workers", type=int, default=6, help="scan：第一階段同時跑幾個程序")
    parser.add_argument("--finalists", type=int, default=80, help="scan：第二階段用完整引擎跑幾個")
    parser.add_argument("--signals-min", type=int, default=1, help="scan：一組最少幾個訊號")
    parser.add_argument("--signals-max", type=int, default=3, help="scan：一組最多幾個訊號")
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
    if args.command == "researcher":        # S9-W07 (2026-10-09): one weekly round of the new-design researcher
        from quant_platform.config.settings import Settings
        from quant_platform.research.agent import daily_researcher
        from quant_platform.research.jobs import JobLog

        researcher = daily_researcher.build(Settings.from_env(), RESEARCH)
        if args.dry_run:
            instructions, user_input = researcher.build_prompt()
            print(instructions, "\n----\n", user_input, flush=True)
            return 0
        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start(f"AI 研究員（{daily_researcher.VERSION}，每週最多 {daily_researcher.MAX_PROPOSALS} 個規則）",
                                    command) as job:
            job.update(current="請模型提出規則（只看 2015-06～2020-09 的結果）", force=True)
            entry = researcher.run_round(Path(args.base))
            job.payload["summary"] = (f"{entry.get('status')}：提出並回測 {len(entry.get('accepted') or [])} 個、"
                                      f"拒絕 {len(entry.get('rejected') or [])} 個" + (f"；{entry.get('error')}" if entry.get("error") else ""))
        for item in entry.get("accepted") or []:
            print(f"#{item['trial_id']} {item['name']}：{item.get('hypothesis', '')}", flush=True)
        for item in entry.get("rejected") or []:
            print(f"拒絕 {item.get('name')}：{item.get('reason')}", flush=True)
        return 0 if entry.get("status") == "ok" else 1
    if args.command == "news-events":       # R15 D (2026-10-09): score the collected headlines not scored yet
        from quant_platform.config.settings import Settings
        from quant_platform.research import news_events
        from quant_platform.research.jobs import JobLog

        client = news_events.build_client(Settings.from_env(), RESEARCH)
        if client is None:
            print("新聞事件評分沒有啟用或沒有 OPENAI_API_KEY", file=sys.stderr)
            return 1
        with JobLog(RESEARCH).start("新聞事件評分（LLM，每月預算內）", "python -m quant_platform.research news-events") as job:
            outcome = news_events.run(client, Path(args.base), job=job)
            job.payload["summary"] = f"評了 {len(outcome['scored'])} 天、待評 {outcome['pending']} 天" + (
                f"；停止：{outcome['error']}" if outcome["error"] else "")
        print(json.dumps(outcome, ensure_ascii=False, default=str), flush=True)
        return 0 if not outcome["error"] else 1
    if args.command == "legacy":
        return _legacy(args)
    if args.command == "stocks":
        return _stocks(args, registry)

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

    if args.command == "factors":
        from quant_platform.research import factors as factor_strength
        from quant_platform.research.jobs import JobLog

        title = "因子強弱分析（2015-06 起、每週、19 個因子）" if args.recent else "因子強弱分析（全市場、2005 起每月）"
        with JobLog(RESEARCH).start(title, "python -m quant_platform.research " + " ".join(sys.argv[1:])) as job:
            run = factor_strength.run_recent if args.recent else factor_strength.run
            path = run(Path(args.base), RESEARCH / "factors", job=job)
        report = json.loads(path.read_text(encoding="utf-8"))
        for key, item in report["factors"].items():
            first = next(iter(item["periods"].values()), {})
            print(f"{item['label']}：{item['verdict']}；{first.get('label')} 排序相關 {first.get('ic')}（t={first.get('t')}），"
                  f"前段比平均每年 {first.get('top_excess_year')}、比 0050 {first.get('top_vs_0050_year')}", flush=True)
        print(f"已寫入 {path}")
        return 0

    if args.command == "execution":         # R4: the tracked rules' stock orders against the odd-lot auctions
        from quant_platform.research import execution
        from quant_platform.research.jobs import JobLog

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start("盤後零股能不能成交（T0 候選與 T1 的個股委託）", command) as job:
            report = execution.run(Path(args.base), RESEARCH, broker_costs(args.broker), job=job)
            folder = RESEARCH / "execution"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"execution-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
            path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            job.payload["summary"] = f"{len(report['rules'])} 個規則、{report['sessions']} 個抽樣交易日"
        for item in report["rules"]:
            part = item["all"]
            if not part.get("orders"):
                print(f"{item['name']}：抽樣日沒有委託", flush=True)
                continue
            cost = part["cost_bps"]
            print(f"{item['name']}：抽樣委託 {part['orders']} 筆；沒成交 {part['no_trade']:.0%}、"
                  f"限價內成交 {part['filled']:.0%}、剛好在限價 {part['at_limit']:.0%}、超出限價 {part['not_filled']:.0%}；"
                  f"成交價離收盤 中位 {cost['median']} 平均 {cost['mean']} bps（引擎 {part['engine_bps']:g}）；"
                  f"多付約 {item['extra_cost']:,} 元（期末的 {item['extra_cost_share']:.1%}）", flush=True)
        print(f"已寫入 {path}", flush=True)
        return 0

    if args.command == "exits":             # R15 C1b: train the learned exit, walk-forward
        from quant_platform.research import daily as daily_research
        from quant_platform.research import exits as research_exits
        from quant_platform.research.jobs import JobLog

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start(f"學習出場（{research_exits.EXIT_VERSION}，逐年 {research_exits.FIRST_YEAR} 起）", command,
                                    total=daily_research.RECENT_END.year - research_exits.FIRST_YEAR + 1) as job:
            job.update(current="載入行情並重播規則的持股", force=True)
            data, fp = daily_research.load(Path(args.base))
            meta = research_exits.train(fp, research_exits.exits_dir(Path(args.base)), job=job)
            sells = [item["sell_share"] for item in meta["years"].values()]
            job.payload["summary"] = f"{len(meta['years'])} 個年度模型；平均賣出比例 {sum(sells) / len(sells):.1%}" if sells else "沒有模型"
        for year, item in meta["years"].items():
            print(f"{year}：訓練 {item['train_rows']:,} 筆、測試 {item['test_rows']:,} 筆；會賣 {item['sell_share']:.1%}；"
                  f"賣掉的之後比替補多 {item['sold_outcome']}、留著的多 {item['kept_outcome']}", flush=True)
        return 0

    if args.command == "rl-sleeves":        # R15 stage C3: the RL allocator across strategy families
        from quant_platform.research import daily as daily_research
        from quant_platform.research import rl_sleeves
        from quant_platform.research.jobs import JobLog

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        version = args.sleeves_version
        with JobLog(RESEARCH).start(f"強化學習：在策略家族間分配（{version}，2017 起逐年、5 個種子）", command,
                                    total=10) as job:
            job.update(current="載入行情並重播三個家族的帳戶", force=True)
            data, fp = daily_research.load(Path(args.base))
            families = rl_sleeves.build_families(data, fp, broker_costs(args.broker),
                                                 third=rl_sleeves.VERSIONS[version]["third"])
            report = rl_sleeves.walk_forward(families, RESEARCH / "rl" / version, job=job, version=version)
            overall = report["overall"]
            job.payload["summary"] = (
                f"樣本外 {report['test_from']}～{report['test_to']}：RL 年化 {overall['rl']['annual']:.1%}"
                f"（回撤 {overall['rl']['max_drawdown']:.0%}）、訓練期最好的固定組合 {overall['best_fixed_result']['annual']:.1%}"
                f"（{overall['best_fixed_result']['max_drawdown']:.0%}）、各半＋波動大時減碼 {overall['vol_scaled']['annual']:.1%}"
                f"（{overall['vol_scaled']['max_drawdown']:.0%}）、0050 {overall['0050']['annual']:.1%}"
                f"（{overall['0050']['max_drawdown']:.0%}）；{'通過' if report['acceptance']['passed'] else '未通過'}")
        for year, item in report["years"].items():
            print(f"{year}：RL {item['rl']['growth']:+.1%}（回撤 {item['rl']['max_drawdown']:.0%}、平均 {item['average']}、"
                  f"換 {item['moves']} 次）；訓練期最好的固定 {item['best_fixed']} {item['best_fixed_result']['growth']:+.1%}；"
                  f"機器學習 {item['model']['growth']:+.1%}；趨勢 {item['trend']['growth']:+.1%}；0050 {item['0050']['growth']:+.1%}",
                  flush=True)
        print(json.dumps(report["overall"], ensure_ascii=False), flush=True)
        return 0

    if args.command == "rl":                # R15 stage C1: the RL exposure overlay, walk-forward
        from quant_platform.research import daily as daily_research
        from quant_platform.research import rl as research_rl
        from quant_platform.research.jobs import JobLog

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        version = args.rl_version
        out = RESEARCH / "rl" / version
        with JobLog(RESEARCH).start(f"強化學習部位調整（{version}，2017 起逐年、5 個種子）", command,
                                    total=10) as job:
            job.update(current="載入行情並重播規則帳戶", force=True)
            data, fp = daily_research.load(Path(args.base))
            overlay = research_rl.build_overlay(data, fp, broker_costs(args.broker))
            report = research_rl.walk_forward(overlay, out, job=job, version=version)
            overall = report["overall"]
            job.payload["summary"] = (f"樣本外 {report['test_from']}～{report['test_to']}：RL {overall['rl']['growth']:+.0%}"
                                      f"（回撤 {overall['rl']['max_drawdown']:.0%}）、規則 {overall['rule']['growth']:+.0%}"
                                      f"（{overall['rule']['max_drawdown']:.0%}）、0050 {overall['0050']['growth']:+.0%}")
        for year, item in report["years"].items():
            print(f"{year}：RL {item['rl']['growth']:+.1%}（回撤 {item['rl']['max_drawdown']:.0%}、平均個股 {item['average_share']:.0%}、"
                  f"換 {item['switches']} 次）；規則 {item['rule']['growth']:+.1%}（{item['rule']['max_drawdown']:.0%}）；"
                  f"一半 {item['half']['growth']:+.1%}；0050 {item['0050']['growth']:+.1%}", flush=True)
        print(json.dumps(report["overall"], ensure_ascii=False), flush=True)
        return 0

    if args.command == "model":             # R15 stage B: train the walk-forward models
        from quant_platform.research import daily as daily_research
        import numpy as np

        from quant_platform.research import model as research_model
        from quant_platform.research.jobs import JobLog

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        years = list(range(research_model.FIRST_YEAR, daily_research.RECENT_END.year + 1))
        version = args.model_version
        label = research_model.VERSIONS[version]["label"]
        with JobLog(RESEARCH).start(f"訓練機器學習模型（{version}，{label} 標籤，逐年 {years[0]}～{years[-1]}）",
                                    command, total=len(years)) as job:
            job.update(current="載入 2013 年起上市行情與籌碼", force=True)
            data, fp = daily_research.load(Path(args.base))
            fingerprint = daily_research.fingerprint(Path(args.base))
            meta = research_model.train(fp, research_model.model_dir(Path(args.base), version), fingerprint, years,
                                        job=job, label=label, version=version,
                                        horizon=research_model.VERSIONS[version].get("horizon", research_model.HORIZON))
            job.update(done=len(years), force=True)
            ics = [item["ic"] for item in meta["years"].values() if item.get("ic") is not None]
            job.payload["summary"] = f"{len(meta['years'])} 個年度模型；樣本外 IC 平均 {np.mean(ics):+.3f}" if ics else "沒有模型"
        for year, item in meta["years"].items():
            print(f"{year}：訓練 {item['train_rows']:,} 筆（標籤到 {item['train_until']}）、樣本外 IC {item['ic']}、"
                  f"IC 為正的週 {item['ic_positive']}、前五分之一排名多 {item['top_fifth_rank_gap']}、"
                  f"20 日報酬比中位數多：前五分之一 {item.get('top_fifth_gain')}、前 20 名 {item.get('top20_gain')}",
                  flush=True)
        return 0

    if args.command == "snapshot":          # S9-W05: the stock page's factor snapshot (worker 15:45)
        from quant_platform.research.snapshot import build as build_snapshot

        print(json.dumps(build_snapshot(Path(args.base), RESEARCH), ensure_ascii=False), flush=True)
        return 0

    if args.command == "daily":
        return _daily(args, registry)

    if args.command == "scan":
        # 2026-10-10: the broad two-stage search (research/scan.py)
        from quant_platform.research import scan
        from quant_platform.research.jobs import JobLog

        count = len(scan.candidates(None, args.signals_max, args.signals_min))
        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start(f"大規模策略搜尋（{args.signals_min}～{args.signals_max} 個訊號一組，{count:,} 個候選，"
                                    f"第一階段 2015-06～2020-09）",
                                    command, total=count + args.finalists) as job:
            summary = scan.run_scan(Path(args.base), RESEARCH, broker_costs(args.broker), workers=args.workers,
                                    count=args.finalists, job=job, largest=args.signals_max,
                                    smallest=args.signals_min)
            job.payload["summary"] = (f"{summary['candidates']:,} 個候選、{summary['passed_screen']:,} 個過第一階段、"
                                      f"{summary['finalists']} 個完整回測：{summary['tiers']}")
        for item in summary["results"]:
            print(f"#{item['trial_id']} {item['tier']} {item['name']}：2015-06 起 {item['full_excess']:+.1%}、"
                  f"2020-10 起 {item['since_2020']:+.1%}、回撤 {item['drawdown']:.1%}", flush=True)
        print(json.dumps({key: value for key, value in summary.items() if key != "results"}, ensure_ascii=False))
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

    if args.command == "stats" and args.family == "daily":
        from quant_platform.research import daily as daily_research

        basis = daily_research.fingerprint(Path(args.base), args.universe)
        report = significance(registry, RESEARCH / "reports", daily_research.PERIOD, fingerprint=basis)
        if not report["candidates"]:
            print("沒有目前資料版本的每天決策規則試驗")
            return 1
        path = save_stats(report, RESEARCH / "stats", "daily-recent", datetime.now(TAIPEI))
        for item in sorted(report["candidates"], key=lambda item: -(item["dsr"]["deflated_sharpe"] or 0))[:8]:
            print(f"#{item['trial_id']} {item['name']}：月超額平均 {item['bootstrap']['mean']:+.3%}"
                  f"（95% 區間 {item['bootstrap']['low']:+.3%}～{item['bootstrap']['high']:+.3%}），"
                  f"DSR {item['dsr']['deflated_sharpe']:.2f}（試驗數 {item['dsr']['trials']}）")
        if report.get("pbo"):
            print(f"PBO {report['pbo']['pbo']:.2f}（{len(report['candidates'])} 個規則）")
        print(f"已寫入 {path}")
        return 0

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
