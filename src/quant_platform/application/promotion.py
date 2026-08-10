from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta

from quant_platform.application.ports import PromotionRepository, ShadowTradingRepository
from quant_platform.domain.entities import (
    BrokerExecutionAudit, PromotionReview, PromotionStatus, ShadowOrderStatus,
)
from quant_platform.execution import (
    BrokerOrderRequest, LocalSandboxBrokerAdapter,
)
from quant_platform.reinforcement_learning.environment import RlEnvironmentService


@dataclass(frozen=True, slots=True)
class PromotionPolicy:
    version: str = "promotion-v1"
    minimum_fold_count: int = 3
    minimum_positive_excess_fold_ratio: float = 0.60
    minimum_excess_return: float = 0.0
    minimum_median_sharpe: float = 0.75
    minimum_worst_drawdown: float = -0.25
    minimum_shadow_observations: int = 20
    minimum_shadow_days: int = 30
    minimum_shadow_excess_return: float = 0.0
    minimum_shadow_average_return: float = 0.0
    approval_days: int = 30


@dataclass(frozen=True, slots=True)
class PromotionCheck:
    key: str
    label: str
    passed: bool
    value: float | int | str | bool
    threshold: float | int | str | bool
    unit: str
    explanation: str


@dataclass(frozen=True, slots=True)
class PromotionReviewView:
    review: PromotionReview
    checks: tuple[PromotionCheck, ...]
    metrics: dict[str, object]
    passed_count: int
    total_count: int


@dataclass(frozen=True, slots=True)
class PromotionOverview:
    reviews: tuple[PromotionReviewView, ...]
    executions: tuple[BrokerExecutionAudit, ...]
    policy: PromotionPolicy
    sandbox_capability: object
    approved_count: int
    review_ready_count: int
    ineligible_count: int


@dataclass(frozen=True, slots=True)
class PromotionRevalidationResult:
    evaluated: int
    revoked: int
    skipped: int


