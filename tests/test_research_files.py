"""2026-10-29: files written whole while other processes read them (research/files.py)."""
import json
import os
import threading
import time

from quant_platform.research import files


def test_a_file_written_whole_is_never_seen_half_written(tmp_path):
    """The forward accounts' list is written beside it and put in its place: a reader reads the old list or the new
    one, never part of one (it was written in place, and the after-close orders read it at 13:50)."""
    path = tmp_path / "tracked.json"
    old, new = [{"rule_hash": "a"}], [{"rule_hash": str(number)} for number in range(20000)]
    files.write_text(path, json.dumps(old))
    seen, stop = [], threading.Event()

    def read():
        while not stop.is_set():
            try:
                seen.append(len(json.loads(path.read_text(encoding="utf-8"))))
            except (PermissionError, FileNotFoundError):     # Windows: the moment it is put in place
                pass

    reader = threading.Thread(target=read)
    reader.start()
    for _ in range(20):
        files.write_text(path, json.dumps(new))
        files.write_text(path, json.dumps(old))
    stop.set()
    reader.join()
    assert seen and set(seen) <= {1, 20000}
    assert not list(tmp_path.glob("*.partial"))


def test_a_file_held_open_is_put_in_place_once_the_reader_lets_go(tmp_path):
    path = tmp_path / "tracked.json"
    files.write_text(path, "[1]")
    handle = open(path, encoding="utf-8")                       # noqa: SIM115 — held by "another reader"
    threading.Timer(0.3, handle.close).start()
    began = time.monotonic()
    files.write_text(path, "[2]", wait=10.0)
    assert path.read_text(encoding="utf-8") == "[2]"
    if os.name == "nt":                                         # Windows does not replace an open file
        assert time.monotonic() - began >= 0.25
