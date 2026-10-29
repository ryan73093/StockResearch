"""Files written whole while other processes read them (2026-10-29).

On 10-29 the after-close orders died at 14:27, 2,222 of 3,607 accounts done, on ``PermissionError [WinError 5]``: a
check was reading the report at the moment the worker put the new one in its place. On Windows a file that another
process has open cannot be replaced — not even when that process shares it for deletion (tried the same day) — and the
pages read the board and the orders as the jobs write them. A reader holds a file for milliseconds; the writers here
try again for a while instead of failing on the first try."""

from __future__ import annotations

import os
import time
from pathlib import Path


def replace(source: str | Path, target: str | Path, wait: float = 30.0) -> None:
    """``os.replace``, tried again while another process holds ``target`` open (up to ``wait`` seconds)."""
    deadline = time.monotonic() + wait
    while True:
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)
