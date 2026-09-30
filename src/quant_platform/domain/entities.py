from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class ExperimentStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ResearchExperiment:
    id: int | None
    name: str
    experiment_type: str
    status: ExperimentStatus
    parameters_json: str
    metrics_json: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class MarketBar:
    """A point-in-time OHLCV observation as known by the research system."""

    symbol: str
    market: str
    interval: str
    event_time: datetime
    available_time: datetime
    ingested_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adjusted_close: Decimal | None
    volume: int
    source: str


@dataclass(frozen=True, slots=True)
class PointInTimeDataset:
    """Catalog metadata for a versioned research dataset.

    ``publication_rule`` documents when the data becomes usable.  It is
    deliberately separate from the observation payload so the same storage and
    as-of semantics can serve intraday bars, derivatives and alternative data.
    """

    dataset_key: str
    display_name: str
    category: str
    market: str
    frequency: str
    entity_type: str
    source: str
    source_dataset: str
    access_tier: str
    unit_schema_json: str
    publication_rule: str
    revision_policy: str
    history_start: date | None
    source_url: str
    enabled: bool
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PointInTimeObservation:
    """One immutable source revision with explicit information availability."""

    id: int | None
    dataset_key: str
    entity_id: str
    event_time: datetime
    available_time: datetime
    ingested_at: datetime
    revision_key: str
    content_hash: str
    payload_json: str
    source: str
    source_uri: str


@dataclass(frozen=True, slots=True)
class PointInTimeCoverage:
    dataset_key: str
    entity_id: str
    source: str
    row_count: int
    revision_count: int
    first_event_time: datetime
    last_event_time: datetime
    last_available_time: datetime
    last_ingested_at: datetime


@dataclass(frozen=True, slots=True)
class DataCoverage:
    symbol: str
    market: str
    interval: str
    source: str
    row_count: int
    first_event_time: datetime
    last_event_time: datetime
    last_ingested_at: datetime


@dataclass(frozen=True, slots=True)
class ResearchAsset:
    id: int | None
    symbol: str
    market: str
    asset_type: str
    sector: str | None
    benchmark_symbol: str | None
    active: bool
    data_start: date
    created_at: datetime
    updated_at: datetime
    company_name: str | None = None
    company_abbreviation: str | None = None
    industry_code: str | None = None
    paid_in_capital: int | None = None
    issued_shares: int | None = None
    market_value_twd: int | None = None
    market_rank: int | None = None
    metadata_source: str | None = None
    metadata_updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class UniverseMembership:
    """One point-in-time interval in which a security belonged to a research universe."""

    id: int | None
    symbol: str
    market: str
    valid_from: date
    valid_to: date | None
    start_is_exact: bool
    end_is_exact: bool
    source: str
    reason: str
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class DailyResearchReport:
    id: int | None
    report_date: date
    market: str
    title: str
    body_markdown: str
    sources_json: str
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class KnowledgeDocument:
    id: int | None
    document_key: str
    document_type: str
    title: str
    content: str
    source_uri: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    id: int | None
    chunk_key: str
    document_key: str
    document_type: str
    title: str
    chunk_index: int
    content: str
    content_hash: str
    source_uri: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    embedding_json: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RagQueryAudit:
    id: int | None
    question: str
    mode: str
    embedding_provider: str
    embedding_model: str
    answer_provider: str
    answer_model: str
    retrieval_json: str
    answer: str
    grounded: bool
    refusal_reason: str | None
    latency_ms: int
    created_at: datetime


class JobRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SchedulerJobRun:
    id: int
    job_name: str
    market: str
    status: JobRunStatus
    started_at: datetime
    completed_at: datetime | None
    metrics_json: str
    error: str | None


@dataclass(frozen=True, slots=True)
class AutomationSchedule:
    job_key: str
    display_name: str
    market: str
    hour: int
    minute: int
    weekdays: str
    timezone: str
    enabled: bool
    max_retries: int
    notify_on_success: bool
    notify_on_failure: bool
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NotificationDelivery:
    id: int | None
    channel: str
    recipient: str
    subject: str
    status: str
    related_run_id: int | None
    attempted_at: datetime
    error: str | None


@dataclass(frozen=True, slots=True)
class PlatformUser:
    id: int | None
    provider: str
    provider_subject: str
    email: str
    display_name: str
    avatar_url: str | None
    is_active: bool
    created_at: datetime
    last_login_at: datetime


@dataclass(frozen=True, slots=True)
class OAuthLoginState:
    state_hash: str
    browser_nonce_hash: str
    code_verifier: str
    next_path: str
    expires_at: datetime
    used_at: datetime | None


