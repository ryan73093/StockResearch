from __future__ import annotations

from decimal import Decimal, ROUND_CEILING


class TaiwanExecutionCostModel:
    """Shared TW execution assumptions for paper trading, RL and shadow trading."""

    def __init__(
        self,
        commission_bps: Decimal = Decimal("14.25"),
        odd_lot_min_commission_twd: Decimal = Decimal("1"),
        regular_min_commission_twd: Decimal = Decimal("20"),
        stock_sell_tax_bps: Decimal = Decimal("30"),
        etf_sell_tax_bps: Decimal = Decimal("10"),
        slippage_bps: Decimal = Decimal("10"),
    ) -> None:
        self.commission_bps = commission_bps
        self.odd_lot_min_commission_twd = odd_lot_min_commission_twd
        self.regular_min_commission_twd = regular_min_commission_twd
        self.stock_sell_tax_bps = stock_sell_tax_bps
        self.etf_sell_tax_bps = etf_sell_tax_bps
        self.slippage_bps = slippage_bps

    @staticmethod
    def money(value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.01"))

    def slipped_price(self, open_price: Decimal, side: str) -> Decimal:
        direction = Decimal("1") if side.upper() == "BUY" else Decimal("-1")
        price = open_price * (
            Decimal("1") + direction * self.slippage_bps / Decimal("10000")
        )
        return price.quantize(Decimal("0.0001"))

    def commission(self, gross: Decimal, quantity: int) -> Decimal:
        minimum = (
            self.odd_lot_min_commission_twd
            if quantity < 1000 else self.regular_min_commission_twd
        )
        calculated = gross * self.commission_bps / Decimal("10000")
        return max(minimum, calculated).quantize(Decimal("1"), rounding=ROUND_CEILING)

    def sell_tax(self, gross: Decimal, asset_type: str = "EQUITY") -> Decimal:
        bps = self.etf_sell_tax_bps if "ETF" in asset_type.upper() else self.stock_sell_tax_bps
        return (gross * bps / Decimal("10000")).quantize(
            Decimal("1"), rounding=ROUND_CEILING
        )
