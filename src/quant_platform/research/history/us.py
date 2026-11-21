"""2026-11-21 (使用者：美股也很多檔，有跟台灣有相關的產業，還有各種就業數據、通膨數據、利率數據，各種的指標——這些都沒有):
the US side of the research data. Until now the research read nothing American but six index series.

Three kinds, each kept raw under ``history/raw``:

* ``yahoo_us/<symbol>.json.gz`` — daily bars of the US stocks whose business Taiwan's listed companies supply or
  compete with (``US_STOCKS``, by group), Yahoo's chart answer from 2010-01. A US session dated D closes at 05:00
  Taipei on D+1: usable from the Taiwan session after D.
* ``fred/<id>.csv`` — market series nobody revises (yields, spreads, the dollar, oil, financial conditions): FRED's
  graph file. ``DAILY`` gives each one's days from its date to its publication.
* ``alfred/<id>.json.gz`` — the statistics that ARE revised (payrolls, the unemployment rate, prices, production,
  sales, orders...): every vintage ALFRED keeps since 2012. Payrolls of a month are revised twice and again every
  February; reading today's file for 2021 would be reading the future. ``known(vintages, day)`` is the series exactly
  as published by ``day``; a vintage dated V (released 08:30 New York, the evening of V in Taipei) is usable from the
  Taiwan session after V.

No key, no login: the three addresses are public files. One request at a time with a pause.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode
from zoneinfo import ZoneInfo

from quant_platform.research import files

NEW_YORK = ZoneInfo("America/New_York")
USER_AGENT = "StockResearch/1.0 (research; daily data)"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv"
ALFRED = "https://alfred.stlouisfed.org/graph/alfredgraph.csv"
ALFRED_PAGE = "https://alfred.stlouisfed.org/series/downloaddata?seid="
START = date(2010, 1, 1)
VINTAGES_FROM = "2012-01-01"      # the research starts in 2015; three years before it for year-over-year readings
OBSERVATIONS_FROM = "2010-01-01"  # observations kept in each vintage
BATCH = 12                        # ALFRED's graph file carries at most 12 lines
DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# symbol: (name, group). Groups are the Taiwan side's businesses, not a US classification.
US_STOCKS: dict[str, tuple[str, str]] = {
    "NVDA": ("輝達", "AI 晶片與伺服器"), "AMD": ("超微", "AI 晶片與伺服器"), "AVGO": ("博通", "AI 晶片與伺服器"),
    "MRVL": ("邁威爾", "AI 晶片與伺服器"), "SMCI": ("美超微", "AI 晶片與伺服器"), "DELL": ("戴爾", "AI 晶片與伺服器"),
    "HPE": ("慧與", "AI 晶片與伺服器"), "VRT": ("維諦", "AI 晶片與伺服器"), "ARM": ("安謀", "AI 晶片與伺服器"),
    "TSM": ("台積電 ADR", "晶圓製造與設備"), "UMC": ("聯電 ADR", "晶圓製造與設備"), "ASX": ("日月光 ADR", "晶圓製造與設備"),
    "INTC": ("英特爾", "晶圓製造與設備"), "GFS": ("格羅方德", "晶圓製造與設備"), "ASML": ("艾司摩爾", "晶圓製造與設備"),
    "AMAT": ("應用材料", "晶圓製造與設備"), "LRCX": ("科林研發", "晶圓製造與設備"), "KLAC": ("科磊", "晶圓製造與設備"),
    "MU": ("美光", "記憶體與儲存"), "WDC": ("威騰", "記憶體與儲存"), "STX": ("希捷", "記憶體與儲存"),
    "TXN": ("德州儀器", "類比與車用晶片"), "ADI": ("亞德諾", "類比與車用晶片"), "ON": ("安森美", "類比與車用晶片"),
    "NXPI": ("恩智浦", "類比與車用晶片"), "MCHP": ("微晶片", "類比與車用晶片"),
    "AAPL": ("蘋果", "手機與消費電子"), "QCOM": ("高通", "手機與消費電子"), "SWKS": ("思佳訊", "手機與消費電子"),
    "QRVO": ("Qorvo", "手機與消費電子"), "HPQ": ("惠普", "手機與消費電子"), "GLW": ("康寧", "手機與消費電子"),
    "MSFT": ("微軟", "雲端大廠"), "GOOGL": ("字母", "雲端大廠"), "AMZN": ("亞馬遜", "雲端大廠"),
    "META": ("臉書", "雲端大廠"), "ORCL": ("甲骨文", "雲端大廠"),
    "CSCO": ("思科", "網通"), "ANET": ("Arista", "網通"), "CIEN": ("Ciena", "網通"), "COHR": ("Coherent", "網通"),
    "TSLA": ("特斯拉", "電動車與能源"), "FSLR": ("第一太陽能", "電動車與能源"), "ENPH": ("Enphase", "電動車與能源"),
    "CAT": ("開拓重工", "景氣循環"), "FCX": ("自由港銅礦", "景氣循環"), "NUE": ("紐柯鋼鐵", "景氣循環"),
    "DOW": ("陶氏化學", "景氣循環"), "ZIM": ("以星航運", "景氣循環"), "FDX": ("聯邦快遞", "景氣循環"),
    "WMT": ("沃爾瑪", "美國消費"), "NKE": ("耐吉", "美國消費"), "HD": ("家得寶", "美國消費"),
    "JPM": ("摩根大通", "金融"),
    "SMH": ("半導體 ETF", "ETF"), "SOXX": ("費半 ETF", "ETF"), "EWT": ("台灣 ETF", "ETF"), "HYG": ("高收益債 ETF", "ETF"),
    "^SOX": ("費城半導體", "指數"), "^GSPC": ("S&P 500", "指數"), "^IXIC": ("那斯達克", "指數"), "^VIX": ("VIX", "指數"),
    "^TNX": ("十年期殖利率", "指數"), "DX-Y.NYB": ("美元指數", "指數"), "TWD=X": ("美元兌台幣", "指數"),
}

# FRED id: (name, kind, days from the observation's date to its publication)
DAILY: dict[str, tuple[str, str, int]] = {
    "DGS3MO": ("美國 3 個月期公債殖利率", "利率", 1), "DGS2": ("美國 2 年期公債殖利率", "利率", 1),
    "DGS10": ("美國 10 年期公債殖利率", "利率", 1), "DFII10": ("美國 10 年期實質殖利率", "利率", 1),
    "DFF": ("聯邦基金有效利率", "利率", 1), "MORTGAGE30US": ("美國 30 年房貸利率", "利率", 1),
    "T5YIE": ("5 年期損益兩平通膨率", "通膨", 1), "T10YIE": ("10 年期損益兩平通膨率", "通膨", 1),
    "BAMLH0A0HYM2": ("高收益債利差", "信用", 1), "BAMLC0A0CM": ("投資級債利差", "信用", 1),
    "NFCI": ("芝加哥聯儲金融狀況指數", "信用", 6), "STLFSI4": ("聖路易聯儲金融壓力指數", "信用", 7),
    "DTWEXBGS": ("美元廣義指數", "美元", 4), "DCOILWTICO": ("西德州原油", "原物料", 6),
    "WALCL": ("聯準會資產負債表", "流動性", 2), "RRPONTSYD": ("隔夜逆回購餘額", "流動性", 1),
}

# FRED id: (name, kind). Revised statistics: read through ALFRED's vintages.
REVISED: dict[str, tuple[str, str]] = {
    "PAYEMS": ("非農就業人數", "就業"), "UNRATE": ("失業率", "就業"), "ICSA": ("初次請領失業金人數", "就業"),
    "CCSA": ("續領失業金人數", "就業"), "JTSJOL": ("職缺數", "就業"), "CES0500000003": ("平均時薪", "就業"),
    "CPIAUCSL": ("消費者物價指數", "通膨"), "CPILFESL": ("核心消費者物價指數", "通膨"), "PCEPI": ("個人消費支出物價指數", "通膨"),
    "PCEPILFE": ("核心個人消費支出物價指數", "通膨"), "PPIFIS": ("生產者物價指數", "通膨"), "MICH": ("密大一年通膨預期", "通膨"),
    "INDPRO": ("工業生產", "景氣"), "IPG3344S": ("半導體與電子零組件生產", "景氣"), "RSAFS": ("零售銷售", "景氣"),
    "DGORDER": ("耐久財訂單", "景氣"), "NEWORDER": ("核心資本財訂單", "景氣"), "A34SNO": ("電腦與電子產品新訂單", "景氣"),
    "HOUST": ("新屋開工", "景氣"), "PERMIT": ("營建許可", "景氣"), "UMCSENT": ("密大消費者信心", "景氣"),
    "GDPC1": ("實質 GDP", "景氣"), "M2SL": ("M2 貨幣供給", "流動性"),
}


def _get(url: str, timeout: int = 60) -> bytes:
    # 2026-11-21: the St. Louis Fed's file servers leave a request with a client name of our own unanswered (60 s each)
    # and answer the library's own name at once; Yahoo wants a name
    headers = {"Accept-Encoding": "identity"} if "stlouisfed.org" in url else {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:   # noqa: S310 - fixed public addresses
        return response.read()


def _file(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9.-]", "_", symbol)


# --- prices ---------------------------------------------------------------------------------------------------------
def bars(payload: dict) -> dict[date, dict[str, float]]:
    """US session date → close, adjusted close, volume (a day without a close is not kept)."""
    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result:
        return {}
    stamps = result[0].get("timestamp") or []
    indicators = result[0].get("indicators") or {}
    quote_ = (indicators.get("quote") or [{}])[0]
    closes, volumes = quote_.get("close") or [], quote_.get("volume") or []
    adjusted = ((indicators.get("adjclose") or [{}])[0]).get("adjclose") or []
    output = {}
    for index, stamp in enumerate(stamps):
        close = closes[index] if index < len(closes) else None
        if close is None or close <= 0:
            continue
        adj = adjusted[index] if index < len(adjusted) and adjusted[index] else close
        volume = volumes[index] if index < len(volumes) and volumes[index] else 0.0
        output[datetime.fromtimestamp(stamp, NEW_YORK).date()] = {"close": float(close), "adjusted": float(adj),
                                                                    "volume": float(volume)}
    return output


def fetch_prices(history: Path, get=_get, pause: float = 0.6, today: date | None = None,
                 only: tuple[str, ...] | None = None) -> dict[str, int]:
    """Every symbol's (or ``only``'s) whole history; one that does not answer (or answers fewer days than kept) keeps
    its last file."""
    folder = Path(history) / "raw" / "yahoo_us"
    folder.mkdir(parents=True, exist_ok=True)
    end = (today or date.today()) + timedelta(days=2)
    query = urlencode({"period1": int(datetime(START.year, START.month, START.day, tzinfo=NEW_YORK).timestamp()),
                       "period2": int(datetime(end.year, end.month, end.day, tzinfo=NEW_YORK).timestamp()),
                       "interval": "1d", "events": "div,splits", "includeAdjustedClose": "true"})
    counts = {}
    for symbol in (only or tuple(US_STOCKS)):
        path = folder / f"{_file(symbol)}.json.gz"
        try:
            raw = get(f"{YAHOO}{quote(symbol)}?{query}")
            found = bars(json.loads(raw.decode("utf-8")))
        except Exception:  # noqa: BLE001
            raw, found = b"", {}
        kept = len(read_bars(path)) if path.is_file() else 0
        if found and len(found) >= kept:
            files.write_bytes(path, gzip.compress(raw))
            counts[symbol] = len(found)
        time.sleep(pause)
    return counts


def read_bars(path: Path) -> dict[date, dict[str, float]]:
    return bars(json.loads(gzip.decompress(Path(path).read_bytes()).decode("utf-8")))


def prices(history: Path) -> dict[str, dict[date, dict[str, float]]]:
    folder = Path(history) / "raw" / "yahoo_us"
    return {symbol: read_bars(folder / f"{_file(symbol)}.json.gz") for symbol in US_STOCKS
            if (folder / f"{_file(symbol)}.json.gz").is_file()}


# --- series nobody revises ------------------------------------------------------------------------------------------
def parse_graph(text: str) -> dict[date, float]:
    output = {}
    for row in csv.reader(io.StringIO(text.lstrip(chr(0xFEFF)))):
        if len(row) < 2 or not DAY.fullmatch(row[0]) or row[1] in ("", "."):
            continue
        output[date.fromisoformat(row[0])] = float(row[1])
    return output


def fetch_daily(history: Path, get=_get, pause: float = 0.6) -> dict[str, int]:
    folder = Path(history) / "raw" / "fred"
    folder.mkdir(parents=True, exist_ok=True)
    counts = {}
    for series in DAILY:
        path = folder / f"{series}.csv"
        try:
            raw = get(f"{FRED}?{urlencode({'id': series, 'cosd': START.isoformat()})}")
            found = parse_graph(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            raw, found = b"", {}
        kept = len(parse_graph(path.read_text(encoding="utf-8"))) if path.is_file() else 0
        if found and len(found) >= kept:
            files.write_bytes(path, raw)
            counts[series] = len(found)
        time.sleep(pause)
    return counts


def daily(history: Path) -> dict[str, dict[date, float]]:
    """Each series by the day it is PUBLISHED (its date plus ``DAILY``'s days): usable from the Taiwan session after."""
    folder = Path(history) / "raw" / "fred"
    output = {}
    for series, (_name, _kind, lag) in DAILY.items():
        if (folder / f"{series}.csv").is_file():
            values = parse_graph((folder / f"{series}.csv").read_text(encoding="utf-8"))
            output[series] = {day + timedelta(days=lag): value for day, value in values.items()}
    return output


# --- revised statistics: every vintage ---------------------------------------------------------------------------------
def vintage_dates(html: str) -> list[str]:
    """The vintages ALFRED lists on a series' download page (its <option value="YYYY-MM-DD">)."""
    return sorted(set(re.findall(r'option value="([0-9]{4}-[0-9]{2}-[0-9]{2})"', html)))


def parse_vintages(text: str) -> dict[str, dict[str, float]]:
    """vintage → {observation: value} of one graph file (columns ``<id>_YYYYMMDD``)."""
    rows = list(csv.reader(io.StringIO(text.lstrip(chr(0xFEFF)))))
    if not rows:
        return {}
    names = []
    for column in rows[0][1:]:
        stamp = column.rsplit("_", 1)[-1]
        names.append(f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if re.fullmatch(r"[0-9]{8}", stamp) else "")
    output: dict[str, dict[str, float]] = {name: {} for name in names if name}
    for row in rows[1:]:
        if not row or not DAY.fullmatch(row[0]):
            continue
        for name, cell in zip(names, row[1:]):
            if name and cell not in ("", "."):
                output[name][row[0]] = float(cell)
    return output


def fetch_revised(history: Path, get=_get, pause: float = 0.6, only: tuple[str, ...] | None = None) -> dict[str, int]:
    """Vintages not yet kept are asked for, 12 to a request, and merged; the first one kept is the last vintage before
    ``VINTAGES_FROM`` (what was known when the window opens). A series whose requests fail keeps what it had."""
    folder = Path(history) / "raw" / "alfred"
    folder.mkdir(parents=True, exist_ok=True)
    counts = {}
    for series in (only or tuple(REVISED)):
        path = folder / f"{series}.json.gz"
        kept = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8")) if path.is_file() else {"vintages": {}}
        try:
            listed = vintage_dates(get(f"{ALFRED_PAGE}{series}").decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            continue
        time.sleep(pause)
        before = [day for day in listed if day < VINTAGES_FROM]
        wanted = [day for day in before[-1:] + [day for day in listed if day >= VINTAGES_FROM]
                  if day not in kept["vintages"]]
        added = 0
        for start in range(0, len(wanted), BATCH):
            part = wanted[start:start + BATCH]
            query = urlencode({"id": ",".join([series] * len(part)), "vintage_date": ",".join(part)})
            try:
                found = parse_vintages(get(f"{ALFRED}?{query}").decode("utf-8"))
            except Exception:  # noqa: BLE001
                break
            for vintage in part:
                values = {key: value for key, value in (found.get(vintage) or {}).items() if key >= OBSERVATIONS_FROM}
                if values:
                    kept["vintages"][vintage] = values
                    added += 1
            time.sleep(pause)
        if added:
            files.write_bytes(path, gzip.compress(json.dumps(kept, separators=(",", ":")).encode("utf-8")))
        if kept["vintages"]:
            counts[series] = len(kept["vintages"])
    return counts


def revised(history: Path) -> dict[str, dict[str, dict[str, float]]]:
    """series → vintage → {observation: value}, as kept."""
    folder = Path(history) / "raw" / "alfred"
    output = {}
    for series in REVISED:
        if (folder / f"{series}.json.gz").is_file():
            output[series] = json.loads(gzip.decompress((folder / f"{series}.json.gz").read_bytes()).decode("utf-8"))["vintages"]
    return output


def known(vintages: dict[str, dict[str, float]], day: date | str) -> dict[str, float]:
    """The series exactly as published by ``day``: the newest vintage on or before it."""
    limit = day if isinstance(day, str) else day.isoformat()
    earlier = [vintage for vintage in vintages if vintage <= limit]
    return vintages[max(earlier)] if earlier else {}


def fetch(history: Path, get=_get, pause: float = 0.6) -> dict[str, dict[str, int]]:
    return {"prices": fetch_prices(history, get, pause), "daily": fetch_daily(history, get, pause),
            "revised": fetch_revised(history, get, pause)}


def status(history: Path) -> dict:
    """What is kept: per kind, how many series and each one's last date."""
    price = {symbol: max(rows).isoformat() for symbol, rows in prices(history).items() if rows}
    market = {series: max(rows).isoformat() for series, rows in daily(history).items() if rows}
    stats = {series: {"vintages": len(rows), "last_vintage": max(rows), "last_observation": max(rows[max(rows)])}
             for series, rows in revised(history).items() if rows}
    return {"prices": {"wanted": len(US_STOCKS), "kept": len(price), "last": price},
            "daily": {"wanted": len(DAILY), "kept": len(market), "last_published": market},
            "revised": {"wanted": len(REVISED), "kept": len(stats), "series": stats}}
