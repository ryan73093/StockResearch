import json

import pytest

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app


@pytest.fixture
def setup(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False))
    return container, create_app(container).test_client()


def test_guide_page_embeds_the_user_guide_and_every_page_links_to_it(setup):
    _container, client = setup

    page = client.get("/help")
    body = page.get_data(as_text=True)

    assert page.status_code == 200 and "<h1>使用教學</h1>" in body
    markdown = json.loads(body.split('id="guide-markdown">', 1)[1].split("</script>", 1)[0])
    for section in ("## 第一次使用", "## 每個交易日怎麼用", "## 每月例行", "## 各頁在看什麼", "## 名詞解釋", "## 常見問題"):
        assert section in markdown
    for path in ("/", "/holdings", "/plan", "/research", "/system"):
        assert 'href="/help"' in client.get(path).get_data(as_text=True)
    assert "使用指南" in client.get("/guide").get_data(as_text=True)          # the legacy guide is untouched
    system = client.get("/system").get_data(as_text=True)
    assert system.index('data-doc="guide"') < system.index('data-doc="roadmap"')   # the first tab
    assert "第一次使用" in client.get("/system/docs/guide").get_json()["markdown"]


def test_today_page_shows_one_line_when_the_system_has_a_problem(setup):
    from datetime import UTC, datetime

    container, client = setup
    assert 'class="alert-line"' not in client.get("/").get_data(as_text=True)

    runs = container.automation_service._runs
    run_id = runs.start("taiwan_research_data", "TW", datetime.now(UTC))
    runs.finish(run_id, "failed", datetime.now(UTC), "{}", "TWSE timeout")

    body = client.get("/").get_data(as_text=True)
    assert 'class="alert-line"' in body and "台股籌碼與基本面（TW）今天執行失敗" in body and "看系統頁" in body


def test_onboarding_checklist_follows_the_real_setup(setup):
    container, client = setup

    body = client.get("/").get_data(as_text=True)
    assert "開始使用" in body and "0／4" in body and "建立投資計畫" in body

    container.investment_plan_service.save({
        "monthly_amount": "10000", "salary_day": "5", "strategy_key": "benchmark_dca", "max_drawdown_tolerance": "30",
    })
    body = client.get("/").get_data(as_text=True)
    assert "開始使用" in body and "1／4" in body                       # a plan, no deposit yet

    container.actual_account_service.record_cash_flow({"kind": "deposit", "day": "2026-09-01", "amount": "10000"})
    assert 'id="onboarding"' not in client.get("/").get_data(as_text=True)   # plan and deposit: done
