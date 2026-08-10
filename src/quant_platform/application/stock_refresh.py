from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from threading import Lock, Thread
from typing import Callable


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StockRefreshState:
    symbol: str
    state: str
    stage: str
    percent: int
    error: str | None
    updated_at: datetime


class StockResearchRefreshCoordinator:
    """Runs one on-demand research refresh at a time outside the web request."""

    def __init__(
        self,
        refresh: Callable[[str, Callable[[str, int], None]], None],
    ) -> None:
        self._refresh = refresh
        self._state_lock = Lock()
        self._work_lock = Lock()
        self._states: dict[str, StockRefreshState] = {}

    def ensure(self, symbol: str) -> StockRefreshState:
        normalized = symbol.strip().upper()
        now = datetime.now(UTC)
        with self._state_lock:
            current = self._states.get(normalized)
            if current is not None and current.state in {"queued", "running"}:
                return current
            if current is not None and current.state == "succeeded":
                return current
            if current is not None and current.state == "failed":
                return current
            queued = StockRefreshState(
                normalized, "queued", "等待執行", 0, None, now
            )
            self._states[normalized] = queued
        Thread(
            target=self._run,
            args=(normalized,),
            name=f"stock-refresh-{normalized}",
            daemon=True,
        ).start()
        return queued

    def status(self, symbol: str) -> StockRefreshState | None:
        with self._state_lock:
            return self._states.get(symbol.strip().upper())

    def _run(self, symbol: str) -> None:
        with self._work_lock:
            self._set(symbol, "running", "準備中", 1)
            try:
                self._refresh(
                    symbol,
                    lambda stage, percent: self._set(
                        symbol, "running", stage, percent
                    ),
                )
            except Exception as exc:
                logger.exception("On-demand stock research refresh failed for %s", symbol)
                current = self.status(symbol)
                self._set(
                    symbol,
                    "failed",
                    "失敗",
                    current.percent if current is not None else 0,
                    str(exc),
                )
            else:
                self._set(symbol, "succeeded", "完成", 100)

    def _set(
        self,
        symbol: str,
        state: str,
        stage: str,
        percent: int,
        error: str | None = None,
    ) -> None:
        with self._state_lock:
            self._states[symbol] = StockRefreshState(
                symbol,
                state,
                stage,
                max(0, min(int(percent), 100)),
                error,
                datetime.now(UTC),
            )
