"""S9-W04 (2026-10-06): nightly chip data from the TWSE and TPEx all-market daily reports."""

import gzip
import json
from datetime import date

import pyarrow.parquet as pq
import pytest

from quant_platform.research.chips import build
from quant_platform.research.history.chips_daily import fetch, key, parse
from quant_platform.research.history.official import OfficialHistoryClient

DAY = date(2026, 10, 5)

# shapes as the exchanges answered on 2026-10-02 (TSMC's numbers; ETFs and 91xx TDRs are skipped)
TWSE = {
    "holding": {"stat": "OK", "tables": [{"fields": ["證券代號", "證券名稱", "國際證券編碼", "發行股數", "外資及陸資尚可投資股數",
                                                      "全體外資及陸資持有股數", "外資及陸資尚可投資比率", "全體外資及陸資持股比率"],
                                           "data": [["0050", "x", "", "1", "1", "1", 1, 2.0],
                                                    ["2330", "台積電", "TW0002330008", "25,932,370,067", "1", "1", 30.82, 69.17]]}]},
    "per": {"stat": "OK", "tables": [{"fields": ["證券代號", "證券名稱", "收盤價", "殖利率(%)", "股利年度", "本益比", "股價淨值比", "財報年/季"],
                                       "data": [["2330", "台積電", "1500", "1.2", 114, "28.98", "10.08", "115/2"],
                                                ["1101", "台泥", "25", "3.2", 114, "-", "0.82", "115/2"]]}]},
    "margin": {"stat": "OK", "tables": [
        {"fields": ["項目", "買進", "賣出", "現金(券)償還", "前日餘額", "今日餘額"], "data": [["融資(交易單位)", "1", "1", "1", "1", "1"]]},
        {"fields": ["代號", "名稱", "買進", "賣出", "現金償還", "前日餘額", "今日餘額", "次一營業日限額", "買進", "賣出", "現券償還",
                    "前日餘額", "今日餘額"],
         "data": [["2330", "台積電", "704", "387", "36", "30,658", "30,939", "6,483,092", "2", "0", "0", "20", "18"]]}]},
    "insti": {"stat": "OK", "fields": ["證券代號", "證券名稱", "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
                                       "外陸資買賣超股數(不含外資自營商)", "外資自營商買進股數", "外資自營商賣出股數", "外資自營商買賣超股數",
                                       "投信買進股數", "投信賣出股數", "投信買賣超股數"],
              "data": [["2330", "台積電", "7,044,515", "12,958,489", "-5,913,974", "0", "0", "100", "539,805", "286,127", "253,678"],
                       ["9105", "TDR", "1", "1", "0", "0", "0", "0", "0", "0", "0"]]},
    # 2026-10-15: as answered for 2026-10-13 / 10-14 (TSMC 借券賣出當日餘額 12,031,514; day trading 4,878,000 shares)
    "sbl": {"stat": "OK", "fields": ["代號", "名稱", "前日餘額", "賣出", "買進", "現券", "今日餘額", "次一營業日限額", "前日餘額",
                                     "當日賣出", "當日還券", "當日調整", "當日餘額", "次一營業日可限額", "備註"],
            "data": [["00403A", "ETF", "1", "0", "0", "0", "1", "1", "1", "0", "0", "0", "1", "1", " "],
                     ["2330", "台積電", "23,000", "2,000", "2,000", "0", "23,000", "6,483,092,516", "11,995,514", "42,000",
                      "6,000", "0", "12,031,514", "7,026,738", " "]]},
    # 2026-10-15: every lending deal of the day, both markets (TSMC's first two deals of 10-14, an ETF, a TPEx stock)
    "lending": {"stat": "OK", "fields": ["成交日期", "證券代號名稱", "交易方式", "成交數量(交易單位)", "成交費率", "成交日收盤價",
                                         "約定還券日期", "約定借券天數", "費率異動"],
                "data": [["115年10月14日", "00403A 主動統一升級50", "競價", "75", "2.00", "11.00", "116年04月14日", 182, ""],
                         ["115年10月14日", "2330 台積電", "議借", "1,450", "0.05", "2,490.00", "116年04月14日", 182, ""],
                         ["115年10月14日", "2330 台積電", "議借", "600", "4.63", "2,490.00", "116年04月14日", 182, ""],
                         ["115年10月14日", "6488 環球晶", "競價", "10", "1.00", "500.00", "116年04月14日", 182, ""]]},
    "daytrade": {"stat": "OK", "tables": [
        {"fields": ["當日沖銷交易總成交股數", "當日沖銷交易總成交股數占市場比重%", "當日沖銷交易總買進成交金額"], "data": [["1", "1", "1"]]},
        {"fields": ["證券代號", "證券名稱", "暫停現股賣出後現款買進當沖註記", "當日沖銷交易成交股數", "當日沖銷交易買進成交金額",
                    "當日沖銷交易賣出成交金額"],
         "data": [["2330", "台積電", "", "4,878,000", "12,229,475,000", "12,299,340,000"]]}]},
}
TPEX = {
    "holding": {"stat": "ok", "tables": [{"fields": ["排行", "代號", "名稱", "發行股數(A)", "僑外資及陸資尚可投資股數B=A*F-C",
                                                      "僑外資及陸資持有股數(C)", "僑外資及陸資尚可投資比率(D=B/A)", "僑外資及陸資持股比率(E=C/A)"],
                                           "data": [["1", "6488", "環球晶", "478,000,000", "1", "1", "60.00%", "40.00%"]]}]},
    "per": {"stat": "ok", "tables": [{"fields": ["股票代號", "公司名稱", "本益比", "每股股利", "股利年度", "殖利率(%)", "股價淨值比", "財報年/季"],
                                       "data": [["6488", "環球晶", "20.5", "3", 114, "1.2", "2.10", "115Q2"]]}]},
    "margin": {"stat": "ok", "tables": [{"fields": ["代號", "名稱", "前資餘額(張)", "資買", "資賣", "現償", "資餘額", "資屬證金", "資使用率(%)",
                                                     "資限額", "前券餘額(張)", "券賣", "券買", "券償", "券餘額"],
                                          "data": [["6488", "環球晶", "8,289", "1", "1", "1", "9,519", "63", "9.24", "1", "13", "0", "0", "0", "15"]]}]},
    "insti": {"stat": "ok", "tables": [{"fields": ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 4,
                                         "data": [["6488", "環球晶", "4,366,400", "5,229,267", "-862,867", "0", "0", "0",
                                                   "4,366,400", "5,229,267", "-862,867", "106,000", "161", "105,839"]]}]},
    "sbl": {"stat": "ok", "tables": [{"fields": ["股票代號", "股票名稱", "前日餘額", "賣出", "買進", "現券", "當日餘額", "限額",
                                                  "前日餘額", "當日賣出", "當日還券", "當日調整數額", "當日餘額",
                                                  "次一營業日可借券賣出限額", "備註"],
                                       "data": [["6488", "環球晶", "1", "0", "0", "0", "486,000", "1", "26,588,000", "484,000",
                                                 "5,813,000", "0", "21,259,000", "1", " "]]}]},
    "daytrade": {"stat": "ok", "tables": [
        {"fields": ["當日沖銷交易總成交股數", "當日沖銷交易總成交股數占市場比重"], "data": [["572,297,000", "23.07%"]]},
        {"fields": ["證券代號", "證券名稱", "暫停現股賣出後現款買進當沖註記", "當日沖銷交易成交股數", "當日沖銷交易買進成交金額",
                    "當日沖銷交易賣出成交金額"],
         "data": [["6488", "環球晶", " ", "4,940,000", "4,994,515,000", "5,000,000,000"]]}]},
}


