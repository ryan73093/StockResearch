import json
from datetime import date

import pytest

from quant_platform.research.history import reductions
from quant_platform.research.legacy_challenger import LegacyData, exchange_events
from quant_platform.research.stock_rules import Panel, unit_factors_all

FIELDS = ["恢復買賣日期", "股票代號", "名稱", "停止買賣前收盤價格", "恢復買賣參考價", "漲停價格", "跌停價格",
          "開盤競價基準", "除權參考價", "減資原因", "詳細資料"]


def write(raw, year, rows):
    folder = raw / "twse_reductions"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{year}.json").write_text(json.dumps({"stat": "OK", "fields": FIELDS, "data": rows},
                                                    ensure_ascii=False), encoding="utf-8")


def test_parse_events_and_limits(tmp_path):
    raw = tmp_path / "raw"
    write(raw, 2024, [["113/01/22", "1234", "某公司", "10.65", "19.69", "21.65", "17.75", "19.69", "", "彌補虧損", ""],
                      ["113/03/04", "5678", "另一家", "50.00", "45.00", "49.50", "40.50", "45.00", "", "退還股款", ""],
                      ["113/05/02", "9999", "壞資料", "--", "12.00", "", "", "", "", "彌補虧損", ""]])
    write(raw, 2014, [["103/02/03", "1234", "某公司", "8.00", "10.00", "10.70", "9.30", "10.00", "", "彌補虧損", ""]])
    events = reductions.events(raw, 2015)
    assert events == {"1234.TW": {date(2024, 1, 22): ("reduction", 10.65, 19.69)},
                      "5678.TW": {date(2024, 3, 4): ("reduction", 50.0, 45.0)}}       # 2014 and the bad row left out
    assert reductions.limits(raw, 2015)["1234.TW"][date(2024, 1, 22)] == (17.75, 21.65)
    assert reductions.parse({"stat": "很抱歉，沒有符合條件的資料!"}) == []


TPEX_FIELDS = ["恢復買賣日期", "股票代號", "名稱", "最後交易日之收盤價格", "減資恢復買賣開始日參考價格", "漲停價格",
               "跌停價格", "開始交易基準價", "除權參考價", "減資原因", "詳細資料"]
TPEX_DETAIL = ("<table><tr><th>每壹仟股換發新股票:</th><td>792.00000000&nbsp股</td></tr>"
               "<tr><th>每股退還股款:</th><td>0.50000000&nbsp元/股</td></tr></table>")
DETAIL = {"stat": "OK", "fields": ["股票代號：", "股票名稱：", "停止買賣日期：", "每壹仟股換發新股票：", "每股退還股款："],
          "data": [["3308  ", "聯德", "113/03/21", "855.66635000 股", "1.443336 元/股"]]}


def test_fetch_asks_both_exchanges_every_year_and_each_twse_detail_once(tmp_path):
    from quant_platform.research.history.official import OfficialHistoryClient

    asked = []

    def fake(url):
        asked.append(url)
        if "TWTAVUDetail" in url:
            return DETAIL
        if "tpex" in url:
            return {"stat": "ok", "tables": [{"fields": TPEX_FIELDS, "data": [
                ["1130205", "3064", "泰偉", "10.65", "35.50", "39.05", "31.95", "35.50", "0.00", "彌補虧損", TPEX_DETAIL]]}]}
        return {"stat": "OK", "fields": FIELDS,
                "data": [["113/04/01", "3308", "聯德", "28.20", "31.26", "34.35", "28.15", "", "", "退還股款", "3308  ,20240320"]]}

    client = OfficialHistoryClient(tmp_path / "raw", fetch_json=fake, min_interval=0, today=lambda: date(2025, 6, 1))
    assert reductions.fetch(client, date(2025, 6, 1), 2024) == 4
    assert "startDate=20240101&endDate=20241231" in asked[0] and "startDate=2024/01/01&endDate=2024/12/31" in asked[1]
    assert "STK_NO=3308&FILE_DATE=20240320" in asked[2] and "endDate=20250601" in asked[3]
    reductions.fetch(client, date(2025, 6, 1), 2024)
    assert not any("2024" in url and "TWTAVUDetail" not in url for url in asked[5:])   # 2024 closed: from the cache
    assert sum("TWTAVUDetail" in url for url in asked) == 1                              # a detail is kept for good
    terms = reductions.exchanges(tmp_path / "raw", 2024)
    assert terms["3308.TW"][date(2024, 4, 1)] == pytest.approx((0.85566635, 1.443336))
    assert terms["3064.TWO"][date(2024, 2, 5)] == pytest.approx((0.792, 0.5))
    assert reductions.events(tmp_path / "raw", 2024)["3064.TWO"][date(2024, 2, 5)] == ("reduction", 10.65, 35.5)
    # the reference is (before − money back) ÷ new shares per old share: (28.20 − 1.443336) ÷ 0.85566635 = 31.27
    assert (28.20 - 1.443336) / 0.85566635 == pytest.approx(31.26, abs=0.02)


