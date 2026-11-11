"""2026-10-14: a PowerShell switch runs every case that matches, so a pipeline name used twice runs both (starting the
new "robot2" retrained the old robot first). Every pipeline name is one case, and both scripts accept exactly those."""
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def accepted(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(r"ValidateSet\(([^)]*)\)", text)
    return re.findall(r'"([^"]+)"', match.group(1))


def test_every_pipeline_name_is_one_case_and_both_scripts_accept_the_same_names():
    text = (ROOT / "scripts" / "research-pipeline.ps1").read_text(encoding="utf-8-sig")
    cases = re.findall(r'^    "([A-Za-z0-9_\-]+)" \{', text, re.M)        # 2026-10-20: names like holding2_a
    assert [name for name, count in Counter(cases).items() if count > 1] == []
    pipeline = accepted(ROOT / "scripts" / "research-pipeline.ps1")
    starter = accepted(ROOT / "scripts" / "start-research.ps1")
    assert sorted(pipeline) == sorted(starter) == sorted(cases)


def _helpers(tmp_path, body: str) -> str:
    """Run PowerShell with the script's step helpers (the region between the two markers) and ``body``."""
    import subprocess

    text = (ROOT / "scripts" / "research-pipeline.ps1").read_text(encoding="utf-8-sig")
    region = text[text.index("#region step-helpers"):text.index("#endregion step-helpers")]
    script = tmp_path / "helpers.ps1"
    script.write_text("\ufeff" + region + "\n" + body, encoding="utf-8")
    done = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                          capture_output=True, timeout=120)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")[-600:]
    return done.stdout.decode("utf-8", "replace")


def test_an_argument_with_a_space_or_a_quote_reaches_the_step_as_one_argument(tmp_path):
    """2026-11-08: refitfix's restart step stopped on its --reason — Start-Process joins the arguments with spaces and
    one with a space became two (system review of 11-10, root cause 5). They are quoted as Windows reads them."""
    import json
    import sys

    slash = chr(92)
    wanted = ["-c", "import sys, json; print(json.dumps(sys.argv[1:]))", "--reason", "模型重訓到 9/30 之前",
              'say "hi" now', "plain", "", "tail" + slash, "a" + slash + '"b', f"C:{slash}Dev{slash}x y{slash}"]
    out = tmp_path / "out.txt"
    literal = ", ".join("'" + item.replace("'", "''") + "'" for item in wanted)
    _helpers(tmp_path, f"""
$arguments = @({literal})
Start-Process -FilePath '{sys.executable}' -ArgumentList (Join-Arguments $arguments) -NoNewWindow -Wait -RedirectStandardOutput '{out}'
""")
    assert json.loads(out.read_text(encoding="utf-8")) == wanted[2:]


def test_what_a_steps_exit_code_does_to_the_flow(tmp_path):
    """2026-11-12 (root cause 5: a flow whose step failed went on and was marked done): a step that never ran (the
    command line refused) or ended on Python's traceback stops the flow; one that ran and could not do its work (2, 3:
    a source refused, no data) lets the flow go on and the flow ends as failed; a 1 without a traceback (an audit
    that found differences) is written down and the flow can end done."""
    usage, crash, quiet = tmp_path / "usage.log", tmp_path / "crash.log", tmp_path / "quiet.log"
    usage.write_text("usage: __main__.py [-h]\n__main__.py: error: unrecognized arguments: 之前\n", encoding="utf-8")
    crash.write_text("Traceback (most recent call last):\n  File x\nKeyError: '2026-10-29'\n", encoding="utf-8")
    quiet.write_text("官方來源拒絕連線，已停止\n", encoding="utf-8")
    out = _helpers(tmp_path, f"""
(Get-StepVerdict 0 '{quiet}'), (Get-StepVerdict 2 '{usage}'), (Get-StepVerdict 1 '{crash}'), (Get-StepVerdict 1 '{quiet}'),
(Get-StepVerdict 2 '{quiet}'), (Get-StepVerdict 3 '{quiet}'), (Get-StepVerdict -1073741819 '{quiet}'),
(Get-StepVerdict 1 '{tmp_path / "none.log"}') -join ','
""")
    assert out.strip() == "ok,stop,stop,noted,unfinished,unfinished,unfinished,noted"


def test_the_flow_uses_the_helpers_and_ends_failed_when_a_step_could_not_do_its_work():
    text = (ROOT / "scripts" / "research-pipeline.ps1").read_text(encoding="utf-8-sig")
    assert "-ArgumentList (Join-Arguments $Arguments)" in text and "-ArgumentList $Arguments" not in text
    assert "Get-StepVerdict $process.ExitCode $err" in text
    assert text.rstrip().splitlines()[-1].startswith("Complete-Pipeline")


def test_memory_another_flow_has_just_reserved_is_not_free(tmp_path):
    """2026-11-12 (the memory answer of 11-11): the wait looked at the free memory once and reserved nothing — two flows
    in the same minute both saw 30 GB free and both started. What a flow is let in for is counted by the next one until
    its time is up or its flow has ended; a flow's own earlier reservation is not counted against it."""
    import os

    folder = tmp_path / "memory"
    out = _helpers(tmp_path, f"""
$folder = '{folder}'
$a = Request-Memory $folder {os.getpid()} 'flow a' 16 30
$b = Request-Memory $folder $PID 'flow b' 16 30
$reserved = Get-ReservedGB $folder
$again = Request-Memory $folder {os.getpid()} 'flow a, next step' 16 30
$small = Request-Memory $folder $PID 'flow b' 14 30
$gone = Request-Memory $folder 999999 'a flow that ended' 4 100
$afterGone = Get-ReservedGB $folder
$expired = Request-Memory $folder $PID 'flow b' 14 30 -Minutes -1
$afterExpired = Get-ReservedGB $folder
"$a,$b,$reserved,$again,$small,$afterGone,$expired,$afterExpired"
""")
    # a in (30 >= 16); b refused (30 - 16 < 16); a's next step counts only the others; b fits 14; the dead flow's
    # 4 GB is dropped (16 + 14 left); b's reservation already expired is dropped (16 left)
    assert out.strip() == "True,False,16,True,True,30,True,16"


def test_the_memory_wait_reserves_what_it_lets_in():
    text = (ROOT / "scripts" / "research-pipeline.ps1").read_text(encoding="utf-8-sig")
    body = text[text.index("function Wait-ForMemory"):text.index("function Wait-ForSimulationSlot")]
    assert "Request-Memory $script:ReservationFolder $PID" in body and "(Get-CommitFreeGB) -lt $need" not in body
