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
