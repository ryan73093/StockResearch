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
