from datetime import date, datetime

import pytest

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.dashboard.v2 import TAIPEI, market_session
from quant_platform.market_calendar import MarketClosure, TradingCalendar


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}", scheduler_in_web=False)
    return create_app(build_container(settings)).test_client()


@pytest.mark.parametrize(
    ("path", "marker"),
    [
        ("/", "今日行動"),
        ("/holdings", "模擬帳戶"),
        ("/plan", "建立你的投資計畫"),
        ("/research", "研究現況"),
        ("/system", "暫停中的模組"),
        ("/system", "專案資訊"),
    ],
)
def test_new_pages_render_with_five_item_navigation(client, path, marker):
    response = client.get(path)
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert marker in body
    for label in ("今日", "持倉", "計畫", "研究", "系統"):
        # Sidebar (tablet and desktop) and bottom tab bar (phone) both carry the item.
        assert body.count(f"<span>{label}</span></a>") == 2
    assert body.count('aria-current="page"') == 2
    assert "本頁名詞解釋" not in body  # legacy chrome stays on legacy pages


def test_today_page_is_light(client):
    body = client.get("/").get_data(as_text=True)
    assert len(body.encode("utf-8")) < 60_000


def test_theme_defaults_to_dark_and_follows_the_cookie(client):
    assert 'data-theme="dark"' in client.get("/").get_data(as_text=True)

    client.set_cookie("sr_theme", "light")
    assert 'data-theme="light"' in client.get("/holdings").get_data(as_text=True)

    client.set_cookie("sr_theme", '"><script>')
    assert 'data-theme="dark"' in client.get("/system").get_data(as_text=True)


def test_system_page_reports_the_research_dataset(tmp_path):
    import json

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'sys.db'}", scheduler_in_web=False)
    client = create_app(build_container(settings)).test_client()
    assert "研究資料" in client.get("/system").get_data(as_text=True)
    assert "尚未建立" in client.get("/system").get_data(as_text=True)

    base = tmp_path / "research" / "history"
    (base / "raw").mkdir(parents=True)
    assert "建立中" in client.get("/system").get_data(as_text=True)

    (base / "manifest.json").write_text(json.dumps({"series": {
        "0050": {"quality": {"last": "2026-09-30"}}, "TAIEX": {"quality": {"last": "2026-09-30"}},
    }}), encoding="utf-8")
    assert "2 個序列・資料到 2026-09-30" in client.get("/system").get_data(as_text=True)


def test_project_docs_are_served_from_the_repository(client):
    response = client.get("/system/docs/roadmap")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["path"] == "docs/development_roadmap.md"
    assert "開發路線圖" in payload["markdown"]
    assert client.get("/system/docs/../../.env").status_code == 404
    assert client.get("/system/docs/unknown").status_code == 404


def test_the_sidebar_always_lists_the_project_documents(client):
    """2026-10-17 (使用者：要在 sidebar 看到專案資訊——路線圖、專案紀錄、開發歷程)."""
    for path in ("/", "/research", "/holdings"):
        body = client.get(path).get_data(as_text=True)
        for link in ("/project/roadmap", "/project/handoff", "/project/history", "/project/research_method"):
            assert f'href="{link}"' in body
    page = client.get("/project/history").get_data(as_text=True)
    assert 'data-doc-key="history"' in page and 'href="/project/history" aria-current="true"' in page
    assert 'data-doc-key="roadmap"' in client.get("/project").get_data(as_text=True)        # the roadmap first
    assert client.get("/project/unknown").status_code == 404
    assert client.get("/system/docs/dl_review").get_json()["path"] == "docs/research_dl_exits_review.md"
    # 2026-10-28 (使用者：專案資訊有把所有的交易事項需知、開發需知、規則寫清楚嗎): both on the list and in the sidebar;
    # a link between documents opens the other tab (the page gets each document's path)
    assert 'href="/project/trading"' in body and 'href="/project/agents"' in body
    assert client.get("/system/docs/trading").get_json()["path"] == "docs/trading_rules.md"
    assert "交易需知" in client.get("/system/docs/trading").get_json()["markdown"]
    assert client.get("/system/docs/agents").get_json()["path"] == "AGENTS.md"
    trading = client.get("/project/trading").get_data(as_text=True)
    assert 'data-doc-key="trading"' in trading and "REQUIREMENTS.md&#34;: &#34;requirements" in trading


