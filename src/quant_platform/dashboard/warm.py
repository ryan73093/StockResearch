"""The research pages' heavy views computed in a process of their own (2026-10-28; 使用者：研究的載入也太慢了吧).

The web serves the pool, the overview's dashboard, the frontier table, the forward accounts' summary, the status
table and the search scale from files this module writes (``instance/research/cache/ready/<view>.pkl``). Computed in
the web process (a thread), each round held the interpreter for a minute or more and every page waited on it; here the
web only reads the files. ``python -m quant_platform.dashboard.warm <research dir>`` — the web's warm-up thread runs it
every few minutes with no window (CREATE_NO_WINDOW).
"""

from __future__ import annotations

import logging
import os
import pickle
import sys
from pathlib import Path

FOLDER = Path("cache") / "ready"


def path_of(research_dir: str | Path, name: str) -> Path:
    return Path(research_dir) / FOLDER / ("".join(ch if ch.isalnum() else "-" for ch in name) + ".pkl")


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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    research = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("instance") / "research"
    print(compute_all(research), flush=True)


if __name__ == "__main__":
    main()
