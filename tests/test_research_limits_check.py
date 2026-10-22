"""2026-10-21 (使用者 2026-10-14：確實照當時的台股規則): the engine's limits against the exchange's, by hand."""
from datetime import date

import numpy as np

from quant_platform.research import limits_check


def test_the_limits_and_locks_that_differ_are_counted():
    sessions = [date(2024, 1, 2), date(2024, 1, 3)]
    close = np.array([[110.0, 100.0]])
    up = np.array([[110.0, 110.0]])
    down = np.array([[90.0, 90.5]])
    exchange = {"1101": {"2024-01-02": (90.0, 110.0), "2024-01-03": (90.0, 110.0)}}
    found = limits_check.compare(sessions, ["1101.TW"], close, up, down, exchange, date(2024, 1, 1))
    assert found["compared"] == 2 and found["limits_differ"] == 1 and found["locks_differ"] == 0
    assert found["locks"] == {"up/up": 1}
    # the engine did not call the first day locked though the exchange's limit says it closed there
    found = limits_check.compare(sessions, ["1101.TW"], close, up, down, exchange, date(2024, 1, 1),
                                 (np.array([[False, False]]), np.array([[False, False]])))
    assert found["locks_differ"] == 1 and found["examples"][0]["exchange_lock"] == "up"


def test_the_overrides_are_the_days_the_exchange_differs_or_the_engine_has_none():
    """Engine 1.6.0 (2026-10-22): only the stock-days to override are kept."""
    import numpy as np

    sessions = [date(2024, 1, day) for day in (1, 2, 3, 4)]
    close = np.array([[10.0, 10.5, 11.0, np.nan]])
    up = np.array([[11.0, 11.0, 11.5, 11.0]])
    down = np.array([[9.0, np.nan, 9.5, 9.0]])
    exchange = {"1101": {"2024-01-01": (9.0, 11.0), "2024-01-02": (9.0, 11.0), "2024-01-03": (9.9, 12.1),
                         "2024-01-04": (9.0, 11.0)}}
    rows = limits_check.overrides(sessions, ["1101.TW"], close, up, down, exchange, date(2024, 1, 2))
    # the 1st is before the start; the 2nd: the engine has no limit-down; the 3rd differs; the 4th did not trade
    assert [(row["date"], row["low"], row["high"]) for row in rows] == [(date(2024, 1, 2), 9.0, 11.0),
                                                                        (date(2024, 1, 3), 9.9, 12.1)]


def test_the_load_reads_the_overrides_only_with_the_switch_on(tmp_path, monkeypatch):
    import pandas as pd

    from test_research_tpex import setup

    from quant_platform.research.stock_rules import load_stock_data, stock_fingerprint

    setup(tmp_path)
    (tmp_path / "limits").mkdir()
    pd.DataFrame([{"symbol": "3092.TW", "date": date(2021, 5, 14), "low": 9.2, "high": 11.2}]).to_parquet(
        tmp_path / "limits" / "official_overrides.parquet", index=False)
    assert limits_check.OFFICIAL_LIMITS is False                       # on with engine 1.6.0, 2026-10-31
    plain = load_stock_data(tmp_path, 2021, 2021)
    before = stock_fingerprint(tmp_path, 2021, 2021)
    assert date(2021, 5, 14) not in (plain.notes["limits"].get("3092.TW") or {})
    monkeypatch.setattr(limits_check, "OFFICIAL_LIMITS", True)
    found = load_stock_data(tmp_path, 2021, 2021)
    assert found.notes["limits"]["3092.TW"][date(2021, 5, 14)] == (9.2, 11.2)
    assert stock_fingerprint(tmp_path, 2021, 2021) != before
    assert limits_check.read_overrides(tmp_path, 2022) == {}
