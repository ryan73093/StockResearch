"""2026-10-27 (使用者：再加一個議題追蹤 — AI 科技巨頭支出可能帶來的風險；台灣房市跟股市；首頁做初步警示)."""
import csv
import io
import json
import zipfile
from datetime import date

from quant_platform.research import issues
from quant_platform.research.history import issues_data


def test_cash_flows_filed_year_to_date_become_quarters():
    """Microsoft's fiscal year starts in July: the second quarter is six months less the first, the fourth the year
    less nine months; a three-month figure filed as such is taken as it is; the latest filing of a span wins."""
    def fact(start, end, value, filed="2026-08-01", form="10-Q"):
        return {"start": start, "end": end, "val": value, "filed": filed, "form": form}

    found = issues_data.quarters([
        fact("2025-07-01", "2025-09-30", 10), fact("2025-07-01", "2025-12-31", 25), fact("2025-07-01", "2026-03-31", 45),
        fact("2026-01-01", "2026-03-31", 20), fact("2025-07-01", "2026-06-30", 70, form="10-K"),
        fact("2025-07-01", "2025-09-30", 9, filed="2025-10-01"),             # an earlier filing of the same span
        fact("2025-07-01", "2025-09-30", 99, form="8-K")])                  # not a 10-Q or 10-K
    assert found == {date(2025, 9, 30): 10, date(2025, 12, 31): 15, date(2026, 3, 31): 20, date(2026, 6, 30): 25}
    assert issues_data.instants([{"end": "2026-06-30", "val": 5, "filed": "2026-07-01", "form": "10-K"},
                                 {"end": "2026-06-30", "val": 6, "filed": "2026-08-01", "form": "10-K/A"}]) == {date(2026, 6, 30): 6}


def test_a_fiscal_quarter_goes_to_the_calendar_quarter_most_of_it_falls_in():
    assert issues_data.calendar_quarter(date(2026, 8, 31)) == "2026Q3"      # Oracle: June to August
    assert issues_data.calendar_quarter(date(2026, 7, 26)) == "2026Q2"      # Nvidia: May to July
    assert issues_data.calendar_quarter(date(2026, 2, 28)) == "2026Q1"
    assert issues_data.calendar_quarter(date(2026, 9, 30)) == "2026Q3"


def _ods(rows, name="WWW"):
    table = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    text = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    body = "".join("<table:table-row>" + "".join(f"<table:table-cell><text:p>{cell}</text:p></table:table-cell>" for cell in row)
                   + "</table:table-row>" for row in rows)
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><office:document-content xmlns:office="urn:oasis:names:tc:opendocument:'
           f'xmlns:office:1.0" xmlns:table="{table}" xmlns:text="{text}"><office:body><office:spreadsheet>'
           f'<table:table table:name="{name}">{body}</table:table></office:spreadsheet></office:body></office:document-content>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("content.xml", xml)
    return buffer.getvalue()


def test_the_central_banks_tables_read_from_ods():
    assert issues_data.roc_month("115/9") == date(2026, 9, 1) and issues_data.roc_month("11509") == date(2026, 9, 1)
    assert issues_data.roc_month("年") is None
    five = issues_data.five_banks(_ods([["五大銀行新承做放款"], ["年", "購屋貸款"], ["115/8", "57,165", "2.321"],
                                        ["115/9", "69,783", "2.315"]], name="上網資料"))
    assert five == [{"month": date(2026, 8, 1), "home_amount": 57165.0, "home_rate": 2.321},
                    {"month": date(2026, 9, 1), "home_amount": 69783.0, "home_rate": 2.315}]
    loans = issues_data.consumer_loans(_ods([["年月底"], ["11509", "14,032,891", "12,023,429", "1", "2", "3", "4", "5",
                                              "3,397,229"]]))
    assert loans == [{"month": date(2026, 9, 1), "home_balance": 12023429.0, "construction_balance": 3397229.0}]


