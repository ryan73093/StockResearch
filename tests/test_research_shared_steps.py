"""2026-10-29 (使用者：是你的程式太肥、架構太肥，還是真的這麼多；不要為了省效能最後又給我錯的資料): the steps every flow
ends with run one at a time, and a run is skipped only when a run that started after it was asked for has finished."""
import json
import threading
import time

from quant_platform.research import shared_steps as ss


def test_the_command_lines_that_are_shared_steps():
    assert ss.step_of(["stats", "--family", "daily"]) == "stats"
    assert ss.step_of(["stats", "--family", "daily", "--universe", "all"]) is None        # another computation
    assert ss.step_of(["rolling", "--step", "select", "--only-new", "--workers", "2", "--broker", "cathay"]) == "select"
    assert ss.step_of(["rolling", "--step", "holdout", "--broker", "cathay"]) == "holdout"
    assert ss.step_of(["rolling", "--step", "holdout", "--cutoff", "2026-07-31"]) is None
    assert ss.step_of(["forward"]) == "forward" and ss.step_of(["forward", "--date", "2026-10-28"]) is None
    assert ss.step_of(["forward-board", "--broker", "cathay"]) == "board"
    assert ss.step_of(["daily", "--name", "bear12", "--broker", "cathay"]) is None


def test_one_after_another_every_call_runs(tmp_path):
    """A step asked for after the last run started always runs — nothing is guessed about what it reads."""
    runs = []
    for _ in range(3):
        assert ss.guarded(lambda: runs.append(1) or 0, tmp_path, ["stats", "--family", "daily"]) == 0
    assert len(runs) == 3
    record = json.loads((tmp_path / "jobs" / "shared" / "stats.json").read_text(encoding="utf-8"))
    assert record["finished"] >= record["started"]


def test_the_flows_waiting_during_a_run_share_the_next_one(tmp_path):
    """Two flows ask while a run is going: the first to get the lock runs (it started after both asked), the other
    skips; one that asked before the run it waited for started would still run."""
    folder = tmp_path / "jobs" / "shared"
    folder.mkdir(parents=True)
    runs, codes = [], []
    holder = open(folder / "board.lock", "a+b")
    assert ss._lock(holder)                                         # a run of the board is going
    started = threading.Event()

    def flow():
        codes.append(ss.guarded(lambda: (runs.append(1), time.sleep(0.2))[0] or 0, tmp_path,
                                ["forward-board", "--broker", "cathay"], pause=0.05))
    waiting = [threading.Thread(target=flow) for _ in range(2)]
    for thread in waiting:
        thread.start()
    time.sleep(0.3)
    holder.close()                                                  # the going run ends
    for thread in waiting:
        thread.join(5)
    assert codes == [0, 0] and len(runs) == 1                       # one run for both waiting flows


def test_a_failed_run_is_not_counted(tmp_path):
    assert ss.guarded(lambda: 1, tmp_path, ["stats", "--family", "daily"]) == 1
    assert not (tmp_path / "jobs" / "shared" / "stats.json").exists()
    assert ss.guarded(lambda: 0, tmp_path, ["daily", "--name", "x"]) == 0       # not a shared step: just runs
