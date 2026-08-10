from __future__ import annotations

import math
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from quant_platform.domain.entities import MarketBar, RegimeState


REGIME_VERSION = "1.0.0"


class RegimeDetectionEngine:
    """Classifies trend and volatility using only observations available at each date."""

    version = REGIME_VERSION

    def compute(
        self, bars: list[MarketBar], computed_at: datetime | None = None
    ) -> list[RegimeState]:
        if not bars:
            return []
        calculated_at = computed_at or datetime.now(UTC)
        ordered = sorted(bars, key=lambda item: item.event_time)
        close = pd.Series(
            [float(item.adjusted_close or item.close) for item in ordered], dtype=float
        )
        returns = close.pct_change(fill_method=None)
        sma_20 = close.rolling(20, min_periods=20).mean()
        sma_50 = close.rolling(50, min_periods=50).mean()
        sma_60 = close.rolling(60, min_periods=60).mean()
        sma_200 = close.rolling(200, min_periods=200).mean()
        momentum_60 = close.pct_change(60, fill_method=None)
        realized_vol = returns.rolling(20, min_periods=20).std(ddof=1) * math.sqrt(252)
        vol_low = realized_vol.rolling(126, min_periods=60).quantile(0.30)
        vol_high = realized_vol.rolling(126, min_periods=60).quantile(0.70)

        output: list[RegimeState] = []
        for index in range(len(ordered)):
            if index < 60 or not math.isfinite(float(realized_vol.iloc[index])):
                continue
            current = float(close.iloc[index])
            if index >= 199:
                slow = float(sma_200.iloc[index])
                fast = float(sma_50.iloc[index])
            else:
                slow = float(sma_60.iloc[index])
                fast = float(sma_20.iloc[index])
            trend_score = (
                0.5 * (current / slow - 1)
                + 0.3 * (fast / slow - 1)
                + 0.2 * float(momentum_60.iloc[index])
            )
            if current > slow and fast > slow:
                trend = "BULL"
            elif current < slow and fast < slow:
                trend = "BEAR"
            else:
                trend = "RANGE"

            current_vol = float(realized_vol.iloc[index])
            low = float(vol_low.iloc[index])
            high = float(vol_high.iloc[index])
            if not math.isfinite(low) or not math.isfinite(high):
                volatility = "NORMAL_VOL"
                volatility_score = 0.0
            elif current_vol > high:
                volatility = "HIGH_VOL"
                volatility_score = current_vol / max(high, 1e-9) - 1
            elif current_vol < low:
                volatility = "LOW_VOL"
                volatility_score = current_vol / max(low, 1e-9) - 1
            else:
                volatility = "NORMAL_VOL"
                midpoint = (low + high) / 2
                volatility_score = (current_vol - midpoint) / max(high - low, 1e-9)
            confidence = float(
                np.clip(
                    0.55 * abs(trend_score) / 0.10
                    + 0.45 * abs(volatility_score),
                    0.0,
                    1.0,
                )
            )
            bar = ordered[index]
            output.append(
                RegimeState(
                    symbol=bar.symbol,
                    event_time=bar.event_time,
                    available_time=bar.available_time,
                    regime_version=REGIME_VERSION,
                    trend_regime=trend,
                    volatility_regime=volatility,
                    composite_regime=f"{trend}_{volatility}",
                    trend_score=trend_score,
                    volatility_score=volatility_score,
                    confidence=confidence,
                    computed_at=calculated_at,
                )
            )
        return output
