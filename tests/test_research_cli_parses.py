"""2026-10-21: an option added twice (--reason) broke every research command at its start; the parser must build."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_the_research_command_line_builds():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "PYTHONIOENCODING": "utf-8"}
    for command in ("trade-audit", "pipeline-wait", "rolling", "scan-large"):
        done = subprocess.run([sys.executable, "-m", "quant_platform.research", command, "--help"], cwd=ROOT, env=env,
                              capture_output=True, text=True, encoding="utf-8", timeout=120)
        assert done.returncode == 0, done.stderr[-400:]
