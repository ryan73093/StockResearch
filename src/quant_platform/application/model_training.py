from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import Protocol


class ModelPipeline(Protocol):
    def run(self, market: str) -> object: ...


@dataclass(frozen=True, slots=True)
class ModelTrainingStatus:
    market: str
    state: str
    message: str
    started_at: str | None
    completed_at: str | None


class ModelTrainingCoordinator:
    """Runs one model-research job at a time without blocking the web request."""

    def __init__(self, pipeline: ModelPipeline) -> None:
        self._pipeline = pipeline
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="model-research")
        self._lock = Lock()
        self._statuses: dict[str, ModelTrainingStatus] = {}

    def start(self, market: str) -> bool:
        normalized = market.upper()
        if normalized not in {"TW", "US"}:
            raise ValueError("market must be TW or US")
        with self._lock:
            current = self._statuses.get(normalized)
            if current and current.state in {"queued", "running"}:
                return False
            now = datetime.now(UTC).isoformat()
            self._statuses[normalized] = ModelTrainingStatus(normalized, "queued", "已排入背景研究", now, None)
            self._executor.submit(self._execute, normalized, now)
            return True

    def get(self, market: str) -> dict[str, str | None]:
        normalized = market.upper()
        with self._lock:
            status = self._statuses.get(normalized) or ModelTrainingStatus(
                normalized, "idle", "尚未啟動", None, None
            )
            return asdict(status)

    def _execute(self, market: str, started_at: str) -> None:
        with self._lock:
            self._statuses[market] = ModelTrainingStatus(
                market, "running", "正在建立資料集、調參與樣本外驗證", started_at, None
            )
        try:
            result = self._pipeline.run(market)
            message = (
                f"完成 {getattr(result, 'experiment_count', 0)} 個模型；"
                f"調參 {getattr(result, 'automl_trials', 0)} 次；"
                f"復用 {getattr(result, 'reused', 0)} 個快照"
            )
            state = "succeeded"
        except Exception as exc:
            message = f"模型研究失敗：{exc}"
            state = "failed"
        with self._lock:
            self._statuses[market] = ModelTrainingStatus(
                market, state, message, started_at, datetime.now(UTC).isoformat()
            )
