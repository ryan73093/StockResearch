from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

import numpy as np

from quant_platform.application.ports import (
    BacktestResearchRepository,
    DailyDecisionRepository,
    EnsembleResearchRepository,
    FeatureLabelStoreRepository,
    MarketBarRepository,
    ModelResearchRepository,
    PortfolioResearchRepository,
    RegimeFactorResearchRepository,
    ResearchUniverseRepository,
    SchedulerJobRunRepository,
)
from quant_platform.domain.entities import DailyDecision, JobRunStatus
from quant_platform.application.stock_refresh import StockRefreshState


DECISION_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class DecisionPipelineResult:
    run_id: int
    market: str
    status: str
    decision_count: int
    observe_count: int
    avoid_count: int
    insufficient_count: int


@dataclass(frozen=True, slots=True)
class DecisionView:
    decision: DailyDecision
    reasons: tuple[str, ...]
    risks: tuple[str, ...]
    gate_checks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DecisionOverview:
    decisions: tuple[DecisionView, ...]
    decision_count: int
    observe_count: int
    avoid_count: int
    insufficient_count: int
    candidate_count: int
    market_count: int


class DecisionOverviewService:
    def __init__(self, repository: DailyDecisionRepository) -> None:
        self._repository = repository

    def get_overview(self, market: str | None = None) -> DecisionOverview:
        decisions = self._repository.list_latest(market)
        views = tuple(
            DecisionView(
                decision=item,
                reasons=tuple(json.loads(item.reasons_json)),
                risks=tuple(json.loads(item.risks_json)),
                gate_checks=tuple(json.loads(item.gate_checks_json)),
            )
            for item in decisions
        )
        return DecisionOverview(
            decisions=views,
            decision_count=len(views),
            observe_count=sum(item.decision.status == "觀察" for item in views),
            avoid_count=sum(item.decision.status == "避免" for item in views),
            insufficient_count=sum(item.decision.status == "資料不足" for item in views),
            candidate_count=sum(item.decision.status == "候選" for item in views),
            market_count=len({item.decision.market for item in views}),
        )


