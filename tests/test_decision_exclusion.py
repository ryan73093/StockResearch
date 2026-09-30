from datetime import UTC, date, datetime, timedelta, timezone

from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import (
    SqlAlchemyDailyDecisionRepository,
    SqlAlchemyFeatureLabelStoreRepository,
    SqlAlchemyResearchUniverseRepository,
)
from quant_platform.domain.entities import FeatureValue


def test_decision_excludes_symbol_trailing_the_market_across_holidays(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'decision.db'}"))
    factory = container.database.session_factory
    universe = SqlAlchemyResearchUniverseRepository(factory)
    for asset in universe.list_all():
        universe.set_active(asset.symbol, False)
    for symbol in ("2330.TW", "2867.TW"):
        container.research_universe_service.add_asset(
            symbol, "TW", asset_type="EQUITY", data_start=date(2026, 1, 1)
        )
    taipei = timezone(timedelta(hours=8))
    latest = {"2330.TW": date(2026, 9, 29), "2867.TW": date(2026, 9, 17)}
    values = []
    for symbol, day in latest.items():
        event = datetime(day.year, day.month, day.day, 13, 30, tzinfo=taipei)
        for name in ("momentum_60d", "return_5d", "volatility_20d", "drawdown_252d"):
            values.append(FeatureValue(
                symbol=symbol, feature_name=name, feature_version="1.0.0", event_time=event,
                available_time=event + timedelta(minutes=15),
                computed_at=event + timedelta(minutes=20), value=0.01,
            ))
    SqlAlchemyFeatureLabelStoreRepository(factory).upsert_features(values)

    container.daily_decision_pipeline.run("TW", now=datetime(2026, 9, 29, 6, 0, tzinfo=UTC))
    decisions = {
        item.symbol: item
        for item in SqlAlchemyDailyDecisionRepository(factory).list_latest("TW")
    }

    # 9/18, 9/21, 9/22, 9/23, 9/24, 9/29 are sessions; 9/25 and 9/28 were holidays.
    assert "行情落後市場，今日不列入決策" in decisions["2867.TW"].gate_checks_json
    assert "6 個交易日" in decisions["2867.TW"].risks_json
    assert decisions["2867.TW"].status == "資料不足"
    assert "行情落後市場" not in decisions["2330.TW"].gate_checks_json
