from __future__ import annotations

from bisect import bisect_right
from datetime import UTC, datetime

from quant_platform.domain.entities import FeatureDefinition, FeatureValue, MarketBar
from quant_platform.feature_engineering.engine import _definition


CROSS_ASSET_VERSION = "1.0.0"
CROSS_ASSET_SYMBOLS = {
    "sp500_return_20d": "^GSPC", "sox_return_20d": "^SOX",
    "twii_return_20d": "^TWII", "gold_return_20d": "GC=F",
    "brent_return_20d": "BZ=F", "usdtwd_return_20d": "TWD=X", "vix_level": "^VIX",
}
CROSS_ASSET_DEFINITIONS: list[FeatureDefinition] = [
    _definition("sp500_return_20d", "cross_asset", "標普500 二十日報酬", 21, source="Yahoo"),
    _definition("sox_return_20d", "cross_asset", "費城半導體指數二十日報酬", 21, source="Yahoo"),
    _definition("twii_return_20d", "cross_asset", "台灣加權指數二十日報酬", 21, source="Yahoo"),
    _definition("gold_return_20d", "cross_asset", "黃金期貨二十日報酬", 21, source="Yahoo"),
    _definition("brent_return_20d", "cross_asset", "布蘭特原油二十日報酬", 21, source="Yahoo"),
    _definition("usdtwd_return_20d", "cross_asset", "美元兌台幣二十日變化", 21, source="Yahoo"),
    _definition("vix_level", "cross_asset", "VIX 最新可用收盤值", 1, source="Yahoo"),
]


class CrossAssetFeatureEngine:
    """As-of joins major indicators to each asset without using future closes."""

    definitions = CROSS_ASSET_DEFINITIONS
    symbols = tuple(sorted(set(CROSS_ASSET_SYMBOLS.values())))

    def compute(self, target_bars: list[MarketBar], context_bars: dict[str, list[MarketBar]], computed_at: datetime | None = None) -> list[FeatureValue]:
        if not target_bars:
            return []
        calculated_at = computed_at or datetime.now(UTC)
        prepared: dict[str, tuple[list[datetime], list[MarketBar]]] = {}
        for symbol, bars in context_bars.items():
            ordered = sorted(bars, key=lambda item: item.available_time)
            prepared[symbol] = ([item.available_time for item in ordered], ordered)
        output: list[FeatureValue] = []
        for target in sorted(target_bars, key=lambda item: item.event_time):
            for feature_name, symbol in CROSS_ASSET_SYMBOLS.items():
                times, bars = prepared.get(symbol, ([], []))
                index = bisect_right(times, target.available_time) - 1
                if index < 0 or (feature_name != "vix_level" and index < 20):
                    continue
                if feature_name == "vix_level":
                    value = float(bars[index].close)
                else:
                    previous = float(bars[index - 20].close)
                    if previous == 0:
                        continue
                    value = float(bars[index].close) / previous - 1
                output.append(FeatureValue(target.symbol, feature_name, CROSS_ASSET_VERSION, target.event_time, max(target.available_time, bars[index].available_time), calculated_at, value))
        return output
