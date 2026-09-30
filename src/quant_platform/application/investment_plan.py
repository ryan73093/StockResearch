"""Investment plan with versions (S5-W01).

The plan says how much money arrives each month, on which day, which
strategy the daily advice follows and how deep a drawdown the investor
accepts. Every save creates a new version; decisions will cite the version
they used (S5-W02). Only built-in baselines can be chosen until candidates
are promoted through S4-W06.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from quant_platform.domain.entities import InvestmentPlan
from quant_platform.research.spec import BASELINES


class InvestmentPlanError(ValueError):
    pass


def strategy_name(plan: InvestmentPlan) -> str:
    spec = BASELINES.get(plan.strategy_key)
    return spec.name if spec else plan.strategy_key


def parse_plan_form(form: dict[str, str]) -> dict[str, object]:
    """Validate raw form text; raises InvestmentPlanError with a readable message."""
    try:
        amount = Decimal(str(form.get("monthly_amount", "")).replace(",", "").strip())
    except InvalidOperation as exc:
        raise InvestmentPlanError("每月投入金額必須是數字") from exc
    if not Decimal("1000") <= amount <= Decimal("10000000"):
        raise InvestmentPlanError("每月投入金額需在 1,000～10,000,000 元之間")
    try:
        salary_day = int(str(form.get("salary_day", "")).strip())
    except ValueError as exc:
        raise InvestmentPlanError("薪資日必須是 1～28 的整數") from exc
    if not 1 <= salary_day <= 28:
        raise InvestmentPlanError("薪資日必須是 1～28（每個月都有的日期）")
    strategy_key = str(form.get("strategy_key", "")).strip()
    if strategy_key not in BASELINES:
        raise InvestmentPlanError("請選擇列出的策略")
    try:
        drawdown = float(str(form.get("max_drawdown_tolerance", "")).strip()) / 100
    except ValueError as exc:
        raise InvestmentPlanError("可承受回撤必須是百分比數字") from exc
    if not 0.05 <= drawdown <= 0.8:
        raise InvestmentPlanError("可承受回撤需在 5%～80% 之間")
    horizon_text = str(form.get("horizon_years", "")).strip()
    horizon = None
    if horizon_text:
        try:
            horizon = int(horizon_text)
        except ValueError as exc:
            raise InvestmentPlanError("預計投資年數必須是整數") from exc
        if not 1 <= horizon <= 60:
            raise InvestmentPlanError("預計投資年數需在 1～60 年之間")
    return {
        "monthly_amount": amount.quantize(Decimal("1")),
        "salary_day": salary_day,
        "strategy_key": strategy_key,
        "max_drawdown_tolerance": round(drawdown, 4),
        "horizon_years": horizon,
        "goal": str(form.get("goal", "")).strip()[:200],
        "note": str(form.get("note", "")).strip()[:1000],
    }


class InvestmentPlanService:
    def __init__(self, repository) -> None:
        self._repository = repository

    def current(self) -> InvestmentPlan | None:
        return self._repository.latest()

    def history(self, limit: int = 20) -> list[InvestmentPlan]:
        return self._repository.list_versions(limit)

    def save(self, form: dict[str, str], now: datetime | None = None) -> InvestmentPlan:
        """Validate the form and store it as the next version."""
        return self._repository.add_version(parse_plan_form(form), now or datetime.now(UTC))