@dataclass(frozen=True, slots=True)
class UserSession:
    token_hash: str
    user_id: int
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    revoked_at: datetime | None


class PaperOrderStatus(StrEnum):
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class ShadowOrderStatus(StrEnum):
    PENDING = "pending"
    EVALUATED = "evaluated"
    NO_ACTION = "no_action"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class PromotionStatus(StrEnum):
    INELIGIBLE = "ineligible"
    REVIEW_READY = "review_ready"
    APPROVED = "approved"
    REJECTED = "rejected"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class ShadowOrder:
    id: int | None
    symbol: str
    algorithm: str
    experiment_id: int
    decision_time: datetime
    data_available_time: datetime
    reference_price: Decimal
    previous_target_weight: float
    target_weight: float
    side: str
    quantity: int
    notional_twd: Decimal
    status: ShadowOrderStatus
    promotion_gate: str
    reason: str
    created_at: datetime
    execution_time: datetime | None
    fill_price: Decimal | None
    gross_twd: Decimal | None
    commission_twd: Decimal | None
    transaction_tax_twd: Decimal | None
    pnl_twd: Decimal | None
    return_rate: float | None
    benchmark_return: float | None


@dataclass(frozen=True, slots=True)
class PromotionReview:
    id: int | None
    symbol: str
    algorithm: str
    experiment_id: int
    policy_version: str
    status: PromotionStatus
    checks_json: str
    metrics_json: str
    reviewer: str | None
    review_note: str | None
    evaluated_at: datetime
    reviewed_at: datetime | None
    approval_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class BrokerExecutionAudit:
    id: int | None
    promotion_review_id: int
    shadow_order_id: int
    client_order_id: str
    environment: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: Decimal | None
    status: str
    broker_order_id: str
    message: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PaperAccount:
    id: int | None
    name: str
    currency: str
    initial_cash: Decimal
    cash: Decimal
    realized_pnl: Decimal
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PaperOrder:
    id: int | None
    account_id: int
    symbol: str
    market: str
    side: str
    quantity: int
    order_type: str
    status: PaperOrderStatus
    submitted_at: datetime
    eligible_after_event_time: datetime
    estimated_price: Decimal
    rejection_reason: str | None
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class PaperFill:
    id: int | None
    order_id: int
    account_id: int
    symbol: str
    side: str
    quantity: int
    price: Decimal
    gross_amount: Decimal
    commission: Decimal
    transaction_tax: Decimal
    slippage_bps: Decimal
    bar_event_time: datetime
    executed_at: datetime


@dataclass(frozen=True, slots=True)
class PaperPosition:
    account_id: int
    symbol: str
    quantity: int
    average_cost: Decimal
    realized_pnl: Decimal
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    name: str
    version: str
    family: str
    description: str
    lookback: int
    parameters_json: str


@dataclass(frozen=True, slots=True)
class FeatureValue:
    symbol: str
    feature_name: str
    feature_version: str
    event_time: datetime
    available_time: datetime
    computed_at: datetime
    value: float


@dataclass(frozen=True, slots=True)
class FeatureRevision:
    """Immutable point-in-time feature revision with reproducible lineage."""

    id: int | None
    symbol: str
    feature_name: str
    feature_version: str
    event_time: datetime
    available_time: datetime
    computed_at: datetime
    value: float
    input_fingerprint: str
    lineage_json: str


@dataclass(frozen=True, slots=True)
class LabelValue:
    symbol: str
    label_name: str
    label_version: str
    event_time: datetime
    available_time: datetime
    computed_at: datetime
    value: float


@dataclass(frozen=True, slots=True)
class StoreCoverage:
    symbol: str
    item_name: str
    item_version: str
    row_count: int
    first_event_time: datetime
    last_event_time: datetime
    last_available_time: datetime


