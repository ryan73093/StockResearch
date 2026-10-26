"""2026-10-27: a module-level name imported again inside ``main`` makes it local to the whole function — every other
command reading it before that line failed (`broker_costs`, 10/26 23:18: the list's holdout and the board)."""
import ast
from pathlib import Path


def test_main_does_not_import_again_a_name_the_module_already_has():
    source = (Path(__file__).resolve().parents[1] / "src" / "quant_platform" / "research" / "__main__.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    module = {alias.asname or alias.name.split(".")[0] for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
              for alias in node.names}
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    again = [(alias.asname or alias.name, node.lineno) for node in ast.walk(main)
             if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names
             if (alias.asname or alias.name.split(".")[0]) in module]
    assert again == []
