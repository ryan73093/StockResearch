from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from urllib.error import HTTPError

from quant_platform.application.ports import ProviderAccessBlockedError
from quant_platform.application.taiwan_data import TAIWAN_DATASETS, TaiwanDataPipeline
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.data_sources.finmind import FinMindProvider
from quant_platform.data_sources.taiwan_official import TaiwanOfficialFallbackProvider
from quant_platform.database.repositories import (
    SqlAlchemyFeatureLabelStoreRepository,
    SqlAlchemyResearchUniverseRepository,
    SqlAlchemySchedulerJobRunRepository,
    SqlAlchemyTaiwanDataRepository,
)
from quant_platform.domain.entities import TaiwanDataRecord


class FakeTaiwanProvider:
    name = "finmind"

    def fetch(self, dataset: str, symbol: str, start: datetime, end: datetime) -> list[TaiwanDataRecord]:
        raw = {
            "date": "2025-01-02",
            "stock_id": symbol.removesuffix(".TW"),
            "buy": 120,
            "sell": 80,
            "MarginPurchaseTodayBalance": 500,
            "ShortSaleTodayBalance": 30,
            "transaction_quantity": 20,
            "PER": 18.5,
            "PBR": 4.2,
            "dividend_yield": 2.1,
            "revenue": 2000,
            "revenue_month": 1,
            "revenue_year": 2025,
        }
        event_time = datetime(2025, 1, 2, tzinfo=UTC)
        statement_rows = {
            "TaiwanStockFinancialStatements": [
                {"type": "EPS", "value": 2.5},
                {"type": "GrossProfit", "value": 400},
                {"type": "OperatingRevenue", "value": 1000},
                {"type": "IncomeAfterTaxes", "value": 100},
            ],
            "TaiwanStockBalanceSheet": [
                {"type": "TotalAssets", "value": 4000},
                {"type": "TotalEquity", "value": 2000},
            ],
            "TaiwanStockCashFlowsStatement": [
                {"type": "CashFlowsFromOperatingActivities", "value": 300},
                {"type": "PropertyAndPlantAndEquipment", "value": -120},
            ],
        }
        raws = [{**raw, **item} for item in statement_rows.get(dataset, [{}])]
        output = []
        for item in raws:
            fields_json = json.dumps(item, sort_keys=True)
            output.append(
            TaiwanDataRecord(
                id=None,
                symbol=symbol,
                dataset=dataset,
                event_time=event_time,
                available_time=event_time + timedelta(hours=10),
                ingested_at=event_time + timedelta(hours=11),
                record_key=str(item.get("type", "record")),
                content_hash=hashlib.sha256(fields_json.encode()).hexdigest(),
                fields_json=fields_json,
                source=self.name,
            ))
        return output


def test_taiwan_data_pipeline_is_idempotent_and_builds_features(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'taiwan.db'}"))
    container.research_universe_service.add_asset(
        "2330.TW", "TW", "EQUITY", "半導體", "0050.TW", date(2024, 1, 1)
    )
    repository = SqlAlchemyTaiwanDataRepository(container.database.session_factory)
    universe_repository = SqlAlchemyResearchUniverseRepository(container.database.session_factory)
    feature_repository = SqlAlchemyFeatureLabelStoreRepository(container.database.session_factory)
    run_repository = SqlAlchemySchedulerJobRunRepository(container.database.session_factory)
    pipeline = TaiwanDataPipeline(
        universe_repository,
        repository,
        feature_repository,
        run_repository,
        FakeTaiwanProvider(),
    )

    first = pipeline.run(now=datetime(2025, 1, 3, tzinfo=UTC))
    second = pipeline.run(now=datetime(2025, 1, 4, tzinfo=UTC))

    assert first.received >= first.asset_count * len(TAIWAN_DATASETS)
    assert first.inserted == first.received
    assert second.inserted == 0
    assert len(repository.list_coverage()) == first.asset_count * len(TAIWAN_DATASETS)
    feature_names = {
        item.item_name for item in container.feature_store_overview_service.get_overview().feature_coverage
    }
    assert {
        "institutional_net_buy", "pe_ratio", "monthly_revenue", "quarterly_eps",
        "roe_annualized", "roa_annualized", "gross_margin", "free_cash_flow",
    } <= feature_names


