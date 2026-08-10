from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quant_platform.application.analytics import QuantAnalyticsService
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import SqlAlchemyMarketBarRepository
from quant_platform.domain.entities import MarketBar


def price_bars(symbol: str, start_price: float, daily_step: float) -> list[MarketBar]:
    bars: list[MarketBar] = []
    for index in range(30):
        event_time = datetime(2025, 1, 1, 16, tzinfo=UTC) + timedelta(days=index)
        price = Decimal(str(start_price + daily_step * index))
        bars.append(
            MarketBar(
                symbol=symbol,
                market="US" if symbol == "SPY" else "TW",
                interval="1d",
                event_time=event_time,
                available_time=event_time + timedelta(minutes=15),
                ingested_at=event_time + timedelta(hours=1),
                open=price,
                high=price + Decimal("1"),
                low=price - Decimal("1"),
                close=price,
                adjusted_close=price,
                volume=1_000_000,
                source="yahoo_finance",
            )
        )
    return bars


def test_command_center_computes_regime_backtest_and_risk(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'analytics.db'}"))
    repository = SqlAlchemyMarketBarRepository(container.database.session_factory)
    repository.add_missing(price_bars("SPY", 500, 1.0))
    repository.add_missing(price_bars("0050.TW", 150, 0.2))
    service = QuantAnalyticsService(repository)

    result = service.command_center(["SPY", "0050.TW"])

    assert len(result["assets"]) == 2
    assert result["assets"][0].regime == "BULL TREND"
    assert len(result["backtests"]) == 2
    assert result["backtests"][0].strategy.endswith("signal lagged 1 session")
    assert result["portfolio"] is not None
    assert abs(sum(result["portfolio"].weights.values()) - 1) < 1e-9
    assert all(item.quality_gate.startswith("CROSS-SECTION") for item in result["factors"])
