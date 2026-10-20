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
