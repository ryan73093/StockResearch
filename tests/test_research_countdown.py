"""2026-11-22 (使用者 D-059：距三十號，我又不知道要多久，只剩八天，沒有一個可以上線的): the wait for the first T0, counted in sessions."""
from datetime import date

from quant_platform.research.countdown import countdown, day_label


def _weekdays(day):
    return day.weekday() < 5            # a calendar without holidays: Monday to Friday


def test_the_wait_is_counted_in_sessions_with_their_dates():
    found = countdown(date(2026, 11, 22), date(2026, 11, 30), date(2026, 11, 20), 54, 60, _weekdays, not_behind_both=324)
    assert found["days"] == 8 and found["sessions_left"] == 6                   # 11/22 is a Sunday: 8 days, 6 sessions
    assert found["dates"] == ["11/23（一）", "11/24（二）", "11/25（三）", "11/26（四）", "11/27（五）", "11/30（一）"]
    assert found["first_label"] == "11/30（一）" and found["today_label"] == "11/22（日）" and found["through_label"] == "11/20（五）"
    assert found["sessions"] == 54 and found["needed"] == 60 and found["not_behind_both"] == 324 and found["t0"] == 0
    assert found["sessions_left"] == found["needed"] - found["sessions"] and found["reached"] is False


def test_a_holiday_is_not_a_session():
    closed = date(2026, 11, 25)
    found = countdown(date(2026, 11, 22), date(2026, 11, 30), date(2026, 11, 20), 54, 60, lambda day: _weekdays(day) and day != closed)
    assert "11/25（三）" not in found["dates"] and found["sessions_left"] == 5


def test_the_day_has_come_or_gone():
    on_the_day = countdown(date(2026, 11, 30), date(2026, 11, 30), date(2026, 11, 30), 60, 60, _weekdays, t0_rules=3)
    assert on_the_day["sessions_left"] == 0 and on_the_day["days"] == 0 and on_the_day["reached"] is True and on_the_day["t0"] == 3
    after = countdown(date(2026, 12, 3), date(2026, 11, 30), date(2026, 12, 2), 62, 60, _weekdays)
    assert after["days"] < 0 and after["sessions_left"] == 0 and after["reached"] is True


def test_no_forward_record_counts_from_today():
    found = countdown(date(2026, 11, 22), date(2026, 11, 24), None, 0, 60, _weekdays)
    assert found["dates"] == ["11/23（一）", "11/24（二）"] and found["through_label"] == ""
    assert day_label(date(2026, 12, 6)) == "12/6（日）"