def test_market_overview_is_new_and_the_legacy_one_moved(client):
    response = client.get("/market")
    assert response.status_code == 200 and "市場總覽" in response.get_data(as_text=True)
    assert "還沒有市場快照" in response.get_data(as_text=True)          # no snapshot in a fresh instance
    legacy = client.get("/market/legacy")
    assert legacy.status_code == 200 and 'href="/"' in legacy.get_data(as_text=True)


@pytest.mark.parametrize(
    ("clock", "short", "tone"),
    [
        ((8, 30), "盤前", "closed"),
        ((10, 0), "盤中", "live"),
        ((13, 35), "收盤", "soon"),
        ((13, 40), "盤後零股", "live"),
        ((14, 29), "盤後零股", "live"),
        ((14, 30), "已收盤", "closed"),
    ],
)
def test_market_session_phases_on_a_trading_day(clock, short, tone):
    calendar = TradingCalendar("TW", covered_years=[2026])
    now = datetime(2026, 9, 30, *clock, tzinfo=TAIPEI)  # Wednesday

    session = market_session(now, calendar)

    assert (session["short"], session["tone"]) == (short, tone)


def test_market_session_names_the_holiday_and_the_next_session():
    calendar = TradingCalendar(
        "TW", [MarketClosure(date(2026, 10, 9), "國慶日補假")], covered_years=[2026]
    )

    holiday = market_session(datetime(2026, 10, 9, 10, 0, tzinfo=TAIPEI), calendar)
    weekend = market_session(datetime(2026, 10, 10, 10, 0, tzinfo=TAIPEI), calendar)

    assert holiday["label"] == "休市（國慶日補假）"
    assert holiday["detail"] == "下一個交易日 10/12"
    assert weekend["label"] == "休市（週末）"


def test_today_order_card_shows_the_limit_and_the_most_it_can_cost(tmp_path):
    from quant_platform.application.plan_decision import PlanDecision, PlanOrder

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}", scheduler_in_web=False))
    order = PlanOrder("0050", "BUY", 98, 101.00, 100.0, 98 * 100.2, 20, 0, 100.2, 9_918)

    class Decide:
        def decide(self, now=None):
            return PlanDecision("invest", "今天依計畫投入：買進 0050 98 股", 1, "定期定額基準", date(2026, 10, 5),
                                orders=[order], reasons=["限價＝收盤加 1%"], budget=10_000, data_time="10/05 收盤")

    container.investment_plan_service.save({
        "monthly_amount": "10000", "salary_day": "5", "strategy_key": "benchmark_dca", "broker": "cathay",
        "max_drawdown_tolerance": "30", "horizon_years": "20", "goal": "退休金", "note": "",
    })
    container.plan_decision_service = Decide()
    body = create_app(container).test_client().get("/").get_data(as_text=True)

    assert "限價 101.00（盤後零股）" in body and "最多也只需 9,918 元" in body
    assert "shares=98&amp;price=100.20#trade" in body   # the report form starts from the expected fill


def test_today_reminds_when_the_account_falls_past_the_plans_tolerance(tmp_path):
    from types import SimpleNamespace

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}", scheduler_in_web=False))
    container.investment_plan_service.save({
        "monthly_amount": "10000", "salary_day": "5", "strategy_key": "benchmark_dca", "broker": "cathay",
        "max_drawdown_tolerance": "30", "horizon_years": "20", "goal": "退休金", "note": "",
    })
    books = SimpleNamespace(current_drawdown=-0.32, trades=[], flows=[], cash=0.0, holdings=[])
    container.actual_account_service.overview = lambda include_shadow=True, include_history=None: books
    body = create_app(container).test_client().get("/").get_data(as_text=True)

    assert "需要留意" in body and "帳戶從高點回落 32.0%，已超過計畫的可承受回撤 30%" in body


