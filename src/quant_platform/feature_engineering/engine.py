from __future__ import annotations

import json
import math
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from quant_platform.domain.entities import FeatureDefinition, FeatureValue, MarketBar


FEATURE_VERSION = "1.0.0"


def _definition(
    name: str, family: str, description: str, lookback: int, **parameters: object
) -> FeatureDefinition:
    return FeatureDefinition(
        name=name,
        version=FEATURE_VERSION,
        family=family,
        description=description,
        lookback=lookback,
        parameters_json=json.dumps(parameters, sort_keys=True),
    )


FEATURE_DEFINITIONS = [
    _definition("return_1d", "return", "One-session close-to-close return", 2),
    _definition("return_5d", "return", "Five-session close-to-close return", 6),
    _definition("momentum_20d", "momentum", "Twenty-session price momentum", 21),
    _definition("momentum_60d", "momentum", "Sixty-session price momentum", 61),
    _definition("close_to_sma_5", "trend", "Close divided by 5-session SMA minus one", 5),
    _definition("close_to_sma_20", "trend", "Close divided by 20-session SMA minus one", 20),
    _definition("close_to_ema_12", "trend", "Close divided by 12-session EMA minus one", 12),
    _definition("close_to_ema_26", "trend", "Close divided by 26-session EMA minus one", 26),
    _definition("rsi_14", "momentum", "Wilder-style 14-session relative strength index", 15),
    _definition("atr_14_pct", "volatility", "14-session average true range divided by close", 15),
    _definition("volatility_20d", "volatility", "Annualized 20-session return volatility", 21),
    _definition("volume_zscore_20", "volume", "Volume z-score over 20 sessions", 20),
    _definition("drawdown_252d", "risk", "Close relative to trailing 252-session high", 252),
    _definition("day_of_week", "calendar", "UTC weekday encoded from zero to four", 1),
]


class FeatureEngine:
    """Computes deterministic point-in-time features from OHLCV bars."""

    definitions = FEATURE_DEFINITIONS

    def compute(self, bars: list[MarketBar], computed_at: datetime | None = None) -> list[FeatureValue]:
        if not bars:
            return []
        calculated_at = computed_at or datetime.now(UTC)
        ordered = sorted(bars, key=lambda item: item.event_time)
        frame = pd.DataFrame(
            {
                "event_time": [item.event_time for item in ordered],
                "available_time": [item.available_time for item in ordered],
                "high": [float(item.high) for item in ordered],
                "low": [float(item.low) for item in ordered],
                "close": [float(item.adjusted_close or item.close) for item in ordered],
                "volume": [float(item.volume) for item in ordered],
            }
        )
        close = frame["close"]
        returns = close.pct_change(fill_method=None)
        previous_close = close.shift(1)
        true_range = pd.concat(
            [
                frame["high"] - frame["low"],
                (frame["high"] - previous_close).abs(),
                (frame["low"] - previous_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        delta = close.diff()
        gains = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        losses = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
        relative_strength = gains / losses.replace(0, np.nan)
        volume_mean = frame["volume"].rolling(20, min_periods=20).mean()
        volume_std = frame["volume"].rolling(20, min_periods=20).std(ddof=0)
        values = pd.DataFrame(
            {
                "return_1d": returns,
                "return_5d": close.pct_change(5, fill_method=None),
                "momentum_20d": close.pct_change(20, fill_method=None),
                "momentum_60d": close.pct_change(60, fill_method=None),
                "close_to_sma_5": close / close.rolling(5, min_periods=5).mean() - 1,
                "close_to_sma_20": close / close.rolling(20, min_periods=20).mean() - 1,
                "close_to_ema_12": close / close.ewm(span=12, adjust=False, min_periods=12).mean() - 1,
                "close_to_ema_26": close / close.ewm(span=26, adjust=False, min_periods=26).mean() - 1,
                "rsi_14": 100 - 100 / (1 + relative_strength),
                "atr_14_pct": true_range.rolling(14, min_periods=14).mean() / close,
                "volatility_20d": returns.rolling(20, min_periods=20).std(ddof=1) * math.sqrt(252),
                "volume_zscore_20": (frame["volume"] - volume_mean) / volume_std.replace(0, np.nan),
                "drawdown_252d": close / close.rolling(252, min_periods=20).max() - 1,
                "day_of_week": pd.to_datetime(frame["event_time"], utc=True).dt.dayofweek.astype(float),
            }
        )

        output: list[FeatureValue] = []
        symbol = ordered[0].symbol
        for index, row in values.iterrows():
            for name, raw_value in row.items():
                value = float(raw_value)
                if not math.isfinite(value):
                    continue
                output.append(
                    FeatureValue(
                        symbol=symbol,
                        feature_name=name,
                        feature_version=FEATURE_VERSION,
                        event_time=ordered[index].event_time,
                        available_time=ordered[index].available_time,
                        computed_at=calculated_at,
                        value=value,
                    )
                )
        return output
