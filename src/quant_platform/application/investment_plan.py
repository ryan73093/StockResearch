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


FULL_WIDTH = str.maketrans("０１２３４５６７８９．，％", "0123456789.,%")


def _clean(text: object, *suffixes: str) -> str:
    """Accept what people type: full-width digits, thousands separators, units."""
    value = str(text or "").translate(FULL_WIDTH).strip()
    for token in ("NT$", "NTD", "$", ",", " ", *suffixes):
        value = value.replace(token, "")
    return value


def parse_amount(text: object) -> Decimal:
    """'10000'、'10,000 元'、'1萬'、'1.5 萬'、'NT$ 12,000' → Decimal."""
    value = _clean(text, "元", "塊")
    multiplier = Decimal(1)
    if value.endswith("萬"):
        value, multiplier = value[:-1], Decimal(10_000)
    elif value.endswith("千"):
        value, multiplier = value[:-1], Decimal(1_000)
    try:
        return Decimal(value) * multiplier
    except InvalidOperation as exc:
        raise InvestmentPlanError(f"看不懂每月投入金額「{text}」；請輸入數字，例如 10000 或 1萬") from exc


def parse_plan_form(form: dict[str, str]) -> dict[str, object]:
    """Validate raw form text; raises InvestmentPlanError with a readable message.

    Salary days 29–31 fall back to the month's last day in shorter months
    (ContributionPlan.schedule and the Today advice both clamp).
    """
    amount = parse_amount(form.get("monthly_amount", ""))
    if not Decimal("1000") <= amount <= Decimal("10000000"):
        raise InvestmentPlanError("每月投入金額需在 1,000～10,000,000 元之間")
    day_text = _clean(form.get("salary_day", ""), "日", "號", "每月")
    if day_text in {"月底", "最後一天"}:
        day_text = "31"
    try:
        salary_day = int(day_text)
    except ValueError as exc:
        raise InvestmentPlanError("薪資日請填 1～31 的數字（月底請填 31）") from exc
    if not 1 <= salary_day <= 31:
        raise InvestmentPlanError("薪資日請填 1～31（月底請填 31）")
    strategy_key = str(form.get("strategy_key", "")).strip()
    if strategy_key not in BASELINES:
        raise InvestmentPlanError("請在「採用策略」選一個策略")
    try:
        drawdown = float(_clean(form.get("max_drawdown_tolerance", ""), "%")) / 100
    except ValueError as exc:
        raise InvestmentPlanError("可承受回撤請填百分比數字，例如 30") from exc
    if not 0.05 <= drawdown <= 0.8:
        raise InvestmentPlanError("可承受回撤需在 5%～80% 之間")
    horizon_text = _clean(form.get("horizon_years", ""), "年")
    horizon = None
    if horizon_text:
        try:
            horizon = int(float(horizon_text))
        except ValueError as exc:
            raise InvestmentPlanError("預計投資年數請填數字，例如 20") from exc
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