def test_today_without_a_plan_asks_for_one_instead_of_legacy_picks(client):
    body = client.get("/").get_data(as_text=True)
    assert "先建立投資計畫" in body and "研究模型觀察" not in body and "模擬權益" not in body


def test_legacy_tools_left_the_research_page_for_the_system_page(client):
    """S9-W05 (2026-10-06): the research tabs no longer list the legacy tools; the system page keeps them."""
    assert "舊版工具" not in client.get("/research").get_data(as_text=True)
    moved = client.get("/research?tab=tools")
    assert moved.status_code == 302 and moved.headers["Location"].endswith("/system#legacy-tools")
    system = client.get("/system").get_data(as_text=True)
    assert 'id="legacy-tools"' in system and "/market/legacy" in system and "/paper-trading" in system


def test_ml_tab_shows_the_models_and_the_rl_report(tmp_path):
    """2026-10-06: research › 機器學習與 RL."""
    import json

    research = tmp_path / "research"
    (research / "models" / "gbm-1.1.0").mkdir(parents=True)
    (research / "models" / "gbm-1.1.0" / "meta.json").write_text(json.dumps({
        "version": "gbm-1.1.0", "label": "excess", "features": ["a"] * 30,
        "years": {"2025": {"ic": 0.04, "top20_gain": 0.02}, "2026": {"ic": 0.06, "top20_gain": 0.04}}}), encoding="utf-8")
    (research / "rl" / "rl-overlay-1.0.0").mkdir(parents=True)
    item = {"growth": 0.5, "annual": 0.1, "max_drawdown": -0.2}
    (research / "rl" / "rl-overlay-1.0.0" / "report.json").write_text(json.dumps({
        "version": "rl-overlay-1.0.0", "base_rule": "規則", "seeds": [0, 1], "test_from": "2017-01-03", "test_to": "2026-10-05",
        "overall": {"rl": item, "rule": item, "half": item, "0050": item, "average_share": 0.6},
        "years": {"2017": {"rl": item, "rule": item, "half": item, "0050": item, "average_share": 0.5, "switches": 3}},
        "shares": {"2017-01-03": 1.0, "2017-01-04": 0.5}}), encoding="utf-8")
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    client = create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}",
                                                 scheduler_in_web=False))).test_client()
    body = client.get("/research?tab=ml").get_data(as_text=True)
    assert "gbm-1.1.0" in body and "+0.050" in body and "+3.00%" in body
    assert "rl-overlay-1.0.0" not in body                       # 2026-10-17: the RL work is on the RL page
    rl = client.get("/research/rl").get_data(as_text=True)
    assert "rl-overlay-1.0.0" in rl and "RL 調整部位" in rl and "平均 60%" in rl and "3 次" in rl and "早期版本" in rl


def test_research_overview_is_the_new_design_and_the_old_tabs_became_one_record(client):
    """2026-10-07: 總覽 shows the new design; ranking, promotion, AI researcher and legacy model are 舊設計紀錄."""
    body = client.get("/research").get_data(as_text=True)
    assert "目前最好的規則" in body and "最近完成的研究程式" in body
    assert "本週研究報告" not in body and "定期定額對照" not in body and "第一批研究結論" not in body
    for old, anchor in (("rules", "ranking"), ("promotion", "promotion"), ("agent", "agent")):
        moved = client.get(f"/research?tab={old}")
        assert moved.status_code == 302 and moved.headers["Location"].endswith(f"tab=legacy#{anchor}")
    record = client.get("/research?tab=legacy").get_data(as_text=True)
    assert "舊設計紀錄" in record and all(f'id="{anchor}"' in record for anchor in ("agent", "weekly", "promotion"))


