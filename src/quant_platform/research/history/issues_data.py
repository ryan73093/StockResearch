"""2026-10-27 (使用者：再加一個議題追蹤，針對現在市場上的議題進行追蹤 — AI 科技巨頭支出可能帶來的風險、營收、景氣；
台灣房市跟股市): the public data the issue pages read, each kept where the research data lives (``history``).

* SEC company facts (data.sec.gov's XBRL API, free, no key): every AI spender's and supplier's capital expenditure,
  revenue, operating cash flow, depreciation and long-term debt as filed (10-Q, 10-K). The raw answer is kept under
  ``raw/sec/<ticker>.json`` and read again once a day; ``quarters`` turns the year-to-date cash-flow figures into
  quarters.
* The central bank's monthly tables (ODS files linked from its statistics pages; a file's address changes every month,
  so the page is read for it): the five big banks' new home loans (amount, rate) and all banks' home-purchase and
  construction loan balances, ``raw/cbc/<name>.ods``. Read with the standard library (an ODS is a zip of XML).
* The interior ministry's real-price registrations (實價登錄): each season's bulk file (about 16 MB) is read in memory
  and only the homes kept — each transaction's city, month, kind (成屋 / 預售屋), total price and unit price without the
  parking — to ``issues/lvr.parquet`` (the zips are not kept).
* The exchange's sector indices, read from the MI_INDEX tables the stock files already keep
  (``raw/twse_stock_all/<year>/<day>.json``): ``issues/sectors.parquet``.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ElementTree
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from quant_platform.research import files  # 2026-10-29: replace, tried again while read

USER_AGENT = "StockResearch research platform (personal, non-commercial)"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
# the five that spend (the clouds and Meta), the two that sell them the chips, one that borrows to build
SPENDERS = {"MSFT": (789019, "微軟"), "GOOGL": (1652044, "Alphabet"), "AMZN": (1018724, "亞馬遜"),
            "META": (1326801, "Meta"), "ORCL": (1341439, "甲骨文")}
SUPPLIERS = {"NVDA": (1045810, "輝達"), "AVGO": (1730168, "博通")}
BORROWERS = {"CRWV": (1769628, "CoreWeave")}
COMPANIES = {**SPENDERS, **SUPPLIERS, **BORROWERS}
# each concept's tags, the one with the latest figure taken (companies changed tags over the years)
CONCEPTS = {
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"),
    "ocf": ("NetCashProvidedByUsedInOperatingActivities",),
    "depreciation": ("DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
                     "DepreciationAndAmortization", "Depreciation"),
    "debt": ("LongTermDebtNoncurrent", "LongTermNotesAndLoans", "LongTermDebt"),      # Oracle: notes and loans
}
INSTANTS = {"debt"}
CBC_PAGES = {"5newloan": "https://www.cbc.gov.tw/tw/cp-528-1079-B4682-1.html",
             "consumer": "https://www.cbc.gov.tw/tw/cp-526-1078-7BD41-1.html"}
CBC_FILES = {"5newloan": "5newloan.ods", "consumer": "CONSUMER.ods"}
LVR_SEASON = "https://plvr.land.moi.gov.tw/DownloadSeason?season={season}&type=zip&fileName=lvr_landcsv.zip"
LVR_CURRENT = "https://plvr.land.moi.gov.tw/Download?type=zip&fileName=lvr_landcsv.zip"
LVR_FIRST = (112, 1)                 # 2023 Q1: two full years before the months the pages compare
SIX = ("臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市")
SECTORS = {"發行量加權股價指數": "taiex", "建材營造類指數": "construction", "金融保險類指數": "finance",
           "半導體類指數": "semiconductor", "電腦及週邊設備類指數": "computer"}
SECTOR_START = date(2018, 1, 1)


def _get(url: str, timeout: int = 120) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=timeout) as response:   # noqa: S310 - fixed public addresses
        return response.read()


# --- SEC --------------------------------------------------------------------------------------------------------------
def fetch_sec(history: Path, get=_get, pause: float = 0.3) -> dict[str, int]:
    """Every company's facts, kept raw; a company that does not answer keeps its last file."""
    folder = Path(history) / "raw" / "sec"
    folder.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for ticker, (cik, _name) in COMPANIES.items():
        try:
            payload = get(SEC_FACTS.format(cik=cik))
            json.loads(payload)
        except Exception:  # noqa: BLE001 - one company down does not stop the others
            continue
        partial = folder / f"{ticker}.partial"
        partial.write_bytes(payload)
        files.replace(partial, folder / f"{ticker}.json", wait=120.0)
        sizes[ticker] = len(payload)
        time.sleep(pause)                       # SEC asks for at most 10 requests a second
    return sizes


