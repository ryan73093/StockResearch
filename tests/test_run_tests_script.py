"""2026-11-12 (D-043): the run of the suite that never ended. The session doing S11-D started the suite from a file
of its own that called pytest at the top; a test that starts processes ("spawn") made each of them run the whole suite
again, for ever, and the work package waited for it uncommitted. scripts/run-tests.ps1 is the one way to run the
tests: it always ends - with pytest's code, or with 3 when its time limit passes."""
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-tests.ps1"


def _run(*arguments: str) -> tuple[int, str, float]:
    started = time.monotonic()
    done = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT), *arguments],
                          capture_output=True, timeout=300, creationflags=subprocess.CREATE_NO_WINDOW)
    return done.returncode, done.stdout.decode("utf-8", "replace"), time.monotonic() - started


def _suite(folder: Path, body: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "test_small.py"
    path.write_text(body, encoding="utf-8")
    return str(path)


def test_a_suite_that_starts_processes_ends_with_pytests_code(tmp_path):
    passing = _suite(tmp_path / "passing", """import multiprocessing


def _double(number):
    return number * 2


def test_two_started_processes_answer():
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        assert pool.map(_double, [1, 2]) == [2, 4]
""")
    code, output, _ = _run("-LimitSeconds", "120", passing)
    assert code == 0 and "1 passed" in output, output
    failing = _suite(tmp_path / "failing", "def test_no():" + chr(10) + "    assert 1 == 2" + chr(10))
    code, output, _ = _run("-LimitSeconds", "120", failing)
    assert code == 1 and "1 failed" in output and "FAILED" in output, output


def test_a_suite_that_does_not_end_is_stopped_at_the_limit(tmp_path):
    endless = _suite(tmp_path / "endless", "import time" + chr(10) + chr(10) + "def test_waits():" + chr(10)
                     + "    time.sleep(600)" + chr(10))
    code, output, seconds = _run("-LimitSeconds", "8", endless)
    assert code == 3 and "沒有在時限" in output, output
    assert seconds < 90
    listed = subprocess.run(["powershell.exe", "-NoProfile", "-Command",
                             "(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*"
                             + str(tmp_path.name) + "*endless*' -and $_.Name -like 'python*' } | Measure-Object).Count"],
                            capture_output=True, timeout=120, creationflags=subprocess.CREATE_NO_WINDOW)
    assert listed.stdout.decode().strip() == "0"


def test_the_suite_is_not_started_from_a_file_of_our_own():
    """No file of the repository calls pytest.main: started that way, every process a test starts runs the suite again."""
    callers = [str(path.relative_to(ROOT)) for folder in ("scripts", "src", "tests") for path in (ROOT / folder).rglob("*.py")
               if path.name != Path(__file__).name and "pytest.main(" in path.read_text(encoding="utf-8", errors="replace")]
    assert callers == []


@pytest.mark.parametrize("name", ["test_two_processes_saving_at_once_keep_both"])
def test_a_test_that_starts_processes_waits_with_a_limit(name):
    """A Pool waits for ever for a worker that ended without an answer; the tests start processes of their own and
    join them with a time limit."""
    text = (ROOT / "tests" / "test_research_decision_race.py").read_text(encoding="utf-8")
    body = text[text.index(f"def {name}"):]
    body = body[:body.index(chr(10) + "def ", 1)]
    assert ".Pool(" not in body and ".join(120)" in body
