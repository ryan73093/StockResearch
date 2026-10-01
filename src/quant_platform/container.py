from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable

from quant_platform import __version__
from quant_platform.market_calendar import MarketCalendarStore, TwseHolidayScheduleClient
from quant_platform.application.listing_reconciliation import TaiwanListingReconciliationService
from quant_platform.application.close_availability import CloseAvailabilityProbe
from quant_platform.application.actual_account import ActualAccountService
from quant_platform.application.investment_plan import InvestmentPlanService
from quant_platform.application.notifications import NotificationService
from quant_platform.application.plan_decision import PlanDecisionService
from quant_platform.research.promotion import strategy_catalog
from quant_platform.application.database_backup import DatabaseBackupService
from quant_platform.application.prediction_archive import (
    PredictionArchiveService,
    active_registry_experiment_ids,
)
from quant_platform.application.services import (
    HealthService,
    MarketDataIngestionService,
    MarketDataOverviewService,
    ResearchOverviewService,
)
from quant_platform.application.analytics import QuantAnalyticsService
from quant_platform.application.feature_store import FeatureLabelPipeline, FeatureStoreOverviewService
from quant_platform.application.factor_research import FactorResearchOverviewService, FactorResearchPipeline
from quant_platform.application.backtest_research import BacktestResearchOverviewService, BacktestResearchPipeline
from quant_platform.application.ensemble_research import EnsembleOverviewService, EnsembleResearchPipeline
from quant_platform.application.portfolio_research import PortfolioOverviewService, PortfolioResearchPipeline
from quant_platform.application.model_research import ModelResearchOverviewService, ModelResearchPipeline
from quant_platform.application.model_training import ModelTrainingCoordinator
from quant_platform.application.decision_support import (
    DailyDecisionPipeline,
    DecisionOverviewService,
    StockResearchService,
)
from quant_platform.application.research_pipeline import DailyResearchPipeline
from quant_platform.application.stock_refresh import StockResearchRefreshCoordinator
from quant_platform.application.taiwan_data import TaiwanDataOverviewService, TaiwanDataPipeline
from quant_platform.application.explainability import ExplainabilityService
from quant_platform.application.macro_data import MacroDataOverviewService, MacroDataPipeline
from quant_platform.application.odd_lot_research import OddLotResearchService
from quant_platform.application.universe_history import UniverseHistoryService
from quant_platform.application.news_research import NewsResearchService
from quant_platform.application.daily_report import DailyReportKnowledgeService
from quant_platform.application.automation import AutomationService, SmtpEmailNotifier
from quant_platform.application.authentication import AuthenticationService, GoogleOidcClient
from quant_platform.application.paper_trading import PaperTradingService
from quant_platform.application.after_hours_ai import AfterHoursAiService
from quant_platform.application.universe_expansion import UniverseExpansionService
from quant_platform.application.shadow_trading import ShadowTradingService
from quant_platform.application.promotion import PromotionService
from quant_platform.application.model_governance import ModelGovernanceService
from quant_platform.application.data_quality import DataQualityService
from quant_platform.application.point_in_time_data import PointInTimeDataService
from quant_platform.application.google_trends import GoogleTrendsCsvImportService
from quant_platform.application.intraday_features import IntradayDerivativeFeaturePipeline
from quant_platform.application.corporate_events import EarningsCallService
from quant_platform.application.failure_memory import ResearchFailureMemoryService
from quant_platform.reinforcement_learning import RlEnvironmentService
from quant_platform.application.universe import DailyMarketDataPipeline, ResearchUniverseService
from quant_platform.config import Settings, get_settings
from quant_platform.database import Database
from quant_platform.data_sources import (
    FinMindProvider, FinMindPointInTimeProvider, FredCsvProvider,
    MopsEarningsCallProvider, PointInTimeProviderRouter, TwseCompanyProvider,
    TwsePointInTimeProvider,
    TaiwanOfficialDailyBarProvider,
    TaiwanOfficialMarketRankingProvider,
    TaiwanOfficialFallbackProvider,
    YahooFinanceProvider,
)
from quant_platform.database.repositories import (
    SqlAlchemyMarketBarRepository,
    SqlAlchemyResearchExperimentRepository,
    SqlAlchemyResearchUniverseRepository,
    SqlAlchemySchedulerJobRunRepository,
    SqlAlchemyFeatureLabelStoreRepository,
    SqlAlchemyActualAccountRepository,
    SqlAlchemyInvestmentPlanRepository,
    SqlAlchemyRegimeFactorResearchRepository,
    SqlAlchemyBacktestResearchRepository,
    SqlAlchemyEnsembleResearchRepository,
    SqlAlchemyPortfolioResearchRepository,
    SqlAlchemyModelResearchRepository,
    SqlAlchemyDailyDecisionRepository,
    SqlAlchemyTaiwanDataRepository,
    SqlAlchemyMacroDataRepository,
    SqlAlchemyResearchKnowledgeRepository,
    SqlAlchemyAutomationRepository,
    SqlAlchemyAuthenticationRepository,
    SqlAlchemyPaperTradingRepository,
    SqlAlchemyShadowTradingRepository,
    SqlAlchemyPromotionRepository,
    SqlAlchemyModelGovernanceRepository,
    SqlAlchemyDataQualityRepository,
    SqlAlchemyPointInTimeDataRepository,
    SqlAlchemyResearchFailureCaseRepository,
)
from quant_platform.backtest import BiasSafeBacktestEngine
from quant_platform.ensemble import DynamicStrategyEnsembleEngine
from quant_platform.portfolio import PORTFOLIO_METHODS, PortfolioRiskEngine
from quant_platform.feature_engineering import (
    CrossAssetFeatureEngine, FeatureEngine, IntradayDerivativeFeatureEngine,
)
from quant_platform.factor import FactorResearchEngine
from quant_platform.labels import LabelEngine
from quant_platform.regime import RegimeDetectionEngine
from quant_platform.runtime.locks import build_execution_lock_manager
from quant_platform.rag import (
    LocalHashEmbeddingProvider,
    OpenAIEmbeddingProvider,
    OpenAIResponsesAnswerProvider,
    SentenceTransformerEmbeddingProvider,
)
from quant_platform.strategy import DEFAULT_STRATEGIES


