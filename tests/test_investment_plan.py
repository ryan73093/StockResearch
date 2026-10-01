from datetime import UTC, datetime

import pytest

from quant_platform.application.investment_plan import InvestmentPlanError, parse_plan_form
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app

FORM = {
    "monthly_amount": "15,000",
    "salary_day": "10",
    "strategy_key": "ma_value",
    "max_drawdown_tolerance": "35",
    "horizon_years": "20",
    "goal": "退休金",
    "note": "第一版",
}


@pytest.fixture
def container(tmp_path):
    return build_container(Settings(database_url=f"sqlite:///{tmp_path / 'plan.db'}", scheduler_in_web=False))


def test_form_validation_messages():
    values = parse_plan_form(FORM)
    assert values["monthly_amount"] == 15_000 and values["max_drawdown_tolerance"] == 0.35
    for field, value in (
        ("monthly_amount", "abc"), ("monthly_amount", "500"), ("salary_day", "32"),
        ("strategy_key", "all_in_2330"), ("max_drawdown_tolerance", "95"), ("horizon_years", "100"),
    ):
        with pytest.raises(InvestmentPlanError):
            parse_plan_form({**FORM, field: value})


@pytest.mark.parametrize(
    ("field", "text", "expected_key", "expected"),
    [
        ("monthly_amount", "1萬", "monthly_amount", 10_000),
        ("monthly_amount", "1.5 萬", "monthly_amount", 15_000),
        ("monthly_amount", "NT$ 12,000 元", "monthly_amount", 12_000),
        ("monthly_amount", "１２０００", "monthly_amount", 12_000),
        ("salary_day", "31", "salary_day", 31),
        ("salary_day", "月底", "salary_day", 31),
        ("salary_day", "10號", "salary_day", 10),
        ("max_drawdown_tolerance", "30%", "max_drawdown_tolerance", 0.3),
        ("max_drawdown_tolerance", "27.5", "max_drawdown_tolerance", 0.275),
        ("horizon_years", "20年", "horizon_years", 20),
    ],
)
def test_form_accepts_what_people_type(field, text, expected_key, expected):
    assert parse_plan_form({**FORM, field: text})[expected_key] == expected


def test_salary_day_31_falls_back_to_short_month_ends():
    from datetime import date

    from quant_platform.dashboard.v2 import next_contribution_day
    from quant_platform.market_calendar import TradingCalendar

    calendar = TradingCalendar("TW", covered_years=[2026])
    # 2026-09-30 is the last day of September; February 2027 has 28 days.
    assert next_contribution_day(calendar, date(2026, 9, 2), 31) == date(2026, 9, 30)
    assert next_contribution_day(calendar, date(2027, 2, 1), 31) == date(2027, 3, 1)  # 02-28 is a Sunday


def test_every_save_is_a_new_version(container):
    service = container.investment_plan_service
    assert service.current() is None

    first = service.save(FORM, now=datetime(2026, 10, 1, tzinfo=UTC))
    second = service.save({**FORM, "monthly_amount": "20000", "note": "加薪"}, now=datetime(2026, 11, 1, tzinfo=UTC))

    assert (first.version, second.version) == (1, 2)
    assert service.current().monthly_amount == 20_000
    assert [item.version for item in service.history()] == [2, 1]
    assert service.history()[1].monthly_amount == 15_000  # the old version is kept


def test_next_contribution_day_skips_closures_and_rolls_to_next_month():
    from datetime import date

    from quant_platform.dashboard.v2 import next_contribution_day
    from quant_platform.market_calendar import MarketClosure, TradingCalendar

    calendar = TradingCalendar("TW", [MarketClosure(date(2026, 10, 9), "國慶日補假")], covered_years=[2026])

    assert next_contribution_day(calendar, date(2026, 10, 1), 9) == date(2026, 10, 12)   # holiday + weekend
    assert next_contribution_day(calendar, date(2026, 10, 12), 9) == date(2026, 10, 12)  # moved salary day is today
    assert next_contribution_day(calendar, date(2026, 10, 13), 5) == date(2026, 11, 5)


def test_today_page_shows_the_plan_card(container):
    client = create_app(container).test_client()
    assert "還沒有投資計畫" in client.get("/").get_data(as_text=True)

    container.investment_plan_service.save(FORM)
    body = client.get("/").get_data(as_text=True)
    assert "我的計畫" in body and "15,000 元" in body and "定期不定額（200 日均線）" in body
    assert "依你的計畫・第 1 版" in body and "未晉級・僅供參考" in body


def test_plan_keeps_the_broker_and_rejects_unknown_ones(container):
    assert parse_plan_form(FORM)["broker"] == "conservative"   # not chosen: conservative costs
    with pytest.raises(InvestmentPlanError, match="券商"):
        parse_plan_form({**FORM, "broker": "unknown"})

    saved = container.investment_plan_service.save({**FORM, "broker": "cathay"})
    assert saved.broker == "cathay" and container.investment_plan_service.current().broker == "cathay"
    assert container.actual_account_service.default_broker() == "cathay"

    body = create_app(container).test_client().get("/plan").get_data(as_text=True)
    assert "國泰證券" in body and "台新證券" in body and "不打折、每筆最低 1 元" in body
    assert "月退（不計入）" in body and "待確認" not in body