def _lvr_zip():
    header = ["鄉鎮市區", "交易標的", "土地位置建物門牌", "交易年月日", "建物型態", "主要用途", "總價元", "車位總價元",
              "建物移轉總面積平方公尺", "車位移轉總面積平方公尺", "備註", "編號", "解約情形"]
    english = ["The villages and towns urban district"] + [""] * (len(header) - 1)

    def table(rows):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(header)
        writer.writerow(english)
        for row in rows:
            writer.writerow([row.get(name, "") for name in header])
        return ("﻿" + buffer.getvalue()).encode("utf-8")

    home = {"交易標的": "房地(土地+建物)+車位", "交易年月日": "1150715", "建物型態": "住宅大樓(11層含以上有電梯)",
            "主要用途": "住家用", "總價元": "10000000", "車位總價元": "2000000", "建物移轉總面積平方公尺": "100",
            "車位移轉總面積平方公尺": "20", "編號": "A1"}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.csv", "﻿name,schema,description\na_lvr_land_a.csv,schema-main.csv,臺北市不動產買賣\n"
                                         "a_lvr_land_b.csv,schema-main-sale.csv,臺北市預售屋買賣\n".encode("utf-8"))
        archive.writestr("a_lvr_land_a.csv", table([
            home,
            {**home, "編號": "A2", "備註": "親友、員工、共有人或其他特殊關係間之交易；"},       # not a market price
            {**home, "編號": "A3", "主要用途": "商業用"},                                         # not a home
            {**home, "編號": "A4", "交易標的": "車位"}]))                                        # a parking space
        archive.writestr("a_lvr_land_b.csv", table([
            {**home, "編號": "B1", "交易年月日": "1150801"},
            {**home, "編號": "B2", "解約情形": "115年9月1日解約"}]))                             # cancelled
    return buffer.getvalue()


def test_the_real_price_file_keeps_the_homes_at_their_price_without_parking():
    rows = issues_data.lvr_rows(_lvr_zip())
    assert [(row["serial"], row["kind"], row["city"], row["month"]) for row in rows] == [
        ("A1", "existing", "臺北市", date(2026, 7, 1)), ("B1", "presale", "臺北市", date(2026, 8, 1))]
    assert abs(rows[0]["unit"] - 8_000_000 / 80 * 3.305785 / 10000) < 0.001                # 33.06 萬元 a 坪


def test_a_season_seen_again_replaces_its_rows_and_the_seasons_published(tmp_path):
    payload = _lvr_zip()
    history = tmp_path / "history"
    history.mkdir()
    asked = []

    def get(url, timeout=0):
        asked.append(url)
        if "115S3" in url or "Download?" in url:
            return payload
        raise OSError("not in this test")

    assert issues_data.seasons(date(2026, 10, 27), first=(115, 1)) == ["115S1", "115S2", "115S3"]
    assert issues_data.seasons(date(2026, 10, 19), first=(115, 1)) == ["115S1", "115S2"]
    counts = issues_data.fetch_lvr(history, date(2026, 10, 27), get=get)
    assert counts == {"115S3": 2, "current": 2}
    import pandas as pd

    kept = pd.read_parquet(tmp_path / "issues" / "lvr.parquet")
    assert sorted(kept["serial"]) == ["A1", "B1"]                                  # the same serials kept once


def test_grades_and_the_worst():
    assert issues.grade(None, 0.2, 0.0) == "none"
    assert issues.grade(0.25, watch=0.2, alert=0.0) == "ok" and issues.grade(0.1, watch=0.2, alert=0.0) == "watch"
    assert issues.grade(-0.01, watch=0.2, alert=0.0) == "alert"
    assert issues.grade(0.4, watch=0.25, alert=0.35, below=False) == "alert"
    assert issues.worst("ok", "watch", "none") == "watch"


def _facts(quarterly):
    """A company's facts with three-month figures: concept -> [(end, value)]."""
    tags = {"capex": "PaymentsToAcquirePropertyPlantAndEquipment", "revenue": "Revenues",
            "ocf": "NetCashProvidedByUsedInOperatingActivities", "depreciation": "Depreciation"}
    facts = {}
    for concept, rows in quarterly.items():
        entries = []
        for end, value in rows:
            finish = date.fromisoformat(end)
            start = date(finish.year, finish.month - 2, 1)
            entries.append({"start": start.isoformat(), "end": end, "val": value, "filed": "2026-10-01", "form": "10-Q"})
        facts[tags[concept]] = {"units": {"USD": entries}}
    return {"facts": {"us-gaap": facts}}


