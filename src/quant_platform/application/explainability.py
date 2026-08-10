from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from quant_platform.application.ports import (
    DailyDecisionRepository,
    ModelResearchRepository,
    ResearchUniverseRepository,
)
from quant_platform.domain.entities import DailyDecision, ModelExplanation


FEATURE_FAMILIES = {
    "return_1d": "價格與動能", "return_5d": "價格與動能",
    "momentum_20d": "價格與動能", "momentum_60d": "價格與動能",
    "close_to_sma_20": "價格與動能", "rsi_14": "價格與動能",
    "atr_14_pct": "波動與風險", "volatility_20d": "波動與風險",
    "drawdown_252d": "波動與風險", "volume_zscore_20": "成交量",
    "day_of_week": "時間效應", "institutional_net_buy": "法人與籌碼",
    "margin_purchase_balance": "法人與籌碼", "short_sale_balance": "法人與籌碼",
    "securities_lending_quantity": "法人與籌碼", "pe_ratio": "估值",
    "pb_ratio": "估值", "dividend_yield": "估值", "monthly_revenue": "基本面",
    "quarterly_eps": "基本面", "roe_annualized": "基本面",
    "roa_annualized": "基本面", "gross_margin": "基本面",
    "free_cash_flow": "基本面", "free_cash_flow_margin": "基本面",
}


@dataclass(frozen=True, slots=True)
class ContributionView:
    feature_name: str
    family: str
    contribution: float
    direction: str


@dataclass(frozen=True, slots=True)
class ModelExplanationView:
    explanation: ModelExplanation
    contributions: tuple[ContributionView, ...]
    failed_gates: tuple[str, ...]
    permutation_importance: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class ExplainabilityOverview:
    symbol: str
    asset: object | None
    decision: DailyDecision | None
    models: tuple[ModelExplanationView, ...]
    positive_evidence: tuple[ContributionView, ...]
    negative_evidence: tuple[ContributionView, ...]
    family_contributions: tuple[tuple[str, float], ...]
    prediction_mean: float | None
    prediction_dispersion: float | None
    direction_agreement: float | None
    latest_data_available: datetime | None
    warning_count: int


class ExplainabilityService:
    def __init__(
        self,
        universe_repository: ResearchUniverseRepository,
        model_repository: ModelResearchRepository,
        decision_repository: DailyDecisionRepository,
    ) -> None:
        self._universe_repository = universe_repository
        self._model_repository = model_repository
        self._decision_repository = decision_repository

    def get(self, raw_symbol: str) -> ExplainabilityOverview:
        symbol = raw_symbol.strip().upper()
        asset = self._universe_repository.get(symbol)
        if asset is None and symbol.isdigit():
            symbol = f"{symbol}.TW"
            asset = self._universe_repository.get(symbol)
        explanations = self._model_repository.list_explanations(
            asset.market if asset else None, symbol
        )
        latest: dict[str, ModelExplanation] = {}
        for item in explanations:
            previous = latest.get(item.model_name)
            if previous is None or (item.computed_at, item.event_time) > (
                previous.computed_at,
                previous.event_time,
            ):
                latest[item.model_name] = item
        model_views: list[ModelExplanationView] = []
        aggregate: dict[str, list[float]] = {}
        warnings: set[str] = set()
        for item in latest.values():
            raw_contributions = json.loads(item.contributions_json)
            contributions = tuple(
                ContributionView(
                    feature_name=name,
                    family=FEATURE_FAMILIES.get(name, "其他"),
                    contribution=float(value),
                    direction="正向" if value > 0 else "負向" if value < 0 else "中性",
                )
                for name, value in sorted(
                    raw_contributions.items(), key=lambda value: abs(value[1]), reverse=True
                )
            )
            experiment = self._model_repository.get(item.experiment_id)
            failed_gates = tuple(json.loads(experiment.limitations_json)) if experiment else ()
            warnings.update(failed_gates)
            importance = json.loads(experiment.feature_importance_json) if experiment else {}
            model_views.append(
                ModelExplanationView(
                    explanation=item,
                    contributions=contributions,
                    failed_gates=failed_gates,
                    permutation_importance=tuple(
                        sorted(importance.items(), key=lambda value: value[1], reverse=True)
                    ),
                )
            )
            for contribution in contributions:
                aggregate.setdefault(contribution.feature_name, []).append(contribution.contribution)
        averaged = [
            ContributionView(
                feature_name=name,
                family=FEATURE_FAMILIES.get(name, "其他"),
                contribution=float(np.mean(values)),
                direction="正向" if np.mean(values) > 0 else "負向" if np.mean(values) < 0 else "中性",
            )
            for name, values in aggregate.items()
        ]
        family: dict[str, float] = {}
        for item in averaged:
            family[item.family] = family.get(item.family, 0.0) + item.contribution
        predictions = np.array([item.explanation.predicted_value for item in model_views])
        agreement = None
        if len(predictions):
            positive = float(np.mean(predictions >= 0))
            agreement = max(positive, 1 - positive)
        return ExplainabilityOverview(
            symbol=symbol,
            asset=asset,
            decision=self._decision_repository.get_latest(symbol),
            models=tuple(sorted(model_views, key=lambda item: item.explanation.model_name)),
            positive_evidence=tuple(
                sorted((item for item in averaged if item.contribution > 0), key=lambda item: item.contribution, reverse=True)[:8]
            ),
            negative_evidence=tuple(
                sorted((item for item in averaged if item.contribution < 0), key=lambda item: item.contribution)[:8]
            ),
            family_contributions=tuple(sorted(family.items(), key=lambda item: abs(item[1]), reverse=True)),
            prediction_mean=float(predictions.mean()) if len(predictions) else None,
            prediction_dispersion=float(predictions.std()) if len(predictions) else None,
            direction_agreement=agreement,
            latest_data_available=max(
                (item.explanation.data_available_time for item in model_views), default=None
            ),
            warning_count=len(warnings),
        )