def test_research_conclusions_come_from_the_method_document(tmp_path):
    from pathlib import Path

    from quant_platform.dashboard.v2 import research_conclusions

    doc = tmp_path / "method.md"
    doc.write_text("## 7. 結論\n\n### 目前結論（網站研究總覽顯示這一段）\n\n- **最好的規則**：A <b>\n- 用 `x` 跑\n\n"
                   "### 下一節\n\n- 不算這行\n", encoding="utf-8")
    assert research_conclusions(doc) == ["<strong>最好的規則</strong>：A &lt;b&gt;", "用 <code>x</code> 跑"]
    assert research_conclusions(tmp_path / "missing.md") == []
    assert research_conclusions(Path("docs/research_method.md"))        # the real document keeps the section


def test_market_overview_has_its_own_entry(client):
    body = client.get("/market").get_data(as_text=True)
    assert "<span>市場總覽</span>" in body and 'href="/market" aria-current="page"' in body
    assert 'href="/stock" aria-current="page"' in client.get("/stock").get_data(as_text=True)


def test_the_stylesheet_url_changes_with_its_content():
    import hashlib
    from pathlib import Path

    from quant_platform.dashboard import v2

    sheet = Path(v2.__file__).parent / "static" / "css" / "v2.css"
    assert v2.ASSET_VERSION == hashlib.sha256(sheet.read_bytes()).hexdigest()[:10]


def test_every_project_document_exists(client):
    """The system page's documents, including the AI-methods review (2026-10-09), are files in the project."""
    from pathlib import Path

    from quant_platform.dashboard.v2 import DOCS

    missing = [path for _label, path in DOCS.values() if not Path(path).is_file()]
    assert missing == []
    assert "樣本外" in client.get("/system/docs/ai_methods").get_json()["markdown"]
    assert "營收" in client.get("/system/docs/taiwan_quant").get_json()["markdown"]


def test_news_tab_categories_and_the_ai_researcher_card(client):
    """2026-10-09: the news event tab, the pool's categories and the AI researcher as a strategy."""
    news = client.get("/research?tab=news").get_data(as_text=True)
    assert "新聞事件" in news and "本月 LLM 總花費" in news and "新聞否決實驗" in news and "上限 5" in news
    pool = client.get("/research/pool").get_data(as_text=True)
    assert "AI 研究員（整體）" in pool and "每季第一個交易日" in pool


def test_the_forward_tab_shows_todays_after_close_orders(tmp_path):
    """2026-10-14 (使用者：要在 14:30 前算出建議): today's orders from research/forward/stocks/orders/<today>.json."""
    import json

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}", scheduler_in_web=False)
    client = create_app(build_container(settings)).test_client()
    assert "今天的單還沒算出來" in client.get("/research?tab=orders").get_data(as_text=True)
    today = datetime.now(TAIPEI).date().isoformat()
    folder = tmp_path / "research" / "forward" / "stocks" / "orders"
    folder.mkdir(parents=True)
    order = {"code": "2330", "name": "台積電", "close": 1000.0, "side": "BUY", "shares": 1234,
             "parts": [{"session": "盤後定價（14:00–14:30，以收盤價成交，整張）", "shares": 1000, "price": 1000.0},
                       {"session": "盤後零股（13:40–14:30 委託，14:30 撮合）", "shares": 234, "limit": 1010.0}]}
    (folder / f"{today}.json").write_text(json.dumps({
        "date": today, "common_start": "2026-10-01", "table_rows": 1081, "taiex": 48341.31,
        "started_at": f"{today}T13:52:00+08:00", "finished_at": None, "done": 1, "total": 2,
        "rules": [{"rule_hash": "x", "name": "測試策略", "rank": 1, "orders": [order], "holdings": [], "cash": 0}]},
        ensure_ascii=False), encoding="utf-8")
    body = client.get("/research?tab=orders").get_data(as_text=True)
    assert "今天的盤後單" in body and "測試策略" in body and "2330 台積電" in body
    assert "限價 1010.0" in body and "計算中（1／2" in body


def test_every_page_turns_its_cards_into_tabs(client):
    """2026-10-14 (使用者：不同區塊用 tab 分隔，不要一頁式長頁面): the content sits in one tab set; the script builds the
    bar from the top-level cards, and a table's header row stays in view."""
    for path in ("/", "/holdings", "/plan", "/research", "/research/pool", "/system"):
        body = client.get(path).get_data(as_text=True)
        assert 'class="tabset" data-tabset' in body and "tabset__bar" in body
    system = client.get("/system").get_data(as_text=True)
    assert 'data-tab="排程與執行" id="runs"' in system


