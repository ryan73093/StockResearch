from datetime import date, timedelta

from quant_platform.research.forward import FORWARD_START, ForwardTracker
from quant_platform.research.history.dataset import write_parquet
from quant_platform.research.history.official import DailyRow


def _sessions(end):
    days, day = [], date(2026, 9, 1)
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _dataset(base, end):
    for key, price in (("0050", 100.0), ("00679B", 25.0), ("TAIEX", 20_000.0)):
        write_parquet(
            [DailyRow(day, price, price, price, price, source="test") for day in _sessions(end)],
            base / "history" / "daily" / f"{key}.parquet",
        )


def test_records_are_appended_once_per_day_and_summarised(tmp_path):
    tracker = ForwardTracker(tmp_path)
    _dataset(tmp_path, date(2026, 10, 6))

    # 2026-10-17 (使用者：同一頁不能有兩個起點): from the month's rolling start (October: 09-01), every session
    first = tracker.record(date(2026, 10, 5))
    again = tracker.record(date(2026, 10, 5))
    tracker.record(date(2026, 10, 6))

    from quant_platform.research.spec import BASELINES

    assert {item["name"] for item in first} == {spec.name for spec in BASELINES.values()}
    assert len(first) == 4 * 25 and again == []                # the four baselines, 09-01 to 10-05, once per day
    assert {item["since"] for item in first} == {"2026-09-01"} and min(item["date"] for item in first) == "2026-09-01"
    dca = next(item for item in first if item["name"] == "定期定額基準" and item["date"] == "2026-09-07")
    assert dca["contributed"] == 10_000 and dca["trades_today"][0]["shares"] == 99   # the 5th was a Saturday
    rows = tracker.summary(date(2026, 10, 6))
    assert rows[0]["benchmark"] and rows[0]["sessions"] == 26 and rows[0]["date"] == "2026-10-06"
    assert all(row["excess"] is not None for row in rows if not row["benchmark"])
    assert tracker.summary(date(2026, 11, 2)) == []            # November starts on 10-01: nothing recorded from there yet
    assert FORWARD_START == date(2026, 10, 1)


def test_days_without_data_are_skipped(tmp_path):
    tracker = ForwardTracker(tmp_path)
    _dataset(tmp_path, date(2026, 10, 2))

    written = tracker.record(date(2026, 10, 5))               # the dataset ends on 10-02
    assert max(item["date"] for item in written) == "2026-10-02"
