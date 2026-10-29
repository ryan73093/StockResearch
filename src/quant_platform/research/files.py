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


def write_text(path: str | Path, text: str, wait: float = 300.0) -> None:
    """The whole file or none of it: written beside it, then put in its place (``replace``). 2026-10-29: the forward
    accounts' list (``tracked.json``) and its companions were written in place — the after-close orders reading the list
    at 13:50 while a research flow's forward step wrote it would have read half of it and stopped. The same bytes as
    ``Path.write_text`` (text mode)."""
    path = Path(path)
    partial = path.with_name(f"{path.name}.{os.getpid()}.partial")
    partial.write_text(text, encoding="utf-8")
    replace(partial, path, wait=wait)


def write_bytes(path: str | Path, data: bytes, wait: float = 300.0) -> None:
    """``write_text`` for bytes (a model's pickle: the forward record, the board and the orders read the models while
    a research flow trains them)."""
    path = Path(path)
    partial = path.with_name(f"{path.name}.{os.getpid()}.partial")
    partial.write_bytes(data)
    replace(partial, path, wait=wait)


def save_beside(path: str | Path, save, wait: float = 300.0) -> None:
    """``save(temporary)`` writes the file under the same name in a folder beside it; then it is put in place. The
    same name because a saver may keep it inside the file (``torch.save`` names its archive after it), so the bytes
    are the ones saved straight to ``path``."""
    path = Path(path)
    folder = path.parent / f".partial-{os.getpid()}"
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / path.name
    save(temporary)
    replace(temporary, path, wait=wait)
    try:
        folder.rmdir()
    except OSError:
        pass                                   # another file of this process is still being saved there
