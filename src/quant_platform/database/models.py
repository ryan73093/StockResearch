from __future__ import annotations

from datetime import UTC, date, datetime

from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from quant_platform.database.engine import Base


class ResearchExperimentModel(Base):
    __tablename__ = "research_experiments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    experiment_type: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    parameters_json: Mapped[str] = mapped_column(Text, default="{}")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class ResearchFailureCaseModel(Base):
    __tablename__ = "research_failure_cases"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    case_key: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    module: Mapped[str] = mapped_column(String(80), index=True)
    title: Mapped[str] = mapped_column(String(300))
    severity: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    cause: Mapped[str] = mapped_column(Text)
    correction: Mapped[str] = mapped_column(Text)
    verification: Mapped[str | None] = mapped_column(Text, nullable=True)
    repeat_count: Mapped[int] = mapped_column(Integer, default=1)
    first_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ShadowOrderModel(Base):
    __tablename__ = "shadow_orders"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "algorithm", "experiment_id", "decision_time",
            name="uq_shadow_order_decision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    algorithm: Mapped[str] = mapped_column(String(40), index=True)
    experiment_id: Mapped[int] = mapped_column(index=True)
    decision_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data_available_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reference_price: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    previous_target_weight: Mapped[float] = mapped_column(Float)
    target_weight: Mapped[float] = mapped_column(Float)
    side: Mapped[str] = mapped_column(String(10), index=True)
    quantity: Mapped[int] = mapped_column(BigInteger)
    notional_twd: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    status: Mapped[str] = mapped_column(String(30), index=True)
    promotion_gate: Mapped[str] = mapped_column(String(30), index=True)
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    execution_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fill_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    gross_twd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    commission_twd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    transaction_tax_twd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    pnl_twd: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    return_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    benchmark_return: Mapped[float | None] = mapped_column(Float, nullable=True)


