import json
from datetime import date, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from quant_platform.research.history.dataset import write_parquet
from quant_platform.research.history.official import DailyRow
from quant_platform.research.history.stocks import write_year
from quant_platform.research.prices import (
    FallbackPrices,
    ResearchPrices,
    fetch_today_close,
    quotes_from_payload,
)

TAIPEI = ZoneInfo("Asia/Taipei")
FIELDS = ["證券代號", "證券名稱", "成交股數", "成交筆數", "成交金額", "開盤價", "最高價", "最低價", "收盤價", "漲跌(+/-)"]


def payload(rows):
    return {"stat": "OK", "tables": [{"fields": ["x"], "data": []}, {"fields": FIELDS, "data": rows}]}


def setup(base):
    days = [date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)]
    write_parquet([DailyRow(day, 1.0, 1.0, 1.0, 110.0 + i, source="t") for i, day in enumerate(days)],
                  base / "daily" / "0050.parquet")
    write_year([{"date": day, "code": "2330", "name": "台積電", "open": 1.0, "high": 1.0, "low": 1.0,
                 "close": 1500.0 + i, "volume": 1, "turnover": 1, "trades": 1} for i, day in enumerate(days)],
               base / "stocks" / "twse" / "2026.parquet")
    raw = base / "raw" / "twse_stock_all" / "2026"
    raw.mkdir(parents=True)
    (raw / "20261005.json").write_text(json.dumps(payload([
        ["0050", "元大台灣50", "1", "1", "1", "1", "1", "1", "113.50", "+"],
        ["2330", "台積電", "1", "1", "1", "1", "1", "1", "1,510.00", "+"],
    ]), ensure_ascii=False), encoding="utf-8")


def test_research_prices_add_today_only_after_the_close_is_public(tmp_path):
    setup(tmp_path)
    prices = ResearchPrices(tmp_path)
    before = datetime.combine(date(2026, 10, 5), time(13, 40), TAIPEI)
    after = datetime.combine(date(2026, 10, 5), time(13, 55), TAIPEI)
    assert prices.history("0050.TW", before)[-1] == (date(2026, 10, 2), 112.0)
    assert prices.history("0050.TW", after)[-1] == (date(2026, 10, 5), 113.5)
    assert prices.latest_closes(["2330.TW", "0050.TW"], after) == {"2330.TW": 1510.0, "0050.TW": 113.5}
    bars = prices.list_bars("2330.TW", as_of=after)
    assert bars[-1].event_time == datetime(2026, 10, 5, 13, 30, tzinfo=TAIPEI) and bars[-1].close == 1510.0
    assert prices.history("6488.TWO", after) == []                     # no TPEx file in this store
    assert prices.latest_market_date(after) == date(2026, 10, 5)


def test_fallback_uses_legacy_bars_only_when_they_are_newer(tmp_path):
    setup(tmp_path)

    def bar(day, close):
        return SimpleNamespace(event_time=datetime.combine(day, time(13, 30), TAIPEI), close=close)

    class Legacy:
        def list_bars(self, symbol, as_of=None):
            return {"6488.TWO": [bar(date(2026, 10, 2), 400.0)], "2330.TW": [bar(date(2026, 10, 5), 1509.0)]}.get(symbol, [])

    fallback = FallbackPrices(ResearchPrices(tmp_path), Legacy())
    before = datetime.combine(date(2026, 10, 5), time(13, 40), TAIPEI)
    assert fallback.latest_closes(["6488.TWO"], before) == {"6488.TWO": 400.0}        # only the legacy store has it
    assert fallback.latest_closes(["2330.TW"], before) == {"2330.TW": 1509.0}         # legacy is a day newer
    assert fallback.latest_closes(["0050.TW"], before) == {"0050.TW": 112.0}          # research store
    assert fallback.history("0050.TW")[0] == (date(2026, 9, 30), 110.0)


def test_close_job_fetches_once(tmp_path):
    asked = []

    class Client:
        requests = 0

        def _today(self):
            return date(2026, 10, 6)

        def _cached(self, key, url, final, period_end=None):
            asked.append(key)
            path = tmp_path / "raw" / f"{key}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            body = payload([[str(1101 + i), "x", "1", "1", "1", "1", "1", "1", "20.00", ""] for i in range(600)])
            path.write_text(json.dumps(body), encoding="utf-8")
            return body

    assert fetch_today_close(tmp_path, date(2026, 10, 6), client=Client()) == 600
    assert fetch_today_close(tmp_path, date(2026, 10, 6), client=Client()) == 600 and len(asked) == 1
    assert quotes_from_payload({"stat": "很抱歉"}) == {}


def test_research_bars_carry_open_and_volume_and_fill_the_paper_account(tmp_path):
    """S9-W05: the paper account fills at the next session's open from the research store."""
    from datetime import UTC
    from decimal import Decimal

    from quant_platform.config import Settings
    from quant_platform.container import build_container

    history = tmp_path / "research" / "history"
    days = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
    write_year([{"date": day, "code": "2330", "name": "台積電", "open": open_, "high": close + 2, "low": open_ - 2,
                 "close": close, "volume": 1_000_000, "turnover": 1, "trades": 1}
                for day, open_, close in zip(days, (98.0, 110.0, 120.0), (100.0, 112.0, 121.0), strict=True)],
               history / "stocks" / "twse" / "2025.parquet")
    bars = ResearchPrices(history).list_bars("2330.TW", as_of=datetime(2025, 1, 7, 9, tzinfo=TAIPEI))
    assert [(bar.open, bar.close, bar.volume) for bar in bars] == [
        (98.0, 100.0, 1_000_000), (110.0, 112.0, 1_000_000), (120.0, 121.0, 1_000_000)]
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'paper.db'}"))
    container.research_universe_service.add_asset("2330.TW", "TW", "EQUITY", "半導體", "0050.TW", date(2020, 1, 1))
    service = container.paper_trading_service
    service.submit_order("2330", "BUY", 100, now=datetime(2025, 1, 2, 7, tzinfo=UTC))
    assert service.process_pending(datetime(2025, 1, 3, 7, tzinfo=UTC)).filled == 1
    assert service.overview(datetime(2025, 1, 3, 7, tzinfo=UTC)).fills[0].price == Decimal("110.110000")
