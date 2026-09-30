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
        ("monthly_amount", "abc"), ("monthly_amount", "500"), ("salary_day", "31"),
        ("strategy_key", "all_in_2330"), ("max_drawdown_tolerance", "95"), ("horizon_years", "100"),
    ):
        with pytest.raises(InvestmentPlanError):
            parse_plan_form({**FORM, field: value})


def test_every_save_is_a_new_version(container):
    service = container.investment_plan_service
    assert service.current() is None

    first = service.save(FORM, now=datetime(2026, 10, 1, tzinfo=UTC))
    second = service.save({**FORM, "monthly_amount": "20000", "note": "加薪"}, now=datetime(2026, 11, 1, tzinfo=UTC))

    assert (first.version, second.version) == (1, 2)
    assert service.current().monthly_amount == 20_000
    assert [item.version for item in service.history()] == [2, 1]
    assert service.history()[1].monthly_amount == 15_000  # the old version is kept


def test_plan_page_saves_and_shows_errors(container):
    client = create_app(container).test_client()
    assert "建立你的投資計畫" in client.get("/plan").get_data(as_text=True)

    bad = client.post("/plan", data={**FORM, "salary_day": "31"})
    assert bad.status_code == 400 and "薪資日必須是 1～28" in bad.get_data(as_text=True)

    saved = client.post("/plan", data=FORM, follow_redirects=True)
    body = saved.get_data(as_text=True)
    assert saved.status_code == 200 and "已儲存投資計畫第 1 版" in body
    assert "定期不定額（200 日均線）" in body and "15,000" in body