def test_the_engine_exchanges_the_shares_and_pays_the_money_back():
    """1,000 shares through 聯德's reduction: 855 new shares (855.66 rounded down) and NT$1,443.34 back."""
    from datetime import timedelta

    from quant_platform.research.costs import CostModel
    from quant_platform.research.daily import DailyRule, simulate_daily
    from quant_platform.research.stock_rules import SeedPlan

    days, day = [], date(2024, 3, 18)
    while len(days) < 12:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    resume = days[8]
    closes = {d: 28.2 for d in days[:4]} | {d: 31.26 for d in days[8:]}     # suspended sessions 4..7
    data = LegacyData(sessions=days, closes={"3308.TW": closes, "0050.TW": {d: 100.0 for d in days}},
                      traded_value={"3308.TW": {d: 1e12 for d in days}, "0050.TW": {d: 1e12 for d in days}},
                      factors={"3308.TW": {resume: 28.2 / 31.26}}, predictions={},
                      notes={"reductions": {"3308.TW": {resume: (0.85566635, 1.443336)}}})
    free = CostModel(fee_rate=0.0, minimum_fee=0.0, slippage_bps=0.0)
    ledger, snapshots = [], {}
    rule = DailyRule(name="x", factors={"momentum_3": 1.0}, top=3, min_hold=0, check="weekly")
    run = simulate_daily(data, rule, free, days[0], days[-1], {days[0]: ["3308.TW"]}, SeedPlan(28_200, 0),
                         ledger=ledger, snapshots=snapshots)
    assert snapshots[days[7]][1]["3308.TW"] == 1000
    assert snapshots[resume][1]["3308.TW"] == 855
    assert [row["cash"] for row in ledger if row["side"] == "REFUND"] == [pytest.approx(1443.34, abs=0.01)]
    assert run.values[8] == pytest.approx(855 * 31.26 + 1443.336, abs=0.01)


def test_a_reduction_changes_the_shares_not_the_value(tmp_path):
    """彌補虧損 10.65 → 19.69: 1,000 shares become 541 (10.65/19.69); before, the jump read as an 85% gain."""
    raw = tmp_path / "raw"
    write(raw, 2024, [["113/01/22", "1234", "某公司", "10.65", "19.69", "21.65", "17.75", "", "", "彌補虧損", ""]])
    (raw / "twse_ex_rights").mkdir(parents=True)
    (raw / "tpex_ex_rights").mkdir(parents=True)
    events = exchange_events(raw, 2024)
    assert events["1234.TW"][date(2024, 1, 22)] == ("reduction", 10.65, 19.69)
    days = [date(2023, 12, 20), date(2023, 12, 21), date(2024, 1, 22), date(2024, 1, 23)]
    closes = dict(zip(days, [10.5, 10.65, 19.69, 20.0]))
    factors = unit_factors_all(closes, events["1234.TW"])
    assert factors == {date(2024, 1, 22): pytest.approx(10.65 / 19.69)}
    # money back worth more than the shares' fall (reference below the close before): a factor above 1
    assert unit_factors_all({days[0]: 50.0, days[1]: 45.0}, {days[1]: ("reduction", 50.0, 45.0)})[days[1]] == \
        pytest.approx(50 / 45)
    # the price factors see no jump: the adjusted close is flat over the resume day
    data = LegacyData(sessions=days, closes={"1234.TW": closes}, traded_value={"1234.TW": {d: 1e8 for d in days}},
                      factors={"1234.TW": factors}, predictions={})
    panel = Panel(data)
    assert panel.adjusted[0, 2] == pytest.approx(10.65)


def test_an_official_ex_rights_event_on_the_same_day_wins(tmp_path):
    raw = tmp_path / "raw"
    write(raw, 2025, [["114/07/16", "2330", "台積電", "1,100.00", "1,200.00", "", "", "", "", "退還股款", ""]])
    (raw / "twse_ex_rights").mkdir(parents=True)
    (raw / "tpex_ex_rights").mkdir(parents=True)
    (raw / "twse_ex_rights" / "2025.json").write_text(json.dumps({
        "fields": ["資料日期", "股票代號", "股票名稱", "除權息前收盤價", "除權息參考價", "權值+息值", "權/息"],
        "data": [["114年07月16日", "2330", "台積電", "1,100.00", "1,095.00", "5.00", "息"]]}), encoding="utf-8")
    assert exchange_events(raw, 2025)["2330.TW"][date(2025, 7, 16)] == ("cash", 1100.0, 1095.0)
