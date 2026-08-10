from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
from threading import Lock
from typing import Protocol


@dataclass(frozen=True, slots=True)
class BrokerCapability:
    broker_name: str
    environment: str
    connected: bool
    live_trading_enabled: bool
    reason: str


@dataclass(frozen=True, slots=True)
class BrokerOrderRequest:
    client_order_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: Decimal | None


@dataclass(frozen=True, slots=True)
class BrokerOrderReceipt:
    broker_order_id: str
    client_order_id: str
    status: str
    message: str


class BrokerAdapter(Protocol):
    def capability(self) -> BrokerCapability: ...

    def submit(self, request: BrokerOrderRequest) -> BrokerOrderReceipt: ...

    def cancel(self, broker_order_id: str) -> BrokerOrderReceipt: ...


class DisabledBrokerAdapter:
    """Hard safety boundary until a sandbox broker is explicitly configured."""

    def capability(self) -> BrokerCapability:
        return BrokerCapability(
            broker_name="未連接券商", environment="disabled", connected=False,
            live_trading_enabled=False,
            reason="券商 Adapter 尚未設定；影子交易不會送出任何外部委託。",
        )

    def submit(self, request: BrokerOrderRequest) -> BrokerOrderReceipt:
        raise PermissionError(self.capability().reason)

    def cancel(self, broker_order_id: str) -> BrokerOrderReceipt:
        raise PermissionError(self.capability().reason)


class LocalSandboxBrokerAdapter:
    """In-process broker simulator. It never performs network or live-trading I/O."""

    def __init__(self) -> None:
        self._receipts: dict[str, BrokerOrderReceipt] = {}
        self._by_broker_id: dict[str, str] = {}
        self._lock = Lock()

    def capability(self) -> BrokerCapability:
        return BrokerCapability(
            broker_name="本機券商沙盒", environment="sandbox", connected=True,
            live_trading_enabled=False,
            reason="僅保存沙盒受理回條；不連網、不持有券商憑證、不會送出真實委託。",
        )

    def submit(self, request: BrokerOrderRequest) -> BrokerOrderReceipt:
        if request.quantity <= 0:
            raise ValueError("沙盒委託股數必須大於 0 股")
        if request.side not in {"BUY", "SELL"}:
            raise ValueError("沙盒委託方向只接受 BUY 或 SELL")
        if request.order_type not in {"MARKET", "LIMIT"}:
            raise ValueError("沙盒委託類型只接受 MARKET 或 LIMIT")
        if request.order_type == "LIMIT" and request.limit_price is None:
            raise ValueError("限價沙盒委託必須提供元／股限價")
        with self._lock:
            existing = self._receipts.get(request.client_order_id)
            if existing:
                return existing
            digest = hashlib.sha256(request.client_order_id.encode("utf-8")).hexdigest()[:16]
            receipt = BrokerOrderReceipt(
                broker_order_id=f"sandbox-{digest}",
                client_order_id=request.client_order_id,
                status="accepted",
                message="本機沙盒已受理；未連接任何外部券商。",
            )
            self._receipts[request.client_order_id] = receipt
            self._by_broker_id[receipt.broker_order_id] = request.client_order_id
            return receipt

    def cancel(self, broker_order_id: str) -> BrokerOrderReceipt:
        with self._lock:
            client_id = self._by_broker_id.get(broker_order_id)
            if client_id is None:
                raise LookupError("找不到沙盒委託")
            current = self._receipts[client_id]
            cancelled = BrokerOrderReceipt(
                broker_order_id=current.broker_order_id,
                client_order_id=current.client_order_id,
                status="cancelled",
                message="本機沙盒委託已取消；沒有任何外部委託需要撤單。",
            )
            self._receipts[client_id] = cancelled
            return cancelled