class PromotionService:
    algorithms = ("cpu", "ppo", "dqn")

    def __init__(
        self,
        repository: PromotionRepository,
        shadow_repository: ShadowTradingRepository,
        rl_service: RlEnvironmentService,
        policy: PromotionPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._shadow = shadow_repository
        self._rl = rl_service
        self._policy = policy or PromotionPolicy()
        self._sandbox = LocalSandboxBrokerAdapter()

    @staticmethod
    def _normalize(raw_symbol: str) -> str:
        symbol = raw_symbol.strip().upper()
        return f"{symbol}.TW" if symbol.isdigit() else symbol

    def _research(self, symbol: str, algorithm: str):
        overview = self._rl.overview(symbol)
        if algorithm == "cpu":
            research = overview.cpu_agent
        else:
            research = next(
                (item for item in overview.neural_agents if item.algorithm == algorithm), None
            )
        if research is None:
            raise ValueError(f"尚無 {algorithm.upper()} 代理研究，請先完成訓練")
        return research

    def evaluate(self, raw_symbol: str, raw_algorithm: str) -> PromotionReviewView:
        symbol = self._normalize(raw_symbol)
        algorithm = raw_algorithm.strip().lower()
        if algorithm not in self.algorithms:
            raise ValueError("晉級審查只支援 CPU、PPO 或 DQN 代理")
        research = self._research(symbol, algorithm)
        summary = research.summary
        shadow = [
            item for item in self._shadow.list_orders(2000)
            if item.symbol == symbol and item.algorithm == algorithm
            and item.experiment_id == research.experiment_id
            and item.status == ShadowOrderStatus.EVALUATED
        ]
        returns = [item.return_rate or 0.0 for item in shadow]
        excess = [
            (item.return_rate or 0.0) - (item.benchmark_return or 0.0)
            for item in shadow
        ]
        times = sorted(item.execution_time for item in shadow if item.execution_time)
        shadow_days = (times[-1] - times[0]).days if len(times) >= 2 else 0
        average_shadow_return = sum(returns) / len(returns) if returns else 0.0
        average_shadow_excess = sum(excess) / len(excess) if excess else 0.0
        checks = (
            self._check("research_candidate", "研究候選門檻", summary.promotion_gate == "CANDIDATE", summary.promotion_gate, "CANDIDATE", "狀態", "底層樣本外研究必須先通過候選門檻。"),
            self._check("current_data", "資料指紋仍為最新", research.data_is_current, research.data_is_current, True, "布林值", "資料更新後舊模型不得沿用核准。"),
            self._check("fold_count", "樣本外折數", research.fold_count >= self._policy.minimum_fold_count, research.fold_count, self._policy.minimum_fold_count, "折", "至少三段獨立樣本外期間。"),
            self._check("positive_excess_folds", "正超額折數比例", summary.positive_excess_fold_ratio >= self._policy.minimum_positive_excess_fold_ratio, summary.positive_excess_fold_ratio, self._policy.minimum_positive_excess_fold_ratio, "%", "多數樣本外期間必須打贏基準。"),
            self._check("excess_return", "複合超額報酬", summary.compounded_excess_return > self._policy.minimum_excess_return, summary.compounded_excess_return, self._policy.minimum_excess_return, "%", "扣除成本後的樣本外報酬必須高於基準。"),
            self._check("median_sharpe", "Sharpe 中位數", summary.median_sharpe >= self._policy.minimum_median_sharpe, summary.median_sharpe, self._policy.minimum_median_sharpe, "無單位", "風險調整後報酬需達最低標準。"),
            self._check("worst_drawdown", "最差最大回撤", summary.worst_max_drawdown >= self._policy.minimum_worst_drawdown, summary.worst_max_drawdown, self._policy.minimum_worst_drawdown, "%", "任何一折回撤不得低於風險下限。"),
            self._check("shadow_count", "影子評估筆數", len(shadow) >= self._policy.minimum_shadow_observations, len(shadow), self._policy.minimum_shadow_observations, "筆", "必須累積足夠的逐日真實後續行情。"),
            self._check("shadow_days", "影子觀察期間", shadow_days >= self._policy.minimum_shadow_days, shadow_days, self._policy.minimum_shadow_days, "天", "避免只靠短期幸運行情。"),
            self._check("shadow_return", "平均影子報酬", average_shadow_return > self._policy.minimum_shadow_average_return, average_shadow_return, self._policy.minimum_shadow_average_return, "%", "含滑價與交易成本後平均報酬必須為正。"),
            self._check("shadow_excess", "平均影子超額報酬", average_shadow_excess > self._policy.minimum_shadow_excess_return, average_shadow_excess, self._policy.minimum_shadow_excess_return, "%", "影子決策平均必須勝過同期持有基準。"),
        )
        metrics = {
            "experiment_id": research.experiment_id,
            "fold_count": research.fold_count,
            "compounded_return": summary.compounded_return,
            "compounded_benchmark_return": summary.compounded_benchmark_return,
            "compounded_excess_return": summary.compounded_excess_return,
            "positive_excess_fold_ratio": summary.positive_excess_fold_ratio,
            "median_sharpe": summary.median_sharpe,
            "worst_max_drawdown": summary.worst_max_drawdown,
            "total_cost_twd": summary.total_cost_twd,
            "shadow_observations": len(shadow),
            "shadow_days": shadow_days,
            "average_shadow_return": average_shadow_return,
            "average_shadow_excess_return": average_shadow_excess,
        }
        now = datetime.now(UTC)
        existing = self._repository.find_review(
            symbol, algorithm, research.experiment_id, self._policy.version
        )
        all_passed = all(item.passed for item in checks)
        status = PromotionStatus.REVIEW_READY if all_passed else PromotionStatus.INELIGIBLE
        if existing:
            if existing.status == PromotionStatus.APPROVED:
                status = PromotionStatus.APPROVED if all_passed else PromotionStatus.REVOKED
            elif existing.status in {PromotionStatus.REJECTED, PromotionStatus.REVOKED}:
                status = existing.status
            review = replace(
                existing, status=status, checks_json=json.dumps([asdict(item) for item in checks], ensure_ascii=False),
                metrics_json=json.dumps(metrics, ensure_ascii=False), evaluated_at=now,
                approval_expires_at=(existing.approval_expires_at if status == PromotionStatus.APPROVED else None),
                updated_at=now,
            )
            self._repository.update_review(review)
        else:
            review = PromotionReview(
                id=None, symbol=symbol, algorithm=algorithm,
                experiment_id=research.experiment_id, policy_version=self._policy.version,
                status=status,
                checks_json=json.dumps([asdict(item) for item in checks], ensure_ascii=False),
                metrics_json=json.dumps(metrics, ensure_ascii=False), reviewer=None,
                review_note=None, evaluated_at=now, reviewed_at=None,
                approval_expires_at=None, created_at=now, updated_at=now,
            )
            review = replace(review, id=self._repository.save_review(review))
        return self._view(review)

    @staticmethod
    def _check(
        key: str, label: str, passed: bool, value: object, threshold: object,
        unit: str, explanation: str,
    ) -> PromotionCheck:
        return PromotionCheck(
            key=key, label=label, passed=bool(passed), value=value,
            threshold=threshold, unit=unit, explanation=explanation,
        )

    def review(
        self, review_id: int, decision: str, reviewer: str, note: str
    ) -> PromotionReviewView:
        current = self._required_review(review_id)
        reviewer = reviewer.strip()
        note = note.strip()
        if len(reviewer) < 2:
            raise ValueError("審查人至少需要 2 個字元")
        if len(note) < 10:
            raise ValueError("審查理由至少需要 10 個字元，以保留可稽核依據")
        now = datetime.now(UTC)
        normalized = decision.strip().lower()
        if normalized == "approve":
            if current.status != PromotionStatus.REVIEW_READY:
                raise PermissionError("只有全部量化門檻通過的待審研究才能核准")
            if not all(item.passed for item in self._view(current).checks):
                raise PermissionError("檢核結果不完整，禁止核准")
            status = PromotionStatus.APPROVED
            expires = now + timedelta(days=self._policy.approval_days)
        elif normalized == "reject":
            if current.status == PromotionStatus.APPROVED:
                raise PermissionError("已核准研究必須使用撤銷流程")
            status = PromotionStatus.REJECTED
            expires = None
        else:
            raise ValueError("審查決定只接受 approve 或 reject")
        updated = replace(
            current, status=status, reviewer=reviewer, review_note=note,
            reviewed_at=now, approval_expires_at=expires, updated_at=now,
        )
        self._repository.update_review(updated)
        return self._view(updated)

    def revoke(self, review_id: int, reviewer: str, note: str) -> PromotionReviewView:
        current = self._required_review(review_id)
        if current.status != PromotionStatus.APPROVED:
            raise PermissionError("只有已核准研究可以撤銷")
        if len(reviewer.strip()) < 2 or len(note.strip()) < 10:
            raise ValueError("撤銷必須提供審查人與至少 10 個字元的原因")
        now = datetime.now(UTC)
        updated = replace(
            current, status=PromotionStatus.REVOKED, reviewer=reviewer.strip(),
            review_note=note.strip(), reviewed_at=now, approval_expires_at=None,
            updated_at=now,
        )
        self._repository.update_review(updated)
        return self._view(updated)

    def submit_sandbox(self, review_id: int, shadow_order_id: int) -> BrokerExecutionAudit:
        review = self._required_review(review_id)
        now = datetime.now(UTC)
        if review.status != PromotionStatus.APPROVED:
            raise PermissionError("研究尚未取得有效人工核准，禁止送入券商沙盒")
        if review.approval_expires_at is None or review.approval_expires_at <= now:
            expired = replace(
                review, status=PromotionStatus.REVOKED, approval_expires_at=None,
                updated_at=now, review_note="核准期限已到，系統自動撤銷。",
            )
            self._repository.update_review(expired)
            raise PermissionError("人工核准已到期，禁止送入券商沙盒")
        order = self._shadow.get_order(shadow_order_id)
        if order is None or order.id is None:
            raise LookupError("找不到影子委託")
        if (
            order.symbol != review.symbol or order.algorithm != review.algorithm
            or order.experiment_id != review.experiment_id
        ):
            raise PermissionError("影子委託與核准研究不一致")
        if order.side not in {"BUY", "SELL"} or order.quantity <= 0:
            raise PermissionError("無有效股數的影子決策不能送入沙盒")
        client_id = f"promotion-{review.id}-shadow-{order.id}"
        existing = self._repository.get_execution_by_client_id(client_id)
        if existing:
            return existing
        receipt = self._sandbox.submit(BrokerOrderRequest(
            client_order_id=client_id, symbol=order.symbol, side=order.side,
            quantity=order.quantity, order_type="MARKET", limit_price=None,
        ))
        audit = BrokerExecutionAudit(
            id=None, promotion_review_id=review.id, shadow_order_id=order.id,
            client_order_id=client_id, environment="sandbox", symbol=order.symbol,
            side=order.side, quantity=order.quantity, order_type="MARKET",
            limit_price=None, status=receipt.status,
            broker_order_id=receipt.broker_order_id, message=receipt.message,
            created_at=now, updated_at=now,
        )
        return replace(audit, id=self._repository.save_execution(audit))

    def _required_review(self, review_id: int) -> PromotionReview:
        review = self._repository.get_review(review_id)
        if review is None:
            raise LookupError("找不到晉級審查")
        return review

    def revalidate_all(self) -> PromotionRevalidationResult:
        evaluated = revoked = skipped = 0
        for current in self._repository.list_reviews(2000):
            try:
                latest = self._research(current.symbol, current.algorithm)
                if (
                    current.status == PromotionStatus.APPROVED
                    and latest.experiment_id != current.experiment_id
                ):
                    now = datetime.now(UTC)
                    self._repository.update_review(replace(
                        current, status=PromotionStatus.REVOKED,
                        approval_expires_at=None, updated_at=now,
                        review_note="已有較新的代理實驗，系統自動撤銷舊實驗核准。",
                    ))
                    revoked += 1
                before = current.status
                result = self.evaluate(current.symbol, current.algorithm)
                evaluated += 1
                if before == PromotionStatus.APPROVED and result.review.status == PromotionStatus.REVOKED:
                    revoked += 1
            except (ValueError, RuntimeError):
                skipped += 1
        return PromotionRevalidationResult(evaluated=evaluated, revoked=revoked, skipped=skipped)

    @staticmethod
    def _view(review: PromotionReview) -> PromotionReviewView:
        checks = tuple(PromotionCheck(**item) for item in json.loads(review.checks_json))
        return PromotionReviewView(
            review=review, checks=checks, metrics=json.loads(review.metrics_json),
            passed_count=sum(item.passed for item in checks), total_count=len(checks),
        )

    def overview(self) -> PromotionOverview:
        now = datetime.now(UTC)
        current_reviews = []
        for review in self._repository.list_reviews():
            if (
                review.status == PromotionStatus.APPROVED
                and review.approval_expires_at is not None
                and review.approval_expires_at <= now
            ):
                review = replace(
                    review, status=PromotionStatus.REVOKED,
                    approval_expires_at=None, updated_at=now,
                    review_note="核准期限已到，系統自動撤銷。",
                )
                self._repository.update_review(review)
            current_reviews.append(review)
        reviews = tuple(self._view(item) for item in current_reviews)
        return PromotionOverview(
            reviews=reviews, executions=tuple(self._repository.list_executions()),
            policy=self._policy, sandbox_capability=self._sandbox.capability(),
            approved_count=sum(item.review.status == PromotionStatus.APPROVED for item in reviews),
            review_ready_count=sum(item.review.status == PromotionStatus.REVIEW_READY for item in reviews),
            ineligible_count=sum(item.review.status == PromotionStatus.INELIGIBLE for item in reviews),
        )
