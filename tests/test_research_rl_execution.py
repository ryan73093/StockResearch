"""2026-10-28 (research_method §7「RL 第六版：成交執行」): fill after the open or at the close, learned by year."""
import numpy as np
import pandas as pd

from quant_platform.research import rl_execution


def test_a_gap_that_fades_is_sold_at_once_and_bought_at_the_close():
    rng = np.random.default_rng(3)
    days = 3000
    gap = rng.normal(0, 0.02, days)
    close = 100.0
    minute = close * (1 + gap * 0.5)             # gaps fade by half during the day: a gap up sells best at once
    frame = pd.DataFrame({"year": rng.choice([2021, 2022, 2023, 2024], days), "day": "2024-01-02", "gap": gap,
                          "yesterday": rng.normal(0, 0.01, days), "volatility": rng.random(days) * 0.02,
                          "momentum": rng.normal(0, 0.05, days), "open": minute, "minute": minute,
                          "close": np.full(days, close)})
    choices = rl_execution.walk_forward(rl_execution.gains(frame))
    sells = choices[choices["side"] == "sell"]
    assert set(choices["year"]) == {2023, 2024}
    assert sells["robot"].mean() > 0 and (sells["robot"] >= 0).mean() > 0.99      # sold at once after gaps up
    found = rl_execution.summary(choices)
    assert found["passed"] and found["both"]["lead"]["mean"] > 0


def test_odd_lots_sell_early_after_a_weak_start_and_buy_at_13_after_a_strong_first_half_hour():
    """2026-10-28 (RL v7): a weak start keeps falling (sell at 09:10), a strong one keeps rising (sell at the close);
    a strong first half hour lifts the last half hour (buy at 13:00, before it)."""
    rng = np.random.default_rng(4)
    days = 4000
    start = rng.normal(0, 0.01, days)                       # open to 09:10
    first = rng.normal(0, 0.01, days)                       # open to 09:30
    open_ = np.full(days, 100.0)
    ten = open_ * (1 + start)
    half = open_ * (1 + first)
    thirteen = ten * (1 + start)                            # the start carries on to 13:00
    close = thirteen * (1 + 0.5 * first)                    # the first half hour carries to the close
    frame = pd.DataFrame({"year": rng.choice([2021, 2022, 2023, 2024], days), "day": "2024-01-02",
                          "gap": rng.normal(0, 0.01, days), "yesterday": rng.normal(0, 0.01, days),
                          "volatility": rng.random(days) * 0.02, "momentum": rng.normal(0, 0.05, days),
                          "open": open_, "minute": ten, "ten": ten, "half": half, "thirteen": thirteen, "close": close})
    choices = rl_execution.multi(frame)
    found = rl_execution.summary(choices)
    sells, buys = choices[choices["side"] == "sell"], choices[choices["side"] == "buy"]
    assert set(choices["year"]) == {2023, 2024}
    assert sells["robot"].mean() > 0.003 and buys["robot"].mean() > 0.001
    assert found["passed"] and found["sell"]["always"]["close"]["count"] == len(sells)
    assert set(found["sell"]["choices"]) <= {"09:10", "13:00", "close"}
