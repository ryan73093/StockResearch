"""2026-10-29 (使用者：你他媽當我電腦資源很多是不是；是你的程式太肥、架構太肥，還是真的這麼多；不要為了省效能最後又給我
錯的資料): the steps every research flow ends with recompute everything — the statistics over every trial, the month's
list, its holdout, every forward account and the board replaying all of them. From 10/28 night to 10/29 morning eight
flows ran the statistics eight times (1-1.5 hours each, three at once) and the board five times (up to 3.5 hours each),
each flow having added 10-60 rules.

Each of these steps now runs one at a time (the operating system's file lock, released with the process), and a run is
skipped only when another run of the same step **started after this one was asked for** and finished: that run read
everything there was when this one was asked for, so its result is what this one would have computed or newer. Nothing
is guessed about what a step reads — a step asked for after the last run started always runs. The flows waiting during
one run share the next run instead of each recomputing everything.

When a step was asked for (2026-10-29 14:0x): the moment its flow got to it — every earlier step of the flow had ended —
not when it finally started. A flow's pipeline waits for another flow's run of the same step before it starts this
process (research-pipeline.ps1 ``Invoke-Step``); taking the start as the asking time made each waiting flow rerun the
board after the run that already covered it (10/29: bear12's board 13:06-13:54 had read everything blendsize and bear11
wrote before 11:43, and both would have replayed the 3,391 accounts again). The pipeline passes the moment
(``RESEARCH_STEP_ASKED``); a pipeline started before it did has it in its flow's log."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
VALUED = ("--broker", "--workers")       # flags with a value that do not change what the step computes
ASKED = "RESEARCH_STEP_ASKED"            # research-pipeline.ps1 Invoke-Step: when the step was asked for (Unix seconds)
# the waits research-pipeline.ps1's Invoke-Step begins with, before it starts the step (each logged as 等待：<reason>
# and 等待結束：<reason>); no wait of a flow's own (Wait-While in its block) has these reasons
STEP_WAITS = ("等逐筆模擬的空位", "等記憶體：", "等另一個研究流程的")

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


def asked_in_log(step_log: Path) -> float | None:
    """When a pipeline step was asked for, from its flow's log (the step writes to ``<flow>-<stamp>-<label>.log``, the
    flow to ``<flow>-<stamp>-pipeline.log``): the first of the waits its Invoke-Step began with, or the step's start when
    it did not wait. The log keeps whole seconds; the next second is returned — never earlier than the moment was."""
    match = re.fullmatch(r"(.+-\d{8}-\d{6})-(.+)\.log", step_log.name)
    if not match:
        return None
    base, label = match.groups()
    try:
        lines = (step_log.parent / f"{base}-pipeline.log").read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    starts = [index for index, line in enumerate(lines) if f" 步驟 {label} 開始：" in line]
    if not starts:
        return None
    first = starts[-1]
    while first > 0 and any(f" {mark}{reason}" in lines[first - 1]
                            for mark in ("等待：", "等待結束：") for reason in STEP_WAITS):
        first -= 1
    try:
        moment = datetime.strptime(lines[first][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIPEI)
    except ValueError:
        return None
    return moment.timestamp() + 1.0


def _own_output() -> Path | None:
    """The file this process's standard output goes to (a pipeline step's log), or None."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        handle = msvcrt.get_osfhandle(sys.stdout.fileno())
        final = ctypes.windll.kernel32.GetFinalPathNameByHandleW
        final.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        final.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(1024)
        size = final(handle, buffer, 1024, 0)
    except (OSError, ValueError, AttributeError):
        return None
    return Path(buffer.value.removeprefix("\\\\?\\")) if 0 < size < 1024 else None


def asked_at(handed: str | None, now: float | None = None) -> float:
    """When this step was asked for: what the pipeline handed (``RESEARCH_STEP_ASKED``), else from its flow's log (a
    pipeline whose script was read before the pipeline handed it), else now — never later than now."""
    now = time.time() if now is None else now
    if handed:
        try:
            return min(float(handed), now)
        except ValueError:
            pass
    output = _own_output()
    found = asked_in_log(output) if output is not None else None
    return min(found, now) if found is not None else now


def _record(folder: Path, step: str) -> dict:
    try:
        return json.loads((folder / f"{step}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def record(research: str | Path, step: str, began: float) -> None:
    """Write down a finished run of ``step`` that began at ``began`` (also a run outside the research CLI: the worker's
    board after the 15:30 forward record). Never moved back to an earlier start; written whole (a reader never sees half
    of it)."""
    folder = Path(research) / "jobs" / "shared"
    folder.mkdir(parents=True, exist_ok=True)
    if float(_record(folder, step).get("started") or 0.0) >= began:
        return
    partial = folder / f"{step}.json.{os.getpid()}.partial"
    partial.write_text(json.dumps({"started": began, "finished": time.time(), "pid": os.getpid()}), encoding="utf-8")
    from quant_platform.research import files

    try:
        files.replace(partial, folder / f"{step}.json")
    except PermissionError:                    # not written: a later flow runs the step again (less shared, not wrong)
        print(f"沒有寫入共用步驟紀錄（{step}）：檔案被另一個程式開著", flush=True)


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


@contextmanager
def exclusive(path: Path, pause: float = 0.5):
    """Hold ``path``'s lock (another process holding it: wait) — for a few writes that must not interleave."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as handle:
        while not _lock(handle):
            time.sleep(pause)
        yield


def _clock(moment: float) -> str:
    return datetime.fromtimestamp(moment, TAIPEI).strftime("%m/%d %H:%M:%S")


def guarded(main, research: str | Path, argv: list[str], pause: float = 60.0, asked: float | None = None) -> int:
    """Run ``main`` (the research CLI) for ``argv``. A shared step waits while another run of it is going, then is
    skipped only if a run of it that started after this step was asked for (``asked_at``) has finished."""
    handed = os.environ.pop(ASKED, None)           # not handed down to what this process starts
    step = step_of(argv)
    if step is None:
        return main()
    asked = asked_at(handed) if asked is None else asked
    folder = Path(research) / "jobs" / "shared"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f"{step}.lock", "a+b") as handle:
        if not _lock(handle):
            print(f"等另一個研究流程的同一步（{step}）跑完（同時只跑一個）", flush=True)
            while not _lock(handle):
                time.sleep(pause)
        last = _record(folder, step)
        if float(last.get("started") or 0.0) > asked:            # strictly later: no tie on the clock's tick
            print(f"跳過：同一步（{step}）在 {_clock(float(last['started']))} 開始、已經跑完，比這一步排隊的時間"
                  f"（{_clock(asked)}，流程前面的步驟都已寫完）晚——它讀到的資料已經包含這一步排隊時的全部，結果相同或更新",
                  flush=True)
            return 0
        began = time.time()
        code = main()
        if not code:
            record(research, step, began)
        return code
