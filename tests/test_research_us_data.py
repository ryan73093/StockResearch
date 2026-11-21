"""The US side of the research data (2026-11-21): bars, market series, vintages, and the readings made of them."""
import gzip
import json
from datetime import date, datetime, timedelta

from quant_platform.research import market_signals
from quant_platform.research.history import us


def _chart(days: list[date], closes: list[float | None]) -> bytes:
    stamps = [int(datetime(day.year, day.month, day.day, 16, 0, tzinfo=us.NEW_YORK).timestamp()) for day in days]
    return json.dumps({"chart": {"result": [{"timestamp": stamps, "indicators": {
        "quote": [{"close": closes, "volume": [100.0] * len(days)}],
        "adjclose": [{"adjclose": [None if close is None else close * 0.9 for close in closes]}]}}]}}).encode("utf-8")


def test_bars_are_keyed_by_the_us_session_date_and_skip_days_without_a_close():
    days = [date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)]
    found = us.bars(json.loads(_chart(days, [10.0, None, 12.0])))
    assert sorted(found) == [date(2026, 11, 18), date(2026, 11, 20)]
    assert found[date(2026, 11, 20)] == {"close": 12.0, "adjusted": 12.0 * 0.9, "volume": 100.0}


def test_a_symbol_that_answers_fewer_days_keeps_its_last_file(tmp_path):
    days = [date(2026, 11, 17) + timedelta(days=number) for number in range(4)]
    full = _chart(days, [1.0, 2.0, 3.0, 4.0])
    counts = us.fetch_prices(tmp_path, get=lambda url: full, pause=0)
    assert counts["NVDA"] == 4
    short = _chart(days[:2], [1.0, 2.0])
    assert us.fetch_prices(tmp_path, get=lambda url: short, pause=0) == {}
    assert len(us.prices(tmp_path)["NVDA"]) == 4

    def broken(url):
        raise OSError("no answer")
    assert us.fetch_prices(tmp_path, get=broken, pause=0) == {}
    assert len(us.prices(tmp_path)["NVDA"]) == 4


def test_market_series_are_dated_by_their_publication(tmp_path):
    text = "observation_date,DGS10\n2026-11-18,4.10\n2026-11-19,.\n2026-11-20,4.20\n"
    assert us.parse_graph(text) == {date(2026, 11, 18): 4.10, date(2026, 11, 20): 4.20}
    us.fetch_daily(tmp_path, get=lambda url: text.replace("DGS10", url.split("id=")[1].split("&")[0]).encode("utf-8"), pause=0)
    found = us.daily(tmp_path)
    assert found["DGS10"] == {date(2026, 11, 19): 4.10, date(2026, 11, 21): 4.20}          # one day after its date
    assert min(found["NFCI"]) == date(2026, 11, 24)                                          # six days


def test_vintages_keep_what_was_published_each_time(tmp_path):
    html = '<option value="lin">x</option><option value="2011-12-02">a</option><option value="2012-01-06">b</option>' \
           '<option value="2012-02-03">c</option>'
    assert us.vintage_dates(html) == ["2011-12-02", "2012-01-06", "2012-02-03"]
    table = ("observation_date,PAYEMS_20111202,PAYEMS_20120106,PAYEMS_20120203\n"
             "2009-12-01,1,1,1\n2011-11-01,100,110,115\n2011-12-01,,200,190\n2012-01-01,,,300\n")
    asked = []

    def get(url):
        asked.append(url)
        return html.encode("utf-8") if "downloaddata" in url else table.encode("utf-8")
    counts = us.fetch_revised(tmp_path, get=get, pause=0, only=("PAYEMS",))
    assert counts == {"PAYEMS": 3}
    vintages = us.revised(tmp_path)["PAYEMS"]
    assert vintages["2012-01-06"] == {"2011-11-01": 110.0, "2011-12-01": 200.0}             # 2009 is before the window
    # as published by a day: the December figure is 200 until the February revision
    assert us.known(vintages, date(2012, 1, 20))["2011-12-01"] == 200.0
    assert us.known(vintages, "2012-02-03")["2011-12-01"] == 190.0
    assert us.known(vintages, "2011-01-01") == {}
    # the second run asks only for the page: every vintage is kept
    asked.clear()
    us.fetch_revised(tmp_path, get=get, pause=0, only=("PAYEMS",))
    assert len(asked) == 1