def quarters(entries: list[dict]) -> dict[date, float]:
    """Quarter-end -> the quarter's figure from a duration fact's filings: a three-month figure as filed, else the
    year-to-date figure less the one ending a quarter earlier from the same start (cash flows are filed year to date;
    the fourth quarter is the year less nine months). The latest filing of a span wins."""
    spans: dict[tuple[date, date], tuple[str, float]] = {}
    for entry in entries:
        if not entry.get("start") or entry.get("form", "").split("/")[0] not in ("10-Q", "10-K"):
            continue
        span = (date.fromisoformat(entry["start"]), date.fromisoformat(entry["end"]))
        filed = entry.get("filed") or ""
        if span not in spans or filed >= spans[span][0]:
            spans[span] = (filed, float(entry["val"]))
    output: dict[date, float] = {}
    for (start, end), (_filed, value) in spans.items():
        if 80 <= (end - start).days <= 100:
            output[end] = value
    for (start, end), (_filed, value) in sorted(spans.items()):
        if (end - start).days > 100 and end not in output:
            earlier = [figure for (first, last), (_f, figure) in spans.items()
                       if first == start and 80 <= (end - last).days <= 100]
            if earlier:
                output[end] = value - earlier[0]
    return dict(sorted(output.items()))


def instants(entries: list[dict]) -> dict[date, float]:
    """Period-end -> the balance as last filed (10-Q, 10-K)."""
    output: dict[date, tuple[str, float]] = {}
    for entry in entries:
        if entry.get("start") or entry.get("form", "").split("/")[0] not in ("10-Q", "10-K"):
            continue
        end = date.fromisoformat(entry["end"])
        filed = entry.get("filed") or ""
        if end not in output or filed >= output[end][0]:
            output[end] = (filed, float(entry["val"]))
    return {end: value for end, (_filed, value) in sorted(output.items())}


def company_series(payload: dict) -> dict[str, dict[date, float]]:
    """concept -> period end -> figure (US$) for one company's facts."""
    facts = (payload.get("facts") or {}).get("us-gaap") or {}
    output: dict[str, dict[date, float]] = {}
    for concept, tags in CONCEPTS.items():
        # a company changes tags over the years (Alphabet's revenue: Revenues, then the contract-revenue tag in
        # 2018-2025, then Revenues again): the tag with the latest figure first, the others filling its gaps
        found = []
        for tag in tags:
            entries = ((facts.get(tag) or {}).get("units") or {}).get("USD") or []
            series = instants(entries) if concept in INSTANTS else quarters(entries)
            if series:
                found.append(series)
        merged: dict[date, float] = {}
        ordered = sorted(found, key=lambda item: max(item), reverse=True)
        if concept in INSTANTS:               # a balance from one tag only (another tag may count the current part)
            ordered = ordered[:1]
        for series in ordered:
            for end, value in series.items():
                merged.setdefault(end, value)
        output[concept] = dict(sorted(merged.items()))
    return output


def read_sec(history: Path) -> dict[str, dict[str, dict[date, float]]]:
    folder = Path(history) / "raw" / "sec"
    output = {}
    for ticker in COMPANIES:
        path = folder / f"{ticker}.json"
        if path.is_file():
            output[ticker] = company_series(json.loads(path.read_text(encoding="utf-8")))
    return output


def calendar_quarter(end: date) -> str:
    """The calendar quarter most of a fiscal quarter falls in (Oracle's ends in Aug/Nov/Feb/May, Nvidia's late in
    Jan/Apr/Jul/Oct): the quarter of the day 45 days before its end."""
    middle = end - timedelta(days=45)
    return f"{middle.year}Q{(middle.month - 1) // 3 + 1}"