class DailyDecisionPipeline:
    """Aggregates model, feature, regime, strategy and portfolio evidence without bypassing gates."""

    def __init__(
        self,
        universe_repository: ResearchUniverseRepository,
        feature_repository: FeatureLabelStoreRepository,
        regime_repository: RegimeFactorResearchRepository,
        backtest_repository: BacktestResearchRepository,
        ensemble_repository: EnsembleResearchRepository,
        portfolio_repository: PortfolioResearchRepository,
        model_repository: ModelResearchRepository,
        decision_repository: DailyDecisionRepository,
        run_repository: SchedulerJobRunRepository,
    ) -> None:
        self._universe_repository = universe_repository
        self._feature_repository = feature_repository
        self._regime_repository = regime_repository
        self._backtest_repository = backtest_repository
        self._ensemble_repository = ensemble_repository
        self._portfolio_repository = portfolio_repository
        self._model_repository = model_repository
        self._decision_repository = decision_repository
        self._run_repository = run_repository

    def run(self, market: str, now: datetime | None = None) -> DecisionPipelineResult:
        normalized_market = market.upper()
        computed_at = now or datetime.now(UTC)
        run_id = self._run_repository.start("daily_decision_build", normalized_market, computed_at)
        assets = [
            item for item in self._universe_repository.list_active(normalized_market)
            if item.asset_type in {"EQUITY", "ETF"}
        ]
        symbols = [item.symbol for item in assets]
        feature_scores, feature_times = self._factor_scores(symbols)
        predictions = self._prediction_evidence(normalized_market)
        regimes = self._latest_regimes(symbols)
        ensembles = {item.symbol: item for item in self._ensemble_repository.list_runs(normalized_market)}
        portfolio_weights = self._portfolio_weights(normalized_market)
        model_candidate = any(
            item.promotion_gate == "CANDIDATE"
            for item in self._model_repository.list_runs(normalized_market)
        )
        output: list[DailyDecision] = []
        for asset in assets:
            symbol = asset.symbol
            evidence = predictions.get(symbol)
            regime = regimes.get(symbol, "UNKNOWN")
            factor_score = feature_scores.get(symbol, 0.5)
            ensemble = ensembles.get(symbol)
            strategy_score = float(
                np.clip(0.5 + (ensemble.sharpe if ensemble else 0) * 0.12 + (ensemble.excess_to_equal if ensemble else 0), 0, 1)
            )
            predicted_return = evidence[0] if evidence else None
            dispersion = evidence[1] if evidence else None
            model_rank = evidence[2] if evidence else None
            regime_score = 0.65 if regime.startswith("BULL") else 0.25 if regime.startswith("BEAR") else 0.5
            score = (
                0.45 * (model_rank if model_rank is not None else 0.3)
                + 0.25 * factor_score
                + 0.20 * strategy_score
                + 0.10 * regime_score
            )
            gate_checks: list[str] = []
            if not model_candidate:
                gate_checks.append("模型尚未通過候選門檻")
            if ensemble is None or ensemble.promotion_gate != "CANDIDATE":
                gate_checks.append("策略整合尚未通過候選門檻")
            if len(assets) < 30:
                gate_checks.append("股票池少於 30 檔")
            if (
                evidence is not None
                and feature_times.get(symbol) is not None
                and evidence[3] < feature_times[symbol]
            ):
                gate_checks.append("模型預測早於最新收盤快照")
            risks: list[str] = []
            reasons: list[str] = []
            if evidence is None:
                status = "資料不足"
                risks.append("缺少最新模型預測")
            elif predicted_return is not None and predicted_return < 0:
                status = "避免"
                risks.append("模型平均預估未來五日報酬為負")
            elif model_rank is not None and model_rank < 0.35:
                status = "避免"
                risks.append("模型橫斷面排名位於後段")
            elif regime.startswith("BEAR") and "HIGH_VOL" in regime:
                status = "避免"
                risks.append("目前為熊市高波動狀態")
            elif not gate_checks and score >= 0.65:
                status = "候選"
            else:
                status = "觀察"
            if predicted_return is not None:
                reasons.append(f"多模型平均五日預估報酬 {predicted_return:+.2%}")
            reasons.append(f"模型排名 {((model_rank or 0) * 100):.0f} 百分位")
            reasons.append(f"市場狀態：{regime}")
            reasons.append(f"特徵綜合分數 {factor_score:.2f}")
            suggested_weight = portfolio_weights.get(symbol, 0.0) if status == "候選" else 0.0
            evidence_times = [
                value for value in (
                    evidence[3] if evidence else None,
                    feature_times.get(symbol),
                ) if value is not None
            ]
            event_time = max(evidence_times) if evidence_times else computed_at
            output.append(
                DailyDecision(
                    id=None,
                    market=normalized_market,
                    symbol=symbol,
                    decision_version=DECISION_VERSION,
                    event_time=event_time,
                    status=status,
                    score=float(score),
                    predicted_return_5d=predicted_return,
                    prediction_dispersion=dispersion,
                    model_rank=model_rank,
                    regime=regime,
                    factor_score=factor_score,
                    strategy_score=strategy_score,
                    suggested_weight=suggested_weight,
                    reasons_json=json.dumps(reasons, ensure_ascii=False),
                    risks_json=json.dumps(risks, ensure_ascii=False),
                    gate_checks_json=json.dumps(gate_checks, ensure_ascii=False),
                    computed_at=computed_at,
                )
            )
        self._decision_repository.upsert(output)
        completed = datetime.now(UTC)
        result = DecisionPipelineResult(
            run_id=run_id,
            market=normalized_market,
            status=JobRunStatus.SUCCEEDED.value,
            decision_count=len(output),
            observe_count=sum(item.status == "觀察" for item in output),
            avoid_count=sum(item.status == "避免" for item in output),
            insufficient_count=sum(item.status == "資料不足" for item in output),
        )
        self._run_repository.finish(
            run_id, JobRunStatus.SUCCEEDED.value, completed,
            json.dumps(result.__dict__ if hasattr(result, "__dict__") else {
                "market": result.market, "decision_count": result.decision_count,
                "observe_count": result.observe_count, "avoid_count": result.avoid_count,
                "insufficient_count": result.insufficient_count,
            }, ensure_ascii=False), None,
        )
        return result

    def _prediction_evidence(
        self, market: str
    ) -> dict[str, tuple[float, float, float, datetime]]:
        grouped: dict[str, list] = {}
        for item in self._model_repository.list_predictions(market):
            grouped.setdefault(item.symbol, []).append(item)
        output: dict[str, tuple[float, float, float, datetime]] = {}
        for symbol, values in grouped.items():
            latest_time = max(item.event_time for item in values)
            latest = [item for item in values if item.event_time == latest_time]
            predicted = np.array([item.predicted_value for item in latest])
            ranks = np.array([item.rank_score for item in latest])
            output[symbol] = (
                float(predicted.mean()), float(predicted.std()),
                float(ranks.mean()), latest_time,
            )
        return output

    def _factor_scores(
        self, symbols: list[str]
    ) -> tuple[dict[str, float], dict[str, datetime]]:
        names = ["momentum_60d", "return_5d", "volatility_20d", "drawdown_252d"]
        latest: dict[tuple[str, str], object] = {}
        for item in self._feature_repository.list_features(symbols, names):
            key = (item.symbol, item.feature_name)
            if key not in latest or item.event_time > latest[key].event_time:
                latest[key] = item
        raw: dict[str, float] = {}
        event_times: dict[str, datetime] = {}
        for symbol in symbols:
            values = {name: latest.get((symbol, name)) for name in names}
            available_times = [item.event_time for item in values.values() if item]
            if available_times:
                event_times[symbol] = max(available_times)
            raw[symbol] = (
                (values["momentum_60d"].value if values["momentum_60d"] else 0)
                + (values["return_5d"].value if values["return_5d"] else 0)
                - 0.2 * (values["volatility_20d"].value if values["volatility_20d"] else 0)
                + 0.5 * (values["drawdown_252d"].value if values["drawdown_252d"] else 0)
            )
        ordered = sorted(raw, key=raw.get)
        return (
            {
                symbol: index / max(len(ordered) - 1, 1)
                for index, symbol in enumerate(ordered)
            },
            event_times,
        )

    def _latest_regimes(self, symbols: list[str]) -> dict[str, str]:
        output: dict[str, str] = {}
        for symbol in symbols:
            values = self._regime_repository.list_regimes(symbol)
            if values:
                output[symbol] = max(values, key=lambda item: item.event_time).composite_regime
        return output

    def _portfolio_weights(self, market: str) -> dict[str, float]:
        runs = self._portfolio_repository.list_runs(market)
        if not runs:
            return {}
        return {key: float(value) for key, value in json.loads(runs[0].latest_weights_json).items()}