@dataclass(slots=True)
class Container:
    settings: Settings
    database: Database
    health_service: HealthService
    research_overview_service: ResearchOverviewService
    market_data_ingestion_service: MarketDataIngestionService
    market_data_overview_service: MarketDataOverviewService
    quant_analytics_service: QuantAnalyticsService
    research_universe_service: ResearchUniverseService
    daily_market_data_pipeline: DailyMarketDataPipeline
    feature_label_pipeline: FeatureLabelPipeline
    feature_store_overview_service: FeatureStoreOverviewService
    daily_research_pipeline: DailyResearchPipeline
    factor_research_pipeline: FactorResearchPipeline
    factor_research_overview_service: FactorResearchOverviewService
    backtest_research_pipeline: BacktestResearchPipeline
    backtest_research_overview_service: BacktestResearchOverviewService
    ensemble_research_pipeline: EnsembleResearchPipeline
    ensemble_overview_service: EnsembleOverviewService
    portfolio_research_pipeline: PortfolioResearchPipeline
    portfolio_overview_service: PortfolioOverviewService
    model_research_pipeline: ModelResearchPipeline
    model_research_overview_service: ModelResearchOverviewService
    model_training_coordinator: ModelTrainingCoordinator
    daily_decision_pipeline: DailyDecisionPipeline
    decision_overview_service: DecisionOverviewService
    stock_research_service: StockResearchService
    taiwan_data_pipeline: TaiwanDataPipeline
    taiwan_data_overview_service: TaiwanDataOverviewService
    explainability_service: ExplainabilityService
    macro_data_pipeline: MacroDataPipeline
    macro_data_overview_service: MacroDataOverviewService
    odd_lot_research_service: OddLotResearchService
    universe_history_service: UniverseHistoryService
    news_research_service: NewsResearchService
    daily_report_knowledge_service: DailyReportKnowledgeService
    automation_service: AutomationService
    authentication_service: AuthenticationService
    paper_trading_service: PaperTradingService
    after_hours_ai_service: AfterHoursAiService
    universe_expansion_service: UniverseExpansionService
    rl_environment_service: RlEnvironmentService
    shadow_trading_service: ShadowTradingService
    promotion_service: PromotionService
    model_governance_service: ModelGovernanceService
    data_quality_service: DataQualityService
    point_in_time_data_service: PointInTimeDataService
    google_trends_service: GoogleTrendsCsvImportService
    intraday_derivative_feature_pipeline: IntradayDerivativeFeaturePipeline
    earnings_call_service: EarningsCallService
    research_failure_memory_service: ResearchFailureMemoryService
    market_calendar: MarketCalendarStore
    prediction_archive: PredictionArchiveService
    database_backup: DatabaseBackupService | None
    close_availability: CloseAvailabilityProbe
    investment_plan_service: InvestmentPlanService
    actual_account_service: ActualAccountService
    plan_decision_service: PlanDecisionService
    notification_service: NotificationService


