"""2026-10-20: the weekly test suite and its System page tile (application/test_runs.py)."""
import subprocess
from datetime import UTC, datetime, timedelta

from quant_platform.application import test_runs

REPORT = """<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" errors="0" failures="1"
skipped="1" tests="4" time="3.0"><testcase classname="tests.test_a" name="test_ok" time="0.1"/>
<testcase classname="tests.test_a" name="test_two" time="0.1"/>
<testcase classname="tests.test_b" name="test_broken" time="0.1"><failure message="assert 1 == 2">long trace</failure>
</testcase><testcase classname="tests.test_b" name="test_later" time="0"><skipped message="no GPU"/></testcase>
</testsuite></testsuites>"""


def test_a_run_keeps_its_summary_and_the_tile_shows_it(tmp_path):
    seen = {}

    def runner(command, **options):
        seen["command"], seen["cwd"] = command, options["cwd"]
        report = next(item.split("=", 1)[1] for item in command if item.startswith("--junitxml="))
        open(report, "w", encoding="utf-8").write(REPORT)
        return subprocess.CompletedProcess(command, 1, stdout="1 failed, 2 passed, 1 skipped in 3.00s\n", stderr="")

    ticks = iter([0.0, 125.0])
    now = datetime(2026, 10, 25, 20, 30, tzinfo=UTC)
    summary = test_runs.run(tmp_path, tmp_path / "test_runs", runner=runner, clock=lambda: next(ticks), now=now)
    assert seen["cwd"] == str(tmp_path) and "tests" in seen["command"] and "--basetemp" in seen["command"]
    assert (summary["total"], summary["passed"], summary["failed"], summary["skipped"]) == (4, 2, 1, 1)
    assert summary["failures"] == [{"test": "tests.test_b::test_broken", "message": "assert 1 == 2"}]
    assert summary["ok"] is False and summary["seconds"] == 125.0
    assert test_runs.latest(tmp_path / "test_runs") == summary
    tile = test_runs.tile(summary, now + timedelta(hours=1))
    assert tile["state"] == "有失敗" and tile["badge"] == "badge--bad" and "test_broken" in tile["detail"]
    assert "10-26 04:30" in tile["detail"]                                         # Taipei time
    good = {**summary, "ok": True, "failed": 0, "failures": []}
    assert test_runs.tile(good, now + timedelta(days=1))["state"] == "全部通過"
    assert test_runs.tile(good, now + timedelta(days=9))["state"] == "逾期"
    assert test_runs.tile(None, now)["state"] == "尚未執行"


def test_a_run_that_does_not_finish_is_not_ok(tmp_path):
    def runner(command, **options):
        raise subprocess.TimeoutExpired(command, test_runs.TIMEOUT)

    summary = test_runs.run(tmp_path, tmp_path / "out", runner=runner)
    assert summary["ok"] is False and summary["total"] == 0 and "沒跑完" in test_runs.tile(summary, datetime.now(UTC))["state"]