@dataclass(frozen=True, slots=True)
class RegimeState:
    symbol: str
    event_time: datetime
    available_time: datetime
    regime_version: str
    trend_regime: str
    volatility_regime: str
    composite_regime: str
    trend_score: float
    volatility_score: float
    confidence: float
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class FactorResearchResult:
    market: str
    feature_name: str
    feature_version: str
    research_version: str
    cross_sections_5d: int
    cross_sections_20d: int
    mean_ic_5d: float | None
    rank_ic_5d: float | None
    ic_ir_5d: float | None
    positive_ic_rate_5d: float | None
    mean_ic_20d: float | None
    rank_ic_20d: float | None
    decay_ratio: float | None
    quantile_spread_5d: float | None
    annualized_spread_5d: float | None
    turnover: float | None
    best_regime: str | None
    best_regime_rank_ic: float | None
    regime_metrics_json: str
    daily_metrics_json: str
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class BacktestRun:
    id: int | None
    symbol: str
    market: str
    strategy_name: str
    strategy_version: str
    research_version: str
    promotion_gate: str
    data_start: datetime
    data_end: datetime
    fold_count: int
    observation_count: int
    total_return: float
    annual_return: float
    benchmark_return: float
    excess_return: float
    sharpe: float
    sortino: float
    calmar: float
    max_drawdown: float
    win_rate: float
    profit_factor: float | None
    turnover: float
    exposure: float
    alpha: float | None
    beta: float | None
    information_ratio: float | None
    trade_count: int
    positive_fold_rate: float
    commission_bps: float
    slippage_bps: float
    parameters_json: str
    limitations_json: str
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class BacktestFold:
    sequence: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    train_observations: int
    test_observations: int
    selected_parameters_json: str
    train_sharpe: float
    test_return: float
    benchmark_return: float
    test_sharpe: float
    max_drawdown: float
    trade_count: int


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    size: float
    net_return: float
    holding_sessions: int
    exit_reason: str
    costs: float


@dataclass(frozen=True, slots=True)
class BacktestEquityPoint:
    event_time: datetime
    equity: float
    benchmark_equity: float
    drawdown: float
    daily_return: float
    position: float


@dataclass(frozen=True, slots=True)
class BacktestArtifact:
    run: BacktestRun
    folds: list[BacktestFold]
    trades: list[BacktestTrade]
    equity: list[BacktestEquityPoint]


@dataclass(frozen=True, slots=True)
class EnsembleRun:
    id: int | None
    symbol: str
    market: str
    ensemble_version: str
    promotion_gate: str
    data_start: datetime
    data_end: datetime
    observation_count: int
    component_count: int
    total_return: float
    annual_return: float
    equal_weight_return: float
    excess_to_equal: float
    sharpe: float
    equal_weight_sharpe: float
    max_drawdown: float
    turnover: float
    allocation_cost_bps: float
    average_weights_json: str
    latest_weights_json: str
    regime_metrics_json: str
    limitations_json: str
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class EnsembleWeightPoint:
    event_time: datetime
    regime: str
    weights_json: str
    scores_json: str
    contributions_json: str
    turnover: float
    cost: float
    daily_return: float


@dataclass(frozen=True, slots=True)
class EnsembleEquityPoint:
    event_time: datetime
    equity: float
    equal_weight_equity: float
    drawdown: float


@dataclass(frozen=True, slots=True)
class EnsembleArtifact:
    run: EnsembleRun
    weights: list[EnsembleWeightPoint]
    equity: list[EnsembleEquityPoint]


@dataclass(frozen=True, slots=True)
class PortfolioRun:
    id: int | None
    market: str
    method: str
    portfolio_version: str
    promotion_gate: str
    data_start: datetime
    data_end: datetime
    observation_count: int
    asset_count: int
    total_return: float
    annual_return: float
    equal_weight_return: float
    benchmark_return: float
    excess_to_equal: float
    excess_to_benchmark: float
    annual_volatility: float
    sharpe: float
    sortino: float
    max_drawdown: float
    var_95: float
    cvar_95: float
    beta: float | None
    turnover: float
    transaction_cost_bps: float
    effective_asset_count: float
    diversification_ratio: float
    average_correlation: float
    liquidity_risk: float
    latest_weights_json: str
    sector_exposure_json: str
    factor_exposure_json: str
    stress_tests_json: str
    limitations_json: str
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class PortfolioAllocationPoint:
    event_time: datetime
    weights_json: str
    cash_weight: float
    turnover: float
    cost: float
    constraint_flags_json: str


@dataclass(frozen=True, slots=True)
class PortfolioEquityPoint:
    event_time: datetime
    equity: float
    equal_weight_equity: float
    benchmark_equity: float
    drawdown: float
    daily_return: float


@dataclass(frozen=True, slots=True)
class PortfolioArtifact:
    run: PortfolioRun
    allocations: list[PortfolioAllocationPoint]
    equity: list[PortfolioEquityPoint]


