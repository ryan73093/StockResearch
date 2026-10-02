"""Append-only trial registry (S4-W01).

Every evaluation of a strategy spec is one trial, including failures, so
multiple-testing corrections can use the true number of attempts
(REQUIREMENTS §7). Records form a hash chain: each carries the previous
record's hash and its own hash over its content, so an edited or deleted
line breaks ``verify()``. Registering the same inputs again returns the
existing record instead of counting a new trial.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
GENESIS = "0" * 64


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class TrialRecord:
    trial_id: int
    kind: str
    period: str
    spec_hash: str
    spec_name: str
    input_hash: str
    data_fingerprint: str
    metrics: dict[str, object]
    report_file: str
    created_at: str
    prev_hash: str
    record_hash: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.period, self.input_hash)


def current_basis(
    records: list[TrialRecord], period: str, kind: str = "candidate", fingerprint: str | None = None
) -> tuple[list[TrialRecord], list[TrialRecord]]:
    """Split one period's trials into those on the current data basis and older ones.

    All trials of a period see the same data (the whole catalog up to the
    period's end), so they share one fingerprint until the history is
    corrected or the catalog grows. The basis is ``fingerprint`` when given
    (computed from the dataset), else the newest trial's. Older trials stay in
    the registry; a rule that only ran on an older basis still counts as an
    attempt (``distinct_rules``), a rule re-run on a new basis counts once.
    """
    selected = [record for record in records if record.kind == kind and record.period == period]
    if not selected:
        return [], []
    basis = fingerprint or selected[-1].data_fingerprint
    return (
        [record for record in selected if record.data_fingerprint == basis],
        [record for record in selected if record.data_fingerprint != basis],
    )


def distinct_rules(records: list[TrialRecord]) -> int:
    """The number of attempts for the multiple-testing correction (owner's decision 2026-10-02):
    every different spec counts once, however many data bases or cost variants it ran on. A spec
    renamed or re-described has another hash and counts again (the conservative side)."""
    return len({record.spec_hash for record in records})


def _is_variant(record: TrialRecord) -> bool:
    return record.metrics.get("execution_lag") not in (None, 0) or record.metrics.get("cost_scale") not in (None, 1, 1.0)


def one_per_rule(records: list[TrialRecord]) -> list[TrialRecord]:
    """One record per spec: the main run (standard costs, no execution lag) before robustness
    variants, the earliest first. Only these carry a DSR and can become promotion candidates."""
    chosen: dict[str, TrialRecord] = {}
    for record in sorted(records, key=lambda item: (_is_variant(item), item.trial_id)):
        chosen.setdefault(record.spec_hash, record)
    return sorted(chosen.values(), key=lambda item: item.trial_id)


class TrialRegistry:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def records(self) -> list[TrialRecord]:
        if not self._path.is_file():
            return []
        return [
            TrialRecord(**json.loads(line))
            for line in self._path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]

    def find(self, kind: str, period: str, input_hash: str) -> TrialRecord | None:
        return next(
            (record for record in self.records() if record.key == (kind, period, input_hash)), None
        )

    def register(
        self,
        *,
        kind: str,
        period: str,
        spec_hash: str,
        spec_name: str,
        input_hash: str,
        data_fingerprint: str,
        metrics: dict[str, object],
        report_file: str = "",
    ) -> TrialRecord:
        existing = self.find(kind, period, input_hash)
        if existing is not None:
            return existing
        records = self.records()
        body = {
            "trial_id": len(records) + 1,
            "kind": kind,
            "period": period,
            "spec_hash": spec_hash,
            "spec_name": spec_name,
            "input_hash": input_hash,
            "data_fingerprint": data_fingerprint,
            "metrics": metrics,
            "report_file": report_file,
            "created_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
            "prev_hash": records[-1].record_hash if records else GENESIS,
        }
        record = TrialRecord(**body, record_hash=_digest(body))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({**body, "record_hash": record.record_hash}, ensure_ascii=False) + "\n")
        return record

    def count(self, kind: str | None = None, period: str | None = None) -> int:
        return sum(
            1 for record in self.records()
            if (kind is None or record.kind == kind) and (period is None or record.period == period)
        )

    def verify(self) -> list[str]:
        """Problems found in the chain; an empty list means intact."""
        problems = []
        previous = GENESIS
        for index, record in enumerate(self.records(), 1):
            body = {name: getattr(record, name) for name in TrialRecord.__slots__ if name != "record_hash"}
            if record.trial_id != index:
                problems.append(f"第 {index} 筆的編號是 {record.trial_id}")
            if record.prev_hash != previous:
                problems.append(f"第 {index} 筆的前一筆雜湊不符（紀錄被刪除或重排）")
            if _digest(body) != record.record_hash:
                problems.append(f"第 {index} 筆內容與雜湊不符（紀錄被修改）")
            previous = record.record_hash
        return problems
