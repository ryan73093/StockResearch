import gzip
import json
from datetime import date, timedelta

from quant_platform.research.history.finmind import (
    QuotaReached,
    fetch_lists,
    path_for,
    read_rows,
    tpex_codes,
)
from quant_platform.research.history.stocks import write_year
from quant_platform.research.news import candidates, collect, read


def quotes(base, days, codes):
    rows = []
    for day in days:
        for rank, code in enumerate(codes):
            rows.append({"date": day, "code": code, "name": code, "open": 1.0, "high": 1.0, "low": 1.0, "close": 10.0,
                         "volume": 1, "turnover": 1_000_000 * (len(codes) - rank), "trades": 1})
    write_year(rows, base / "stocks" / "twse" / f"{days[0].year}.parquet")


def test_candidates_are_the_most_traded_and_the_forward_holdings(tmp_path):
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(30)]
    quotes(tmp_path / "history", days, ["2330", "2317", "2454", "1101"])
    log = tmp_path / "forward" / "stocks" / "log.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps({"rule_hash": "r", "holdings": [{"code": "8021"}]}) + "\n", encoding="utf-8")
    assert candidates(tmp_path / "history", tmp_path, date(2026, 10, 2), top=2) == ["2317", "2330", "8021"]


def test_news_is_stored_once_per_stock_day_and_stops_at_the_hourly_limit(tmp_path):
    asked = []

    def get(token, code, day):
        asked.append(code)
        if code == "2454":
            raise QuotaReached("limit")
        return [{"date": f"{day} 09:00:00", "stock_id": code, "title": f"{code} 新聞", "source": "s", "link": "l"}]

    day = date(2026, 10, 2)
    result = collect(tmp_path, day, ["2330", "2317", "2454", "1101"], "token", get=get, sleep=lambda _s: None)
    assert result == {"stored": 2, "skipped": 0, "articles": 2} and asked == ["2330", "2317", "2454"]
    assert read(tmp_path, day)["2330"][0]["title"] == "2330 新聞"
    asked.clear()
    collect(tmp_path, day, ["2330", "2317", "2454", "1101"], "token", get=lambda t, c, d: asked.append(c) or [],
            sleep=lambda _s: None)
    assert asked == ["2454", "1101"]                     # the next run resumes where the limit stopped it


def test_tpex_codes_and_list_downloads(tmp_path):
    quotes(tmp_path, [date(2026, 10, 2)], ["2330", "1101"])
    queries = []

    def get(query):
        queries.append(query)
        if "TaiwanStockDelisting" in query:
            return {"data": [{"stock_id": "5346", "stock_name": "力晶"}, {"stock_id": "1101", "stock_name": "x"},
                             {"stock_id": "00732", "stock_name": "ETF"}, {"stock_id": "9105", "stock_name": "TDR"}]}
        if "TaiwanStockInfo" in query:
            return {"data": [{"stock_id": "6488", "type": "tpex"}, {"stock_id": "2330", "type": "twse"},
                             {"stock_id": "006201", "type": "tpex"}]}
        return {"data": [{"date": "2026-10-02", "name": "外資"}]}

    counts = fetch_lists(tmp_path, "token", get=get, sleep=lambda _s: None)
    assert counts["TaiwanStockDelisting:all"] == 4 and counts["TaiwanFuturesInstitutionalInvestors:TX"] == 1
    assert len(queries) == 6 and read_rows(tmp_path, "TaiwanStockDelisting", "all")[0]["stock_id"] == "5346"
    # today's TPEx stock plus a delisted code TWSE never quoted; ETFs, TDRs and listed codes are out
    assert tpex_codes(tmp_path) == ["5346", "6488"]
    assert json.loads(gzip.decompress(path_for(tmp_path, "TaiwanStockInfo", "all").read_bytes()))["data"]
