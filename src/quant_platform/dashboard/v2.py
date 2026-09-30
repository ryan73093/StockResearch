"""New single-user interface (S2-W03): 今日、持倉、計畫、研究、系統.

Each page answers one question and reuses existing services; the legacy
research pages stay reachable from 研究 until S8 retires them.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from quant_platform.application.close_availability import SOURCES as CLOSE_SOURCES
from quant_platform.application.actual_account import FLOW_KINDS, ActualAccountError
from quant_platform.application.investment_plan import InvestmentPlanError, strategy_name
from quant_platform.research.spec import BASELINES
from quant_platform.application.close_availability import recent_table
from quant_platform.config.settings import PAUSABLE_MODULES
from quant_platform.container import _instance_dir
from quant_platform.research.forward import FORWARD_START, ForwardTracker
from quant_platform.research.reports import latest_reports, latest_stats, report_rows, trial_ranking

TAIPEI = ZoneInfo("Asia/Taipei")
WEEKDAYS = "一二三四五六日"
ASSET_VERSION = "2.3.0"
THEME_COOKIE = "sr_theme"
THEMES = ("dark", "light")
DOCS = {
    "roadmap": ("路線圖", "docs/development_roadmap.md"),
    "architecture": ("架構", "docs/system_architecture.md"),
    "requirements": ("需求", "REQUIREMENTS.md"),
    "handoff": ("交接", "docs/development_handoff.md"),
    "history": ("開發歷程", "DEVELOPMENT_HISTORY.md"),
}
JOB_LABELS = {
    "daily_market_data": "日線行情",
    "taiwan_research_data": "台股籌碼與基本面",
    "fred_macro_data": "總經資料",
    "feature_label_build": "特徵與標籤",
    "model_zoo_research": "模型研究",
    "regime_factor_research": "市場狀態與因子",
    "walk_forward_backtest": "樣本外回測",
    "strategy_ensemble": "策略整合",
    "portfolio_risk_research": "組合風險",
    "daily_decision_build": "每日決策",
    "tw_listing_reconciliation": "官方名冊比對",
    "tw_universe_expansion": "股票池擴充",
    "database_backup": "資料庫備份",
}
WEEKDAY_RULES = {
    "mon-fri": "週一至週五",
    "tue-sat": "週二至週六",
    "mon-sat": "週一至週六",
    "mon-sun": "每日",
    "*": "每日",
}
# Code-defined jobs (scheduler/runner.py); the daily workflows come from automation_schedules.
MAINTENANCE_JOBS = (
    (time(2, 30), "預測封存", "每日；舊實驗預測移到 Parquet"),
    (time(3, 0), "資料庫備份", "每日；保留最近 7 份"),
    (time(13, 30), "收盤資料時效實測", "交易日每分鐘到 14:45；記錄各來源公布時間"),
    (time(15, 15), "研究資料補抓", "交易日；長歷史資料只補當月、除權息只補今年"),
    (time(15, 30), "前向模擬紀錄", "交易日；追蹤中的策略當日狀態只追加不改寫"),
)
BACKGROUND_JOBS = "背景工作：每 15 分鐘檢查漏跑、每 10 分鐘補台股研究池資料、每小時檢查證交所休市日。"
STATUS_BADGES = {
    "succeeded": ("成功", "badge--ok"),
    "partial": ("部分成功", "badge--warn"),
    "failed": ("失敗", "badge--bad"),
    "running": ("執行中", "badge--accent"),
}
QUALITY_BADGES = {
    "pass": ("通過", "badge--ok"),
    "warning": ("有警告", "badge--warn"),
    "blocked": ("阻擋", "badge--bad"),
}
TOOL_GROUPS = (
    {
        "title": "決策與交易", "badge": "", "badge_class": "", "paused": False,
        "tools": (
            ("/ai-trading", "盤後 AI（舊版）", "完整盤後清單、歷史重播與驗證"),
            ("/decisions", "每日決策明細", "逐檔分數、門檻與風險"),
            ("/paper-trading", "模擬交易", "手動委託、成交與帳務"),
            ("/odd-lot", "零股定期投入研究", "固定投入與趨勢過濾比較"),
        ),
    },
    {
        "title": "市場與個股", "badge": "", "badge_class": "", "paused": False,
        "tools": (
            ("/market", "市場總覽（舊版）", "指標、K 線與跨資產熱圖"),
            ("/stocks", "單股研究", "K 線、特徵、預測與回測"),
            ("/universe", "股票池", "新增、停用與排程標的"),
        ),
    },
    {
        "title": "模型與策略", "badge": "", "badge_class": "", "paused": False,
        "tools": (
            ("/strategies", "策略中心", "策略、模型與版本"),
            ("/models", "模型研究", "樣本外績效與 AutoML"),
            ("/backtests", "回測", "Walk-forward 與交易明細"),
            ("/factors", "因子研究", "IC、衰退與分狀態表現"),
            ("/ensembles", "策略整合", "動態權重與績效"),
            ("/portfolios", "組合與風險", "配置方法與壓力測試"),
            ("/explain", "模型解釋", "特徵重要性與單股貢獻"),
        ),
    },
    {
        "title": "資料", "badge": "", "badge_class": "", "paused": False,
        "tools": (
            ("/data-quality", "資料品質", "閘門、缺漏與排除標的"),
            ("/data-pipeline", "資料建置進度", "補資料與特徵進度"),
            ("/taiwan-data", "台股研究資料", "法人、融資券、估值、財報"),
            ("/macro-data", "總經資料", "利率、通膨與殖利率"),
            ("/database", "資料庫檢視", "資料表筆數與範圍"),
            ("/automation", "排程設定", "時間、重試與通知"),
        ),
    },
    {
        "title": "暫停中的模組", "badge": "S8 決定去留", "badge_class": "badge--warn", "paused": True,
        "tools": (
            ("/rl-lab", "強化學習", "PPO／DQN 研究"),
            ("/shadow-trading", "影子交易", "代理假想委託"),
            ("/promotions", "晉級審查", "人工核准與券商沙盒"),
            ("/model-governance", "模型治理", "冠軍挑戰者與漂移"),
            ("/reports", "財經記憶與問答", "RAG 知識庫"),
            ("/news", "新聞情緒", "個股新聞與情緒"),
            ("/point-in-time-data", "盤中與衍生資料", "分鐘、期貨、選擇權"),
            ("/intraday-features", "盤中衍生特徵", "26 項特徵"),
            ("/corporate-events", "法說會事件", "重大訊息與簡報"),
            ("/google-trends", "Google 搜尋趨勢", "CSV 匯入"),
        ),
    },
)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _taipei_text(value: datetime | None, pattern: str = "%m/%d %H:%M") -> str:
    return _aware(value).astimezone(TAIPEI).strftime(pattern) if value else "尚無"


def _project_root() -> Path:
    # Services run with the project root as working directory (scripts/run_local_services.ps1).
    return Path.cwd()


def _tone(badge: str) -> str:
    return badge.removeprefix("badge--") if badge else "none"


def next_contribution_day(calendar, today, salary_day: int):
    """First session on or after this month's salary day; next month's once it has passed."""
    target = date(today.year, today.month, salary_day)
    session = target if calendar.is_trading_day(target) else calendar.next_trading_day(target)
    if session < today:
        year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        target = date(year, month, salary_day)
        session = target if calendar.is_trading_day(target) else calendar.next_trading_day(target)
    return session


def market_session(now: datetime, calendar) -> dict[str, str]:
    """Taiwan session phase shown in the navigation (after-hours odd lots: 13:40–14:30)."""
    local = now.astimezone(TAIPEI)
    day, clock = local.date(), local.time()
    if not calendar.is_trading_day(day):
        closure = calendar.closure(day)
        reason = closure.name if closure is not None and closure.source != "weekday" else "週末"
        return {
            "short": "休市",
            "label": f"休市（{reason}）",
            "detail": f"下一個交易日 {calendar.next_trading_day(day):%m/%d}",
            "tone": "closed",
        }
    if clock < time(9, 0):
        return {"short": "盤前", "label": "盤前", "detail": "09:00 開盤", "tone": "closed"}
    if clock < time(13, 30):
        return {"short": "盤中", "label": "盤中", "detail": "13:30 收盤後產生決策", "tone": "live"}
    if clock < time(13, 40):
        return {
            "short": "收盤", "label": "已收盤", "detail": "13:40 開始盤後零股委託", "tone": "soon",
        }
    if clock < time(14, 30):
        return {
            "short": "盤後零股", "label": "盤後零股進行中", "detail": "14:30 一次撮合", "tone": "live",
        }
    return {
        "short": "已收盤",
        "label": "今日已收盤",
        "detail": f"下一個交易日 {calendar.next_trading_day(day):%m/%d}",
        "tone": "closed",
    }


def create_v2_blueprint(dependencies) -> Blueprint:
    blueprint = Blueprint("v2", __name__)

    @blueprint.context_processor
    def layout_context() -> dict[str, object]:
        theme = request.cookies.get(THEME_COOKIE)
        return {
            "theme": theme if theme in THEMES else "dark",
            "asset_version": ASSET_VERSION,
            "taipei": TAIPEI,
            "market_session": market_session(
                datetime.now(TAIPEI), dependencies.market_calendar.calendar("TW")
            ),
        }

    def names_for(symbols) -> dict[str, str]:
        wanted = set(symbols)
        return {
            asset.symbol: asset.company_abbreviation or ""
            for asset in dependencies.research_universe_service.list_all()
            if asset.symbol in wanted
        }

    def backup_tile(now: datetime) -> dict[str, str]:
        service = dependencies.database_backup
        if service is None:
            return {"label": "資料庫備份", "state": "未啟用", "badge": "", "detail": "非 SQLite 檔案"}
        status = service.status(now)
        if status.latest is None:
            return {
                "label": "資料庫備份",
                "state": "尚無備份",
                "badge": "badge--warn",
                "detail": f"每日 03:00 自動備份，保留 {status.keep} 份",
            }
        latest = status.latest
        return {
            "label": "資料庫備份",
            "state": "逾期" if status.overdue else "正常",
            "badge": "badge--warn" if status.overdue else "badge--ok",
            "detail": (
                f"最近 {_taipei_text(latest.created)}・{latest.size_bytes / 2**30:.1f} GiB・"
                f"共 {status.count} 份（上限 {status.keep}）"
            ),
        }

    def research_tile() -> dict[str, str]:
        base = _instance_dir(dependencies.settings.database_url) / "research" / "history"
        manifest_path = base / "manifest.json"
        if not manifest_path.is_file():
            building = (base / "raw").is_dir()
            return {
                "label": "研究資料",
                "state": "建立中" if building else "尚未建立",
                "badge": "badge--accent" if building else "badge--warn",
                "detail": "官方長歷史日線下載中（背景執行）" if building else "執行 research.history fetch",
            }
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            series = manifest.get("series") or {}
            last = max((entry.get("quality") or {}).get("last") or "" for entry in series.values())
        except (OSError, ValueError):
            return {"label": "研究資料", "state": "讀取失敗", "badge": "badge--bad", "detail": "manifest.json"}
        return {
            "label": "研究資料",
            "state": "已建立",
            "badge": "badge--ok",
            "detail": f"{len(series)} 個序列・資料到 {last or '—'}",
        }

    def quality_status(market: str) -> dict[str, object]:
        view = dependencies.data_quality_service.latest_view(market)
        if view is None:
            return {"label": "尚無檢查", "badge": "", "excluded": 0, "computed": "尚無"}
        status = view.snapshot.status.value
        label, badge = QUALITY_BADGES.get(status, (status, ""))
        return {
            "label": label,
            "badge": badge,
            "excluded": sum(1 for item in view.issues if item.excludes_asset),
            "computed": _taipei_text(view.snapshot.computed_at),
            "blocking": view.snapshot.blocking_issue_count,
        }

    @blueprint.get("/")
    def today():
        now = datetime.now(TAIPEI)
        calendar = dependencies.market_calendar.calendar("TW")
        day = now.date()
        is_trading = calendar.is_trading_day(day)
        closure = calendar.closure(day)
        label = f"{day:%m/%d}（{WEEKDAYS[day.weekday()]}）"
        if not is_trading:
            reason = closure.name if closure is not None and closure.source != "weekday" else "週末"
            label += f" · 休市（{reason}），下一個交易日 {calendar.next_trading_day(day):%m/%d}"
        deadline = (
            datetime.combine(day, time(14, 30), TAIPEI).isoformat()
            if is_trading and now.time() < time(14, 30) else None
        )
        plan = dependencies.after_hours_ai_service.plan_for_page()
        if plan.orders and plan.submission_allowed:
            kind = "trade"
        elif not plan.orders and plan.headline.startswith(("今日不交易", "今日休市")):
            kind = "idle"
        else:
            kind = "hold"
        if plan.orders:
            # Order reasons are "訊號：…；部位：…；風險：…；回看：…"; the page keeps the
            # signal and the risk, the full text stays on the legacy after-hours page.
            reasons = [
                f"{order.symbol.split('.')[0]} "
                + "；".join(
                    part for part in order.reason.split("；")
                    if part.startswith(("訊號", "風險"))
                )
                for order in plan.orders[:3]
            ]
        else:
            reasons = [
                f"{blocker}（{count} 檔）"
                for blocker, count in Counter(
                    item.blocker for item in plan.watchlist if item.blocker
                ).most_common(3)
            ]
        market_date = dependencies.daily_market_data_pipeline.latest_market_date("TW")
        quality = quality_status("TW")
        investment_plan = dependencies.investment_plan_service.current()
        plan_card = None
        if investment_plan is not None:
            upcoming = next_contribution_day(calendar, day, investment_plan.salary_day)
            plan_card = {
                "version": investment_plan.version,
                "amount": investment_plan.monthly_amount,
                "salary_day": investment_plan.salary_day,
                "strategy": strategy_name(investment_plan),
                "next": upcoming,
                "is_today": upcoming == day,
                "drawdown": investment_plan.max_drawdown_tolerance,
            }
        decision = dependencies.plan_decision_service.decide(now)
        decision_tone = {
            "invest": "trade", "rebalance": "trade", "idle": "idle",
        }.get(decision.kind, "hold")
        return render_template(
            "v2/today.html",
            active_nav="today",
            plan=plan,
            plan_card=plan_card,
            decision=decision,
            decision_tone=decision_tone,
            kind=kind,
            today={"label": label},
            deadline_iso=deadline,
            reasons=reasons,
            totals={
                "amount": sum(order.estimated_amount for order in plan.orders),
                "cost": sum(order.estimated_cost for order in plan.orders),
            },
            names=names_for(
                [order.symbol for order in plan.orders] + [item.symbol for item in plan.watchlist]
                + [f"{order.symbol}.{suffix}" for order in decision.orders for suffix in ("TW", "TWO")]
            ),
            status={
                "decision_time": _taipei_text(plan.decision_time),
                "market_date": f"{market_date:%m/%d}" if market_date else "尚無",
                "quality_label": quality["label"],
                "quality_badge": quality["badge"],
                "excluded": quality["excluded"],
            },
        )

    def render_holdings(error: str | None = None, form: dict | None = None, status: int = 200):
        overview = dependencies.paper_trading_service.overview()
        actual = dependencies.actual_account_service.overview()
        symbols = [item.position.symbol for item in overview.positions]
        symbols += [f"{item.symbol}.{suffix}" for item in actual.holdings for suffix in ("TW", "TWO")]
        names = names_for(symbols)
        return render_template(
            "v2/holdings.html",
            active_nav="holdings",
            overview=overview,
            actual=actual,
            names=names,
            code_names={symbol.split(".")[0]: name for symbol, name in names.items()},
            flow_kinds=FLOW_KINDS,
            error=error,
            form=form or {},
            today_iso=datetime.now(TAIPEI).date().isoformat(),
        ), status

    @blueprint.get("/holdings")
    def holdings():
        return render_holdings()

    @blueprint.post("/holdings/cash")
    def holdings_cash():
        try:
            saved = dependencies.actual_account_service.record_cash_flow(dict(request.form))
        except ActualAccountError as exc:
            return render_holdings(str(exc), {**dict(request.form), "form": "cash"}, 400)
        flash(f"已記錄{FLOW_KINDS[saved.kind]} {saved.amount:,.0f} 元（{saved.day}）。", "success")
        return redirect(url_for("v2.holdings"))

    @blueprint.post("/holdings/trade")
    def holdings_trade():
        try:
            saved = dependencies.actual_account_service.record_trade(dict(request.form))
        except ActualAccountError as exc:
            return render_holdings(str(exc), {**dict(request.form), "form": "trade"}, 400)
        side = "買進" if saved.side == "BUY" else "賣出"
        flash(f"已記錄 {saved.day} {side} {saved.symbol} {saved.shares:,} 股（手續費 {saved.fee} 元、證交稅 {saved.tax} 元）。", "success")
        return redirect(url_for("v2.holdings"))

    @blueprint.post("/holdings/void")
    def holdings_void():
        try:
            done = dependencies.actual_account_service.void(
                str(request.form.get("kind", "")), int(request.form.get("id", "0")),
                str(request.form.get("reason", "")),
            )
        except (ActualAccountError, ValueError) as exc:
            return render_holdings(str(exc), {}, 400)
        flash("已作廢該筆紀錄。" if done else "找不到該筆紀錄或已作廢。", "success" if done else "error")
        return redirect(url_for("v2.holdings"))

    @blueprint.route("/plan", methods=["GET", "POST"])
    def plan():
        service = dependencies.investment_plan_service
        error = None
        form = dict(request.form) if request.method == "POST" else {}
        if request.method == "POST":
            try:
                saved = service.save(form)
            except InvestmentPlanError as exc:
                error = str(exc)
            else:
                flash(f"已儲存投資計畫第 {saved.version} 版。", "success")
                return redirect(url_for("v2.plan"))
        current = service.current()
        defaults = {
            "monthly_amount": f"{current.monthly_amount:.0f}" if current else "10000",
            "salary_day": str(current.salary_day) if current else "5",
            "strategy_key": current.strategy_key if current else "benchmark_dca",
            "max_drawdown_tolerance": f"{current.max_drawdown_tolerance * 100:g}" if current else "30",
            "horizon_years": str(current.horizon_years or "") if current else "",
            "goal": current.goal if current else "",
            "note": "",
        }
        reports_dir = _instance_dir(dependencies.settings.database_url) / "research" / "reports"
        research = {
            row["name"]: row for row in report_rows(latest_reports(reports_dir, limit=50))
            if row["period_label"] == "全期間"
        }
        return render_template(
            "v2/plan.html",
            active_nav="plan",
            current=current,
            current_strategy=strategy_name(current) if current else None,
            history=service.history(),
            strategies=[(key, spec.name, spec.description) for key, spec in BASELINES.items()],
            strategy_names={key: spec.name for key, spec in BASELINES.items()},
            values={**defaults, **form},
            error=error,
            research=research,
        ), (400 if error else 200)

    @blueprint.get("/research")
    def research():
        research_dir = _instance_dir(dependencies.settings.database_url) / "research"
        reports_dir = research_dir / "reports"
        ranking = trial_ranking(research_dir / "trials.jsonl", "development")
        stats = latest_stats(research_dir / "stats", "development")
        best_dsr = None
        if stats and stats.get("candidates"):
            best = max(stats["candidates"], key=lambda item: item["dsr"]["deflated_sharpe"] or 0)
            best_dsr = {"name": best["name"], "value": best["dsr"]["deflated_sharpe"], "trials": best["dsr"]["trials"]}
        forward_rows = ForwardTracker(research_dir).summary()
        return render_template(
            "v2/research.html",
            active_nav="research",
            tool_groups=TOOL_GROUPS,
            forward_rows=forward_rows,
            forward_start=FORWARD_START,
            ranking=ranking,
            stats=stats,
            best_dsr=best_dsr,
            baseline_rows=[
                row for row in report_rows(latest_reports(reports_dir, limit=200))
                if row["kind"] == "baseline"
            ],
        )

    @blueprint.get("/system")
    def system():
        health = dependencies.health_service.check()
        now = datetime.now(UTC)
        items = [{
            "label": "服務與資料庫",
            "state": "正常" if health.status == "healthy" else "異常",
            "badge": "badge--ok" if health.status == "healthy" else "badge--bad",
            "detail": f"資料庫 {health.database}",
        }]
        for market, title in (("TW", "台股行情"), ("US", "美股行情")):
            latest = dependencies.daily_market_data_pipeline.latest_market_date(market, now)
            fresh = dependencies.daily_market_data_pipeline.is_fresh(market, now)
            items.append({
                "label": title,
                "state": "最新" if fresh else "落後",
                "badge": "badge--ok" if fresh else "badge--warn",
                "detail": f"最新收盤 {latest:%Y-%m-%d}" if latest else "尚無資料",
            })
        quality = quality_status("TW")
        items.append({
            "label": "台股資料品質",
            "state": quality["label"],
            "badge": quality["badge"],
            "detail": f"檢查 {quality['computed']}；排除 {quality['excluded']} 檔",
        })
        calendar_status = dependencies.market_calendar.status(now.astimezone(TAIPEI).date())
        years = calendar_status["covered_years"]
        manual = len(calendar_status["manual_closures"])
        items.append({
            "label": "交易日曆",
            "state": "已收錄今年" if calendar_status["current_year_covered"] else "缺今年資料",
            "badge": "badge--ok" if calendar_status["current_year_covered"] else "badge--warn",
            "detail": f"{years[0]}–{years[-1]} 年；人工休市 {manual} 筆" if years else "尚無資料",
        })
        items.append(backup_tile(now))
        items.append(research_tile())
        access_on = dependencies.settings.auth_mode == "cloudflare-access"
        items.append({
            "label": "外網發布",
            "state": "Cloudflare Access" if access_on else "僅限本機",
            "badge": "badge--ok" if access_on else "",
            "detail": dependencies.settings.public_url or "stockresearch.pimi-sunsun.com（設定中）",
        })
        overview = dependencies.automation_service.overview()
        schedule = [
            {
                "at": time(item.hour, item.minute),
                "name": item.display_name,
                "rule": WEEKDAY_RULES.get(item.weekdays, item.weekdays)
                + ("；休市日略過" if item.market == "TW" else ""),
                "enabled": item.enabled,
            }
            for item in overview.schedules
        ] + [
            {"at": at, "name": name, "rule": rule, "enabled": True}
            for at, name, rule in MAINTENANCE_JOBS
        ]
        schedule.sort(key=lambda item: item["at"])
        runs = []
        for run in overview.recent_runs[:12]:
            status_label, badge = STATUS_BADGES.get(run.status.value, (run.status.value, ""))
            runs.append({
                "started": _taipei_text(run.started_at),
                "job": JOB_LABELS.get(run.job_name, run.job_name),
                "market": run.market,
                "status": status_label,
                "badge": badge,
            })
        for item in items:
            item["tone"] = _tone(item["badge"])
        return render_template(
            "v2/system.html",
            active_nav="system",
            health_items=items,
            recent_runs=runs,
            schedule=schedule,
            background_jobs=BACKGROUND_JOBS,
            paused_modules=[
                label for name, label in PAUSABLE_MODULES.items()
                if dependencies.settings.is_paused(name)
            ],
            close_sources=CLOSE_SOURCES,
            close_rows=recent_table(dependencies.close_availability),
            checked_at=_taipei_text(now),
            doc_tabs=[(key, label) for key, (label, _) in DOCS.items()],
        )

    @blueprint.get("/system/docs/<key>")
    def system_doc(key: str):
        if key not in DOCS:
            abort(404)
        relative = DOCS[key][1]
        path = _project_root() / relative
        if not path.is_file():
            abort(404)
        modified = datetime.fromtimestamp(path.stat().st_mtime, TAIPEI)
        return jsonify(
            path=relative,
            modified=f"{modified:%Y-%m-%d %H:%M}",
            markdown=path.read_text(encoding="utf-8"),
        )

    return blueprint
