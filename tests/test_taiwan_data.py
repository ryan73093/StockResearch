from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta

from quant_platform.application.taiwan_data import TAIWAN_DATASETS, TaiwanDataPipeline
from quant_platform.application.ports import ProviderAccessBlockedError
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.database.repositories import (
    SqlAlchemyFeatureLabelStoreRepository,
    SqlAlchemyResearchUniverseRepository,
    SqlAlchemySchedulerJobRunRepository,
    SqlAlchemyTaiwanDataRepository,
)
from quant_platform.data_sources.finmind import FinMindProvider
from quant_platform.domain.entities import TaiwanDataRecord
from urllib.error import HTTPError


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
