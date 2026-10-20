"""2026-10-20: the streamed search (research/scan_large.py)."""
import json
from math import comb

import numpy as np

from quant_platform.research import scan, scan_large


def test_the_blocks_cover_every_candidate_once_in_a_fixed_order():
    names = [f"s{index}" for index in range(7)]
    made = [item for _number, block in scan_large.blocks(names, [2, 3], block=10) for item in block]
    assert len(made) == scan_large.total(names, [2, 3]) == (comb(7, 2) + comb(7, 3)) * 24
    keys = {(tuple(item["factors"]), item["universe"], item["weighting"], item["check"], item["hold"]) for item in made}
    assert len(keys) == len(made) and {item["hold"] for item in made} == {"days", "trend", "free"}
    for _number, block in scan_large.blocks(names, [2], block=10):          # a block shares one key (the GPU's group)
        assert len({(item["universe"], item["weighting"], item["check"], item["hold"]) for item in block}) == 1
    numbers = [number for number, _block in scan_large.blocks(names, [2, 3], block=10)]
    assert numbers == list(range(len(numbers)))


def test_the_stream_keeps_the_passing_samples_the_rest_and_goes_on_after_a_stop(monkeypatch, tmp_path):
    days = 260
    from datetime import date, timedelta

    arrays = type("A", (), {"days": [date(2016, 1, 1) + timedelta(days=i) for i in range(days)],
                            "bench": np.zeros(days - 1)})()
    monkeypatch.setattr(scan, "load_arrays", lambda folder: arrays)
    monkeypatch.setattr(scan_large, "SAMPLE_EVERY", 7)
    names = [f"s{index}" for index in range(5)]

    def accounts(chunk):                              # every candidate with s0 earns 0.1% a day, the others lose
        returns = np.array([[0.001 if "s0" in item["factors"] else -0.001] * (days - 1) for item in chunk])
        return returns, np.full(len(chunk), 10)
    calls = []

    def counted(chunk):
        calls.append(len(chunk))
        if len(calls) == 3:
            raise KeyboardInterrupt                   # a reboot in the middle
        return accounts(chunk)
    try:
        scan_large.screen_stream(tmp_path, names, [2], costs=None, out=tmp_path / "run", accounts=counted)
    except KeyboardInterrupt:
        pass
    stopped = json.loads((tmp_path / "run" / "progress.json").read_text(encoding="utf-8"))
    assert stopped["next_block"] == 2 and stopped["screened"] == sum(calls[:2])
    done = scan_large.screen_stream(tmp_path, names, [2], costs=None, out=tmp_path / "run", accounts=accounts)
    assert done["screened"] == scan_large.total(names, [2]) == comb(5, 2) * 24
    rows = [json.loads(line) for path in sorted((tmp_path / "run").glob("screen-s*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows and all("s0" in row["factors"] for row in rows) and done["passed"] == len(rows)
    samples = sum(len(np.load(path)) for path in (tmp_path / "run").glob("sample-*.excess.npy"))
    assert samples == len(range(0, done["screened"], 7))


def test_a_free_hold_is_named_and_has_no_minimum():
    rule = scan.to_rule({"factors": ["trend_200"], "universe": "all", "weighting": "equal", "check": "weekly",
                         "hold": "free"})
    assert rule.min_hold == 0 and rule.hold == "days" and "不設最短持有" in rule.name
    named = scan.to_rule({"factors": ["trend_200"], "universe": "all", "weighting": "equal", "check": "weekly",
                          "hold": "days", "named_hold": True})
    assert named.min_hold == scan.MIN_HOLD and "持有 20 天" in named.name