def test_the_spenders_combined_and_graded(tmp_path, monkeypatch):
    """Five companies spending 10 a quarter a year ago and 14 now (+40%), on revenue 45 (31%: past the 25% watch line,
    under the 35% alert) and cash 18 (78%: past 75%); one of them burning cash; depreciation up 50% on flat revenue."""
    research = tmp_path / "research"
    folder = research / "history" / "raw" / "sec"
    folder.mkdir(parents=True)
    ends = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    for number, ticker in enumerate(issues_data.SPENDERS):
        capex = [2, 2, 2, 2, 2.4, 2.8]
        ocf = [4] * 6 if number else [4, 4, 4, 4, 4, 2.0]
        payload = _facts({"capex": list(zip(ends, capex)), "revenue": list(zip(ends, [9] * 6)),
                          "ocf": list(zip(ends, ocf)), "depreciation": list(zip(ends, [1, 1, 1, 1, 1, 1.5]))})
        (folder / f"{ticker}.json").write_text(json.dumps(payload), encoding="utf-8")
    payload = issues.build(research, today=date(2026, 10, 27))
    ai = next(topic for topic in payload["topics"] if topic["key"] == "ai")
    found = {item["id"]: item for item in ai["indicators"]}
    assert ai["series"]["quarters"][-1]["x"] == "2026Q2" and round(ai["series"]["quarters"][-1]["capex"] * 1e8, 6) == 14
    assert abs(found["ai_capex_growth"]["value"] - 0.4) < 1e-9 and found["ai_capex_growth"]["level"] == "ok"
    assert abs(found["ai_capex_intensity"]["value"] - 14 / 45) < 1e-9 and found["ai_capex_intensity"]["level"] == "watch"
    assert abs(found["ai_capex_cash"]["value"] - 14 / 18) < 1e-9 and found["ai_capex_cash"]["level"] == "watch"
    assert found["ai_free_cash"]["level"] == "alert" and found["ai_free_cash"]["value"] == 1      # 2.0 - 2.8 < 0
    assert abs(found["ai_depreciation"]["value"] - 0.5) < 1e-9 and found["ai_depreciation"]["level"] == "alert"
    assert found["ai_tsmc"]["level"] == "none"                                       # no monthly revenue in the test
    assert (research / "issues" / "latest.json").is_file()
    log = (research / "issues" / "log.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(line)["id"] == "ai_free_cash" and json.loads(line)["to"] == "alert" for line in log)
    issues.build(research, today=date(2026, 10, 27))                               # nothing changed: nothing logged
    assert len((research / "issues" / "log.jsonl").read_text(encoding="utf-8").splitlines()) == len(log)
    head = issues.headline(issues.read(research))
    assert head["topics"][0]["level"] == "alert" and head["topics"][0]["flagged"][0]["level"] == "alert"
    drawn = issues.charts(issues.read(research))
    assert "五巨頭合計" in drawn["ai_cash"] and "ai_companies" in drawn


def test_the_page_and_the_home_card(tmp_path):
    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.dashboard.app import create_app

    research = tmp_path / "research"
    (research / "issues").mkdir(parents=True)
    item = {"id": "ai_tsmc", "name": "台積電月營收（近 3 個月合計）年增率", "value": -0.02, "value_text": "-2.0%",
            "as_of": "2026-09", "level": "alert", "rank": 3, "detail": "", "what": "台積電每月營收", "why": "AI 需求溫度計",
            "watch": "低於 +15%", "alert": "轉負", "source": "月營收", "frequency": "每月"}
    topic = {"key": "ai", "title": "AI 科技巨頭資本支出", "short": "AI 資本支出", "summary": "巨頭的錢", "indicators": [item],
             "companies": [], "debt": [], "reported": {}, "news": [], "series": {"quarters": [], "taiwan": [], "stocks": []}}
    payload = {"built_at": "2026-10-27T18:20:00+08:00", "today": "2026-10-27", "topics": [topic],
               "counts": {"ok": 0, "watch": 0, "alert": 1, "none": 0}}
    (research / "issues" / "latest.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    client = create_app(build_container(Settings(database_url=f"sqlite:///{tmp_path / 'v2.db'}",
                                                 scheduler_in_web=False))).test_client()
    page = client.get("/issues").get_data(as_text=True)
    assert "議題追蹤" in page and "台積電月營收" in page and "告警" in page and "追蹤什麼、為什麼" in page
    assert '<a href="/issues" aria-current="page">' in page                                  # in the main navigation
    home = client.get("/").get_data(as_text=True)
    assert "議題警示" in home and "台積電月營收（近 3 個月合計）年增率：-2.0%" in home
