"""2026-10-19 (使用者：評估專案可能失敗的原因並排除): the second copy on another disk."""
import os
import zipfile
from datetime import datetime

from quant_platform.research.mirror import mirror, status


def build(instance):
    research = instance / "research"
    (research / "reports").mkdir(parents=True)
    (research / "scans" / "x").mkdir(parents=True)
    (research / "forward" / "stocks").mkdir(parents=True)
    (research / "trials.jsonl").write_text('{"trial_id": 1}\n', encoding="utf-8")
    (research / "reports" / "a.json").write_text("{}", encoding="utf-8")
    (research / "scans" / "x" / "big.npy").write_bytes(b"0" * 100)
    (research / "forward" / "stocks" / "log.jsonl").write_text("{}\n", encoding="utf-8")
    daily = instance / "backups" / "daily"
    daily.mkdir(parents=True)
    for day in range(1, 10):
        (daily / f"quant_platform-202610{day:02d}-030000.db").write_bytes(b"db")


def test_the_mirror_copies_what_cannot_be_rebuilt_and_only_what_changed(tmp_path):
    instance, target = tmp_path / "instance", tmp_path / "other-disk"
    build(instance)
    first = mirror(instance, target, datetime(2026, 10, 19, 3, 30))
    assert first["copied"] == 3 and (target / "research" / "trials.jsonl").is_file()
    assert not (target / "research" / "scans").exists()                     # the search caches are rebuilt, not copied
    assert first["database"] == "quant_platform-20261009-030000.db" and (target / "db" / first["database"]).is_file()
    with zipfile.ZipFile(target / "snapshots" / "20261019.zip") as bundle:
        assert set(bundle.namelist()) == {"trials.jsonl", "forward/stocks/log.jsonl"}
    again = mirror(instance, target, datetime(2026, 10, 20, 3, 30))
    assert again["copied"] == 0                                              # nothing changed, nothing copied
    (instance / "research" / "trials.jsonl").write_text('{"trial_id": 1}\n{"trial_id": 2}\n', encoding="utf-8")
    (instance / "research" / "reports" / "a.json").unlink()                 # lost here …
    third = mirror(instance, target, datetime(2026, 10, 21, 3, 30))
    assert third["copied"] == 1 and (target / "research" / "reports" / "a.json").is_file()   # … still there
    assert status(target)["copied"] == 1 and status("") is None


def test_old_databases_and_snapshots_are_pruned(tmp_path):
    instance, target = tmp_path / "instance", tmp_path / "other-disk"
    build(instance)
    daily = instance / "backups" / "daily"
    for day in range(10, 20):
        (daily / f"quant_platform-202610{day:02d}-030000.db").write_bytes(b"db")
        mirror(instance, target, datetime(2026, 10, day, 3, 30))
    assert len(list((target / "db").glob("*.db"))) == 7
    for day in range(1, 32):
        mirror(instance, target, datetime(2026, 12, day, 3, 30))
    assert len(list((target / "snapshots").glob("*.zip"))) == 30
    assert not list(target.rglob("*.partial")) and os.path.isfile(target / "mirror.json")
