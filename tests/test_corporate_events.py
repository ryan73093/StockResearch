from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from quant_platform.api.app import create_api
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.database.repositories import SqlAlchemyPointInTimeDataRepository
from quant_platform.data_sources.mops_events import MopsEarningsCallProvider
from quant_platform.domain.entities import PointInTimeObservation


def _container(tmp_path):
    return build_container(Settings(database_url=f"sqlite:///{tmp_path / 'events.db'}"))


def _definition(container):
    repository = SqlAlchemyPointInTimeDataRepository(container.database.session_factory)
    return repository, repository.get_dataset("tw_earnings_call")


def test_catalog_enables_official_earnings_calls_with_units(tmp_path) -> None:
    container = _container(tmp_path)
    _, definition = _definition(container)
    assert definition is not None
    assert definition.enabled is True
    assert definition.source == "mops"
    assert definition.frequency == "event"
    units = json.loads(definition.unit_schema_json)
    assert units["簡報文件數"] == "份文件"
    assert units["影音／相關連結數"] == "個連結"


def test_disclosure_normalization_separates_announcement_and_future_event(tmp_path) -> None:
    container = _container(tmp_path)
    _, definition = _definition(container)
    provider = MopsEarningsCallProvider()
    ingested = datetime(2026, 7, 19, 4, tzinfo=UTC)
    row = provider._normalize_disclosure(definition, "上櫃", {
        "Date": "1150719", "發言日期": "1150718", "發言時間": "153158",
        "SecuritiesCompanyCode": "6229", "CompanyName": "研通",
        "主旨": "本公司受邀參加線上法人說明會", "符合條款": "第12款",
        "事實發生日": "1150720",
        "說明": (
            "1.召開法人說明會之日期：115/07/20\r\n"
            "2.召開法人說明會之時間：14 時 00 分\r\n"
            "3.召開法人說明會之地點：線上法說會\r\n"
            "4.法人說明會擇要訊息：說明第一季營運概況。\r\n"
            "5.其他應敘明事項：無 https://example.com/video"
        ),
    }, ingested)
    assert row is not None
    assert row.available_time < row.event_time
    payload = json.loads(row.payload_json)
    assert payload["公司名稱"] == "研通"
    assert payload["地點"] == "線上法說會"
    assert payload["影音／相關連結數"] == 1
    assert payload["來源類型"] == "每日重大訊息"


def test_mops_detail_extracts_presentations_and_related_links(tmp_path) -> None:
    container = _container(tmp_path)
    _, definition = _definition(container)
    html = """
    <b>公司名稱：</b>台積電 <b>1 召開法人說明會日期：</b>115/07/16
    時間：14 點 0 分 (24小時制)
    召開法人說明會地點：台北會議中心
    法人說明會擇要訊息：公布第二季財務報告與第三季展望。
    法人說明會簡報內容
    <a href='/nas/STR/233020260716M001.pdf'>中文簡報</a>
    <a href='https://investor.example.com/q2'>影音連結</a>
    """
    now = datetime(2026, 7, 19, tzinfo=UTC)
    row = MopsEarningsCallProvider()._normalize_detail(
        definition, "2330", html, now
    )
    assert row is not None
    payload = json.loads(row.payload_json)
    assert payload["簡報文件數"] == 1
    assert payload["影音／相關連結數"] == 1
    assert payload["簡報網址"][0].endswith("233020260716M001.pdf")
    assert payload["擇要訊息"] == "公布第二季財務報告與第三季展望。"


def test_event_validation_and_as_of_allow_preannounced_events(tmp_path) -> None:
    container = _container(tmp_path)
    repository, definition = _definition(container)
    event = datetime(2026, 8, 4, 6, tzinfo=UTC)
    available = datetime(2026, 7, 18, 7, tzinfo=UTC)
    payload = json.dumps({
        "市場": "上櫃", "公司代號": "5347", "公司名稱": "世界",
        "公告時間": available.isoformat(), "法說會時間": event.isoformat(),
        "主旨": "線上法人說明會", "地點": "線上", "擇要訊息": "公布財報",
        "符合條款": "第12款", "簡報網址": [], "影音／相關網址": [],
        "簡報文件數": 0, "影音／相關連結數": 0, "來源類型": "每日重大訊息",
    }, ensure_ascii=False, sort_keys=True)
    observation = PointInTimeObservation(
        id=None, dataset_key=definition.dataset_key, entity_id="5347",
        event_time=event, available_time=available, ingested_at=available,
        revision_key="5347:event", content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        payload_json=payload, source="mops", source_uri="https://example.invalid",
    )

    class Provider:
        name = "mops"
        def fetch(self, *args):
            return [observation]

    container.point_in_time_data_service._provider = Provider()
    result = container.earnings_call_service.refresh("5347", now=available)
    assert result.inserted == 1
    assert repository.list_observations(
        definition.dataset_key, "5347", as_of=available - timedelta(seconds=1)
    ) == []
    assert len(repository.list_observations(
        definition.dataset_key, "5347", as_of=available
    )) == 1


def test_corporate_event_page_api_and_explicit_units(tmp_path) -> None:
    container = _container(tmp_path)
    repository, definition = _definition(container)
    available = datetime(2026, 7, 18, 7, tzinfo=UTC)
    event = datetime(2026, 8, 4, 6, tzinfo=UTC)
    payload = json.dumps({
        "市場": "上櫃", "公司名稱": "世界", "主旨": "公布第二季財報線上法說會",
        "地點": "線上", "擇要訊息": "說明財報與下一季展望", "符合條款": "第12款",
        "簡報網址": ["https://example.invalid/presentation.pdf"],
        "影音／相關網址": ["https://example.invalid/video"],
        "簡報文件數": 1, "影音／相關連結數": 1, "來源類型": "MOPS 法說會明細",
    }, ensure_ascii=False, sort_keys=True)
    repository.add_revisions([PointInTimeObservation(
        id=None, dataset_key=definition.dataset_key, entity_id="5347",
        event_time=event, available_time=available, ingested_at=available,
        revision_key="5347:event", content_hash=hashlib.sha256(payload.encode()).hexdigest(),
        payload_json=payload, source="mops", source_uri="https://example.invalid/source",
    )])
    page = create_app(container).test_client().get(
        "/corporate-events?symbol=5347&scope=all"
    ).get_data(as_text=True)
    assert "法說會與重大事件" in page
    assert "1 場" in page
    assert "1 家" in page
    assert "1 份文件" in page
    assert "1 個連結" in page
    assert "台北時間" in page
    assert "本頁名詞解釋" in page
    paths = {route.path for route in create_api(container).routes}
    assert "/api/v1/corporate-events" in paths
    assert "/api/v1/pipelines/corporate-events" in paths
