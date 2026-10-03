import importlib.util
import json
import sqlite3
from pathlib import Path

import pyarrow.parquet as pq

from quant_platform.research.jobs import JobLog

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "archive_legacy_tables.py"


def load_script():
    spec = importlib.util.spec_from_file_location("archive_legacy_tables", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_archive_writes_every_row_in_chunks_with_stable_types(tmp_path, monkeypatch):
    database = tmp_path / "legacy.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE market_bars (id INTEGER PRIMARY KEY, symbol VARCHAR(20), close NUMERIC, note TEXT)")
    rows = [(i, f"{1101 + i % 3}", 10.5 + i, None if i % 2 else f"n{i}") for i in range(1, 8)]
    rows.append((8, "2330", "not a number", 7))          # SQLite lets a column hold another type
    connection.executemany("INSERT INTO market_bars VALUES (?, ?, ?, ?)", rows)
    connection.commit()
    connection.close()
    archive = load_script()
    monkeypatch.setattr(archive, "DATABASE", database)
    monkeypatch.setattr(archive, "OUT", tmp_path / "out")
    monkeypatch.setattr(archive, "CHUNK", 3)
    with JobLog(tmp_path / "research").start("封存", "x") as job:
        manifest = archive.archive(job)
    item = manifest["tables"]["market_bars"]
    assert item["rows"] == 8 == item["count_at_end"] and item["sha256"]
    table = pq.read_table(tmp_path / "out" / "market_bars.parquet")
    assert table.num_rows == 8 and table.column("_rowid").to_pylist() == list(range(1, 9))
    closes = table.column("close").to_pylist()
    assert closes[0] == 11.5 and closes[-1] is None                     # the odd value does not break the file
    assert table.column("note").to_pylist()[-1] == "7"
    assert json.loads((tmp_path / "out" / "manifest.json").read_text(encoding="utf-8"))["finished_at"]
    assert JobLog(tmp_path / "research").jobs()[0]["status"] == "done"
