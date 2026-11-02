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
    assert limits_check.OFFICIAL_LIMITS is True                        # on with engine 1.6.0, 2026-10-31
    monkeypatch.setattr(limits_check, "OFFICIAL_LIMITS", False)       # the switch off: the engine's own reckoning
    plain = load_stock_data(tmp_path, 2021, 2021)
    before = stock_fingerprint(tmp_path, 2021, 2021)
    assert date(2021, 5, 14) not in (plain.notes["limits"].get("3092.TW") or {})
    monkeypatch.setattr(limits_check, "OFFICIAL_LIMITS", True)
    found = load_stock_data(tmp_path, 2021, 2021)
    assert found.notes["limits"]["3092.TW"][date(2021, 5, 14)] == (9.2, 11.2)
    assert stock_fingerprint(tmp_path, 2021, 2021) != before
    assert limits_check.read_overrides(tmp_path, 2022) == {}


def test_the_exchange_limits_take_the_whole_market_days_and_the_overrides_know_their_day(tmp_path):
    """2026-11-02: the exchange's limits had stopped on 10-22 (one download); from then a session a request for every
    stock — the day's file replaces the stocks' rows of that day; the overrides record the newest exchange day."""
    import gzip
    import json

    from quant_platform.research import limits_check

    raw = tmp_path / "raw" / "finmind" / "TaiwanStockPriceLimit"
    (raw / "days").mkdir(parents=True)
    (raw / "2330.json.gz").write_bytes(gzip.compress(json.dumps({"data": [
        {"date": "2026-10-21", "stock_id": "2330", "limit_down": 900.0, "limit_up": 1100.0},
        {"date": "2026-10-22", "stock_id": "2330", "limit_down": 1.0, "limit_up": 2.0}]}).encode("utf-8")))
    (raw / "days" / "2026-10-22.json.gz").write_bytes(gzip.compress(json.dumps({"data": [
        {"date": "2026-10-22", "stock_id": "2330", "limit_down": 910.0, "limit_up": 1110.0},
        {"date": "2026-10-22", "stock_id": "2317", "limit_down": 0.0, "limit_up": 0.0}]}).encode("utf-8")))
    found = limits_check.official(tmp_path, ["2330", "2317"])
    assert found == {"2330": {"2026-10-21": (900.0, 1100.0), "2026-10-22": (910.0, 1110.0)}}
    assert not limits_check.overrides_current(tmp_path)
    marker = (tmp_path / limits_check.OVERRIDES).with_suffix(".json")
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"exchange_through": "2026-10-22"}), encoding="utf-8")
    assert limits_check.overrides_current(tmp_path)
