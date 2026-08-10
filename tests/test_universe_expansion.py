from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from quant_platform.application.universe_expansion import UniverseExpansionService
from quant_platform.data_sources.taiwan_market_rank import (
    TaiwanOfficialMarketRankingProvider,
)
from quant_platform.domain.entities import MarketBar, ResearchAsset, UniverseMembership


NOW = datetime(2026, 7, 27, 5, 30, tzinfo=UTC)


class UniverseRepo:
    def __init__(self):
        self.assets = {}

    def get(self, symbol):
        return self.assets.get(symbol)

    def list_active(self, market=None):
        return [
            item for item in self.assets.values()
            if item.active and (market is None or item.market == market)
        ]


class UniverseService:
    def __init__(self, repo):
        self.repo = repo

    def add_asset(
        self, symbol, market, asset_type="EQUITY", sector=None,
        benchmark_symbol=None, data_start=None,
    ):
        value = ResearchAsset(
            id=len(self.repo.assets) + 1, symbol=symbol, market=market,
            asset_type=asset_type, sector=sector,
            benchmark_symbol=benchmark_symbol, active=True,
            data_start=data_start, created_at=NOW, updated_at=NOW,
        )
        self.repo.assets[symbol] = value
        return value

    def set_active(self, symbol, active):
        value = self.repo.assets[symbol]
        self.repo.assets[symbol] = ResearchAsset(
            id=value.id, symbol=value.symbol, market=value.market,
            asset_type=value.asset_type, sector=value.sector,
            benchmark_symbol=value.benchmark_symbol, active=active,
            data_start=value.data_start, created_at=value.created_at,
            updated_at=NOW,
        )


class History:
    def members_on(self, effective_date, market=None):
        return [
            UniverseMembership(
                id=index, symbol=symbol, market="TW",
                valid_from=date(2000, 1, 1), valid_to=None,
                start_is_exact=True, end_is_exact=True, source="test",
                reason="test", recorded_at=NOW,
            )
            for index, symbol in enumerate(
                ["2330.TW", "2317.TW", "6488.TWO", "006208.TW"], 1
            )
        ]


def bar(symbol, sequence):
    return MarketBar(
        symbol=symbol, market="TW", interval="1d",
        event_time=NOW, available_time=NOW, ingested_at=NOW,
        open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
        close=Decimal("100"), adjusted_close=Decimal("100"),
        volume=1_000_000, source=f"test-{sequence}",
    )


class Bars:
    def __init__(self):
        self.values = {}

    def list_bars(self, symbol, interval="1d", source=None, as_of=None):
        return self.values.get(symbol, [])


class MarketPipeline:
    def __init__(self, bars):
        self.bars = bars

    def run(self, market, now=None, full_refresh=False, symbols=None):
        for symbol in symbols:
            self.bars.values[symbol] = [bar(symbol, index) for index in range(252)]
        return SimpleNamespace(
            received=252 * len(symbols), inserted=252 * len(symbols), failures={}
        )


class TaiwanPipeline:
    def run(self, market, now=None, full_refresh=False, symbols=None):
        return SimpleNamespace()


class Decisions:
    def list_latest(self, market=None):
        return []


def test_expansion_prioritizes_large_cross_industry_names_and_keeps_ready_assets():
    repository = UniverseRepo()
    universe = UniverseService(repository)
    bars = Bars()
    service = UniverseExpansionService(
        universe, repository, History(), MarketPipeline(bars),
        TaiwanPipeline(), bars, Decisions(), daily_batch_size=2,
    )

    before = service.overview()
    result = service.run_batch(now=NOW)
    after = service.overview()

    assert before.eligible_common_stocks == 3
    assert before.next_symbols[:2] == ("2317.TW", "2330.TW")
    assert result.symbols == ("2317.TW", "2330.TW")
    assert result.kept_active == 2
    assert result.deactivated == 0
    assert after.data_ready_assets == 2
    assert after.remaining_assets == 1


def test_official_market_rank_combines_twse_and_tpex_values():
    provider = TaiwanOfficialMarketRankingProvider()
    responses = {
        provider._twse_companies: [
            {"公司代號": "2330", "已發行普通股數或TDR原股發行股數": "1000"},
        ],
        provider._twse_daily: [
            {"Code": "2330", "ClosingPrice": "100"},
        ],
        provider._tpex_value: [
            {"SecuritiesCompanyCode": "6488", "MarketValue": "0.01"},
        ],
    }
    provider._request = lambda url: responses[url]

    values = provider.fetch(force=True)

    assert values[0].symbol == "2330.TW"
    assert values[0].market_value_twd == Decimal("100000")
    assert values[1].symbol == "6488.TWO"