def test_every_report_parses_into_the_chip_columns():
    assert parse("twse", "holding", TWSE["holding"], DAY) == [
        {"date": DAY, "code": "2330", "ForeignInvestmentSharesRatio": 69.17, "NumberOfSharesIssued": 25_932_370_067}]
    assert parse("twse", "per", TWSE["per"], DAY)[1] == {"date": DAY, "code": "1101", "PER": None, "PBR": 0.82}
    assert parse("twse", "margin", TWSE["margin"], DAY) == [
        {"date": DAY, "code": "2330", "MarginPurchaseTodayBalance": 30_939, "ShortSaleTodayBalance": 18}]
    assert parse("twse", "insti", TWSE["insti"], DAY) == [
        {"date": DAY, "code": "2330", "foreign_net": -5_913_874, "trust_net": 253_678, "dealer_self_net": None,
         "dealer_hedge_net": None}]                                                  # an old 11-field reply
    assert parse("tpex", "holding", TPEX["holding"], DAY)[0]["ForeignInvestmentSharesRatio"] == 40.0
    assert parse("tpex", "per", TPEX["per"], DAY)[0]["PBR"] == 2.1
    assert parse("tpex", "margin", TPEX["margin"], DAY)[0] == {
        "date": DAY, "code": "6488", "MarginPurchaseTodayBalance": 9_519, "ShortSaleTodayBalance": 15}
    assert parse("tpex", "insti", TPEX["insti"], DAY)[0] == {"date": DAY, "code": "6488", "foreign_net": -862_867,
                                                             "trust_net": 105_839, "dealer_self_net": None,
                                                             "dealer_hedge_net": None}
    assert parse("twse", "per", {"stat": "很抱歉，沒有符合條件的資料!"}, DAY) == []
    assert parse("twse", "sbl", TWSE["sbl"], DAY) == [
        {"date": DAY, "code": "2330", "SBLShortSalesCurrentDayBalance": 12_031_514, "MarginShortSalesCurrentDayBalance": 23_000}]
    assert parse("tpex", "sbl", TPEX["sbl"], DAY) == [
        {"date": DAY, "code": "6488", "SBLShortSalesCurrentDayBalance": 21_259_000, "MarginShortSalesCurrentDayBalance": 486_000}]
    assert parse("twse", "daytrade", TWSE["daytrade"], DAY) == [
        {"date": DAY, "code": "2330", "BuyAmount": 12_229_475_000, "Volume": 4_878_000}]
    assert parse("tpex", "daytrade", TPEX["daytrade"], DAY) == [
        {"date": DAY, "code": "6488", "BuyAmount": 4_994_515_000, "Volume": 4_940_000}]
    assert parse("twse", "lending", TWSE["lending"], DAY) == [
        {"date": DAY, "code": "2330", "LendingVolume": 2_050, "FeeVolume": pytest.approx(1_450 * 0.05 + 600 * 4.63)},
        {"date": DAY, "code": "6488", "LendingVolume": 10, "FeeVolume": pytest.approx(10.0)}]


