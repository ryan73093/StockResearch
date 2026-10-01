"""Trading costs and after-hours odd-lot fills (S3-W03).

Fees follow the common broker formula: floor(amount × 0.1425% × discount)
with a minimum fee; securities transaction tax is charged on sells only and
depends on the instrument (stock 0.3%, stock ETF 0.1%, bond ETF exempt
through 2026-12-31). Fills model the 14:30 after-hours odd-lot call auction
as the day's close plus a slippage allowance, rounded to the tick in the
direction that hurts the trader.

Slippage defaults to 20 bps: sampled after-hours odd-lot prices (TWT53U,
every 10th session since 2010; instance/research/history/odd_lot.json) sit a
median 13 bps (0050), 18 bps (0056) and 16 bps (00878) above the close.

The default cost model is the conservative profile (no discount, minimum
NT$20). Broker profiles (BROKERS) carry the user's brokers' published
odd-lot terms; they stay marked unconfirmed until the user checks them
against a statement (REQUIREMENTS §14).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

TAX_RATES = {"stock": 0.003, "stock_etf": 0.001, "bond_etf": 0.0}
DEFAULT_SLIPPAGE_BPS = 20.0


@dataclass(frozen=True, slots=True)
class CostModel:
    fee_rate: float = 0.001425
    fee_discount: float = 1.0
    minimum_fee: int = 20
    slippage_bps: float = DEFAULT_SLIPPAGE_BPS
    tax_rates: tuple[tuple[str, float], ...] = tuple(sorted(TAX_RATES.items()))

    def fee(self, amount: float) -> int:
        if amount <= 0:
            return 0
        return max(self.minimum_fee, math.floor(amount * self.fee_rate * self.fee_discount))

    def tax(self, amount: float, kind: str, side: str) -> int:
        if side != "SELL" or amount <= 0:
            return 0
        return math.floor(amount * dict(self.tax_rates)[kind])

    def scaled(self, factor: float) -> "CostModel":
        """Robustness variant, e.g. doubled costs (S4 promotion gate)."""
        return CostModel(
            fee_rate=self.fee_rate * factor,
            fee_discount=self.fee_discount,
            minimum_fee=int(round(self.minimum_fee * factor)),
            slippage_bps=self.slippage_bps * factor,
            tax_rates=self.tax_rates,
        )

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["tax_rates"] = dict(self.tax_rates)
        return payload


@dataclass(frozen=True, slots=True)
class BrokerProfile:
    key: str
    name: str
    fee_discount: float
    minimum_fee: int
    rebate: str      # when the discount is paid back: 當日折 / 月退 / 待確認
    note: str
    confirmed: bool = False  # True once the user checked it against a statement

    def cost_model(self, slippage_bps: float = DEFAULT_SLIPPAGE_BPS) -> CostModel:
        return CostModel(fee_discount=self.fee_discount, minimum_fee=self.minimum_fee, slippage_bps=slippage_bps)


# Electronic odd-lot terms as published in 2026; the official pages confirm the
# formula and the NT$1 regular-savings fees, the discounts come from public
# broker comparisons. Monthly rebates (月退) are modelled as if deducted at once.
BROKERS: dict[str, BrokerProfile] = {
    "conservative": BrokerProfile(
        "conservative", "保守估計", 1.0, 20, "—",
        "不打折、每筆最低 20 元；還沒確認券商條件時使用，研究預設也用這組。",
    ),
    "cathay": BrokerProfile(
        "cathay", "國泰證券", 0.28, 1, "待確認",
        "電子下單 2.8 折、零股每筆最低 1 元（零股另有 1.8 折優惠至 2026-12-31，未計入）；"
        "定期定額每筆 1 元（官網，至 2026-12-31）。以對帳單為準。",
    ),
    "taishin": BrokerProfile(
        "taishin", "台新證券", 0.28, 1, "月退",
        "電子下單 2.8 折、月退（成交時先收原價，次月退回）；零股每筆最低 1 元；"
        "存才富定期定額／預約買零股每筆 2 萬元以下收 1 元（官網）。以對帳單為準。",
    ),
}


def broker_costs(key: str | None) -> CostModel:
    """Cost model of a broker profile; unknown or missing keys fall back to conservative."""
    return BROKERS.get(key or "conservative", BROKERS["conservative"]).cost_model()


def etf_tick(price: float) -> float:
    """TWSE/TPEx tick size for ETFs and ETNs: 0.01 below 50, 0.05 from 50."""
    return 0.01 if price < 50 else 0.05


def fill_price(close: float, side: str, slippage_bps: float) -> float:
    """After-hours odd-lot fill: close ± slippage, rounded against the trader."""
    raw = close * (1 + slippage_bps / 10_000) if side == "BUY" else close * (1 - slippage_bps / 10_000)
    tick = etf_tick(raw)
    steps = raw / tick
    rounded = math.ceil(steps - 1e-9) if side == "BUY" else math.floor(steps + 1e-9)
    return round(rounded * tick, 2)


def affordable_shares(cash: float, price: float, costs: CostModel) -> int:
    """Largest whole number of shares whose cost plus fee fits in ``cash``."""
    if price <= 0 or cash <= 0:
        return 0
    shares = math.floor(cash / (price * (1 + costs.fee_rate * costs.fee_discount)))
    while shares > 0 and shares * price + costs.fee(shares * price) > cash + 1e-9:
        shares -= 1
    return shares