@dataclass(frozen=True, slots=True)
class StockResearchView:
    symbol: str
    asset: object | None
    candle_dates: tuple[str, ...]
    candle_data: tuple[tuple[float, float, float, float], ...]
    volumes: tuple[int, ...]
    latest_features: tuple[tuple[str, float], ...]
    regime: object | None
    backtests: tuple[object, ...]
    ensemble: object | None
    predictions: tuple[object, ...]
    decision: DailyDecision | None
    technical_analysis: "TechnicalAnalysisSummary"
    auto_refresh_attempted: bool = False
    auto_refresh_state: str | None = None
    auto_refresh_error: str | None = None


@dataclass(frozen=True, slots=True)
class TechnicalEvidence:
    name: str
    signal: str
    value: str
    explanation: str


@dataclass(frozen=True, slots=True)
class TechnicalAnalysisSummary:
    stance: str
    score: float
    confidence: float
    evidence: tuple[TechnicalEvidence, ...]
    risk_notes: tuple[str, ...]
    market_context: tuple[str, ...]


class StockResearchService:
    def __init__(
        self,
        universe_repository: ResearchUniverseRepository,
        market_bar_repository: MarketBarRepository,
        feature_repository: FeatureLabelStoreRepository,
        regime_repository: RegimeFactorResearchRepository,
        backtest_repository: BacktestResearchRepository,
        ensemble_repository: EnsembleResearchRepository,
        model_repository: ModelResearchRepository,
        decision_repository: DailyDecisionRepository,
        missing_data_loader: Callable[[str], None] | None = None,
        refresh_status_loader: Callable[[str], StockRefreshState | None] | None = None,
    ) -> None:
        self._universe_repository = universe_repository
        self._market_bar_repository = market_bar_repository
        self._feature_repository = feature_repository
        self._regime_repository = regime_repository
        self._backtest_repository = backtest_repository
        self._ensemble_repository = ensemble_repository
        self._model_repository = model_repository
        self._decision_repository = decision_repository
        self._missing_data_loader = missing_data_loader
        self._refresh_status_loader = refresh_status_loader

    def refresh_status(self, raw_symbol: str) -> dict[str, object]:
        symbol = raw_symbol.strip().upper()
        if symbol.isdigit():
            symbol = f"{symbol}.TW"
        refresh = (
            self._refresh_status_loader(symbol)
            if self._refresh_status_loader is not None
            else None
        )
        return {
            "symbol": symbol,
            "state": refresh.state if refresh is not None else "idle",
            "stage": refresh.stage if refresh is not None else "閒置",
            "percent": refresh.percent if refresh is not None else 0,
            "error": refresh.error if refresh is not None else None,
        }

    def get(self, raw_symbol: str) -> StockResearchView:
        symbol = raw_symbol.strip().upper()
        asset = self._universe_repository.get(symbol)
        if asset is None and symbol.isdigit():
            symbol = f"{symbol}.TW"
            asset = self._universe_repository.get(symbol)
        existing_bars = self._market_bar_repository.list_bars(symbol) if asset else []
        existing_features = self._feature_repository.list_features([symbol]) if asset else []
        existing_regimes = self._regime_repository.list_regimes(symbol) if asset else []
        existing_backtests = (
            [
                item
                for item in self._backtest_repository.list_runs(asset.market)
                if item.symbol == symbol
            ]
            if asset
            else []
        )
        existing_predictions = (
            self._model_repository.list_predictions(asset.market, symbol) if asset else []
        )
        auto_refresh_attempted = False
        auto_refresh_state = None
        auto_refresh_error = None
        # 行情存在不代表所有研究產物都已建立。頁面載入時只補「找不到標的」
        # 或「完全沒有行情」的情況；缺少 regime／回測／預測應由背景研究排程
        # 補齊，不能因為一張研究表是空的就同步重跑整個標的。
        needs_refresh = asset is None or not asset.active or not existing_bars
        if needs_refresh and self._missing_data_loader is not None:
            auto_refresh_attempted = True
            try:
                self._missing_data_loader(symbol)
            except Exception as exc:
                auto_refresh_error = str(exc)
            asset = self._universe_repository.get(symbol)
            if asset is not None and not asset.active:
                asset = None
            if asset is not None:
                # 補資料協調器可能在這次請求內完成；只有真的觸發補資料時才重讀。
                existing_bars = self._market_bar_repository.list_bars(symbol)
                existing_features = self._feature_repository.list_features([symbol])
                existing_regimes = self._regime_repository.list_regimes(symbol)
                existing_backtests = [
                    item for item in self._backtest_repository.list_runs(asset.market)
                    if item.symbol == symbol
                ]
                existing_predictions = self._model_repository.list_predictions(
                    asset.market, symbol
                )
        refresh = (
            self._refresh_status_loader(symbol)
            if self._refresh_status_loader is not None
            else None
        )
        if refresh is not None:
            auto_refresh_attempted = True
            auto_refresh_state = refresh.state
            auto_refresh_error = refresh.error
        bars = existing_bars[-240:] if asset else []
        features = existing_features if asset else []
        latest_features: dict[str, object] = {}
        for item in features:
            if item.feature_name not in latest_features or item.event_time > latest_features[item.feature_name].event_time:
                latest_features[item.feature_name] = item
        regimes = existing_regimes if asset else []
        backtests = tuple(existing_backtests) if asset else ()
        ensembles = [item for item in self._ensemble_repository.list_runs(asset.market if asset else None) if item.symbol == symbol]
        predictions = list(existing_predictions) if asset else []
        latest_prediction_time = max((item.event_time for item in predictions), default=None)
        technical_analysis = self._technical_analysis(
            {name: item.value for name, item in latest_features.items()},
            max(regimes, key=lambda item: item.event_time) if regimes else None,
        )
        return StockResearchView(
            symbol=symbol,
            asset=asset,
            candle_dates=tuple(item.event_time.strftime("%Y-%m-%d") for item in bars),
            candle_data=tuple((float(item.open), float(item.close), float(item.low), float(item.high)) for item in bars),
            volumes=tuple(item.volume for item in bars),
            latest_features=tuple(sorted((name, item.value) for name, item in latest_features.items())),
            regime=max(regimes, key=lambda item: item.event_time) if regimes else None,
            backtests=backtests,
            ensemble=ensembles[0] if ensembles else None,
            predictions=tuple(item for item in predictions if item.event_time == latest_prediction_time),
            decision=self._decision_repository.get_latest(symbol),
            technical_analysis=technical_analysis,
            auto_refresh_attempted=auto_refresh_attempted,
            auto_refresh_state=auto_refresh_state,
            auto_refresh_error=auto_refresh_error,
        )

    def _technical_analysis(
        self, features: dict[str, float], regime: object | None
    ) -> TechnicalAnalysisSummary:
        evidence: list[TechnicalEvidence] = []
        scores: list[float] = []

        def add(name: str, feature: str, positive: float, negative: float, formatter: str, positive_text: str, negative_text: str, neutral_text: str) -> None:
            if feature not in features:
                return
            value = features[feature]
            if value >= positive:
                signal, score, explanation = "偏多", 1.0, positive_text
            elif value <= negative:
                signal, score, explanation = "偏空", -1.0, negative_text
            else:
                signal, score, explanation = "中性", 0.0, neutral_text
            scores.append(score)
            evidence.append(TechnicalEvidence(name, signal, formatter.format(value), explanation))

        add("二十日均線", "close_to_sma_20", 0.02, -0.02, "{:+.1%}", "價格明顯站上二十日均線，短期趨勢偏強。", "價格明顯跌破二十日均線，短期趨勢承壓。", "價格靠近二十日均線，方向尚不明確。")
        add("二十日動能", "momentum_20d", 0.05, -0.05, "{:+.1%}", "近二十日報酬偏強，動能延續。", "近二十日報酬偏弱，尚未出現反轉證據。", "近二十日漲跌幅有限，動能中性。")
        add("六十日動能", "momentum_60d", 0.10, -0.10, "{:+.1%}", "中期價格結構向上。", "中期價格結構向下。", "中期趨勢仍在整理。")
        if "rsi_14" in features:
            value = features["rsi_14"]
            if value >= 70:
                signal, score, explanation = "過熱", -0.25, "RSI 進入過熱區，追價風險提高，但不等於立即反轉。"
            elif value <= 30:
                signal, score, explanation = "超賣", 0.25, "RSI 進入超賣區，可能反彈，但仍需趨勢與量能確認。"
            elif value >= 55:
                signal, score, explanation = "偏多", 0.5, "RSI 位於多方區但尚未過熱。"
            elif value <= 45:
                signal, score, explanation = "偏空", -0.5, "RSI 位於弱勢區。"
            else:
                signal, score, explanation = "中性", 0.0, "RSI 位於中性區。"
            scores.append(score)
            evidence.append(TechnicalEvidence("十四日 RSI", signal, f"{value:.1f}", explanation))
        add("成交量", "volume_zscore_20", 1.0, -1.0, "{:+.2f}σ", "成交量顯著高於二十日平均，市場參與度增加。", "成交量明顯偏低，訊號可信度需打折。", "成交量接近近期平均。")
        add("距一年高點", "drawdown_252d", -0.03, -0.20, "{:+.1%}", "價格接近一年高點，趨勢結構仍強。", "距一年高點回撤超過二成，風險結構偏弱。", "處於年度區間中段。")
        risk_notes: list[str] = []
        volatility = features.get("volatility_20d")
        if volatility is not None and volatility > 0.35:
            risk_notes.append(f"二十日年化波動 {volatility:.1%}，屬高波動，部位應下調。")
        atr = features.get("atr_14_pct")
        if atr is not None and atr > 0.035:
            risk_notes.append(f"平均真實波幅約股價 {atr:.1%}，停損距離不能設得過窄。")
        regime_name = getattr(regime, "composite_regime", "UNKNOWN")
        if str(regime_name).startswith("BEAR"):
            scores.append(-1.0)
            risk_notes.append("市場狀態屬空頭，個股偏多訊號需降低權重。")
        elif str(regime_name).startswith("BULL"):
            scores.append(1.0)
        score = float(np.mean(scores)) if scores else 0.0
        stance = "技術面偏多" if score >= 0.25 else "技術面偏空" if score <= -0.25 else "技術面中性"
        context = self._cross_asset_context()
        return TechnicalAnalysisSummary(
            stance, score, min(len(evidence) / 6, 1.0), tuple(evidence),
            tuple(risk_notes or ["目前未偵測到額外的高波動技術風險。"]), context,
        )

    def _cross_asset_context(self) -> tuple[str, ...]:
        labels = {"^GSPC": "標普500", "^SOX": "費城半導體", "^TWII": "台灣加權", "GC=F": "黃金", "BZ=F": "布蘭特原油", "TWD=X": "美元兌台幣"}
        output: list[str] = []
        vix = self._market_bar_repository.list_bars("^VIX")
        if vix:
            output.append(f"VIX 最新 {float(vix[-1].close):.1f}，" + ("市場避險情緒偏高。" if float(vix[-1].close) >= 25 else "市場恐慌程度未達高檔。"))
        for symbol, label in labels.items():
            bars = self._market_bar_repository.list_bars(symbol)
            if len(bars) >= 21:
                change = float(bars[-1].close / bars[-21].close - 1)
                output.append(f"{label}近二十日 {change:+.1%}。")
        return tuple(output)
