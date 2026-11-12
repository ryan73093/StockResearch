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
    # 2026-11-12: the robots' and the veto model's digests too
    text = (root / "daily.py").read_text(encoding="utf-8")
    calls = [text[match.start():match.start() + 230] for match in re.finditer(r"(?:robot_digest|meta_digest)[(]", text)]
    assert len(calls) >= 7 and all("until=RECENT_END)" in call for call in calls), [call[:90] for call in calls]


def _robot(folder: Path, monthly: dict, weights: dict | None = None, trained_at: str = "2026-10-16T12:10:21+00:00") -> Path:
    import json

    folder.mkdir(parents=True, exist_ok=True)
    meta = {"version": "block-rl-9", "factors": ["a", "b"], "config": {"step": 20},
            "weights": weights or {"2026-09-04": [0.5, 0.5], "2026-10-05": [0.6, 0.4], "2026-11-03": [0.7, 0.3]},
            "yearly": {"2026": {"abstained": False}}, "trained_at": trained_at, "monthly": monthly}
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return folder


def _refit(weights: dict, trained_at: str = "2026-11-03T12:05:00+00:00") -> dict:
    return {"weights": weights, "members": None, "abstained": False, "held_robot": 0.01, "held_equal": 0.0,
            "held_from": "2025-06-02", "train_decisions": 900, "trained_at": trained_at}


def test_a_robots_refit_to_the_runs_end_does_not_change_the_runs_hash(tmp_path):
    """2026-11-12 (problem register P4): the robot's hash was its whole meta.json. A monthly refit writes
    ``monthly[<cutoff>]`` — the weights of the sessions after the cutoff — so each month's refit changed the hash of
    every run that ends at the cutoff and reads none of it."""
    from quant_platform.research import rl_timing

    until = date(2026, 10, 31)
    september = _refit({"2026-10-05": [0.55, 0.45]})
    folder = _robot(tmp_path / "block-rl-9", {"2026-09-30": september})
    whole = hashlib.sha256((folder / "meta.json").read_bytes()).hexdigest()
    # the results registered so far keep their hash (the pool's end today): the file as it was
    assert rl_timing.digest(folder) == whole == rl_timing.digest(folder, until=date(2026, 9, 30))
    before = rl_timing.digest(folder, until=until)
    assert before != whole
    # the refit to the run's end, the one after it, the earlier refit deciding more days as the data grows, trained again
    _robot(tmp_path / "block-rl-9", {
        "2026-09-30": _refit({"2026-10-05": [0.55, 0.45], "2026-11-03": [0.5, 0.5]}, trained_at="2026-12-01T22:40:00+00:00"),
        "2026-10-31": _refit({"2026-11-03": [0.2, 0.8]}), "2026-11-30": _refit({"2026-12-01": [0.1, 0.9]})},
        weights={"2026-09-04": [0.5, 0.5], "2026-10-05": [0.6, 0.4], "2026-11-03": [0.7, 0.3], "2026-12-01": [0.9, 0.1]},
        trained_at="2026-12-01T22:40:00+00:00")
    assert rl_timing.digest(folder, until=until) == before
    # a decision the run reads
    _robot(tmp_path / "block-rl-9", {"2026-09-30": _refit({"2026-10-05": [0.9, 0.1]})})
    assert rl_timing.digest(folder, until=until) != before
    _robot(tmp_path / "block-rl-9", {"2026-09-30": september}, weights={"2026-09-04": [0.4, 0.6], "2026-10-05": [0.6, 0.4]})
    assert rl_timing.digest(folder, until=until) != before
    assert rl_timing.digest(tmp_path / "none", until=until) == "missing"


def test_the_veto_models_hash_takes_the_files_a_run_reads(tmp_path):
    """The veto (meta-label) model's hash was its meta.json alone: the yearly classifiers and the monthly refits it
    scores with were not in it, so a classifier fitted again left every result that read it under an unchanged hash
    (the deep models' hole of 10/28, here still open). From the pool's next end it takes them — the refits whose cutoff
    is before the run's end; the refit to the end itself changes nothing."""
    from quant_platform.research import meta_label

    folder = tmp_path / "meta-9"
    (folder / "monthly").mkdir(parents=True)
    (folder / "meta.json").write_text('{"version": "meta-9"}', encoding="utf-8")
    for year in (2025, 2026):
        (folder / f"{year}.pkl").write_bytes(f"year {year}".encode())
    (folder / "monthly" / "2026-09-30.pkl").write_bytes(b"to 9/30")
    whole = hashlib.sha256((folder / "meta.json").read_bytes()).hexdigest()
    assert meta_label.digest(folder) == whole == meta_label.digest(folder, until=date(2026, 9, 30))
    until = date(2026, 10, 31)
    before = meta_label.digest(folder, until=until)
    assert before != whole
    (folder / "monthly" / "2026-10-31.pkl").write_bytes(b"to 10/31")           # scores the sessions after 10/31
    assert meta_label.digest(folder, until=until) == before
    (folder / "monthly" / "2026-09-30.pkl").write_bytes(b"to 9/30, fitted again")   # October's sessions read it
    changed = meta_label.digest(folder, until=until)
    assert changed != before
    (folder / "2026.pkl").write_bytes(b"year 2026, fitted again")
    assert meta_label.digest(folder, until=until) != changed
    assert meta_label.digest(tmp_path / "none", until=until) == "missing"
