from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from quant_platform.application.ports import (
    MarketBarRepository, PaperTradingRepository, ResearchUniverseRepository,
)
from quant_platform.domain.entities import (
    PaperAccount, PaperFill, PaperOrder, PaperOrderStatus, PaperPosition,
)
from quant_platform.execution import TaiwanExecutionCostModel


ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class PaperTradingAssumptions:
    initial_cash_twd: Decimal = Decimal("1000000")
    commission_bps: Decimal = Decimal("14.25")
    odd_lot_min_commission_twd: Decimal = Decimal("1")
    regular_min_commission_twd: Decimal = Decimal("20")
    stock_sell_tax_bps: Decimal = Decimal("30")
    etf_sell_tax_bps: Decimal = Decimal("10")
    slippage_bps: Decimal = Decimal("10")
    max_position_weight: Decimal = Decimal("0.20")
    max_gross_exposure: Decimal = Decimal("0.80")
    max_volume_participation: Decimal = Decimal("0.05")
    drawdown_halt: Decimal = Decimal("0.10")
    stale_after_days: int = 10


@dataclass(frozen=True, slots=True)
class PaperPositionView:
    position: PaperPosition
    mark_price: Decimal
    market_value: Decimal
    unrealized_pnl: Decimal
    portfolio_weight: Decimal
    last_event_time: datetime | None


@dataclass(frozen=True, slots=True)
class PaperTradingOverview:
    account: PaperAccount
    positions: tuple[PaperPositionView, ...]
    orders: tuple[PaperOrder, ...]
    fills: tuple[PaperFill, ...]
    equity: Decimal
    total_return: Decimal
    gross_exposure: Decimal
    pending_count: int
    assumptions: PaperTradingAssumptions


@dataclass(frozen=True, slots=True)
class ProcessOrdersResult:
    pending: int
    filled: int
    rejected: int