# --- the central bank ---------------------------------------------------------------------------------------------------
def ods_rows(payload: bytes, sheet: str | None = None) -> list[list[str]]:
    """The first (or ``sheet``) table of an ODS file as text rows (repeated cells expanded, empty rows left out)."""
    table_ns = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
    text_ns = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
    root = ElementTree.fromstring(zipfile.ZipFile(io.BytesIO(payload)).read("content.xml"))
    for table in root.iter(f"{{{table_ns}}}table"):
        if sheet and table.get(f"{{{table_ns}}}name") != sheet:
            continue
        rows = []
        for row in table.iter(f"{{{table_ns}}}table-row"):
            cells: list[str] = []
            for cell in row.findall(f"{{{table_ns}}}table-cell"):
                repeat = int(cell.get(f"{{{table_ns}}}number-columns-repeated", "1"))
                value = "".join("".join(part.itertext()) for part in cell.iter(f"{{{text_ns}}}p")).strip()
                cells += [value] * min(repeat, 30)
            if any(cells):
                rows.append(cells)
        return rows
    return []


def _number(text: str) -> float | None:
    text = (text or "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def roc_month(text: str) -> date | None:
    """'115/9' or '11509' (ROC year and month) -> the month's first day."""
    text = (text or "").strip()
    found = re.fullmatch(r"(\d{2,3})/(\d{1,2})", text) or re.fullmatch(r"(\d{2,3})(\d{2})", text)
    if not found:
        return None
    year, month = int(found.group(1)) + 1911, int(found.group(2))
    return date(year, month, 1) if 1 <= month <= 12 else None


def five_banks(payload: bytes) -> list[dict]:
    """The five big banks' new loans: month, home-loan amount (NT$ million) and rate (% a year)."""
    output = []
    for row in ods_rows(payload):
        month = roc_month(row[0]) if row else None
        if month and len(row) > 2 and _number(row[1]) is not None:
            output.append({"month": month, "home_amount": _number(row[1]), "home_rate": _number(row[2])})
    return output


def consumer_loans(payload: bytes) -> list[dict]:
    """All banks' balances at each month's end: home-purchase loans and construction loans (NT$ million)."""
    output = []
    for row in ods_rows(payload, "WWW"):
        month = roc_month(row[0]) if row else None
        if month and len(row) > 8 and _number(row[2]) is not None:
            output.append({"month": month, "home_balance": _number(row[2]), "construction_balance": _number(row[8])})
    return output


def fetch_cbc(history: Path, get=_get) -> dict[str, int]:
    """Each table's ODS, found on its page; a page or file that does not answer keeps the last file."""
    folder = Path(history) / "raw" / "cbc"
    folder.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for name, page in CBC_PAGES.items():
        try:
            html = get(page).decode("utf-8", "replace")
            found = re.search(r'href="(https://www\.cbc\.gov\.tw/tw/dl-\d+-[0-9a-f]+\.html)"\s+title="'
                              + re.escape(CBC_FILES[name]) + '"', html)
            if not found:
                continue
            payload = get(found.group(1))
            ods_rows(payload)                    # an answer that is not an ODS is not kept
        except Exception:  # noqa: BLE001
            continue
        (folder / f"{name}.ods").write_bytes(payload)
        sizes[name] = len(payload)
    return sizes


def read_cbc(history: Path) -> dict[str, list[dict]]:
    folder = Path(history) / "raw" / "cbc"
    output: dict[str, list[dict]] = {"5newloan": [], "consumer": []}
    if (folder / "5newloan.ods").is_file():
        output["5newloan"] = five_banks((folder / "5newloan.ods").read_bytes())
    if (folder / "consumer.ods").is_file():
        output["consumer"] = consumer_loans((folder / "consumer.ods").read_bytes())
    return output


# --- real-price registrations -------------------------------------------------------------------------------------------
HOME_TYPES = ("住宅大樓", "華廈", "公寓", "透天厝", "套房")
# a sale between relatives, a defect, a rush, a share of a building... is not a market price
SPECIAL = ("親友", "員工", "共有人", "特殊關係", "瑕疵", "急買急賣", "債權債務", "政府機關", "法院", "地上權", "毛胚",
           "畸零", "含增建", "未登記建物")
PING = 3.305785                      # square metres in a 坪


def _cities(archive: zipfile.ZipFile) -> dict[str, str]:
    """File letter -> city, from the archive's manifest ('臺北市不動產買賣')."""
    output = {}
    if "manifest.csv" in archive.namelist():
        for row in csv.reader(io.StringIO(archive.read("manifest.csv").decode("utf-8-sig"))):
            if len(row) >= 3 and row[0].endswith("_lvr_land_a.csv"):
                output[row[0][0]] = row[2].replace("不動產買賣", "")
    return output


def lvr_rows(payload: bytes) -> list[dict]:
    """The homes in one bulk file: sales of existing homes (``_a``) and pre-sales (``_b``, the cancelled left out)."""
    archive = zipfile.ZipFile(io.BytesIO(payload))
    cities = _cities(archive)
    output = []
    for name in archive.namelist():
        found = re.fullmatch(r"([a-z])_lvr_land_([ab])\.csv", name)
        if not found or found.group(1) not in cities:
            continue
        kind = "existing" if found.group(2) == "a" else "presale"
        reader = csv.DictReader(io.StringIO(archive.read(name).decode("utf-8-sig")))
        for row in reader:
            when = (row.get("交易年月日") or "").strip()
            if not when.isdigit() or len(when) < 6:
                continue                         # the English header line, a row without a date
            if "建物" not in (row.get("交易標的") or "") or not (row.get("建物型態") or "").startswith(HOME_TYPES):
                continue
            if "住" not in (row.get("主要用途") or "") and kind == "existing":
                continue
            note = (row.get("備註") or "") + (row.get("解約情形") or "")
            if any(word in note for word in SPECIAL) or (kind == "presale" and "解約" in note):
                continue
            total, parking = _number(row.get("總價元")), _number(row.get("車位總價元")) or 0.0
            area, parking_area = _number(row.get("建物移轉總面積平方公尺")), _number(row.get("車位移轉總面積平方公尺")) or 0.0
            if not total or not area or area - parking_area < 10:
                continue
            roc = int(when[:-4])
            try:
                month = date(roc + 1911, int(when[-4:-2]), 1)
            except ValueError:
                continue
            unit = (total - parking) / (area - parking_area) * PING / 10000          # 萬元 a 坪
            if not 3 <= unit <= 600:
                continue
            output.append({"serial": row.get("編號") or "", "city": cities[found.group(1)], "kind": kind,
                           "month": month, "total": total, "unit": round(unit, 3)})
    return output


def seasons(today: date, first: tuple[int, int] = LVR_FIRST) -> list[str]:
    """Every season's name from ``first`` to the last one published (a season's file comes out after its last
    registration day, the 10th of the month after the quarter)."""
    output = []
    year, quarter = first
    while True:
        last_day = date(year + 1911, quarter * 3, 10) + timedelta(days=40)
        if last_day > today:
            break
        output.append(f"{year}S{quarter}")
        year, quarter = (year, quarter + 1) if quarter < 4 else (year + 1, 1)
    return output


def fetch_lvr(history: Path, today: date, get=_get, full: bool = False) -> dict[str, int]:
    """The seasons not kept yet (all with ``full``), the latest season again and the current period; the homes kept
    in ``issues/lvr.parquet``, one row a transaction (by its serial: a registration seen again replaces the old)."""
    import pandas as pd

    path = Path(history).parent / "issues" / "lvr.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = pd.read_parquet(path) if path.is_file() and not full else pd.DataFrame()
    done = set(kept["season"].unique()) if len(kept) else set()
    wanted = seasons(today)
    todo = [name for name in wanted if name not in done] or []
    if wanted and wanted[-1] not in todo:
        todo.append(wanted[-1])
    frames, counts = [kept] if len(kept) else [], {}
    for name in todo + ["current"]:
        try:
            payload = get(LVR_SEASON.format(season=name) if name != "current" else LVR_CURRENT, timeout=600)
            rows = lvr_rows(payload)
        except Exception:  # noqa: BLE001 - a season that does not answer is tried again next time
            continue
        frame = pd.DataFrame(rows)
        if not len(frame):
            continue
        frame["season"] = name
        if frames:
            frames = [part[part["season"] != name] for part in frames]
        frames.append(frame)
        counts[name] = len(frame)
    if not frames:
        return counts
    merged = pd.concat(frames, ignore_index=True)
    merged["month"] = pd.to_datetime(merged["month"]).dt.date
    order = {name: index for index, name in enumerate(wanted + ["current"])}
    merged["order"] = merged["season"].map(order).fillna(-1)
    merged = merged.sort_values("order").drop_duplicates("serial", keep="last").drop(columns="order")
    partial = path.with_suffix(".partial")
    merged.to_parquet(partial, index=False)
    files.replace(partial, path, wait=120.0)
    return counts


# --- the market's margin loans ------------------------------------------------------------------------------------------
MARGIN_DATASET = "TaiwanStockTotalMarginPurchaseShortSale"
MAINTENANCE_DATASET = "TaiwanTotalExchangeMarginMaintenance"     # the market's margin maintenance ratio (%)


def fetch_margin(history: Path, token: str, today: date, get=None) -> int:
    """The listed market's margin-loan balance (NT$) every session from 2018 (FinMind's market totals), to
    ``issues/margin.parquet``."""
    import urllib.parse

    import pandas as pd

    from quant_platform.research.history.finmind import URL

    if not token:
        return 0
    query = urllib.parse.urlencode({"dataset": MARGIN_DATASET, "start_date": "2018-01-01", "end_date": today.isoformat()})
    if get is None:
        request = urllib.request.Request(f"{URL}?{query}", headers={"Authorization": f"Bearer {token}",
                                                                     "User-Agent": "StockResearch/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response:   # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    else:
        payload = get(query)
    rows = [{"date": row["date"], "balance": float(row["TodayBalance"])} for row in payload.get("data") or []
            if row.get("name") == "MarginPurchaseMoney" and row.get("TodayBalance")]
    if not rows:
        return 0
    path = Path(history).parent / "issues" / "margin.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).drop_duplicates("date").sort_values("date").to_parquet(path, index=False)
    # 2026-10-28 (the leverage project): the market's margin maintenance ratio (%), the same way
    query = urllib.parse.urlencode({"dataset": MAINTENANCE_DATASET, "start_date": "2018-01-01", "end_date": today.isoformat()})
    if get is None:
        request = urllib.request.Request(f"{URL}?{query}", headers={"Authorization": f"Bearer {token}",
                                                                     "User-Agent": "StockResearch/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response:   # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
    else:
        payload = get(query)
    ratios = [{"date": row["date"], "ratio": float(row["TotalExchangeMarginMaintenance"])} for row in payload.get("data") or []
              if row.get("TotalExchangeMarginMaintenance")]
    if ratios:
        pd.DataFrame(ratios).drop_duplicates("date").sort_values("date").to_parquet(path.with_name("maintenance.parquet"),
                                                                                     index=False)
    return len(rows)


# --- sector indices -----------------------------------------------------------------------------------------------------
def sector_rows(payload: dict, day: date) -> dict[str, float]:
    tables = (payload.get("payload", payload) or {}).get("tables") or []
    output = {}
    for table in tables:
        if ((table or {}).get("fields") or [""])[0] != "指數":
            continue
        for item in table.get("data") or []:
            key = SECTORS.get(str(item[0]).strip())
            value = _number(item[1]) if len(item) > 1 else None
            if key and value:
                output[key] = value
    return output


def update_sectors(history: Path) -> int:
    """The sector indices of every day kept raw since SECTOR_START and not read yet, to ``issues/sectors.parquet``."""
    import pandas as pd

    history = Path(history)
    path = history.parent / "issues" / "sectors.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = pd.read_parquet(path) if path.is_file() else pd.DataFrame(columns=["date", *SECTORS.values()])
    seen = {str(day) for day in kept["date"]} if len(kept) else set()
    rows = []
    for year in range(SECTOR_START.year, datetime.now().year + 1):
        folder = history / "raw" / "twse_stock_all" / str(year)
        for file in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            day = datetime.strptime(file.stem, "%Y%m%d").date()
            if day < SECTOR_START or day.isoformat() in seen:
                continue
            try:
                found = sector_rows(json.loads(file.read_text(encoding="utf-8")), day)
            except (OSError, ValueError):
                continue
            if found.get("taiex"):
                rows.append({"date": day.isoformat(), **found})
    if not rows:
        return 0
    merged = pd.concat([kept, pd.DataFrame(rows)], ignore_index=True).drop_duplicates("date", keep="last")
    merged = merged.sort_values("date")
    partial = path.with_suffix(".partial")
    merged.to_parquet(partial, index=False)
    files.replace(partial, path, wait=120.0)
    return len(rows)