def test_old_researcher_rounds_without_numbers_still_render(client, tmp_path):
    """2026-10-15: an old AI-researcher round whose accepted spec has no win_3y/median_3y (a missing key, not None)
    made the whole 舊設計紀錄 tab fail with 500."""
    import json

    research = tmp_path / "research"
    research.mkdir(parents=True, exist_ok=True)
    (research / "journal.jsonl").write_text(json.dumps(
        {"round_id": "20261008T2300", "status": "ok", "hypothesis": "測試假設", "accepted": [{"name": "沒有數值的設定"}], "rejected": []},
        ensure_ascii=False) + "\n", encoding="utf-8")
    response = client.get("/research?tab=legacy")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "沒有數值的設定：3 年勝率 —、中位 —" in body


def test_plan_market_and_stock_sections_are_tabs(client):
    """2026-10-15 (繼續優化): the plan's two columns, the market's side-by-side grids and the stock's factor groups were
    outside the tab set (always shown, so the page stayed long)."""
    plan = client.get("/plan").get_data(as_text=True)
    assert "grid--main-side" not in plan and 'id="plan-form" data-tab-default' in plan
    market = client.get("/market").get_data(as_text=True)
    assert market.count("data-tab=") in (0, 2)          # the grids are tabs whenever the snapshot exists


def test_the_rl_page_shows_each_robots_years(client, tmp_path):
    """2026-10-16 (使用者：我要怎麼看到學習的狀況？)."""
    import json

    folder = tmp_path / "research" / "models" / "block-rl-3.0.0"
    folder.mkdir(parents=True)
    (folder / "meta.json").write_text(json.dumps({
        "version": "block-rl-3.0.0", "factors": ["high_52w", "trend_200"], "trained_at": "2026-10-16T15:00:00+00:00",
        "weights": {"2019-01-02": [0.7, 0.3], "2020-01-02": [0.5, 0.5]},
        "yearly": {"2019": {"decisions": 12, "train_decisions": 120, "held_decisions": 30, "held_robot": 0.01,
                            "held_equal": 0.005, "abstained": False, "best_epochs": [40, 60], "curve_train": [0.0, 0.01, 0.02],
                            "curve_held": [0.0, 0.004, 0.002], "robot_excess": 0.12, "equal_excess": 0.05,
                            "heaviest": [["high_52w", 0.7]]},
                   "2020": {"decisions": 12, "train_decisions": 150, "held_decisions": 36, "held_robot": -0.01,
                            "held_equal": 0.0, "abstained": True, "best_epochs": [10, 10], "curve_train": [0.0, 0.01, 0.02],
                            "curve_held": [-0.01, -0.02, -0.03], "robot_excess": -0.02, "equal_excess": -0.02,
                            "heaviest": [["high_52w", 0.5]]}}}, ensure_ascii=False), encoding="utf-8")
    body = client.get("/research/rl").get_data(as_text=True)
    assert "RL 機器人 3.0" in body and "不押，用平均" in body and "照機器人" in body and "接近 52 週高點" in body
    assert "1<small> 年／2 年" in body and 'href="/research/rl"' in client.get("/research?tab=ml").get_data(as_text=True)


