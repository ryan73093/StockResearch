import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from quant_platform.research.jobs import JobLog

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "slim_legacy_tables.py"


def load_script():
    spec = importlib.util.spec_from_file_location("slim_legacy_tables", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make(tmp_path, rows):
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE feature_values (id INTEGER PRIMARY KEY, value REAL)")
    connection.execute("CREATE TABLE market_bars (id INTEGER PRIMARY KEY, close REAL)")
    connection.executemany("INSERT INTO feature_values VALUES (?, ?)", [(i, i * 1.5) for i in range(1, rows + 1)])
    connection.execute("INSERT INTO market_bars VALUES (1, 100.0)")
    connection.commit()
    connection.close()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"tables": {"feature_values": {"rows": 5, "sha256": "x"}}}), encoding="utf-8")
    return database, manifest


def test_slim_empties_only_archived_tables_and_refuses_when_counts_changed(tmp_path):
    slim = load_script()
    database, manifest = make(tmp_path, rows=5)
    with JobLog(tmp_path / "research").start("瘦身", "x") as job:
        result = slim.slim(job, database, manifest, tables=("feature_values",))
    connection = sqlite3.connect(database)
    assert connection.execute("SELECT COUNT(*) FROM feature_values").fetchone()[0] == 0     # emptied, schema kept
    assert connection.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0] == 1        # not archived: untouched
    connection.close()
    assert result["rows"] == {"feature_values": 5}

    changed, manifest = make(tmp_path / "second", rows=6) if (tmp_path / "second").mkdir() is None else (None, None)
    with pytest.raises(SystemExit, match="封存後有變動"), JobLog(tmp_path / "research").start("瘦身", "x") as job:
        slim.slim(job, changed, manifest, tables=("feature_values",))
    connection = sqlite3.connect(changed)
    assert connection.execute("SELECT COUNT(*) FROM feature_values").fetchone()[0] == 6     # nothing deleted
    connection.close()
