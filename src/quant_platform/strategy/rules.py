from __future__ import annotations

import pandas as pd


class TrendMomentumStrategy:
    name = "trend_momentum"
    version = "1.0.0"
    warmup = 100
    parameter_grid = (
        {"fast": 20, "slow": 60, "momentum": 20},
        {"fast": 20, "slow": 100, "momentum": 20},
        {"fast": 50, "slow": 100, "momentum": 60},
    )

    def generate(self, frame, parameters, regimes_by_date) -> pd.Series:
        close = frame["close"]
        fast = close.rolling(int(parameters["fast"]), min_periods=int(parameters["fast"])).mean()
        slow = close.rolling(int(parameters["slow"]), min_periods=int(parameters["slow"])).mean()
        momentum = close.pct_change(int(parameters["momentum"]), fill_method=None)
        return ((fast > slow) & (momentum > 0)).astype(float)


class MeanReversionStrategy:
    name = "mean_reversion"
    version = "1.0.0"
    warmup = 30
    parameter_grid = (
        {"lookback": 10, "entry_z": -1.0, "exit_z": 0.0},
        {"lookback": 20, "entry_z": -1.0, "exit_z": 0.0},
        {"lookback": 20, "entry_z": -1.5, "exit_z": 0.5},
    )

    def generate(self, frame, parameters, regimes_by_date) -> pd.Series:
        lookback = int(parameters["lookback"])
        close = frame["close"]
        mean = close.rolling(lookback, min_periods=lookback).mean()
        deviation = close.rolling(lookback, min_periods=lookback).std(ddof=0)
        zscore = (close - mean) / deviation.replace(0, float("nan"))
        position = 0.0
        output: list[float] = []
        for value in zscore:
            if pd.isna(value):
                position = 0.0
            elif position == 0 and value <= float(parameters["entry_z"]):
                position = 1.0
            elif position > 0 and value >= float(parameters["exit_z"]):
                position = 0.0
            output.append(position)
        return pd.Series(output, index=frame.index, dtype=float)


class RegimeAwareStrategy:
    name = "regime_aware"
    version = "1.0.0"
    warmup = 100
    parameter_grid = (
        {"fast": 20, "slow": 60, "range_lookback": 10, "range_entry_z": -1.0},
        {"fast": 20, "slow": 100, "range_lookback": 20, "range_entry_z": -1.0},
        {"fast": 50, "slow": 100, "range_lookback": 20, "range_entry_z": -1.5},
    )

    def generate(self, frame, parameters, regimes_by_date) -> pd.Series:
        close = frame["close"]
        fast_window = int(parameters["fast"])
        slow_window = int(parameters["slow"])
        lookback = int(parameters["range_lookback"])
        fast = close.rolling(fast_window, min_periods=fast_window).mean()
        slow = close.rolling(slow_window, min_periods=slow_window).mean()
        mean = close.rolling(lookback, min_periods=lookback).mean()
        deviation = close.rolling(lookback, min_periods=lookback).std(ddof=0)
        zscore = (close - mean) / deviation.replace(0, float("nan"))
        output: list[float] = []
        range_position = 0.0
        for index, event_time in enumerate(frame["event_time"]):
            regime = regimes_by_date.get(event_time.date(), "UNKNOWN")
            if regime.startswith("BULL"):
                range_position = 0.0
                position = float(fast.iloc[index] > slow.iloc[index])
            elif regime.startswith("RANGE"):
                value = zscore.iloc[index]
                if pd.isna(value):
                    range_position = 0.0
                elif range_position == 0 and value <= float(parameters["range_entry_z"]):
                    range_position = 1.0
                elif range_position > 0 and value >= 0:
                    range_position = 0.0
                position = range_position
            else:
                range_position = 0.0
                position = 0.0
            output.append(position)
        return pd.Series(output, index=frame.index, dtype=float)


DEFAULT_STRATEGIES = (
    TrendMomentumStrategy(),
    MeanReversionStrategy(),
    RegimeAwareStrategy(),
)