def test_taiwan_features_can_be_rebuilt_without_downloading_again(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'rebuild.db'}"))
    container.research_universe_service.add_asset(
        "2330.TW", "TW", "EQUITY", "半導體", "0050.TW", date(2024, 1, 1)
    )
    repository = SqlAlchemyTaiwanDataRepository(container.database.session_factory)
    universe_repository = SqlAlchemyResearchUniverseRepository(container.database.session_factory)
    feature_repository = SqlAlchemyFeatureLabelStoreRepository(container.database.session_factory)
    run_repository = SqlAlchemySchedulerJobRunRepository(container.database.session_factory)
    provider = FakeTaiwanProvider()
    raw = provider.fetch(
        "TaiwanStockPER",
        "2330.TW",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 3, tzinfo=UTC),
    )
    repository.add_revisions(raw)

    class NoDownloadProvider:
        name = "must-not-run"

        def fetch(self, *_args, **_kwargs):
            raise AssertionError("feature rebuild must not call a provider")

    pipeline = TaiwanDataPipeline(
        universe_repository,
        repository,
        feature_repository,
        run_repository,
        NoDownloadProvider(),
    )

    assert pipeline.rebuild_features(datetime(2025, 1, 4, tzinfo=UTC)) >= 1
    assert any(
        item.item_name == "pe_ratio"
        for item in container.feature_store_overview_service.get_overview().feature_coverage
    )
    assert run_repository.list_recent(1)[0].job_name == "taiwan_feature_rebuild"


def test_taiwan_repository_honors_available_time_and_preserves_revision(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'revisions.db'}"))
    repository = SqlAlchemyTaiwanDataRepository(container.database.session_factory)
    original = FakeTaiwanProvider().fetch(
        "TaiwanStockPER",
        "2330.TW",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 1, 3, tzinfo=UTC),
    )[0]
    revised_json = original.fields_json.replace("18.5", "19.0")
    revised = TaiwanDataRecord(
        **{
            **{field: getattr(original, field) for field in original.__dataclass_fields__},
            "id": None,
            "content_hash": hashlib.sha256(revised_json.encode()).hexdigest(),
            "fields_json": revised_json,
            "ingested_at": original.ingested_at + timedelta(hours=1),
        }
    )
    assert repository.add_revisions([original, original, revised]) == 2
    assert repository.list_records(as_of=original.available_time - timedelta(seconds=1)) == []
    assert len(repository.list_records(as_of=original.available_time)) == 2


def test_monthly_revenue_uses_actual_announcement_date() -> None:
    provider = FinMindProvider("https://example.invalid")
    record = provider._record(
        "TaiwanStockMonthRevenue",
        "2330.TW",
        {
            "date": "2026-06-01",
            "stock_id": "2330",
            "revenue": 100,
            "revenue_month": 5,
            "revenue_year": 2026,
            "create_time": "2026-06-10",
        },
        datetime(2026, 6, 11, tzinfo=UTC),
    )
    assert record.event_time == datetime(2026, 5, 31, 16, tzinfo=UTC)
    assert record.available_time == datetime(2026, 6, 10, 10, tzinfo=UTC)


