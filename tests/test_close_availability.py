from datetime import date, datetime

from quant_platform.application.close_availability import (
    CloseAvailabilityProbe,
    recent_table,
)
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.market_calendar import MarketClosure, TradingCalendar

TAIPEI = __import__("zoneinfo").ZoneInfo("Asia/Taipei")
DAY = date(2026, 10, 1)


class Calendars:
    def __init__(self, closures=()):
        self._calendar = TradingCalendar("TW", closures, covered_years=[2026])

    def calendar(self, market):
        return self._calendar


class FakeSources:
    """URL-prefix routed responses; ``state`` flips as the afternoon goes on."""

    def __init__(self):
        self.state = {"twse": False, "tpex": False, "yahoo_close": 79.9, "odd_lot": False}
        self.calls = []

    def __call__(self, url):
        self.calls.append(url.split("?")[0])
        if "MI_INDEX" in url:
            if not self.state["twse"]:
                return {"stat": "很抱歉，沒有符合條件的資料!"}
            return {
                "stat": "OK",
                "date": "20261001",
                "tables": [
                    {"fields": ["指數", "收盤指數"], "data": [["發行量加權股價指數", "23,000"]]},
                    {
                        "fields": ["證券代號", "證券名稱", "成交股數"],
                        "data": [
                            ["0050", "元大台灣50", "1,000", "", "", "79.5", "80.2", "79.1", "80.00"],
                            ["2330", "台積電", "2,000", "", "", "1000", "1010", "990", "1,005.00"],
                        ],
                    },
                ],
            }
        if "tpex_mainboard_daily_close_quotes" in url:
            stamp = "1151001" if self.state["tpex"] else "1150930"
            return [{"Date": stamp, "SecuritiesCompanyCode": "6488", "Close": "500"}]
        if "finance.yahoo.com" in url:
            session = int(datetime(2026, 10, 1, 9, 0, tzinfo=TAIPEI).timestamp())
            return {"chart": {"result": [{
                "timestamp": [session - 86400, session],
                "indicators": {"quote": [{"close": [79.0, self.state["yahoo_close"]]}]},
            }]}}
        if "TWT53U" in url:
            if not self.state["odd_lot"]:
                return {"stat": "很抱歉，沒有符合條件的資料!"}
            return {"stat": "OK", "date": "20261001", "data": [["0050", "元大台灣50", "5,000"]]}
        raise AssertionError(url)


def at(hour, minute):
    return datetime(2026, 10, 1, hour, minute, tzinfo=TAIPEI)


def test_probe_records_the_first_sighting_of_each_source(tmp_path):
    sources = FakeSources()
    probe = CloseAvailabilityProbe(tmp_path, Calendars(), fetch_json=sources)

    assert probe.run(at(13, 32)) == []

    sources.state["twse"] = True
    first = probe.run(at(13, 36))
    assert [(item.source, item.minutes_after_close, item.rows, item.value) for item in first] == [
        ("twse_close", 6.0, 2, 80.0)
    ]  # Yahoo still shows 79.9, so it is not final yet

    sources.state.update(tpex=True, yahoo_close=80.0)
    second = probe.run(at(13, 41))
    assert [item.source for item in second] == ["tpex_close", "yahoo_0050"]

    sources.calls.clear()
    sources.state["odd_lot"] = True
    third = probe.run(at(14, 33))
    assert [item.source for item in third] == ["twse_odd_lot"]
    assert sources.calls == ["https://www.twse.com.tw/rwd/zh/afterTrading/TWT53U"]

    sources.calls.clear()
    assert probe.run(at(14, 34)) == [] and sources.calls == []  # everything already seen

    recorded = probe.sightings()["2026-10-01"]
    assert recorded["twse_close"].first_seen == "2026-10-01T13:36:00+08:00"
    assert recorded["twse_odd_lot"].minutes_after_close == 63.0


def test_probe_stays_quiet_outside_the_window_and_on_holidays(tmp_path):
    sources = FakeSources()
    sources.state.update(twse=True, tpex=True, odd_lot=True)
    probe = CloseAvailabilityProbe(tmp_path, Calendars(), fetch_json=sources)
    holiday = CloseAvailabilityProbe(
        tmp_path / "holiday", Calendars([MarketClosure(DAY, "測試休市")]), fetch_json=sources
    )

    assert probe.run(at(13, 29)) == []
    assert probe.run(at(14, 46)) == []
    assert holiday.run(at(13, 40)) == []
    assert sources.calls == []


def test_probe_survives_source_errors(tmp_path):
    def broken(url):
        raise TimeoutError("slow source")

    probe = CloseAvailabilityProbe(tmp_path, Calendars(), fetch_json=broken)

    assert probe.run(at(13, 40)) == []
    assert not probe.path.exists()


def test_recent_table_and_system_page(tmp_path):
    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'app.db'}", scheduler_in_web=False)
    )
    client = create_app(container).test_client()
    assert "尚無紀錄" in client.get("/system").get_data(as_text=True)

    sources = FakeSources()
    sources.state.update(twse=True)
    container.close_availability._fetch = sources
    container.close_availability._calendar_store = Calendars()
    container.close_availability.run(at(13, 36))

    rows = recent_table(container.close_availability)
    assert rows[0]["day"] == "2026-10-01"
    assert rows[0]["sources"]["twse_close"] == "13:36（+6 分）"
    assert rows[0]["sources"]["tpex_close"] == "—"
    assert "13:36（+6 分）" in client.get("/system").get_data(as_text=True)
