"""The worker's heavy jobs in a child process (2026-11-12; problem register P5).

The worker ran the forward record, the after-close orders, the model refit, the stock snapshot and the research data
refresh in its own process. Each loads the price panel (and the refit the GPU); Python does not hand that memory back,
so the worker held 11.8 GB private two days after a restart with nothing running — a sixth of the machine, beside the
research flows that wait for memory. ``in_child`` gives the scheduler a job that starts
``python -m quant_platform.scheduler.child <job>`` (no window, the worker's own priority and environment) and waits for
it: one run at a time as before, the job's failure is the scheduled job's failure, and the memory goes back to the
system when the job ends. The job itself is the same function with its own container.
"""

from __future__ import annotations

import functools
import logging
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

from quant_platform.container import build_container

logger = logging.getLogger(__name__)

HEAVY = ("refresh_research_history", "prepare_after_close_orders", "record_forward_simulation", "retrain_models",
         "build_stock_snapshot")


class ChildJobFailed(RuntimeError):
    """The child process of a scheduled job ended on a code other than 0."""


def _log_path(container) -> Path:
    from quant_platform.container import _instance_dir

    return _instance_dir(container.settings.database_url) / "worker-jobs.log"


def _stamp(name: str, what: str) -> bytes:
    return (f"{datetime.now():%Y-%m-%d %H:%M:%S} {name} {what}" + chr(10)).encode("utf-8")


def in_child(function, when=None):
    """The scheduler's job for ``function`` (one of ``HEAVY``): run in a child process. ``when(container)``: a cheap
    look in the worker first — a job asked every minute (the after-close orders) starts a process only when it has
    work to do."""
    name = function.__name__
    if name not in HEAVY:
        raise ValueError(f"{name} is not listed in scheduler.child.HEAVY")

    @functools.wraps(function)
    def job(container, *extra):
        if extra:
            raise ValueError(f"{name}: a time of the caller's cannot be passed to the child; call the function itself")
        if when is not None and not when(container):
            return None
        path = _log_path(container)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as log:
            log.write(_stamp(name, "開始"))
            log.flush()
            done = subprocess.run([sys.executable, "-m", "quant_platform.scheduler.child", name], stdout=log, stderr=log,
                                  stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            log.write(_stamp(name, f"結束（結束碼 {done.returncode}）"))
        if done.returncode != 0:
            raise ChildJobFailed(f"{name} 在子程序結束碼 {done.returncode}；輸出在 {path}")
        return None

    job.in_child = True
    return job


def main(arguments: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if arguments is None else arguments
    if len(arguments) != 1 or arguments[0] not in HEAVY:
        print("usage: python -m quant_platform.scheduler.child <" + "|".join(HEAVY) + ">", file=sys.stderr)
        return 2
    from quant_platform.scheduler import runner

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        result = getattr(runner, arguments[0])(build_container())
    except Exception:
        traceback.print_exc()
        return 1
    logger.info("%s: %s", arguments[0], result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
