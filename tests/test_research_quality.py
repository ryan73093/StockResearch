"""S9-W05 (2026-10-06): the research store's data quality on the system page."""

from datetime import date

from quant_platform.research.history.dataset import write_parquet
from quant_platform.research.history.official import DailyRow
from quant_platform.research.history.stocks import write_year
from quant_platform.research.quality import check

DAYS = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)]


def quote(day, code, close):
    return {"date": day, "code": code, "name": f"公司{code}", "open": close, "high": close, "low": close, "close": close,
            "volume": 1, "turnover": 1, "trades": 1}


def store(base, last=date(2026, 10, 5)):
    rows = []
    for day in [day for day in DAYS if day <= last]:
        rows += [quote(day, "1101", 10.0), quote(day, str(2330), 100.0)]
    rows += [quote(last, "1102", 20.0)]
    rows[-1]["close"] = 20.0
    # 1103 jumps 20% on the last day with no event; 1104 drops 30% on an ex-rights day
    rows += [quote(DAYS[1], "1103", 10.0), quote(last, "1103", 12.0), quote(DAYS[1], "1104", 10.0), quote(last, "1104", 7.0)]
    write_year(rows, base / "stocks" / "twse" / "2026.parquet")
    write_parquet([DailyRow(day, 1.0, 1.0, 1.0, 1.0, source="t") for day in DAYS if day <= last], base / "daily" / "0050.parquet")


def test_fresh_store_and_moves_beyond_the_limit(tmp_path):
    base = tmp_path / "history"
    store(base)
    events = {"1104.TW": {date(2026, 10, 5): ("shares", 10.0, 7.0)}}
    result = check(base, tmp_path, date(2026, 10, 5), DAYS, events)
    rows = {row["name"]: row for row in result["rows"]}
    assert rows["上市個股日行情"]["status"] == "ok" and rows["上市個股日行情"]["last"] == "2026-10-05"
    assert rows["上櫃個股日行情"]["status"] == "bad"                         # no TPEx file
    assert rows["ETF 與指數日線"]["status"] == "ok" and rows["個股因子快照（個股頁、市場總覽）"]["status"] == "bad"
    moves = {move["code"]: move for move in result["moves"]}
    assert set(moves) == {"1103", "1104"} and moves["1104"]["explained"] == "除權息" and moves["1103"]["explained"] is None
    assert result["status"] == "bad"


def test_a_store_a_session_behind_is_late(tmp_path):
    base = tmp_path / "history"
    store(base, last=date(2026, 10, 2))
    result = check(base, tmp_path, date(2026, 10, 5), DAYS)
    rows = {row["name"]: row for row in result["rows"]}
    assert rows["上市個股日行情"]["status"] == "bad" and rows["ETF 與指數日線"]["status"] == "warn"
    assert "0050" in rows["ETF 與指數日線"]["detail"]


def test_system_page_shows_the_research_quality(tmp_path):
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    store(tmp_path / "research" / "history")
    client = create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}",
                                                 scheduler_in_web=False))).test_client()
    body = client.get("/system").get_data(as_text=True)
    assert 'id="quality"' in body and "上市個股日行情" in body and "個股因子快照" in body
    assert "官方收盤表" in body and "個股因子快照" in body                      # the schedule lists the new jobs


def test_today_reads_the_price_quality_of_the_research_store(tmp_path):
    """S9-W03 (2026-10-06): Today's quality chip follows the research prices (no TPEx file here: 落後)."""
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    store(tmp_path / "research" / "history")
    client = create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}",
                                                 scheduler_in_web=False))).test_client()
    body = client.get("/").get_data(as_text=True)
    assert "資料品質</dt><dd><span class=\"badge badge--bad\">落後</span>" in body
