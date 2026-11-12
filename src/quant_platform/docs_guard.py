"""2026-11-11 (使用者：D 要能確保後續文件開發後也要能維持文件的穩定、ai 要能讀對資料、省 token;
docs/problem_register_2026-11-11.md「D 的設計」第 4 點): the documents' guard. ``tests/test_docs_guard.py`` runs it on
every test run and the 16:00 check (``research/invariants.py``) every day, so the documents cannot grow back into
what they were on 11-11 (AGENTS.md 14,609 characters loaded into every conversation, a handoff of six dated blocks,
T0 defined in five places, the roadmap 63 commits and the architecture 286 behind).

What it holds the documents to:

- sizes: ``AGENTS.md`` (loaded into every conversation) at most 3,000 characters, each ``docs/rules/*.md`` 4,000,
  the handoff and the research conclusions their own limits;
- the handoff only has its fixed fields (當前工作包、等使用者決定、下一步) — no dated blocks, no hand-written 現況;
- T0, 截止日 and 可以上線 are defined in one place only (a ``<!-- 定義：… -->`` marker there; nowhere else words that
  define them);
- every decision number a document cites (``D-001``) is in ``docs/decisions.md``, and every decision there is cited
  by AGENTS.md or a ``docs/rules`` file (none dropped); AGENTS.md names every ``docs/rules`` file;
- every file a living document names in backticks exists;
- the roadmap and the architecture are not more than ``LAG_LIMIT`` code commits behind HEAD.

Characters are counted as Python counts them (``len``): what a conversation loads."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

AGENTS = "AGENTS.md"
RULES = "docs/rules"
DECISIONS = "docs/decisions.md"
HANDOFF = "docs/development_handoff.md"
CONCLUSIONS = "docs/research/conclusions.md"
# 2026-11-12: the conclusions were moved on 11/11 as they were (17,901 characters, a log written by date, limit 18,000);
# rewritten to a sentence or two each (4,415), what was taken out is in the results record word for word
LIMITS = {AGENTS: 3000, HANDOFF: 3000, CONCLUSIONS: 8000}
RULES_LIMIT = 4000
HANDOFF_FIELDS = ("當前工作包", "等使用者決定", "下一步")
HANDOFF_WORDS = ("現況", "重點", "背景執行中", "接手前必讀")       # what the dated blocks were called
LAG_LIMIT = 30                     # code commits (src/, scripts/) a living document may be behind HEAD
LAGGING = ("docs/development_roadmap.md", "docs/system_architecture.md")
DEFINITIONS = {
    "T0": (r"T0(?: 候選)?(?:（高風險）)?\s*[＝=]", r"^\s*(?:[-*]|\d+\.)\s*\**T0(?: 候選)?(?:（高風險）)?\**\s*[：:]",
           r"T0 判定[（(：:]"),
    "截止日": (r"截止日\s*[＝=]", r"截止日是", r"為截止日"),
    "可以上線": (r"可以上線\s*[＝=：:]", r"的就是當月可用", r"當月就可以用", r"當月可用\s*[＝=：:]",
             r"前向模擬[^。\n|]{0,6}(?:至少|滿)\s*\d+\s*(?:週|個交易日)", r"(?:至少|滿)\s*\d+\s*個交易日後等待使用者核准"),
}
MARKER = re.compile(r"<!--\s*定義：(\S+?)\s*-->")
DECISION = re.compile(r"\bD-\d{3}\b")
DECISION_HEADING = re.compile(r"^## (D-\d{3})\b", re.MULTILINE)
# The records that only grow, the dated reviews and the decisions' own words quote old wording on purpose
RECORDS = (DECISIONS, "docs/research/results.md", "DEVELOPMENT_HISTORY.md")
DATED = re.compile(r"\d{4}-\d{2}-\d{2}")
CODE = re.compile(r"`([^`\n]+)`")
# code and documents (data files such as .json or .parquet are named relative to instance/research, not the repository)
PATHLIKE = re.compile(r"^[\w.\-/\\]+\.(?:py|md|ps1|toml|txt|html|css|js|ya?ml|cfg|ini|bat|cmd)$")
DATA_PREFIXES = ("instance/", ".runtime/", ".venv/", "build/", ".env")


def _read(root: Path, name: str) -> str:
    path = root / name
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def rules_files(root: Path) -> list[Path]:
    folder = root / RULES
    return sorted(folder.glob("*.md")) if folder.is_dir() else []


def living(root: Path) -> list[Path]:
    """The documents that state how things are now (not the records that only grow, the archive or a dated review)."""
    found = [root / AGENTS, root / "REQUIREMENTS.md", *sorted((root / "docs").rglob("*.md"))]
    output = []
    for path in found:
        name = path.relative_to(root).as_posix()
        if not path.is_file() or name in RECORDS or name.startswith("docs/archive/") or DATED.search(path.name):
            continue
        output.append(path)
    return output


def sizes(root: Path) -> list[str]:
    wrong = []
    for name, limit in LIMITS.items():
        text = _read(root, name)
        if not text:
            wrong.append(f"沒有 {name}")
        elif len(text) > limit:
            wrong.append(f"{name} {len(text):,} 字，上限 {limit:,}")
    found = rules_files(root)
    if not found:
        wrong.append(f"沒有 {RULES}/*.md（依工作類型分的規則）")
    for path in found:
        count = len(path.read_text(encoding="utf-8"))
        if count > RULES_LIMIT:
            wrong.append(f"{RULES}/{path.name} {count:,} 字，上限 {RULES_LIMIT:,}")
    return wrong


def handoff(root: Path) -> list[str]:
    """Only the fixed fields, in order; no dated block (a heading or bold line with a date or 現況／重點)."""
    text = _read(root, HANDOFF)
    if not text:
        return [f"沒有 {HANDOFF}"]
    wrong = []
    headings = re.findall(r"^(#{2,6})\s+(.+?)\s*$", text, re.MULTILINE)
    names = [title for level, title in headings]
    if any(level != "##" for level, title in headings) or names != list(HANDOFF_FIELDS):
        wrong.append(f"交接檔的欄位應該只有 {'、'.join(HANDOFF_FIELDS)}（依序），現在是 {'、'.join(names) or '沒有'}")
    for line in text.splitlines():
        head = re.match(r"^\s*(?:#+\s*|\*\*)(.{0,40})", line)
        if not head:
            continue
        words = head.group(1)
        if re.search(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}", words) or any(word in words for word in HANDOFF_WORDS):
            wrong.append(f"交接檔有日期區塊：{line.strip()[:40]}")
    return wrong


def definitions(root: Path) -> list[str]:
    """Each of T0, 截止日, 可以上線: one ``<!-- 定義：… -->`` marker, and no other living document with words that
    define it."""
    wrong = []
    pages = {path: path.read_text(encoding="utf-8") for path in living(root)}
    for term, patterns in DEFINITIONS.items():
        homes = [path for path, text in pages.items() if term in MARKER.findall(text)]
        if len(homes) != 1:
            wrong.append(f"「{term}」的定義標記應該剛好一處，現在 {len(homes)} 處"
                         + (f"（{'、'.join(path.relative_to(root).as_posix() for path in homes)}）" if homes else ""))
        for path, text in pages.items():
            if path in homes:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if any(re.search(pattern, line) for pattern in patterns):
                    wrong.append(f"「{term}」在 {path.relative_to(root).as_posix()}:{number} 又定義一次")
                    break
    return wrong


def decisions(root: Path) -> list[str]:
    """The decision numbers: each cited one exists, none listed twice, every one cited by AGENTS.md or a rules file;
    AGENTS.md names every rules file."""
    text = _read(root, DECISIONS)
    if not text:
        return [f"沒有 {DECISIONS}（決定登記簿）"]
    listed = DECISION_HEADING.findall(text)
    wrong = [f"決定 {number} 登記了 {listed.count(number)} 次" for number in sorted(set(listed)) if listed.count(number) > 1]
    known = set(listed)
    rules = [root / AGENTS, *rules_files(root)]
    cited: set[str] = set()
    for path in living(root):
        if path.relative_to(root).as_posix() == DECISIONS:
            continue
        found = set(DECISION.findall(path.read_text(encoding="utf-8")))
        wrong += [f"{path.relative_to(root).as_posix()} 引用的 {number} 不在決定登記簿" for number in sorted(found - known)]
        if path in rules:
            cited |= found
    lost = sorted(known - cited)
    if lost:
        wrong.append(f"決定登記簿裡沒有被 AGENTS.md 或 {RULES} 引用的 {len(lost)} 個：{'、'.join(lost[:8])}")
    agents = _read(root, AGENTS)
    wrong += [f"AGENTS.md 的索引沒有 {RULES}/{path.name}" for path in rules_files(root) if f"{RULES}/{path.name}" not in agents]
    return wrong


def _package_folders(root: Path) -> set[str]:
    package = root / "src" / "quant_platform"
    return {path.name for path in package.iterdir() if path.is_dir()} if package.is_dir() else set()


def named_paths(text: str) -> list[str]:
    """The file paths a document names in backticks (``scripts\\deploy.ps1``, ``research/daily.py``,
    ``docs/decisions.md:12``), without placeholders, globs or commands."""
    found = []
    for raw in CODE.findall(text):
        token = raw.strip().replace("\\", "/")
        token = re.sub(r":\d+(?:-\d+)?$", "", token).split("#")[0]
        if not token or any(mark in token for mark in "<>*{}…$% ") or "..." in token or ":" in token:
            continue
        if PATHLIKE.match(token) and ("/" in token or token.endswith(".md")):
            found.append(token.lstrip("./") if token.startswith("./") else token)
    return found


def paths(root: Path) -> list[str]:
    """Every repository file a living document names in backticks exists (data under instance/ and the like are not
    the repository's; ``research/daily.py`` is the package's)."""
    folders = _package_folders(root)
    top = {path.name for path in root.iterdir()}
    wrong = []
    for page in living(root):
        missing = []
        for token in named_paths(page.read_text(encoding="utf-8")):
            if token.startswith(DATA_PREFIXES):
                continue
            first = token.split("/")[0]
            candidates = []
            if first in top or "/" not in token:
                candidates.append(root / token)
            if "/" not in token:                       # a bare document name: at the root or in docs/
                candidates.append(root / "docs" / token)
            if first in folders:
                candidates.append(root / "src" / "quant_platform" / token)
            if first == "quant_platform":
                candidates.append(root / "src" / token)
            if candidates and not any(path.exists() for path in candidates):
                missing.append(token)
        if missing:
            unique = sorted(set(missing))
            wrong.append(f"{page.relative_to(root).as_posix()} 提到不存在的檔案 {len(unique)} 個：{'、'.join(unique[:6])}")
    return wrong


def _git() -> str | None:
    found = shutil.which("git")
    if found:
        return found
    fallback = Path(r"C:\Program Files\Git\cmd\git.exe")
    return str(fallback) if fallback.is_file() else None


def lag(root: Path, limit: int = LAG_LIMIT) -> list[str]:
    """The roadmap and the architecture: how many code commits (touching src/ or scripts/) came after the last one
    that changed them."""
    git = _git()
    if not git:
        return ["沒有 git，沒辦法看路線圖與架構落後幾個 commit"]
    wrong = []
    for name in LAGGING:
        try:
            last = subprocess.run([git, "log", "-1", "--format=%H", "--", name], cwd=root, capture_output=True,
                                  text=True, timeout=60, check=True).stdout.strip()
            if not last:
                wrong.append(f"{name} 不在版本庫裡")
                continue
            behind = int(subprocess.run([git, "rev-list", "--count", f"{last}..HEAD", "--", "src", "scripts"], cwd=root,
                                        capture_output=True, text=True, timeout=60, check=True).stdout.strip() or 0)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            wrong.append(f"沒辦法看 {name} 落後幾個 commit：{str(exc)[:80]}")
            continue
        if behind > limit:
            wrong.append(f"{name} 落後 {behind} 個改程式的 commit（上限 {limit}）")
    return wrong


def problems(root: str | Path, with_git: bool = True) -> list[str]:
    root = Path(root)
    found = [*sizes(root), *handoff(root), *definitions(root), *decisions(root), *paths(root)]
    return found + (lag(root) if with_git else [])
