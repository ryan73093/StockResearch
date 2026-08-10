from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd

from quant_platform.application.odd_lot_research import OddLotAssumptions, OddLotResearchService
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import SqlAlchemyMarketBarRepository
from quant_platform.dashboard.app import create_app
from quant_platform.domain.entities import MarketBar


def _bars(symbol: str, daily_growth: float) -> list[MarketBar]:
    output = []
    for index, day in enumerate(pd.bdate_range("2022-01-03", periods=700)):
        price = 100 * (1 + daily_growth) ** index
        event_time = day.to_pydatetime().replace(tzinfo=UTC)
        output.append(
            MarketBar(
                symbol=symbol,
                market="TW",
                interval="1d",
                event_time=event_time,
                available_time=event_time + timedelta(hours=6),
                ingested_at=datetime(2026, 1, 1, tzinfo=UTC),
                open=Decimal(str(price)),
                high=Decimal(str(price * 1.01)),
                low=Decimal(str(price * 0.99)),
                close=Decimal(str(price)),
                adjusted_close=Decimal(str(price)),
                volume=1_000_000,
                source="test",
            )
        )
    return output


def test_odd_lot_research_compares_cost_aware_monthly_strategies(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'odd-lot.db'}"))
    repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    repository.add_missing(_bars("0050.TW", 0.0003))
    repository.add_missing(_bars("006208.TW", 0.0005))
    repository.add_missing(_bars("00878.TW", 0.0001))

    overview = OddLotResearchService(repository).run(
        OddLotAssumptions(monthly_budget=10_000, salary_day=5, minimum_fee=20)
    )

    assert len(overview.results) == 3
    assert overview.results[0].total_contributions > 0
    assert overview.results[0].purchase_count > 0
    assert any(item.strategy == "ETF 相對動能" for item in overview.results)
    assert any("不保證" in item for item in overview.limitations)
    assert overview.research_ready is False
    assert overview.best_strategy is None
    assert any("14:30" in item for item in overview.quality_notes)


def test_odd_lot_page_and_page_glossary_render(tmp_path) -> None:
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'odd-page.db'}"))
    repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    for symbol, growth in (("0050.TW", 0.0003), ("006208.TW", 0.0005), ("00878.TW", 0.0001)):
        repository.add_missing(_bars(symbol, growth))
    body = create_app(container).test_client().get("/odd-lot?budget=12000").get_data(as_text=True)

    assert "盤後 AI 決策" in body
    assert "盤後零股投入研究（已整合）" in body
    assert "本頁名詞解釋" in body
    assert "集合競價" in body
    assert "盤後零股" in body
