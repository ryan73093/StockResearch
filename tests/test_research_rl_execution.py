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


def test_the_forward_test_learns_before_the_start_and_judges_the_close_buys_after_it():
    """2026-10-28 (RL v8): the model learns only days before the start; only forward buys filled at the close count;
    a strong first half hour lifts the close, so buying at 13:00 gains, per fill and per day."""
    rng = np.random.default_rng(6)
    rows = []
    for number in range(3000):
        day = f"2025-{1 + number % 12:02d}-{1 + number % 28:02d}" if number < 2400 else f"2026-09-{1 + number % 28:02d}"
        first = rng.normal(0, 0.01)
        thirteen = 100 * (1 + rng.normal(0, 0.01))
        rows.append({"day": day, "year": int(day[:4]), "code": str(1000 + number), "open": 100.0, "half": 100 * (1 + first),
                     "thirteen": thirteen, "close": thirteen * (1 + 0.5 * first), "gap": rng.normal(0, 0.01),
                     "yesterday": rng.normal(0, 0.01), "volatility": 0.02, "momentum": 0.0})
    frame = pd.DataFrame(rows)
    later = frame[frame["day"] >= "2026-09-01"]
    buys = [{"code": code, "filled": day, "price": close} for code, day, close in zip(later["code"], later["day"], later["close"])]
    buys[0]["price"] = buys[0]["price"] * 1.05                 # filled at the open, not the close: left out
    found = rl_execution.forward_test(frame, buys)
    assert found["train"] == 2400 and found["test"] == 599 and found["days"] == 28
    assert found["robot"]["mean"] > 0.0015 and found["robot_by_day"]["t"] > 2 and found["passed"]
    assert 0.3 < found["share_1300"] < 0.7
