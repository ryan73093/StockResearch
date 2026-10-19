"""2026-10-20 (docs/project_risks.md item 9: tests left broken went unnoticed): the whole test suite once a week, its
result on the System page.

The worker runs ``python -m pytest tests`` from the project root (the tests read the source tree, ``pythonpath = src``)
with a scratch directory under ``instance/test_runs`` and a JUnit report; the summary — passed, failed, skipped, how
long, the failing tests' names and first message lines — is kept as ``latest.json`` (and one file per run).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ElementTree
from datetime import UTC, datetime, timedelta
from pathlib import Path

TIMEOUT = 90 * 60             # seconds the suite may take
STALE = timedelta(days=8)     # a weekly run older than this is overdue
KEEP = 12                     # runs kept


def parse(report: Path) -> dict[str, object]:
    """Counts and the failing tests from a JUnit XML report."""
    root = ElementTree.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = failures = errors = skipped = 0
    failed: list[dict[str, str]] = []
    for suite in suites:
        total += int(suite.get("tests", 0))
        failures += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
        for case in suite.iter("testcase"):
            problem = case.find("failure") if case.find("failure") is not None else case.find("error")
            if problem is not None:
                message = (problem.get("message") or problem.text or "").strip().splitlines()
                failed.append({"test": f"{case.get('classname', '')}::{case.get('name', '')}",
                               "message": (message[0] if message else "")[:300]})
    return {"total": total, "failed": failures + errors, "skipped": skipped,
            "passed": total - failures - errors - skipped, "failures": failed}


def run(root: Path, out: Path, runner=subprocess.run, clock=time.monotonic, now=None) -> dict[str, object]:
    """Run the suite, keep and return the summary (``ok`` is False when a test failed or the run did not finish)."""
    out.mkdir(parents=True, exist_ok=True)
    report = out / "junit.xml"
    if report.exists():
        report.unlink()
    started = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    began = clock()
    command = [sys.executable, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider",
               "--basetemp", str(out / "tmp"), f"--junitxml={report}"]
    try:
        finished = runner(command, cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=TIMEOUT)
        code, tail = finished.returncode, (finished.stdout or "").strip().splitlines()[-3:]
    except subprocess.TimeoutExpired:
        code, tail = None, [f"超過 {TIMEOUT // 60} 分鐘沒跑完"]
    summary: dict[str, object] = {"started_at": started, "seconds": round(clock() - began, 1), "exit_code": code,
                                  "tail": tail}
    if report.is_file():
        summary.update(parse(report))
    else:
        summary.update({"total": 0, "failed": 0, "skipped": 0, "passed": 0, "failures": []})
    summary["ok"] = code == 0 and summary["failed"] == 0 and summary["total"] > 0
    text = json.dumps(summary, ensure_ascii=False, indent=1)
    (out / "latest.json").write_text(text, encoding="utf-8")
    stamp = started.replace(":", "").replace("-", "")[:15]
    (out / f"run-{stamp}.json").write_text(text, encoding="utf-8")
    for old in sorted(out.glob("run-*.json"))[:-KEEP]:
        old.unlink()
    return summary


def latest(out: Path) -> dict[str, object] | None:
    try:
        return json.loads((out / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def tile(summary: dict[str, object] | None, now: datetime) -> dict[str, str]:
    """The System page's tile."""
    if summary is None:
        return {"label": "自動測試", "state": "尚未執行", "badge": "", "detail": "每週日 04:30 跑全部測試"}
    when = datetime.fromisoformat(str(summary["started_at"]))
    overdue = now - when > STALE
    if not summary.get("ok"):
        state, badge = ("有失敗" if summary.get("failed") else "沒跑完"), "badge--bad"
    else:
        state, badge = ("逾期" if overdue else "全部通過"), ("badge--warn" if overdue else "badge--ok")
    names = "；".join(str(item["test"]).split("::")[-1] for item in (summary.get("failures") or [])[:3])
    detail = (f"{when.astimezone(_taipei()):%m-%d %H:%M}・通過 {summary.get('passed', 0)}、失敗 {summary.get('failed', 0)}、"
              f"略過 {summary.get('skipped', 0)}・{float(summary.get('seconds') or 0) / 60:.0f} 分鐘")
    return {"label": "自動測試", "state": state, "badge": badge, "detail": detail + (f"・{names}" if names else "")}


def _taipei():
    from zoneinfo import ZoneInfo

    return ZoneInfo("Asia/Taipei")
