"""2026-11-11 (使用者：D 要能確保後續文件開發後也要能維持文件的穩定、ai 要能讀對資料、省 token): the documents' guard.

The first group runs it on this repository — written first, it failed on the 11-11 documents (AGENTS.md 14,609
characters, no docs/rules, a handoff of dated blocks, T0 defined in five places, no decisions register, the roadmap 46
and the architecture 199 code commits behind). The second group shows each guard failing on a broken copy, so a
guard that cannot fail is not counted as one (使用者 2026-11-03：檢查要真的比對)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from quant_platform import docs_guard

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("guard", ["sizes", "handoff", "definitions", "decisions", "paths", "lag"])
def test_the_repository_documents_pass(guard):
    assert getattr(docs_guard, guard)(ROOT) == []


# --- each guard fails on a broken copy ------------------------------------------------------------
def _write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


HANDOFF_OK = "# 交接\n\n## 當前工作包\n\nD\n\n## 等使用者決定\n\n- 無\n\n## 下一步\n\n- B\n"


def _good(root: Path) -> None:
    _write(root, "AGENTS.md", "# 入口\n\n- 研究讀 `docs/rules/research.md`（D-001）。\n")
    _write(root, "docs/rules/research.md", "# 研究\n\n- 規則（D-001）。\n")
    _write(root, "docs/decisions.md", "# 決定\n\n## D-001 一律 100% 個股\n\n原文。\n")
    _write(root, "docs/development_handoff.md", HANDOFF_OK)
    _write(root, "docs/research/conclusions.md", "# 現行結論\n")
    _write(root, "docs/trading_rules.md", "<!-- 定義：T0 -->\n- T0：前向滿 60 個交易日。\n<!-- 定義：截止日 -->\n"
                                          "截止日＝兩個月前的月底。\n<!-- 定義：可以上線 -->\n可以上線＝核准之後。\n")
    _write(root, "REQUIREMENTS.md", "# 需求\n\n分級見 `docs/trading_rules.md`。\n")


def test_a_good_copy_passes(tmp_path):
    _good(tmp_path)
    assert docs_guard.problems(tmp_path, with_git=False) == []


def test_sizes_fail_past_the_limits(tmp_path):
    _good(tmp_path)
    _write(tmp_path, "AGENTS.md", "字" * 3001)
    _write(tmp_path, "docs/rules/ui.md", "字" * 4001)
    found = docs_guard.sizes(tmp_path)
    assert any("AGENTS.md 3,001 字" in item for item in found)
    assert any("docs/rules/ui.md 4,001 字" in item for item in found)
    (tmp_path / "docs" / "rules" / "ui.md").unlink()
    (tmp_path / "docs" / "rules" / "research.md").unlink()
    assert any("沒有 docs/rules" in item for item in docs_guard.sizes(tmp_path))


@pytest.mark.parametrize("extra", ["## 11/10 的重點\n\n- 舊的\n", "**現況（2026-10-29 05:00）**\n", "### 背景執行中\n"])
def test_the_handoff_fails_with_a_dated_block(tmp_path, extra):
    _good(tmp_path)
    _write(tmp_path, "docs/development_handoff.md", HANDOFF_OK + "\n" + extra)
    assert docs_guard.handoff(tmp_path)


def test_the_handoff_fails_without_its_fields_in_order(tmp_path):
    _good(tmp_path)
    _write(tmp_path, "docs/development_handoff.md", "# 交接\n\n## 下一步\n\n## 當前工作包\n")
    assert docs_guard.handoff(tmp_path)


def test_a_second_definition_fails(tmp_path):
    _good(tmp_path)
    _write(tmp_path, "REQUIREMENTS.md", "# 需求\n\n- 選手池分級：T0 候選＝全部門檻都過。\n")
    assert any("「T0」在 REQUIREMENTS.md:3" in item for item in docs_guard.definitions(tmp_path))
    _write(tmp_path, "REQUIREMENTS.md", "# 需求\n\n每個月以「兩個月前的月底」為截止日。\n")
    assert any("「截止日」在 REQUIREMENTS.md" in item for item in docs_guard.definitions(tmp_path))
    _write(tmp_path, "REQUIREMENTS.md", "# 需求\n\n前向模擬（至少 8 週）後核准。\n")
    assert any("「可以上線」在 REQUIREMENTS.md" in item for item in docs_guard.definitions(tmp_path))


def test_a_missing_or_doubled_marker_fails(tmp_path):
    _good(tmp_path)
    _write(tmp_path, "docs/user-guide.md", "<!-- 定義：T0 -->\n")
    assert any("「T0」的定義標記應該剛好一處，現在 2 處" in item for item in docs_guard.definitions(tmp_path))
    _write(tmp_path, "docs/user-guide.md", "")
    _write(tmp_path, "docs/trading_rules.md", "沒有標記\n")
    assert any("現在 0 處" in item for item in docs_guard.definitions(tmp_path))


def test_the_records_may_quote_old_definitions(tmp_path):
    _good(tmp_path)
    _write(tmp_path, "docs/decisions.md", "# 決定\n\n## D-001 原文\n\n- T0 候選＝2020-10 起贏 0050\n")
    _write(tmp_path, "docs/system_review_2026-11-10.md", "可以上線有四個版本：前向模擬至少 8 週。\n")
    assert docs_guard.definitions(tmp_path) == []


def test_decision_numbers_fail_when_missing_unused_or_unindexed(tmp_path):
    _good(tmp_path)
    _write(tmp_path, "docs/rules/data.md", "# 資料\n\n- 備份（D-009）。\n")
    found = docs_guard.decisions(tmp_path)
    assert any("D-009 不在決定登記簿" in item for item in found)
    assert any("索引沒有 docs/rules/data.md" in item for item in found)
    _write(tmp_path, "docs/rules/data.md", "# 資料\n")
    _write(tmp_path, "docs/decisions.md", "# 決定\n\n## D-001 甲\n\n## D-002 乙\n\n## D-002 乙\n")
    found = docs_guard.decisions(tmp_path)
    assert any("D-002 登記了 2 次" in item for item in found)
    assert any("沒有被 AGENTS.md 或 docs/rules 引用的 1 個：D-002" in item for item in found)
    (tmp_path / "docs" / "decisions.md").unlink()
    assert docs_guard.decisions(tmp_path) == ["沒有 docs/decisions.md（決定登記簿）"]


def test_a_named_file_that_does_not_exist_fails(tmp_path):
    _good(tmp_path)
    _write(tmp_path, "scripts/deploy.ps1", "")
    _write(tmp_path, "src/quant_platform/research/daily.py", "")
    _write(tmp_path, "docs/rules/services.md", "部署 `scripts\\deploy.ps1`；引擎 `research/daily.py:120`；資料 "
                                                "`instance/research/trials.jsonl`；指令 `python -m x status`；"
                                                "沒有的 `scripts/nothing.ps1`、`research/gone.py`。\n")
    _write(tmp_path, "AGENTS.md", "# 入口\n\n- `docs/rules/research.md`、`docs/rules/services.md`（D-001）。\n")
    found = docs_guard.paths(tmp_path)
    assert found == ["docs/rules/services.md 提到不存在的檔案 2 個：research/gone.py、scripts/nothing.ps1"]


def test_a_document_behind_the_code_fails(tmp_path):
    git = docs_guard._git()
    if not git:
        pytest.skip("no git")

    def run(*args):
        subprocess.run([git, *args], cwd=tmp_path, check=True, capture_output=True)

    run("init", "-q")
    for name in docs_guard.LAGGING:
        _write(tmp_path, name, "# doc\n")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "docs")
    for number in range(3):
        _write(tmp_path, f"src/m{number}.py", "x = 1\n")
        run("add", ".")
        run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", f"code {number}")
    _write(tmp_path, "notes.txt", "not code\n")
    run("add", ".")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "not code")
    assert docs_guard.lag(tmp_path, limit=3) == []
    found = docs_guard.lag(tmp_path, limit=2)
    assert found == [f"{name} 落後 3 個改程式的 commit（上限 2）" for name in docs_guard.LAGGING]