def test_existing_plan_and_ledger_tables_get_the_broker_column(tmp_path):
    import sqlite3

    from quant_platform.database.engine import Database
    from quant_platform.database.repositories import SqlAlchemyInvestmentPlanRepository

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE investment_plans (id INTEGER PRIMARY KEY AUTOINCREMENT, version INTEGER NOT NULL UNIQUE, "
            "created_at DATETIME NOT NULL, monthly_amount NUMERIC(20, 2) NOT NULL, salary_day INTEGER NOT NULL, "
            "strategy_key VARCHAR(80) NOT NULL, max_drawdown_tolerance FLOAT NOT NULL, goal VARCHAR(200) NOT NULL, "
            "horizon_years INTEGER, note VARCHAR(1000) NOT NULL)"
        )
        connection.execute(
            "INSERT INTO investment_plans VALUES (1, 1, '2026-10-01 00:00:00', 10000, 5, 'benchmark_dca', 0.3, '', NULL, '')"
        )
    database = Database(f"sqlite:///{path}")
    database.create_schema()

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(actual_trades)")}
    assert "broker" in columns
    assert SqlAlchemyInvestmentPlanRepository(database.session_factory).latest().broker == "conservative"


def test_today_action_card_shows_the_window_copy_text_and_attention(container):
    from datetime import date
    from types import SimpleNamespace

    from flask import render_template

    from quant_platform.application.plan_decision import PlanDecision, PlanOrder

    app = create_app(container)
    order = PlanOrder("0050", "BUY", 99, 100.2, 100.0, 9_919.8, 14, 0)
    reasons = ["限價＝收盤加 20 bps", "手續費以台新證券估算", "本月入金尚未記錄", "第四點依據"]
    status = {"decision_time": "10/05 13:46", "market_date": "2026-10-05", "quality_badge": "badge--warn",
              "quality_label": "有警告", "excluded": 2}
    common = dict(
        decision_tone="trade", deadline_iso="2026-10-05T14:30:00+08:00", opening_iso="2026-10-05T13:40:00+08:00",
        status=status, names={}, reasons=[], today={"label": "10/05（一）"},
        plan=SimpleNamespace(headline="", mode="", orders=[]),
        plan_card={"amount": 10_000, "version": 1, "salary_day": 5, "strategy": "定期定額基準", "drawdown": 0.3},
    )
    invest = PlanDecision("invest", "今天依計畫投入：買進 0050 99 股", 1, "定期定額基準", date(2026, 10, 5),
                          [order], reasons, 10_000, "10/05 收盤")
    with app.test_request_context("/"):
        body = render_template("v2/today.html", decision=invest, **common)
        missing = render_template("v2/today.html", **{**common, "decision": PlanDecision(
            "missing_data", "0050 今日收盤尚未取得，稍後重新整理", 1, "定期定額基準", date(2026, 10, 5))})

    assert 'data-opening="2026-10-05T13:40:00+08:00"' in body and "盤後零股 13:40～14:30" in body
    assert "0050 買 99 股 限價 100.20（盤後零股）" in body                     # the copy text
    assert "查看其他依據（1）" in body and "第四點依據" in body                # reasons beyond three are folded
    assert "需要留意" in body and "今日排除 2 檔" in body                       # quality warning
    assert "今日收盤資料尚未齊全" in missing


def test_changing_the_plan_shows_the_changes_before_saving(container):
    client = create_app(container).test_client()
    container.investment_plan_service.save(FORM)                        # version 1: 15,000, day 10, ma_value
    changed = {**FORM, "monthly_amount": "2萬", "salary_day": "5", "strategy_key": "benchmark_dca",
               "broker": "taishin", "note": "加薪"}

    preview = client.post("/plan", data={**changed, "action": "preview"})
    body = preview.get_data(as_text=True)

    assert preview.status_code == 200 and "確認變更" in body and "將存成第 2 版" in body
    assert "15,000 元</span> → <strong>20,000 元" in body
    assert "定期不定額（200 日均線）</span> → <strong>定期定額基準" in body
    assert "台新證券" in body and "每年投入 180,000 → 240,000 元" in body
    assert [plan.version for plan in container.investment_plan_service.history()] == [1]   # nothing saved yet

    saved = client.post("/plan", data={**changed, "action": "save"}, follow_redirects=True)
    assert "已儲存投資計畫第 2 版" in saved.get_data(as_text=True)
    current = container.investment_plan_service.current()
    assert (current.version, current.monthly_amount, current.broker) == (2, 20_000, "taishin")

    bad = client.post("/plan", data={**changed, "salary_day": "40", "action": "preview"})
    assert bad.status_code == 400 and "沒有儲存" in bad.get_data(as_text=True)


def test_plan_page_saves_and_shows_errors(container):
    client = create_app(container).test_client()
    assert "建立你的投資計畫" in client.get("/plan").get_data(as_text=True)

    bad = client.post("/plan", data={**FORM, "salary_day": "32"})
    assert bad.status_code == 400 and "沒有儲存" in bad.get_data(as_text=True)
    assert "薪資日請填 1～31" in bad.get_data(as_text=True)

    saved = client.post("/plan", data=FORM, follow_redirects=True)
    body = saved.get_data(as_text=True)
    assert saved.status_code == 200 and "已儲存投資計畫第 1 版" in body
    assert "定期不定額（200 日均線）" in body and "15,000" in body
