"""Strategy specification v1 (S3-W05).

A spec only describes rules: when new money is invested, how much of the
available cash goes in (optionally scaled by a market signal), and how it is
split across instruments. Instruments are limited to the research catalog's
ETFs; individual stocks cannot be named (REQUIREMENTS §8). The AI researcher
(S4) will only be able to emit documents that validate against this model.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quant_platform.research.history.catalog import SERIES

TRADABLE = {item.key for item in SERIES if item.tradable}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Contribution(_Strict):
    """When new money is invested."""

    invest_on: Literal["contribution_day", "day_of_month"] = "contribution_day"
    day_of_month: int | None = Field(default=None, ge=1, le=28)


class Sizing(_Strict):
    """How much of the available cash is invested on an invest day.

    all_cash: everything available (new money, idle cash, dividends).
    moving_average / drawdown: new money × multiplier, where the multiplier
    depends on the signal asset's close versus its moving average or its
    drawdown from the 252-session high; unspent cash builds a reserve that is
    capped at ``max_reserve_months`` of new money.
    """

    type: Literal["all_cash", "moving_average", "drawdown"] = "all_cash"
    signal_asset: str | None = None
    ma_sessions: int = Field(default=200, ge=5, le=500)
    drawdown_threshold: float = Field(default=0.1, gt=0, lt=1)
    weak_multiplier: float = Field(default=2.0, ge=0, le=5)
    strong_multiplier: float = Field(default=1.0, ge=0, le=5)
    max_reserve_months: float = Field(default=6.0, ge=0, le=60)


class Defensive(_Strict):
    """Trend control: while the signal closes below its moving average, the target becomes
    these weights (for example all in a bond ETF), or cash when they are empty; with
    ``rebalance="band"`` the holdings move too. (Bond ETFs list from 2017, after the development period.)"""

    weights: dict[str, float] = Field(default_factory=dict)
    signal_asset: str | None = None  # default: the spec's signal
    ma_sessions: int = Field(default=200, ge=20, le=500)


class Rotation(_Strict):
    """ETF rotation (and core + satellite): on each invest day the ``top`` candidates with the best
    (momentum) or worst (reversal) return over ``lookback_sessions`` share what ``core`` leaves."""

    candidates: list[str] = Field(min_length=2, max_length=8)
    lookback_sessions: int = Field(default=126, ge=20, le=504)
    top: int = Field(default=1, ge=1, le=4)
    mode: Literal["momentum", "reversal"] = "momentum"
    core: dict[str, float] = Field(default_factory=dict)  # fixed weights, together below 1


class Allocation(_Strict):
    """``weights`` is the target; with ``rotation`` it is the fallback while a candidate lacks history."""

    weights: dict[str, float]
    rebalance: Literal["with_new_money", "band"] = "with_new_money"
    band: float = Field(default=0.05, gt=0, lt=0.5)
    defensive: Defensive | None = None
    rotation: Rotation | None = None


EXTENSIONS = ("defensive", "rotation")  # absent from the canonical form when unused: older hashes stay


class StrategySpec(_Strict):
    schema_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    allocation: Allocation
    contribution: Contribution = Contribution()
    sizing: Sizing = Sizing()
    execution: Literal["after_hours_odd_lot"] = "after_hours_odd_lot"

    @model_validator(mode="after")
    def _check(self) -> "StrategySpec":
        _check_weights(self.allocation.weights, "配置")
        if self.contribution.invest_on == "day_of_month" and self.contribution.day_of_month is None:
            raise ValueError("invest_on=day_of_month 需要 day_of_month")
        signals = [self.sizing.signal_asset]
        if self.allocation.defensive is not None:
            signals.append(self.allocation.defensive.signal_asset)
        for signal in signals:
            if signal is not None and signal not in TRADABLE | {"TAIEX"}:
                raise ValueError(f"訊號標的 {signal} 不在研究目錄")
        if self.allocation.defensive is not None and self.allocation.defensive.weights:  # empty: hold cash
            _check_weights(self.allocation.defensive.weights, "防守配置")
        rotation = self.allocation.rotation
        if rotation is not None:
            unknown = sorted(set(rotation.candidates) - TRADABLE)
            if unknown:
                raise ValueError(f"只能使用研究目錄的 ETF：{', '.join(unknown)} 不在清單內")
            if len(set(rotation.candidates)) != len(rotation.candidates) or rotation.top >= len(rotation.candidates):
                raise ValueError("輪動候選不可重複，且挑選檔數必須少於候選數")
            if rotation.core:
                _check_weights(rotation.core, "核心配置", total_below_one=True)
        return self

    @property
    def assets(self) -> list[str]:
        """Every instrument the rules can hold."""
        held = set(self.allocation.weights)
        if self.allocation.defensive is not None:
            held |= set(self.allocation.defensive.weights)
        if self.allocation.rotation is not None:
            held |= set(self.allocation.rotation.candidates) | set(self.allocation.rotation.core)
        return sorted(held)

    @property
    def signal(self) -> str:
        return self.sizing.signal_asset or sorted(self.allocation.weights)[0]

    @property
    def signals(self) -> set[str]:
        """Instruments whose past closes the rules read (none for plain all-cash buying)."""
        read = set()
        if self.sizing.type != "all_cash":
            read.add(self.signal)
        if self.allocation.defensive is not None:
            read.add(self.allocation.defensive.signal_asset or self.signal)
        if self.allocation.rotation is not None:
            read |= set(self.allocation.rotation.candidates)
        return read

    def canonical_dict(self, exclude: set[str] | None = None) -> dict[str, object]:
        """The rule as data; extensions that are not used are left out, so specs written before
        they existed keep their hashes (trial reuse and the registry depend on them)."""
        data = self.model_dump(mode="json", exclude=exclude)
        for key in EXTENSIONS:
            if data["allocation"].get(key) is None:
                data["allocation"].pop(key, None)
        return data

    def canonical_json(self) -> str:
        return json.dumps(self.canonical_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @property
    def spec_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


def _check_weights(weights: dict[str, float], label: str, total_below_one: bool = False) -> None:
    unknown = sorted(set(weights) - TRADABLE)
    if unknown:
        raise ValueError(f"只能使用研究目錄的 ETF：{', '.join(unknown)} 不在清單內")
    if any(weight <= 0 for weight in weights.values()):
        raise ValueError(f"{label}權重必須大於 0")
    total = sum(weights.values())
    if total_below_one and not total < 1:
        raise ValueError(f"{label}權重合計必須小於 1（其餘由輪動標的分配）")
    if not total_below_one and abs(total - 1) > 1e-6:
        raise ValueError(f"{label}權重合計必須為 1")


BASELINES: dict[str, StrategySpec] = {
    "benchmark_dca": StrategySpec(
        name="定期定額基準",
        description="每次入帳當天以盤後零股把可用現金全數買進 0050；股利留到下次一起投入。所有研究策略的比較基準。",
        allocation=Allocation(weights={"0050": 1.0}),
    ),
    "fixed_day_dca": StrategySpec(
        name="定期定額（每月 6 日）",
        description="券商常見的固定日期扣款：每月 6 日（遇休市順延）買進 0050。",
        allocation=Allocation(weights={"0050": 1.0}),
        contribution=Contribution(invest_on="day_of_month", day_of_month=6),
    ),
    "ma_value": StrategySpec(
        name="定期不定額（200 日均線）",
        description="0050 收盤低於 200 日均線時投入當月新資金的 2 倍，否則 0.5 倍；閒置現金最多保留 6 個月新資金。",
        allocation=Allocation(weights={"0050": 1.0}),
        sizing=Sizing(type="moving_average", ma_sessions=200, weak_multiplier=2.0, strong_multiplier=0.5),
    ),
    "rebalance_80_20": StrategySpec(
        name="股債 80/20 再平衡",
        description="0050 80%、00679B 20%；新資金補足偏低的一方，偏離超過 5 個百分點時賣高買低回到目標。",
        allocation=Allocation(weights={"0050": 0.8, "00679B": 0.2}, rebalance="band", band=0.05),
    ),
}


def load_spec(value: str) -> StrategySpec:
    """``baseline:<name>`` or a path to a JSON document."""
    if value.startswith("baseline:"):
        name = value.split(":", 1)[1]
        if name not in BASELINES:
            raise ValueError(f"未知基準：{name}；可用：{', '.join(BASELINES)}")
        return BASELINES[name]
    from pathlib import Path

    return StrategySpec.model_validate_json(Path(value).read_text(encoding="utf-8"))


def json_schema() -> dict[str, object]:
    return StrategySpec.model_json_schema()
