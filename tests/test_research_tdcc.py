"""2026-10-20: the depository's weekly holder distribution, kept from now on (research/history/tdcc.py)."""
import gzip

import pandas as pd

from quant_platform.research.history import tdcc

SAMPLE = ("﻿資料日期,證券代號,持股分級,人數,股數,占集保庫存數比例%\n"
          "20261016,2330,1,500000,100000000,0.38\n"
          "20261016,2330,15,1500,23000000000,88.70\n"
          "20261016,2330,16,0,0,0.00\n"
          "20261016,2330,17,2000000,25932000000,100.00\n"
          "bad,line\n").encode("utf-8")


def test_a_new_week_is_stored_once_and_built(tmp_path):
    assert [row["level"] for row in tdcc.parse(SAMPLE)] == [1, 15, 16, 17]
    first = tdcc.fetch(tmp_path, get=lambda: SAMPLE)
    assert first == {"date": "20261016", "stored": True, "rows": 4}
    assert gzip.decompress(tdcc.raw_path(tmp_path, "20261016").read_bytes()) == SAMPLE
    assert tdcc.fetch(tmp_path, get=lambda: SAMPLE)["stored"] is False            # the same week again
    built = tdcc.build(tmp_path)
    frame = pd.read_parquet(tmp_path / "tdcc" / "holding.parquet")
    assert built == {"weeks": 1, "rows": 3} and sorted(frame["level"]) == [1, 15, 17]   # the adjustment row left out
    big = frame[(frame["code"] == "2330") & (frame["level"] == tdcc.BIG)].iloc[0]
    assert big["percent"] == 88.70 and big["holders"] == 1500
    assert tdcc.fetch(tmp_path, get=lambda: b"")["stored"] is False


def test_the_disclosures_refresh_every_trading_morning(tmp_path):
    """2026-10-20: model H's three data sets kept current by the worker (scheduler.refresh_disclosures)."""
    from datetime import date

    from quant_platform.config.settings import Settings
    from quant_platform.container import build_container
    from quant_platform.scheduler.runner import refresh_disclosures

    calls = []

    class Fake:
        def __init__(self, name, **answer):
            self.name, self.answer = name, answer

        def download(self, base, today):
            calls.append((self.name, "fetch", today))
            return self.answer

        fetch = download

        def build(self, base):
            calls.append((self.name, "build"))
            return {}

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'x.db'}"))
    found = refresh_disclosures(container, today=date(2026, 10, 21),
                                modules=(Fake("insider", stored=1), Fake("buyback", asked=4), Fake("pledge", asked=4)))
    assert [call[:2] for call in calls] == [("insider", "fetch"), ("insider", "build"), ("buyback", "fetch"),
                                            ("buyback", "build"), ("pledge", "fetch"), ("pledge", "build")]
    assert found["insider"] == {"stored": 1} and calls[0][2] == date(2026, 10, 21)
