from __future__ import annotations

from collections.abc import Iterator
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import sessionmaker

from quant_platform.database.models import (
    ActualCashFlowModel,
    ActualTradeModel,
    InvestmentPlanModel,
    FeatureDefinitionModel,
    FeatureValueModel,
    FeatureRevisionModel,
    LabelValueModel,
    MarketBarModel,
    PointInTimeDatasetModel,
    PointInTimeObservationModel,
    ResearchAssetModel,
    UniverseMembershipModel,
    DailyResearchReportModel,
    KnowledgeDocumentModel,
    KnowledgeChunkModel,
    RagQueryAuditModel,
    ResearchExperimentModel,
    ResearchFailureCaseModel,
    SchedulerJobRunModel,
    AutomationScheduleModel,
    NotificationDeliveryModel,
    OAuthLoginStateModel,
    PlatformUserModel,
    UserSessionModel,
    PaperAccountModel,
    PaperFillModel,
    PaperOrderModel,
    PaperPositionModel,
    ShadowOrderModel,
    PromotionReviewModel,
    BrokerExecutionAuditModel,
    RegimeStateModel,
    FactorResearchResultModel,
    BacktestRunModel,
    BacktestFoldModel,
    BacktestTradeModel,
    BacktestEquityPointModel,
    EnsembleRunModel,
    EnsembleWeightPointModel,
    EnsembleEquityPointModel,
    PortfolioRunModel,
    PortfolioAllocationPointModel,
    PortfolioEquityPointModel,
    ModelExperimentModel,
    ModelPredictionModel,
    ModelExplanationModel,
    ModelRegistryEntryModel,
    ModelDriftSnapshotModel,
    DataQualitySnapshotModel,
    DailyDecisionModel,
    TaiwanDataRecordModel,
    MacroObservationModel,
)
from quant_platform.domain.entities import (
    ActualCashFlow,
    ActualTrade,
    InvestmentPlan,
    DataCoverage,
    ExperimentStatus,
    JobRunStatus,
    MarketBar,
    PointInTimeDataset,
    PointInTimeObservation,
    PointInTimeCoverage,
    ResearchAsset,
    UniverseMembership,
    DailyResearchReport,
    KnowledgeDocument,
    KnowledgeChunk,
    RagQueryAudit,
    ResearchExperiment,
    SchedulerJobRun,
    AutomationSchedule,
    NotificationDelivery,
    OAuthLoginState,
    PlatformUser,
    UserSession,
    PaperAccount,
    PaperFill,
    PaperOrder,
    PaperOrderStatus,
    PaperPosition,
    ShadowOrder,
    ShadowOrderStatus,
    PromotionReview,
    PromotionStatus,
    BrokerExecutionAudit,
    FeatureDefinition,
    FeatureValue,
    FeatureRevision,
    LabelValue,
    StoreCoverage,
    RegimeState,
    FactorResearchResult,
    BacktestArtifact,
    BacktestRun,
    BacktestFold,
    BacktestTrade,
    BacktestEquityPoint,
    EnsembleArtifact,
    EnsembleRun,
    EnsembleWeightPoint,
    EnsembleEquityPoint,
    PortfolioArtifact,
    PortfolioRun,
    ModelExperiment,
    ModelRegistryEntry,
    ModelRegistryStatus,
    ModelDriftSnapshot,
    DriftStatus,
    DataQualitySnapshot,
    DataQualityStatus,
    ModelPrediction,
    ModelExplanation,
    DailyDecision,
    PortfolioAllocationPoint,
    PortfolioEquityPoint,
    TaiwanDataRecord,
    TaiwanDataCoverage,
    MacroObservation,
    ResearchFailureCase,
)


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _utc_aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class SqlAlchemyMacroDataRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def add_revisions(self, values: list[MacroObservation]) -> int:
        inserted = 0
        with self._session_factory() as session:
            for value in values:
                exists = session.scalar(select(MacroObservationModel.id).where(
                    MacroObservationModel.series_id == value.series_id,
                    MacroObservationModel.event_time == value.event_time,
                    MacroObservationModel.content_hash == value.content_hash,
                ))
                if exists is None:
                    session.add(MacroObservationModel(**{key: getattr(value, key) for key in value.__dataclass_fields__ if key != "id"}))
                    inserted += 1
            session.commit()
        return inserted

    def list_latest(self, series_id: str | None = None) -> list[MacroObservation]:
        statement = select(MacroObservationModel)
        if series_id:
            statement = statement.where(MacroObservationModel.series_id == series_id)
        statement = statement.order_by(MacroObservationModel.event_time, MacroObservationModel.ingested_at.desc())
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        latest: dict[tuple[str, datetime], MacroObservationModel] = {}
        for row in rows:
            latest.setdefault((row.series_id, row.event_time), row)
        return [MacroObservation(row.id, row.series_id, row.event_time, row.available_time, row.ingested_at, row.value, row.content_hash, row.source) for row in latest.values()]


class SqlAlchemyResearchExperimentRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def count(self) -> int:
        with self._session_factory() as session:
            return int(session.scalar(select(func.count(ResearchExperimentModel.id))) or 0)

    def list_recent(self, limit: int = 5) -> list[ResearchExperiment]:
        statement = (
            select(ResearchExperimentModel)
            .order_by(ResearchExperimentModel.created_at.desc())
            .limit(limit)
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [
            ResearchExperiment(
                id=row.id,
                name=row.name,
                experiment_type=row.experiment_type,
                status=ExperimentStatus(row.status),
                parameters_json=row.parameters_json,
                metrics_json=row.metrics_json,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def save(self, experiment: ResearchExperiment) -> int:
        values = {
            key: getattr(experiment, key)
            for key in experiment.__dataclass_fields__
            if key != "id"
        }
        with self._session_factory() as session:
            row = ResearchExperimentModel(**values)
            session.add(row)
            session.commit()
            session.refresh(row)
            return int(row.id)

    def get_latest(
        self, name: str, experiment_type: str
    ) -> ResearchExperiment | None:
        statement = (
            select(ResearchExperimentModel)
            .where(
                ResearchExperimentModel.name == name,
                ResearchExperimentModel.experiment_type == experiment_type,
            )
            .order_by(ResearchExperimentModel.created_at.desc())
            .limit(1)
        )
        with self._session_factory() as session:
            row = session.scalar(statement)
        if row is None:
            return None
        return ResearchExperiment(
            id=row.id, name=row.name, experiment_type=row.experiment_type,
            status=ExperimentStatus(row.status), parameters_json=row.parameters_json,
            metrics_json=row.metrics_json, created_at=row.created_at,
        )


class SqlAlchemyResearchFailureCaseRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _entity(row: ResearchFailureCaseModel) -> ResearchFailureCase:
        return ResearchFailureCase(
            id=row.id,
            case_key=row.case_key,
            module=row.module,
            title=row.title,
            severity=row.severity,
            status=row.status,
            evidence_json=row.evidence_json,
            cause=row.cause,
            correction=row.correction,
            verification=row.verification,
            repeat_count=row.repeat_count,
            first_occurred_at=_utc_aware(row.first_occurred_at),
            last_occurred_at=_utc_aware(row.last_occurred_at),
        )

    def record(self, value: ResearchFailureCase) -> ResearchFailureCase:
        with self._session_factory() as session:
            row = session.scalar(select(ResearchFailureCaseModel).where(
                ResearchFailureCaseModel.case_key == value.case_key
            ))
            if row is None:
                row = ResearchFailureCaseModel(**{
                    field: getattr(value, field)
                    for field in value.__dataclass_fields__
                    if field != "id"
                })
                session.add(row)
            else:
                row.module = value.module
                row.title = value.title
                row.severity = value.severity
                row.status = value.status
                row.evidence_json = value.evidence_json
                row.cause = value.cause
                row.correction = value.correction
                row.verification = value.verification
                row.repeat_count += 1
                row.last_occurred_at = value.last_occurred_at
            session.commit()
            session.refresh(row)
            return self._entity(row)

    def list_recent(self, limit: int = 50) -> list[ResearchFailureCase]:
        statement = (
            select(ResearchFailureCaseModel)
            .order_by(
                ResearchFailureCaseModel.last_occurred_at.desc(),
                ResearchFailureCaseModel.id.desc(),
            )
            .limit(limit)
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._entity(row) for row in rows]

    def resolve(self, case_key: str, verification: str) -> ResearchFailureCase:
        with self._session_factory() as session:
            row = session.scalar(select(ResearchFailureCaseModel).where(
                ResearchFailureCaseModel.case_key == case_key
            ))
            if row is None:
                raise LookupError(f"Failure case {case_key} does not exist")
            row.status = "resolved"
            row.verification = verification
            session.commit()
            session.refresh(row)
            return self._entity(row)


class SqlAlchemyShadowTradingRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _entity(row: ShadowOrderModel) -> ShadowOrder:
        def aware(value: datetime | None) -> datetime | None:
            if value is None:
                return None
            return value if value.tzinfo else value.replace(tzinfo=UTC)

        return ShadowOrder(
            id=row.id, symbol=row.symbol, algorithm=row.algorithm,
            experiment_id=row.experiment_id, decision_time=aware(row.decision_time),
            data_available_time=aware(row.data_available_time),
            reference_price=row.reference_price,
            previous_target_weight=row.previous_target_weight,
            target_weight=row.target_weight, side=row.side, quantity=row.quantity,
            notional_twd=row.notional_twd,
            status=ShadowOrderStatus(row.status), promotion_gate=row.promotion_gate,
            reason=row.reason, created_at=aware(row.created_at),
            execution_time=aware(row.execution_time), fill_price=row.fill_price,
            gross_twd=row.gross_twd, commission_twd=row.commission_twd,
            transaction_tax_twd=row.transaction_tax_twd, pnl_twd=row.pnl_twd,
            return_rate=row.return_rate, benchmark_return=row.benchmark_return,
        )

    def save(self, order: ShadowOrder) -> int:
        values = {
            key: (getattr(order, key).value if key == "status" else getattr(order, key))
            for key in order.__dataclass_fields__ if key != "id"
        }
        with self._session_factory() as session:
            row = ShadowOrderModel(**values)
            session.add(row)
            session.commit()
            session.refresh(row)
            return int(row.id)

    def get_decision(
        self, symbol: str, algorithm: str, experiment_id: int, decision_time: datetime
    ) -> ShadowOrder | None:
        statement = select(ShadowOrderModel).where(
            ShadowOrderModel.symbol == symbol,
            ShadowOrderModel.algorithm == algorithm,
            ShadowOrderModel.experiment_id == experiment_id,
            ShadowOrderModel.decision_time == decision_time,
        )
        with self._session_factory() as session:
            row = session.scalar(statement)
        return self._entity(row) if row else None

    def get_order(self, order_id: int) -> ShadowOrder | None:
        with self._session_factory() as session:
            row = session.get(ShadowOrderModel, order_id)
        return self._entity(row) if row else None

    def list_orders(self, limit: int = 200) -> list[ShadowOrder]:
        statement = (
            select(ShadowOrderModel)
            .order_by(ShadowOrderModel.decision_time.desc(), ShadowOrderModel.id.desc())
            .limit(limit)
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._entity(row) for row in rows]

    def list_pending(self) -> list[ShadowOrder]:
        statement = (
            select(ShadowOrderModel)
            .where(ShadowOrderModel.status == ShadowOrderStatus.PENDING.value)
            .order_by(ShadowOrderModel.decision_time, ShadowOrderModel.id)
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._entity(row) for row in rows]

    def update(self, order: ShadowOrder) -> None:
        if order.id is None:
            raise ValueError("Cannot update an unsaved shadow order")
        with self._session_factory() as session:
            row = session.get(ShadowOrderModel, order.id)
            if row is None:
                raise ValueError(f"Shadow order {order.id} does not exist")
            for key in order.__dataclass_fields__:
                if key == "id":
                    continue
                value = getattr(order, key)
                setattr(row, key, value.value if key == "status" else value)
            session.commit()


class SqlAlchemyPromotionRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @classmethod
    def _review_entity(cls, row: PromotionReviewModel) -> PromotionReview:
        return PromotionReview(
            id=row.id, symbol=row.symbol, algorithm=row.algorithm,
            experiment_id=row.experiment_id, policy_version=row.policy_version,
            status=PromotionStatus(row.status), checks_json=row.checks_json,
            metrics_json=row.metrics_json, reviewer=row.reviewer,
            review_note=row.review_note, evaluated_at=cls._aware(row.evaluated_at),
            reviewed_at=cls._aware(row.reviewed_at),
            approval_expires_at=cls._aware(row.approval_expires_at),
            created_at=cls._aware(row.created_at), updated_at=cls._aware(row.updated_at),
        )

    @classmethod
    def _execution_entity(cls, row: BrokerExecutionAuditModel) -> BrokerExecutionAudit:
        return BrokerExecutionAudit(
            id=row.id, promotion_review_id=row.promotion_review_id,
            shadow_order_id=row.shadow_order_id, client_order_id=row.client_order_id,
            environment=row.environment, symbol=row.symbol, side=row.side,
            quantity=row.quantity, order_type=row.order_type,
            limit_price=row.limit_price, status=row.status,
            broker_order_id=row.broker_order_id, message=row.message,
            created_at=cls._aware(row.created_at), updated_at=cls._aware(row.updated_at),
        )

    def save_review(self, review: PromotionReview) -> int:
        values = {
            key: (getattr(review, key).value if key == "status" else getattr(review, key))
            for key in review.__dataclass_fields__ if key != "id"
        }
        with self._session_factory() as session:
            row = PromotionReviewModel(**values)
            session.add(row)
            session.commit()
            session.refresh(row)
            return int(row.id)

    def update_review(self, review: PromotionReview) -> None:
        if review.id is None:
            raise ValueError("Cannot update an unsaved promotion review")
        with self._session_factory() as session:
            row = session.get(PromotionReviewModel, review.id)
            if row is None:
                raise ValueError(f"Promotion review {review.id} does not exist")
            for key in review.__dataclass_fields__:
                if key == "id":
                    continue
                value = getattr(review, key)
                setattr(row, key, value.value if key == "status" else value)
            session.commit()

    def get_review(self, review_id: int) -> PromotionReview | None:
        with self._session_factory() as session:
            row = session.get(PromotionReviewModel, review_id)
        return self._review_entity(row) if row else None

    def find_review(
        self, symbol: str, algorithm: str, experiment_id: int, policy_version: str
    ) -> PromotionReview | None:
        statement = select(PromotionReviewModel).where(
            PromotionReviewModel.symbol == symbol,
            PromotionReviewModel.algorithm == algorithm,
            PromotionReviewModel.experiment_id == experiment_id,
            PromotionReviewModel.policy_version == policy_version,
        )
        with self._session_factory() as session:
            row = session.scalar(statement)
        return self._review_entity(row) if row else None

    def list_reviews(self, limit: int = 200) -> list[PromotionReview]:
        statement = (
            select(PromotionReviewModel)
            .order_by(PromotionReviewModel.updated_at.desc(), PromotionReviewModel.id.desc())
            .limit(limit)
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._review_entity(row) for row in rows]

    def save_execution(self, audit: BrokerExecutionAudit) -> int:
        values = {
            key: getattr(audit, key)
            for key in audit.__dataclass_fields__ if key != "id"
        }
        with self._session_factory() as session:
            row = BrokerExecutionAuditModel(**values)
            session.add(row)
            session.commit()
            session.refresh(row)
            return int(row.id)

    def get_execution_by_client_id(
        self, client_order_id: str
    ) -> BrokerExecutionAudit | None:
        statement = select(BrokerExecutionAuditModel).where(
            BrokerExecutionAuditModel.client_order_id == client_order_id
        )
        with self._session_factory() as session:
            row = session.scalar(statement)
        return self._execution_entity(row) if row else None

    def list_executions(self, limit: int = 200) -> list[BrokerExecutionAudit]:
        statement = (
            select(BrokerExecutionAuditModel)
            .order_by(BrokerExecutionAuditModel.created_at.desc(), BrokerExecutionAuditModel.id.desc())
            .limit(limit)
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._execution_entity(row) for row in rows]


class SqlAlchemyMarketBarRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def add_missing(self, bars: list[MarketBar]) -> int:
        inserted = 0
        with self._session_factory() as session:
            for bar in bars:
                event_time = _utc_naive(bar.event_time)
                available_time = _utc_naive(bar.available_time)
                ingested_at = _utc_naive(bar.ingested_at)
                existing = session.scalar(
                    select(MarketBarModel.id).where(
                        MarketBarModel.symbol == bar.symbol,
                        MarketBarModel.interval == bar.interval,
                        MarketBarModel.source == bar.source,
                        MarketBarModel.event_time == event_time,
                        MarketBarModel.available_time == available_time,
                    )
                )
                if existing is not None:
                    continue
                session.add(
                    MarketBarModel(
                        symbol=bar.symbol,
                        market=bar.market,
                        interval=bar.interval,
                        event_time=event_time,
                        available_time=available_time,
                        ingested_at=ingested_at,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        adjusted_close=bar.adjusted_close,
                        volume=bar.volume,
                        source=bar.source,
                    )
                )
                inserted += 1
            session.commit()
        return inserted

    def list_coverage(self) -> list[DataCoverage]:
        statement = (
            select(
                MarketBarModel.symbol,
                MarketBarModel.market,
                MarketBarModel.interval,
                MarketBarModel.source,
                func.count(MarketBarModel.id),
                func.min(MarketBarModel.event_time),
                func.max(MarketBarModel.event_time),
                func.max(MarketBarModel.ingested_at),
            )
            .group_by(
                MarketBarModel.symbol,
                MarketBarModel.market,
                MarketBarModel.interval,
                MarketBarModel.source,
            )
            .order_by(MarketBarModel.symbol)
        )
        with self._session_factory() as session:
            rows = session.execute(statement).all()
        return [
            DataCoverage(
                symbol=row[0],
                market=row[1],
                interval=row[2],
                source=row[3],
                row_count=int(row[4]),
                first_event_time=_utc_aware(row[5]),
                last_event_time=_utc_aware(row[6]),
                last_ingested_at=_utc_aware(row[7]),
            )
            for row in rows
        ]

    def latest_event_time(self, symbol: str, interval: str, source: str) -> datetime | None:
        statement = select(func.max(MarketBarModel.event_time)).where(
            MarketBarModel.symbol == symbol,
            MarketBarModel.interval == interval,
            MarketBarModel.source == source,
        )
        with self._session_factory() as session:
            value = session.scalar(statement)
        return _utc_aware(value) if value is not None else None

    def count_daily_sessions(
        self, market: str | None = None, as_of: datetime | None = None
    ) -> dict[str, int]:
        statement = (
            select(
                MarketBarModel.symbol,
                func.count(func.distinct(MarketBarModel.event_time)),
            )
            .where(MarketBarModel.interval == "1d")
            .group_by(MarketBarModel.symbol)
        )
        if market is not None:
            statement = statement.where(MarketBarModel.market == market.upper())
        if as_of is not None:
            statement = statement.where(
                MarketBarModel.available_time <= _utc_naive(as_of)
            )
        with self._session_factory() as session:
            rows = session.execute(statement).all()
        return {str(symbol): int(count) for symbol, count in rows}

    def latest_closes(
        self, symbols: list[str], as_of: datetime | None = None
    ) -> dict[str, float]:
        normalized = sorted({symbol.upper() for symbol in symbols})
        if not normalized:
            return {}
        latest = (
            select(
                MarketBarModel.symbol.label("symbol"),
                func.max(MarketBarModel.event_time).label("event_time"),
            )
            .where(
                MarketBarModel.symbol.in_(normalized),
                MarketBarModel.interval == "1d",
            )
            .group_by(MarketBarModel.symbol)
            .subquery()
        )
        if as_of is not None:
            latest = (
                select(
                    MarketBarModel.symbol.label("symbol"),
                    func.max(MarketBarModel.event_time).label("event_time"),
                )
                .where(
                    MarketBarModel.symbol.in_(normalized),
                    MarketBarModel.interval == "1d",
                    MarketBarModel.available_time <= _utc_naive(as_of),
                )
                .group_by(MarketBarModel.symbol)
                .subquery()
            )
        statement = (
            select(MarketBarModel.symbol, MarketBarModel.close)
            .join(
                latest,
                (MarketBarModel.symbol == latest.c.symbol)
                & (MarketBarModel.event_time == latest.c.event_time),
            )
            .where(MarketBarModel.interval == "1d")
            .order_by(MarketBarModel.symbol, MarketBarModel.source)
        )
        if as_of is not None:
            statement = statement.where(
                MarketBarModel.available_time <= _utc_naive(as_of)
            )
        with self._session_factory() as session:
            rows = session.execute(statement).all()
        return {str(symbol): float(close) for symbol, close in rows}

    def list_bars(
        self,
        symbol: str,
        interval: str = "1d",
        source: str | None = None,
        as_of: datetime | None = None,
    ) -> list[MarketBar]:
        statement = select(MarketBarModel).where(
            MarketBarModel.symbol == symbol.upper(),
            MarketBarModel.interval == interval,
        )
        if source is not None:
            statement = statement.where(MarketBarModel.source == source)
        if as_of is not None:
            statement = statement.where(MarketBarModel.available_time <= _utc_naive(as_of))
        statement = statement.order_by(
            MarketBarModel.event_time,
            MarketBarModel.available_time.desc(),
            MarketBarModel.ingested_at.desc(),
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        # A point-in-time query must expose one revision per session.  The
        # newest revision that was already public at ``as_of`` wins.
        latest: dict[datetime, MarketBarModel] = {}
        for row in rows:
            latest.setdefault(row.event_time, row)
        return [
            MarketBar(
                symbol=row.symbol,
                market=row.market,
                interval=row.interval,
                event_time=_utc_aware(row.event_time),
                available_time=_utc_aware(row.available_time),
                ingested_at=_utc_aware(row.ingested_at),
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                adjusted_close=row.adjusted_close,
                volume=row.volume,
                source=row.source,
            )
            for row in latest.values()
        ]

    def list_bars_for_symbols(
        self,
        symbols: list[str],
        interval: str = "1d",
        start: datetime | None = None,
        end: datetime | None = None,
        as_of: datetime | None = None,
    ) -> dict[str, list[MarketBar]]:
        """Load a bounded multi-stock window in one query.

        Historical decision replay used to open hundreds of SQLite sessions,
        one for every stock.  Besides being slow, that also amplified lock
        contention with the background downloader.  Keep the same point-in-time
        revision rule while grouping the requested symbols in one read.
        """
        normalized = sorted({symbol.upper() for symbol in symbols})
        if not normalized:
            return {}
        statement = select(MarketBarModel).where(
            MarketBarModel.symbol.in_(normalized),
            MarketBarModel.interval == interval,
        )
        if start is not None:
            statement = statement.where(
                MarketBarModel.event_time >= _utc_naive(start)
            )
        if end is not None:
            statement = statement.where(
                MarketBarModel.event_time <= _utc_naive(end)
            )
        if as_of is not None:
            statement = statement.where(
                MarketBarModel.available_time <= _utc_naive(as_of)
            )
        statement = statement.order_by(
            MarketBarModel.symbol,
            MarketBarModel.event_time,
            MarketBarModel.available_time.desc(),
            MarketBarModel.ingested_at.desc(),
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        latest: dict[tuple[str, datetime], MarketBarModel] = {}
        for row in rows:
            latest.setdefault((row.symbol, row.event_time), row)
        output: dict[str, list[MarketBar]] = {symbol: [] for symbol in normalized}
        for row in latest.values():
            output[row.symbol].append(MarketBar(
                symbol=row.symbol,
                market=row.market,
                interval=row.interval,
                event_time=_utc_aware(row.event_time),
                available_time=_utc_aware(row.available_time),
                ingested_at=_utc_aware(row.ingested_at),
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                adjusted_close=row.adjusted_close,
                volume=row.volume,
                source=row.source,
            ))
        return output

    def list_recent_bars(
        self, symbol: str, start: datetime, as_of: datetime
    ) -> list[MarketBar]:
        """Load only the bounded audit window used by the quality terminal."""
        statement = (
            select(MarketBarModel)
            .where(
                MarketBarModel.symbol == symbol.upper(),
                MarketBarModel.interval == "1d",
                MarketBarModel.event_time >= _utc_naive(start),
                MarketBarModel.available_time <= _utc_naive(as_of),
            )
            .order_by(
                MarketBarModel.event_time,
                MarketBarModel.available_time.desc(),
                MarketBarModel.ingested_at.desc(),
            )
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        latest: dict[datetime, MarketBarModel] = {}
        for row in rows:
            latest.setdefault(row.event_time, row)
        return [
            MarketBar(
                symbol=row.symbol, market=row.market, interval=row.interval,
                event_time=_utc_aware(row.event_time),
                available_time=_utc_aware(row.available_time),
                ingested_at=_utc_aware(row.ingested_at),
                open=row.open, high=row.high, low=row.low, close=row.close,
                adjusted_close=row.adjusted_close, volume=row.volume, source=row.source,
            )
            for row in latest.values()
        ]


class SqlAlchemyPointInTimeDataRepository:
    """Stores immutable source revisions and resolves what was visible as-of time."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def upsert_datasets(self, values: list[PointInTimeDataset]) -> None:
        with self._session_factory() as session:
            for value in values:
                row = session.get(PointInTimeDatasetModel, value.dataset_key)
                fields = {
                    key: getattr(value, key)
                    for key in value.__dataclass_fields__
                    if key != "dataset_key"
                }
                if row is None:
                    session.add(PointInTimeDatasetModel(dataset_key=value.dataset_key, **fields))
                else:
                    for key, field_value in fields.items():
                        setattr(row, key, field_value)
            session.commit()

    def list_datasets(self, enabled: bool | None = None) -> list[PointInTimeDataset]:
        statement = select(PointInTimeDatasetModel)
        if enabled is not None:
            statement = statement.where(PointInTimeDatasetModel.enabled == enabled)
        statement = statement.order_by(
            PointInTimeDatasetModel.category, PointInTimeDatasetModel.dataset_key
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._dataset_entity(row) for row in rows]

    def get_dataset(self, dataset_key: str) -> PointInTimeDataset | None:
        with self._session_factory() as session:
            row = session.get(PointInTimeDatasetModel, dataset_key)
        return self._dataset_entity(row) if row else None

    def add_revisions(self, values: list[PointInTimeObservation]) -> int:
        inserted = 0
        with self._session_factory() as session:
            for value in values:
                existing = session.scalar(select(PointInTimeObservationModel.id).where(
                    PointInTimeObservationModel.dataset_key == value.dataset_key,
                    PointInTimeObservationModel.entity_id == value.entity_id,
                    PointInTimeObservationModel.event_time == value.event_time,
                    PointInTimeObservationModel.revision_key == value.revision_key,
                    PointInTimeObservationModel.content_hash == value.content_hash,
                ))
                if existing is not None:
                    continue
                session.add(PointInTimeObservationModel(**{
                    key: getattr(value, key)
                    for key in value.__dataclass_fields__
                    if key != "id"
                }))
                inserted += 1
            session.commit()
        return inserted

    def list_observations(
        self,
        dataset_key: str,
        entity_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        as_of: datetime | None = None,
        limit: int | None = None,
    ) -> list[PointInTimeObservation]:
        statement = select(PointInTimeObservationModel).where(
            PointInTimeObservationModel.dataset_key == dataset_key
        )
        if entity_id:
            statement = statement.where(
                PointInTimeObservationModel.entity_id == entity_id.upper()
            )
        if start is not None:
            statement = statement.where(PointInTimeObservationModel.event_time >= start)
        if end is not None:
            statement = statement.where(PointInTimeObservationModel.event_time <= end)
        if as_of is not None:
            statement = statement.where(PointInTimeObservationModel.available_time <= as_of)
        statement = statement.order_by(
            PointInTimeObservationModel.event_time.desc(),
            PointInTimeObservationModel.revision_key,
            PointInTimeObservationModel.available_time.desc(),
            PointInTimeObservationModel.ingested_at.desc(),
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        # One row per logical record. Later corrections only become visible once
        # their available_time has passed, which prevents look-ahead leakage.
        latest: dict[tuple[datetime, str], PointInTimeObservationModel] = {}
        for row in rows:
            latest.setdefault((row.event_time, row.revision_key), row)
        selected = list(latest.values())
        if limit is not None:
            selected = selected[:max(0, limit)]
        return [self._observation_entity(row) for row in selected]

    def list_coverage(self) -> list[PointInTimeCoverage]:
        statement = select(PointInTimeObservationModel).order_by(
            PointInTimeObservationModel.dataset_key,
            PointInTimeObservationModel.entity_id,
            PointInTimeObservationModel.event_time,
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        grouped: dict[tuple[str, str, str], list[PointInTimeObservationModel]] = {}
        for row in rows:
            grouped.setdefault((row.dataset_key, row.entity_id, row.source), []).append(row)
        output: list[PointInTimeCoverage] = []
        for (dataset_key, entity_id, source), values in grouped.items():
            logical = {(item.event_time, item.revision_key) for item in values}
            output.append(PointInTimeCoverage(
                dataset_key=dataset_key,
                entity_id=entity_id,
                source=source,
                row_count=len(logical),
                revision_count=len(values) - len(logical),
                first_event_time=_utc_aware(min(item.event_time for item in values)),
                last_event_time=_utc_aware(max(item.event_time for item in values)),
                last_available_time=_utc_aware(max(item.available_time for item in values)),
                last_ingested_at=_utc_aware(max(item.ingested_at for item in values)),
            ))
        return output

    @staticmethod
    def _dataset_entity(row: PointInTimeDatasetModel) -> PointInTimeDataset:
        values = {
            key: getattr(row, key) for key in PointInTimeDataset.__dataclass_fields__
        }
        values["updated_at"] = _utc_aware(values["updated_at"])
        return PointInTimeDataset(**values)

    @staticmethod
    def _observation_entity(row: PointInTimeObservationModel) -> PointInTimeObservation:
        values = {
            key: getattr(row, key) for key in PointInTimeObservation.__dataclass_fields__
        }
        for key in ("event_time", "available_time", "ingested_at"):
            values[key] = _utc_aware(values[key])
        return PointInTimeObservation(**values)


class SqlAlchemyTaiwanDataRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def add_revisions(self, values: list[TaiwanDataRecord]) -> int:
        inserted = 0
        with self._session_factory() as session:
            for value in values:
                existing = session.scalar(
                    select(TaiwanDataRecordModel.id).where(
                        TaiwanDataRecordModel.source == value.source,
                        TaiwanDataRecordModel.dataset == value.dataset,
                        TaiwanDataRecordModel.symbol == value.symbol,
                        TaiwanDataRecordModel.event_time == value.event_time,
                        TaiwanDataRecordModel.available_time == value.available_time,
                        TaiwanDataRecordModel.record_key == value.record_key,
                        TaiwanDataRecordModel.content_hash == value.content_hash,
                    )
                )
                if existing is not None:
                    continue
                session.add(
                    TaiwanDataRecordModel(
                        symbol=value.symbol,
                        dataset=value.dataset,
                        event_time=value.event_time,
                        available_time=value.available_time,
                        ingested_at=value.ingested_at,
                        record_key=value.record_key,
                        content_hash=value.content_hash,
                        fields_json=value.fields_json,
                        source=value.source,
                    )
                )
                inserted += 1
            session.commit()
        return inserted

    def list_records(
        self,
        dataset: str | None = None,
        symbol: str | None = None,
        as_of: datetime | None = None,
    ) -> list[TaiwanDataRecord]:
        statement = select(TaiwanDataRecordModel)
        if dataset:
            statement = statement.where(TaiwanDataRecordModel.dataset == dataset)
        if symbol:
            statement = statement.where(TaiwanDataRecordModel.symbol == symbol.upper())
        if as_of:
            statement = statement.where(TaiwanDataRecordModel.available_time <= as_of)
        statement = statement.order_by(
            TaiwanDataRecordModel.event_time,
            TaiwanDataRecordModel.ingested_at,
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._entity(row) for row in rows]

    def list_coverage(self) -> list[TaiwanDataCoverage]:
        statement = (
            select(
                TaiwanDataRecordModel.symbol,
                TaiwanDataRecordModel.dataset,
                TaiwanDataRecordModel.source,
                func.count(TaiwanDataRecordModel.id),
                func.min(TaiwanDataRecordModel.event_time),
                func.max(TaiwanDataRecordModel.event_time),
                func.max(TaiwanDataRecordModel.available_time),
                func.max(TaiwanDataRecordModel.ingested_at),
            )
            .group_by(
                TaiwanDataRecordModel.symbol,
                TaiwanDataRecordModel.dataset,
                TaiwanDataRecordModel.source,
            )
            .order_by(TaiwanDataRecordModel.symbol, TaiwanDataRecordModel.dataset)
        )
        with self._session_factory() as session:
            rows = session.execute(statement).all()
        return [
            TaiwanDataCoverage(
                symbol=row[0],
                dataset=row[1],
                source=row[2],
                row_count=int(row[3]),
                first_event_time=row[4],
                last_event_time=row[5],
                last_available_time=row[6],
                last_ingested_at=row[7],
            )
            for row in rows
        ]

    def list_latest(self, symbol: str | None = None) -> list[TaiwanDataRecord]:
        latest_events = (
            select(
                TaiwanDataRecordModel.symbol.label("symbol"),
                TaiwanDataRecordModel.dataset.label("dataset"),
                func.max(TaiwanDataRecordModel.event_time).label("event_time"),
            )
            .group_by(TaiwanDataRecordModel.symbol, TaiwanDataRecordModel.dataset)
            .subquery()
        )
        statement = select(TaiwanDataRecordModel).join(
            latest_events,
            (TaiwanDataRecordModel.symbol == latest_events.c.symbol)
            & (TaiwanDataRecordModel.dataset == latest_events.c.dataset)
            & (TaiwanDataRecordModel.event_time == latest_events.c.event_time),
        )
        if symbol:
            statement = statement.where(TaiwanDataRecordModel.symbol == symbol.upper())
        statement = statement.order_by(TaiwanDataRecordModel.ingested_at.desc())
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        latest: dict[tuple[str, str], TaiwanDataRecordModel] = {}
        for row in rows:
            latest.setdefault((row.symbol, row.dataset), row)
        return [self._entity(row) for row in latest.values()]

    def available_dataset_keys(
        self, symbols: list[str], datasets: list[str], as_of: datetime
    ) -> set[tuple[str, str]]:
        """Return coverage keys without grouping or deserializing the history table."""
        if not symbols or not datasets:
            return set()
        statement = (
            select(TaiwanDataRecordModel.symbol, TaiwanDataRecordModel.dataset)
            .where(
                TaiwanDataRecordModel.symbol.in_(symbols),
                TaiwanDataRecordModel.dataset.in_(datasets),
                TaiwanDataRecordModel.available_time <= _utc_naive(as_of),
            )
            .distinct()
        )
        with self._session_factory() as session:
            return {(str(row[0]), str(row[1])) for row in session.execute(statement)}

    def latest_event_time(self, dataset: str, symbol: str, source: str) -> datetime | None:
        statement = select(func.max(TaiwanDataRecordModel.event_time)).where(
            TaiwanDataRecordModel.dataset == dataset,
            TaiwanDataRecordModel.symbol == symbol.upper(),
            TaiwanDataRecordModel.source == source,
        )
        with self._session_factory() as session:
            return session.scalar(statement)

    @staticmethod
    def _entity(row: TaiwanDataRecordModel) -> TaiwanDataRecord:
        return TaiwanDataRecord(
            id=row.id,
            symbol=row.symbol,
            dataset=row.dataset,
            event_time=row.event_time,
            available_time=row.available_time,
            ingested_at=row.ingested_at,
            record_key=row.record_key,
            content_hash=row.content_hash,
            fields_json=row.fields_json,
            source=row.source,
        )

class SqlAlchemyResearchUniverseRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def list_all(self) -> list[ResearchAsset]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(ResearchAssetModel).order_by(
                    ResearchAssetModel.active.desc(), ResearchAssetModel.market, ResearchAssetModel.symbol
                )
            ).all()
        return [self._to_entity(row) for row in rows]

    def list_active(self, market: str | None = None) -> list[ResearchAsset]:
        statement = select(ResearchAssetModel).where(ResearchAssetModel.active.is_(True))
        if market is not None:
            statement = statement.where(ResearchAssetModel.market == market.upper())
        statement = statement.order_by(ResearchAssetModel.market, ResearchAssetModel.symbol)
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._to_entity(row) for row in rows]

    def get(self, symbol: str) -> ResearchAsset | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(ResearchAssetModel).where(ResearchAssetModel.symbol == symbol.upper())
            )
        return self._to_entity(row) if row else None

    def add(self, asset: ResearchAsset) -> ResearchAsset:
        row = ResearchAssetModel(
            symbol=asset.symbol.upper(),
            market=asset.market.upper(),
            asset_type=asset.asset_type,
            sector=asset.sector,
            company_name=asset.company_name,
            company_abbreviation=asset.company_abbreviation,
            industry_code=asset.industry_code,
            paid_in_capital=asset.paid_in_capital,
            issued_shares=asset.issued_shares,
            market_value_twd=asset.market_value_twd,
            market_rank=asset.market_rank,
            metadata_source=asset.metadata_source,
            metadata_updated_at=asset.metadata_updated_at,
            benchmark_symbol=asset.benchmark_symbol,
            active=asset.active,
            data_start=asset.data_start,
            created_at=asset.created_at,
            updated_at=asset.updated_at,
        )
        with self._session_factory() as session:
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._to_entity(row)

    def set_active(self, symbol: str, active: bool) -> bool:
        with self._session_factory() as session:
            row = session.scalar(
                select(ResearchAssetModel).where(ResearchAssetModel.symbol == symbol.upper())
            )
            if row is None:
                return False
            row.active = active
            row.updated_at = datetime.now(UTC)
            session.commit()
            return True

    def update_metadata(self, symbol: str, **values: object) -> bool:
        allowed = {
            "company_name", "company_abbreviation", "industry_code", "sector",
            "paid_in_capital", "issued_shares", "market_value_twd", "market_rank",
            "metadata_source", "metadata_updated_at",
        }
        with self._session_factory() as session:
            row = session.scalar(
                select(ResearchAssetModel).where(ResearchAssetModel.symbol == symbol.upper())
            )
            if row is None:
                return False
            for name, value in values.items():
                if name in allowed and value is not None:
                    setattr(row, name, value)
            row.updated_at = datetime.now(UTC)
            session.commit()
            return True

    def upsert_memberships(self, values: list[UniverseMembership]) -> int:
        changed = 0
        with self._session_factory() as session:
            for value in values:
                row = session.scalar(
                    select(UniverseMembershipModel).where(
                        UniverseMembershipModel.symbol == value.symbol.upper(),
                        UniverseMembershipModel.valid_from == value.valid_from,
                        UniverseMembershipModel.source == value.source,
                    )
                )
                if row is None:
                    session.add(UniverseMembershipModel(
                        symbol=value.symbol.upper(), market=value.market.upper(),
                        valid_from=value.valid_from, valid_to=value.valid_to,
                        start_is_exact=value.start_is_exact, end_is_exact=value.end_is_exact,
                        source=value.source, reason=value.reason, recorded_at=value.recorded_at,
                    ))
                    changed += 1
                elif any((
                    row.valid_to != value.valid_to,
                    row.start_is_exact != value.start_is_exact,
                    row.end_is_exact != value.end_is_exact,
                    row.reason != value.reason,
                )):
                    row.valid_to = value.valid_to
                    row.start_is_exact = value.start_is_exact
                    row.end_is_exact = value.end_is_exact
                    row.reason = value.reason
                    row.recorded_at = value.recorded_at
                    changed += 1
            session.commit()
        return changed

    def list_memberships(self, market: str | None = None) -> list[UniverseMembership]:
        statement = select(UniverseMembershipModel)
        if market:
            statement = statement.where(UniverseMembershipModel.market == market.upper())
        statement = statement.order_by(UniverseMembershipModel.valid_from, UniverseMembershipModel.symbol)
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._membership_to_entity(row) for row in rows]

    def list_members_on(self, effective_date: date, market: str | None = None) -> list[UniverseMembership]:
        statement = select(UniverseMembershipModel).where(
            UniverseMembershipModel.valid_from <= effective_date,
            (UniverseMembershipModel.valid_to.is_(None)) | (UniverseMembershipModel.valid_to >= effective_date),
        )
        if market:
            statement = statement.where(UniverseMembershipModel.market == market.upper())
        statement = statement.order_by(UniverseMembershipModel.symbol)
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._membership_to_entity(row) for row in rows]

    @staticmethod
    def _membership_to_entity(row: UniverseMembershipModel) -> UniverseMembership:
        return UniverseMembership(
            id=row.id, symbol=row.symbol, market=row.market,
            valid_from=row.valid_from, valid_to=row.valid_to,
            start_is_exact=row.start_is_exact, end_is_exact=row.end_is_exact,
            source=row.source, reason=row.reason, recorded_at=row.recorded_at,
        )

    @staticmethod
    def _to_entity(row: ResearchAssetModel) -> ResearchAsset:
        return ResearchAsset(
            id=row.id,
            symbol=row.symbol,
            market=row.market,
            asset_type=row.asset_type,
            sector=row.sector,
            company_name=row.company_name,
            company_abbreviation=row.company_abbreviation,
            industry_code=row.industry_code,
            paid_in_capital=row.paid_in_capital,
            issued_shares=row.issued_shares,
            market_value_twd=row.market_value_twd,
            market_rank=row.market_rank,
            metadata_source=row.metadata_source,
            metadata_updated_at=row.metadata_updated_at,
            benchmark_symbol=row.benchmark_symbol,
            active=row.active,
            data_start=row.data_start,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class SqlAlchemyResearchKnowledgeRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def save_report(self, report: DailyResearchReport) -> int:
        with self._session_factory() as session:
            row = session.scalar(select(DailyResearchReportModel).where(
                DailyResearchReportModel.report_date == report.report_date,
                DailyResearchReportModel.market == report.market.upper(),
            ))
            if row is None:
                row = DailyResearchReportModel(
                    report_date=report.report_date, market=report.market.upper(), title=report.title,
                    body_markdown=report.body_markdown, sources_json=report.sources_json,
                    generated_at=report.generated_at,
                )
                session.add(row)
            else:
                row.title = report.title
                row.body_markdown = report.body_markdown
                row.sources_json = report.sources_json
                row.generated_at = report.generated_at
            session.commit()
            session.refresh(row)
            return int(row.id)

    def list_reports(self, market: str | None = None, limit: int = 30) -> list[DailyResearchReport]:
        statement = select(DailyResearchReportModel)
        if market:
            statement = statement.where(DailyResearchReportModel.market == market.upper())
        statement = statement.order_by(DailyResearchReportModel.report_date.desc()).limit(limit)
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [DailyResearchReport(
            id=row.id, report_date=row.report_date, market=row.market, title=row.title,
            body_markdown=row.body_markdown, sources_json=row.sources_json,
            generated_at=row.generated_at,
        ) for row in rows]

    def upsert_documents(self, values: list[KnowledgeDocument]) -> int:
        changed = 0
        with self._session_factory() as session:
            for value in values:
                row = session.scalar(select(KnowledgeDocumentModel).where(
                    KnowledgeDocumentModel.document_key == value.document_key
                ))
                if row is None:
                    session.add(KnowledgeDocumentModel(
                        document_key=value.document_key, document_type=value.document_type,
                        title=value.title, content=value.content, source_uri=value.source_uri,
                        updated_at=value.updated_at,
                    ))
                    changed += 1
                elif row.content != value.content or row.title != value.title:
                    row.document_type = value.document_type
                    row.title = value.title
                    row.content = value.content
                    row.source_uri = value.source_uri
                    row.updated_at = value.updated_at
                    changed += 1
            session.commit()
        return changed

    def list_documents(self) -> list[KnowledgeDocument]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(KnowledgeDocumentModel).order_by(KnowledgeDocumentModel.updated_at.desc())
            ).all()
        return [KnowledgeDocument(
            id=row.id, document_key=row.document_key, document_type=row.document_type,
            title=row.title, content=row.content, source_uri=row.source_uri,
            updated_at=row.updated_at,
        ) for row in rows]

    def remove_documents(self, document_keys: list[str]) -> int:
        if not document_keys:
            return 0
        with self._session_factory() as session:
            session.execute(delete(KnowledgeChunkModel).where(
                KnowledgeChunkModel.document_key.in_(document_keys)
            ))
            result = session.execute(delete(KnowledgeDocumentModel).where(
                KnowledgeDocumentModel.document_key.in_(document_keys)
            ))
            session.commit()
        return int(result.rowcount or 0)

    def list_chunks(self, document_key: str | None = None) -> list[KnowledgeChunk]:
        statement = select(KnowledgeChunkModel)
        if document_key:
            statement = statement.where(KnowledgeChunkModel.document_key == document_key)
        statement = statement.order_by(
            KnowledgeChunkModel.document_key, KnowledgeChunkModel.chunk_index
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [KnowledgeChunk(
            id=row.id, chunk_key=row.chunk_key, document_key=row.document_key,
            document_type=row.document_type, title=row.title,
            chunk_index=row.chunk_index, content=row.content,
            content_hash=row.content_hash, source_uri=row.source_uri,
            embedding_provider=row.embedding_provider,
            embedding_model=row.embedding_model,
            embedding_dimensions=row.embedding_dimensions,
            embedding_json=row.embedding_json, updated_at=row.updated_at,
        ) for row in rows]

    def replace_chunks(self, document_key: str, values: list[KnowledgeChunk]) -> int:
        expected = {
            (item.chunk_key, item.content_hash, item.embedding_provider, item.embedding_model)
            for item in values
        }
        with self._session_factory() as session:
            current_rows = session.scalars(select(KnowledgeChunkModel).where(
                KnowledgeChunkModel.document_key == document_key
            )).all()
            current = {
                (row.chunk_key, row.content_hash, row.embedding_provider, row.embedding_model)
                for row in current_rows
            }
            if current == expected:
                return 0
            session.execute(delete(KnowledgeChunkModel).where(
                KnowledgeChunkModel.document_key == document_key
            ))
            for value in values:
                session.add(KnowledgeChunkModel(
                    chunk_key=value.chunk_key, document_key=value.document_key,
                    document_type=value.document_type, title=value.title,
                    chunk_index=value.chunk_index, content=value.content,
                    content_hash=value.content_hash, source_uri=value.source_uri,
                    embedding_provider=value.embedding_provider,
                    embedding_model=value.embedding_model,
                    embedding_dimensions=value.embedding_dimensions,
                    embedding_json=value.embedding_json, updated_at=value.updated_at,
                ))
            session.commit()
        return len(values)

    def save_query_audit(self, value: RagQueryAudit) -> int:
        with self._session_factory() as session:
            row = RagQueryAuditModel(
                question=value.question, mode=value.mode,
                embedding_provider=value.embedding_provider,
                embedding_model=value.embedding_model,
                answer_provider=value.answer_provider,
                answer_model=value.answer_model,
                retrieval_json=value.retrieval_json, answer=value.answer,
                grounded=value.grounded, refusal_reason=value.refusal_reason,
                latency_ms=value.latency_ms, created_at=value.created_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return int(row.id)

    def list_query_audits(self, limit: int = 30) -> list[RagQueryAudit]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(RagQueryAuditModel)
                .order_by(RagQueryAuditModel.created_at.desc())
                .limit(limit)
            ).all()
        return [RagQueryAudit(
            id=row.id, question=row.question, mode=row.mode,
            embedding_provider=row.embedding_provider,
            embedding_model=row.embedding_model,
            answer_provider=row.answer_provider, answer_model=row.answer_model,
            retrieval_json=row.retrieval_json, answer=row.answer,
            grounded=row.grounded, refusal_reason=row.refusal_reason,
            latency_ms=row.latency_ms, created_at=row.created_at,
        ) for row in rows]


class SqlAlchemySchedulerJobRunRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def start(self, job_name: str, market: str, started_at: datetime) -> int:
        with self._session_factory() as session:
            row = SchedulerJobRunModel(
                job_name=job_name,
                market=market,
                status=JobRunStatus.RUNNING.value,
                started_at=started_at,
                metrics_json="{}",
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id

    def finish(
        self,
        run_id: int,
        status: str,
        completed_at: datetime,
        metrics_json: str,
        error: str | None,
    ) -> None:
        with self._session_factory() as session:
            row = session.get(SchedulerJobRunModel, run_id)
            if row is None:
                raise LookupError(f"Scheduler job run {run_id} does not exist")
            row.status = status
            row.completed_at = completed_at
            row.metrics_json = metrics_json
            row.error = error
            session.commit()

    def update_progress(self, run_id: int, metrics_json: str) -> None:
        with self._session_factory() as session:
            row = session.get(SchedulerJobRunModel, run_id)
            if row is None or row.status != JobRunStatus.RUNNING.value:
                return
            row.metrics_json = metrics_json
            session.commit()

    def fail_stale_running(
        self, cutoff: datetime, completed_at: datetime
    ) -> int:
        with self._session_factory() as session:
            result = session.execute(
                update(SchedulerJobRunModel)
                .where(
                    SchedulerJobRunModel.status == JobRunStatus.RUNNING.value,
                    SchedulerJobRunModel.started_at < _utc_naive(cutoff),
                )
                .values(
                    status=JobRunStatus.FAILED.value,
                    completed_at=_utc_naive(completed_at),
                    error="工作程序已中止；啟動時自動關閉逾時的執行紀錄",
                )
            )
            session.commit()
            return int(result.rowcount or 0)

    @staticmethod
    def _job_run(row: SchedulerJobRunModel) -> SchedulerJobRun:
        return SchedulerJobRun(
            id=row.id,
            job_name=row.job_name,
            market=row.market,
            status=JobRunStatus(row.status),
            started_at=row.started_at,
            completed_at=row.completed_at,
            metrics_json=row.metrics_json,
            error=row.error,
        )

    def list_recent(self, limit: int = 20) -> list[SchedulerJobRun]:
        statement = select(SchedulerJobRunModel).order_by(
            SchedulerJobRunModel.started_at.desc()
        ).limit(limit)
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._job_run(row) for row in rows]

    def latest_succeeded(
        self, job_name: str, market: str, since: datetime
    ) -> SchedulerJobRun | None:
        """Latest successful run started at or after ``since`` (stored as naive UTC)."""
        statement = (
            select(SchedulerJobRunModel)
            .where(
                SchedulerJobRunModel.job_name == job_name,
                SchedulerJobRunModel.market == market.upper(),
                SchedulerJobRunModel.status == JobRunStatus.SUCCEEDED.value,
                SchedulerJobRunModel.started_at >= _utc_naive(since),
            )
            .order_by(SchedulerJobRunModel.started_at.desc())
            .limit(1)
        )
        with self._session_factory() as session:
            row = session.scalars(statement).first()
        return self._job_run(row) if row is not None else None


class SqlAlchemyAutomationRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _schedule(row: AutomationScheduleModel) -> AutomationSchedule:
        return AutomationSchedule(
            job_key=row.job_key, display_name=row.display_name, market=row.market,
            hour=row.hour, minute=row.minute, weekdays=row.weekdays, timezone=row.timezone,
            enabled=row.enabled, max_retries=row.max_retries,
            notify_on_success=row.notify_on_success, notify_on_failure=row.notify_on_failure,
            updated_at=row.updated_at,
        )

    def upsert_schedule(self, value: AutomationSchedule) -> None:
        with self._session_factory() as session:
            row = session.get(AutomationScheduleModel, value.job_key)
            if row is None:
                row = AutomationScheduleModel(job_key=value.job_key)
                session.add(row)
            for field in (
                "display_name", "market", "hour", "minute", "weekdays", "timezone",
                "enabled", "max_retries", "notify_on_success", "notify_on_failure", "updated_at",
            ):
                setattr(row, field, getattr(value, field))
            session.commit()

    def get_schedule(self, job_key: str) -> AutomationSchedule | None:
        with self._session_factory() as session:
            row = session.get(AutomationScheduleModel, job_key)
            return self._schedule(row) if row else None

    def list_schedules(self) -> list[AutomationSchedule]:
        with self._session_factory() as session:
            rows = session.scalars(select(AutomationScheduleModel).order_by(AutomationScheduleModel.job_key)).all()
        return [self._schedule(row) for row in rows]

    def save_delivery(self, value: NotificationDelivery) -> int:
        with self._session_factory() as session:
            row = NotificationDeliveryModel(
                channel=value.channel, recipient=value.recipient, subject=value.subject,
                status=value.status, related_run_id=value.related_run_id,
                attempted_at=value.attempted_at, error=value.error,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return row.id

    def list_deliveries(self, limit: int = 20) -> list[NotificationDelivery]:
        with self._session_factory() as session:
            rows = session.scalars(select(NotificationDeliveryModel).order_by(
                NotificationDeliveryModel.attempted_at.desc()).limit(limit)).all()
        return [NotificationDelivery(
            id=row.id, channel=row.channel, recipient=row.recipient, subject=row.subject,
            status=row.status, related_run_id=row.related_run_id,
            attempted_at=row.attempted_at, error=row.error,
        ) for row in rows]


class SqlAlchemyAuthenticationRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _user(row: PlatformUserModel) -> PlatformUser:
        return PlatformUser(
            id=row.id, provider=row.provider, provider_subject=row.provider_subject,
            email=row.email, display_name=row.display_name, avatar_url=row.avatar_url,
            is_active=row.is_active, created_at=row.created_at, last_login_at=row.last_login_at,
        )

    def save_login_state(self, value: OAuthLoginState) -> None:
        with self._session_factory() as session:
            session.merge(OAuthLoginStateModel(
                state_hash=value.state_hash, browser_nonce_hash=value.browser_nonce_hash,
                code_verifier=value.code_verifier, next_path=value.next_path,
                expires_at=value.expires_at, used_at=value.used_at,
            ))
            session.commit()

    def consume_login_state(
        self, state_hash: str, browser_nonce_hash: str, now: datetime
    ) -> OAuthLoginState | None:
        with self._session_factory() as session:
            row = session.scalar(select(OAuthLoginStateModel).where(
                OAuthLoginStateModel.state_hash == state_hash,
                OAuthLoginStateModel.browser_nonce_hash == browser_nonce_hash,
                OAuthLoginStateModel.used_at.is_(None),
                OAuthLoginStateModel.expires_at >= now,
            ))
            if row is None:
                return None
            claimed = session.execute(update(OAuthLoginStateModel).where(
                OAuthLoginStateModel.state_hash == state_hash,
                OAuthLoginStateModel.browser_nonce_hash == browser_nonce_hash,
                OAuthLoginStateModel.used_at.is_(None),
                OAuthLoginStateModel.expires_at >= now,
            ).values(used_at=now).execution_options(synchronize_session=False))
            if claimed.rowcount != 1:
                session.rollback()
                return None
            value = OAuthLoginState(
                state_hash=row.state_hash, browser_nonce_hash=row.browser_nonce_hash,
                code_verifier=row.code_verifier, next_path=row.next_path,
                expires_at=row.expires_at, used_at=now,
            )
            session.commit()
            return value

    def upsert_user(self, value: PlatformUser) -> PlatformUser:
        with self._session_factory() as session:
            row = session.scalar(select(PlatformUserModel).where(
                PlatformUserModel.provider == value.provider,
                PlatformUserModel.provider_subject == value.provider_subject,
            ))
            if row is None:
                row = PlatformUserModel(
                    provider=value.provider, provider_subject=value.provider_subject,
                    created_at=value.created_at,
                )
                session.add(row)
            row.email = value.email
            row.display_name = value.display_name
            row.avatar_url = value.avatar_url
            row.is_active = value.is_active
            row.last_login_at = value.last_login_at
            session.commit()
            session.refresh(row)
            return self._user(row)

    def save_session(self, value: UserSession) -> None:
        with self._session_factory() as session:
            session.add(UserSessionModel(
                token_hash=value.token_hash, user_id=value.user_id,
                created_at=value.created_at, expires_at=value.expires_at,
                last_seen_at=value.last_seen_at, revoked_at=value.revoked_at,
            ))
            session.commit()

    def user_for_session(self, token_hash: str, now: datetime) -> PlatformUser | None:
        with self._session_factory() as session:
            statement = (
                select(PlatformUserModel)
                .join(UserSessionModel, UserSessionModel.user_id == PlatformUserModel.id)
                .where(
                    UserSessionModel.token_hash == token_hash,
                    UserSessionModel.revoked_at.is_(None),
                    UserSessionModel.expires_at >= now,
                    PlatformUserModel.is_active.is_(True),
                )
            )
            row = session.scalar(statement)
            if row is None:
                return None
            session.execute(update(UserSessionModel).where(
                UserSessionModel.token_hash == token_hash,
                UserSessionModel.last_seen_at <= now - timedelta(minutes=5),
            ).values(last_seen_at=now).execution_options(synchronize_session=False))
            session.commit()
            return self._user(row)

    def revoke_session(self, token_hash: str, now: datetime) -> None:
        with self._session_factory() as session:
            row = session.get(UserSessionModel, token_hash)
            if row is not None and row.revoked_at is None:
                row.revoked_at = now
                session.commit()


class SqlAlchemyPaperTradingRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _account(row: PaperAccountModel) -> PaperAccount:
        return PaperAccount(
            id=row.id, name=row.name, currency=row.currency,
            initial_cash=Decimal(row.initial_cash), cash=Decimal(row.cash),
            realized_pnl=Decimal(row.realized_pnl), created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _order(row: PaperOrderModel) -> PaperOrder:
        return PaperOrder(
            id=row.id, account_id=row.account_id, symbol=row.symbol, market=row.market,
            side=row.side, quantity=row.quantity, order_type=row.order_type,
            status=PaperOrderStatus(row.status), submitted_at=row.submitted_at,
            eligible_after_event_time=row.eligible_after_event_time,
            estimated_price=Decimal(row.estimated_price),
            rejection_reason=row.rejection_reason, completed_at=row.completed_at,
        )

    @staticmethod
    def _position(row: PaperPositionModel) -> PaperPosition:
        return PaperPosition(
            account_id=row.account_id, symbol=row.symbol, quantity=row.quantity,
            average_cost=Decimal(row.average_cost), realized_pnl=Decimal(row.realized_pnl),
            updated_at=row.updated_at,
        )

    @staticmethod
    def _fill(row: PaperFillModel) -> PaperFill:
        return PaperFill(
            id=row.id, order_id=row.order_id, account_id=row.account_id,
            symbol=row.symbol, side=row.side, quantity=row.quantity,
            price=Decimal(row.price), gross_amount=Decimal(row.gross_amount),
            commission=Decimal(row.commission), transaction_tax=Decimal(row.transaction_tax),
            slippage_bps=Decimal(row.slippage_bps), bar_event_time=row.bar_event_time,
            executed_at=row.executed_at,
        )

    def ensure_account(self, name: str, initial_cash: Decimal, now: datetime) -> PaperAccount:
        with self._session_factory() as session:
            row = session.scalar(select(PaperAccountModel).where(PaperAccountModel.name == name))
            if row is None:
                row = PaperAccountModel(
                    name=name, currency="TWD", initial_cash=initial_cash, cash=initial_cash,
                    realized_pnl=Decimal("0"), created_at=now, updated_at=now,
                )
                session.add(row)
                session.commit()
                session.refresh(row)
            return self._account(row)

    def get_account(self, account_id: int) -> PaperAccount | None:
        with self._session_factory() as session:
            row = session.get(PaperAccountModel, account_id)
            return self._account(row) if row else None

    def save_order(self, value: PaperOrder) -> PaperOrder:
        with self._session_factory() as session:
            row = PaperOrderModel(
                account_id=value.account_id, symbol=value.symbol, market=value.market,
                side=value.side, quantity=value.quantity, order_type=value.order_type,
                status=value.status.value, submitted_at=value.submitted_at,
                eligible_after_event_time=value.eligible_after_event_time,
                estimated_price=value.estimated_price,
                rejection_reason=value.rejection_reason, completed_at=value.completed_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._order(row)

    def get_order(self, order_id: int, account_id: int) -> PaperOrder | None:
        with self._session_factory() as session:
            row = session.scalar(select(PaperOrderModel).where(
                PaperOrderModel.id == order_id, PaperOrderModel.account_id == account_id,
            ))
            return self._order(row) if row else None

    def list_orders(self, account_id: int, limit: int = 100) -> list[PaperOrder]:
        with self._session_factory() as session:
            rows = session.scalars(select(PaperOrderModel).where(
                PaperOrderModel.account_id == account_id
            ).order_by(PaperOrderModel.submitted_at.desc()).limit(limit)).all()
        return [self._order(row) for row in rows]

    def list_pending_orders(self, account_id: int) -> list[PaperOrder]:
        with self._session_factory() as session:
            rows = session.scalars(select(PaperOrderModel).where(
                PaperOrderModel.account_id == account_id,
                PaperOrderModel.status == PaperOrderStatus.PENDING.value,
            ).order_by(PaperOrderModel.submitted_at)).all()
        return [self._order(row) for row in rows]

    def cancel_order(self, order_id: int, account_id: int, now: datetime) -> bool:
        with self._session_factory() as session:
            result = session.execute(update(PaperOrderModel).where(
                PaperOrderModel.id == order_id,
                PaperOrderModel.account_id == account_id,
                PaperOrderModel.status == PaperOrderStatus.PENDING.value,
            ).values(status=PaperOrderStatus.CANCELLED.value, completed_at=now))
            session.commit()
            return result.rowcount == 1

    def reject_order(
        self, order_id: int, account_id: int, reason: str, now: datetime
    ) -> bool:
        with self._session_factory() as session:
            result = session.execute(update(PaperOrderModel).where(
                PaperOrderModel.id == order_id,
                PaperOrderModel.account_id == account_id,
                PaperOrderModel.status == PaperOrderStatus.PENDING.value,
            ).values(
                status=PaperOrderStatus.REJECTED.value,
                rejection_reason=reason,
                completed_at=now,
            ))
            session.commit()
            return result.rowcount == 1

    def list_positions(self, account_id: int) -> list[PaperPosition]:
        with self._session_factory() as session:
            rows = session.scalars(select(PaperPositionModel).where(
                PaperPositionModel.account_id == account_id,
                PaperPositionModel.quantity != 0,
            ).order_by(PaperPositionModel.symbol)).all()
        return [self._position(row) for row in rows]

    def list_fills(self, account_id: int, limit: int = 100) -> list[PaperFill]:
        with self._session_factory() as session:
            rows = session.scalars(select(PaperFillModel).where(
                PaperFillModel.account_id == account_id
            ).order_by(PaperFillModel.executed_at.desc()).limit(limit)).all()
        return [self._fill(row) for row in rows]

    def execute_fill(
        self, order: PaperOrder, fill: PaperFill, account: PaperAccount,
        position: PaperPosition,
    ) -> None:
        if order.id is None or account.id is None:
            raise ValueError("Order and account must be persisted before execution")
        with self._session_factory() as session:
            order_row = session.scalar(select(PaperOrderModel).where(
                PaperOrderModel.id == order.id,
                PaperOrderModel.account_id == account.id,
            ).with_for_update())
            if order_row is None or order_row.status != PaperOrderStatus.PENDING.value:
                raise ValueError("Order is no longer pending")
            account_row = session.get(PaperAccountModel, account.id, with_for_update=True)
            if account_row is None:
                raise LookupError("Paper account does not exist")
            position_row = session.scalar(select(PaperPositionModel).where(
                PaperPositionModel.account_id == account.id,
                PaperPositionModel.symbol == order.symbol,
            ).with_for_update())
            if position_row is None:
                position_row = PaperPositionModel(
                    account_id=account.id, symbol=order.symbol, quantity=0,
                    average_cost=Decimal("0"), realized_pnl=Decimal("0"),
                    updated_at=fill.executed_at,
                )
                session.add(position_row)
            account_row.cash = account.cash
            account_row.realized_pnl = account.realized_pnl
            account_row.updated_at = account.updated_at
            position_row.quantity = position.quantity
            position_row.average_cost = position.average_cost
            position_row.realized_pnl = position.realized_pnl
            position_row.updated_at = position.updated_at
            order_row.status = PaperOrderStatus.FILLED.value
            order_row.completed_at = fill.executed_at
            session.add(PaperFillModel(
                order_id=order.id, account_id=account.id, symbol=fill.symbol,
                side=fill.side, quantity=fill.quantity, price=fill.price,
                gross_amount=fill.gross_amount, commission=fill.commission,
                transaction_tax=fill.transaction_tax, slippage_bps=fill.slippage_bps,
                bar_event_time=fill.bar_event_time, executed_at=fill.executed_at,
            ))
            session.commit()


class SqlAlchemyFeatureLabelStoreRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def register_definitions(self, definitions: list[FeatureDefinition]) -> None:
        rows = [
            {
                "name": item.name,
                "version": item.version,
                "family": item.family,
                "description": item.description,
                "lookback": item.lookback,
                "parameters_json": item.parameters_json,
            }
            for item in definitions
        ]
        self._upsert_rows(
            FeatureDefinitionModel,
            rows,
            ["name", "version"],
            ["family", "description", "lookback", "parameters_json"],
        )

    def list_definitions(self) -> list[FeatureDefinition]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(FeatureDefinitionModel).order_by(
                    FeatureDefinitionModel.family, FeatureDefinitionModel.name
                )
            ).all()
        return [
            FeatureDefinition(
                name=row.name,
                version=row.version,
                family=row.family,
                description=row.description,
                lookback=row.lookback,
                parameters_json=row.parameters_json,
            )
            for row in rows
        ]

    def upsert_features(self, values: list[FeatureValue]) -> int:
        rows = [
            {
                "symbol": item.symbol,
                "feature_name": item.feature_name,
                "feature_version": item.feature_version,
                "event_time": item.event_time,
                "available_time": item.available_time,
                "computed_at": item.computed_at,
                "value": item.value,
            }
            for item in values
        ]
        self._upsert_rows(
            FeatureValueModel,
            rows,
            ["symbol", "feature_name", "feature_version", "event_time"],
            ["available_time", "computed_at", "value"],
        )
        return len(rows)

    def add_feature_revisions(self, values: list[FeatureRevision]) -> int:
        inserted = 0
        with self._session_factory() as session:
            for item in values:
                existing = session.scalar(select(FeatureRevisionModel.id).where(
                    FeatureRevisionModel.symbol == item.symbol,
                    FeatureRevisionModel.feature_name == item.feature_name,
                    FeatureRevisionModel.feature_version == item.feature_version,
                    FeatureRevisionModel.event_time == item.event_time,
                    FeatureRevisionModel.available_time == item.available_time,
                    FeatureRevisionModel.input_fingerprint == item.input_fingerprint,
                ))
                if existing is not None:
                    continue
                session.add(FeatureRevisionModel(**{
                    key: getattr(item, key)
                    for key in item.__dataclass_fields__ if key != "id"
                }))
                inserted += 1
            session.commit()
        return inserted

    def list_feature_revisions(
        self,
        symbols: list[str] | None = None,
        feature_names: list[str] | None = None,
        as_of: datetime | None = None,
        limit: int | None = None,
    ) -> list[FeatureRevision]:
        statement = select(FeatureRevisionModel)
        if symbols:
            statement = statement.where(FeatureRevisionModel.symbol.in_(symbols))
        if feature_names:
            statement = statement.where(FeatureRevisionModel.feature_name.in_(feature_names))
        if as_of is not None:
            statement = statement.where(FeatureRevisionModel.available_time <= as_of)
        statement = statement.order_by(
            FeatureRevisionModel.event_time.desc(),
            FeatureRevisionModel.feature_name,
            FeatureRevisionModel.available_time.desc(),
            FeatureRevisionModel.computed_at.desc(),
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        latest: dict[tuple[str, str, str, datetime], FeatureRevisionModel] = {}
        for row in rows:
            latest.setdefault(
                (row.symbol, row.feature_name, row.feature_version, row.event_time), row
            )
        selected = list(latest.values())
        if limit is not None:
            selected = selected[:max(0, limit)]
        return [FeatureRevision(**{
            key: getattr(row, key) for key in FeatureRevision.__dataclass_fields__
        }) for row in selected]

    def upsert_labels(self, values: list[LabelValue]) -> int:
        rows = [
            {
                "symbol": item.symbol,
                "label_name": item.label_name,
                "label_version": item.label_version,
                "event_time": item.event_time,
                "available_time": item.available_time,
                "computed_at": item.computed_at,
                "value": item.value,
            }
            for item in values
        ]
        self._upsert_rows(
            LabelValueModel,
            rows,
            ["symbol", "label_name", "label_version", "event_time"],
            ["available_time", "computed_at", "value"],
        )
        return len(rows)

    def feature_coverage(self) -> list[StoreCoverage]:
        return self._coverage(FeatureValueModel, "feature_name", "feature_version")

    def label_coverage(self) -> list[StoreCoverage]:
        return self._coverage(LabelValueModel, "label_name", "label_version")

    def list_features(
        self, symbols: list[str], feature_names: list[str] | None = None
    ) -> list[FeatureValue]:
        if not symbols:
            return []
        statement = select(
            FeatureValueModel.symbol,
            FeatureValueModel.feature_name,
            FeatureValueModel.feature_version,
            FeatureValueModel.event_time,
            FeatureValueModel.available_time,
            FeatureValueModel.computed_at,
            FeatureValueModel.value,
        ).where(FeatureValueModel.symbol.in_(symbols))
        if feature_names:
            statement = statement.where(FeatureValueModel.feature_name.in_(feature_names))
        statement = statement.order_by(FeatureValueModel.event_time, FeatureValueModel.symbol)
        with self._session_factory() as session:
            rows = session.execute(statement).all()
        return [
            FeatureValue(
                symbol=row[0],
                feature_name=row[1],
                feature_version=row[2],
                event_time=row[3],
                available_time=row[4],
                computed_at=row[5],
                value=row[6],
            )
            for row in rows
        ]

    def feature_series_coverage(
        self, feature_names: list[str]
    ) -> dict[tuple[str, str], tuple[int, datetime]]:
        """Row count and latest event time per (symbol, feature) for ``feature_names``."""
        if not feature_names:
            return {}
        statement = (
            select(
                FeatureValueModel.symbol,
                FeatureValueModel.feature_name,
                func.count(),
                func.max(FeatureValueModel.event_time),
            )
            .where(FeatureValueModel.feature_name.in_(feature_names))
            .group_by(FeatureValueModel.symbol, FeatureValueModel.feature_name)
        )
        with self._session_factory() as session:
            rows = session.execute(statement).all()
        return {(row[0], row[1]): (int(row[2]), row[3]) for row in rows}

    def list_recent_features(
        self, symbols: list[str], feature_names: list[str], start: datetime
    ) -> list[FeatureValue]:
        if not symbols:
            return []
        statement = (
            select(FeatureValueModel)
            .where(
                FeatureValueModel.symbol.in_(symbols),
                FeatureValueModel.feature_name.in_(feature_names),
                FeatureValueModel.event_time >= _utc_naive(start),
            )
            .order_by(FeatureValueModel.event_time, FeatureValueModel.symbol)
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [
            FeatureValue(
                symbol=row.symbol, feature_name=row.feature_name,
                feature_version=row.feature_version, event_time=row.event_time,
                available_time=row.available_time, computed_at=row.computed_at,
                value=row.value,
            )
            for row in rows
        ]

    def list_latest_features(
        self, symbols: list[str], feature_names: list[str], as_of: datetime
    ) -> list[FeatureValue]:
        if not symbols:
            return []
        latest_events = (
            select(
                FeatureValueModel.symbol.label("symbol"),
                FeatureValueModel.feature_name.label("feature_name"),
                func.max(FeatureValueModel.event_time).label("event_time"),
            )
            .where(
                FeatureValueModel.symbol.in_(symbols),
                FeatureValueModel.feature_name.in_(feature_names),
                FeatureValueModel.available_time <= _utc_naive(as_of),
            )
            .group_by(FeatureValueModel.symbol, FeatureValueModel.feature_name)
            .subquery()
        )
        statement = select(FeatureValueModel).join(
            latest_events,
            (FeatureValueModel.symbol == latest_events.c.symbol)
            & (FeatureValueModel.feature_name == latest_events.c.feature_name)
            & (FeatureValueModel.event_time == latest_events.c.event_time),
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [
            FeatureValue(
                symbol=row.symbol, feature_name=row.feature_name,
                feature_version=row.feature_version, event_time=row.event_time,
                available_time=row.available_time, computed_at=row.computed_at,
                value=row.value,
            )
            for row in rows
        ]

    def list_labels(
        self, symbols: list[str], label_names: list[str] | None = None
    ) -> list[LabelValue]:
        if not symbols:
            return []
        statement = select(
            LabelValueModel.symbol,
            LabelValueModel.label_name,
            LabelValueModel.label_version,
            LabelValueModel.event_time,
            LabelValueModel.available_time,
            LabelValueModel.computed_at,
            LabelValueModel.value,
        ).where(LabelValueModel.symbol.in_(symbols))
        if label_names:
            statement = statement.where(LabelValueModel.label_name.in_(label_names))
        statement = statement.order_by(LabelValueModel.event_time, LabelValueModel.symbol)
        with self._session_factory() as session:
            rows = session.execute(statement).all()
        return [
            LabelValue(
                symbol=row[0],
                label_name=row[1],
                label_version=row[2],
                event_time=row[3],
                available_time=row[4],
                computed_at=row[5],
                value=row[6],
            )
            for row in rows
        ]

    def list_latest_labels(
        self, symbols: list[str], label_names: list[str]
    ) -> list[LabelValue]:
        if not symbols or not label_names:
            return []
        latest_events = (
            select(
                LabelValueModel.symbol.label("symbol"),
                LabelValueModel.label_name.label("label_name"),
                func.max(LabelValueModel.event_time).label("event_time"),
            )
            .where(
                LabelValueModel.symbol.in_(symbols),
                LabelValueModel.label_name.in_(label_names),
            )
            .group_by(LabelValueModel.symbol, LabelValueModel.label_name)
            .subquery()
        )
        statement = select(LabelValueModel).join(
            latest_events,
            (LabelValueModel.symbol == latest_events.c.symbol)
            & (LabelValueModel.label_name == latest_events.c.label_name)
            & (LabelValueModel.event_time == latest_events.c.event_time),
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [
            LabelValue(
                symbol=row.symbol,
                label_name=row.label_name,
                label_version=row.label_version,
                event_time=row.event_time,
                available_time=row.available_time,
                computed_at=row.computed_at,
                value=row.value,
            )
            for row in rows
        ]

    def _coverage(self, model, name_column: str, version_column: str) -> list[StoreCoverage]:
        name = getattr(model, name_column)
        version = getattr(model, version_column)
        statement = (
            select(
                model.symbol,
                name,
                version,
                func.count(model.id),
                func.min(model.event_time),
                func.max(model.event_time),
                func.max(model.available_time),
            )
            .group_by(model.symbol, name, version)
            .order_by(model.symbol, name)
        )
        with self._session_factory() as session:
            rows = session.execute(statement).all()
        return [
            StoreCoverage(
                symbol=row[0],
                item_name=row[1],
                item_version=row[2],
                row_count=int(row[3]),
                first_event_time=row[4],
                last_event_time=row[5],
                last_available_time=row[6],
            )
            for row in rows
        ]

    def _upsert_rows(
        self,
        model,
        rows: list[dict[str, object]],
        conflict_columns: list[str],
        update_columns: list[str],
    ) -> None:
        if not rows:
            return
        with self._session_factory() as session:
            dialect = session.bind.dialect.name
            for start in range(0, len(rows), 500):
                chunk = rows[start : start + 500]
                if dialect == "sqlite":
                    from sqlalchemy.dialects.sqlite import insert
                elif dialect == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert
                else:
                    for row in chunk:
                        session.merge(model(**row))
                    continue
                statement = insert(model).values(chunk)
                statement = statement.on_conflict_do_update(
                    index_elements=conflict_columns,
                    set_={column: getattr(statement.excluded, column) for column in update_columns},
                )
                session.execute(statement)
            session.commit()


class SqlAlchemyRegimeFactorResearchRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def upsert_regimes(self, values: list[RegimeState]) -> int:
        rows = [
            {
                "symbol": item.symbol,
                "event_time": item.event_time,
                "available_time": item.available_time,
                "regime_version": item.regime_version,
                "trend_regime": item.trend_regime,
                "volatility_regime": item.volatility_regime,
                "composite_regime": item.composite_regime,
                "trend_score": item.trend_score,
                "volatility_score": item.volatility_score,
                "confidence": item.confidence,
                "computed_at": item.computed_at,
            }
            for item in values
        ]
        self._upsert(
            RegimeStateModel,
            rows,
            ["symbol", "event_time", "regime_version"],
            [
                "available_time", "trend_regime", "volatility_regime", "composite_regime",
                "trend_score", "volatility_score", "confidence", "computed_at",
            ],
        )
        return len(rows)

    def list_regimes(self, symbol: str | None = None) -> list[RegimeState]:
        statement = select(RegimeStateModel)
        if symbol:
            statement = statement.where(RegimeStateModel.symbol == symbol.upper())
        statement = statement.order_by(RegimeStateModel.event_time)
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [
            RegimeState(
                symbol=row.symbol,
                event_time=row.event_time,
                available_time=row.available_time,
                regime_version=row.regime_version,
                trend_regime=row.trend_regime,
                volatility_regime=row.volatility_regime,
                composite_regime=row.composite_regime,
                trend_score=row.trend_score,
                volatility_score=row.volatility_score,
                confidence=row.confidence,
                computed_at=row.computed_at,
            )
            for row in rows
        ]

    def upsert_factor_results(self, values: list[FactorResearchResult]) -> int:
        rows = [
            {
                "market": item.market,
                "feature_name": item.feature_name,
                "feature_version": item.feature_version,
                "research_version": item.research_version,
                "cross_sections_5d": item.cross_sections_5d,
                "cross_sections_20d": item.cross_sections_20d,
                "mean_ic_5d": item.mean_ic_5d,
                "rank_ic_5d": item.rank_ic_5d,
                "ic_ir_5d": item.ic_ir_5d,
                "positive_ic_rate_5d": item.positive_ic_rate_5d,
                "mean_ic_20d": item.mean_ic_20d,
                "rank_ic_20d": item.rank_ic_20d,
                "decay_ratio": item.decay_ratio,
                "quantile_spread_5d": item.quantile_spread_5d,
                "annualized_spread_5d": item.annualized_spread_5d,
                "turnover": item.turnover,
                "best_regime": item.best_regime,
                "best_regime_rank_ic": item.best_regime_rank_ic,
                "regime_metrics_json": item.regime_metrics_json,
                "daily_metrics_json": item.daily_metrics_json,
                "computed_at": item.computed_at,
            }
            for item in values
        ]
        self._upsert(
            FactorResearchResultModel,
            rows,
            ["market", "feature_name", "feature_version", "research_version"],
            [column for column in rows[0] if column not in {
                "market", "feature_name", "feature_version", "research_version"
            }] if rows else [],
        )
        return len(rows)

    def list_factor_results(self, market: str | None = None) -> list[FactorResearchResult]:
        statement = select(FactorResearchResultModel)
        if market:
            statement = statement.where(FactorResearchResultModel.market == market.upper())
        statement = statement.order_by(
            FactorResearchResultModel.market,
            FactorResearchResultModel.rank_ic_5d.desc(),
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [
            FactorResearchResult(
                market=row.market,
                feature_name=row.feature_name,
                feature_version=row.feature_version,
                research_version=row.research_version,
                cross_sections_5d=row.cross_sections_5d,
                cross_sections_20d=row.cross_sections_20d,
                mean_ic_5d=row.mean_ic_5d,
                rank_ic_5d=row.rank_ic_5d,
                ic_ir_5d=row.ic_ir_5d,
                positive_ic_rate_5d=row.positive_ic_rate_5d,
                mean_ic_20d=row.mean_ic_20d,
                rank_ic_20d=row.rank_ic_20d,
                decay_ratio=row.decay_ratio,
                quantile_spread_5d=row.quantile_spread_5d,
                annualized_spread_5d=row.annualized_spread_5d,
                turnover=row.turnover,
                best_regime=row.best_regime,
                best_regime_rank_ic=row.best_regime_rank_ic,
                regime_metrics_json=row.regime_metrics_json,
                daily_metrics_json=row.daily_metrics_json,
                computed_at=row.computed_at,
            )
            for row in rows
        ]

    def _upsert(
        self,
        model,
        rows: list[dict[str, object]],
        conflict_columns: list[str],
        update_columns: list[str],
    ) -> None:
        if not rows:
            return
        with self._session_factory() as session:
            dialect = session.bind.dialect.name
            for start in range(0, len(rows), 500):
                chunk = rows[start : start + 500]
                if dialect == "sqlite":
                    from sqlalchemy.dialects.sqlite import insert
                elif dialect == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert
                else:
                    for row in chunk:
                        session.add(model(**row))
                    continue
                statement = insert(model).values(chunk)
                statement = statement.on_conflict_do_update(
                    index_elements=conflict_columns,
                    set_={column: getattr(statement.excluded, column) for column in update_columns},
                )
                session.execute(statement)
            session.commit()


class SqlAlchemyBacktestResearchRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def save(self, artifact: BacktestArtifact) -> int:
        run = artifact.run
        values = {
            field: getattr(run, field)
            for field in run.__dataclass_fields__
            if field != "id"
        }
        with self._session_factory() as session:
            statement = select(BacktestRunModel).where(
                BacktestRunModel.symbol == run.symbol,
                BacktestRunModel.strategy_name == run.strategy_name,
                BacktestRunModel.strategy_version == run.strategy_version,
                BacktestRunModel.research_version == run.research_version,
                BacktestRunModel.data_end == run.data_end,
            )
            row = session.scalar(statement)
            if row is None:
                row = BacktestRunModel(**values)
                session.add(row)
                session.flush()
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            run_id = int(row.id)
            session.execute(delete(BacktestFoldModel).where(BacktestFoldModel.run_id == run_id))
            session.execute(delete(BacktestTradeModel).where(BacktestTradeModel.run_id == run_id))
            session.execute(
                delete(BacktestEquityPointModel).where(BacktestEquityPointModel.run_id == run_id)
            )
            session.add_all(
                [BacktestFoldModel(run_id=run_id, **asdict(item)) for item in artifact.folds]
            )
            session.add_all(
                [BacktestTradeModel(run_id=run_id, **asdict(item)) for item in artifact.trades]
            )
            session.add_all(
                [BacktestEquityPointModel(run_id=run_id, **asdict(item)) for item in artifact.equity]
            )
            session.commit()
        return run_id

    def list_runs(self, market: str | None = None) -> list[BacktestRun]:
        statement = select(BacktestRunModel)
        if market:
            statement = statement.where(BacktestRunModel.market == market.upper())
        statement = statement.order_by(
            BacktestRunModel.promotion_gate,
            BacktestRunModel.sharpe.desc(),
            BacktestRunModel.symbol,
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._run_entity(row) for row in rows]

    def get(self, run_id: int) -> BacktestArtifact | None:
        with self._session_factory() as session:
            row = session.get(BacktestRunModel, run_id)
            if row is None:
                return None
            folds = session.scalars(
                select(BacktestFoldModel)
                .where(BacktestFoldModel.run_id == run_id)
                .order_by(BacktestFoldModel.sequence)
            ).all()
            trades = session.scalars(
                select(BacktestTradeModel)
                .where(BacktestTradeModel.run_id == run_id)
                .order_by(BacktestTradeModel.entry_time)
            ).all()
            equity = session.scalars(
                select(BacktestEquityPointModel)
                .where(BacktestEquityPointModel.run_id == run_id)
                .order_by(BacktestEquityPointModel.event_time)
            ).all()
            run_entity = self._run_entity(row)
        return BacktestArtifact(
            run=run_entity,
            folds=[
                BacktestFold(
                    sequence=item.sequence,
                    train_start=item.train_start,
                    train_end=item.train_end,
                    test_start=item.test_start,
                    test_end=item.test_end,
                    train_observations=item.train_observations,
                    test_observations=item.test_observations,
                    selected_parameters_json=item.selected_parameters_json,
                    train_sharpe=item.train_sharpe,
                    test_return=item.test_return,
                    benchmark_return=item.benchmark_return,
                    test_sharpe=item.test_sharpe,
                    max_drawdown=item.max_drawdown,
                    trade_count=item.trade_count,
                )
                for item in folds
            ],
            trades=[
                BacktestTrade(
                    entry_time=item.entry_time,
                    exit_time=item.exit_time,
                    entry_price=item.entry_price,
                    exit_price=item.exit_price,
                    size=item.size,
                    net_return=item.net_return,
                    holding_sessions=item.holding_sessions,
                    exit_reason=item.exit_reason,
                    costs=item.costs,
                )
                for item in trades
            ],
            equity=[
                BacktestEquityPoint(
                    event_time=item.event_time,
                    equity=item.equity,
                    benchmark_equity=item.benchmark_equity,
                    drawdown=item.drawdown,
                    daily_return=item.daily_return,
                    position=item.position,
                )
                for item in equity
            ],
        )

    @staticmethod
    def _run_entity(row: BacktestRunModel) -> BacktestRun:
        return BacktestRun(
            id=row.id,
            symbol=row.symbol,
            market=row.market,
            strategy_name=row.strategy_name,
            strategy_version=row.strategy_version,
            research_version=row.research_version,
            promotion_gate=row.promotion_gate,
            data_start=row.data_start,
            data_end=row.data_end,
            fold_count=row.fold_count,
            observation_count=row.observation_count,
            total_return=row.total_return,
            annual_return=row.annual_return,
            benchmark_return=row.benchmark_return,
            excess_return=row.excess_return,
            sharpe=row.sharpe,
            sortino=row.sortino,
            calmar=row.calmar,
            max_drawdown=row.max_drawdown,
            win_rate=row.win_rate,
            profit_factor=row.profit_factor,
            turnover=row.turnover,
            exposure=row.exposure,
            alpha=row.alpha,
            beta=row.beta,
            information_ratio=row.information_ratio,
            trade_count=row.trade_count,
            positive_fold_rate=row.positive_fold_rate,
            commission_bps=row.commission_bps,
            slippage_bps=row.slippage_bps,
            parameters_json=row.parameters_json,
            limitations_json=row.limitations_json,
            computed_at=row.computed_at,
        )


class SqlAlchemyEnsembleResearchRepository:
    """Persists a complete ensemble snapshot and replaces its child series atomically."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def save(self, artifact: EnsembleArtifact) -> int:
        run = artifact.run
        values = {
            field: getattr(run, field)
            for field in run.__dataclass_fields__
            if field != "id"
        }
        with self._session_factory() as session:
            row = session.scalar(
                select(EnsembleRunModel).where(
                    EnsembleRunModel.symbol == run.symbol,
                    EnsembleRunModel.ensemble_version == run.ensemble_version,
                    EnsembleRunModel.data_end == run.data_end,
                )
            )
            if row is None:
                row = EnsembleRunModel(**values)
                session.add(row)
                session.flush()
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            run_id = int(row.id)
            session.execute(
                delete(EnsembleWeightPointModel).where(EnsembleWeightPointModel.run_id == run_id)
            )
            session.execute(
                delete(EnsembleEquityPointModel).where(EnsembleEquityPointModel.run_id == run_id)
            )
            session.add_all(
                [EnsembleWeightPointModel(run_id=run_id, **asdict(item)) for item in artifact.weights]
            )
            session.add_all(
                [EnsembleEquityPointModel(run_id=run_id, **asdict(item)) for item in artifact.equity]
            )
            session.commit()
        return run_id

    def list_runs(self, market: str | None = None) -> list[EnsembleRun]:
        statement = select(EnsembleRunModel)
        if market:
            statement = statement.where(EnsembleRunModel.market == market.upper())
        statement = statement.order_by(
            EnsembleRunModel.promotion_gate,
            EnsembleRunModel.sharpe.desc(),
            EnsembleRunModel.symbol,
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._run_entity(row) for row in rows]

    def get(self, run_id: int) -> EnsembleArtifact | None:
        with self._session_factory() as session:
            row = session.get(EnsembleRunModel, run_id)
            if row is None:
                return None
            weight_rows = session.scalars(
                select(EnsembleWeightPointModel)
                .where(EnsembleWeightPointModel.run_id == run_id)
                .order_by(EnsembleWeightPointModel.event_time)
            ).all()
            equity_rows = session.scalars(
                select(EnsembleEquityPointModel)
                .where(EnsembleEquityPointModel.run_id == run_id)
                .order_by(EnsembleEquityPointModel.event_time)
            ).all()
            run_entity = self._run_entity(row)
        return EnsembleArtifact(
            run=run_entity,
            weights=[
                EnsembleWeightPoint(
                    event_time=item.event_time,
                    regime=item.regime,
                    weights_json=item.weights_json,
                    scores_json=item.scores_json,
                    contributions_json=item.contributions_json,
                    turnover=item.turnover,
                    cost=item.cost,
                    daily_return=item.daily_return,
                )
                for item in weight_rows
            ],
            equity=[
                EnsembleEquityPoint(
                    event_time=item.event_time,
                    equity=item.equity,
                    equal_weight_equity=item.equal_weight_equity,
                    drawdown=item.drawdown,
                )
                for item in equity_rows
            ],
        )

    @staticmethod
    def _run_entity(row: EnsembleRunModel) -> EnsembleRun:
        return EnsembleRun(
            id=row.id,
            symbol=row.symbol,
            market=row.market,
            ensemble_version=row.ensemble_version,
            promotion_gate=row.promotion_gate,
            data_start=row.data_start,
            data_end=row.data_end,
            observation_count=row.observation_count,
            component_count=row.component_count,
            total_return=row.total_return,
            annual_return=row.annual_return,
            equal_weight_return=row.equal_weight_return,
            excess_to_equal=row.excess_to_equal,
            sharpe=row.sharpe,
            equal_weight_sharpe=row.equal_weight_sharpe,
            max_drawdown=row.max_drawdown,
            turnover=row.turnover,
            allocation_cost_bps=row.allocation_cost_bps,
            average_weights_json=row.average_weights_json,
            latest_weights_json=row.latest_weights_json,
            regime_metrics_json=row.regime_metrics_json,
            limitations_json=row.limitations_json,
            computed_at=row.computed_at,
        )


class SqlAlchemyPortfolioResearchRepository:
    """Stores portfolio results and their rebalance/equity audit trails transactionally."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def save(self, artifact: PortfolioArtifact) -> int:
        run = artifact.run
        values = {
            field: getattr(run, field)
            for field in run.__dataclass_fields__
            if field != "id"
        }
        with self._session_factory() as session:
            row = session.scalar(
                select(PortfolioRunModel).where(
                    PortfolioRunModel.market == run.market,
                    PortfolioRunModel.method == run.method,
                    PortfolioRunModel.portfolio_version == run.portfolio_version,
                    PortfolioRunModel.data_end == run.data_end,
                )
            )
            if row is None:
                row = PortfolioRunModel(**values)
                session.add(row)
                session.flush()
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            run_id = int(row.id)
            session.execute(
                delete(PortfolioAllocationPointModel).where(
                    PortfolioAllocationPointModel.run_id == run_id
                )
            )
            session.execute(
                delete(PortfolioEquityPointModel).where(
                    PortfolioEquityPointModel.run_id == run_id
                )
            )
            session.add_all(
                [
                    PortfolioAllocationPointModel(run_id=run_id, **asdict(item))
                    for item in artifact.allocations
                ]
            )
            session.add_all(
                [PortfolioEquityPointModel(run_id=run_id, **asdict(item)) for item in artifact.equity]
            )
            session.commit()
        return run_id

    def list_runs(self, market: str | None = None) -> list[PortfolioRun]:
        statement = select(PortfolioRunModel)
        if market:
            statement = statement.where(PortfolioRunModel.market == market.upper())
        statement = statement.order_by(
            PortfolioRunModel.promotion_gate,
            PortfolioRunModel.sharpe.desc(),
            PortfolioRunModel.market,
            PortfolioRunModel.method,
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._run_entity(row) for row in rows]

    def get(self, run_id: int) -> PortfolioArtifact | None:
        with self._session_factory() as session:
            row = session.get(PortfolioRunModel, run_id)
            if row is None:
                return None
            allocation_rows = session.scalars(
                select(PortfolioAllocationPointModel)
                .where(PortfolioAllocationPointModel.run_id == run_id)
                .order_by(PortfolioAllocationPointModel.event_time)
            ).all()
            equity_rows = session.scalars(
                select(PortfolioEquityPointModel)
                .where(PortfolioEquityPointModel.run_id == run_id)
                .order_by(PortfolioEquityPointModel.event_time)
            ).all()
            run_entity = self._run_entity(row)
        return PortfolioArtifact(
            run=run_entity,
            allocations=[
                PortfolioAllocationPoint(
                    event_time=item.event_time,
                    weights_json=item.weights_json,
                    cash_weight=item.cash_weight,
                    turnover=item.turnover,
                    cost=item.cost,
                    constraint_flags_json=item.constraint_flags_json,
                )
                for item in allocation_rows
            ],
            equity=[
                PortfolioEquityPoint(
                    event_time=item.event_time,
                    equity=item.equity,
                    equal_weight_equity=item.equal_weight_equity,
                    benchmark_equity=item.benchmark_equity,
                    drawdown=item.drawdown,
                    daily_return=item.daily_return,
                )
                for item in equity_rows
            ],
        )

    @staticmethod
    def _run_entity(row: PortfolioRunModel) -> PortfolioRun:
        return PortfolioRun(
            id=row.id,
            market=row.market,
            method=row.method,
            portfolio_version=row.portfolio_version,
            promotion_gate=row.promotion_gate,
            data_start=row.data_start,
            data_end=row.data_end,
            observation_count=row.observation_count,
            asset_count=row.asset_count,
            total_return=row.total_return,
            annual_return=row.annual_return,
            equal_weight_return=row.equal_weight_return,
            benchmark_return=row.benchmark_return,
            excess_to_equal=row.excess_to_equal,
            excess_to_benchmark=row.excess_to_benchmark,
            annual_volatility=row.annual_volatility,
            sharpe=row.sharpe,
            sortino=row.sortino,
            max_drawdown=row.max_drawdown,
            var_95=row.var_95,
            cvar_95=row.cvar_95,
            beta=row.beta,
            turnover=row.turnover,
            transaction_cost_bps=row.transaction_cost_bps,
            effective_asset_count=row.effective_asset_count,
            diversification_ratio=row.diversification_ratio,
            average_correlation=row.average_correlation,
            liquidity_risk=row.liquidity_risk,
            latest_weights_json=row.latest_weights_json,
            sector_exposure_json=row.sector_exposure_json,
            factor_exposure_json=row.factor_exposure_json,
            stress_tests_json=row.stress_tests_json,
            limitations_json=row.limitations_json,
            computed_at=row.computed_at,
        )


class SqlAlchemyModelGovernanceRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @classmethod
    def _entry_entity(cls, row: ModelRegistryEntryModel) -> ModelRegistryEntry:
        return ModelRegistryEntry(
            id=row.id, experiment_id=row.experiment_id, market=row.market,
            model_name=row.model_name, label_name=row.label_name,
            status=ModelRegistryStatus(row.status), quality_score=row.quality_score,
            eligibility_checks_json=row.eligibility_checks_json,
            registered_at=cls._aware(row.registered_at),
            promoted_at=cls._aware(row.promoted_at), demoted_at=cls._aware(row.demoted_at),
            reviewer=row.reviewer, decision_note=row.decision_note,
            updated_at=cls._aware(row.updated_at),
        )

    @classmethod
    def _drift_entity(cls, row: ModelDriftSnapshotModel) -> ModelDriftSnapshot:
        return ModelDriftSnapshot(
            id=row.id, registry_entry_id=row.registry_entry_id,
            policy_version=row.policy_version, snapshot_time=cls._aware(row.snapshot_time),
            baseline_end=cls._aware(row.baseline_end),
            recent_start=cls._aware(row.recent_start), recent_end=cls._aware(row.recent_end),
            feature_count=row.feature_count, observation_count=row.observation_count,
            feature_psi_median=row.feature_psi_median, feature_psi_max=row.feature_psi_max,
            prediction_psi=row.prediction_psi,
            directional_accuracy_change=row.directional_accuracy_change,
            rank_ic_change=row.rank_ic_change,
            quality_score_change=row.quality_score_change,
            status=DriftStatus(row.status), feature_details_json=row.feature_details_json,
            checks_json=row.checks_json, computed_at=cls._aware(row.computed_at),
        )

    def save_entry(self, entry: ModelRegistryEntry) -> int:
        values = {
            key: (getattr(entry, key).value if key == "status" else getattr(entry, key))
            for key in entry.__dataclass_fields__ if key != "id"
        }
        with self._session_factory() as session:
            row = session.scalar(select(ModelRegistryEntryModel).where(
                ModelRegistryEntryModel.experiment_id == entry.experiment_id
            ))
            if row is None:
                row = ModelRegistryEntryModel(**values)
                session.add(row)
                session.flush()
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            entry_id = int(row.id)
            session.commit()
        return entry_id

    def update_entry(self, entry: ModelRegistryEntry) -> None:
        if entry.id is None:
            raise ValueError("Cannot update an unsaved model registry entry")
        with self._session_factory() as session:
            row = session.get(ModelRegistryEntryModel, entry.id)
            if row is None:
                raise LookupError(f"Model registry entry {entry.id} does not exist")
            for key in entry.__dataclass_fields__:
                if key == "id":
                    continue
                value = getattr(entry, key)
                setattr(row, key, value.value if key == "status" else value)
            session.commit()

    def get_entry(self, entry_id: int) -> ModelRegistryEntry | None:
        with self._session_factory() as session:
            row = session.get(ModelRegistryEntryModel, entry_id)
        return self._entry_entity(row) if row else None

    def get_by_experiment(self, experiment_id: int) -> ModelRegistryEntry | None:
        with self._session_factory() as session:
            row = session.scalar(select(ModelRegistryEntryModel).where(
                ModelRegistryEntryModel.experiment_id == experiment_id
            ))
        return self._entry_entity(row) if row else None

    def list_entries(self, market: str | None = None) -> list[ModelRegistryEntry]:
        statement = select(ModelRegistryEntryModel)
        if market:
            statement = statement.where(ModelRegistryEntryModel.market == market.upper())
        statement = statement.order_by(
            ModelRegistryEntryModel.market, ModelRegistryEntryModel.label_name,
            ModelRegistryEntryModel.status, ModelRegistryEntryModel.quality_score.desc(),
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._entry_entity(row) for row in rows]

    def save_drift(self, snapshot: ModelDriftSnapshot) -> int:
        values = {
            key: (getattr(snapshot, key).value if key == "status" else getattr(snapshot, key))
            for key in snapshot.__dataclass_fields__ if key != "id"
        }
        with self._session_factory() as session:
            row = session.scalar(select(ModelDriftSnapshotModel).where(
                ModelDriftSnapshotModel.registry_entry_id == snapshot.registry_entry_id,
                ModelDriftSnapshotModel.policy_version == snapshot.policy_version,
                ModelDriftSnapshotModel.recent_end == snapshot.recent_end,
            ))
            if row is None:
                row = ModelDriftSnapshotModel(**values)
                session.add(row)
                session.flush()
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            snapshot_id = int(row.id)
            session.commit()
        return snapshot_id

    def latest_drift(self, entry_id: int) -> ModelDriftSnapshot | None:
        statement = (
            select(ModelDriftSnapshotModel)
            .where(ModelDriftSnapshotModel.registry_entry_id == entry_id)
            .order_by(ModelDriftSnapshotModel.recent_end.desc(), ModelDriftSnapshotModel.id.desc())
            .limit(1)
        )
        with self._session_factory() as session:
            row = session.scalar(statement)
        return self._drift_entity(row) if row else None

    def list_drift(
        self, entry_id: int | None = None, limit: int = 500
    ) -> list[ModelDriftSnapshot]:
        statement = select(ModelDriftSnapshotModel)
        if entry_id is not None:
            statement = statement.where(ModelDriftSnapshotModel.registry_entry_id == entry_id)
        statement = statement.order_by(
            ModelDriftSnapshotModel.recent_end.desc(), ModelDriftSnapshotModel.id.desc()
        ).limit(limit)
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._drift_entity(row) for row in rows]


class SqlAlchemyDataQualityRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @classmethod
    def _entity(cls, row: DataQualitySnapshotModel) -> DataQualitySnapshot:
        return DataQualitySnapshot(
            id=row.id, market=row.market, stage=row.stage,
            policy_version=row.policy_version, data_fingerprint=row.data_fingerprint,
            status=DataQualityStatus(row.status), data_as_of=cls._aware(row.data_as_of),
            active_asset_count=row.active_asset_count,
            covered_asset_count=row.covered_asset_count,
            bar_coverage_rate=row.bar_coverage_rate,
            feature_coverage_rate=row.feature_coverage_rate,
            stale_asset_count=row.stale_asset_count,
            temporal_violation_count=row.temporal_violation_count,
            invalid_value_count=row.invalid_value_count,
            missing_session_count=row.missing_session_count,
            issue_count=row.issue_count, blocking_issue_count=row.blocking_issue_count,
            research_allowed=row.research_allowed, issues_json=row.issues_json,
            checks_json=row.checks_json, computed_at=cls._aware(row.computed_at),
        )

    def save(self, snapshot: DataQualitySnapshot) -> int:
        values = {
            key: (getattr(snapshot, key).value if key == "status" else getattr(snapshot, key))
            for key in snapshot.__dataclass_fields__ if key != "id"
        }
        with self._session_factory() as session:
            row = session.scalar(select(DataQualitySnapshotModel).where(
                DataQualitySnapshotModel.market == snapshot.market,
                DataQualitySnapshotModel.stage == snapshot.stage,
                DataQualitySnapshotModel.policy_version == snapshot.policy_version,
                DataQualitySnapshotModel.data_fingerprint == snapshot.data_fingerprint,
            ))
            if row is None:
                row = DataQualitySnapshotModel(**values)
                session.add(row)
                session.flush()
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            snapshot_id = int(row.id)
            session.commit()
        return snapshot_id

    def latest(self, market: str, stage: str = "full") -> DataQualitySnapshot | None:
        statement = (
            select(DataQualitySnapshotModel)
            .where(
                DataQualitySnapshotModel.market == market.upper(),
                DataQualitySnapshotModel.stage == stage,
            )
            .order_by(DataQualitySnapshotModel.computed_at.desc(), DataQualitySnapshotModel.id.desc())
            .limit(1)
        )
        with self._session_factory() as session:
            row = session.scalar(statement)
        return self._entity(row) if row else None

    def list_snapshots(
        self, market: str | None = None, stage: str | None = None, limit: int = 100
    ) -> list[DataQualitySnapshot]:
        statement = select(DataQualitySnapshotModel)
        if market:
            statement = statement.where(DataQualitySnapshotModel.market == market.upper())
        if stage:
            statement = statement.where(DataQualitySnapshotModel.stage == stage)
        statement = statement.order_by(
            DataQualitySnapshotModel.computed_at.desc(), DataQualitySnapshotModel.id.desc()
        ).limit(limit)
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._entity(row) for row in rows]


class SqlAlchemyModelResearchRepository:
    """Idempotent registry for model experiments and their audit payloads."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def save(self, experiment: ModelExperiment) -> int:
        values = {
            field: getattr(experiment, field)
            for field in experiment.__dataclass_fields__
            if field != "id"
        }
        with self._session_factory() as session:
            row = session.scalar(
                select(ModelExperimentModel).where(
                    ModelExperimentModel.market == experiment.market,
                    ModelExperimentModel.model_name == experiment.model_name,
                    ModelExperimentModel.model_version == experiment.model_version,
                    ModelExperimentModel.label_name == experiment.label_name,
                    ModelExperimentModel.experiment_version == experiment.experiment_version,
                    ModelExperimentModel.data_end == experiment.data_end,
                )
            )
            if row is None:
                row = ModelExperimentModel(**values)
                session.add(row)
                session.flush()
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            run_id = int(row.id)
            session.commit()
        return run_id

    def list_runs(self, market: str | None = None) -> list[ModelExperiment]:
        statement = select(ModelExperimentModel)
        if market:
            statement = statement.where(ModelExperimentModel.market == market.upper())
        statement = statement.order_by(
            ModelExperimentModel.promotion_gate,
            ModelExperimentModel.rank_ic.desc(),
            ModelExperimentModel.r2.desc(),
            ModelExperimentModel.model_name,
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._entity(row) for row in rows]

    def get(self, run_id: int) -> ModelExperiment | None:
        with self._session_factory() as session:
            row = session.get(ModelExperimentModel, run_id)
            return self._entity(row) if row is not None else None

    def save_predictions(self, values: list[ModelPrediction]) -> int:
        if not values:
            return 0
        payloads = [
            {
                field: getattr(value, field)
                for field in value.__dataclass_fields__
                if field != "id"
            }
            for value in values
        ]
        with self._session_factory() as session:
            dialect = session.get_bind().dialect.name
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert

                statement = insert(ModelPredictionModel).values(payloads)
                statement = statement.on_conflict_do_update(
                    index_elements=["experiment_id", "symbol", "event_time"],
                    set_={
                        "market": statement.excluded.market,
                        "model_name": statement.excluded.model_name,
                        "label_name": statement.excluded.label_name,
                        "horizon": statement.excluded.horizon,
                        "available_time": statement.excluded.available_time,
                        "predicted_value": statement.excluded.predicted_value,
                        "rank_score": statement.excluded.rank_score,
                        "computed_at": statement.excluded.computed_at,
                    },
                )
                session.execute(statement)
            elif dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert

                statement = insert(ModelPredictionModel).values(payloads)
                statement = statement.on_conflict_do_update(
                    constraint="uq_model_prediction_snapshot",
                    set_={
                        "market": statement.excluded.market,
                        "model_name": statement.excluded.model_name,
                        "label_name": statement.excluded.label_name,
                        "horizon": statement.excluded.horizon,
                        "available_time": statement.excluded.available_time,
                        "predicted_value": statement.excluded.predicted_value,
                        "rank_score": statement.excluded.rank_score,
                        "computed_at": statement.excluded.computed_at,
                    },
                )
                session.execute(statement)
            else:
                for payload in payloads:
                    row = session.scalar(select(ModelPredictionModel).where(
                        ModelPredictionModel.experiment_id == payload["experiment_id"],
                        ModelPredictionModel.symbol == payload["symbol"],
                        ModelPredictionModel.event_time == payload["event_time"],
                    ))
                    if row is None:
                        session.add(ModelPredictionModel(**payload))
                    else:
                        for key, item in payload.items():
                            setattr(row, key, item)
            session.commit()
        return len(values)

    def list_predictions(
        self, market: str | None = None, symbol: str | None = None
    ) -> list[ModelPrediction]:
        statement = select(ModelPredictionModel)
        if market:
            statement = statement.where(ModelPredictionModel.market == market.upper())
        if symbol:
            statement = statement.where(ModelPredictionModel.symbol == symbol.upper())
        statement = statement.order_by(
            ModelPredictionModel.event_time.desc(),
            ModelPredictionModel.symbol,
            ModelPredictionModel.model_name,
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [
            ModelPrediction(
                id=row.id,
                experiment_id=row.experiment_id,
                market=row.market,
                symbol=row.symbol,
                model_name=row.model_name,
                label_name=row.label_name,
                horizon=row.horizon,
                event_time=row.event_time,
                available_time=row.available_time,
                predicted_value=row.predicted_value,
                rank_score=row.rank_score,
                computed_at=row.computed_at,
            )
            for row in rows
        ]

    def list_predictions_for_experiments(
        self, experiment_ids: list[int]
    ) -> list[ModelPrediction]:
        if not experiment_ids:
            return []
        statement = (
            select(ModelPredictionModel)
            .where(ModelPredictionModel.experiment_id.in_(experiment_ids))
            .order_by(
                ModelPredictionModel.event_time,
                ModelPredictionModel.symbol,
                ModelPredictionModel.model_name,
            )
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [
            ModelPrediction(
                id=row.id,
                experiment_id=row.experiment_id,
                market=row.market,
                symbol=row.symbol,
                model_name=row.model_name,
                label_name=row.label_name,
                horizon=row.horizon,
                event_time=row.event_time,
                available_time=row.available_time,
                predicted_value=row.predicted_value,
                rank_score=row.rank_score,
                computed_at=row.computed_at,
            )
            for row in rows
        ]

    def prediction_coverage(self, experiment_id: int) -> tuple[int, int]:
        statement = select(
            func.count(ModelPredictionModel.id),
            func.count(func.distinct(func.date(ModelPredictionModel.event_time))),
        ).where(ModelPredictionModel.experiment_id == experiment_id)
        with self._session_factory() as session:
            row_count, date_count = session.execute(statement).one()
        return int(row_count or 0), int(date_count or 0)

    PREDICTION_ARCHIVE_COLUMNS = (
        "id", "experiment_id", "market", "symbol", "model_name", "label_name", "horizon",
        "event_time", "available_time", "predicted_value", "rank_score", "computed_at",
    )

    def prediction_experiment_ids(self) -> list[int]:
        """Experiments that still hold prediction rows in the database."""
        statement = select(ModelPredictionModel.experiment_id).distinct()
        with self._session_factory() as session:
            return sorted(int(value) for value in session.scalars(statement).all())

    def iter_prediction_batches(
        self, experiment_id: int, batch_size: int = 100_000
    ) -> Iterator[list[tuple]]:
        """Stream one experiment's rows as tuples in ``PREDICTION_ARCHIVE_COLUMNS`` order."""
        table = ModelPredictionModel.__table__
        statement = select(
            *(table.c[name] for name in self.PREDICTION_ARCHIVE_COLUMNS)
        ).where(table.c.experiment_id == experiment_id)
        with self._session_factory() as session:
            result = session.execute(
                statement.execution_options(stream_results=True, yield_per=batch_size)
            )
            for partition in result.partitions(batch_size):
                yield [tuple(row) for row in partition]

    def delete_predictions_for_experiment(self, experiment_id: int) -> int:
        with self._session_factory() as session:
            result = session.execute(
                delete(ModelPredictionModel).where(
                    ModelPredictionModel.experiment_id == experiment_id
                )
            )
            session.commit()
            return int(result.rowcount or 0)

    def save_explanations(self, values: list[ModelExplanation]) -> int:
        if not values:
            return 0
        with self._session_factory() as session:
            for value in values:
                row = session.scalar(
                    select(ModelExplanationModel).where(
                        ModelExplanationModel.experiment_id == value.experiment_id,
                        ModelExplanationModel.symbol == value.symbol,
                        ModelExplanationModel.event_time == value.event_time,
                        ModelExplanationModel.explanation_version == value.explanation_version,
                    )
                )
                payload = {
                    field: getattr(value, field)
                    for field in value.__dataclass_fields__
                    if field != "id"
                }
                if row is None:
                    session.add(ModelExplanationModel(**payload))
                else:
                    for key, item in payload.items():
                        setattr(row, key, item)
            session.commit()
        return len(values)

    def list_explanations(
        self, market: str | None = None, symbol: str | None = None
    ) -> list[ModelExplanation]:
        statement = select(ModelExplanationModel)
        if market:
            statement = statement.where(ModelExplanationModel.market == market.upper())
        if symbol:
            statement = statement.where(ModelExplanationModel.symbol == symbol.upper())
        statement = statement.order_by(
            ModelExplanationModel.event_time.desc(),
            ModelExplanationModel.symbol,
            ModelExplanationModel.model_name,
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [
            ModelExplanation(
                id=row.id,
                experiment_id=row.experiment_id,
                market=row.market,
                symbol=row.symbol,
                model_name=row.model_name,
                event_time=row.event_time,
                data_available_time=row.data_available_time,
                predicted_value=row.predicted_value,
                baseline_value=row.baseline_value,
                contributions_json=row.contributions_json,
                explanation_method=row.explanation_method,
                explanation_version=row.explanation_version,
                computed_at=row.computed_at,
            )
            for row in rows
        ]

    @staticmethod
    def _entity(row: ModelExperimentModel) -> ModelExperiment:
        return ModelExperiment(
            id=row.id,
            market=row.market,
            model_name=row.model_name,
            model_version=row.model_version,
            label_name=row.label_name,
            experiment_version=row.experiment_version,
            promotion_gate=row.promotion_gate,
            data_start=row.data_start,
            data_end=row.data_end,
            observation_count=row.observation_count,
            fold_count=row.fold_count,
            feature_count=row.feature_count,
            rmse=row.rmse,
            mae=row.mae,
            r2=row.r2,
            directional_accuracy=row.directional_accuracy,
            rank_ic=row.rank_ic,
            long_short_spread=row.long_short_spread,
            feature_names_json=row.feature_names_json,
            parameters_json=row.parameters_json,
            fold_metrics_json=row.fold_metrics_json,
            feature_importance_json=row.feature_importance_json,
            limitations_json=row.limitations_json,
            computed_at=row.computed_at,
        )


class SqlAlchemyDailyDecisionRepository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    def upsert(self, values: list[DailyDecision]) -> int:
        if not values:
            return 0
        with self._session_factory() as session:
            for value in values:
                row = session.scalar(
                    select(DailyDecisionModel).where(
                        DailyDecisionModel.market == value.market,
                        DailyDecisionModel.symbol == value.symbol,
                        DailyDecisionModel.decision_version == value.decision_version,
                        DailyDecisionModel.event_time == value.event_time,
                    )
                )
                payload = {
                    field: getattr(value, field)
                    for field in value.__dataclass_fields__
                    if field != "id"
                }
                if row is None:
                    session.add(DailyDecisionModel(**payload))
                else:
                    for key, item in payload.items():
                        setattr(row, key, item)
            session.commit()
        return len(values)

    def list_latest(self, market: str | None = None) -> list[DailyDecision]:
        statement = select(DailyDecisionModel)
        if market:
            statement = statement.where(DailyDecisionModel.market == market.upper())
        statement = statement.order_by(
            DailyDecisionModel.computed_at.desc(),
            DailyDecisionModel.event_time.desc(),
            DailyDecisionModel.score.desc(),
        )
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        latest: dict[str, DailyDecisionModel] = {}
        for row in rows:
            latest.setdefault(row.symbol, row)
        return [self._entity(row) for row in sorted(latest.values(), key=lambda item: item.score, reverse=True)]

    def list_history(
        self, market: str | None = None, limit: int | None = None
    ) -> list[DailyDecision]:
        statement = select(DailyDecisionModel)
        if market:
            statement = statement.where(DailyDecisionModel.market == market.upper())
        statement = statement.order_by(
            DailyDecisionModel.event_time.asc(),
            DailyDecisionModel.computed_at.asc(),
            DailyDecisionModel.score.desc(),
        )
        if limit is not None:
            statement = statement.limit(limit)
        with self._session_factory() as session:
            rows = session.scalars(statement).all()
        return [self._entity(row) for row in rows]

    def get_latest(self, symbol: str) -> DailyDecision | None:
        with self._session_factory() as session:
            row = session.scalar(
                select(DailyDecisionModel)
                .where(DailyDecisionModel.symbol == symbol.upper())
                .order_by(DailyDecisionModel.computed_at.desc(), DailyDecisionModel.event_time.desc())
            )
        return self._entity(row) if row is not None else None

    @staticmethod
    def _entity(row: DailyDecisionModel) -> DailyDecision:
        return DailyDecision(
            id=row.id,
            market=row.market,
            symbol=row.symbol,
            decision_version=row.decision_version,
            event_time=row.event_time,
            status=row.status,
            score=row.score,
            predicted_return_5d=row.predicted_return_5d,
            prediction_dispersion=row.prediction_dispersion,
            model_rank=row.model_rank,
            regime=row.regime,
            factor_score=row.factor_score,
            strategy_score=row.strategy_score,
            suggested_weight=row.suggested_weight,
            reasons_json=row.reasons_json,
            risks_json=row.risks_json,
            gate_checks_json=row.gate_checks_json,
            computed_at=row.computed_at,
        )

class SqlAlchemyInvestmentPlanRepository:
    """Append-only plan versions (S5-W01)."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _entity(row: InvestmentPlanModel) -> InvestmentPlan:
        return InvestmentPlan(
            version=row.version, created_at=_utc_aware(row.created_at),
            monthly_amount=Decimal(row.monthly_amount), salary_day=row.salary_day,
            strategy_key=row.strategy_key, max_drawdown_tolerance=row.max_drawdown_tolerance,
            goal=row.goal or "", horizon_years=row.horizon_years, note=row.note or "",
        )

    def latest(self) -> InvestmentPlan | None:
        with self._session_factory() as session:
            row = session.scalars(
                select(InvestmentPlanModel).order_by(InvestmentPlanModel.version.desc()).limit(1)
            ).first()
        return self._entity(row) if row else None

    def list_versions(self, limit: int = 20) -> list[InvestmentPlan]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(InvestmentPlanModel).order_by(InvestmentPlanModel.version.desc()).limit(limit)
            ).all()
        return [self._entity(row) for row in rows]

    def add_version(self, values: dict[str, object], created_at: datetime) -> InvestmentPlan:
        with self._session_factory() as session:
            latest = session.scalar(select(func.max(InvestmentPlanModel.version))) or 0
            row = InvestmentPlanModel(version=latest + 1, created_at=created_at, **values)
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._entity(row)

class SqlAlchemyActualAccountRepository:
    """The investor's real account ledger (S5-W03); entries are voided, never deleted."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _flow(row: ActualCashFlowModel) -> ActualCashFlow:
        return ActualCashFlow(
            id=row.id, day=row.day, kind=row.kind, amount=Decimal(row.amount), symbol=row.symbol or "",
            note=row.note or "", voided=row.voided_at is not None,
        )

    @staticmethod
    def _trade(row: ActualTradeModel) -> ActualTrade:
        return ActualTrade(
            id=row.id, day=row.day, symbol=row.symbol, side=row.side, shares=int(row.shares),
            price=Decimal(row.price), fee=int(row.fee), tax=int(row.tax), note=row.note or "",
            voided=row.voided_at is not None,
        )

    def add_cash_flow(self, value: ActualCashFlow, created_at: datetime) -> ActualCashFlow:
        with self._session_factory() as session:
            row = ActualCashFlowModel(
                day=value.day, kind=value.kind, amount=value.amount, symbol=value.symbol,
                note=value.note, created_at=created_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._flow(row)

    def add_trade(self, value: ActualTrade, created_at: datetime) -> ActualTrade:
        with self._session_factory() as session:
            row = ActualTradeModel(
                day=value.day, symbol=value.symbol, side=value.side, shares=value.shares,
                price=value.price, fee=value.fee, tax=value.tax, note=value.note, created_at=created_at,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._trade(row)

    def list_cash_flows(self, include_voided: bool = False) -> list[ActualCashFlow]:
        statement = select(ActualCashFlowModel).order_by(ActualCashFlowModel.day, ActualCashFlowModel.id)
        if not include_voided:
            statement = statement.where(ActualCashFlowModel.voided_at.is_(None))
        with self._session_factory() as session:
            return [self._flow(row) for row in session.scalars(statement).all()]

    def list_trades(self, include_voided: bool = False) -> list[ActualTrade]:
        statement = select(ActualTradeModel).order_by(ActualTradeModel.day, ActualTradeModel.id)
        if not include_voided:
            statement = statement.where(ActualTradeModel.voided_at.is_(None))
        with self._session_factory() as session:
            return [self._trade(row) for row in session.scalars(statement).all()]

    def void(self, kind: str, entry_id: int, reason: str, voided_at: datetime) -> bool:
        model = ActualTradeModel if kind == "trade" else ActualCashFlowModel
        with self._session_factory() as session:
            row = session.get(model, entry_id)
            if row is None or row.voided_at is not None:
                return False
            row.voided_at = voided_at
            row.void_reason = reason
            session.commit()
            return True