def test_finmind_access_error_opens_circuit_without_retry(monkeypatch) -> None:
    calls = 0

    def denied(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise HTTPError("https://example.invalid", 402, "Payment Required", {}, None)

    monkeypatch.setattr("quant_platform.data_sources.finmind.urlopen", denied)
    provider = FinMindProvider("https://example.invalid", timeout=1)
    for _ in range(2):
        try:
            provider._request({"dataset": "TaiwanStockPER", "data_id": "2330"})
        except ProviderAccessBlockedError:
            pass
        else:
            raise AssertionError("expected provider access to be blocked")
    assert calls == 1
    state = provider.status()
    assert state["http_code"] == 402
    assert 3500 <= state["retry_after_seconds"] <= 3600


def test_finmind_rate_limit_honors_retry_after_without_recalling(monkeypatch) -> None:
    calls = 0

    def denied(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise HTTPError(
            "https://example.invalid", 429, "Too Many Requests", {"Retry-After": "90"}, None
        )

    monkeypatch.setattr("quant_platform.data_sources.finmind.urlopen", denied)
    provider = FinMindProvider("https://example.invalid", timeout=1)
    for _ in range(2):
        try:
            provider._request({"dataset": "TaiwanStockPER", "data_id": "2330"})
        except ProviderAccessBlockedError:
            pass
    assert calls == 1
    assert provider.status()["retry_after_seconds"] in {89, 90}


def test_finmind_circuit_survives_process_restart(tmp_path, monkeypatch) -> None:
    calls = 0

    def denied(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise HTTPError("https://example.invalid", 403, "Forbidden", {}, None)

    monkeypatch.setattr("quant_platform.data_sources.finmind.urlopen", denied)
    state_path = tmp_path / "finmind-circuit.json"
    first = FinMindProvider("https://example.invalid", circuit_state_path=state_path)
    try:
        first._request({"dataset": "TaiwanStockPER"})
    except ProviderAccessBlockedError:
        pass
    second = FinMindProvider("https://example.invalid", circuit_state_path=state_path)
    try:
        second._request({"dataset": "TaiwanStockPER"})
    except ProviderAccessBlockedError:
        pass
    assert calls == 1
    assert second.status()["http_code"] == 403


def test_taiwan_data_page_displays_units_for_every_numeric_family(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'units.db'}"))
    container.research_universe_service.add_asset(
        "2330.TW", "TW", "EQUITY", "半導體", "0050.TW", date(2024, 1, 1)
    )
    pipeline = TaiwanDataPipeline(
        SqlAlchemyResearchUniverseRepository(container.database.session_factory),
        SqlAlchemyTaiwanDataRepository(container.database.session_factory),
        SqlAlchemyFeatureLabelStoreRepository(container.database.session_factory),
        SqlAlchemySchedulerJobRunRepository(container.database.session_factory),
        FakeTaiwanProvider(),
    )
    pipeline.run(now=datetime(2025, 1, 3, tzinfo=UTC))
    page = create_app(container).test_client().get(
        "/taiwan-data?symbol=2330.TW"
    ).get_data(as_text=True)
    assert "120 股" in page
    assert "500 張" in page
    assert "18.50 倍" in page
    assert "2.10%" in page
    assert "2,000 元" in page
    assert "2.50 元／股" in page
    assert "2025 年" in page


def test_quarterly_statements_use_conservative_ninety_day_availability() -> None:
    provider = FinMindProvider("https://example.invalid")
    record = provider._record(
        "TaiwanStockFinancialStatements",
        "2330.TW",
        {"date": "2025-03-31", "stock_id": "2330", "type": "EPS", "value": 12.3},
        datetime(2025, 7, 1, tzinfo=UTC),
    )
    assert record.available_time == datetime(2025, 6, 29, 10, tzinfo=UTC)


def test_official_market_margin_and_tdcc_shareholding_are_normalized(monkeypatch) -> None:
    class Fallback:
        name = "fallback"

        def fetch(self, *_args, **_kwargs):
            raise AssertionError("official datasets must not call fallback")

    provider = TaiwanOfficialFallbackProvider(Fallback())

    def snapshots(dataset, suffix):
        if dataset == "TaiwanStockMarginPurchaseShortSale":
            return {"2330": [{
                "股票代號": "2330", "融資今日餘額": "1234",
                "融券今日餘額": "56", "融資買進": "100", "融資賣出": "90",
            }]}
        if dataset == "TaiwanStockPER":
            return {"2330": [{"Date": "20250102"}]}
        if dataset == "TaiwanStockInstitutionalInvestorsBuySell":
            return {"2330": [{
                "Date": "20250102", "證券代號": "2330",
                "外陸資買賣超股數(不含外資自營商)": "100",
                "投信買賣超股數": "20", "自營商買賣超股數": "-5",
                "三大法人買賣超股數": "115",
            }]}
        return {"2330": [
            {"\ufeff資料日期": "20250103", "證券代號": "2330  ",
             "持股分級": "1", "人數": "100", "股數": "200",
             "占集保庫存數比例%": "1.5"},
            {"\ufeff資料日期": "20250103", "證券代號": "2330  ",
             "持股分級": "15", "人數": "8", "股數": "9000",
             "占集保庫存數比例%": "80.5"},
            {"\ufeff資料日期": "20250103", "證券代號": "2330  ",
             "持股分級": "17", "人數": "108", "股數": "9200",
             "占集保庫存數比例%": "100"},
        ]}

    monkeypatch.setattr(provider, "_market_snapshot", snapshots)
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 1, 10, tzinfo=UTC)
    margin = provider.fetch(
        "TaiwanStockMarginPurchaseShortSale", "2330.TW", start, end
    )
    fields = json.loads(margin[0].fields_json)
    assert fields["MarginPurchaseTodayBalance"] == "1234"
    assert fields["ShortSaleTodayBalance"] == "56"

    institutional = provider.fetch(
        "TaiwanStockInstitutionalInvestorsBuySell", "2330.TW", start, end
    )
    institution_fields = json.loads(institutional[0].fields_json)
    assert institution_fields["foreign_net_buy"] == "100"
    assert institution_fields["institutional_net_buy"] == "115"

    distribution = provider.fetch(
        "TaiwanStockShareholdingDistribution", "2330.TW", start, end
    )
    assert len(distribution) == 3
    assert distribution[0].source == "tdcc_openapi"
    assert distribution[0].available_time == datetime(2025, 1, 6, 2, tzinfo=UTC)


def test_tdcc_distribution_materializes_large_and_retail_holder_features(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'tdcc.db'}"))
    pipeline = container.taiwan_data_pipeline
    values = pipeline._extract("TaiwanStockShareholdingDistribution", [
        {"holding_level": "1", "holders": "100", "ratio_pct": "1.5"},
        {"holding_level": "15", "holders": "8", "ratio_pct": "80.5"},
        {"holding_level": "17", "holders": "108", "ratio_pct": "100"},
    ])
    assert values == {
        "large_holder_ratio_1000_lots": 80.5,
        "retail_holder_ratio_under_1_lot": 1.5,
        "large_holder_count_1000_lots": 8.0,
        "shareholder_count": 108.0,
    }


def test_the_retired_taiwan_data_pipeline_writes_nothing_while_the_legacy_research_is_paused(tmp_path) -> None:
    """S9-W04 (2026-10-06): no page or API call writes the SQLite Taiwan data any more."""
    from unittest.mock import MagicMock

    repository, provider = MagicMock(), MagicMock()
    pipeline = TaiwanDataPipeline(MagicMock(), repository, MagicMock(), MagicMock(), provider, paused=lambda: True)
    result = pipeline.run(now=datetime(2025, 1, 3, tzinfo=UTC))
    assert result.status == "paused" and "legacy_research" in result.failures
    repository.add_revisions.assert_not_called()
    provider.assert_not_called()
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'paused.db'}"))
    assert container.taiwan_data_pipeline.run(now=datetime(2025, 1, 3, tzinfo=UTC)).status == "paused"
