from __future__ import annotations

import hashlib
import json
import logging
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from quant_platform.domain.entities import ModelExperiment

logger = logging.getLogger(__name__)


class PredictionArchiveRepository(Protocol):
    PREDICTION_ARCHIVE_COLUMNS: tuple[str, ...]

    def list_runs(self, market: str | None = None) -> list[ModelExperiment]: ...

    def prediction_experiment_ids(self) -> list[int]: ...

    def iter_prediction_batches(
        self, experiment_id: int, batch_size: int = 100_000
    ) -> Iterator[list[tuple]]: ...

    def delete_predictions_for_experiment(self, experiment_id: int) -> int: ...


@dataclass(frozen=True, slots=True)
class ArchivedExperiment:
    experiment_id: int
    rows: int
    path: str
    sha256: str
    archived_at: str


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def active_registry_experiment_ids(entries: Iterable[object]) -> set[int]:
    """Experiment ids of model-registry entries that are not retired."""
    return {
        int(entry.experiment_id)
        for entry in entries
        if str(getattr(entry.status, "value", entry.status)).upper() != "RETIRED"
    }


def experiments_in_use(
    experiments: Iterable[ModelExperiment], registry_ids: Iterable[int] = ()
) -> set[int]:
    """Experiments whose predictions the running system still reads.

    - latest experiment per market, model and label (governance, challengers);
    - latest experiment with positive rank IC per group (after-hours replay);
    - best CANDIDATE per market and label (daily decision evidence);
    - experiments referenced by active model-registry entries.
    """
    keep = {int(value) for value in registry_ids}
    groups: dict[tuple[str, str, str], list[ModelExperiment]] = defaultdict(list)
    candidates: dict[tuple[str, str], list[ModelExperiment]] = defaultdict(list)
    for experiment in experiments:
        if experiment.id is None:
            continue
        groups[(experiment.market, experiment.model_name, experiment.label_name)].append(experiment)
        if experiment.promotion_gate == "CANDIDATE":
            candidates[(experiment.market, experiment.label_name)].append(experiment)
    for items in groups.values():
        keep.add(int(max(items, key=lambda item: _aware(item.computed_at)).id))
        positive = [item for item in items if (item.rank_ic or 0) > 0]
        if positive:
            keep.add(int(max(positive, key=lambda item: _aware(item.computed_at)).id))
    for items in candidates.values():
        best = max(
            items,
            key=lambda item: (
                item.rank_ic or -1.0, item.long_short_spread or -1.0, _aware(item.computed_at)
            ),
        )
        keep.add(int(best.id))
    return keep


class PredictionArchiveService:
    """Move superseded experiments' predictions from SQLite to verified Parquet files.

    Each experiment becomes one Parquet file. The file is read back and its row
    count must match the database before the rows are deleted; a JSONL manifest
    records path, row count and SHA-256 for every archived experiment.
    """

    def __init__(
        self,
        repository: PredictionArchiveRepository,
        archive_dir: str | Path,
        registry_ids: Callable[[], Iterable[int]] | None = None,
    ) -> None:
        self._repository = repository
        self._archive_dir = Path(archive_dir)
        self._registry_ids = registry_ids or (lambda: ())

    @property
    def manifest_path(self) -> Path:
        return self._archive_dir / "manifest.jsonl"

    def plan(self) -> tuple[set[int], list[int]]:
        keep = experiments_in_use(self._repository.list_runs(), self._registry_ids())
        archive = [
            experiment_id for experiment_id in self._repository.prediction_experiment_ids()
            if experiment_id not in keep
        ]
        return keep, archive

    def _schema(self):
        import pyarrow as pa

        timestamp = pa.timestamp("us", tz="UTC")
        types = {
            "id": pa.int64(), "experiment_id": pa.int64(), "market": pa.string(),
            "symbol": pa.string(), "model_name": pa.string(), "label_name": pa.string(),
            "horizon": pa.int32(), "event_time": timestamp, "available_time": timestamp,
            "predicted_value": pa.float64(), "rank_score": pa.float64(), "computed_at": timestamp,
        }
        return pa.schema([
            (name, types[name]) for name in self._repository.PREDICTION_ARCHIVE_COLUMNS
        ])

    def archive_experiment(self, experiment_id: int) -> ArchivedExperiment:
        import pyarrow as pa
        import pyarrow.parquet as pq

        schema = self._schema()
        columns = self._repository.PREDICTION_ARCHIVE_COLUMNS
        time_columns = {"event_time", "available_time", "computed_at"}
        self._archive_dir.mkdir(parents=True, exist_ok=True)
        target = self._archive_dir / f"experiment_{experiment_id}.parquet"
        temporary = target.with_suffix(".parquet.tmp")
        rows = 0
        with pq.ParquetWriter(temporary, schema, compression="zstd") as writer:
            for batch in self._repository.iter_prediction_batches(experiment_id):
                if not batch:
                    continue
                data = {
                    name: [
                        _aware(row[index]) if name in time_columns else row[index]
                        for row in batch
                    ]
                    for index, name in enumerate(columns)
                }
                writer.write_table(pa.table(data, schema=schema))
                rows += len(batch)
        written = pq.ParquetFile(temporary).metadata.num_rows
        if written != rows:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"experiment {experiment_id}: wrote {written} rows, expected {rows}")
        temporary.replace(target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        deleted = self._repository.delete_predictions_for_experiment(experiment_id)
        if deleted != rows:
            raise RuntimeError(
                f"experiment {experiment_id}: archived {rows} rows but deleted {deleted}; "
                f"archive kept at {target}"
            )
        record = ArchivedExperiment(
            experiment_id=experiment_id, rows=rows, path=str(target), sha256=digest,
            archived_at=datetime.now(UTC).isoformat(),
        )
        with self.manifest_path.open("a", encoding="utf-8") as manifest:
            manifest.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        return record

    def apply(self, limit: int | None = None) -> list[ArchivedExperiment]:
        _, archive = self.plan()
        archived = []
        for experiment_id in archive[:limit] if limit else archive:
            archived.append(self.archive_experiment(experiment_id))
            logger.info(
                "Archived predictions of experiment %s (%s rows)",
                experiment_id, archived[-1].rows,
            )
        return archived
