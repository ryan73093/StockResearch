"""2026-11-12 (problem register P4; system review of 11-10, root cause 3): a model rule's input hash took every file of
its models — the monthly refits too. A refit to the cutoff changes only the scores of the sessions after the cutoff,
but the hash of every run ending at or before it changed with it: under engine 1.6.1, 5,743 rules were run twice or
more with the same result. The hash takes what a run ending on ``until`` can read."""
import hashlib
from datetime import date
from pathlib import Path

import numpy as np

from quant_platform.research import model


def _old(folder: Path) -> str:
    """The digest as it was before 2026-11-12 (every file), written out again to compare."""
    output = hashlib.sha256()
    monthly = sorted((folder / "monthly").glob("*.pkl")) if (folder / "monthly").is_dir() else []
    for path in (sorted(folder.glob("*.pkl")) if folder.is_dir() else []) + monthly:
        output.update(path.name.encode())
        output.update(hashlib.sha256(path.read_bytes()).digest())
    deep = [path for pattern in ("*.pt", "*.npz") for place in (folder, folder / "monthly") if place.is_dir()
            for path in place.glob(pattern)]
    for path in sorted(deep, key=lambda path: path.relative_to(folder).as_posix()):
        output.update(path.relative_to(folder).as_posix().encode())
        output.update(hashlib.sha256(path.read_bytes()).digest())
    return output.hexdigest()


def _scores(path: Path, days: list[str], value: float = 0.5) -> None:
    np.savez(path, days=np.array(days), symbols=np.array(["1101", "2330"]),
             scores=np.full((len(days), 2), value, dtype=np.float32))


def _tree(tmp_path: Path) -> Path:
    folder = tmp_path / "gbm-9"
    (folder / "monthly").mkdir(parents=True)
    for year in (2025, 2026):
        (folder / f"{year}.pkl").write_bytes(f"year {year}".encode())
    (folder / "monthly" / "2026-08-31.pkl").write_bytes(b"to 8/31")
    (folder / "monthly" / "2026-09-30.pkl").write_bytes(b"to 9/30")
    (folder / "meta.json").write_text("{}", encoding="utf-8")
    return folder


def test_the_results_registered_so_far_keep_their_hash(tmp_path):
    """Up to the end the pool runs to today (2026-09-30) the digest is the old one, byte for byte — changing it would
    run every model rule again for nothing; the new rule starts when the end next moves and everything runs anyway."""
    folder = _tree(tmp_path)
    _scores(folder / "deep.npz", ["2026-09-29", "2026-09-30", "2026-10-01"])
    (folder / "2026-0.pt").write_bytes(b"network")
    assert model.digest(folder) == _old(folder) == model.digest(folder, until=date(2026, 9, 30))
    assert model.digest(folder, until=date(2026, 10, 31)) != _old(folder)


def test_a_refit_to_the_runs_end_or_later_does_not_change_the_runs_hash(tmp_path):
    folder = _tree(tmp_path)
    until = date(2026, 10, 31)
    before = model.digest(folder, until=until)
    (folder / "monthly" / "2026-10-31.pkl").write_bytes(b"to 10/31")      # scores only the sessions after 10/31
    (folder / "monthly" / "2026-11-30.pkl").write_bytes(b"to 11/30")
    (folder / "2027.pkl").write_bytes(b"year 2027")                       # a year after the run's end
    assert model.digest(folder, until=until) == before
    (folder / "monthly" / "2026-09-30.pkl").write_bytes(b"to 9/30, again")  # read by October's sessions
    changed = model.digest(folder, until=until)
    assert changed != before
    (folder / "2026.pkl").write_bytes(b"year 2026, again")
    assert model.digest(folder, until=until) != changed


def test_a_deep_models_later_days_and_new_networks_do_not_change_the_hash_of_a_run_they_do_not_reach(tmp_path):
    folder = tmp_path / "dl-9"
    (folder / "monthly").mkdir(parents=True)
    until = date(2026, 10, 31)
    _scores(folder / "deep.npz", ["2026-10-29", "2026-10-30", "2026-11-02"])
    (folder / "2026-0.pt").write_bytes(b"october's fit")
    _scores(folder / "monthly" / "2026-09-30.npz", ["2026-10-01", "2026-10-30"])
    (folder / "monthly" / "2026-09-30-0.pt").write_bytes(b"cutoff network")
    before = model.digest(folder, until=until)
    # the nightly month refit: days after the run's end kept, this year's networks written again
    _scores(folder / "deep.npz", ["2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03"])
    (folder / "2026-0.pt").write_bytes(b"november's fit")
    _scores(folder / "monthly" / "2026-10-31.npz", ["2026-11-02"])
    (folder / "monthly" / "2026-10-31-0.pt").write_bytes(b"next cutoff")
    _scores(folder / "monthly" / "2026-09-30.npz", ["2026-10-01", "2026-10-30", "2026-11-02"])      # extended later
    assert model.digest(folder, until=until) == before
    _scores(folder / "deep.npz", ["2026-10-29", "2026-10-30", "2026-11-02", "2026-11-03"], value=0.7)   # a score it reads
    assert model.digest(folder, until=until) != before


def test_the_networks_count_when_the_kept_scores_stop_before_the_runs_end(tmp_path):
    """The sessions after the last kept day are scored with the saved networks when read (model_deep2.scores)."""
    folder = tmp_path / "dl-8"
    folder.mkdir()
    until = date(2026, 10, 31)
    _scores(folder / "deep.npz", ["2026-10-19", "2026-10-20"])             # fitted on 10/20: the scores stop there
    (folder / "2026-0.pt").write_bytes(b"the fit")
    before = model.digest(folder, until=until)
    (folder / "2026-0.pt").write_bytes(b"another fit")
    assert model.digest(folder, until=until) != before


def test_every_model_digest_of_a_rules_input_is_taken_to_the_runs_end():
    """The call sites: a digest taken without the end would go on changing with every refit."""
    import re

    root = Path(model.__file__).resolve().parent
    for name in ("daily.py", "consensus.py"):
        text = (root / name).read_text(encoding="utf-8")
        calls = re.findall(r"(?<![A-Za-z_.])(?:model_digest|digest)\(model_dir\([^\n]*", text)
        assert calls and all("until=" in call for call in calls), (name, [call for call in calls if "until=" not in call])