def test_the_home_page_and_the_roles_page_show_how_each_strategy_role_grew(client, monkeypatch):
    """2026-10-16 (使用者：每次優化之後我要在首頁看到每種角色成長了多少，不只是現在多少錢)."""
    from quant_platform.research import roles

    fake = {"latest_run": "2026-10-16T21:00:00+08:00", "benchmark": 9821414.0, "roles": [{
        "role": "機器學習", "note": "", "rules": 205, "grades": {"T0 候選": 15}, "change": 0.25,
        "now": {"trial": 3919, "spec": "abc", "name": "外資持股＋新高次數", "engine": "daily-1.5.0", "tier": "T0 候選",
                "final": 25910000.0, "bench_final": 9821414.0, "xirr": 0.381, "bench_xirr": 0.247, "excess_full": 9.69,
                "excess_recent": 2.76, "drawdown": -0.318, "bench_drawdown": -0.340, "calmar": 1.2, "win_1y": 0.68,
                "win_3y": 0.9, "worst_1y": -0.2, "orders": 30.5, "cost_share": 0.4, "turnover": 5.0, "start": "2015-06-01",
                "contributed": 1660000.0},
        "before": {"final": 20728000.0}, "first": {"day": "2026-10-09", "final": 8770000.0}, "growth_total": 1.95,
        "last_run": "2026-10-16T10:03:00+08:00", "history": [{"day": "2026-10-09", "final": 8770000.0},
                                                             {"day": "2026-10-16", "final": 25910000.0}]}]}
    monkeypatch.setattr(roles, "summary", lambda _research: fake)
    home = client.get("/").get_data(as_text=True)
    assert "策略角色成長" in home and "機器學習" in home and "+25%" in home and "2,591 萬" in home
    page = client.get("/research/roles").get_data(as_text=True)
    assert "策略角色" in page and "報酬／回撤" in page and "10-09：877" in page and "+195%" in page


def test_the_home_page_shows_this_months_rolling_forward_test(client, monkeypatch):
    """2026-10-16 (使用者：訓練到兩個月前，最近兩個月拿來做前向測試)."""
    from quant_platform.research import rolling

    payload = {"cutoff": "2026-08-31", "data_through": "2026-10-16", "models": [],
               "graded": [{"trial": 1, "name": "甲", "passed": True}, {"trial": 2, "name": "乙", "passed": False}],
               "holdout": [{"trial": 1, "rule_hash": "h", "name": "甲策略", "start": "2026-09-01", "end": "2026-10-16",
                            "sessions": 33, "value": 345000.0, "benchmark_value": 330000.0, "contributed": 320000.0,
                            "excess": 0.047, "return": 0.08, "benchmark_return": 0.03, "drawdown": -0.05, "trades": 40,
                            "usable": True}]}
    monkeypatch.setattr(rolling, "latest", lambda _research: payload)
    home = client.get("/").get_data(as_text=True)
    assert "本月可用策略" in home and "甲策略" in home and "2026-08-31" in home and "贏 0050" in home
    page = client.get("/research/roles").get_data(as_text=True)
    assert "本月可用策略" in page or "還沒有試驗紀錄" in page
    monkeypatch.setattr(rolling, "latest", lambda _research: None)
    monkeypatch.setattr(rolling, "pending", lambda _research: {"cutoff": "2026-08-31", "holdout_start": None,
                                                                "data_through": None, "graded": 0, "passed": 0,
                                                                "errors": 0, "usable": [], "holdout": [], "models": []})
    assert "這個月的挑選進行中" in client.get("/").get_data(as_text=True)


def test_the_sidebar_groups_research_and_always_lists_system(client):
    """2026-10-17 (使用者：分類到底有沒有好好分類、sidebar 的內容是否還有缺的)."""
    body = client.get("/").get_data(as_text=True)
    for caption in ("每天看", "策略", "方法與模型", "程式與紀錄"):
        assert f'<span class="sidegroup__caption">{caption}</span>' in body
    assert "機器學習模型" in body and "強化學習（RL）" in body and "機器學習與 RL" not in body
    for link in ("/system#runs", "/system#quality", "/system#timing", "/system#legacy-tools"):
        assert f'href="{link}"' in body                               # 系統's pages on every page, as 研究's
    assert body.count('href="/project"') >= 1 and 'href="/system#project"' not in body
    redirect = client.get("/roadmap")
    assert redirect.status_code == 302 and redirect.headers["Location"].endswith("/project/roadmap")


