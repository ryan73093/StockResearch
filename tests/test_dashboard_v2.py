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
    assert "rl-overlay-1.0.0" in body and "RL 調整部位" in body and "平均 60%" in body and "3 次" in body
