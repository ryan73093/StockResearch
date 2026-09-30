"""Build the research dataset and run the first research round, unattended (S3/S4).

Waits for a running `research.history fetch` (PID file) to finish, then runs
in order, logging to instance/research/first_run.log:

  history build (offline) → actions → crosscheck → oddlot (every 10 sessions)
  → baselines (full period) → batch "first" (development) → stats (development)

Network steps pause between 13:20 and 14:45 Taipei so they never compete with
the after-hours workflow. Run from the project root:

    $env:PYTHONPATH = "src"; .\\.venv\\Scripts\\python.exe scripts\\research_first_run.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, time as clock
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "instance" / "research" / "history"
LOG = ROOT / "instance" / "research" / "first_run.log"
QUIET = (clock(13, 20), clock(14, 45))
STEPS = [
    ("history build（離線重建）", ["-m", "quant_platform.research.history", "build"], False),
    ("除權息與總報酬", ["-m", "quant_platform.research.history", "actions"], True),
    ("Yahoo 交叉核對", ["-m", "quant_platform.research.history", "crosscheck"], True),
    ("盤後零股成交分布", ["-m", "quant_platform.research.history", "oddlot", "--every", "10"], True),
    ("基準策略（全期間）", ["-m", "quant_platform.research", "baselines", "--period", "full"], False),
    ("第一批研究（開發期）", ["-m", "quant_platform.research", "batch", "--name", "first", "--period", "development"], False),
    ("統計檢定（開發期）", ["-m", "quant_platform.research", "stats", "--period", "development"], False),
]


def log(message: str) -> None:
    line = f"{datetime.now(TAIPEI):%Y-%m-%d %H:%M:%S} {message}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def running(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, check=False
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def wait_quiet_hours() -> None:
    while True:
        now = datetime.now(TAIPEI).time()
        if not (QUIET[0] <= now <= QUIET[1]):
            return
        log("13:20–14:45 暫停網路步驟，等待中")
        time.sleep(300)


def main() -> int:
    pid_file = HISTORY / "fetch.pid"
    if pid_file.is_file():
        pid = int(pid_file.read_text(encoding="ascii").strip() or 0)
        if pid and running(pid):
            log(f"等待下載程序 {pid} 結束")
            while running(pid):
                time.sleep(60)
    errors = HISTORY / "fetch.err.log"
    if errors.is_file() and "拒絕" in errors.read_text(encoding="utf-8", errors="replace"):
        log("下載被官方來源拒絕，停止；稍後重新執行 fetch 續抓")
        return 2
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}
    for name, arguments, network in STEPS:
        if network:
            wait_quiet_hours()
        log(f"開始：{name}")
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, *arguments], cwd=ROOT, env=environment,
            capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
        )
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(result.stdout[-20000:])
            if result.stderr.strip():
                handle.write("[stderr]\n" + result.stderr[-5000:] + "\n")
        log(f"結束：{name}（{time.monotonic() - started:.0f} 秒，結束碼 {result.returncode}）")
        if result.returncode not in (0, 1) and name.startswith(("history build", "除權息")):
            log("關鍵步驟失敗，停止")
            return result.returncode
    log("全部完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
