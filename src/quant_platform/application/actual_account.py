"""The investor's real account and its DCA shadow (S5-W03, S5-W04).

The investor records deposits, withdrawals, cash dividends and the fills
they got from their broker. Holdings use the average-cost method; cash is
deposits − withdrawals + dividends − buys (with fees) + sells (after fees
and tax). The DCA shadow account invests every deposit into the benchmark
ETF on the same day with the research engine's cost and fill model, so the
two can be compared under identical cash flows (REQUIREMENTS §4, §8).
Entries are voided with a reason, never deleted.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from quant_platform.domain.entities import ActualCashFlow, ActualTrade
from quant_platform.research.costs import BROKERS, CostModel, broker_costs
from quant_platform.research.metrics import max_drawdown, unit_values, xirr

logger = logging.getLogger(__name__)
SYMBOL = re.compile(r"^[0-9A-Z]{4,6}$")
FLOW_KINDS = {"deposit": "入金", "withdrawal": "出金", "dividend": "現金股利"}


class ActualAccountError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Holding:
    symbol: str
    shares: int
    average_cost: float
    price: float | None
    value: float
    unrealized: float | None
    weight: float = 0.0


@dataclass(frozen=True)
class ActualAccountOverview:
    cash: float
    holdings: list[Holding]
    market_value: float
    total_value: float
    net_deposits: float
    dividends: float
    fees: int
    taxes: int
    realized: float
    xirr: float | None
    shadow: dict[str, object] | None
    flows: list[ActualCashFlow]
    trades: list[ActualTrade]
    notes: list[str] = field(default_factory=list)
    max_drawdown: float | None = None  # unit-value drawdown of the real account
    monthly: list[dict[str, object]] = field(default_factory=list)  # month-end real vs shadow
    history: dict[str, object] | None = None  # session closes: {"days", "values"}
    shadow_series: dict[date, float] = field(default_factory=dict)  # DCA shadow by session


def _day(text: str) -> date:
    try:
        return date.fromisoformat(str(text).strip())
    except ValueError as exc:
        raise ActualAccountError("日期格式是 YYYY-MM-DD") from exc


def _decimal(text: str, label: str) -> Decimal:
    try:
        return Decimal(str(text).replace(",", "").strip())
    except InvalidOperation as exc:
        raise ActualAccountError(f"{label}必須是數字") from exc


def tax_kind(symbol: str) -> str:
    from quant_platform.research.history.catalog import SERIES_BY_KEY

    if symbol in SERIES_BY_KEY:
        return SERIES_BY_KEY[symbol].tax_kind
    if symbol.endswith("B") and symbol.startswith("00"):
        return "bond_etf"
    return "stock_etf" if symbol.startswith("00") else "stock"


class ActualAccountService:
    def __init__(
        self,
        repository,
        price_lookup: Callable[[list[str]], dict[str, float]],
        research_dir: str | Path | None = None,
        costs: CostModel | None = None,
        today: Callable[[], date] | None = None,
        default_broker: Callable[[], str] | None = None,
        bar_history: Callable[[str], list[tuple[date, float]]] | None = None,
    ) -> None:
        self._repository = repository
        self._prices = price_lookup
        # Daily closes by Yahoo symbol ("0050.TW"), oldest first: the account's
        # day-by-day value, drawdown and month-end comparison (S6-W02).
        self._bar_history = bar_history
        self._research_dir = Path(research_dir) if research_dir else None
        self._fixed_costs = costs
        self._today = today or date.today
        # The plan's broker: default for new entries and for the shadow's fees.
        self._default_broker = default_broker or (lambda: "conservative")

    def default_broker(self) -> str:
        return self._default_broker()

    def _costs(self, broker: str | None = None) -> CostModel:
        return self._fixed_costs or broker_costs(broker or self._default_broker())

    @staticmethod
    def _broker(form: dict[str, str]) -> str:
        broker = str(form.get("broker", "")).strip()
        if broker and broker not in BROKERS:
            raise ActualAccountError("請選擇券商")
        return broker

    # --- recording --------------------------------------------------------
    def record_cash_flow(self, form: dict[str, str]) -> ActualCashFlow:
        kind = str(form.get("kind", "")).strip()
        if kind not in FLOW_KINDS:
            raise ActualAccountError("請選擇入金、出金或現金股利")
        day = _day(form.get("day", ""))
        if day > self._today():
            raise ActualAccountError("日期不能在未來")
        amount = _decimal(form.get("amount", ""), "金額")
        if amount <= 0:
            raise ActualAccountError("金額必須大於 0")
        symbol = str(form.get("symbol", "")).strip().upper()
        if symbol and not SYMBOL.match(symbol):
            raise ActualAccountError("代號格式不正確（例如 0050、00679B）")
        if kind == "withdrawal" and float(amount) > self.overview(include_shadow=False).cash + 1e-6:
            raise ActualAccountError("出金金額超過目前現金")
        return self._repository.add_cash_flow(
            ActualCashFlow(None, day, kind, amount.quantize(Decimal("0.01")), symbol,
                           str(form.get("note", "")).strip()[:500], broker=self._broker(form)),
            datetime.now(UTC),
        )

    def record_trade(self, form: dict[str, str]) -> ActualTrade:
        day = _day(form.get("day", ""))
        if day > self._today():
            raise ActualAccountError("日期不能在未來")
        symbol = str(form.get("symbol", "")).strip().upper()
        if not SYMBOL.match(symbol):
            raise ActualAccountError("代號格式不正確（例如 0050、00679B）")
        side = str(form.get("side", "")).strip().upper()
        if side not in {"BUY", "SELL"}:
            raise ActualAccountError("請選擇買進或賣出")
        try:
            shares = int(str(form.get("shares", "")).replace(",", "").strip())
        except ValueError as exc:
            raise ActualAccountError("股數必須是整數") from exc
        if not 1 <= shares <= 100_000_000:
            raise ActualAccountError("股數必須大於 0")
        price = _decimal(form.get("price", ""), "成交價")
        if price <= 0:
            raise ActualAccountError("成交價必須大於 0")
        amount = float(price) * shares
        broker = self._broker(form)
        costs = self._costs(broker)
        fee = self._integer_or_default(form.get("fee"), costs.fee(amount), "手續費")
        tax = self._integer_or_default(form.get("tax"), costs.tax(amount, tax_kind(symbol), side), "證交稅")
        trades = self._repository.list_trades()
        held = sum(
            (trade.shares if trade.side == "BUY" else -trade.shares)
            for trade in trades if trade.symbol == symbol and trade.day <= day
        )
        if side == "SELL" and shares > held:
            raise ActualAccountError(f"{day} 當時只有 {held:,} 股 {symbol}，無法賣出 {shares:,} 股")
        return self._repository.add_trade(
            ActualTrade(None, day, symbol, side, shares, price, fee, tax, str(form.get("note", "")).strip()[:500],
                        broker=broker),
            datetime.now(UTC),
        )

    @staticmethod
    def _integer_or_default(text: str | None, default: int, label: str) -> int:
        value = str(text or "").replace(",", "").strip()
        if not value:
            return default
        try:
            number = int(value)
        except ValueError as exc:
            raise ActualAccountError(f"{label}必須是整數（元）") from exc
        if number < 0:
            raise ActualAccountError(f"{label}不能是負數")
        return number

    def void(self, kind: str, entry_id: int, reason: str) -> bool:
        if kind not in {"trade", "cash"}:
            raise ActualAccountError("未知的紀錄類型")
        if not reason.strip():
            raise ActualAccountError("作廢需要填寫原因")
        return self._repository.void(kind, entry_id, reason.strip()[:500], datetime.now(UTC))

    # --- overview -----------------------------------------------------------
    def overview(self, include_shadow: bool = True) -> ActualAccountOverview:
        """Books as of today; ``include_shadow=False`` skips the DCA replay."""
        flows = self._repository.list_cash_flows()
        trades = self._repository.list_trades()
        cash = 0.0
        deposits = withdrawals = dividends = 0.0
        for flow in flows:
            amount = float(flow.amount)
            if flow.kind == "deposit":
                cash += amount
                deposits += amount
            elif flow.kind == "withdrawal":
                cash -= amount
                withdrawals += amount
            else:
                cash += amount
                dividends += amount
        positions: dict[str, list[float]] = {}  # symbol -> [shares, cost]
        realized = 0.0
        for trade in sorted(trades, key=lambda item: (item.day, item.id or 0)):
            shares, cost = positions.setdefault(trade.symbol, [0, 0.0])
            gross = trade.shares * float(trade.price)
            if trade.side == "BUY":
                cash -= gross + trade.fee
                positions[trade.symbol] = [shares + trade.shares, cost + gross + trade.fee]
            else:
                average = cost / shares if shares else 0.0
                cash += gross - trade.fee - trade.tax
                realized += gross - trade.fee - trade.tax - average * trade.shares
                positions[trade.symbol] = [shares - trade.shares, cost - average * trade.shares]
        held = {symbol: values for symbol, values in positions.items() if values[0] > 0}
        quotes = self._prices([f"{symbol}.{suffix}" for symbol in held for suffix in ("TW", "TWO")])
        holdings = []
        for symbol, (shares, cost) in sorted(held.items()):
            price = quotes.get(f"{symbol}.TW", quotes.get(f"{symbol}.TWO"))
            value = shares * price if price is not None else cost
            holdings.append(Holding(
                symbol, int(shares), cost / shares, price, value,
                None if price is None else value - cost,
            ))
        market_value = sum(item.value for item in holdings)
        total = cash + market_value
        holdings = [_with_weight(item, total) for item in holdings]
        today = self._today()
        external = [(flow.day, -float(flow.amount)) for flow in flows if flow.kind == "deposit"]
        external += [(flow.day, float(flow.amount)) for flow in flows if flow.kind == "withdrawal"]
        rate = xirr(external + [(today, total)]) if external and total > 0 else None
        notes = [] if not any(item.price is None for item in holdings) else [
            "部分持股沒有最新收盤價，暫以成本估值。"
        ]
        shadow, shadow_series = (None, {})
        history = None
        if include_shadow:
            shadow, shadow_series = self._shadow(flows, total, rate, notes)
            history = self._history(flows, trades)
        return ActualAccountOverview(
            cash=cash, holdings=holdings, market_value=market_value, total_value=total,
            net_deposits=deposits - withdrawals, dividends=dividends,
            fees=sum(trade.fee for trade in trades), taxes=sum(trade.tax for trade in trades),
            realized=realized, xirr=rate,
            shadow=shadow,
            flows=flows, trades=trades, notes=notes,
            max_drawdown=history["max_drawdown"] if history else None,
            monthly=_month_ends(history, shadow_series) if history else [],
            history=history, shadow_series=shadow_series,
        )

    def _closes(self, symbol: str) -> dict[date, float]:
        for suffix in ("TW", "TWO"):
            rows = self._bar_history(f"{symbol}.{suffix}")
            if rows:
                return dict(rows)  # one close per day; a later source for the same day wins
        return {}

    def _history(self, flows, trades) -> dict[str, object] | None:
        """The account's value at every session close since its first entry (S6-W02)."""
        if self._bar_history is None or not flows:
            return None
        first = min([flow.day for flow in flows] + [trade.day for trade in trades])
        sessions = sorted({day for day, _ in self._bar_history("0050.TW") if first <= day <= self._today()})
        if not sessions:
            return None
        closes = {symbol: self._closes(symbol) for symbol in {trade.symbol for trade in trades}}
        ordered_flows = sorted(flows, key=lambda item: (item.day, item.id or 0))
        ordered_trades = sorted(trades, key=lambda item: (item.day, item.id or 0))
        cash, shares, last_price = 0.0, {}, {}
        values, external, next_flow, next_trade = [], [], 0, 0
        for session in sessions:
            arrived = 0.0  # entries dated on a closed day count on the next session
            while next_flow < len(ordered_flows) and ordered_flows[next_flow].day <= session:
                flow = ordered_flows[next_flow]
                amount = float(flow.amount)
                cash += -amount if flow.kind == "withdrawal" else amount
                if flow.kind in {"deposit", "withdrawal"}:
                    arrived += -amount if flow.kind == "withdrawal" else amount
                next_flow += 1
            while next_trade < len(ordered_trades) and ordered_trades[next_trade].day <= session:
                trade = ordered_trades[next_trade]
                gross = trade.shares * float(trade.price)
                if trade.side == "BUY":
                    cash -= gross + trade.fee
                    shares[trade.symbol] = shares.get(trade.symbol, 0) + trade.shares
                else:
                    cash += gross - trade.fee - trade.tax
                    shares[trade.symbol] = shares.get(trade.symbol, 0) - trade.shares
                last_price[trade.symbol] = float(trade.price)
                next_trade += 1
            for symbol in shares:
                if session in closes.get(symbol, {}):
                    last_price[symbol] = closes[symbol][session]
            values.append(cash + sum(count * last_price.get(symbol, 0.0) for symbol, count in shares.items()))
            external.append(arrived)
        drawdown, _peak, _trough = max_drawdown(unit_values(values, external))
        return {"days": sessions, "values": values, "max_drawdown": drawdown}

    def _shadow(self, flows, total, rate, notes) -> tuple[dict[str, object] | None, dict[date, float]]:
        deposits = [(flow.day, float(flow.amount)) for flow in flows if flow.kind == "deposit"]
        if not deposits or self._research_dir is None:
            return None, {}
        if any(flow.kind == "withdrawal" for flow in flows):
            notes.append("影子帳戶只模擬入金；有出金時兩者的比較需另行解讀。")
        try:
            from quant_platform.research.cashflow import ContributionPlan
            from quant_platform.research.engine import simulate
            from quant_platform.research.market import load_market
            from quant_platform.research.spec import BASELINES

            benchmark = BASELINES["benchmark_dca"]
            market = load_market(benchmark.assets, self._research_dir / "history")
            first = min(day for day, _ in deposits)
            result = simulate(
                benchmark, market, ContributionPlan(), self._costs(), start=first,
                contributions=deposits,
            )
        except FileNotFoundError:
            logger.info("DCA shadow skipped: research history not built yet")
            notes.append("長歷史研究資料尚未建立；建立後會自動計算定期定額影子帳戶。")
            return None, {}
        except ValueError as exc:
            logger.warning("DCA shadow could not be computed: %s", exc)
            notes.append("定期定額影子帳戶暫時無法計算（研究資料尚未涵蓋入金日期）。")
            return None, {}
        return {
            "name": benchmark.name,
            "as_of": result.end.isoformat(),
            "value": result.final_value,
            "xirr": result.xirr,
            "difference": total - result.final_value,
            "xirr_difference": None if rate is None or result.xirr is None else rate - result.xirr,
            "max_drawdown": result.max_drawdown,
            "trades": len(result.trades),
        }, dict(zip(result.days, result.values))


