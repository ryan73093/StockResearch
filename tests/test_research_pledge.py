"""2026-10-20: directors' and supervisors' pledged shares (公開資訊觀測站 IRB180), a static page a month and market."""
from datetime import date

from quant_platform.research.history import pledge

PAGE = ("<html><table><tr><td>百 分 比</td><td>公 司 名 稱</td></tr>"
        "<tr><td>90 以上</td><td>6431 光麗-KY 99.99</td><td>2514 龍邦 95.60</td></tr>"
        "<tr><td>0 以上</td><td>8481 政伸 0.00</td><td>1101 台泥 12.34</td></tr></table></html>").encode("cp950")


def test_each_company_on_the_page_with_its_percent():
    assert pledge.parse(PAGE) == [("6431", "光麗-KY", 99.99), ("2514", "龍邦", 95.6), ("8481", "政伸", 0.0),
                                  ("1101", "台泥", 12.34)]
    assert pledge.usable_from(2025, 3) == date(2025, 4, 16) and pledge.usable_from(2025, 12) == date(2026, 1, 16)


def test_the_months_are_fetched_once_but_the_last_two_and_built(tmp_path):
    asked = []

    def get(market, year, month):
        asked.append((market, year, month))
        return PAGE if (year, month) != (2015, 3) else b"<html>not yet</html>"
    pledge.FIRST = (2015, 1)
    found = pledge.fetch(tmp_path, date(2015, 4, 10), get=get, sleep=lambda seconds: None)
    assert found["asked"] == 6 and len(asked) == 6                       # Jan–Mar, two markets
    assert not pledge.raw_path(tmp_path, "sii", 2015, 3).is_file()       # nothing published yet: not stored
    asked.clear()
    pledge.fetch(tmp_path, date(2015, 4, 10), get=get, sleep=lambda seconds: None)
    assert sorted(asked) == [("otc", 2015, 2), ("otc", 2015, 3), ("sii", 2015, 2), ("sii", 2015, 3)]
    built = pledge.build(tmp_path)
    assert built == {"rows": 16, "months": 2}


def test_a_month_counts_from_the_session_after_its_filing_deadline_until_the_next(tmp_path):
    from datetime import timedelta

    import numpy as np
    import pyarrow as pa
    import pyarrow.parquet as pq

    from quant_platform.research.chips import ChipStore

    days, day = [], date(2025, 3, 3)
    while day <= date(2025, 6, 30):
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    (tmp_path / "chips").mkdir()
    (tmp_path / "pledge").mkdir()
    pq.write_table(pa.table({"year": [2025, 2025, 2025], "month": [3, 3, 4],
                             "usable_from": [date(2025, 4, 16), date(2025, 4, 16), date(2025, 5, 16)],
                             "market": ["sii"] * 3, "code": ["1101", "2330", "1101"], "name": ["a", "b", "a"],
                             "percent": [60.0, 0.0, 10.0]}), tmp_path / "pledge" / "ratios.parquet")
    chips = ChipStore(tmp_path, days, ["1101.TW", "2330.TW"], np.full((2, len(days)), 50.0))
    heavy, low = chips.pledge_matrix("pledge_heavy"), chips.pledge_matrix("low_pledge")
    april, may = days.index(date(2025, 4, 16)), days.index(date(2025, 5, 16))
    assert np.isnan(heavy[0, april - 1]) and heavy[0, april] == 1.0 and heavy[0, may - 1] == 1.0
    assert heavy[0, may] == 0.0 and low[0, may] == np.float32(-0.10)          # April's 10% from May 16
    assert low[1, april] == 0.0 and np.isnan(low[1, may])                     # 2330 not on April's page
