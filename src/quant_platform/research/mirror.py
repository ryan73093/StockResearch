"""2026-10-19 (使用者：評估這個專案可能會失敗的原因，給予排除): a second copy of what cannot be rebuilt, on another disk.

The nightly database backup (03:00, seven copies) sits on the same disk as the database, and nothing copied the
research folder at all: the trial registry and its reports (every trial ever run, the chain the statistics rest on),
the forward accounts' records, the monthly selections, the trained models, the long price history. A failing disk
would take all of it. Each night, to ``RESEARCH_MIRROR_DIR`` (another physical disk):

- research/: every file new or changed since the last run (by size and time) — never deleting there, so a file lost
  or damaged here is still there; the search caches (scans), logs, job records and pipeline records are left out
  (they are rebuilt or only describe runs);
- db/: the newest nightly database backup, seven kept;
- snapshots/<date>.zip: the registry, the forward records and the monthly selections as they were that night, 30 kept
  (a damaged registry copied over the mirror can be taken back from a day before).
"""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

SKIPPED = ("scans", "logs", "jobs", "pipelines")
SNAPSHOT = ("trials.jsonl", "forward", "rolling")
KEEP_DATABASES = 7
KEEP_SNAPSHOTS = 30


def _changed(source: Path, target: Path) -> bool:
    if not target.is_file():
        return True
    here, there = source.stat(), target.stat()
    return here.st_size != there.st_size or int(here.st_mtime) > int(there.st_mtime)


def mirror(instance: str | Path, target: str | Path, now: datetime | None = None) -> dict[str, object]:
    """Copy ``instance``/research (but SKIPPED) and the newest database backup to ``target``; a dated snapshot of the
    registry and the forward records. Returns what was copied."""
    instance, target = Path(instance), Path(target)
    now = now or datetime.now()
    research = instance / "research"
    copied = files = size = 0
    for source in research.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(research)
        if relative.parts[0] in SKIPPED or source.suffix in (".partial", ".tmp"):
            continue
        files += 1
        destination = target / "research" / relative
        if _changed(source, destination):
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied += 1
            size += source.stat().st_size
    databases = sorted((instance / "backups" / "daily").glob("*.db"))
    database = None
    if databases:
        newest = databases[-1]
        destination = target / "db" / newest.name
        if not destination.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(newest, destination)
        database = newest.name
        for old in sorted((target / "db").glob("*.db"))[:-KEEP_DATABASES]:
            old.unlink()
    snapshots = target / "snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    archive = snapshots / f"{now:%Y%m%d}.zip"
    partial = archive.with_suffix(".partial")
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name in SNAPSHOT:
            path = research / name
            for item in ([path] if path.is_file() else sorted(path.rglob("*")) if path.is_dir() else []):
                if item.is_file():
                    bundle.write(item, item.relative_to(research).as_posix())
    os.replace(partial, archive)
    for old in sorted(snapshots.glob("*.zip"))[:-KEEP_SNAPSHOTS]:
        old.unlink()
    result = {"at": now.isoformat(timespec="seconds"), "target": str(target), "files": files, "copied": copied,
              "copied_bytes": size, "database": database, "snapshot": archive.name}
    (target / "mirror.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def status(target: str | Path | None) -> dict[str, object] | None:
    """The last run's record (the system page), or None when there is no mirror."""
    if not target:
        return None
    path = Path(target) / "mirror.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"target": str(target), "at": None}
