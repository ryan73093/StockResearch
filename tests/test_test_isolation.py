import os
import subprocess
import sys
from pathlib import Path

import pytest

from quant_platform.database.engine import Database

ROOT = Path(__file__).resolve().parents[1]


def test_tests_cannot_open_the_production_database():
    with pytest.raises(RuntimeError, match="production database"):
        Database(f"sqlite:///{ROOT / 'instance' / 'quant_platform.db'}")


def test_importing_the_api_module_opens_no_database(tmp_path):
    database = tmp_path / "import.db"
    env = {**os.environ, "DATABASE_URL": f"sqlite:///{database.as_posix()}", "PYTHONPATH": str(ROOT / "src")}
    script = (
        "import pathlib, quant_platform.api.app as module\n"
        f"database = pathlib.Path({str(database)!r})\n"
        "assert not database.exists(), 'importing the module opened the database'\n"
        "assert type(module.app).__name__ == 'FastAPI'   # uvicorn's 'quant_platform.api.app:app' still works\n"
        "assert database.exists()\n"
    )
    result = subprocess.run([sys.executable, "-c", script], env=env, cwd=tmp_path, capture_output=True, text=True,
                            timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]
