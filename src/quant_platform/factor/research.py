from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import UTC, datetime

import pandas as pd

from quant_platform.domain.entities import (
    FactorResearchResult,
    FeatureDefinition,
    FeatureValue,
    LabelValue,
    RegimeState,
)


RESEARCH_VERSION = "1.0.0"


def _safe_mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _available_by(value: LabelValue, as_of: datetime) -> bool:
    available = value.available_time
    if available.tzinfo is None:
        available = available.replace(tzinfo=UTC)
    return available <= as_of


def _feature_available_by(value: FeatureValue, as_of: datetime) -> bool:
    available = value.available_time
    if available.tzinfo is None:
        available = available.replace(tzinfo=UTC)
    return available <= as_of


class FactorResearchEngine:
    """Evaluates cross-sectional factors against realized, availability-safe labels."""

    version = RESEARCH_VERSION

    def evaluate(
        self,
        market: str,
        definitions: list[FeatureDefinition],
        features: list[FeatureValue],
        labels: list[LabelValue],
        regimes: list[RegimeState],
        as_of: datetime | None = None,
    ) -> list[FactorResearchResult]:
        calculated_at = as_of or datetime.now(UTC)
        feature_frame = pd.DataFrame(
            [
                {
                    "symbol": item.symbol,
                    "event_time": item.event_time,
                    "feature_name": item.feature_name,
                    "feature_version": item.feature_version,
                    "feature_value": item.value,
                }
                for item in features
                if _feature_available_by(item, calculated_at)
            ]
        )
        label_frame = pd.DataFrame(
            [
                {
                    "symbol": item.symbol,
                    "event_time": item.event_time,
                    "label_name": item.label_name,
                    "label_value": item.value,
                }
                for item in labels
                if _available_by(item, calculated_at)
            ]
        )
        if feature_frame.empty or label_frame.empty:
            return []
        label_frame = label_frame.pivot_table(
            index=["symbol", "event_time"],
            columns="label_name",
            values="label_value",
            aggfunc="last",
        ).reset_index()
        regime_by_date = {item.event_time.date(): item.composite_regime for item in regimes}
        definition_names = {item.name for item in definitions}
        results: list[FactorResearchResult] = []
        for (feature_name, feature_version), subset in feature_frame.groupby(
            ["feature_name", "feature_version"]
        ):
            if feature_name not in definition_names:
                continue
            merged = subset.merge(label_frame, on=["symbol", "event_time"], how="inner")
            result = self._evaluate_one(
                market.upper(),
                str(feature_name),
                str(feature_version),
                merged,
                regime_by_date,
                calculated_at,
            )
            if result is not None:
                results.append(result)
        return results

    def _evaluate_one(
        self,
        market: str,
        feature_name: str,
        feature_version: str,
        frame: pd.DataFrame,
        regime_by_date: dict,
        calculated_at: datetime,
    ) -> FactorResearchResult | None:
        daily_5 = self._daily_metrics(frame, "future_return_5d", "excess_return_5d")
        daily_20 = self._daily_metrics(frame, "future_return_20d", None)
        if not daily_5 and not daily_20:
            return None
        rank_5 = [item["rank_ic"] for item in daily_5]
        rank_20 = [item["rank_ic"] for item in daily_20]
        pearson_5 = [item["ic"] for item in daily_5]
        pearson_20 = [item["ic"] for item in daily_20]
        spreads = [item["spread"] for item in daily_5 if item["spread"] is not None]
        turnovers = [item["turnover"] for item in daily_5 if item["turnover"] is not None]
        rank_5_mean = _safe_mean(rank_5)
        rank_20_mean = _safe_mean(rank_20)
        rank_std = statistics.stdev(rank_5) if len(rank_5) > 1 else 0.0
        spread_mean = _safe_mean(spreads)
        annualized_spread = None
        if spread_mean is not None and spread_mean > -1:
            annualized_spread = (1 + spread_mean) ** (252 / 5) - 1
        decay_ratio = None
        if rank_5_mean is not None and rank_20_mean is not None and abs(rank_5_mean) > 1e-9:
            decay_ratio = abs(rank_20_mean) / abs(rank_5_mean)

        by_regime: dict[str, list[float]] = defaultdict(list)
        for item in daily_5:
            event_date = datetime.fromisoformat(item["date"]).date()
            regime = regime_by_date.get(event_date)
            if regime:
                by_regime[regime].append(item["rank_ic"])
        regime_metrics = {
            regime: {"rank_ic_5d": statistics.fmean(values), "cross_sections": len(values)}
            for regime, values in by_regime.items()
        }
        eligible_regimes = {
            regime: metrics
            for regime, metrics in regime_metrics.items()
            if metrics["cross_sections"] >= 5
        }
        best_regime = (
            max(eligible_regimes, key=lambda key: abs(eligible_regimes[key]["rank_ic_5d"]))
            if eligible_regimes
            else None
        )
        return FactorResearchResult(
            market=market,
            feature_name=feature_name,
            feature_version=feature_version,
            research_version=RESEARCH_VERSION,
            cross_sections_5d=len(daily_5),
            cross_sections_20d=len(daily_20),
            mean_ic_5d=_safe_mean(pearson_5),
            rank_ic_5d=rank_5_mean,
            ic_ir_5d=rank_5_mean / rank_std * math.sqrt(252) if rank_5_mean is not None and rank_std else None,
            positive_ic_rate_5d=(sum(value > 0 for value in rank_5) / len(rank_5) if rank_5 else None),
            mean_ic_20d=_safe_mean(pearson_20),
            rank_ic_20d=rank_20_mean,
            decay_ratio=decay_ratio,
            quantile_spread_5d=spread_mean,
            annualized_spread_5d=annualized_spread,
            turnover=_safe_mean(turnovers),
            best_regime=best_regime,
            best_regime_rank_ic=(eligible_regimes[best_regime]["rank_ic_5d"] if best_regime else None),
            regime_metrics_json=json.dumps(regime_metrics, sort_keys=True),
            daily_metrics_json=json.dumps({"5d": daily_5, "20d": daily_20}, sort_keys=True),
            computed_at=calculated_at,
        )

    @staticmethod
    def _daily_metrics(
        frame: pd.DataFrame, label_name: str, spread_label: str | None
    ) -> list[dict[str, object]]:
        if label_name not in frame.columns:
            return []
        output: list[dict[str, object]] = []
        previous_top: set[str] | None = None
        for event_time, group in frame.groupby("event_time"):
            required = ["symbol", "feature_value", label_name]
            if spread_label and spread_label in group.columns:
                required.append(spread_label)
            clean = group[required].dropna()
            if len(clean) < 3 or clean["feature_value"].nunique() < 2 or clean[label_name].nunique() < 2:
                continue
            ic = float(clean["feature_value"].corr(clean[label_name]))
            rank_ic = float(clean["feature_value"].rank().corr(clean[label_name].rank()))
            if not math.isfinite(ic) or not math.isfinite(rank_ic):
                continue
            ordered = clean.sort_values("feature_value")
            quantile_size = max(1, math.ceil(len(ordered) * 0.20))
            bottom = ordered.head(quantile_size)
            top = ordered.tail(quantile_size)
            spread_column = spread_label if spread_label and spread_label in clean.columns else label_name
            spread = float(top[spread_column].mean() - bottom[spread_column].mean())
            current_top = set(top["symbol"])
            turnover = (
                1 - len(current_top & previous_top) / max(len(current_top), 1)
                if previous_top is not None
                else None
            )
            previous_top = current_top
            output.append(
                {
                    "date": event_time.date().isoformat(),
                    "n": int(len(clean)),
                    "ic": ic,
                    "rank_ic": rank_ic,
                    "spread": spread,
                    "turnover": turnover,
                }
            )
        return output