class PromotionReviewModel(Base):
    __tablename__ = "promotion_reviews"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "algorithm", "experiment_id", "policy_version",
            name="uq_promotion_review_policy",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    algorithm: Mapped[str] = mapped_column(String(40), index=True)
    experiment_id: Mapped[int] = mapped_column(index=True)
    policy_version: Mapped[str] = mapped_column(String(30), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    checks_json: Mapped[str] = mapped_column(Text)
    metrics_json: Mapped[str] = mapped_column(Text)
    reviewer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BrokerExecutionAuditModel(Base):
    __tablename__ = "broker_execution_audits"
    __table_args__ = (
        UniqueConstraint("client_order_id", name="uq_broker_execution_client_order"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    promotion_review_id: Mapped[int] = mapped_column(
        ForeignKey("promotion_reviews.id"), index=True
    )
    shadow_order_id: Mapped[int] = mapped_column(ForeignKey("shadow_orders.id"), index=True)
    client_order_id: Mapped[str] = mapped_column(String(120), index=True)
    environment: Mapped[str] = mapped_column(String(30), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    side: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[int] = mapped_column(BigInteger)
    order_type: Mapped[str] = mapped_column(String(20))
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    broker_order_id: Mapped[str] = mapped_column(String(120), index=True)
    message: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ModelExperimentModel(Base):
    __tablename__ = "model_experiments"
    __table_args__ = (
        UniqueConstraint(
            "market", "model_name", "model_version", "label_name", "experiment_version", "data_end",
            name="uq_model_experiment_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    model_name: Mapped[str] = mapped_column(String(100), index=True)
    model_version: Mapped[str] = mapped_column(String(30))
    label_name: Mapped[str] = mapped_column(String(100), index=True)
    experiment_version: Mapped[str] = mapped_column(String(30), index=True)
    promotion_gate: Mapped[str] = mapped_column(String(30), index=True)
    data_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    observation_count: Mapped[int]
    fold_count: Mapped[int]
    feature_count: Mapped[int]
    rmse: Mapped[float] = mapped_column(Float)
    mae: Mapped[float] = mapped_column(Float)
    r2: Mapped[float] = mapped_column(Float)
    directional_accuracy: Mapped[float] = mapped_column(Float)
    rank_ic: Mapped[float | None] = mapped_column(Float, nullable=True)
    long_short_spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    feature_names_json: Mapped[str] = mapped_column(Text, default="[]")
    parameters_json: Mapped[str] = mapped_column(Text, default="{}")
    fold_metrics_json: Mapped[str] = mapped_column(Text, default="[]")
    feature_importance_json: Mapped[str] = mapped_column(Text, default="{}")
    limitations_json: Mapped[str] = mapped_column(Text, default="[]")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ModelPredictionModel(Base):
    __tablename__ = "model_predictions"
    __table_args__ = (
        UniqueConstraint("experiment_id", "symbol", "event_time", name="uq_model_prediction_snapshot"),
    )

    # Queries filter by experiment (leading column of the unique constraint) or
    # by symbol; other single-column indexes were unused and dropped in S1-W03.
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("model_experiments.id"))
    market: Mapped[str] = mapped_column(String(20))
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    model_name: Mapped[str] = mapped_column(String(100))
    label_name: Mapped[str] = mapped_column(String(100))
    horizon: Mapped[int]
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    predicted_value: Mapped[float] = mapped_column(Float)
    rank_score: Mapped[float] = mapped_column(Float)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ModelExplanationModel(Base):
    __tablename__ = "model_explanations"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id", "symbol", "event_time", "explanation_version",
            name="uq_model_explanation_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("model_experiments.id"), index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    model_name: Mapped[str] = mapped_column(String(100), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data_available_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    predicted_value: Mapped[float] = mapped_column(Float)
    baseline_value: Mapped[float] = mapped_column(Float)
    contributions_json: Mapped[str] = mapped_column(Text)
    explanation_method: Mapped[str] = mapped_column(String(80), index=True)
    explanation_version: Mapped[str] = mapped_column(String(30), index=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ModelRegistryEntryModel(Base):
    __tablename__ = "model_registry_entries"
    __table_args__ = (
        UniqueConstraint("experiment_id", name="uq_model_registry_experiment"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("model_experiments.id"), index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    model_name: Mapped[str] = mapped_column(String(100), index=True)
    label_name: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    quality_score: Mapped[float] = mapped_column(Float)
    eligibility_checks_json: Mapped[str] = mapped_column(Text)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    demoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ModelDriftSnapshotModel(Base):
    __tablename__ = "model_drift_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "registry_entry_id", "policy_version", "recent_end",
            name="uq_model_drift_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    registry_entry_id: Mapped[int] = mapped_column(
        ForeignKey("model_registry_entries.id"), index=True
    )
    policy_version: Mapped[str] = mapped_column(String(30), index=True)
    snapshot_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    baseline_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recent_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recent_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    feature_count: Mapped[int]
    observation_count: Mapped[int]
    feature_psi_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    feature_psi_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    prediction_psi: Mapped[float | None] = mapped_column(Float, nullable=True)
    directional_accuracy_change: Mapped[float] = mapped_column(Float)
    rank_ic_change: Mapped[float | None] = mapped_column(Float, nullable=True)
    quality_score_change: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(30), index=True)
    feature_details_json: Mapped[str] = mapped_column(Text)
    checks_json: Mapped[str] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DataQualitySnapshotModel(Base):
    __tablename__ = "data_quality_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "market", "stage", "policy_version", "data_fingerprint",
            name="uq_data_quality_snapshot_fingerprint",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    stage: Mapped[str] = mapped_column(String(20), index=True)
    policy_version: Mapped[str] = mapped_column(String(40), index=True)
    data_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_asset_count: Mapped[int]
    covered_asset_count: Mapped[int]
    bar_coverage_rate: Mapped[float] = mapped_column(Float)
    feature_coverage_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    stale_asset_count: Mapped[int]
    temporal_violation_count: Mapped[int]
    invalid_value_count: Mapped[int]
    missing_session_count: Mapped[int]
    issue_count: Mapped[int]
    blocking_issue_count: Mapped[int]
    research_allowed: Mapped[bool] = mapped_column(Boolean, index=True)
    issues_json: Mapped[str] = mapped_column(Text)
    checks_json: Mapped[str] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DailyDecisionModel(Base):
    __tablename__ = "daily_decisions"
    __table_args__ = (
        UniqueConstraint("market", "symbol", "decision_version", "event_time", name="uq_daily_decision_snapshot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    decision_version: Mapped[str] = mapped_column(String(30), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    score: Mapped[float] = mapped_column(Float)
    predicted_return_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    prediction_dispersion: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_rank: Mapped[float | None] = mapped_column(Float, nullable=True)
    regime: Mapped[str] = mapped_column(String(60), index=True)
    factor_score: Mapped[float] = mapped_column(Float)
    strategy_score: Mapped[float] = mapped_column(Float)
    suggested_weight: Mapped[float] = mapped_column(Float)
    reasons_json: Mapped[str] = mapped_column(Text, default="[]")
    risks_json: Mapped[str] = mapped_column(Text, default="[]")
    gate_checks_json: Mapped[str] = mapped_column(Text, default="[]")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MarketBarModel(Base):
    __tablename__ = "market_bars"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "interval",
            "source",
            "event_time",
            "available_time",
            name="uq_market_bar_point_in_time",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    interval: Mapped[str] = mapped_column(String(10), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    available_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    high: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    low: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    close: Mapped[Decimal] = mapped_column(Numeric(20, 8))
    adjusted_close: Mapped[Decimal | None] = mapped_column(Numeric(20, 8), nullable=True)
    volume: Mapped[int] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(40), index=True)


class PointInTimeDatasetModel(Base):
    __tablename__ = "point_in_time_datasets"

    dataset_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(40), index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    frequency: Mapped[str] = mapped_column(String(30), index=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(40), index=True)
    source_dataset: Mapped[str] = mapped_column(String(100), index=True)
    access_tier: Mapped[str] = mapped_column(String(40))
    unit_schema_json: Mapped[str] = mapped_column(Text, default="{}")
    publication_rule: Mapped[str] = mapped_column(String(500))
    revision_policy: Mapped[str] = mapped_column(String(300))
    history_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_url: Mapped[str] = mapped_column(String(500))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PointInTimeObservationModel(Base):
    __tablename__ = "point_in_time_observations"
    __table_args__ = (
        UniqueConstraint(
            "dataset_key", "entity_id", "event_time", "revision_key", "content_hash",
            name="uq_point_in_time_observation_revision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dataset_key: Mapped[str] = mapped_column(
        ForeignKey("point_in_time_datasets.dataset_key"), index=True
    )
    entity_id: Mapped[str] = mapped_column(String(100), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    available_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revision_key: Mapped[str] = mapped_column(String(240), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), index=True)
    source_uri: Mapped[str] = mapped_column(String(500))


class TaiwanDataRecordModel(Base):
    __tablename__ = "taiwan_data_records"
    __table_args__ = (
        UniqueConstraint(
            "source",
            "dataset",
            "symbol",
            "event_time",
            "available_time",
            "record_key",
            "content_hash",
            name="uq_taiwan_data_revision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    dataset: Mapped[str] = mapped_column(String(100), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    available_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    record_key: Mapped[str] = mapped_column(String(160), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    fields_json: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), index=True)


class MacroObservationModel(Base):
    __tablename__ = "macro_observations"
    __table_args__ = (UniqueConstraint("series_id", "event_time", "content_hash", name="uq_macro_revision"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    series_id: Mapped[str] = mapped_column(String(40), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    available_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    value: Mapped[float] = mapped_column(Float)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(50), index=True)


class ResearchAssetModel(Base):
    __tablename__ = "research_universe"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    asset_type: Mapped[str] = mapped_column(String(30), index=True)
    sector: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    company_name: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    company_abbreviation: Mapped[str | None] = mapped_column(String(100), nullable=True)
    industry_code: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    paid_in_capital: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    issued_shares: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    market_value_twd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    market_rank: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    metadata_source: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metadata_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    benchmark_symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    data_start: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


class UniverseMembershipModel(Base):
    __tablename__ = "universe_memberships"
    __table_args__ = (
        UniqueConstraint("symbol", "valid_from", "source", name="uq_universe_membership_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    valid_from: Mapped[date] = mapped_column(Date, index=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    start_is_exact: Mapped[bool] = mapped_column(Boolean, default=False)
    end_is_exact: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(60), index=True)
    reason: Mapped[str] = mapped_column(String(200))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class DailyResearchReportModel(Base):
    __tablename__ = "daily_research_reports"
    __table_args__ = (UniqueConstraint("report_date", "market", name="uq_daily_report_market"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_date: Mapped[date] = mapped_column(Date, index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body_markdown: Mapped[str] = mapped_column(Text)
    sources_json: Mapped[str] = mapped_column(Text, default="[]")
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class KnowledgeDocumentModel(Base):
    __tablename__ = "knowledge_documents"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_key: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    document_type: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(240))
    content: Mapped[str] = mapped_column(Text)
    source_uri: Mapped[str] = mapped_column(String(300))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class KnowledgeChunkModel(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("chunk_key", name="uq_knowledge_chunk_key"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    chunk_key: Mapped[str] = mapped_column(String(220), index=True)
    document_key: Mapped[str] = mapped_column(String(180), index=True)
    document_type: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(240))
    chunk_index: Mapped[int] = mapped_column(index=True)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    source_uri: Mapped[str] = mapped_column(String(300))
    embedding_provider: Mapped[str] = mapped_column(String(40), index=True)
    embedding_model: Mapped[str] = mapped_column(String(100), index=True)
    embedding_dimensions: Mapped[int] = mapped_column()
    embedding_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class RagQueryAuditModel(Base):
    __tablename__ = "rag_query_audits"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(80), index=True)
    embedding_provider: Mapped[str] = mapped_column(String(40), index=True)
    embedding_model: Mapped[str] = mapped_column(String(100), index=True)
    answer_provider: Mapped[str] = mapped_column(String(40), index=True)
    answer_model: Mapped[str] = mapped_column(String(100), index=True)
    retrieval_json: Mapped[str] = mapped_column(Text, default="[]")
    answer: Mapped[str] = mapped_column(Text)
    grounded: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    refusal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SchedulerJobRunModel(Base):
    __tablename__ = "scheduler_job_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    job_name: Mapped[str] = mapped_column(String(100), index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AutomationScheduleModel(Base):
    __tablename__ = "automation_schedules"
    job_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(160))
    market: Mapped[str] = mapped_column(String(20), index=True)
    hour: Mapped[int]
    minute: Mapped[int]
    weekdays: Mapped[str] = mapped_column(String(40))
    timezone: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_retries: Mapped[int] = mapped_column(default=1)
    notify_on_success: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_on_failure: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class NotificationDeliveryModel(Base):
    __tablename__ = "notification_deliveries"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(30), index=True)
    recipient: Mapped[str] = mapped_column(String(240))
    subject: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(30), index=True)
    related_run_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class PlatformUserModel(Base):
    __tablename__ = "platform_users"
    __table_args__ = (
        UniqueConstraint("provider", "provider_subject", name="uq_user_provider_subject"),
    )
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    provider_subject: Mapped[str] = mapped_column(String(180))
    email: Mapped[str] = mapped_column(String(320), index=True)
    display_name: Mapped[str] = mapped_column(String(160))
    avatar_url: Mapped[str | None] = mapped_column(String(600), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OAuthLoginStateModel(Base):
    __tablename__ = "oauth_login_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_nonce_hash: Mapped[str] = mapped_column(String(64), index=True)
    code_verifier: Mapped[str] = mapped_column(String(160))
    next_path: Mapped[str] = mapped_column(String(500), default="/")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserSessionModel(Base):
    __tablename__ = "user_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("platform_users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaperAccountModel(Base):
    __tablename__ = "paper_accounts"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    currency: Mapped[str] = mapped_column(String(10), default="TWD")
    initial_cash: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    cash: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PaperOrderModel(Base):
    __tablename__ = "paper_orders"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    side: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[int]
    order_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    eligible_after_event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    estimated_price: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PaperFillModel(Base):
    __tablename__ = "paper_fills"
    __table_args__ = (UniqueConstraint("order_id", name="uq_paper_fill_order"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("paper_orders.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    side: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[int]
    price: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    commission: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    transaction_tax: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    slippage_bps: Mapped[Decimal] = mapped_column(Numeric(12, 4))
    bar_event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PaperPositionModel(Base):
    __tablename__ = "paper_positions"
    __table_args__ = (UniqueConstraint("account_id", "symbol", name="uq_paper_position"),)
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("paper_accounts.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    quantity: Mapped[int]
    average_cost: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class FeatureDefinitionModel(Base):
    __tablename__ = "feature_definitions"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_feature_definition"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), index=True)
    version: Mapped[str] = mapped_column(String(30))
    family: Mapped[str] = mapped_column(String(50), index=True)
    description: Mapped[str] = mapped_column(Text)
    lookback: Mapped[int]
    parameters_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class FeatureValueModel(Base):
    __tablename__ = "feature_values"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "feature_name", "feature_version", "event_time",
            name="uq_feature_value_versioned",
        ),
    )

    # Queries always filter by symbol (leading column of the unique constraint);
    # feature_name keeps its index for market-wide coverage checks (S1-W03).
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40))
    feature_name: Mapped[str] = mapped_column(String(100), index=True)
    feature_version: Mapped[str] = mapped_column(String(30))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    value: Mapped[float] = mapped_column(Float)


class FeatureRevisionModel(Base):
    __tablename__ = "feature_revisions"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "feature_name", "feature_version", "event_time",
            "available_time", "input_fingerprint",
            name="uq_feature_revision_point_in_time",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    feature_name: Mapped[str] = mapped_column(String(100), index=True)
    feature_version: Mapped[str] = mapped_column(String(30), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    available_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    value: Mapped[float] = mapped_column(Float)
    input_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    lineage_json: Mapped[str] = mapped_column(Text)


class LabelValueModel(Base):
    __tablename__ = "label_values"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "label_name", "label_version", "event_time",
            name="uq_label_value_versioned",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    label_name: Mapped[str] = mapped_column(String(100), index=True)
    label_version: Mapped[str] = mapped_column(String(30))
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    available_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    value: Mapped[float] = mapped_column(Float)


class RegimeStateModel(Base):
    __tablename__ = "regime_states"
    __table_args__ = (
        UniqueConstraint("symbol", "event_time", "regime_version", name="uq_regime_state_versioned"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    available_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    regime_version: Mapped[str] = mapped_column(String(30), index=True)
    trend_regime: Mapped[str] = mapped_column(String(30), index=True)
    volatility_regime: Mapped[str] = mapped_column(String(30), index=True)
    composite_regime: Mapped[str] = mapped_column(String(60), index=True)
    trend_score: Mapped[float] = mapped_column(Float)
    volatility_score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class FactorResearchResultModel(Base):
    __tablename__ = "factor_research_results"
    __table_args__ = (
        UniqueConstraint(
            "market", "feature_name", "feature_version", "research_version",
            name="uq_factor_research_versioned",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    feature_name: Mapped[str] = mapped_column(String(100), index=True)
    feature_version: Mapped[str] = mapped_column(String(30))
    research_version: Mapped[str] = mapped_column(String(30), index=True)
    cross_sections_5d: Mapped[int]
    cross_sections_20d: Mapped[int]
    mean_ic_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank_ic_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    ic_ir_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    positive_ic_rate_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_ic_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    rank_ic_20d: Mapped[float | None] = mapped_column(Float, nullable=True)
    decay_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    quantile_spread_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    annualized_spread_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float | None] = mapped_column(Float, nullable=True)
    best_regime: Mapped[str | None] = mapped_column(String(60), nullable=True)
    best_regime_rank_ic: Mapped[float | None] = mapped_column(Float, nullable=True)
    regime_metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    daily_metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BacktestRunModel(Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (
        UniqueConstraint(
            "symbol", "strategy_name", "strategy_version", "research_version", "data_end",
            name="uq_backtest_research_snapshot",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    strategy_name: Mapped[str] = mapped_column(String(100), index=True)
    strategy_version: Mapped[str] = mapped_column(String(30))
    research_version: Mapped[str] = mapped_column(String(30), index=True)
    promotion_gate: Mapped[str] = mapped_column(String(30), index=True)
    data_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fold_count: Mapped[int]
    observation_count: Mapped[int]
    total_return: Mapped[float] = mapped_column(Float)
    annual_return: Mapped[float] = mapped_column(Float)
    benchmark_return: Mapped[float] = mapped_column(Float)
    excess_return: Mapped[float] = mapped_column(Float)
    sharpe: Mapped[float] = mapped_column(Float)
    sortino: Mapped[float] = mapped_column(Float)
    calmar: Mapped[float] = mapped_column(Float)
    max_drawdown: Mapped[float] = mapped_column(Float)
    win_rate: Mapped[float] = mapped_column(Float)
    profit_factor: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float] = mapped_column(Float)
    exposure: Mapped[float] = mapped_column(Float)
    alpha: Mapped[float | None] = mapped_column(Float, nullable=True)
    beta: Mapped[float | None] = mapped_column(Float, nullable=True)
    information_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    trade_count: Mapped[int]
    positive_fold_rate: Mapped[float] = mapped_column(Float)
    commission_bps: Mapped[float] = mapped_column(Float)
    slippage_bps: Mapped[float] = mapped_column(Float)
    parameters_json: Mapped[str] = mapped_column(Text, default="{}")
    limitations_json: Mapped[str] = mapped_column(Text, default="[]")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BacktestFoldModel(Base):
    __tablename__ = "backtest_folds"
    __table_args__ = (UniqueConstraint("run_id", "sequence", name="uq_backtest_fold_sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    sequence: Mapped[int]
    train_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    train_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    test_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    test_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    train_observations: Mapped[int]
    test_observations: Mapped[int]
    selected_parameters_json: Mapped[str] = mapped_column(Text)
    train_sharpe: Mapped[float] = mapped_column(Float)
    test_return: Mapped[float] = mapped_column(Float)
    benchmark_return: Mapped[float] = mapped_column(Float)
    test_sharpe: Mapped[float] = mapped_column(Float)
    max_drawdown: Mapped[float] = mapped_column(Float)
    trade_count: Mapped[int]


class BacktestTradeModel(Base):
    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    exit_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    size: Mapped[float] = mapped_column(Float)
    net_return: Mapped[float] = mapped_column(Float)
    holding_sessions: Mapped[int]
    exit_reason: Mapped[str] = mapped_column(String(40), index=True)
    costs: Mapped[float] = mapped_column(Float)


class BacktestEquityPointModel(Base):
    __tablename__ = "backtest_equity_points"
    __table_args__ = (UniqueConstraint("run_id", "event_time", name="uq_backtest_equity_time"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id"), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    equity: Mapped[float] = mapped_column(Float)
    benchmark_equity: Mapped[float] = mapped_column(Float)
    drawdown: Mapped[float] = mapped_column(Float)
    daily_return: Mapped[float] = mapped_column(Float)
    position: Mapped[float] = mapped_column(Float)


class EnsembleRunModel(Base):
    __tablename__ = "ensemble_runs"
    __table_args__ = (
        UniqueConstraint("symbol", "ensemble_version", "data_end", name="uq_ensemble_snapshot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    ensemble_version: Mapped[str] = mapped_column(String(30), index=True)
    promotion_gate: Mapped[str] = mapped_column(String(30), index=True)
    data_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    observation_count: Mapped[int]
    component_count: Mapped[int]
    total_return: Mapped[float] = mapped_column(Float)
    annual_return: Mapped[float] = mapped_column(Float)
    equal_weight_return: Mapped[float] = mapped_column(Float)
    excess_to_equal: Mapped[float] = mapped_column(Float)
    sharpe: Mapped[float] = mapped_column(Float)
    equal_weight_sharpe: Mapped[float] = mapped_column(Float)
    max_drawdown: Mapped[float] = mapped_column(Float)
    turnover: Mapped[float] = mapped_column(Float)
    allocation_cost_bps: Mapped[float] = mapped_column(Float)
    average_weights_json: Mapped[str] = mapped_column(Text)
    latest_weights_json: Mapped[str] = mapped_column(Text)
    regime_metrics_json: Mapped[str] = mapped_column(Text)
    limitations_json: Mapped[str] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EnsembleWeightPointModel(Base):
    __tablename__ = "ensemble_weight_points"
    __table_args__ = (UniqueConstraint("run_id", "event_time", name="uq_ensemble_weight_time"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("ensemble_runs.id"), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    regime: Mapped[str] = mapped_column(String(60), index=True)
    weights_json: Mapped[str] = mapped_column(Text)
    scores_json: Mapped[str] = mapped_column(Text)
    contributions_json: Mapped[str] = mapped_column(Text)
    turnover: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)
    daily_return: Mapped[float] = mapped_column(Float)


class EnsembleEquityPointModel(Base):
    __tablename__ = "ensemble_equity_points"
    __table_args__ = (UniqueConstraint("run_id", "event_time", name="uq_ensemble_equity_time"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("ensemble_runs.id"), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    equity: Mapped[float] = mapped_column(Float)
    equal_weight_equity: Mapped[float] = mapped_column(Float)
    drawdown: Mapped[float] = mapped_column(Float)


class PortfolioRunModel(Base):
    __tablename__ = "portfolio_runs"
    __table_args__ = (
        UniqueConstraint("market", "method", "portfolio_version", "data_end", name="uq_portfolio_snapshot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    market: Mapped[str] = mapped_column(String(20), index=True)
    method: Mapped[str] = mapped_column(String(60), index=True)
    portfolio_version: Mapped[str] = mapped_column(String(30), index=True)
    promotion_gate: Mapped[str] = mapped_column(String(30), index=True)
    data_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    observation_count: Mapped[int]
    asset_count: Mapped[int]
    total_return: Mapped[float] = mapped_column(Float)
    annual_return: Mapped[float] = mapped_column(Float)
    equal_weight_return: Mapped[float] = mapped_column(Float)
    benchmark_return: Mapped[float] = mapped_column(Float)
    excess_to_equal: Mapped[float] = mapped_column(Float)
    excess_to_benchmark: Mapped[float] = mapped_column(Float)
    annual_volatility: Mapped[float] = mapped_column(Float)
    sharpe: Mapped[float] = mapped_column(Float)
    sortino: Mapped[float] = mapped_column(Float)
    max_drawdown: Mapped[float] = mapped_column(Float)
    var_95: Mapped[float] = mapped_column(Float)
    cvar_95: Mapped[float] = mapped_column(Float)
    beta: Mapped[float | None] = mapped_column(Float, nullable=True)
    turnover: Mapped[float] = mapped_column(Float)
    transaction_cost_bps: Mapped[float] = mapped_column(Float)
    effective_asset_count: Mapped[float] = mapped_column(Float)
    diversification_ratio: Mapped[float] = mapped_column(Float)
    average_correlation: Mapped[float] = mapped_column(Float)
    liquidity_risk: Mapped[float] = mapped_column(Float)
    latest_weights_json: Mapped[str] = mapped_column(Text)
    sector_exposure_json: Mapped[str] = mapped_column(Text)
    factor_exposure_json: Mapped[str] = mapped_column(Text)
    stress_tests_json: Mapped[str] = mapped_column(Text)
    limitations_json: Mapped[str] = mapped_column(Text)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class PortfolioAllocationPointModel(Base):
    __tablename__ = "portfolio_allocation_points"
    __table_args__ = (UniqueConstraint("run_id", "event_time", name="uq_portfolio_allocation_time"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("portfolio_runs.id"), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    weights_json: Mapped[str] = mapped_column(Text)
    cash_weight: Mapped[float] = mapped_column(Float)
    turnover: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)
    constraint_flags_json: Mapped[str] = mapped_column(Text)


class PortfolioEquityPointModel(Base):
    __tablename__ = "portfolio_equity_points"
    __table_args__ = (UniqueConstraint("run_id", "event_time", name="uq_portfolio_equity_time"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("portfolio_runs.id"), index=True)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    equity: Mapped[float] = mapped_column(Float)
    equal_weight_equity: Mapped[float] = mapped_column(Float)
    benchmark_equity: Mapped[float] = mapped_column(Float)
    drawdown: Mapped[float] = mapped_column(Float)
    daily_return: Mapped[float] = mapped_column(Float)

class InvestmentPlanModel(Base):
    """One saved version of the investor's plan (S5-W01); rows are never updated."""

    __tablename__ = "investment_plans"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    monthly_amount: Mapped[Decimal] = mapped_column(Numeric(20, 2))
    salary_day: Mapped[int]
    strategy_key: Mapped[str] = mapped_column(String(80))
    max_drawdown_tolerance: Mapped[float] = mapped_column(Float)
    goal: Mapped[str] = mapped_column(String(200), default="")
    horizon_years: Mapped[int | None] = mapped_column(nullable=True)
    note: Mapped[str] = mapped_column(String(1000), default="")
