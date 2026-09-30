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

    assert tracker.record(date(2026, 9, 30)) == []           # before the forward start
    first = tracker.record(date(2026, 10, 5))
    again = tracker.record(date(2026, 10, 5))
    tracker.record(date(2026, 10, 6))

    from quant_platform.research.spec import BASELINES

    assert {item["name"] for item in first} == {spec.name for spec in BASELINES.values()}
    assert len(first) == 4 and again == []                     # the four baselines, once per day
    dca = next(item for item in first if item["name"] == "定期定額基準")
    assert dca["contributed"] == 10_000 and dca["trades_today"][0]["shares"] == 99
    rows = tracker.summary()
    assert rows[0]["benchmark"] and rows[0]["sessions"] == 2 and rows[0]["date"] == "2026-10-06"
    assert all(row["excess"] is not None for row in rows if not row["benchmark"])
    assert FORWARD_START == date(2026, 10, 1)


def test_days_without_data_are_skipped(tmp_path):
    tracker = ForwardTracker(tmp_path)
    _dataset(tmp_path, date(2026, 10, 2))

    assert tracker.record(date(2026, 10, 5)) == []            # the dataset ends on 10-02
    assert not tracker.log_path.exists()
