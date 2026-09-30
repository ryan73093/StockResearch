"""Trading costs and after-hours odd-lot fills (S3-W03).

Fees follow the common broker formula: floor(amount × 0.1425% × discount)
with a minimum fee; securities transaction tax is charged on sells only and
depends on the instrument (stock 0.3%, stock ETF 0.1%, bond ETF exempt
through 2026-12-31). Fills model the 14:30 after-hours odd-lot call auction
as the day's close plus a slippage allowance, rounded to the tick in the
direction that hurts the trader.

Defaults are conservative until the user confirms the broker discount and
minimum fee (REQUIREMENTS §14).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

TAX_RATES = {"stock": 0.003, "stock_etf": 0.001, "bond_etf": 0.0}


@dataclass(frozen=True, slots=True)
class CostModel:
    fee_rate: float = 0.001425
    fee_discount: float = 1.0
    minimum_fee: int = 20
    slippage_bps: float = 10.0
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
