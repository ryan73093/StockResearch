"""2026-10-16: the exchanges' nightly ex-rights tables extend FinMind's one-time dividend download."""
import gzip
import json
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from quant_platform.research.history import dividends_daily
from quant_platform.research.history.dividends_daily import announcements, parse

# shapes as answered on 2026-10-16
TWSE = {"stat": "OK", "title": "除權除息預告表",
        "fields": ["除權除息日期", "股票代號", "名稱", "除權息", "無償配股率", "現金增資配股率", "現金增資認購價", "現金股利", "詳細資料",
                   "參考價<br>試算", "最近一次申報資料 季別/日期", "最近一次申報每股 (單位)淨值", "最近一次申報每股 (單位)盈餘"],
        "data": [["115年10月15日", "00401A", "主動摩根台灣鑫收", "息", "0", "0", "0", "0.131", "", "", "", "0.00", ""],
                 ["115年11月03日", "2330", "台積電", "息", "0.00000000", "0.00000000", "0.00000000",
                  "<p style= text-align:center;>待公告實際收益分配金額</p>", "", "", "", "0.00", ""],
                 ["115年11月20日", "1101", "台泥", "權", "0.00000000", "0.05000000", "25.00", "0.00000000", "", "", "", "0.00", ""]]}
TPEX = [{"ExRrightsExDividendDate": "1151007", "SecuritiesCompanyCode": "2947", "CompanyName": "振宇五金",
         "ExRrightsExDividend": "除息", "StockDividendRatio": "0.00000000", "SubscriptionRatioToNewSharesIssued": "0.00000000",
         "SubscriptionPricePerShare": "0.00", "CashDividend": "1.00000000", "AllocatedForPublicUnderwriting": "0",
         "SubscribedByEmployees": "0", "SubscribedByExistingShareholders": "0", "SubscribedProRataInThousandShares": "0"},
        {"ExRrightsExDividendDate": "1151007", "SecuritiesCompanyCode": "6129", "CompanyName": "普誠",
         "ExRrightsExDividend": "除權", "StockDividendRatio": "0.00000000", "SubscriptionRatioToNewSharesIssued": "0.12455307",
         "SubscriptionPricePerShare": "11.75", "CashDividend": "0.00000000", "AllocatedForPublicUnderwriting": "2000000",
         "SubscribedByEmployees": "3000000", "SubscribedByExistingShareholders": "15000000",
         "SubscribedProRataInThousandShares": "93.41480433"}]


def keep(base, exchange, night, payload):
    folder = base / "raw" / "official_dividends" / exchange
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{night:%Y%m%d}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_both_tables_parse_into_stock_rows():
    twse = parse("twse", TWSE)
    assert [row["code"] for row in twse] == ["2330", "1101"]                       # the ETF is left out
    assert twse[0] == {"code": "2330", "ex_date": date(2026, 11, 3), "cash": None, "issue_ratio": 0.0, "issue_shares": None}
    assert twse[1]["issue_ratio"] == 0.05
    tpex = parse("tpex", TPEX)
    assert tpex[0]["cash"] == 1.0 and tpex[0]["ex_date"] == date(2026, 10, 7)
    assert tpex[1]["issue_shares"] == 20_000_000 and tpex[1]["issue_ratio"] == pytest.approx(0.12455307)
    assert parse("twse", {"stat": "很抱歉"}) == [] and parse("tpex", None) == []


def test_an_event_counts_from_the_first_night_its_amount_is_listed(tmp_path):
    keep(tmp_path, "twse", date(2026, 10, 16), TWSE)
    later = json.loads(json.dumps(TWSE))
    later["data"][1][7] = "7.00000000"                                               # TSMC's amount is out
    keep(tmp_path, "twse", date(2026, 10, 19), later)
    keep(tmp_path, "twse", date(2026, 10, 20), later)
    keep(tmp_path, "tpex", date(2026, 10, 16), TPEX)
    found = announcements(tmp_path)
    assert found["2330"] == [{"ex_date": date(2026, 11, 3), "cash": 7.0, "cash_announced": date(2026, 10, 19),
                              "issue_ratio": 0.0, "issue_shares": None, "issue_announced": None}]
    assert found["1101"][0]["issue_announced"] == date(2026, 10, 16) and found["6129"][0]["issue_shares"] == 20_000_000


def test_the_nightly_announcements_join_the_corporate_events_and_the_dividend_plans(tmp_path):
    from quant_platform.research.actions import dividend_plans
    from quant_platform.research.chips import CORPORATE, build

    finmind = tmp_path / "raw" / "finmind" / "TaiwanStockDividend"
    finmind.mkdir(parents=True)
    (finmind / "2330.json.gz").write_bytes(gzip.compress(json.dumps({"data": [
        {"AnnouncementDate": "2025-09-01", "CashEarningsDistribution": 5.0, "CashStatutorySurplus": 0.0,
         "CashExDividendTradingDate": "2025-09-16"},
        {"AnnouncementDate": "2026-09-01", "CashEarningsDistribution": 7.0, "CashStatutorySurplus": 0.0,
         "CashExDividendTradingDate": "2026-09-16"}]}).encode()))
    keep(tmp_path, "twse", date(2026, 9, 2), {**TWSE, "data": [["115年09月16日", "2330", "台積電", "息", "0", "0", "0",
                                                              "7.00000000", "", "", "", "0.00", ""]]})   # FinMind has it
    nov = json.loads(json.dumps(TWSE))
    nov["data"][1][7] = "7.00000000"
    keep(tmp_path, "twse", date(2026, 10, 19), nov)
    chips = tmp_path / "chips"
    chips.mkdir()
    holding = tmp_path / "raw" / "finmind" / "TaiwanStockShareholding"
    holding.mkdir(parents=True)
    (holding / "1101.json.gz").write_bytes(gzip.compress(json.dumps({"data": [
        {"date": "2026-10-01", "ForeignInvestmentSharesRatio": 10.0, "NumberOfSharesIssued": 8_000_000.0}]}).encode()))
    build(tmp_path)
    rows = pq.read_table(chips / f"{CORPORATE}.parquet").to_pylist()
    events = {(row["code"], row["date"]): (row["dividend_change"], row["cash_issue_shares"]) for row in rows}
    assert events[("2330", date(2026, 9, 1))][0] == pytest.approx(2.0)              # FinMind's, once (not twice)
    assert ("2330", date(2026, 9, 2)) not in events
    # the November payout has no payout a year before it here, so no change; TWSE's capital increase: 5% ÷ 0.8
    assert events[("1101", date(2026, 10, 19))] == (None, pytest.approx(0.05 * 8_000_000 / 0.8))   # first listed 10-19
    plans = dividend_plans(tmp_path, ["2330"])
    assert (date(2026, 10, 19), date(2026, 11, 3), 7.0) in plans["2330"]
    assert sum(1 for plan in plans["2330"] if plan[1] == date(2026, 9, 16)) == 1


def test_tonight_asks_both_exchanges_once(tmp_path):
    from quant_platform.research.history.official import OfficialHistoryClient

    asked = []

    def answer(url):
        asked.append(url)
        return TWSE if "twse" in url else TPEX

    client = OfficialHistoryClient(tmp_path / "raw", fetch_json=answer, min_interval=0, today=lambda: date(2026, 10, 16))
    assert dividends_daily.fetch(client, date(2026, 10, 16)) == {"twse": 2, "tpex": 2} and len(asked) == 2
    assert (tmp_path / "raw" / "official_dividends" / "tpex" / "20261016.json").is_file()
