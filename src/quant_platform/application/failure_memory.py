from __future__ import annotations

import json
from datetime import UTC, datetime

from quant_platform.application.ports import ResearchFailureCaseRepository
from quant_platform.domain.entities import ResearchFailureCase


class ResearchFailureMemoryService:
    """Persistent post-mortem memory for invalid or failed research attempts."""

    def __init__(self, repository: ResearchFailureCaseRepository) -> None:
        self._repository = repository

    def record(
        self,
        *,
        case_key: str,
        module: str,
        title: str,
        severity: str,
        evidence: dict[str, object],
        cause: str,
        correction: str,
        status: str = "open",
        verification: str | None = None,
        occurred_at: datetime | None = None,
    ) -> ResearchFailureCase:
        timestamp = occurred_at or datetime.now(UTC)
        return self._repository.record(ResearchFailureCase(
            id=None,
            case_key=case_key,
            module=module,
            title=title,
            severity=severity,
            status=status,
            evidence_json=json.dumps(evidence, ensure_ascii=False, sort_keys=True),
            cause=cause,
            correction=correction,
            verification=verification,
            repeat_count=1,
            first_occurred_at=timestamp,
            last_occurred_at=timestamp,
        ))

    def list_recent(self, limit: int = 50) -> tuple[ResearchFailureCase, ...]:
        return tuple(self._repository.list_recent(limit))

    def resolve(self, case_key: str, verification: str) -> ResearchFailureCase:
        """Keep the original failure while marking the correction as verified."""
        return self._repository.resolve(case_key, verification)
