from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from quant_platform.application.feature_store import FeatureLabelPipeline, FeatureLabelPipelineResult
from quant_platform.application.factor_research import FactorResearchPipeline, FactorResearchPipelineResult
from quant_platform.application.universe import DailyMarketDataPipeline, DailyPipelineResult
from quant_platform.application.backtest_research import BacktestResearchPipeline, BacktestResearchPipelineResult
from quant_platform.application.ensemble_research import EnsemblePipelineResult, EnsembleResearchPipeline
from quant_platform.application.portfolio_research import PortfolioPipelineResult, PortfolioResearchPipeline
from quant_platform.application.model_research import (
    AVAILABLE_MODELS,
    ModelPipelineResult,
    ModelResearchPipeline,
)
from quant_platform.application.decision_support import DecisionPipelineResult, DailyDecisionPipeline
from quant_platform.application.taiwan_data import TaiwanDataPipeline, TaiwanDataPipelineResult
from quant_platform.application.macro_data import MacroDataPipeline, MacroPipelineResult
from quant_platform.application.data_quality import (
    DataQualityService, DataQualitySnapshotView,
)
from quant_platform.application.listing_reconciliation import (
    TaiwanListingReconciliationService,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DailyResearchPipelineResult:
    market: str
    market_data: DailyPipelineResult
    taiwan_data: TaiwanDataPipelineResult
    macro_data: MacroPipelineResult
    raw_data_quality: DataQualitySnapshotView
    feature_store: FeatureLabelPipelineResult
    data_quality: DataQualitySnapshotView
    factor_research: FactorResearchPipelineResult
    backtest_research: BacktestResearchPipelineResult
    strategy_ensemble: EnsemblePipelineResult
    portfolio_risk: PortfolioPipelineResult
    model_research: ModelPipelineResult
    daily_decision: DecisionPipelineResult


class DailyResearchPipeline:
    """Coordinates independent daily modules without coupling their implementations."""

    def __init__(
        self,
        market_data_pipeline: DailyMarketDataPipeline,
        taiwan_data_pipeline: TaiwanDataPipeline,
        macro_data_pipeline: MacroDataPipeline,
        data_quality_service: DataQualityService,
        feature_label_pipeline: FeatureLabelPipeline,
        factor_research_pipeline: FactorResearchPipeline,
        backtest_research_pipeline: BacktestResearchPipeline,
        ensemble_research_pipeline: EnsembleResearchPipeline,
        portfolio_research_pipeline: PortfolioResearchPipeline,
        model_research_pipeline: ModelResearchPipeline,
        daily_decision_pipeline: DailyDecisionPipeline,
        listing_reconciliation: TaiwanListingReconciliationService | None = None,
        legacy_paused: Callable[[], bool] | None = None,
    ) -> None:
        self._listing_reconciliation = listing_reconciliation
        # S9-W04 (2026-10-06): while the legacy research is paused, no caller (API, legacy pages, CLI) may
        # run its steps — they write the SQLite Taiwan data and features the research no longer uses
        self._legacy_paused = legacy_paused
        self._market_data_pipeline = market_data_pipeline
        self._taiwan_data_pipeline = taiwan_data_pipeline
        self._macro_data_pipeline = macro_data_pipeline
        self._data_quality = data_quality_service
        self._feature_label_pipeline = feature_label_pipeline
        self._factor_research_pipeline = factor_research_pipeline
        self._backtest_research_pipeline = backtest_research_pipeline
        self._ensemble_research_pipeline = ensemble_research_pipeline
        self._portfolio_research_pipeline = portfolio_research_pipeline
        self._model_research_pipeline = model_research_pipeline
        self._daily_decision_pipeline = daily_decision_pipeline

    def run(
        self,
        market: str,
        now: datetime | None = None,
        full_refresh: bool = False,
        symbols: list[str] | None = None,
        refresh_macro: bool = True,
        progress_callback: Callable[[str, int], None] | None = None,
        early_decision_callback: Callable[[DecisionPipelineResult], None] | None = None,
        legacy_research: bool = True,
    ) -> DailyResearchPipelineResult:
        """``legacy_research=False`` (S9-W05): market data, the listing check and the raw data-quality
        snapshot only; the legacy research steps after them are skipped."""
        if legacy_research and self._legacy_paused is not None and self._legacy_paused():
            legacy_research = False
        progress = progress_callback or (lambda _stage, _percent: None)
        progress("抓取日線行情", 5)
        market_result = self._market_data_pipeline.run(
            market, now=now, full_refresh=full_refresh, symbols=symbols
        )
        if market_result is not None and not getattr(market_result, "fresh", True):
            raise RuntimeError(
                f"{market.upper()} 行情尚未更新到 {market_result.expected_date}；"
                f"目前只有 {market_result.data_date or '無資料'}，停止建立過期決策"
            )
        if (
            market.upper() == "TW"
            and symbols is None
            and self._listing_reconciliation is not None
        ):
            # Retire delisted equities before the early decision and the data
            # quality gate, so one delisted name cannot block the whole market.
            try:
                self._listing_reconciliation.run(now=now)
            except Exception:
                logger.exception("Taiwan listing reconciliation failed; continuing")
        feature_result: FeatureLabelPipelineResult | None = None
        model_result: ModelPipelineResult | None = None
        if not legacy_research:
            progress("檢查原始資料品質", 60)
            raw_quality = self._data_quality.evaluate(market, as_of=now, stage="raw")
            progress("完成（舊版研究流程已暫停）", 100)
            return DailyResearchPipelineResult(
                market=market.upper(), market_data=market_result, taiwan_data=None, macro_data=None,
                raw_data_quality=raw_quality, feature_store=None, data_quality=raw_quality, factor_research=None,
                backtest_research=None, strategy_ensemble=None, portfolio_risk=None, model_research=None,
                daily_decision=None,
            )
        if early_decision_callback is not None:
            # The after-hours order window is time-limited. Today's price
            # features and preliminary decision must not wait for slower
            # auxiliary/news/fundamental providers.
            progress("建立今日價格特徵", 12)
            if symbols is None:
                feature_result = self._feature_label_pipeline.run(market, now=now)
            else:
                feature_result = self._feature_label_pipeline.run(
                    market, now=now, symbols=symbols
                )
            if market.upper() == "TW" and "torch_cuda_mlp" in AVAILABLE_MODELS:
                progress("使用 GPU 更新今日模型預測", 16)
                model_result = self._model_research_pipeline.run(
                    market,
                    now=now,
                    model_names=("torch_cuda_mlp",),
                    feature_profile="price_core",
                )
            progress("產生盤後零股初步決策", 20)
            early_decision_callback(
                self._daily_decision_pipeline.run(market, now=now)
            )
        progress("抓取籌碼與基本面", 15)
        taiwan_result = self._taiwan_data_pipeline.run(
            market, now=now, full_refresh=full_refresh, symbols=symbols
        )
        progress("更新總體經濟資料" if refresh_macro else "整理既有總體經濟資料", 25)
        if refresh_macro and symbols is None:
            macro_result = self._macro_data_pipeline.run(now=now)
        else:
            macro_result = self._macro_data_pipeline.run(
                now=now, download=refresh_macro, symbols=symbols
            )
        progress("檢查原始資料品質", 35)
        raw_quality = self._data_quality.evaluate(market, as_of=now, stage="raw")
        self._data_quality.ensure_research_ready(raw_quality)
        progress("建立研究特徵", 42)
        if feature_result is None:
            if symbols is None:
                feature_result = self._feature_label_pipeline.run(market, now=now)
            else:
                feature_result = self._feature_label_pipeline.run(
                    market, now=now, symbols=symbols
                )
        progress("檢查完整資料品質", 52)
        quality = self._data_quality.evaluate(market, as_of=now, stage="full")
        self._data_quality.ensure_research_ready(quality)
        progress("訓練與預測模型", 58)
        if model_result is None:
            model_result = self._model_research_pipeline.run(market, now=now)
        progress("研究市場狀態與因子", 72)
        factor_result = self._factor_research_pipeline.run(market, now=now)
        progress("執行樣本外回測", 82)
        backtest_result = self._backtest_research_pipeline.run(market, now=now)
        progress("整合策略", 92)
        ensemble_result = self._ensemble_research_pipeline.run(market, now=now)
        progress("計算投資組合風險", 95)
        portfolio_result = self._portfolio_research_pipeline.run(market, now=now)
        progress("建立每日決策", 98)
        decision_result = self._daily_decision_pipeline.run(market, now=now)
        progress("完成", 100)
        return DailyResearchPipelineResult(
            market=market.upper(),
            market_data=market_result,
            taiwan_data=taiwan_result,
            macro_data=macro_result,
            raw_data_quality=raw_quality,
            feature_store=feature_result,
            data_quality=quality,
            factor_research=factor_result,
            backtest_research=backtest_result,
            strategy_ensemble=ensemble_result,
            portfolio_risk=portfolio_result,
            model_research=model_result,
            daily_decision=decision_result,
        )
