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


PIPELINE = """﻿2026-10-29 01:05:48 開始研究流程 blendsize（PID 8136）
2026-10-29 11:33:21 步驟 4-holdout 結束（結束碼 0）
2026-10-29 11:34:36 等待：等記憶體：這一步要約 16 GB 可用，而且沒有（10/28 記憶體用完七次）
2026-10-29 11:35:05 等待結束：等記憶體：這一步要約 16 GB 可用，而且沒有（10/28 記憶體用完七次）
2026-10-29 11:35:05 步驟 5-forward 開始：python -m quant_platform.research forward（輸出：x）
2026-10-29 11:39:55 步驟 5-forward 結束（結束碼 0）
2026-10-29 11:40:00 等待：13:30～14:40 盤後單時段，研究程式避開
2026-10-29 11:41:00 等待結束：13:30～14:40 盤後單時段，研究程式避開
2026-10-29 11:42:08 等待：等記憶體：這一步要約 16 GB 可用，而且沒有（10/28 記憶體用完七次）
2026-10-29 11:42:37 等待結束：等記憶體：這一步要約 16 GB 可用，而且沒有（10/28 記憶體用完七次）
2026-10-29 11:43:02 等待：等另一個研究流程的 forward-board 步驟跑完（同時只跑一個）
2026-10-29 13:54:13 等待結束：等另一個研究流程的 forward-board 步驟跑完（同時只跑一個）
2026-10-29 13:54:13 步驟 6-board 開始：python -m quant_platform.research forward-board --broker cathay（輸出：x）
"""


def _taipei(text):
    from datetime import datetime

    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ss.TAIPEI).timestamp()


def test_when_a_step_was_asked_for_from_its_flows_log(tmp_path):
    """The first of the waits its Invoke-Step began with (not the flow's own wait before it, not the start after the
    waits), to the next second; a step that did not wait: its start."""
    (tmp_path / "blendsize-20261029-010548-pipeline.log").write_text(PIPELINE, encoding="utf-8")
    board = ss.asked_in_log(tmp_path / "blendsize-20261029-010548-6-board.log")
    assert board == _taipei("2026-10-29 11:42:08") + 1
    forward = ss.asked_in_log(tmp_path / "blendsize-20261029-010548-5-forward.log")
    assert forward == _taipei("2026-10-29 11:34:36") + 1
    (tmp_path / "x-20261029-120000-pipeline.log").write_text(
        "2026-10-29 12:00:00 開始研究流程 x（PID 1）\n2026-10-29 12:00:01 步驟 1-board 開始：python（輸出：x）\n",
        encoding="utf-8")
    assert ss.asked_in_log(tmp_path / "x-20261029-120000-1-board.log") == _taipei("2026-10-29 12:00:01") + 1
    assert ss.asked_in_log(tmp_path / "x-20261029-120000-2-stats.log") is None          # not started by that flow
    assert ss.asked_in_log(tmp_path / "board.log") is None                               # not a pipeline step's log


def test_a_flow_asked_before_the_last_run_started_skips_it(tmp_path):
    """10/29: bear12's board started 13:06:49 and finished; blendsize asked for the board at 11:42 (its forward step
    ended 11:39) and waited — it skips; a flow that asks after 13:06:49 runs."""
    ss.record(tmp_path, "board", _taipei("2026-10-29 13:06:49"))
    runs = []
    code = ss.guarded(lambda: runs.append(1) or 0, tmp_path, ["forward-board", "--broker", "cathay"],
                      asked=_taipei("2026-10-29 11:42:08") + 1)
    assert code == 0 and runs == []
    ss.guarded(lambda: runs.append(1) or 0, tmp_path, ["forward-board", "--broker", "cathay"],
               asked=_taipei("2026-10-29 13:54:13") + 1)
    assert runs == [1]


def test_the_time_the_pipeline_hands_is_used_and_not_handed_further(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setenv(ss.ASKED, "100.0")
    ss.guarded(lambda: seen.append(ss.os.environ.get(ss.ASKED)) or 0, tmp_path, ["daily", "--name", "x"])
    assert seen == [None]                                  # taken off before the step runs, shared step or not
    assert ss.asked_at("100.0", now=200.0) == 100.0
    assert ss.asked_at("300.0", now=200.0) == 200.0        # never later than now
    assert ss.asked_at("garbage", now=200.0) <= 200.0


def test_a_record_never_moves_back(tmp_path):
    ss.record(tmp_path, "board", 200.0)
    ss.record(tmp_path, "board", 100.0)                    # an older run finishing later
    assert ss._record(tmp_path / "jobs" / "shared", "board")["started"] == 200.0
    ss.record(tmp_path, "board", 300.0)
    assert ss._record(tmp_path / "jobs" / "shared", "board")["started"] == 300.0


def test_a_step_finds_its_own_log(tmp_path):
    """On Windows the process reads the path its output is written to (the pipeline's redirection)."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    if os.name != "nt":
        return
    log = tmp_path / "flow-20261029-120000-1-board.log"
    with open(log, "w", encoding="utf-8") as out:
        subprocess.run([sys.executable, "-c", "from quant_platform.research.shared_steps import _own_output; "
                        "print(_own_output())"], stdout=out, check=True,
                       env={**os.environ, "PYTHONPATH": str(Path(ss.__file__).resolve().parents[2])})
    found = Path(log.read_text(encoding="utf-8").strip())
    assert os.path.normcase(found.resolve()) == os.path.normcase(log.resolve())