class PaperTradingService:
    """Auditable next-open paper broker for Taiwan stocks and odd lots."""

    def __init__(
        self,
        repository: PaperTradingRepository,
        universe: ResearchUniverseRepository,
        bars: MarketBarRepository,
        assumptions: PaperTradingAssumptions | None = None,
    ) -> None:
        self._repository = repository
        self._universe = universe
        self._bars = bars
        self._assumptions = assumptions or PaperTradingAssumptions()
        self._costs = TaiwanExecutionCostModel(
            commission_bps=self._assumptions.commission_bps,
            odd_lot_min_commission_twd=self._assumptions.odd_lot_min_commission_twd,
            regular_min_commission_twd=self._assumptions.regular_min_commission_twd,
            stock_sell_tax_bps=self._assumptions.stock_sell_tax_bps,
            etf_sell_tax_bps=self._assumptions.etf_sell_tax_bps,
            slippage_bps=self._assumptions.slippage_bps,
        )
        self._account = repository.ensure_account(
            "本機台股模擬帳戶", self._assumptions.initial_cash_twd, datetime.now(UTC)
        )

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @staticmethod
    def _money(value: Decimal) -> Decimal:
        return TaiwanExecutionCostModel.money(value)

    def _normalize_symbol(self, raw_symbol: str) -> str:
        symbol = raw_symbol.strip().upper()
        if symbol.isdigit():
            symbol = f"{symbol}.TW"
        return symbol

    def _commission(self, gross: Decimal, quantity: int) -> Decimal:
        return self._costs.commission(gross, quantity)

    def _sell_tax(self, gross: Decimal, asset_type: str) -> Decimal:
        return self._costs.sell_tax(gross, asset_type)

    def _latest_bar(self, symbol: str, as_of: datetime):
        values = self._bars.list_bars(symbol, as_of=as_of)
        return values[-1] if values else None

    def _account_state(self, as_of: datetime) -> tuple[PaperAccount, dict[str, PaperPosition], Decimal, Decimal]:
        if self._account.id is None:
            raise RuntimeError("Paper account has not been persisted")
        account = self._repository.get_account(self._account.id)
        if account is None:
            raise LookupError("Paper account does not exist")
        positions = {item.symbol: item for item in self._repository.list_positions(account.id)}
        market_value = ZERO
        for symbol, position in positions.items():
            bar = self._latest_bar(symbol, as_of)
            if bar:
                market_value += Decimal(bar.close) * position.quantity
        return account, positions, account.cash + market_value, market_value

    def submit_order(
        self, symbol: str, side: str, quantity: int, now: datetime | None = None
    ) -> PaperOrder:
        submitted_at = now or datetime.now(UTC)
        submitted_at = self._aware(submitted_at)
        normalized = self._normalize_symbol(symbol)
        normalized_side = side.strip().upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError("買賣方向只能是 BUY 或 SELL")
        if quantity <= 0 or quantity > 1_000_000:
            raise ValueError("股數必須介於 1 至 1,000,000 股")
        asset = self._universe.get(normalized)
        if asset is None or asset.market != "TW":
            raise ValueError("第一版模擬交易只接受股票池內的台股標的")
        latest = self._latest_bar(normalized, submitted_at)
        account, positions, equity, gross_exposure = self._account_state(submitted_at)
        rejection: str | None = None
        estimated_price = Decimal(latest.close) if latest else ZERO
        eligible_after = self._aware(latest.event_time) if latest else submitted_at
        if latest is None:
            rejection = "缺少當下可用的日線行情"
        elif submitted_at - self._aware(latest.available_time) > timedelta(
            days=self._assumptions.stale_after_days
        ):
            rejection = f"行情已超過 {self._assumptions.stale_after_days} 天未更新"
        current = positions.get(normalized)
        current_quantity = current.quantity if current else 0
        pending = self._repository.list_pending_orders(account.id or 0)
        pending_buy_amount = sum(
            item.estimated_price * item.quantity for item in pending if item.side == "BUY"
        )
        pending_buy_quantity = sum(
            item.quantity for item in pending
            if item.side == "BUY" and item.symbol == normalized
        )
        pending_sell_quantity = sum(
            item.quantity for item in pending if item.side == "SELL" and item.symbol == normalized
        )
        estimated_gross = estimated_price * quantity
        estimated_commission = self._commission(estimated_gross, quantity) if latest else ZERO
        if rejection is None and normalized_side == "BUY":
            projected_position = estimated_price * (
                current_quantity + pending_buy_quantity + quantity
            )
            if equity <= ZERO:
                rejection = "帳戶權益小於或等於零"
            elif equity < account.initial_cash * (ONE - self._assumptions.drawdown_halt):
                rejection = "帳戶已觸發總回撤停止買進門檻"
            elif projected_position > equity * self._assumptions.max_position_weight:
                rejection = "超過單一標的 20% 權益上限"
            elif gross_exposure + pending_buy_amount + estimated_gross > equity * self._assumptions.max_gross_exposure:
                rejection = "超過帳戶 80% 總曝險上限"
            elif estimated_gross + estimated_commission + pending_buy_amount > account.cash:
                rejection = "可用現金不足（已包含待成交買單）"
            elif latest.volume > 0 and quantity > latest.volume * float(self._assumptions.max_volume_participation):
                rejection = "委託股數超過最近成交量的 5%"
        elif rejection is None and quantity > current_quantity - pending_sell_quantity:
            rejection = "持股不足；模擬帳戶不允許放空"
        return self._repository.save_order(PaperOrder(
            id=None, account_id=account.id or 0, symbol=normalized, market="TW",
            side=normalized_side, quantity=quantity, order_type="NEXT_OPEN_MARKET",
            status=PaperOrderStatus.REJECTED if rejection else PaperOrderStatus.PENDING,
            submitted_at=submitted_at, eligible_after_event_time=eligible_after,
            estimated_price=self._money(estimated_price), rejection_reason=rejection,
            completed_at=submitted_at if rejection else None,
        ))

    def process_pending(self, now: datetime | None = None) -> ProcessOrdersResult:
        processed_at = self._aware(now or datetime.now(UTC))
        if self._account.id is None:
            raise RuntimeError("Paper account has not been persisted")
        filled = rejected = 0
        for order in self._repository.list_pending_orders(self._account.id):
            future_bars = [
                item for item in self._bars.list_bars(order.symbol, as_of=processed_at)
                if self._aware(item.event_time) > self._aware(order.eligible_after_event_time)
            ]
            if not future_bars:
                continue
            execution_bar = future_bars[0]
            asset = self._universe.get(order.symbol)
            account, positions, _, _ = self._account_state(processed_at)
            position = positions.get(order.symbol) or PaperPosition(
                account_id=account.id or 0, symbol=order.symbol, quantity=0,
                average_cost=ZERO, realized_pnl=ZERO, updated_at=processed_at,
            )
            fill_price = self._costs.slipped_price(
                Decimal(execution_bar.open), order.side
            )
            gross = self._money(fill_price * order.quantity)
            commission = self._commission(gross, order.quantity)
            tax = self._sell_tax(gross, asset.asset_type) if order.side == "SELL" and asset else ZERO
            if order.side == "BUY":
                total_cost = gross + commission
                if total_cost > account.cash:
                    self._repository.reject_order(
                        order.id or 0, account.id or 0, "成交時可用現金不足", processed_at
                    )
                    rejected += 1
                    continue
                new_quantity = position.quantity + order.quantity
                new_cost = position.average_cost * position.quantity + total_cost
                new_position = replace(
                    position, quantity=new_quantity,
                    average_cost=self._money(new_cost / new_quantity), updated_at=processed_at,
                )
                new_account = replace(
                    account, cash=self._money(account.cash - total_cost), updated_at=processed_at,
                )
            else:
                if order.quantity > position.quantity:
                    self._repository.reject_order(
                        order.id or 0, account.id or 0, "成交時持股不足", processed_at
                    )
                    rejected += 1
                    continue
                proceeds = gross - commission - tax
                realized = proceeds - position.average_cost * order.quantity
                new_quantity = position.quantity - order.quantity
                new_position = replace(
                    position, quantity=new_quantity,
                    average_cost=ZERO if new_quantity == 0 else position.average_cost,
                    realized_pnl=self._money(position.realized_pnl + realized),
                    updated_at=processed_at,
                )
                new_account = replace(
                    account, cash=self._money(account.cash + proceeds),
                    realized_pnl=self._money(account.realized_pnl + realized),
                    updated_at=processed_at,
                )
            self._repository.execute_fill(
                order,
                PaperFill(
                    id=None, order_id=order.id or 0, account_id=account.id or 0,
                    symbol=order.symbol, side=order.side, quantity=order.quantity,
                    price=fill_price, gross_amount=gross, commission=commission,
                    transaction_tax=tax, slippage_bps=self._assumptions.slippage_bps,
                    bar_event_time=self._aware(execution_bar.event_time), executed_at=processed_at,
                ),
                new_account, new_position,
            )
            filled += 1
        pending = len(self._repository.list_pending_orders(self._account.id))
        return ProcessOrdersResult(pending=pending, filled=filled, rejected=rejected)

    def cancel(self, order_id: int, now: datetime | None = None) -> bool:
        return self._repository.cancel_order(
            order_id, self._account.id or 0, self._aware(now or datetime.now(UTC))
        )

    def overview(self, now: datetime | None = None) -> PaperTradingOverview:
        as_of = self._aware(now or datetime.now(UTC))
        account, positions, equity, gross = self._account_state(as_of)
        views: list[PaperPositionView] = []
        for position in positions.values():
            bar = self._latest_bar(position.symbol, as_of)
            mark = Decimal(bar.close) if bar else ZERO
            value = self._money(mark * position.quantity)
            views.append(PaperPositionView(
                position=position, mark_price=mark, market_value=value,
                unrealized_pnl=self._money((mark - position.average_cost) * position.quantity),
                portfolio_weight=value / equity if equity > ZERO else ZERO,
                last_event_time=self._aware(bar.event_time) if bar else None,
            ))
        orders = tuple(self._repository.list_orders(account.id or 0))
        return PaperTradingOverview(
            account=account, positions=tuple(views), orders=orders,
            fills=tuple(self._repository.list_fills(account.id or 0)), equity=self._money(equity),
            total_return=(equity / account.initial_cash - ONE) if account.initial_cash else ZERO,
            gross_exposure=(gross / equity) if equity > ZERO else ZERO,
            pending_count=sum(item.status == PaperOrderStatus.PENDING for item in orders),
            assumptions=self._assumptions,
        )
