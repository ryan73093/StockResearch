"""S9-W05 (2026-10-05): the stock page's nightly factor snapshot and the page itself."""

from datetime import date, timedelta

import pytest

from quant_platform.research.history.dataset import write_parquet
from quant_platform.research.history.official import DailyRow
from quant_platform.research.history.stocks import write_year
from quant_platform.research.snapshot import (
    GROUPS,
    build,
    candles,
    display,
    load,
    market_view,
    search,
    stock_bars,
    view,
)


def weekdays(start, end):
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = weekdays(date(2025, 6, 2), date(2026, 10, 9))
GROWTH = {"1101": 1.0, "1102": 1.0005, "1103": 1.001, "6488": 1.002}     # 6488 (TPEx) rises fastest


def history(base):
    write_parquet([DailyRow(day, 100.0, 100.0, 100.0, 100.0, source="t") for day in DAYS], base / "daily" / "TAIEX.parquet")
    write_parquet([DailyRow(day, 50.0, 50.0, 50.0, 50.0, source="t") for day in DAYS], base / "daily" / "0050.parquet")
    for exchange, codes in (("twse", ("1101", "1102", "1103")), ("tpex", ("6488",))):
        by_year = {}
        for i, day in enumerate(DAYS):
            for code in codes:
                close = round(20 * GROWTH[code] ** i, 4)
                by_year.setdefault(day.year, []).append({
                    "date": day, "code": code, "name": {"1101": "台泥", "6488": "環球晶"}.get(code, f"公司{code}"),
                    "open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1_000,
                    "turnover": 60_000_000, "trades": 10})
        for year, rows in by_year.items():
            write_year(rows, base / "stocks" / exchange / f"{year}.parquet")
    return base


def test_snapshot_ranks_every_factor_across_both_markets(tmp_path):
    base = history(tmp_path / "research" / "history")
    summary = build(base, tmp_path / "research", date(2026, 10, 9))
    assert summary == {"date": "2026-10-09", "stocks": 4, "chips_as_of": None}
    snapshot = load(tmp_path / "research")
    tpex, flat = snapshot["stocks"]["6488"], snapshot["stocks"]["1101"]
    assert tpex["exchange"] == "上櫃" and tpex["name"] == "環球晶" and flat["exchange"] == "上市"
    assert tpex["ranks"]["trend_200"] == 1.0 and flat["ranks"]["trend_200"] == 0.0
    assert tpex["values"]["momentum_3"] == pytest.approx(1.002 ** 63 - 1, rel=1e-4)
    assert tpex["eligible"] and tpex["values"]["foreign_holding"] is None          # no chip data here
    assert sum(len(factors) for _title, factors in GROUPS) == len(snapshot["labels"]) == 35


def test_display_search_bars_and_view(tmp_path):
    assert display("momentum_6", 0.123) == "+12.3%" and display("reversal_1", -0.05) == "+5.0%"
    assert display("low_volatility_60", -0.021) == "日波動 2.1%" and display("earnings_yield", 0.05) == "本益比 20.0"
    assert display("market_cap", 30.0) == "10.69 兆" and display("trend_200", None) == "—"
    base = history(tmp_path / "research" / "history")
    build(base, tmp_path / "research", date(2026, 10, 9))
    snapshot = load(tmp_path / "research")
    assert [item["code"] for item in search(snapshot, "11")] == ["1101", "1102", "1103"]
    assert [item["code"] for item in search(snapshot, "環球")] == ["6488"] and search(snapshot, "6488.TWO")[0]["code"] == "6488"
    bars = stock_bars(base, "6488")
    assert len(bars) == 260 and bars[-1]["date"] == date(2026, 10, 9)
    page = view(base, tmp_path / "research", "6488", snapshot, verdicts={"trend_200": "強"})
    trend = page["groups"][0]["rows"][0]
    assert trend["factor"] == "trend_200" and trend["top"] == 1 and trend["verdict"] == "強"
    assert len(page["chart"]["bodies"]) == 120 and page["chart"]["ma20"] and page["chart"]["ma60"]
    assert view(base, tmp_path / "research", "9999", snapshot) is None


def test_candles_color_and_scale():
    rows = [{"date": date(2026, 1, 1) + timedelta(days=i), "open": o, "high": h, "low": l, "close": c}
            for i, (o, h, l, c) in enumerate([(10, 12, 9, 11), (11, 11.5, 8, 9)])]
    chart = candles(rows, rows, width=200, height=112)
    up, down = chart["bodies"]
    assert up["up"] and not down["up"] and chart["high"] == 12 and chart["low"] == 8
    assert up["high"] == 6 and down["low"] == 106                       # 6 px margins top and bottom
    assert chart["ma20"] == ""                                          # not enough days for an average


def test_stock_page_and_search(tmp_path):
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    base = history(tmp_path / "research" / "history")
    build(base, tmp_path / "research", date(2026, 10, 9))
    client = create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}",
                                                 scheduler_in_web=False))).test_client()
    body = client.get("/stock/6488").get_data(as_text=True)
    assert "環球晶 6488" in body and "日 K 線" in body and "站上 200 日均線的幅度" in body and "前 1%" in body
    assert client.get("/stock?q=6488").headers["Location"].endswith("/stock/6488")
    listed = client.get("/stock?q=11").get_data(as_text=True)
    assert "符合「11」的股票" in listed and "/stock/1102" in listed and "3 檔" in listed
    missing = client.get("/stock/9999")
    assert missing.status_code == 404 and "找不到 9999" in missing.get_data(as_text=True)
    assert client.get("/stock/<script>").status_code == 404
    assert "查個股" in client.get("/stock").get_data(as_text=True)


def test_market_breadth_and_overview_page(tmp_path):
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    base = history(tmp_path / "research" / "history")
    build(base, tmp_path / "research", date(2026, 10, 9))
    snapshot = load(tmp_path / "research")
    market = snapshot["market"]
    assert len(market["days"]) == 250 and market["days"][-1] == "2026-10-09"
    # 1101 is flat (no advance, at its high and low at once); the three others rise every day
    assert market["advance"][-1] == 3 and market["decline"][-1] == 0 and market["new_high"][-1] == 4
    assert market["above_200"][-1] == 0.75 and market["traded"][-1] == 4
    page = market_view(snapshot)
    assert page["taiex_vs_200"] == 0.0 and page["gainers"][0]["code"] == "6488" and page["industries"] == []
    client = create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}",
                                                 scheduler_in_web=False))).test_client()
    body = client.get("/market").get_data(as_text=True)
    assert "站上自己 200 日均線的股票" in body and "/stock/6488" in body and "75%" in body