def test_reports_fill_the_days_finmind_lacks(tmp_path):
    base = tmp_path / "history"
    finmind = base / "raw" / "finmind" / "TaiwanStockShareholding"
    finmind.mkdir(parents=True)
    short = base / "raw" / "finmind" / "TaiwanDailyShortSaleBalances"      # the one-time download (2026-10-14)
    short.mkdir(parents=True)
    (short / "2330.json.gz").write_bytes(gzip.compress(json.dumps({"data": [
        {"date": "2026-10-02", "SBLShortSalesCurrentDayBalance": 11_000_000, "MarginShortSalesCurrentDayBalance": 20}]}).encode()))
    (finmind / "2330.json.gz").write_bytes(gzip.compress(json.dumps({"data": [
        {"date": "2026-10-02", "ForeignInvestmentSharesRatio": 69.0, "NumberOfSharesIssued": 25_932_370_067}]}).encode()))
    reports = {**{("twse", name): payload for name, payload in TWSE.items()},
               **{("tpex", name): payload for name, payload in TPEX.items()}}
    asked = []

    def answer(url):
        asked.append(url)
        exchange = "twse" if "twse.com.tw" in url else "tpex"
        name = next(name for name, part in (("lending", "t13sa710"), ("sbl", "TWT93U"), ("sbl", "margin/sbl"), ("daytrade", "TWTB4U"),
                                             ("daytrade", "intraday/stat"),
                                             ("holding", "QFII"), ("holding", "qfii"), ("per", "BWIBBU"), ("per", "peQry"),
                                             ("margin", "MARGN"), ("margin", "margin/"), ("insti", "T86"), ("insti", "dailyTrade"))
                    if part in url)
        return reports[(exchange, name)]

    client = OfficialHistoryClient(base / "raw", fetch_json=answer, min_interval=0, today=lambda: date(2026, 10, 6))
    result = fetch(base, client, [date(2026, 10, 2), DAY], date(2026, 10, 6))
    assert len(asked) == 26 and result["rows"]["twse_holding"] == 2 and result["rows"]["tpex_insti"] == 2
    assert result["rows"]["twse_lending"] == 4 and "tpex_lending" not in result["rows"]       # one report, both markets
    assert (base / "raw" / f"{key('twse', 'per', DAY)}.json").is_file()
    build(base)
    rows = pq.read_table(base / "chips" / "TaiwanStockShareholding.parquet").to_pylist()
    by_key = {(row["date"], row["code"]): row["ForeignInvestmentSharesRatio"] for row in rows}
    # FinMind's 10-02 stays (69.0, not the report's 69.17); 10-05 and the TPEx stock come from the reports
    assert by_key == {(date(2026, 10, 2), "2330"): 69.0, (DAY, "2330"): 69.17,
                      (date(2026, 10, 2), "6488"): 40.0, (DAY, "6488"): 40.0}
    flows = pq.read_table(base / "chips" / "TaiwanStockInstitutionalInvestorsBuySell.parquet").to_pylist()
    assert {(row["code"], row["foreign_net"]) for row in flows if row["date"] == DAY} == {("2330", -5_913_874), ("6488", -862_867)}
    # 2026-10-15: the short-selling file is extended by the reports too (FinMind's 10-02 kept)
    lent = {(row["date"], row["code"]): row["SBLShortSalesCurrentDayBalance"]
            for row in pq.read_table(base / "chips" / "TaiwanDailyShortSaleBalances.parquet").to_pylist()}
    assert lent[(date(2026, 10, 2), "2330")] == 11_000_000 and lent[(DAY, "2330")] == 12_031_514
    assert lent[(DAY, "6488")] == 21_259_000