def _sqlite_path(database_url: str) -> Path | None:
    prefix = "sqlite:///"
    if database_url.startswith(prefix) and ":memory:" not in database_url:
        return Path(database_url[len(prefix):].split("?", 1)[0])
    return None


def _instance_dir(database_url: str) -> Path:
    """Runtime files live next to the SQLite database (``instance/`` by default)."""
    path = _sqlite_path(database_url)
    return path.parent if path is not None else Path("instance")


def build_container(settings: Settings | None = None) -> Container:
    resolved = settings or get_settings()
    database = Database(resolved.database_url)
    database.create_schema()
    repository = SqlAlchemyResearchExperimentRepository(database.session_factory)
    failure_case_repository = SqlAlchemyResearchFailureCaseRepository(
        database.session_factory
    )
    market_bar_repository = SqlAlchemyMarketBarRepository(database.session_factory)
    universe_repository = SqlAlchemyResearchUniverseRepository(database.session_factory)
    job_run_repository = SqlAlchemySchedulerJobRunRepository(database.session_factory)
    feature_store_repository = SqlAlchemyFeatureLabelStoreRepository(database.session_factory)
    factor_research_repository = SqlAlchemyRegimeFactorResearchRepository(database.session_factory)
    backtest_repository = SqlAlchemyBacktestResearchRepository(database.session_factory)
    ensemble_repository = SqlAlchemyEnsembleResearchRepository(database.session_factory)
    portfolio_repository = SqlAlchemyPortfolioResearchRepository(database.session_factory)
    model_repository = SqlAlchemyModelResearchRepository(database.session_factory)
    decision_repository = SqlAlchemyDailyDecisionRepository(database.session_factory)
    taiwan_data_repository = SqlAlchemyTaiwanDataRepository(database.session_factory)
    macro_data_repository = SqlAlchemyMacroDataRepository(database.session_factory)
    knowledge_repository = SqlAlchemyResearchKnowledgeRepository(database.session_factory)
    automation_repository = SqlAlchemyAutomationRepository(database.session_factory)
    authentication_repository = SqlAlchemyAuthenticationRepository(database.session_factory)
    paper_trading_repository = SqlAlchemyPaperTradingRepository(database.session_factory)
    shadow_trading_repository = SqlAlchemyShadowTradingRepository(database.session_factory)
    promotion_repository = SqlAlchemyPromotionRepository(database.session_factory)
    model_governance_repository = SqlAlchemyModelGovernanceRepository(database.session_factory)
    data_quality_repository = SqlAlchemyDataQualityRepository(database.session_factory)
    point_in_time_repository = SqlAlchemyPointInTimeDataRepository(database.session_factory)
    lock_manager = build_execution_lock_manager(
        resolved.redis_url, resolved.redis_enabled, resolved.redis_required
    )
    universe_service = ResearchUniverseService(universe_repository, TwseCompanyProvider())
    universe_service.ensure_default_universe()
    finmind_provider = FinMindProvider(
        resolved.finmind_base_url, resolved.finmind_token,
        circuit_state_path="instance/finmind_circuit.json",
    )
    finmind_pit_provider = FinMindPointInTimeProvider(
        resolved.finmind_base_url, resolved.finmind_token
    )
    point_in_time_data_service = PointInTimeDataService(
        point_in_time_repository,
        PointInTimeProviderRouter([
            finmind_pit_provider, TwsePointInTimeProvider(), MopsEarningsCallProvider(),
        ]),
    )
    google_trends_service = GoogleTrendsCsvImportService(point_in_time_repository)
    intraday_derivative_feature_pipeline = IntradayDerivativeFeaturePipeline(
        point_in_time_repository, feature_store_repository, universe_repository,
        job_run_repository, IntradayDerivativeFeatureEngine(),
    )
    earnings_call_service = EarningsCallService(
        point_in_time_data_service, point_in_time_repository
    )
    universe_history_service = UniverseHistoryService(
        universe_repository, finmind_provider, TwseCompanyProvider()
    )
    universe_history_service.ensure_seed_memberships()
    ingestion_service = MarketDataIngestionService(YahooFinanceProvider(), market_bar_repository)
    market_calendar = MarketCalendarStore(
        _instance_dir(resolved.database_url), client=TwseHolidayScheduleClient()
    )
    market_data_pipeline = DailyMarketDataPipeline(
        universe_repository,
        market_bar_repository,
        ingestion_service,
        job_run_repository,
        TaiwanOfficialDailyBarProvider(calendar_store=market_calendar),
        calendar_store=market_calendar,
        official_wait=timedelta(minutes=resolved.tw_official_close_wait_minutes),
    )
    taiwan_data_pipeline = TaiwanDataPipeline(
        universe_repository,
        taiwan_data_repository,
        feature_store_repository,
        job_run_repository,
        TaiwanOfficialFallbackProvider(finmind_provider),
    )
    feature_label_pipeline = FeatureLabelPipeline(
        universe_repository,
        market_bar_repository,
        feature_store_repository,
        job_run_repository,
        FeatureEngine(),
        LabelEngine(),
        CrossAssetFeatureEngine(),
    )
    factor_research_pipeline = FactorResearchPipeline(
        universe_repository,
        market_bar_repository,
        feature_store_repository,
        factor_research_repository,
        job_run_repository,
        RegimeDetectionEngine(),
        FactorResearchEngine(),
    )
    backtest_research_pipeline = BacktestResearchPipeline(
        universe_repository,
        market_bar_repository,
        factor_research_repository,
        backtest_repository,
        job_run_repository,
        universe_history_service,
        BiasSafeBacktestEngine(),
        DEFAULT_STRATEGIES,
    )
    ensemble_research_pipeline = EnsembleResearchPipeline(
        backtest_repository,
        factor_research_repository,
        ensemble_repository,
        job_run_repository,
        DynamicStrategyEnsembleEngine(),
    )
    portfolio_research_pipeline = PortfolioResearchPipeline(
        universe_repository,
        market_bar_repository,
        ensemble_repository,
        portfolio_repository,
        job_run_repository,
        PortfolioRiskEngine(),
        PORTFOLIO_METHODS,
    )
    model_research_pipeline = ModelResearchPipeline(
        universe_repository,
        feature_store_repository,
        model_repository,
        job_run_repository,
    )
    macro_data_pipeline = MacroDataPipeline(
        universe_repository,
        macro_data_repository,
        feature_store_repository,
        job_run_repository,
        FredCsvProvider(),
    )
    daily_decision_pipeline = DailyDecisionPipeline(
        universe_repository,
        feature_store_repository,
        factor_research_repository,
        backtest_repository,
        ensemble_repository,
        portfolio_repository,
        model_repository,
        decision_repository,
        job_run_repository,
    )
    news_research_service = NewsResearchService(taiwan_data_repository)
    data_quality_service = DataQualityService(
        data_quality_repository, market_bar_repository, universe_repository,
        feature_store_repository, taiwan_data_repository, macro_data_repository,
        point_in_time_repository,
        calendar_store=market_calendar,
    )
    listing_reconciliation = TaiwanListingReconciliationService(
        universe_repository, market_bar_repository, TwseCompanyProvider(),
        job_run_repository, calendar_store=market_calendar,
    )
    daily_research_pipeline = DailyResearchPipeline(
        market_data_pipeline, taiwan_data_pipeline, macro_data_pipeline, data_quality_service,
        feature_label_pipeline, factor_research_pipeline, backtest_research_pipeline,
        ensemble_research_pipeline, portfolio_research_pipeline,
        model_research_pipeline, daily_decision_pipeline,
        listing_reconciliation=listing_reconciliation,
    )

    def load_missing_stock_research(
        symbol: str,
        progress: Callable[[str, int], None],
    ) -> None:
        normalized = symbol.strip().upper()
        is_taiwan = normalized.endswith((".TW", ".TWO"))
        market = "TW" if is_taiwan else "US"
        progress("加入研究股票池", 2)
        universe_service.add_asset(
            normalized,
            market,
            benchmark_symbol="0050.TW" if is_taiwan else "SPY",
        )
        try:
            daily_research_pipeline.run(
                market,
                full_refresh=True,
                symbols=[normalized],
                refresh_macro=False,
                progress_callback=progress,
            )
        except Exception:
            if market_bar_repository.latest_event_time(
                normalized, "1d", "yahoo_finance"
            ) is None:
                universe_service.set_active(normalized, False)
            raise
        if market_bar_repository.latest_event_time(
            normalized, "1d", "yahoo_finance"
        ) is None:
            universe_service.set_active(normalized, False)
            raise ValueError(f"找不到 {normalized} 的日線行情，請確認股票代號")
    stock_research_refresh = StockResearchRefreshCoordinator(load_missing_stock_research)
    if resolved.openai_embedding_enabled and resolved.openai_api_key:
        embedding_provider = OpenAIEmbeddingProvider(
            api_key=resolved.openai_api_key,
            model=resolved.openai_embedding_model,
            dimensions=resolved.openai_embedding_dimensions,
            base_url=resolved.openai_base_url,
            timeout_seconds=resolved.openai_timeout_seconds,
        )
    elif resolved.local_embedding_backend in {"sentence-transformer", "sentence_transformer"}:
        embedding_provider = SentenceTransformerEmbeddingProvider(
            model=resolved.local_embedding_model,
            device=resolved.local_embedding_device,
            batch_size=resolved.local_embedding_batch_size,
            offline=resolved.local_embedding_offline,
        )
    else:
        embedding_provider = LocalHashEmbeddingProvider(resolved.rag_local_dimensions)
    answer_provider = (
        OpenAIResponsesAnswerProvider(
            api_key=resolved.openai_api_key,
            model=resolved.openai_response_model,
            base_url=resolved.openai_base_url,
            timeout_seconds=resolved.openai_timeout_seconds,
        )
        if resolved.openai_enabled and resolved.openai_api_key
        else None
    )
    report_service = DailyReportKnowledgeService(
        knowledge_repository, decision_repository, factor_research_repository,
        backtest_repository, portfolio_repository, model_repository, news_research_service,
        embedding_provider, answer_provider,
    )
    authentication_service = AuthenticationService(
        resolved, authentication_repository, GoogleOidcClient(resolved)
    )
    paper_trading_service = PaperTradingService(
        paper_trading_repository, universe_repository, market_bar_repository
    )
    after_hours_ai_service = AfterHoursAiService(
        decision_repository,
        market_bar_repository,
        paper_trading_service,
        universe_repository,
        universe_history_service,
        portfolio_repository,
        model_repository,
        calendar_store=market_calendar,
    )
    universe_expansion_service = UniverseExpansionService(
        universe_service,
        universe_repository,
        universe_history_service,
        market_data_pipeline,
        taiwan_data_pipeline,
        market_bar_repository,
        decision_repository,
        TaiwanOfficialMarketRankingProvider(),
        run_repository=job_run_repository,
    )
    rl_environment_service = RlEnvironmentService(
        market_bar_repository, universe_repository, repository
    )
    shadow_trading_service = ShadowTradingService(
        shadow_trading_repository, market_bar_repository, universe_repository,
        rl_environment_service,
    )
    promotion_service = PromotionService(
        promotion_repository, shadow_trading_repository, rl_environment_service,
    )
    model_governance_service = ModelGovernanceService(
        model_governance_repository, model_repository, feature_store_repository,
        universe_repository,
    )
    automation_service = AutomationService(
        resolved, automation_repository, job_run_repository, daily_research_pipeline,
        report_service, SmtpEmailNotifier(resolved), lock_manager, paper_trading_service,
        shadow_trading_service, promotion_service, model_governance_service,
        point_in_time_data_service,
        intraday_derivative_feature_pipeline,
        earnings_call_service,
        after_hours_ai_service,
        universe_expansion_service,
    )
    research_dir = _instance_dir(resolved.database_url) / "research"
    investment_plan_service = InvestmentPlanService(
        SqlAlchemyInvestmentPlanRepository(database.session_factory),
        # Built-in baselines plus candidates the user approved (S4-W06).
        strategies=lambda: strategy_catalog(research_dir),
    )
    def plan_broker() -> str:
        plan = investment_plan_service.current()
        return plan.broker if plan else "conservative"

    actual_account_service = ActualAccountService(
        SqlAlchemyActualAccountRepository(database.session_factory),
        price_lookup=market_bar_repository.latest_closes,
        research_dir=_instance_dir(resolved.database_url) / "research",
        default_broker=plan_broker,
    )
    return Container(
        settings=resolved,
        database=database,
        health_service=HealthService(
            database, __version__, lock_manager, resolved.redis_enabled, resolved.redis_required
        ),
        research_overview_service=ResearchOverviewService(repository),
        market_data_ingestion_service=ingestion_service,
        market_data_overview_service=MarketDataOverviewService(market_bar_repository),
        quant_analytics_service=QuantAnalyticsService(market_bar_repository),
        research_universe_service=universe_service,
        daily_market_data_pipeline=market_data_pipeline,
        feature_label_pipeline=feature_label_pipeline,
        feature_store_overview_service=FeatureStoreOverviewService(feature_store_repository),
        daily_research_pipeline=daily_research_pipeline,
        factor_research_pipeline=factor_research_pipeline,
        factor_research_overview_service=FactorResearchOverviewService(factor_research_repository),
        backtest_research_pipeline=backtest_research_pipeline,
        backtest_research_overview_service=BacktestResearchOverviewService(backtest_repository),
        ensemble_research_pipeline=ensemble_research_pipeline,
        ensemble_overview_service=EnsembleOverviewService(ensemble_repository),
        portfolio_research_pipeline=portfolio_research_pipeline,
        portfolio_overview_service=PortfolioOverviewService(portfolio_repository),
        model_research_pipeline=model_research_pipeline,
        model_research_overview_service=ModelResearchOverviewService(model_repository),
        model_training_coordinator=ModelTrainingCoordinator(model_research_pipeline),
        daily_decision_pipeline=daily_decision_pipeline,
        decision_overview_service=DecisionOverviewService(decision_repository),
        stock_research_service=StockResearchService(
            universe_repository,
            market_bar_repository,
            feature_store_repository,
            factor_research_repository,
            backtest_repository,
            ensemble_repository,
            model_repository,
            decision_repository,
            stock_research_refresh.ensure,
            stock_research_refresh.status,
        ),
        taiwan_data_pipeline=taiwan_data_pipeline,
        taiwan_data_overview_service=TaiwanDataOverviewService(
            taiwan_data_repository, feature_store_repository
        ),
        explainability_service=ExplainabilityService(
            universe_repository, model_repository, decision_repository
        ),
        macro_data_pipeline=macro_data_pipeline,
        macro_data_overview_service=MacroDataOverviewService(macro_data_repository),
        odd_lot_research_service=OddLotResearchService(market_bar_repository),
        universe_history_service=universe_history_service,
        news_research_service=news_research_service,
        daily_report_knowledge_service=report_service,
        automation_service=automation_service,
        authentication_service=authentication_service,
        paper_trading_service=paper_trading_service,
        after_hours_ai_service=after_hours_ai_service,
        universe_expansion_service=universe_expansion_service,
        rl_environment_service=rl_environment_service,
        shadow_trading_service=shadow_trading_service,
        promotion_service=promotion_service,
        model_governance_service=model_governance_service,
        data_quality_service=data_quality_service,
        point_in_time_data_service=point_in_time_data_service,
        google_trends_service=google_trends_service,
        intraday_derivative_feature_pipeline=intraday_derivative_feature_pipeline,
        earnings_call_service=earnings_call_service,
        research_failure_memory_service=ResearchFailureMemoryService(
            failure_case_repository
        ),
        market_calendar=market_calendar,
        prediction_archive=PredictionArchiveService(
            model_repository,
            _instance_dir(resolved.database_url) / "research" / "predictions",
            registry_ids=lambda: active_registry_experiment_ids(
                model_governance_repository.list_entries()
            ),
        ),
        database_backup=(
            DatabaseBackupService(
                _sqlite_path(resolved.database_url),
                _instance_dir(resolved.database_url) / "backups" / "daily",
                keep=7,
                runs=job_run_repository,
            )
            if _sqlite_path(resolved.database_url) is not None else None
        ),
        close_availability=CloseAvailabilityProbe(
            _instance_dir(resolved.database_url), market_calendar
        ),
        investment_plan_service=investment_plan_service,
        actual_account_service=actual_account_service,
        plan_decision_service=PlanDecisionService(
            investment_plan_service, actual_account_service, market_bar_repository, market_calendar,
        ),
        notification_service=NotificationService(resolved, automation_repository),
    )