STRESS_DROPS = (0.20, 0.30, 0.55)  # 0.55 ≈ 0050 in 2008


def stress_scenarios(overview: ActualAccountOverview, tolerance: float | None = None) -> list[dict[str, object]]:
    """Account value if Taiwan equity ETFs fell 20%, 30% or 55% (S7-W06).

    Simplification: stock ETFs fall by the full amount; bond ETFs and cash do
    not move. ``tolerance`` is the plan's acceptable drawdown.
    """
    if overview.total_value <= 0:
        return []
    equity = sum(item.value for item in overview.holdings if tax_kind(item.symbol) != "bond_etf")
    rows = []
    for drop in STRESS_DROPS:
        loss = equity * drop
        share = loss / overview.total_value
        rows.append({
            "drop": drop, "loss": loss, "after": overview.total_value - loss, "share": share,
            "over_tolerance": tolerance is not None and share > tolerance,
        })
    return rows


def compare_on(overview: ActualAccountOverview, day: date) -> dict[str, object]:
    """Both accounts at the last session on or before ``day`` (S5-W04).

    Returns ``{"error": ...}`` when the day is before the first entry.
    """
    history = overview.history or {"days": [], "values": []}
    sessions = [(session, value) for session, value in zip(history["days"], history["values"]) if session <= day]
    if not sessions:
        first = min([flow.day for flow in overview.flows] + [trade.day for trade in overview.trades], default=None)
        return {"error": f"{day} 還沒有任何紀錄" + (f"（第一筆是 {first}）" if first else "")}
    session, value = sessions[-1]
    shadow_days = [item for item in overview.shadow_series if item <= session]
    shadow_day = max(shadow_days) if shadow_days else None  # research history can lag a day
    shadow = overview.shadow_series[shadow_day] if shadow_day else None
    invested = sum(
        float(flow.amount) if flow.kind == "deposit" else -float(flow.amount)
        for flow in overview.flows if flow.kind in {"deposit", "withdrawal"} and flow.day <= session
    )
    return {
        "requested": day, "session": session, "actual": value, "shadow": shadow, "shadow_day": shadow_day,
        "invested": invested, "difference": None if shadow is None else value - shadow,
    }


def _month_ends(history: dict[str, object], shadow: dict[date, float], months: int = 12) -> list[dict[str, object]]:
    """The last session of each month: real account against the DCA shadow, newest first."""
    last: dict[str, tuple[date, float]] = {}
    for day, value in zip(history["days"], history["values"]):
        last[f"{day:%Y-%m}"] = (day, value)
    rows = []
    for month, (day, value) in sorted(last.items(), reverse=True)[:months]:
        shadow_days = [item for item in shadow if item <= day]
        shadow_value = shadow[max(shadow_days)] if shadow_days else None
        rows.append({
            "month": month, "day": day, "actual": value, "shadow": shadow_value,
            "difference": None if shadow_value is None else value - shadow_value,
        })
    return rows


def _with_weight(item: Holding, total: float) -> Holding:
    return Holding(
        item.symbol, item.shares, item.average_cost, item.price, item.value, item.unrealized,
        item.value / total if total else 0.0,
    )
