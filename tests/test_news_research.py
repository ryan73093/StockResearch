import json
from datetime import UTC, datetime

from quant_platform.application.news_research import NewsResearchService
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import SqlAlchemyTaiwanDataRepository
from quant_platform.domain.entities import TaiwanDataRecord


def test_news_sentiment_and_dashboard(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'news.db'}"))
    repository = SqlAlchemyTaiwanDataRepository(container.database.session_factory)
    now = datetime(2026, 7, 18, tzinfo=UTC)
    raw = {"date": "2026-07-18", "title": "獲利成長並上調展望", "description": "營收創高", "source": "測試來源", "link": "https://example.com/news"}
    repository.add_revisions([TaiwanDataRecord(
        id=None, symbol="2330.TW", dataset="TaiwanStockNews",
        event_time=now, available_time=now, ingested_at=now,
        record_key="positive-news", content_hash="a" * 64,
        fields_json=json.dumps(raw, ensure_ascii=False), source="test",
    )])

    overview = NewsResearchService(repository).get_overview("2330")
    body = container.news_research_service.get_overview("2330")
    from quant_platform.dashboard.app import create_app
    page = create_app(container).test_client().get("/news?symbol=2330").get_data(as_text=True)

    assert overview.total_count == 1
    assert overview.positive_count == 1
    assert body.items[0].sentiment_label == "正向"
    assert "獲利成長並上調展望" in page
    assert "本頁名詞解釋" in page
