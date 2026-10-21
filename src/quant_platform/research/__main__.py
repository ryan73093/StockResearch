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
import time
import traceback
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
    from quant_platform.research.pool import record_sources

    record_sources(RESEARCH, rules, f"每天決策批次 {args.name}")       # 2026-10-21: the pool reads it
    costs = broker_costs(args.broker)
    command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
    with JobLog(RESEARCH).start(f"每天決策規則・{args.name}（2015-06 起、啟動資金 30 萬＋每月 1 萬）", command,
                                total=len(rules)) as job:
        loaded: dict[str, tuple] = {}
        stamp = datetime.now(TAIPEI).strftime("%Y%m%d-%H%M%S")
        passed, failed = 0, []
        for index, rule in enumerate(rules):
            # 2026-10-20 (AGENTS.md: research runs keep out of 13:30–14:40, the after-close orders): wait between rules
            while datetime.now(TAIPEI).weekday() < 5 and "13:30" <= datetime.now(TAIPEI).strftime("%H:%M") < "14:40":
                job.update(current="13:30～14:40 盤後單時段，暫停", force=True)
                time.sleep(60)
            if rule.universe not in loaded:          # each universe once (R6: TPEx stocks too)
                job.update(current=f"載入 2013 年起全市場行情（{'上市＋上櫃' if rule.universe == 'all' else '上市'}）",
                           force=True)
                loaded[rule.universe] = (*daily_research.load(Path(args.base), rule.universe),
                                         daily_research.fingerprint(Path(args.base), rule.universe), {})
            data, fp, fingerprint, cache = loaded[rule.universe]
            job.update(done=index, current=rule.name)
            try:
                if getattr(rule, "kind", None) == "blend":    # 2026-10-09: one account across strategy families
                    from quant_platform.research.blend import run_blend_trial

                    record, report = run_blend_trial(rule, args.base, registry, RESEARCH / "reports", costs, data, fp,
                                                     fingerprint, cache)
                else:
                    record, report = daily_research.run_trial(rule, args.base, registry, RESEARCH / "reports", costs,
                                                              data, fp, fingerprint, cache, stamp)
            except Exception as error:  # noqa: BLE001 - 2026-10-20: one rule's error no longer ends a 379-rule batch
                failed.append(f"{rule.name}：{type(error).__name__}: {str(error)[:160]}")
                print(f"{rule.name}：失敗 {type(error).__name__}: {error}", flush=True)
                traceback.print_exc()
                continue
            reasons = record.metrics.get("reasons") or []
            passed += not reasons
            verdict = "過門檻" if not reasons else "；".join(reasons)
            print(f"{rule.name}：2015-06 起比 0050 {report['full_period_excess']:+.1%}、"
                  f"2020-10 起 {report['since_2020']['excess']:+.1%}；{verdict}", flush=True)
        job.update(done=len(rules), force=True)
        job.payload["summary"] = (f"{len(rules)} 個規則、{passed} 個過門檻"
                                  + (f"、{len(failed)} 個失敗（第一個：{failed[0]}）" if failed else ""))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="研究回測（相同現金流對照定期定額）")
    parser.add_argument(
        "command", choices=("baselines", "trial", "batch", "trials", "stats", "schema", "agent", "promote", "legacy", "stocks",
                            "forward", "factors", "daily", "snapshot", "model", "rl", "exits", "execution", "rl-sleeves", "researcher", "news-events", "scan", "tech-rl", "rl-strategies", "diagnose", "scan-trials", "leakcheck",
                            "forward-board", "deep2-diagnose", "deep2-leakcheck", "exit2", "robustness", "hypotheses", "trade-audit", "tech-rl2", "factor-rl21", "block-rl3", "block-rl31", "block-rl32", "block-rl33", "block-rl34", "meta-label", "scan-large", "llm-trader", "pipeline-wait", "worst-rl", "free-rl4", "free-rl41", "rolling",
                            "window-weight", "intraday", "limits-check"),
    )
    parser.add_argument("--date", help="forward：記錄哪一天（預設今天；補記的會標示為補記）")
    parser.add_argument("--correlations", action="store_true", help="factors：因子之間的排名相關與高度相關的群")
    parser.add_argument("--events", help="factors：事件因子分析，逗號分隔（例如 low_insider_sell_60,buyback_60）")
    parser.add_argument("--explain", action="store_true",
                        help="forward：替 2026-10-19 以前的決定補上買賣原因（重算排名，和當時一樣的才補）")
    parser.add_argument("--trend-from", help="forward：（2026-10-20 停用）平台不替規則轉換持有方式")
    parser.add_argument("--freeze", action="store_true",
                        help="forward：把每個帳戶最新紀錄背後的決定寫下來（之後重算不再改變已做的決定）")
    parser.add_argument("--rebase", help="forward：把接不上前一天的紀錄標成重算基準，附上原因")
    parser.add_argument("--restart-from", help="forward：所有在跑的帳戶從這天重新起算（舊紀錄封存，YYYY-MM-DD）")
    parser.add_argument("--reason", help="forward --restart-from：原因")
    parser.add_argument("--stop-ai-account", action="store_true",
                        help="forward：停止追蹤「AI 研究員（整體）」帳戶（沒有合格規則時整個放 0050；紀錄保留）")
    parser.add_argument("--passed", action="store_true", help="stocks：只跑開發期已通過視窗與回撤門檻的規則")
    parser.add_argument("--family", default="etf", choices=("etf", "stocks", "daily"), help="stats：ETF、個股或每天決策規則")
    parser.add_argument("--rl-version", default="rl-overlay-1.1.0", choices=("rl-overlay-1.0.0", "rl-overlay-1.1.0"),
                        help="rl：版本（1.1.0 換部位在獎勵裡多扣 2%%）")
    parser.add_argument("--sleeves-version", default="rl-sleeves-2.0.0",
                        choices=("rl-sleeves-1.0.0", "rl-sleeves-1.1.0", "rl-sleeves-2.0.0"),
                        help="rl-sleeves：版本（1.1.0 隨機起點；2.0.0 第三個位置是現金、回撤懲罰加倍）")
    from quant_platform.research.model import VERSIONS as MODEL_VERSIONS

    # 2026-10-20: every registered version (a hand-kept list left gbm-2.3.0 and dl-2.4.0 out and their runs failed)
    parser.add_argument("--model-version", default="gbm-1.2.0", choices=sorted(MODEL_VERSIONS),
                        help="model：要訓練的模型版本（標籤寫在 research/model.py MODELS）")
    parser.add_argument("--universe", default="twse", choices=("twse", "all"),
                        help="stats --family daily：上市（twse）或上市＋上櫃（all）的資料版本")
    parser.add_argument("--recent", action="store_true", help="factors：新設計（2015-06 起、每週排名、看 20 個交易日）")
    parser.add_argument("--only", help="factors --recent：只分析這些因子（逗號分隔），其他沿用最新一份完整報告")
    parser.add_argument("--experiment", type=int, default=241, help="legacy：舊版模型實驗編號")
    parser.add_argument("--lump-sum", type=float, default=0, help="stocks：一次投入的金額（0＝每月投入）")
    parser.add_argument("--initial", type=float, default=0,
                        help="stocks：啟動資金，之後每月照 --monthly 投入（使用者的帳戶；0＝沒有啟動資金）")
    parser.add_argument("--screen", action="store_true", help="stocks：只算全期間、不算滾動視窗（大掃描第一階段）")
    parser.add_argument("--trials", help="robustness：指定的試驗編號，逗號分隔（任何等級，含組合帳戶）")
    parser.add_argument("--top", type=int, default=0, help="stocks：只跑登錄檔中全期超額最高的前 N 個規則（第二階段）")
    parser.add_argument("--dry-run", action="store_true", help="agent：只印出提示內容，不呼叫模型")
    parser.add_argument("--check", action="store_true", help="agent：極小的連線檢查呼叫（金鑰、模型、JSON 格式）")
    parser.add_argument("--name", default="first", help="batch：批次名稱")
    parser.add_argument("--spec")
    parser.add_argument("--period", default="full", choices=tuple(PERIODS))
    parser.add_argument("--monthly", type=float, default=10_000)
    parser.add_argument("--day", type=int, default=5)
    parser.add_argument("--pipeline", help="pipeline-wait：等待中的研究流程名稱")
    parser.add_argument("--wait-reason", help="pipeline-wait：在等什麼")
    parser.add_argument("--pid", type=int, help="pipeline-wait：研究流程（PowerShell）的 PID")
    parser.add_argument("--finish", help="pipeline-wait：等完了，結束這筆紀錄（pipeline-wait 印出的編號）")
    parser.add_argument("--decision-day", help="llm-trader：決定哪一天收盤後（YYYY-MM-DD，預設今天）")
    parser.add_argument("--replay-from", help="llm-trader：從這一天起補做每個交易日的決定到 --decision-day（只用當天以前的資料）")
    parser.add_argument("--windows", default="36,60")
    parser.add_argument("--cost-scale", type=float, default=1.0)
    parser.add_argument("--broker", default="conservative", choices=tuple(BROKERS), help="券商手續費設定")
    parser.add_argument("--dividend-lag", type=int, default=25)
    parser.add_argument("--execution-lag", type=int, default=0, help="穩健性：晚幾個交易日成交")
    parser.add_argument("--base", default=str(DEFAULT_BASE))
    parser.add_argument("--workers", type=int, default=6, help="scan：第一階段同時跑幾個程序")
    parser.add_argument("--finalists", type=int, default=80, help="scan：第二階段用完整引擎跑幾個")
    parser.add_argument("--universes", help="scan-large：股票池，逗號分隔（all＝全部上市、large＝市值前 100；預設兩種）")
    parser.add_argument("--signals-top", type=int, help="scan-large：只用前一輪過第一階段時出現最多的前 N 個訊號")
    parser.add_argument("--signals-from", help="scan-large：--signals-top 依據的前一輪搜尋資料夾")
    parser.add_argument("--holds", help="scan-large：持有方式，逗號分隔（days＝固定持有 20 天、trend＝月線上續抱、free＝不設最短持有；預設三種）")
    parser.add_argument("--signals-min", type=int, default=1, help="scan：一組最少幾個訊號")
    parser.add_argument("--signals-max", type=int, default=3, help="scan：一組最多幾個訊號")
    parser.add_argument("--require", help="scan：只跑含這個訊號的組（例如新模型 ml_gbm_quality）")
    parser.add_argument("--refit-accounts", action="store_true",
                        help="rolling --step models：重訓後，讀到這些模型的前向帳戶從起算日重新記錄（舊紀錄與決定封存）")
    parser.add_argument("--step", choices=("select", "models", "holdout"),
                        help="rolling：每月滾動的哪一步（挑策略／重訓模型／前向測試）")
    parser.add_argument("--cutoff", help="rolling：截止日（預設：兩個月前的月底）")
    parser.add_argument("--only-new", action="store_true",
                        help="rolling --step select：只評這個月還沒評過的規則，加進原本的名單（2026-10-18：組合帳戶）")
    parser.add_argument("--all-rules", action="store_true",
                        help="rolling --step select：全部重評（預設：上月比 0050 少三成以上的 T3 沿用上月結果，每季全部重評）")
    parser.add_argument("--calm", action="store_true",
                        help="scan：最後名單只挑開發期 0050 每次大跌（10%% 以上）都沒有比 0050 多跌超過 1 個百分點的（使用者 2026-10-18）")
    parser.add_argument("--prefilter", action="store_true",
                        help="scan：先在開發期單獨篩每個訊號，只用單獨贏過 0050 的訊號組合（GPU 用來跑 4 個一組）")
    parser.add_argument("--require-new", action="store_true",
                        help="scan：只跑至少含一個 2026-10-13 新價量因子的組（先過新穎性閘門）")
    parser.add_argument("--require-time", action="store_true",
                        help="scan：只跑至少含一個 2026-10-14 時間因子的組（先過新穎性閘門）")
    parser.add_argument("--require-industry", action="store_true",
                        help="scan：只跑至少含一個產業因子的組（先過新穎性閘門；2026-10-20）")
    parser.add_argument("--require-dealer", action="store_true",
                        help="scan：只跑至少含一個自營商因子的組（先過新穎性閘門）")
    parser.add_argument("--require-issuance", action="store_true",
                        help="scan：只跑至少含「股本沒有膨脹」的組（先過新穎性閘門）")
    parser.add_argument("--require-corporate", action="store_true",
                        help="scan：只跑至少含一個股利調升或現金增資因子的組（先過新穎性閘門）")
    parser.add_argument("--require-lending", action="store_true",
                        help="scan：只跑至少含一個借券費率或借券量因子的組（先過新穎性閘門）")
    parser.add_argument("--require-short", action="store_true",
                        help="scan：只跑至少含一個借券、當沖、注意或處置因子的組（先過新穎性閘門）")
    parser.add_argument("--resume", help="scan：第一階段已完成的搜尋資料夾，只跑第二階段")
    parser.add_argument("--pool", type=int, default=600, help="scan：從第一階段最好的前幾名裡挑彼此不同的最後名單")
    parser.add_argument("--by-excess", action="store_true",
                        help="scan：照 1.0.0 依開發期超額挑最後名單（預設 1.1.0 依穩定度）")
    parser.add_argument("--tech-version", default="tech-rl-1.0.0", choices=("tech-rl-1.0.0", "tech-rl-1.1.0"),
                        help="tech-rl：機器人版本（1.1.0 每週決策）")
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

        if args.correlations:                    # 2026-10-20: which factors rank stocks alike
            with JobLog(RESEARCH).start("因子相關分析（2015-06 起、每 20 個交易日）",
                                        "python -m quant_platform.research " + " ".join(sys.argv[1:])) as job:
                path = factor_strength.correlations(Path(args.base), RESEARCH / "factors", job=job)
            report = json.loads(path.read_text(encoding="utf-8"))
            for family in report["families"]:
                print("、".join(family["labels"]), flush=True)
            print(f"{report['factors']} 個因子、{len(report['families'])} 群；已寫入 {path}")
            return 0
        if args.events:                          # 2026-10-19: event factors, the stocks with an event against all
            names = [name.strip() for name in args.events.split(",") if name.strip()]
            with JobLog(RESEARCH).start(f"事件因子分析（{len(names)} 個，2015-06 起每週）",
                                        "python -m quant_platform.research " + " ".join(sys.argv[1:])) as job:
                path = factor_strength.event_study(Path(args.base), RESEARCH / "factors", names, job=job)
            report = json.loads(path.read_text(encoding="utf-8"))
            for key, item in report["factors"].items():
                periods = "；".join(f"{period['label']} {period['excess_20']:+.2%}（t={period['t']}）"
                                   for period in item["periods"].values())
                print(f"{item['label']}：每週約 {item['stocks_per_week']} 檔；之後 20 日比平均 {periods}", flush=True)
            print(f"已寫入 {path}")
            return 0

        title = "因子強弱分析（2015-06 起、每週、19 個因子）" if args.recent else "因子強弱分析（全市場、2005 起每月）"
        with JobLog(RESEARCH).start(title, "python -m quant_platform.research " + " ".join(sys.argv[1:])) as job:
            if args.recent and args.only:          # 2026-10-21: a new family only, over the latest full report
                path = factor_strength.run_recent(Path(args.base), RESEARCH / "factors", job=job,
                                                  names=[name.strip() for name in args.only.split(",") if name.strip()])
            else:
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
            # 2026-10-18: a model learning from the TPEx stocks too loads the "all" panel (MODELS[...]["universe"])
            data, fp = daily_research.load(Path(args.base), universe=research_model.VERSIONS[version].get("universe", "twse"))
            fingerprint = daily_research.fingerprint(Path(args.base))
            meta = research_model.train(fp, research_model.model_dir(Path(args.base), version), fingerprint, years,
                                        job=job, label=label, version=version,
                                        horizon=research_model.VERSIONS[version].get("horizon", research_model.HORIZON))
            job.update(done=len(years), force=True)
            ics = [item["ic"] for item in meta["years"].values() if item.get("ic") is not None]
            job.payload["summary"] = f"{len(meta['years'])} 個年度模型；樣本外 IC 平均 {np.mean(ics):+.3f}" if ics else "沒有模型"
            if label == "deep2":                # 2026-10-17: the second deep version's registered gate
                from quant_platform.research.model_deep2 import gate as deep2_gate

                check = deep2_gate(research_model.models_root(Path(args.base)), version)
                job.payload["summary"] = (f"{len(meta['years'])} 個年度模型；2016～2020 前 20 名多 {check.get('top20_gain')}"
                                          f"（{check.get('against')} {check.get('against_top20_gain')}）；"
                                          f"門檻{'通過' if check['passed'] else '沒過'}")
                print(check, flush=True)
            if label == "deep_risk2":           # 2026-10-17: R3's registered gate
                check = meta.get("gate") or {}
                job.payload["summary"] = (f"{len(meta['years'])} 個年度模型；2016～2020 波動十分位內 AUC "
                                          f"{check.get('auc_within_volatility')}、被否決的輸同組 {check.get('years_vetoed_lost')}／5 年；"
                                          f"門檻{'通過' if check.get('passed') else '沒過'}")
                print(check, flush=True)
            if label == "deep_risk":            # 2026-10-16: the drop model's AUC against the volatility yardstick
                from quant_platform.research.model_risk import gate as risk_gate

                check = risk_gate(research_model.models_root(Path(args.base)))
                job.payload["summary"] = (f"{len(meta['years'])} 個年度模型；2016～2020 AUC {check['auc']}、"
                                          f"波動對照 {check['auc_volatility']}；門檻{'通過' if check['passed'] else '沒過'}")
                print(check, flush=True)
        for year, item in meta["years"].items():
            print(f"{year}：訓練 {item.get('train_rows', item.get('train_samples'))} 筆、樣本外 IC {item.get('ic')}、"
                  f"IC 為正的週 {item.get('ic_positive')}、前五分之一排名多 {item.get('top_fifth_rank_gap')}、"
                  f"20 日報酬比中位數多：前五分之一 {item.get('top_fifth_gain')}、前 20 名 {item.get('top20_gain')}",
                  flush=True)
        return 0

    if args.command == "snapshot":          # S9-W05: the stock page's factor snapshot (worker 15:45)
        from quant_platform.research.snapshot import build as build_snapshot

        print(json.dumps(build_snapshot(Path(args.base), RESEARCH), ensure_ascii=False), flush=True)
        return 0

    if args.command == "daily":
        return _daily(args, registry)

    if args.command == "hypotheses":
        # 2026-10-13: the owner's waiting hypothesis checks (research/hypothesis.py)
        from quant_platform.config.settings import Settings
        from quant_platform.research import hypothesis
        from quant_platform.research.jobs import JobLog

        waiting = hypothesis.pending(RESEARCH)
        if not waiting:
            print("沒有等待中的假設")
            return 0
        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start(f"假設檢驗（{len(waiting)} 個）", command, total=len(waiting)) as job:
            done = hypothesis.run_pending(RESEARCH, Path(args.base), Settings.from_env(), broker_costs(args.broker), job=job)
            job.payload["summary"] = "；".join(f"{item['text'][:20]}→{item.get('verdict', item['status'])}" for item in done)
        for item in done:
            print(f"{item['text']}：{item.get('verdict', item['status'])}；{item.get('reason', '')}", flush=True)
        return 0

    if args.command == "deep2-diagnose":    # 2026-10-17: stage 0 of the review, free (research/model_deep2.py)
        from quant_platform.research import daily as daily_research
        from quant_platform.research.model import models_root
        from quant_platform.research.model_deep2 import diagnose

        _data, fp = daily_research.load(Path(args.base))
        result = diagnose(fp, models_root(Path(args.base)))
        print(json.dumps(result["development"], ensure_ascii=False), flush=True)
        for year, row in result["years"].items():
            print(year, json.dumps(row, ensure_ascii=False), flush=True)
        return 0

    if args.command == "robustness":        # 2026-10-18: the best T0 candidates with costs doubled, filled a session late
        from quant_platform.research import robustness
        from quant_platform.research.jobs import JobLog

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        top = args.top or 10
        trials = [int(item) for item in args.trials.split(",")] if args.trials else None
        title = (f"穩健性檢查：#{'、#'.join(str(item) for item in trials)}" if trials
                 else f"穩健性檢查：最好的 {top} 個 T0 候選") + "，成本加倍、晚一個交易日成交"
        with JobLog(RESEARCH).start(title, command, total=len(trials) if trials else top) as job:
            result = robustness.run(Path(args.base), RESEARCH, broker_costs(args.broker), top, job, trials)
            kept = sum(1 for row in result["rows"] if str(row["costs_doubled"]["tier"]).startswith("T0"))
            job.payload["summary"] = f"成本加倍後仍是 T0 候選（含高風險）：{kept}／{len(result['rows'])}"
        for row in result["rows"]:
            print(json.dumps(row, ensure_ascii=False), flush=True)
        return 0

    if args.command == "exit2":             # 2026-10-17: E2, the weekly optimal-stopping exit (research/exits2.py)
        from quant_platform.research import daily as daily_research
        from quant_platform.research import exits2
        from quant_platform.research.jobs import JobLog
        from quant_platform.research.model import models_root

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start("學習出場第二版（每週最佳停止，dl-2.1.0 的排名）：模型與門檻", command) as job:
            job.update(current="載入行情與因子", force=True)
            _data, fp = daily_research.load(Path(args.base))
            meta = exits2.train(fp, models_root(Path(args.base)) / exits2.VERSION, job=job)
            check = meta["gate"]
            job.payload["summary"] = (f"換掉的比替補（之後 60 日）{check.get('swapped_gap')}、比隨機差的年 "
                                      f"{check.get('years_worse_than_random')}／5、每週換 {check.get('swaps_per_week')}；"
                                      f"門檻{'通過' if check['passed'] else '沒過'}")
        for year, item in meta["years"].items():
            print(year, json.dumps(item, ensure_ascii=False), flush=True)
        print(json.dumps(meta["gate"], ensure_ascii=False), flush=True)
        return 0

    if args.command == "deep2-leakcheck":   # 2026-10-17: dl-2.1.0's kept scores against data cut at the day
        from quant_platform.research import daily as daily_research
        from quant_platform.research import leakcheck
        from quant_platform.research.model import models_root
        from quant_platform.research.model_deep2 import leak_check

        data, fp = daily_research.load(Path(args.base))
        days = leakcheck.sample_days(fp.sessions, 6, daily_research.RECENT_START)
        report = leak_check(Path(args.base), data, models_root(Path(args.base)), "dl-2.1.0", days)
        for day, item in report["results"].items():
            print(day, json.dumps(item, ensure_ascii=False), flush=True)
        print("有差異（偷看）" if report["leak"] else "沒有發現偷看", flush=True)
        return 0 if not report["leak"] else 1

    if args.command == "forward-board":
        # 2026-10-13: the common-start board (research/forward_board.py; also built after the 15:30 forward record)
        from quant_platform.research.forward_board import build as build_board

        day = date.fromisoformat(args.date) if args.date else datetime.now(TAIPEI).date()
        board = build_board(RESEARCH, Path(args.base), day, broker_costs(args.broker))
        if not board:
            print("沒有可以計算的前向策略")
            return 1
        print(f"{board['common_start']} 起到 {board['date']}（{board['sessions']} 個交易日）：{board['counts']}")
        for row in sorted((row for row in board["rows"] if "rank" in row), key=lambda row: row["rank"])[:15]:
            print(f"{row['rank']:>2}. {row['name'][:40]}：報酬 {row['return']:+.2%}、比 0050 {row['excess']:+.2%}、{row['state']}")
        return 0

    if args.command == "tech-rl2":
        # 2026-10-14 (使用者：RL 的目標設計是不是有問題？): the robot 2.0, walk-forward (research/rl_timing.py)
        from quant_platform.research import daily as daily_research
        from quant_platform.research import rl_timing
        from quant_platform.research.jobs import JobLog
        from quant_platform.research.model import models_root

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start("技術 RL 機器人 2.0：逐年滾動訓練（20 個技術因子的權重，永遠滿倉）", command) as job:
            data, fp = daily_research.load(Path(args.base))
            meta = rl_timing.walk_forward(fp, rl_timing.benchmark_units(data, fp.sessions),
                                          models_root(Path(args.base)) / rl_timing.VERSION, job=job)
            robot = sum(item["robot_excess"] for item in meta["yearly"].values())
            alike = sum(item["equal_excess"] for item in meta["yearly"].values())
            job.payload["summary"] = f"{len(meta['yearly'])} 年樣本外：機器人比 0050 {robot:+.3f}、平均權重 {alike:+.3f}（對數報酬加總）"
        print(job.payload["summary"])
        for year, item in meta["yearly"].items():
            print(year, item)
        return 0

    if args.command == "factor-rl21":
        # 2026-10-14: the robot 2.1 — the robot 2.0 over every factor and model score (research/rl_timing.py)
        from quant_platform.research import daily as daily_research
        from quant_platform.research import rl_timing
        from quant_platform.research.jobs import JobLog
        from quant_platform.research.model import models_root

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start("RL 機器人 2.1：逐年滾動訓練（全部因子與模型分數的權重，永遠滿倉）", command) as job:
            data, fp = daily_research.load(Path(args.base))
            meta = rl_timing.walk_forward(fp, rl_timing.benchmark_units(data, fp.sessions),
                                          models_root(Path(args.base)) / rl_timing.ALL_VERSION, job=job,
                                          version=rl_timing.ALL_VERSION)
            robot = sum(item["robot_excess"] for item in meta["yearly"].values())
            alike = sum(item["equal_excess"] for item in meta["yearly"].values())
            job.payload["summary"] = f"{len(meta['yearly'])} 年樣本外：機器人比 0050 {robot:+.3f}、平均權重 {alike:+.3f}（對數報酬加總）"
        print(job.payload["summary"])
        for year, item in meta["yearly"].items():
            print(year, item)
        return 0

    if args.command == "block-rl3":
        # 2026-10-16: the robot 3.0 — eight blocks, four offsets, a held-out fifth (research/rl_timing.py)
        from quant_platform.research import daily as daily_research
        from quant_platform.research import rl_timing
        from quant_platform.research.jobs import JobLog
        from quant_platform.research.model import models_root

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start("RL 機器人 3.0：逐年滾動訓練（8 個強訊號區塊的權重，驗證沒贏平均就不押）", command) as job:
            data, fp = daily_research.load(Path(args.base))
            meta = rl_timing.walk_forward_blocks(fp, rl_timing.benchmark_units(data, fp.sessions),
                                                 models_root(Path(args.base)) / rl_timing.BLOCK_VERSION, job=job)
            robot = sum(item["robot_excess"] for item in meta["yearly"].values())
            alike = sum(item["equal_excess"] for item in meta["yearly"].values())
            abstained = sum(1 for item in meta["yearly"].values() if item["abstained"])
            job.payload["summary"] = (f"{len(meta['yearly'])} 年樣本外：機器人比 0050 {robot:+.3f}、平均 {alike:+.3f}"
                                      f"（對數報酬加總）；{abstained} 年驗證沒贏平均、不押")
        print(job.payload["summary"])
        for year, item in meta["yearly"].items():
            print(year, {key: value for key, value in item.items() if not key.startswith("curve")})
        return 0

    if args.command == "rolling":
        # 2026-10-16 (使用者：訓練到兩個月前，後面兩個月拿來做前向測試；模型也要每月更新): research/rolling.py
        from datetime import date as _date

        from quant_platform.research import daily as daily_research
        from quant_platform.research import model as research_model
        from quant_platform.research import rolling
        from quant_platform.research.jobs import JobLog

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        cutoff = _date.fromisoformat(args.cutoff) if args.cutoff else rolling.cutoff_for(_date.today())
        path = rolling.folder(RESEARCH) / f"{cutoff.isoformat()}.json"
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
            "version": rolling.ROLLING_VERSION, "cutoff": cutoff.isoformat(), "graded": [], "holdout": [], "models": []}
        if args.step == "select":
            with JobLog(RESEARCH).start(f"每月滾動：用 {cutoff} 以前的資料挑策略（全部規則回測到截止日）", command) as job:
                def progress(done, total, row):
                    job.update(done=done, total=total, current=f"{row.get('name', '')[:40]}：{'過' if row.get('passed') else '沒過'}")
                kept = [row for row in payload.get("graded") or [] if not row.get("error")] if args.only_new else []
                # 2026-10-21 (使用者：每個月都要跑那麼久，我時間很多是不是): a T3 rule that ended under 70% of 0050 last
                # month cannot pass a month later — its grade is carried; every rule again each quarter
                carried = ([] if args.only_new or args.all_rules
                           else rolling.carried(RESEARCH, cutoff, daily_research.ENGINE_VERSION))
                kept = kept + carried
                graded = kept + rolling.select(Path(args.base), RESEARCH, cutoff, args.broker, args.workers, progress,
                                               skip={row["rule_hash"] for row in kept})
                payload.update(graded=graded, selected_at=datetime.now().isoformat(timespec="seconds"),
                               engine=daily_research.ENGINE_VERSION)
                rolling.save(RESEARCH, cutoff, payload)
                job.payload["summary"] = (f"{len(graded)} 個規則、{sum(1 for row in graded if row.get('passed'))} 個過截止日門檻"
                                          + (f"（{len(carried)} 個上月比 0050 少三成以上的 T3 沿用上月結果）" if carried else ""))
            print(job.payload["summary"])
            return 0
        if args.step == "models":
            from quant_platform.research import model_deep2
            from quant_platform.research.stock_forward import StockForwardTracker

            versions = [spec["version"] for spec in research_model.MODELS.values()
                        if spec["label"] in ("rank", "excess", "ensemble")
                        and research_model.model_dir(Path(args.base), spec["version"]).is_dir()]
            # 2026-10-21 (使用者 2026-10-16：模型也要更新；稽核：深度學習只有逐年模型): the deep models the forward
            # accounts read, refitted to the cutoff too (monthly-refit versions refit themselves)
            tracker = StockForwardTracker(RESEARCH)
            read = set().union(*[tracker.uses(item) for item in tracker.tracked() if not item.get("stopped")] or [set()])
            deep = sorted({spec["version"] for factor, spec in research_model.MODELS.items()
                           if spec["label"] == "deep2" and factor in read and spec["version"] not in model_deep2.MONTHLY
                           and research_model.model_dir(Path(args.base), spec["version"]).is_dir()})
            with JobLog(RESEARCH).start(f"每月滾動：用 {cutoff} 以前的資料重訓 {len(versions) + len(deep)} 個模型", command,
                                        total=len(versions) + len(deep)) as job:
                data, fp = daily_research.load(Path(args.base))
                trained = []
                for number, version in enumerate(versions):
                    job.update(done=number, current=version, force=True)
                    spec = research_model.VERSIONS[version]
                    trained.append(research_model.train_until(fp, research_model.model_dir(Path(args.base), version), cutoff,
                                                              spec["label"], version, spec.get("horizon", research_model.HORIZON)))
                for number, version in enumerate(deep, start=len(versions)):
                    job.update(done=number, current=version, force=True)
                    trained.append(model_deep2.train_until(fp, research_model.model_dir(Path(args.base), version), version,
                                                           cutoff, job))
                risky = bool(read & {"dl1", "dl1x", "dl_risk"})          # the risk overlay and weights
                if risky:
                    from quant_platform.research import model_risk

                    trained.append(model_risk.train_until(
                        fp, research_model.model_dir(Path(args.base), research_model.MODELS["dl_risk"]["version"]), cutoff,
                        job))
                payload["models"] = trained
                rolling.save(RESEARCH, cutoff, payload)
                from quant_platform.research import rl_timing

                robots = {"rl_blocks": rl_timing.BLOCK_VERSION, "rl_rolling": rl_timing.ROLLING_VERSION,
                          "rolling_equal": rl_timing.ROLLING_VERSION, "rl_rolling32": rl_timing.ROLLING32_VERSION,
                          "rolling32_equal": rl_timing.ROLLING32_VERSION, "rl_rolling33": rl_timing.ROLLING33_VERSION,
                          "rolling33_equal": rl_timing.ROLLING33_VERSION, "rl_rolling34": rl_timing.ROLLING34_VERSION,
                          "rolling34_equal": rl_timing.ROLLING34_VERSION}
                benchmark = None
                for version in sorted({robots[factor] for factor in read if factor in robots}):
                    folder = research_model.model_dir(Path(args.base), version)
                    if not (folder / "meta.json").is_file():
                        continue
                    benchmark = benchmark if benchmark is not None else rl_timing.benchmark_units(data, fp.sessions)
                    job.update(current=f"{version}：重訓到 {cutoff}", force=True)
                    trained.append(rl_timing.refit_until(fp, benchmark, folder, version, cutoff, job))
                if "meta1" in read:                                    # the meta-label veto (it reads the deep model)
                    from quant_platform.research import meta_label

                    trained.append(meta_label.train_until(fp, research_model.model_dir(Path(args.base), meta_label.VERSION),
                                                          cutoff))
                retrained = ({factor for factor, spec in research_model.MODELS.items()
                              if spec["version"] in set(versions) | set(deep)}
                             | ({"dl1", "dl1x", "dl_risk"} if risky else set()) | ({"meta1"} & read)
                             | {factor for factor in read if factor in robots})
                again = tracker.refit(retrained, f"模型重訓到 {cutoff}（每月滾動），從起算日重新記錄") if args.refit_accounts else []
                job.payload["summary"] = (f"{len(trained)} 個模型重訓到 {cutoff}（深度學習 {len(deep)} 個）"
                                          + (f"；{len(again)} 個前向帳戶重新記錄" if again else ""))
            print(job.payload["summary"])
            return 0
        with JobLog(RESEARCH).start(f"每月滾動：{cutoff} 之後的前向測試（過門檻的策略各用新的 30 萬帳戶）", command) as job:
            from quant_platform.research.stock_forward import StockForwardTracker

            # 2026-10-21: the forward accounts already ran these rules to the day at 15:30 — read, don't run again
            refreshed = rolling.refresh(RESEARCH, lambda: daily_research.load(Path(args.base)), broker_costs(args.broker),
                                        cutoff, records=StockForwardTracker(RESEARCH).records())
            usable = [row for row in (refreshed or {}).get("holdout") or [] if row["usable"]]
            job.payload["summary"] = (f"{len((refreshed or {}).get('holdout') or [])} 個策略前向測試、{len(usable)} 個贏 0050"
                                      f"（重算 {refreshed.get('holdout_run', 0)} 個，其餘讀前向帳戶紀錄）"
                                      if refreshed else "這個月還沒挑策略")
        print(job.payload["summary"])
        return 0

    if args.command == "limits-check":
        # 2026-10-21 (使用者 2026-10-14：確實照當時的台股規則): research/limits_check.py
        from quant_platform.research import limits_check

        path = limits_check.run(Path(args.base), RESEARCH)
        report = json.loads(path.read_text(encoding="utf-8"))
        print(f"比對 {report['compared']:,} 個（股票×交易日）：漲跌停價不同 {report['limits_differ']:,} 個、"
              f"漲跌停鎖死判斷不同 {report['locks_differ']:,} 個；各年 {report['limits_differ_by_year']}；鎖死組合 {report['locks']}")
        print(f"已寫入 {path}")
        return 0

    if args.command == "intraday":
        # 2026-10-21 (使用者 2026-10-16：盤中買會不會比較有效; FinMind 分 K): research/intraday.py
        from quant_platform.research import intraday
        from quant_platform.research.history.finmind import env_token
        from quant_platform.research.jobs import JobLog

        with JobLog(RESEARCH).start("盤中成交時機（前 10 名策略 2021 起的買賣決定抽 4,000 筆，隔天的分 K）",
                                    "python -m quant_platform.research " + " ".join(sys.argv[1:])) as job:
            path = intraday.run(Path(args.base), RESEARCH, env_token(Path.cwd()), job)
            report = json.loads(path.read_text(encoding="utf-8"))
            job.payload["summary"] = f"{report['sampled']} 筆、有分 K 的 {report['with_bars']} 筆"
        for point, label in report["points"].items():
            item = report["gains"][point]
            parts = "；".join(f"{side} {(item.get(side) or {}).get('mean', 0):+.3%}（t={(item.get(side) or {}).get('t')}）"
                             for side in ("buy", "sell", "both"))
            print(f"{label}：{parts}；{'值得做' if item['worth'] else '不值得'}")
        print(f"已寫入 {path}")
        return 0

    if args.command == "window-weight":
        # 2026-10-21 (使用者：2015 到現在其實也很久了，評估長資料跟短歷史的權重): research/window_weight.py
        from quant_platform.research import window_weight

        path = window_weight.run(RESEARCH, RESEARCH / "diagnostics")
        report = json.loads(path.read_text(encoding="utf-8"))
        print(f"{report['rules']} 個規則、{report['months']}；每種方法的平均排序相關（之後 {report['horizon']} 個月）：")
        for method, item in report["summary"].items():
            print(f"  {method:12s} IC {item['ic']:+.4f}（t={item['t']}）長歷史時點 {item['ic_long_history']}  勝過全部 "
                  f"{item['better_than_all']:.0%}  前 10% 之後 12 個月 {item['top_outcome']:+.2%}、贏 0050 {item['top_beat']:.0%}")
        print(report["verdict_main"])
        print(report["verdict_short"])
        print(f"已寫入 {path}")
        return 0

    if args.command == "worst-rl":
        # 2026-10-16 (使用者：訓練一個賠最多的模型): the robot 3.0's learning, the reward turned around
        from quant_platform.research import daily as daily_research
        from quant_platform.research import rl_timing
        from quant_platform.research.jobs import JobLog
        from quant_platform.research.model import models_root

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start("反向 RL 機器人：學著賠最多（8 個區塊，逐年滾動）", command) as job:
            data, fp = daily_research.load(Path(args.base))
            meta = rl_timing.walk_forward_blocks(fp, rl_timing.benchmark_units(data, fp.sessions),
                                                 models_root(Path(args.base)) / rl_timing.WORST_VERSION, job=job, worst=True)
            robot = sum(item["robot_excess"] for item in meta["yearly"].values())
            alike = sum(item["equal_excess"] for item in meta["yearly"].values())
            job.payload["summary"] = f"{len(meta['yearly'])} 年樣本外：反向機器人比 0050 {robot:+.3f}、平均 {alike:+.3f}"
        print(job.payload["summary"])
        return 0

    if args.command == "pipeline-wait":      # 2026-10-21: a queued research pipeline shows on 執行中的程式
        from quant_platform.research.jobs import JobLog

        if args.finish:
            JobLog(RESEARCH).end_wait(args.finish)
            return 0
        print(JobLog(RESEARCH).waiting(args.pipeline or "?", args.wait_reason or "", int(args.pid or 0)), flush=True)
        return 0

    if args.command == "llm-trader":
        # 2026-10-20 (使用者：LLM 有沒有辦法做成交易 agent): the day's decision of the LLM trader and its twin
        from quant_platform.config.settings import Settings
        from quant_platform.research import llm_trader
        from quant_platform.research.jobs import JobLog

        day = date.fromisoformat(args.decision_day) if args.decision_day else datetime.now(TAIPEI).date()
        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        if args.replay_from:          # 2026-10-21 (使用者：不要跟我說前向觀察只能做一天): the days before it existed
            first = date.fromisoformat(args.replay_from)
            with JobLog(RESEARCH).start(f"LLM 交易員：補做 {first}～{day} 每天收盤後的決定（只用當天以前的資料）",
                                        command) as job:
                client = llm_trader.build_client(Settings.from_env(), RESEARCH)
                made = llm_trader.replay(Path(args.base), RESEARCH, first, day, client, job)
                fallback = sum(1 for record in made if record["llm"].get("fallback"))
                job.payload["summary"] = f"補做 {len(made)} 天" + (f"（{fallback} 天沿用前一天）" if fallback else "")
            print(job.payload["summary"], flush=True)
            return 0
        with JobLog(RESEARCH).start(f"LLM 交易員：{day} 收盤後的決定", command) as job:
            client = llm_trader.build_client(Settings.from_env(), RESEARCH)
            for attempt in range(7):                # the day's prices may still be on their way (15:15 refresh)
                if attempt:
                    job.update(current=f"還沒有 {day} 的收盤資料，5 分鐘後再試（第 {attempt} 次）", force=True)
                    time.sleep(300)
                record = llm_trader.run_day(Path(args.base), RESEARCH, day, client)
                if record is not None or any(item["date"] == day.isoformat() for item in llm_trader.decisions(RESEARCH)):
                    break
            job.payload["summary"] = ("已經決定過或不是交易日" if record is None else
                                      f"LLM 持有 {len(record['llm']['hold'])} 檔"
                                      + (f"（{record['llm']['fallback']}）" if record['llm'].get('fallback') else "")
                                      + f"；對照組 {len(record['control']['hold'])} 檔")
        print(job.payload["summary"], flush=True)
        return 0

    if args.command == "scan-large":
        # 2026-10-20 (使用者：沒看到 1 億個策略不要說有在研究): the streamed search, every holding style a dimension
        from quant_platform.research import scan_large
        from quant_platform.research.jobs import JobLog

        sizes = list(range(args.signals_min, args.signals_max + 1))
        holds = tuple((args.holds or ",".join(scan_large.HOLDS)).split(","))
        universes = tuple((args.universes or ",".join(scan_large.UNIVERSES)).split(","))
        only = (scan_large.frequent(Path(args.signals_from), args.signals_top)
                if args.signals_top and args.signals_from else None)
        folder = Path(args.resume) if args.resume else None
        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        settings = len(scan_large.keys(holds, universes)) // len(holds)
        with JobLog(RESEARCH).start(f"大規模搜尋（串流）：{args.signals_min}～{args.signals_max} 個訊號一組"
                                    + (f"（最常過篩的 {len(only)} 個訊號）" if only else "")
                                    + f" × {len(holds)} 種持有方式 × {settings} 種設定", command) as job:
            summary = scan_large.run(Path(args.base), RESEARCH, broker_costs(args.broker), sizes, holds,
                                     count=args.finalists, job=job, folder=folder, universes=universes, only=only)
            job.payload["summary"] = (f"{summary['candidates']:,} 個策略、{summary['passed_screen']:,} 個過第一階段、"
                                      f"{summary['finalists']} 個進引擎：{summary['tiers']}")
        print(json.dumps({key: value for key, value in summary.items() if key != "results"}, ensure_ascii=False), flush=True)
        return 0

    if args.command == "meta-label":
        # 2026-10-20: the meta-label model on the deep model's picks (research/meta_label.py) and its registered gate
        from quant_platform.research import daily as daily_research
        from quant_platform.research import meta_label
        from quant_platform.research.jobs import JobLog
        from quant_platform.research.model import models_root

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start("後設標籤模型：深度學習前 40 名，預測買進後 40 個交易日贏不贏 0050（逐年滾動）",
                                    command) as job:
            _data, fp = daily_research.load(Path(args.base))
            meta = meta_label.train(fp, models_root(Path(args.base)) / meta_label.VERSION, job=job)
            check = meta["gate"]
            job.payload["summary"] = (f"2016～2020：留下 {check.get('kept')}、否決 {check.get('vetoed')}；留下的平均贏 0050 "
                                      f"{check.get('excess_kept')}、否決的 {check.get('excess_vetoed')}；勝率留下 "
                                      f"{check.get('win_kept')}、全部 {check.get('win_all')}；門檻{'通過' if check['passed'] else '沒過'}")
        print(json.dumps(check, ensure_ascii=False), flush=True)
        for year, item in meta["years"].items():
            print(year, json.dumps({key: value for key, value in item.items() if not key.startswith("sum_")},
                                   ensure_ascii=False), flush=True)
        return 0

    if args.command in ("block-rl31", "block-rl32", "block-rl33", "block-rl34"):
        # 2026-10-16 (the audit): the robot 3.1 — each test year's slot factors chosen before the year (rl_timing.py);
        # 2026-10-20: 3.2, nine slots with today's models and a defensive one
        from quant_platform.research import daily as daily_research
        from quant_platform.research import rl_timing
        from quant_platform.research.jobs import JobLog
        from quant_platform.research.model import models_root

        version = {"block-rl32": rl_timing.ROLLING32_VERSION, "block-rl33": rl_timing.ROLLING33_VERSION,
                   "block-rl34": rl_timing.ROLLING34_VERSION}.get(
            args.command, rl_timing.ROLLING_VERSION)
        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        title = {"block-rl32": "RL 機器人 3.2：逐年滾動訓練（9 個槽位，含深度學習與跌市穩）",
                 "block-rl33": "RL 機器人 3.3：逐年滾動訓練（同 3.2，沒有紀錄的年份用平均）",
                 "block-rl34": "RL 機器人 3.4：逐年滾動訓練（只分配進攻、趨勢因子、防守三個角色）"}.get(
            args.command, "RL 機器人 3.1：逐年滾動訓練（每年只用之前的資料挑 8 個槽位的因子）")
        with JobLog(RESEARCH).start(title, command) as job:
            data, fp = daily_research.load(Path(args.base))
            meta = rl_timing.walk_forward_rolling(fp, rl_timing.benchmark_units(data, fp.sessions),
                                                  models_root(Path(args.base)) / version, job=job, version=version)
            robot = sum(item["robot_excess"] for item in meta["yearly"].values())
            alike = sum(item["equal_excess"] for item in meta["yearly"].values())
            abstained = sum(1 for item in meta["yearly"].values() if item["abstained"])
            job.payload["summary"] = (f"{len(meta['yearly'])} 年樣本外：機器人比 0050 {robot:+.3f}、平均 {alike:+.3f}"
                                      f"（對數報酬加總）；{abstained} 年驗證沒贏平均、不押")
        print(job.payload["summary"])
        for year, item in meta["yearly"].items():
            print(year, {key: value for key, value in item.items() if not key.startswith("curve")})
        return 0

    if args.command in ("free-rl4", "free-rl41"):
        # 2026-10-16: the robot 4.0 — a free agent, every session's weights (research/rl_free.py)
        from quant_platform.research import daily as daily_research
        from quant_platform.research import rl_free
        from quant_platform.research.jobs import JobLog
        from quant_platform.research.model import models_root
        from quant_platform.research.rl_timing import benchmark_units

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        version = rl_free.VERSION_41 if args.command == "free-rl41" else rl_free.VERSION
        with JobLog(RESEARCH).start(f"RL 機器人 {version}：每天自己決定每檔比重與現金（逐年滾動訓練）", command) as job:
            job.update(current="載入資料、整理每天每檔的因子", force=True)
            data, fp = daily_research.load(Path(args.base))
            market = rl_free.Market(fp, benchmark_units(data, fp.sessions), broker_costs(args.broker))
            meta = rl_free.walk_forward(market, models_root(Path(args.base)) / version, job=job, version=version)
            robot = sum(item["robot_excess"] for item in meta["yearly"].values())
            job.payload["summary"] = f"{len(meta['yearly'])} 年樣本外（訓練環境）：比 0050 {robot:+.3f}（對數報酬加總）"
        print(job.payload["summary"])
        for year, item in meta["yearly"].items():
            print(year, {key: value for key, value in item.items() if not key.startswith("curve")})
        return 0

    if args.command == "trade-audit":
        # 2026-10-13 (使用者：確實檢查所有交易都照台股的交易規則和機制): every order of the tracked rules checked
        from quant_platform.research import trade_audit
        from quant_platform.research.jobs import JobLog

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start("交易規則稽核：前向追蹤的每個規則逐筆檢查", command) as job:
            report = trade_audit.run(Path(args.base), RESEARCH, broker_costs(args.broker), job=job)
            counts = report["violation_counts"]
            job.payload["summary"] = (f"{report['orders']:,} 筆買賣，沒有違規" if not counts else
                                      f"{report['orders']:,} 筆買賣；違規：" + "、".join(
                                          f"{trade_audit.CHECKS[key]} {value}" for key, value in counts.items()))
        print(job.payload["summary"])
        print(report["path"])
        return 0 if not report["violation_counts"] else 1

    if args.command == "leakcheck":
        # 2026-10-11: every factor recomputed from data cut at random sessions (research/leakcheck.py)
        from quant_platform.research import daily as daily_research
        from quant_platform.research import leakcheck
        from quant_platform.research.jobs import JobLog

        data, fp = daily_research.load(Path(args.base))
        factors = [*daily_research.FACTOR_LABELS, *daily_research.MODEL_FACTORS, *daily_research.CONSENSUS_FACTORS]
        days = leakcheck.sample_days(fp.sessions, 5, daily_research.RECENT_START)
        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start(f"防偷看檢查：{len(days)} 個日期截斷重算 {len(factors)} 個因子", command,
                                    total=len(days)) as job:
            report = leakcheck.run(Path(args.base), RESEARCH, data, fp, factors, days, job=job)
            found = report["leaks"]
            job.payload["summary"] = "沒有發現偷看" if not found else "有差異：" + "、".join(found)
        for day, item in report["results"].items():
            bad = {factor: result["differ"] for factor, result in item.items() if result["differ"]}
            print(f"{day}：比對 {sum(result['checked'] for result in item.values()):,} 個值；有差異 {bad or '無'}", flush=True)
        return 0 if not report["leaks"] else 1

    if args.command == "scan-trials":
        # 2026-10-11: effective trials of the scans made before 1.1.0 (a replayed random sample of each)
        from quant_platform.research import scan
        from quant_platform.research.jobs import JobLog

        folders = [folder for folder in sorted((RESEARCH / "scans").glob("scan-*"))
                   if not any(folder.glob("screen-*.excess.npy")) and not (folder / "effective.json").is_file()]
        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start(f"估算搜尋的有效試驗數（{len(folders)} 輪，每輪抽 10,000 個重算）", command,
                                    total=len(folders)) as job:
            for index, folder in enumerate(folders):
                job.update(done=index, current=folder.name, force=True)
                found = scan.sample_effective(folder, broker_costs(args.broker), workers=args.workers)
                print(f"{folder.name}：{found}", flush=True)
            total = scan.all_effective(RESEARCH)
            job.payload["summary"] = (f"{total['scans']} 輪、第一階段 {total['screened']:,} 個、"
                                      f"超額相關 0.9 以上合併後至少 {total['groups_090']:,} 組")
        print(total)
        return 0

    if args.command == "diagnose":
        # 2026-10-11: crowding, size and overlap of the T0 candidates and the consensus list (research/diagnostics.py)
        from quant_platform.research import consensus, diagnostics
        from quant_platform.research.blend import parse_spec
        from quant_platform.research.jobs import JobLog

        trials = list(consensus.members())
        latest = {}
        for record in registry.records():
            if record.kind == "candidate" and record.period == "recent" and record.spec_name.startswith("T0 共識清單"):
                latest[record.spec_hash] = record
        for record in latest.values():
            spec = json.loads((RESEARCH / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
            trials.append((record.trial_id, record.spec_name, parse_spec(spec)))
        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        with JobLog(RESEARCH).start(f"T0 擁擠、規模與重疊診斷（{len(trials)} 個規則，不是新試驗）", command,
                                    total=len(trials)) as job:
            report = diagnostics.run(Path(args.base), RESEARCH, broker_costs(args.broker), trials, job=job)
            job.payload["summary"] = f"{len(report['rules'])} 個規則；平均持股重疊 {report['average_overlap']}"
        for row in report["rules"]:
            print(f"#{row['trial']} 市值百分位 {row['cap_percentile']}、最小兩成 {row['smallest_fifth_share']}、"
                  f"融資增加百分位 {row['margin_growth_percentile']}、台積電 {row['tsmc_share']}；超額 {row['excess']:+.1%} → "
                  f"排除最小兩成 {row['excess_without_smallest_fifth']:+.1%}（2020-10 起 "
                  f"{row['since_2020_without_smallest_fifth']:+.1%}）", flush=True)
        return 0

    if args.command == "rl-strategies":
        # 2026-10-10: RL 3.0, the account shared among the T0 candidates (research/rl_strategies.py)
        from quant_platform.research import daily as daily_research
        from quant_platform.research import rl_strategies
        from quant_platform.research.jobs import JobLog

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        specs = rl_strategies.t0_candidates(RESEARCH)
        years = list(range(rl_strategies.FIRST_YEAR, daily_research.RECENT_END.year + 1))
        with JobLog(RESEARCH).start(f"強化學習 3.0：在 {len(specs)} 個 T0 候選之間分配（{rl_strategies.VERSION}，"
                                    f"{years[0]} 起逐年）", command, total=len(years)) as job:
            job.update(current="載入行情、重算每個策略的帳戶", force=True)
            data, fp = daily_research.load(Path(args.base))
            strategies = rl_strategies.build(data, fp, broker_costs(args.broker), specs)
            report = rl_strategies.walk_forward(strategies, RESEARCH / "rl" / rl_strategies.VERSION, job=job)
            overall = report["overall"]
            job.payload["summary"] = (f"RL 年化 {overall['rl']['annual']:.1%}（回撤 {overall['rl']['max_drawdown']:.0%}）、"
                                      f"平均分配 {overall['equal']['annual']:.1%}（{overall['equal']['max_drawdown']:.0%}）、"
                                      f"0050 {overall['0050']['annual']:.1%}（{overall['0050']['max_drawdown']:.0%}）；"
                                      f"{'通過' if report['acceptance']['passed'] else '未通過'}")
        print(json.dumps({key: report[key] for key in ("overall", "clean", "acceptance", "average_shares")},
                         ensure_ascii=False, indent=1))
        return 0

    if args.command == "tech-rl":
        # 2026-10-10: train the technical-analysis RL robot (research/tech_rl.py), walk-forward by year
        from quant_platform.research import daily as daily_research
        from quant_platform.research import tech_rl
        from quant_platform.research.jobs import JobLog

        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        years = list(range(tech_rl.FIRST_YEAR, daily_research.RECENT_END.year + 1))
        with JobLog(RESEARCH).start(f"技術分析 RL 機器人（{args.tech_version}，逐年 {years[0]}～{years[-1]}、"
                                    f"{len(tech_rl.SEEDS)} 個種子）", command, total=len(years)) as job:
            job.update(current="載入行情與技術因子", force=True)
            data, fp = daily_research.load(Path(args.base))
            meta = tech_rl.train(fp, tech_rl.tech_dir(Path(args.base), args.tech_version),
                                 daily_research.fingerprint(Path(args.base)), years, job=job, version=args.tech_version)
            job.update(done=len(years), force=True)
            job.payload["summary"] = f"{len(meta['years'])} 個年度機器人"
        for year, item in meta["years"].items():
            print(f"{year}：訓練到 {item['trained_until']}、訓練報酬 {item['training_reward_first']} → "
                  f"{item['training_reward_last']}；樣本外 進場時每天超額 {item.get('excess_per_in_session')}、"
                  f"在場比例 {item.get('share_in')}、平均持有 {item.get('average_hold')} 天、進場 {item.get('entries')} 次",
                  flush=True)
        return 0

    if args.command == "scan":
        # 2026-10-10: the broad two-stage search (research/scan.py)
        from quant_platform.research import scan
        from quant_platform.research.jobs import JobLog

        if args.require == "ml_deep2_top":     # 2026-10-17: the second deep version, only past its registered gate
            from quant_platform.research.model_deep2 import gate as deep2_gate

            check = deep2_gate(RESEARCH / "models", "dl-2.1.0")
            print(f"dl-2.1.0 門檻：{check}", flush=True)
            if not check["passed"]:
                return 0
        if args.require in ("ml_gbm_ens", "ml_gbm_ext", "ml_deep"):   # only past the model's registered gate
            from quant_platform.research.model_ensemble import gate

            check = (gate(RESEARCH / "models") if args.require == "ml_gbm_ens"
                     else gate(RESEARCH / "models", "gbm-2.2.0", "gbm-2.0.0") if args.require == "ml_gbm_ext"
                     else gate(RESEARCH / "models", "dl-1.0.0", "gbm-2.0.0"))           # 2026-10-16: G, the deep model
            print(f"{check['version']} 門檻：{check}", flush=True)
            if not check["passed"]:
                return 0
        require = args.require
        if (args.require_new or args.require_time or args.require_short or args.require_lending
                or args.require_corporate or args.require_issuance or args.require_dealer
                or args.require_industry):                                                      # after the novelty gate
            from quant_platform.research import daily as daily_research

            family = (daily_research.TIME_FACTORS if args.require_time else
                      {**daily_research.SHORT_FACTORS, **daily_research.ALERT_FACTORS} if args.require_short
                      else daily_research.LENDING_FACTORS if args.require_lending
                      else daily_research.CORPORATE_FACTORS if args.require_corporate
                      else daily_research.ISSUANCE_FACTORS if args.require_issuance
                      else daily_research.DEALER_FACTORS if args.require_dealer
                      else daily_research.INDUSTRY_FACTORS if args.require_industry else daily_research.RAW_V2)
            _data, novelty_fp = daily_research.load(Path(args.base))
            gate = scan.novelty(novelty_fp, list(family))
            print(f"新穎性閘門：{gate}", flush=True)
            (RESEARCH / "scans").mkdir(parents=True, exist_ok=True)
            (RESEARCH / "scans" / ("novelty-time.json" if args.require_time else
                                   "novelty-short.json" if args.require_short else
                                   "novelty-lending.json" if args.require_lending else
                                   "novelty-corporate.json" if args.require_corporate else
                                   "novelty-issuance.json" if args.require_issuance else
                                   "novelty-dealer.json" if args.require_dealer else
                                   "novelty-industry.json" if args.require_industry else "novelty-raw-v2.json")).write_text(
                json.dumps(gate, ensure_ascii=False, indent=1), encoding="utf-8")
            require = [name for name, item in gate.items() if item["kept"]]
            del novelty_fp, _data
            if not require:
                return 0
        count = (sum(1 for path in Path(args.resume).glob("screen-*.jsonl") for _line in open(path, "rb"))
                 if args.resume else 0 if args.prefilter
                 else len(scan.candidates(None, args.signals_max, args.signals_min, require)))
        command = "python -m quant_platform.research " + " ".join(sys.argv[1:])
        title = (f"大規模策略搜尋：接著跑第二階段（第一階段 {count:,} 個已完成）" if args.resume else
                 f"大規模策略搜尋（GPU：先篩單一訊號，再 {args.signals_min}～{args.signals_max} 個一組，第一階段 2015-06～2020-09"
                 f"{'；最後名單要跌市不多跌' if args.calm else ''}）"
                 if args.prefilter else
                 f"大規模策略搜尋（{args.signals_min}～{args.signals_max} 個訊號一組，{count:,} 個候選，第一階段 2015-06～2020-09）")
        with JobLog(RESEARCH).start(title,
                                    command, total=count + args.finalists) as job:
            summary = scan.run_scan(Path(args.base), RESEARCH, broker_costs(args.broker), workers=args.workers,
                                    count=args.finalists, job=job, largest=args.signals_max,
                                    smallest=args.signals_min, require=require,
                                    resume=Path(args.resume) if args.resume else None, pool=args.pool,
                                    stable=not args.by_excess, prefilter=args.prefilter, calm=args.calm)
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
        if args.stop_ai_account:                # 2026-10-15: it held nothing but 0050 (META_STOPPED)
            from quant_platform.research.stock_forward import META_AI, META_STOPPED

            print("停止 AI 研究員（整體）帳戶：" + ("完成" if tracker.stop(META_AI["rule_hash"], day, META_STOPPED) else "沒有在追蹤"),
                  flush=True)
        if args.restart_from:                   # 2026-10-17: every live account from the common start again
            found = tracker.restart(date.fromisoformat(args.restart_from), args.reason or "使用者：前向從每月滾動起點起算")
            print(f"重新起算：{found}", flush=True)
        if args.rebase:                         # 2026-10-15: known causes, not reconciliation errors
            print(f"標成重算基準：{tracker.rebase(args.rebase)} 筆紀錄", flush=True)
        if args.explain:                        # 2026-10-19: the reasons for the decisions kept before reasons were
            found = tracker.explain_backfill()  # written down
            print(f"補上買賣原因：{found['accounts']} 個帳戶、{found['explained']}／{found['days']} 個有換股的決策日"
                  f"（{found['differed']} 天重算和當時不同，只寫進出）", flush=True)
            return 0
        if args.freeze:                         # 2026-10-15: the decisions behind every latest record, kept
            differences = tracker.freeze(day)
            print(f"寫下決定；重算和最新紀錄不同的帳戶 {len(differences)} 個", flush=True)
            for item in differences:
                print(f"  {item['date']} {item['name']}：紀錄 {len(item['recorded'])} 檔、重算 {len(item['replayed'])} 檔", flush=True)
            return 0
        if args.trend_from:                     # 2026-10-14: fixed-holding rules give way to their trend versions
            # 2026-10-20 (使用者：持有 20 天也是策略、月線上續抱也是策略，不是規則): the platform never converts a rule's
            # holding style
            raise SystemExit("不再提供：持有方式是策略的一部分，平台不替規則轉換（使用者 2026-10-20；轉換程式 10-21 已刪除）")
        written = tracker.record(day)
        print(f"{day}：寫入 {len(written)} 筆個股規則前向紀錄", flush=True)
        for row in tracker.summary():
            print(f"{row['name']}（{row['since']} 起）：{row['sessions']} 個交易日、投入 {row['contributed']:,.0f}、"
                  f"資產 {row['value']:,.0f}、0050 {row['benchmark_value']:,.0f}、持股 {len(row['holdings'])} 檔、"
                  f"費稅 {row['costs']:,}；對帳 {'正常' if not row['problems'] else '；'.join(row['problems'])}")
        # 2026-10-20: reconcile lists every account (an empty list when it is fine) and needs the rebase marks — the old
        # test returned 1 on every run
        return 0 if not any(reconcile(tracker.records(), tracker.rebases()).values()) else 1

    if args.command == "stats" and args.family == "daily":
        from quant_platform.research import daily as daily_research

        from quant_platform.research.scan import all_effective

        basis = daily_research.fingerprint(Path(args.base), args.universe)
        report = significance(registry, RESEARCH / "reports", daily_research.PERIOD, fingerprint=basis,
                              scans=all_effective(RESEARCH))
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
