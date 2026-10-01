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
NT$20). Broker profiles (BROKERS) carry the user's brokers' odd-lot terms.
The user's brokers rebate the discount monthly (月退); the user decided
(2026-10-01) that the rebate is extra income and is not modelled: estimates
use the list price charged on the trade, and real profit and loss follows
the broker statement (REQUIREMENTS §5).
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
    fee_discount: float  # applied on the trade; a monthly rebate is not a discount here
    minimum_fee: int
    rebate: str          # how a discount comes back, e.g. 月退（不計入）
    note: str
    confirmed: bool = False  # True once the user decided how this broker is modelled

    def cost_model(self, slippage_bps: float = DEFAULT_SLIPPAGE_BPS) -> CostModel:
        return CostModel(fee_discount=self.fee_discount, minimum_fee=self.minimum_fee, slippage_bps=slippage_bps)


# The fee charged on the trade: list price 0.1425%, odd-lot minimum NT$1 (both
# brokers' published odd-lot terms). The monthly rebate (月退) is extra income
# the user does not want modelled (2026-10-01).
BROKERS: dict[str, BrokerProfile] = {
    "conservative": BrokerProfile(
        "conservative", "保守估計", 1.0, 20, "—",
        "還沒選券商時使用；研究預設也用這組。",
    ),
    "cathay": BrokerProfile(
        "cathay", "國泰證券", 1.0, 1, "月退（不計入）",
        "成交時收原價 0.1425%、零股每筆最低 1 元；月退的退佣視為額外收入，不計入估算。實際損益以對帳單為準。",
        confirmed=True,
    ),
    "taishin": BrokerProfile(
        "taishin", "台新證券", 1.0, 1, "月退（不計入）",
        "成交時收原價 0.1425%、零股每筆最低 1 元；月退的退佣視為額外收入，不計入估算。"
        "存才富定期定額／預約買零股另有每筆 2 萬元以下收 1 元的方案。實際損益以對帳單為準。",
        confirmed=True,
    ),
}


def broker_costs(key: str | None) -> CostModel:
    """Cost model of a broker profile; unknown or missing keys fall back to conservative."""
    return BROKERS.get(key or "conservative", BROKERS["conservative"]).cost_model()


def etf_tick(price: float) -> float:
    """TWSE/TPEx tick size for ETFs and ETNs: 0.01 below 50, 0.05 from 50."""
    return 0.01 if price < 50 else 0.05


def stock_tick(price: float) -> float:
    """TWSE/TPEx tick size for stocks: 0.01 below 10, 0.05 below 50, 0.1 below 100, 0.5 below 500,
    1 below 1,000 and 5 from 1,000 (the legacy challenger evaluation trades stocks)."""
    for limit, tick in ((10, 0.01), (50, 0.05), (100, 0.1), (500, 0.5), (1_000, 1.0)):
        if price < limit:
            return tick
    return 5.0


def fill_price(close: float, side: str, slippage_bps: float, tick_size=etf_tick) -> float:
    """After-hours odd-lot fill: close ± slippage, rounded against the trader."""
    raw = close * (1 + slippage_bps / 10_000) if side == "BUY" else close * (1 - slippage_bps / 10_000)
    tick = tick_size(raw)
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
