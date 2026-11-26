"""The research pages' heavy views computed in a process of their own (2026-10-28; 使用者：研究的載入也太慢了吧).

The web serves the pool, the overview's dashboard, the frontier table, the forward accounts' summary, the status
table and the search scale from files this module writes (``instance/research/cache/ready/<code>/<view>.pkl``).
Computed in the web process (a thread), each round held the interpreter for a minute or more and every page waited on
it; here the web only reads the files. ``python -m quant_platform.dashboard.warm <research dir>`` — the web's warm-up
thread runs it every few minutes with no window (CREATE_NO_WINDOW), and scripts/deploy.ps1 runs it from the source
tree before it stops the services.

2026-11-26 (使用者：知道有問題了不修不然是要我幹嘛; the home page took 30.4 s after that morning's deploy): ``<code>`` is
the version of the code that wrote the view (``code_stamp``). A restarted web used to read nothing written before it
started — it might have been written by the version before it (2026-11-12: /research answered 500 on such a file) — so
the first clicks after every deploy computed the views themselves. A file in a version's own folder was written by that
version, whenever that was.
"""

from __future__ import annotations

import hashlib
import logging
import os
import pickle
import shutil
import sys
import time
from pathlib import Path

FOLDER = Path("cache") / "ready"
CODE: str | None = None          # the running code's stamp, worked out once; the preview sets the deployed site's


def code_stamp(package: Path | None = None) -> str:
    """A hash of the package's Python files (their names and bytes): the same for the source tree and for the package
    installed from it, another as soon as any of them differs."""
    root = package or Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for name, file in sorted((item.relative_to(root).as_posix(), item) for item in root.rglob("*.py")):
        digest.update(name.encode("utf-8") + b"\0" + file.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def current_code() -> str:
    global CODE
    if CODE is None:
        CODE = code_stamp()
    return CODE


def _written(folder: Path) -> float:
    """When a version's folder was last written to."""
    return max((item.stat().st_mtime for item in folder.glob("*.pkl")), default=0.0)


def newest_code(research_dir: str | Path) -> str | None:
    """The version whose views were written last — what the deployed site serves (scripts/preview-site.py reads them)."""
    base = Path(research_dir) / FOLDER
    folders = [item for item in base.iterdir() if item.is_dir()] if base.is_dir() else []
    return max(folders, key=_written).name if folders else None


def path_of(research_dir: str | Path, name: str) -> Path:
    return Path(research_dir) / FOLDER / current_code() / ("".join(ch if ch.isalnum() else "-" for ch in name) + ".pkl")


def write(research_dir: str | Path, name: str, value: object) -> Path:
    path = path_of(research_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    with open(partial, "wb") as handle:
        pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(partial, path)
    return path


def compute_all(research_dir: str | Path) -> dict[str, str]:
    """Every view the pages keep ready, each written as soon as it is computed; name -> "ok" or the error."""
    from quant_platform.dashboard.v2 import ready_views

    done = {}
    for name, compute in ready_views(Path(research_dir)).items():
        try:
            write(research_dir, name, compute())
            done[name] = "ok"
        except Exception as exc:                        # noqa: BLE001 - one view failing leaves the others ready
            logging.getLogger(__name__).exception("warm view %s failed", name)
            done[name] = str(exc)[:200]
    return done


def prepared(research_dir: str | Path) -> bool:
    """Whether every view of this version of the code is there and young enough to be read (scripts/deploy.ps1 asks the
    installed package before it starts the site: a source file another session changed after the views were computed
    makes it another version)."""
    from quant_platform.dashboard.v2 import READY_FOR, ready_views

    files = [path_of(research_dir, name) for name in ready_views(Path(research_dir))]
    return all(file.is_file() and time.time() - file.stat().st_mtime < READY_FOR for file in files)


def prune(research_dir: str | Path) -> None:
    """Keep this version's folder and, of the others, the one written to last (during a deploy that is the site still
    serving); older versions' folders go, and so do the files kept without a version folder before 2026-11-26."""
    base = Path(research_dir) / FOLDER
    if not base.is_dir():
        return
    others = sorted((item for item in base.iterdir() if item.is_dir() and item.name != current_code()), key=_written, reverse=True)
    for old in others[1:]:
        shutil.rmtree(old, ignore_errors=True)
    for flat in base.glob("*.pkl"):
        if time.time() - flat.stat().st_mtime > 24 * 3600:
            flat.unlink(missing_ok=True)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from quant_platform.research import stock_forward

    stock_forward.SHARE_PARSED = True     # 2026-10-30: this process only reads; each record file parsed once
    from quant_platform.research import pool

    pool.SHARE_VIEWS = True               # and the round's views share one pool
    import gc

    # one round, then the process ends: the parsed records stay alive the whole round and every automatic collection
    # walked them again (a round took 61 s of CPU, 32 s without; the views are the same, build/check_warm_same.py)
    gc.disable()
    research = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("instance") / "research"
    current_code()                        # stamped before the round: the files named are the ones this process runs
    print(compute_all(research), flush=True)
    prune(research)


if __name__ == "__main__":
    main()
