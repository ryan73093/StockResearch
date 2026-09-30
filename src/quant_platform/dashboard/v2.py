"""New single-user interface (S2-W03): 今日、持倉、計畫、研究、系統.

Each page answers one question and reuses existing services; the legacy
research pages stay reachable from 研究 until S8 retires them.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, jsonify, render_template

TAIPEI = ZoneInfo("Asia/Taipei")
WEEKDAYS = "一二三四五六日"
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
}
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
        "title": "決策與交易", "badge": "", "badge_class": "",
        "tools": (
            ("/ai-trading", "盤後 AI（舊版）", "完整盤後清單、歷史重播與驗證"),
            ("/decisions", "每日決策明細", "逐檔分數、門檻與風險"),
            ("/paper-trading", "模擬交易", "手動委託、成交與帳務"),
            ("/odd-lot", "零股定期投入研究", "固定投入與趨勢過濾比較"),
        ),
    },
    {
        "title": "市場與個股", "badge": "", "badge_class": "",
        "tools": (
            ("/market", "市場總覽（舊版）", "指標、K 線與跨資產熱圖"),
            ("/stocks", "單股研究", "K 線、特徵、預測與回測"),
            ("/universe", "股票池", "新增、停用與排程標的"),
        ),
    },
    {
        "title": "模型與策略", "badge": "", "badge_class": "",
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
        "title": "資料", "badge": "", "badge_class": "",
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
        "title": "暫停中的模組", "badge": "S8 決定去留", "badge_class": "badge--warn",
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


def create_v2_blueprint(dependencies) -> Blueprint:
    blueprint = Blueprint("v2", __name__)

    def names_for(symbols) -> dict[str, str]:
        wanted = set(symbols)
        return {
            asset.symbol: asset.company_abbreviation or ""
            for asset in dependencies.research_universe_service.list_all()
            if asset.symbol in wanted
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
        return render_template(
            "v2/today.html",
            active_nav="today",
            plan=plan,
            kind=kind,
            today={"label": label},
            deadline_iso=deadline,
            reasons=reasons,
            names=names_for(
                [order.symbol for order in plan.orders] + [item.symbol for item in plan.watchlist]
            ),
            status={
                "decision_time": _taipei_text(plan.decision_time),
                "market_date": f"{market_date:%m/%d}" if market_date else "尚無",
                "quality_label": quality["label"],
                "quality_badge": quality["badge"],
                "excluded": quality["excluded"],
            },
        )

    @blueprint.get("/holdings")
    def holdings():
        overview = dependencies.paper_trading_service.overview()
        return render_template(
            "v2/holdings.html",
            active_nav="holdings",
            overview=overview,
            names=names_for(item.position.symbol for item in overview.positions),
        )

    @blueprint.get("/plan")
    def plan():
        return render_template("v2/plan.html", active_nav="plan")

    @blueprint.get("/research")
    def research():
        return render_template("v2/research.html", active_nav="research", tool_groups=TOOL_GROUPS)

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
        access_on = dependencies.settings.auth_mode == "cloudflare-access"
        items.append({
            "label": "外網發布",
            "state": "Cloudflare Access" if access_on else "僅限本機",
            "badge": "badge--ok" if access_on else "",
            "detail": dependencies.settings.public_url or "stockresearch.pimi-sunsun.com（設定中）",
        })
        runs = []
        for run in dependencies.automation_service.overview().recent_runs[:12]:
            status_label, badge = STATUS_BADGES.get(run.status.value, (run.status.value, ""))
            runs.append({
                "started": _taipei_text(run.started_at),
                "job": JOB_LABELS.get(run.job_name, run.job_name),
                "market": run.market,
                "status": status_label,
                "badge": badge,
            })
        return render_template(
            "v2/system.html",
            active_nav="system",
            health_items=items,
            recent_runs=runs,
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