def test_the_research_overview_shows_the_robustness_check(tmp_path):
    """2026-10-18: the newest robustness check (research/robustness/<stamp>.json) as a tab of the overview."""
    import json

    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    folder = tmp_path / "research" / "robustness"
    folder.mkdir(parents=True)
    item = {"final_value": 30_000_000, "since_2020_final_value": 9_000_000, "benchmark_final_value": 9_820_000,
            "since_2020_benchmark_final_value": 3_770_000, "max_drawdown": -0.34, "tier": "T0 候選", "reason": ""}
    row = {"trial": 1, "rule_hash": "abc", "name": "測試規則", "base": item, "costs_doubled": {**item, "tier": "T1"},
           "next_session": None}
    (folder / "20261018-000000.json").write_text(json.dumps({"rows": [row], "built_at": "2026-10-18T02:14:08"}),
                                                 encoding="utf-8")
    client = create_app(build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))).test_client()
    body = client.get("/research").get_data(as_text=True)
    assert "穩健性檢查" in body and 'href="/research/rules/abc"' in body and "成本加倍後仍是 T0 候選：<strong>0／1</strong>" in body


def test_the_research_overview_shows_how_rules_fare_when_0050_falls(tmp_path, monkeypatch):
    """2026-10-18 (使用者：市場不好時不要比 0050 跌得多): the falling-market table on the overview."""
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard import v2
    from quant_platform.dashboard.app import create_app

    row = {"trial": 7, "rule_hash": "xyz", "name": "穩的規則", "tier": "T0 候選（高風險）", "final_value": 24_000_000,
           "max_drawdown": -0.40, "benchmark_max_drawdown": -0.34, "down_months": 40, "down_rule": -0.025,
           "down_base": -0.036, "down_worse_share": 0.39, "most_behind": -0.15,
           "worst_fall": {"gap": -0.028, "months": "2018-08～2018-12", "rule": -0.161, "base": -0.133}}
    monkeypatch.setattr(v2, "downside_table", lambda research: [row])
    client = create_app(build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))).test_client()
    body = client.get("/research").get_data(as_text=True)
    assert "跌市時的表現" in body and 'href="/research/rules/xyz"' in body and "多跌 2.8 點" in body and "badge--bad" in body


def test_a_rule_falling_further_than_0050_is_marked_on_the_pages(tmp_path, monkeypatch):
    """2026-10-18: the falling-market mark next to the rule's name (research/downside.notes)."""
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard import v2
    from quant_platform.dashboard.app import create_app

    note = {"gap": -0.089, "months": "2018-08～2018-12", "rule": -0.222, "base": -0.133}
    row = {"trial": 7, "rule_hash": "xyz", "name": "多跌的規則", "tier": "T0 候選", "final_value": 30_000_000,
           "max_drawdown": -0.34, "benchmark_max_drawdown": -0.34, "down_months": 40, "down_rule": -0.017,
           "down_base": -0.036, "down_worse_share": 0.39, "most_behind": -0.28, "worst_fall": note}
    monkeypatch.setattr(v2, "downside_table", lambda research: [row])
    monkeypatch.setattr(v2, "downside_notes", lambda research: {"xyz": note})
    client = create_app(build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))).test_client()
    body = client.get("/research").get_data(as_text=True)
    assert "高風險・跌市多跌 8.9 點" in body


def test_the_optimization_log_is_read_from_the_research_method(tmp_path):
    """2026-10-20 (使用者：持續優化每一個策略，要看得到結果)."""
    from quant_platform.dashboard.v2 import research_optimizations

    doc = tmp_path / "method.md"
    doc.write_text("### 目前結論\n- x\n\n### 最佳化紀錄（網站研究總覽）\n\n說明。\n\n| 日期 | 家族 | 做了什麼 | 結果 | 判定 |\n"
                   "|---|---|---|---|---|\n| 2026-10-20 | 深度學習 | 前 10 名 | 2,650 萬 | 沒過 |\n"
                   "| 2026-10-19 | 新資料 | 內部人 | 反向 | 沒過 |\n\n### 下一節\n| a | b | c | d | e |\n", encoding="utf-8")
    rows = research_optimizations(doc)
    assert [row["what"] for row in rows] == ["前 10 名", "內部人"] and rows[0]["verdict"] == "沒過"
    assert research_optimizations(tmp_path / "missing.md") == []
