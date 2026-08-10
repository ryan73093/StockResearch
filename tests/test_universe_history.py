from datetime import date

from quant_platform.application.universe_history import UniverseHistoryService
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import SqlAlchemyResearchUniverseRepository


class _FinMindLifecycleStub:
    def fetch_dataset(self, dataset: str):
        if dataset == "TaiwanStockInfo":
            return [
                {"stock_id": "2330", "stock_name": "台積電", "type": "twse"},
                {"stock_id": "6488", "stock_name": "環球晶", "type": "tpex"},
            ]
        return [{"date": "2010-06-30", "stock_id": "9999", "stock_name": "下市公司"}]


class _TwseLifecycleStub:
    def fetch_listed_companies(self):
        return [{"公司代號": "2330", "上市日期": "83/09/05"}]

    @staticmethod
    def company_code(row):
        return row["公司代號"]

    @staticmethod
    def listing_date(row):
        return date(1994, 9, 5)


def test_point_in_time_universe_sync_and_query(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'history.db'}"))
    repository = SqlAlchemyResearchUniverseRepository(container.database.session_factory)
    service = UniverseHistoryService(repository, _FinMindLifecycleStub(), _TwseLifecycleStub())

    result = service.sync_taiwan()
    members_2005 = service.members_on(date(2005, 1, 1), "TW")
    members_2015 = service.members_on(date(2015, 1, 1), "TW")
    audit = service.audit(date(2005, 1, 1), "TW")

    assert result.current_received == 2
    assert result.delisted_received == 1
    assert any(item.symbol == "2330.TW" and item.start_is_exact for item in members_2005)
    assert any(item.symbol == "9999.TW" for item in members_2005)
    assert not any(item.symbol == "9999.TW" for item in members_2015)
    assert audit.delisted_count >= 1
    assert audit.survivorship_safe is False


def test_universe_history_page_and_api(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'history-page.db'}"))
    from quant_platform.dashboard.app import create_app
    from quant_platform.api.app import create_api

    body = create_app(container).test_client().get(
        "/universe?effective_date=2024-01-02"
    ).get_data(as_text=True)
    api_paths = {route.path for route in create_api(container).routes}

    assert "存活者偏差稽核" in body
    assert "存活者安全門檻" in body
    assert "/api/v1/universe/history" in api_paths
    assert "/api/v1/universe/history/sync" in api_paths
    assert "/api/v1/rl/environment/{symbol}" in api_paths
    assert "/api/v1/rl/walk-forward/{symbol}" in api_paths
    assert "/api/v1/rl/cpu-agent/{symbol}" in api_paths
    assert "/api/v1/rl/neural-agent/{algorithm}/{symbol}" in api_paths
    assert "/api/v1/shadow-trading" in api_paths
    assert "/api/v1/shadow-trading/orders/{algorithm}/{symbol}" in api_paths
    assert "/api/v1/shadow-trading/process" in api_paths
    assert "/api/v1/promotions" in api_paths
    assert "/api/v1/promotions/evaluate/{algorithm}/{symbol}" in api_paths
    assert "/api/v1/promotions/{review_id}/decision" in api_paths
    assert "/api/v1/promotions/{review_id}/sandbox/{shadow_order_id}" in api_paths
    assert "/api/v1/model-governance" in api_paths
    assert "/api/v1/model-governance/refresh" in api_paths
    assert "/api/v1/model-governance/{entry_id}/promote" in api_paths
    assert "/api/v1/data-quality" in api_paths
    assert "/api/v1/data-quality/{market}" in api_paths
