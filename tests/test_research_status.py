"""2026-10-22 (使用者：研究首頁建一個表——因子、策略類別、分析項目、成長、版本、角色成長): research/status.py and its card."""
from datetime import date

from quant_platform.research import status


def test_the_growth_reads_the_last_point_on_or_before_the_day():
    history = [{"day": "2026-10-10", "final": 100.0}, {"day": "2026-10-20", "final": 150.0}, {"day": "2026-10-22", "final": 180.0}]
    assert status._growth(history, 180.0, date(2026, 10, 21)) == 180 / 150 - 1
    assert status._growth(history, 180.0, date(2026, 10, 15)) == 180 / 100 - 1
    assert status._growth(history, 180.0, date(2026, 10, 1)) is None


def test_an_empty_research_folder_still_gives_every_part(tmp_path):
    found = status.overview(tmp_path)
    assert found["categories"]["rows"] == [] and found["factors"]["rows"] == []
    assert len(found["analyses"]) >= 10 and all(item["name"] for item in found["analyses"])
    assert found["versions"]["engine"].startswith("daily-") and found["versions"]["accounts"] == 0


def test_the_research_page_shows_the_status_table(tmp_path):
    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'status.db'}"))
    body = create_app(container).test_client().get("/research").get_data(as_text=True)
    assert "研究現況總表" in body and "策略類別與成長" in body and "角色成長歷程" in body and "欄位說明" in body
