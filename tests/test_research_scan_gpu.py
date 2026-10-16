"""2026-10-16 (使用者：先把 GPU 用起來): the search's first stage on the GPU gives the CPU's accounts."""
from datetime import date, timedelta

import numpy as np
import pytest

from quant_platform.research import scan
from quant_platform.research.costs import BROKERS

torch = pytest.importorskip("torch")
pytestmark = pytest.mark.skipif(not torch.cuda.is_available(), reason="no GPU")


def arrays(seed=0, symbols=90, sessions=160, signals=5):
    rng = np.random.default_rng(seed)
    percentiles = rng.random((2, signals, symbols, sessions)).astype(np.float16)
    percentiles[:, :, :8, :30] = np.nan                                   # not ranked yet
    percentiles[1, :, 40:, :] = np.nan                                    # outside the large caps
    percentiles[:, 2, :, :] = np.round(percentiles[:, 2, :, :] * 4) / 4   # many equal values: the tie-break matters
    liquidity = rng.integers(1, 6, (symbols, sessions)).astype(np.float32) * 1e7   # equal turnovers too
    days = [date(2016, 1, 4) + timedelta(days=index) for index in range(sessions)]
    return scan.Arrays(names=[f"s{index}" for index in range(signals)], percentiles=percentiles,
                       next_returns=rng.normal(0.0005, 0.02, (symbols, sessions)).astype(np.float32),
                       calm=rng.uniform(5, 50, (symbols, sessions)).astype(np.float32),
                       industry=rng.integers(0, 6, symbols).astype(np.int32),
                       weekly_days=np.array([day.weekday() == 0 for day in days]),
                       bench=rng.normal(0.0004, 0.01, sessions), days=days,
                       rising=(rng.random((symbols, sessions)) > 0.5).astype(np.uint8), liquidity=liquidity)


@pytest.mark.parametrize("universe,check,weighting,hold", [
    ("all", "daily", "equal", "trend"), ("all", "weekly", "inverse_vol", "trend"),
    ("large", "weekly", "equal", "days"), ("all", "daily", "inverse_vol", "days")])
def test_the_gpu_accounts_are_the_cpu_accounts(universe, check, weighting, hold):
    from quant_platform.research.scan_gpu import Device, accounts

    data = arrays()
    costs = BROKERS["cathay"].cost_model()
    items = [{"factors": factors, "universe": universe, "weighting": weighting, "check": check, "hold": hold}
             for factors in (["s0"], ["s2"], ["s1", "s2"], ["s2", "s3", "s4"], ["s0", "s1", "s3", "s4"], ["s4"])]
    returns, entries = accounts(Device(data), items, costs)
    for item, row, count in zip(items, returns, entries):
        _metrics, (expected, expected_entries) = scan.evaluate(data, item, costs)
        assert np.allclose(row, expected, atol=1e-7), item           # the CPU sums in float32 (NumPy 2), the GPU in float64
        assert count == expected_entries


def test_the_gpu_screen_writes_what_the_cpu_screen_writes(tmp_path):
    import json

    data = arrays(seed=3)
    folder = tmp_path / "arrays"
    folder.mkdir()
    np.save(folder / "percentiles.npy", data.percentiles)
    for name in ("next_returns", "calm", "industry", "weekly_days", "bench", "rising", "liquidity"):
        np.save(folder / f"{name}.npy", getattr(data, name))
    (folder / "meta.json").write_text(json.dumps({"names": data.names, "days": [day.isoformat() for day in data.days]}),
                                      encoding="utf-8")
    costs = BROKERS["cathay"].cost_model()
    items = [{"factors": factors, "universe": "all", "weighting": "equal", "check": check, "hold": "trend"}
             for factors in (["s0"], ["s1", "s2"]) for check in ("daily", "weekly")]
    on_gpu = scan.screen(folder, items, costs, 0, tmp_path / "gpu", gpu=True)
    on_cpu = scan.screen(folder, items, costs, 0, tmp_path / "cpu", gpu=False)
    key = lambda row: (tuple(row["factors"]), row["check"])          # noqa: E731
    assert sorted(on_gpu, key=key) == sorted(on_cpu, key=key)
    assert sum(len(np.load(path)) for path in (tmp_path / "gpu").glob("screen-*.excess.npy")) == len(items)
