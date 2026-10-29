"""2026-10-29 (使用者：你他媽當我電腦資源很多是不是；是你的程式太肥、架構太肥，還是真的這麼多；不要為了省效能最後又給我
錯的資料): the steps every research flow ends with recompute everything — the statistics over every trial, the month's
list, its holdout, every forward account and the board replaying all of them. From 10/28 night to 10/29 morning eight
flows ran the statistics eight times (1-1.5 hours each, three at once) and the board five times (up to 3.5 hours each),
each flow having added 10-60 rules.

Each of these steps now runs one at a time (the operating system's file lock, released with the process), and a run is
skipped only when another run of the same step **started after this one was asked for** and finished: that run read
everything there was when this one was asked for, so its result is what this one would have computed or newer. Nothing
is guessed about what a step reads — a step asked for after the last run started always runs. The flows waiting during
one run share the next run instead of each recomputing everything."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
VALUED = ("--broker", "--workers")       # flags with a value that do not change what the step computes

# the command line (without VALUED flags) → the shared step; any other flag (a date, a cutoff, another universe) is
# another computation and runs as it is
STEPS: dict[tuple[str, ...], str] = {
    ("stats", "--family", "daily"): "stats",
    ("rolling", "--step", "select", "--only-new"): "select",
    ("rolling", "--step", "holdout"): "holdout",
    ("forward",): "forward",
    ("forward-board",): "board",
}


def step_of(argv: list[str]) -> str | None:
    """The shared step a command line runs, or None."""
    plain, skip = [], False
    for token in argv:
        if skip:
            skip = False
            continue
        if token in VALUED:
            skip = True
            continue
        plain.append(token)
    return STEPS.get(tuple(plain))


def _record(folder: Path, step: str) -> dict:
    try:
        return json.loads((folder / f"{step}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _lock(handle) -> bool:
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def guarded(main, research: str | Path, argv: list[str], pause: float = 60.0) -> int:
    """Run ``main`` (the research CLI) for ``argv``. A shared step waits while another run of it is going, then is
    skipped only if a run of it that started after this call finished in the meantime."""
    step = step_of(argv)
    if step is None:
        return main()
    asked = time.time()
    folder = Path(research) / "jobs" / "shared"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f"{step}.lock", "a+b") as handle:
        if not _lock(handle):
            print(f"等另一個研究流程的同一步（{step}）跑完（同時只跑一個）", flush=True)
            while not _lock(handle):
                time.sleep(pause)
        last = _record(folder, step)
        if float(last.get("started") or 0.0) > asked:            # strictly later: no tie on the clock's tick
            when = datetime.fromtimestamp(float(last["started"]), TAIPEI).strftime("%m/%d %H:%M:%S")
            print(f"跳過：同一步（{step}）在 {when} 開始、已經跑完，比這一步排隊的時間晚——它讀到的資料已經包含這一步排隊時的"
                  "全部，結果相同或更新", flush=True)
            return 0
        began = time.time()
        code = main()
        if not code:
            (folder / f"{step}.json").write_text(json.dumps(
                {"started": began, "finished": time.time(), "pid": os.getpid()}), encoding="utf-8")
        return code
