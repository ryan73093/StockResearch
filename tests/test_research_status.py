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


def test_blend_rounds_are_not_summed_and_tiers_read_in_words(tmp_path):
    import json

    folder = tmp_path / "blend_scan"
    folder.mkdir()
    for name, pool, count in (("a", 100, 1000), ("b", 200, 9000), ("c", 200, 9000)):     # c reruns b
        (folder / f"finalists-{name}.json").write_text(json.dumps({"pool": pool, "candidates": count}), encoding="utf-8")
    scan = tmp_path / "scans" / "scan-large-1"
    scan.mkdir(parents=True)
    (scan / "summary.json").write_text(json.dumps({"candidates": 500, "tiers": {"T2": 1}}), encoding="utf-8")
    found = {item["name"]: item["result"] for item in status.analyses(tmp_path)}
    blend = next(text for name, text in found.items() if name.startswith("組合搜尋"))
    assert blend.startswith("2 輪；最大一輪 9,000 個組合（200 個策略") and "18,000" not in blend and "19,000" not in blend
    large = next(text for name, text in found.items() if name.startswith("大規模搜尋"))
    assert "T2 1 個" in large and "{" not in large
