"""2026-10-21 (使用者：盤中買會不會比較好): the minute-bar prices and the gains against the close by hand."""
import pytest

from quant_platform.research import intraday


def bar(minute, open_, high, low, close, volume):
    return {"minute": minute, "open": open_, "high": high, "low": low, "close": close, "volume": volume}


def test_the_points_of_a_session_by_hand():
    rows = [bar("09:00:00", 100, 102, 99, 101, 10), bar("09:29:00", 101, 101, 101, 101, 30),
            bar("10:00:00", 103, 103, 103, 103, 20), bar("13:24:00", 104, 104, 104, 104, 10),
            bar("13:30:00", 105, 105, 105, 105, 50), bar("14:30:00", 106, 106, 106, 106, 1)]
    found = intraday.points(rows)
    assert found["open"] == 100 and found["close"] == 105 and found["before_close"] == 104
    assert found["first30"] == pytest.approx((((102 + 99 + 101) / 3) * 10 + 101 * 30) / 40)
    assert found["day"] == pytest.approx((((102 + 99 + 101) / 3) * 10 + 101 * 30 + 103 * 20 + 104 * 10) / 70)
    assert intraday.points([]) is None


def test_a_buy_gains_below_the_close_and_a_sell_above_it():
    rows = [{"code": "1101", "filled": "2024-01-02", "side": "buy"}, {"code": "1102", "filled": "2024-01-02", "side": "sell"},
            {"code": "1103", "filled": "2024-01-02", "side": "buy"}, {"code": "1104", "filled": "2024-01-02", "side": "sell"}]
    prices = {("1101", "2024-01-02"): {"open": 99.0, "close": 100.0}, ("1102", "2024-01-02"): {"open": 101.0, "close": 100.0},
              ("1103", "2024-01-02"): {"open": 98.0, "close": 100.0}, ("1104", "2024-01-02"): {"open": 102.0, "close": 100.0}}
    found = intraday.gains(rows, prices)["open"]
    assert found["buy"]["mean"] == pytest.approx((100 / 99 - 1 + 100 / 98 - 1) / 2, abs=1e-6)
    assert found["sell"]["mean"] == pytest.approx(0.015) and found["both"]["beat"] == 1.0 and found["worth"] is True


def test_the_sample_is_half_buys_half_sells_and_fixed():
    rows = [{"trial": 1, "code": str(1000 + n), "filled": "2024-01-02", "side": "buy" if n % 3 else "sell"} for n in range(300)]
    first, again = intraday.sample(rows, 100), intraday.sample(rows, 100)
    assert first == again and sum(row["side"] == "buy" for row in first) == 50 and len(first) == 100


def test_the_opening_auction_and_the_first_odd_lot_match_by_hand():
    rows = [bar("09:00:00", 100, 100, 100, 100, 50), bar("09:05:00", 100, 101, 99, 99, 10),
            bar("09:10:00", 99, 99, 98, 98.5, 10), bar("09:30:00", 98, 98, 98, 98, 20), bar("13:30:00", 97, 97, 97, 97, 60)]
    found = intraday.opening(rows)
    assert found["open"] == 100 and found["auction"] == 50 * 1000 * 100 and found["odd_first"] == 98.5
    assert found["close"] == 97 and found["day"] == pytest.approx(1000 * (50 * 100 + 10 * (101 + 99 + 99) / 3
                                                                          + 10 * (99 + 98 + 98.5) / 3 + 20 * 98 + 60 * 97))
    assert intraday.opening([bar("09:01:00", 1, 1, 1, 1, 1)]) is None          # no opening auction bar