def revenue_row(code, month="11508", compiled="1150917", revenue="514805337"):
    return {"出表日期": compiled, "資料年月": month, "公司代號": code, "公司名稱": "x", "營業收入-當月營收": revenue}


def test_monthly_revenue_tables_keep_the_first_day_each_company_appears(tmp_path):
    from quant_platform.research.history.chips_daily import fetch_revenue, parse_revenue

    assert parse_revenue([revenue_row("2330"), revenue_row("00878"), revenue_row("1101", revenue="-")]) == [
        {"date": date(2026, 8, 1), "code": "2330", "available": date(2026, 9, 17), "revenue": 514_805_337_000.0,
         "year": 2026, "month": 8}]
    base = tmp_path / "history"
    finmind = base / "raw" / "finmind" / "TaiwanStockMonthRevenue"
    finmind.mkdir(parents=True)
    (finmind / "2330.json.gz").write_bytes(gzip.compress(json.dumps({"data": [
        {"revenue_year": 2026, "revenue_month": 8, "revenue": 514_805_337_000, "create_time": "2026-09-10"}]}).encode()))
    tables = {"twse": [[revenue_row("2330"), revenue_row("1101", revenue="13515534")],
                       [revenue_row("2330", compiled="1150920"), revenue_row("1101", compiled="1150920", revenue="13515534"),
                        revenue_row("1102", compiled="1150920", revenue="100")]],
              "tpex": [[revenue_row("6488", revenue="4764363")], [revenue_row("6488", revenue="4764363")]]}
    for night in (0, 1):
        client = OfficialHistoryClient(base / "raw", min_interval=0,
                                       fetch_json=lambda url, night=night: tables["twse" if "twse" in url else "tpex"][night])
        fetch_revenue(base, client)
    kept = sorted(path.name for path in (base / "raw" / "official_revenue").rglob("*.json"))
    assert kept == ["202608-20260917.json", "202608-20260917.json", "202608-20260920.json"]
    build(base)
    rows = {row["code"]: row for row in pq.read_table(base / "chips" / "TaiwanStockMonthRevenue.parquet").to_pylist()}
    assert rows["2330"]["available"] == date(2026, 9, 10)                 # FinMind's own announcement date stays
    assert rows["1101"]["available"] == date(2026, 9, 17) and rows["1101"]["revenue"] == 13_515_534_000
    assert rows["1102"]["available"] == date(2026, 9, 20)                 # a late reporter: its first table
    assert rows["6488"]["revenue"] == 4_764_363_000


def test_the_dealers_own_trading_and_hedging_come_from_the_full_reports():
    """2026-10-16: T86 columns 14 and 17, dailyTrade 16 and 19 (TSMC and 環球晶 on 10-15)."""
    t86 = {"stat": "OK", "fields": ["證券代號", "證券名稱"] + [f"f{i}" for i in range(2, 19)],
           "data": [["2330", "台積電", "13,293,478", "47,404,843", "-34,111,365", "0", "0", "0", "3,305,000", "70,000",
                     "3,235,000", "7,415,881", "150,838", "215,953", "-65,115", "8,874,320", "1,393,324", "7,480,996",
                     "-23,460,484"]]}
    row = parse("twse", "insti", t86, DAY)[0]
    assert row["dealer_self_net"] == -65_115 and row["dealer_hedge_net"] == 7_480_996 and row["trust_net"] == 3_235_000
    daily = {"stat": "ok", "tables": [{"fields": ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 7 + ["合計"],
                                        "data": [["6488", "環球晶", "6,097,707", "8,211,537", "-2,113,830", "0", "0", "0",
                                                  "6,097,707", "8,211,537", "-2,113,830", "306,600", "28,000", "278,600",
                                                  "206,548", "388,030", "-181,482", "1,491,683", "1,589,873", "-98,190",
                                                  "1,698,231", "1,977,903", "-279,672", "-2,114,902"]]}]}
    row = parse("tpex", "insti", daily, DAY)[0]
    assert row["dealer_self_net"] == -181_482 and row["dealer_hedge_net"] == -98_190 and row["trust_net"] == 278_600