def test_the_fed_servers_are_asked_without_a_client_name_of_our_own(monkeypatch):
    seen = []

    class Answer:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b""

    def fake(request, timeout):
        seen.append(dict(request.header_items()))
        return Answer()
    monkeypatch.setattr(us.urllib.request, "urlopen", fake)
    us._get("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10")
    us._get("https://query1.finance.yahoo.com/v8/finance/chart/NVDA")
    assert "User-agent" not in seen[0] and seen[1]["User-agent"] == us.USER_AGENT


def _write_vintages(folder, series, vintages):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{series}.json.gz").write_bytes(gzip.compress(json.dumps({"vintages": vintages}).encode("utf-8")))


def test_a_revised_statistic_is_read_as_published_and_carried_to_every_weekday(tmp_path):
    # consumer prices: 2% a year as first published on 2024-02-13; the January figure revised up on 2024-03-12
    months = [f"{year}-{month:02d}-01" for year in (2023, 2024) for month in range(1, 13)]
    first = {month: 100.0 * (1.02 if month >= "2024" else 1.0) for month in months if month <= "2024-01-01"}
    second = dict(first, **{"2024-01-01": 103.0, "2024-02-01": 104.0})
    _write_vintages(tmp_path / "raw" / "alfred", "CPIAUCSL", {"2024-02-13": first, "2024-03-12": second})
    found = market_signals.us_macro(tmp_path)
    reading = found[f"{market_signals.MACRO}消費者物價（年增率）"]
    assert abs(reading[date(2024, 2, 13)] - 0.02) < 1e-9
    assert abs(reading[date(2024, 3, 11)] - 0.02) < 1e-9                    # the revision is not known the day before
    assert abs(reading[date(2024, 3, 12)] - 0.04) < 1e-9                    # February against February 2023
    assert date(2024, 2, 17) not in reading and date(2024, 2, 19) in reading     # weekdays only
    assert min(reading) == date(2024, 2, 13)


def test_the_unemployment_reading_is_the_rise_above_the_low_of_the_year_before():
    months = [f"{year}-{month:02d}-01" for year in (2022, 2023) for month in range(1, 13)]
    known = {month: 4.0 for month in months}
    known.update({"2023-10-01": 4.3, "2023-11-01": 4.6, "2023-12-01": 4.9})
    assert abs(market_signals._sahm(known) - 0.6) < 1e-9
    assert market_signals._sahm({month: 4.0 for month in months[:10]}) is None


def test_a_us_statistic_is_useful_only_with_the_same_end_in_both_periods():
    # 0050 rising 0.05% a session, except that it falls 20% over the 60 sessions after a "high" reading
    start = date(2014, 1, 1)
    days = [start + timedelta(days=number) for number in range(4700) if (start + timedelta(days=number)).weekday() < 5]
    high = {day: (index // 150) % 5 == 4 for index, day in enumerate(days)}
    level, index = 100.0, {}
    for day in days:
        index[day] = level
        level *= 0.996 if high[day] else 1.0005
    same = {day: (5.0 if high[day] else 1.0) + (number % 7) * 0.01 for number, day in enumerate(days)}
    flipped = {day: value if day < date(2020, 10, 1) else 6.0 - value for day, value in same.items()}
    found = market_signals.study(index, {f"{market_signals.MACRO}同一頭": same, f"{market_signals.MACRO}換邊": flipped})
    assert found[f"{market_signals.MACRO}同一頭"]["same_end"] and found[f"{market_signals.MACRO}同一頭"]["useful"]
    assert not found[f"{market_signals.MACRO}換邊"]["same_end"] and not found[f"{market_signals.MACRO}換邊"]["useful"]
    assert found[f"{market_signals.MACRO}同一頭"]["halves_below"] >= 3
