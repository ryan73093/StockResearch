from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from quant_platform.application.listing_reconciliation import TaiwanListingReconciliationService
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import (
    SqlAlchemyMarketBarRepository,
    SqlAlchemyResearchUniverseRepository,
    SqlAlchemySchedulerJobRunRepository,
)
from quant_platform.domain.entities import MarketBar
from quant_platform.market_calendar import MarketCalendarStore

TAIPEI = timezone(timedelta(hours=8))
NOW = datetime(2026, 9, 30, 14, 0, tzinfo=TAIPEI)


class Roster:
    def __init__(self, symbols):
        self.symbols = symbols

    def fetch_company_profiles(self):
        return tuple(SimpleNamespace(symbol=symbol) for symbol in self.symbols)


def _bar(symbol: str, day: date, volume: int) -> MarketBar:
    close = datetime(day.year, day.month, day.day, 13, 30, tzinfo=TAIPEI)
    price = Decimal("10")
    return MarketBar(
        symbol=symbol, market="TW", interval="1d", event_time=close,
        available_time=close + timedelta(minutes=15), ingested_at=close + timedelta(hours=1),
        open=price, high=price, low=price, close=price, adjusted_close=price,
        volume=volume, source="yahoo_finance",
    )


def _setup(tmp_path, traded_until: dict[str, date], flat_until: date = date(2026, 9, 11)):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'listing.db'}"))
    universe = SqlAlchemyResearchUniverseRepository(container.database.session_factory)
    for asset in universe.list_all():
        universe.set_active(asset.symbol, False)
    bars = SqlAlchemyMarketBarRepository(container.database.session_factory)
    rows = []
    for symbol, last_traded in traded_until.items():
        container.research_universe_service.add_asset(
            symbol, "TW", asset_type="EQUITY", data_start=date(2026, 8, 1)
        )
        day = date(2026, 8, 3)
        while day <= max(last_traded, flat_until):
            if day.weekday() < 5:
                rows.append(_bar(symbol, day, 1_000 if day <= last_traded else 0))
            day += timedelta(days=1)
    container.research_universe_service.add_asset(
        "0050.TW", "TW", asset_type="ETF", data_start=date(2026, 8, 1)
    )
    bars.add_missing(rows)
    container.universe_history_service.ensure_seed_memberships()
    return container, universe, bars


def _service(universe, bars, roster, tmp_path, **options):
    return TaiwanListingReconciliationService(
        universe, bars, Roster(roster), calendar_store=MarketCalendarStore(tmp_path),
        minimum_roster_size=1, **options,
    )


def test_delisted_equity_is_retired_and_its_universe_interval_closed(tmp_path):
    container, universe, bars = _setup(
        tmp_path, {"2867.TW": date(2026, 9, 3), "2330.TW": date(2026, 9, 30)}
    )

    result = _service(universe, bars, ["2330.TW"], tmp_path).run(NOW)

    assert result.deactivated == ("2867.TW",)
    assert "最後成交 2026-09-03" in result.reasons["2867.TW"]
    assert universe.get("2867.TW").active is False
    assert universe.get("2330.TW").active is True
    assert universe.get("0050.TW").active is True  # ETFs are not on company rosters
    members = {item.symbol for item in container.universe_history_service.members_on(
        date(2026, 9, 30), "TW"
    )}
    assert "2867.TW" not in members
    assert "2330.TW" in members
    history = [item for item in universe.list_memberships("TW") if item.symbol == "2867.TW"]
    assert history and all(item.valid_to == date(2026, 9, 3) for item in history)


def test_recently_traded_equity_missing_from_roster_is_kept(tmp_path):
    _, universe, bars = _setup(tmp_path, {"6999.TW": date(2026, 9, 29)})

    result = _service(universe, bars, ["2330.TW"], tmp_path).run(NOW)

    assert result.deactivated == ()
    assert universe.get("6999.TW").active is True


def test_short_roster_download_retires_nothing(tmp_path):
    _, universe, bars = _setup(tmp_path, {"2867.TW": date(2026, 9, 3)})
    service = TaiwanListingReconciliationService(
        universe, bars, Roster(["2330.TW"]), calendar_store=MarketCalendarStore(tmp_path),
        minimum_roster_size=1500,
    )

    result = service.run(NOW)

    assert result.deactivated == ()
    assert "下載不完整" in result.skipped_reason
    assert universe.get("2867.TW").active is True


def test_too_many_candidates_requires_manual_review(tmp_path):
    _, universe, bars = _setup(
        tmp_path, {"2867.TW": date(2026, 9, 3), "5371.TWO": date(2026, 8, 28)}
    )

    result = _service(universe, bars, ["2330.TW"], tmp_path, maximum_retirements=1).run(NOW)

    assert result.deactivated == ()
    assert set(result.reasons) == {"2867.TW", "5371.TWO"}
    assert "人工確認" in result.skipped_reason
    assert universe.get("2867.TW").active is True


def test_reconciliation_records_an_audited_run(tmp_path):
    container, universe, bars = _setup(tmp_path, {"2867.TW": date(2026, 9, 3)})
    runs = SqlAlchemySchedulerJobRunRepository(container.database.session_factory)
    service = TaiwanListingReconciliationService(
        universe, bars, Roster(["2330.TW"]), runs,
        calendar_store=MarketCalendarStore(tmp_path), minimum_roster_size=1,
    )

    service.run(NOW)
    latest = runs.list_recent(1)[0]

    assert latest.job_name == "tw_listing_reconciliation"
    assert latest.status.value == "succeeded"
    assert "2867.TW" in latest.metrics_json
