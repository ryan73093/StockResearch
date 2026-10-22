"""2026-10-23 (八大行庫事件研究): an event is a day of net buying in the stock's own top 1% of its last 250 sessions."""
import numpy as np

from quant_platform.research import govbank_events


def test_an_event_is_a_day_far_above_the_stocks_own_past():
    net = np.zeros((2, 260))
    net[0, :250] = np.arange(250) % 10                  # 0..9 again and again
    net[0, 255] = 50.0                                   # far above: an event
    net[0, 256] = 5.0                                    # ordinary
    net[1, 255] = -50.0                                  # selling is never an event
    net[1, :100] = np.nan
    found = govbank_events.events(net)
    assert found[0, 255] and not found[0, 256] and not found[1].any()


def test_the_summary_gives_the_mean_and_t():
    item = govbank_events.summarize([0.01, 0.03, 0.02, 0.02])
    assert item["events"] == 4 and abs(item["mean"] - 0.02) < 1e-9 and item["t"] > 4
    assert govbank_events.summarize([0.1])["mean"] is None
