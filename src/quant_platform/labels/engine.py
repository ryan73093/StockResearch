from __future__ import annotations

from datetime import UTC, datetime

from quant_platform.domain.entities import LabelValue, MarketBar


LABEL_VERSION = "1.0.0"
LABEL_DEFINITIONS: tuple[dict[str, object], ...] = (
    {"name": "future_return_5d", "horizon": 5, "description": "Forward five-session return"},
    {"name": "future_return_20d", "horizon": 20, "description": "Forward twenty-session return"},
    {"name": "direction_5d", "horizon": 5, "description": "One when the five-session return is positive"},
    {"name": "excess_return_5d", "horizon": 5, "description": "Five-session return less benchmark return"},
)


class LabelEngine:
    """Builds labels whose available_time is the future observation that reveals them."""

    definitions = LABEL_DEFINITIONS

    def compute(
        self,
        bars: list[MarketBar],
        benchmark_bars: list[MarketBar] | None = None,
        computed_at: datetime | None = None,
    ) -> list[LabelValue]:
        if not bars:
            return []
        calculated_at = computed_at or datetime.now(UTC)
        ordered = sorted(bars, key=lambda item: item.event_time)
        benchmark_by_time = {
            item.event_time: item for item in sorted(benchmark_bars or [], key=lambda item: item.event_time)
        }
        output: list[LabelValue] = []
        for index, current in enumerate(ordered):
            for horizon in (5, 20):
                target_index = index + horizon
                if target_index >= len(ordered):
                    continue
                target = ordered[target_index]
                current_close = float(current.adjusted_close or current.close)
                target_close = float(target.adjusted_close or target.close)
                future_return = target_close / current_close - 1
                output.append(
                    self._value(current, target.available_time, calculated_at, f"future_return_{horizon}d", future_return)
                )
                if horizon == 5:
                    output.append(
                        self._value(current, target.available_time, calculated_at, "direction_5d", float(future_return > 0))
                    )
                    benchmark_current = benchmark_by_time.get(current.event_time)
                    benchmark_target = benchmark_by_time.get(target.event_time)
                    if benchmark_current is not None and benchmark_target is not None:
                        benchmark_return = (
                            float(benchmark_target.adjusted_close or benchmark_target.close)
                            / float(benchmark_current.adjusted_close or benchmark_current.close)
                            - 1
                        )
                        available_time = max(target.available_time, benchmark_target.available_time)
                        output.append(
                            self._value(
                                current,
                                available_time,
                                calculated_at,
                                "excess_return_5d",
                                future_return - benchmark_return,
                            )
                        )
        return output

    @staticmethod
    def _value(
        current: MarketBar,
        available_time: datetime,
        computed_at: datetime,
        name: str,
        value: float,
    ) -> LabelValue:
        return LabelValue(
            symbol=current.symbol,
            label_name=name,
            label_version=LABEL_VERSION,
            event_time=current.event_time,
            available_time=available_time,
            computed_at=computed_at,
            value=value,
        )