@dataclass(frozen=True, slots=True)
class ModelExperiment:
    """A reproducible, time-split model research result."""

    id: int | None
    market: str
    model_name: str
    model_version: str
    label_name: str
    experiment_version: str
    promotion_gate: str
    data_start: datetime
    data_end: datetime
    observation_count: int
    fold_count: int
    feature_count: int
    rmse: float
    mae: float
    r2: float
    directional_accuracy: float
    rank_ic: float | None
    long_short_spread: float | None
    feature_names_json: str
    parameters_json: str
    fold_metrics_json: str
    feature_importance_json: str
    limitations_json: str
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class ModelPrediction:
    id: int | None
    experiment_id: int
    market: str
    symbol: str
    model_name: str
    label_name: str
    horizon: int
    event_time: datetime
    available_time: datetime
    predicted_value: float
    rank_score: float
    computed_at: datetime


class ModelRegistryStatus(StrEnum):
    CHALLENGER = "challenger"
    CHAMPION = "champion"
    DEMOTED = "demoted"
    RETIRED = "retired"


class DriftStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    STABLE = "stable"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class ModelRegistryEntry:
    id: int | None
    experiment_id: int
    market: str
    model_name: str
    label_name: str
    status: ModelRegistryStatus
    quality_score: float
    eligibility_checks_json: str
    registered_at: datetime
    promoted_at: datetime | None
    demoted_at: datetime | None
    reviewer: str | None
    decision_note: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ModelDriftSnapshot:
    id: int | None
    registry_entry_id: int
    policy_version: str
    snapshot_time: datetime
    baseline_end: datetime
    recent_start: datetime
    recent_end: datetime
    feature_count: int
    observation_count: int
    feature_psi_median: float | None
    feature_psi_max: float | None
    prediction_psi: float | None
    directional_accuracy_change: float
    rank_ic_change: float | None
    quality_score_change: float
    status: DriftStatus
    feature_details_json: str
    checks_json: str
    computed_at: datetime


class DataQualityStatus(StrEnum):
    PASS = "pass"
    WARNING = "warning"
    BLOCKED = "blocked"


class DataQualitySeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class DataQualitySnapshot:
    id: int | None
    market: str
    stage: str
    policy_version: str
    data_fingerprint: str
    status: DataQualityStatus
    data_as_of: datetime | None
    active_asset_count: int
    covered_asset_count: int
    bar_coverage_rate: float
    feature_coverage_rate: float | None
    stale_asset_count: int
    temporal_violation_count: int
    invalid_value_count: int
    missing_session_count: int
    issue_count: int
    blocking_issue_count: int
    research_allowed: bool
    issues_json: str
    checks_json: str
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class DailyDecision:
    id: int | None
    market: str
    symbol: str
    decision_version: str
    event_time: datetime
    status: str
    score: float
    predicted_return_5d: float | None
    prediction_dispersion: float | None
    model_rank: float | None
    regime: str
    factor_score: float
    strategy_score: float
    suggested_weight: float
    reasons_json: str
    risks_json: str
    gate_checks_json: str
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class TaiwanDataRecord:
    """A revision-preserving Taiwan market observation from an external source."""

    id: int | None
    symbol: str
    dataset: str
    event_time: datetime
    available_time: datetime
    ingested_at: datetime
    record_key: str
    content_hash: str
    fields_json: str
    source: str


@dataclass(frozen=True, slots=True)
class TaiwanDataCoverage:
    symbol: str
    dataset: str
    source: str
    row_count: int
    first_event_time: datetime
    last_event_time: datetime
    last_available_time: datetime
    last_ingested_at: datetime


@dataclass(frozen=True, slots=True)
class MacroObservation:
    id: int | None
    series_id: str
    event_time: datetime
    available_time: datetime
    ingested_at: datetime
    value: float
    content_hash: str
    source: str


@dataclass(frozen=True, slots=True)
class ModelExplanation:
    id: int | None
    experiment_id: int
    market: str
    symbol: str
    model_name: str
    event_time: datetime
    data_available_time: datetime
    predicted_value: float
    baseline_value: float
    contributions_json: str
    explanation_method: str
    explanation_version: str
    computed_at: datetime


@dataclass(frozen=True, slots=True)
class ResearchFailureCase:
    id: int | None
    case_key: str
    module: str
    title: str
    severity: str
    status: str
    evidence_json: str
    cause: str
    correction: str
    verification: str | None
    repeat_count: int
    first_occurred_at: datetime
    last_occurred_at: datetime

@dataclass(frozen=True, slots=True)
class InvestmentPlan:
    """One saved version of the investor's plan (S5-W01)."""

    version: int
    created_at: datetime
    monthly_amount: Decimal
    salary_day: int
    strategy_key: str
    max_drawdown_tolerance: float
    goal: str = ""
    horizon_years: int | None = None
    note: str = ""
