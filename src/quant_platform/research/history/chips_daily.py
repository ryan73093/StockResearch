"""Nightly chip and fundamental data from the exchanges' all-market daily reports (roadmap S9-W04, 2026-10-06).

FinMind's free tier answers one stock per request (about 600 an hour), so the whole market's chip data
cannot be refreshed every night from it. TWSE and TPEx publish each dataset for every stock in one daily
report, and their numbers are the ones FinMind serves (checked on 2026-10-02: TSMC's foreign holding
69.17% and 25,932,370,067 shares, margin 30,939 / short 18 lots, foreign 7,044,515 bought / 12,958,489
sold, trust 539,805 / 286,127 — the same). So the history built from the FinMind download is extended
day by day from these reports, four requests per exchange and session:

| dataset (chips parquet) | TWSE | TPEx |
|---|---|---|
| TaiwanStockShareholding | fund/MI_QFIIS | insti/qfii |
| TaiwanStockPER | afterTrading/BWIBBU_d | afterTrading/peQryDate |
| TaiwanStockMarginPurchaseShortSale | marginTrading/MI_MARGN | margin/balance |
| TaiwanStockInstitutionalInvestorsBuySell | fund/T86 | insti/dailyTrade |
| TaiwanDailyShortSaleBalances | marginTrading/TWT93U | margin/sbl |
| TaiwanStockDayTrading | dayTrading/TWTB4U | intraday/stat |

The last two (2026-10-15) feed the short-selling factors (research/chips.py SHORT_FACTORS) after their one-time FinMind
download: 借券賣出當日餘額 and 融券今日餘額 from the 信用額度總量管制餘額表, the day-trading shares and buy amount
from the 當日沖銷交易標的 table — the same numbers FinMind serves (checked 2026-10-15: TSMC 12,031,514 borrowed
shares sold on 10-13, 4,878,000 day-traded shares and NT$12,229,475,000 bought on 10-14; 環球晶 21,259,000).

TaiwanStockSecuritiesLending (2026-10-15, the lending factors): TWSE's 歷史借券成交明細 (SBL/t13sa710) lists every
lending deal of the day, listed and TPEx stocks alike, in one reply; the rows are summed per stock into lots lent and
lots × fee. Checked against FinMind for 10-14: 環球晶 and TSMC deal for deal, except two deals FinMind does not have
(TSMC 600 lots at 4.63%, a negotiated deal reported late); the reply is asked again once after the day closed.

The raw replies are cached under ``raw/official_chips/`` (a session's copy is requested again once after
it closed); ``research/chips.py`` adds their rows for the days the FinMind files do not have.

Monthly revenue (2026-10-06): the exchanges' open data publish the latest month's table for every listed
(TWSE t187ap05_L) and TPEx (mopsfin_t187ap05_O) company, in thousands of NT$, with the date the table was
compiled (出表日期). It is fetched with the chips every night and each distinct table (month and
compilation date) kept under ``raw/official_revenue/``; a company's month is usable from the first table
that lists it — later than the company's own announcement that FinMind dates, so never early.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from datetime import date
from pathlib import Path

from quant_platform.research.history.official import TPEX_BULLETIN, OfficialHistoryClient, number

TWSE = "https://www.twse.com.tw/rwd/zh"
TPEX = TPEX_BULLETIN.rsplit("/", 1)[0]
STOCK = re.compile(r"^(?!91)[1-9]\d{3}$")
DATASETS = ("holding", "per", "margin", "insti", "sbl", "daytrade")
TWSE_ONLY = ("lending",)            # one report for both markets
LENDING_URL = "https://www.twse.com.tw/SBL/t13sa710"


def url(exchange: str, dataset: str, day: date) -> str:
    if exchange == "twse":
        compact = f"{day:%Y%m%d}"
        return {
            "holding": f"{TWSE}/fund/MI_QFIIS?date={compact}&selectType=ALLBUT0999&response=json",
            "per": f"{TWSE}/afterTrading/BWIBBU_d?date={compact}&selectType=ALL&response=json",
            "margin": f"{TWSE}/marginTrading/MI_MARGN?date={compact}&selectType=ALL&response=json",
            "insti": f"{TWSE}/fund/T86?date={compact}&selectType=ALLBUT0999&response=json",
            "sbl": f"{TWSE}/marginTrading/TWT93U?date={compact}&response=json",
            "daytrade": f"{TWSE}/dayTrading/TWTB4U?date={compact}&selectType=All&response=json",
            "lending": f"{LENDING_URL}?startDate={compact}&endDate={compact}&response=json",
        }[dataset]
    slash = f"{day:%Y/%m/%d}"
    return {
        "holding": f"{TPEX}/insti/qfii?date={slash}&response=json",
        "per": f"{TPEX}/afterTrading/peQryDate?date={slash}&response=json",
        "margin": f"{TPEX}/margin/balance?date={slash}&response=json",
        "insti": f"{TPEX}/insti/dailyTrade?type=Daily&sect=EW&date={slash}&response=json",
        "sbl": f"{TPEX}/margin/sbl?date={slash}&response=json",
        "daytrade": f"{TPEX}/intraday/stat?type=Daily&date={slash}&response=json",
    }[dataset]


def key(exchange: str, dataset: str, day: date) -> str:
    return f"official_chips/{exchange}_{dataset}/{day:%Y}/{day:%Y%m%d}"


def _tables(payload: object) -> list[dict]:
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
        return []
    return [table for table in (payload.get("tables") or [payload]) if isinstance(table, dict) and table.get("data")]


def _value(text: object) -> float | None:
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = str(text or "").strip().rstrip("%")
    return number(cleaned) if cleaned not in ("", "-", "--", "N/A") else None


def _column(fields: list[str], *names: str, start: int = 0) -> int:
    """The first field at or after ``start`` that equals one of ``names`` (spaces ignored)."""
    clean = [str(field).replace(" ", "") for field in fields]
    for index in range(start, len(clean)):
        if clean[index] in names:
            return index
    raise ValueError(f"欄位不見了：{names}（{fields}）")


def parse_lending(payload: object, day: date) -> list[dict[str, object]]:
    """The day's lending deals summed per stock: lots lent and lots × fee rate (%)."""
    totals: dict[str, list[float]] = {}
    for table in _tables(payload):
        fields = table.get("fields") or []
        try:
            name_at = _column(fields, "證券代號名稱")
            lots_at = _column(fields, "成交數量(交易單位)", "成交數量")
            fee_at = _column(fields, "成交費率")
        except ValueError:
            continue
        for item in table["data"]:
            code = str(item[name_at]).split()[0] if str(item[name_at]).strip() else ""
            lots, fee = _value(item[lots_at]), _value(item[fee_at])
            if not STOCK.match(code) or not lots or lots <= 0 or fee is None:
                continue
            total = totals.setdefault(code, [0.0, 0.0])
            total[0] += lots
            total[1] += lots * fee
    return [{"date": day, "code": code, "LendingVolume": lots, "FeeVolume": weighted}
            for code, (lots, weighted) in sorted(totals.items())]


def parse(exchange: str, dataset: str, payload: object, day: date) -> list[dict[str, object]]:
    """Rows in the chips parquet's columns: date, code and the dataset's values."""
    if dataset == "lending":
        return parse_lending(payload, day)
    rows: list[dict[str, object]] = []
    for table in _tables(payload):
        fields = table.get("fields") or []
        try:
            if dataset == "holding":
                code_at = _column(fields, "證券代號", "代號")
                shares_at = _column(fields, "發行股數", "發行股數(A)")
                ratio_at = _column(fields, "全體外資及陸資持股比率", "僑外資及陸資持股比率(E=C/A)")
            elif dataset == "per":
                code_at = _column(fields, "證券代號", "股票代號")
                per_at = _column(fields, "本益比")
                pbr_at = _column(fields, "股價淨值比")
            elif dataset == "margin":
                code_at = _column(fields, "代號", "股票代號")
                if exchange == "twse":       # 融資 then 融券, each with its own 今日餘額
                    margin_at = _column(fields, "今日餘額")
                    short_at = _column(fields, "今日餘額", start=margin_at + 1)
                else:
                    margin_at = _column(fields, "資餘額")
                    short_at = _column(fields, "券餘額")
            elif dataset == "sbl":           # 融券 (今日餘額 at TWSE, 當日餘額 at TPEx) then 借券賣出 (當日餘額)
                code_at = _column(fields, "代號", "股票代號")
                short_at = _column(fields, "今日餘額", "當日餘額")
                sbl_at = _column(fields, "當日餘額", start=_column(fields, "當日賣出"))
            elif dataset == "daytrade":      # the per-stock table, after the market totals
                code_at = _column(fields, "證券代號")
                volume_at = _column(fields, "當日沖銷交易成交股數")
                bought_at = _column(fields, "當日沖銷交易買進成交金額")
            else:
                code_at = _column(fields, "證券代號", "代號")
        except ValueError:
            continue
        for item in table["data"]:
            code = str(item[code_at]).strip()
            if not STOCK.match(code):
                continue
            row: dict[str, object] = {"date": day, "code": code}
            if dataset == "holding":
                row.update(ForeignInvestmentSharesRatio=_value(item[ratio_at]), NumberOfSharesIssued=_value(item[shares_at]))
            elif dataset == "per":
                row.update(PER=_value(item[per_at]), PBR=_value(item[pbr_at]))
            elif dataset == "margin":
                row.update(MarginPurchaseTodayBalance=_value(item[margin_at]), ShortSaleTodayBalance=_value(item[short_at]))
            elif dataset == "sbl":
                row.update(SBLShortSalesCurrentDayBalance=_value(item[sbl_at]),
                           MarginShortSalesCurrentDayBalance=_value(item[short_at]))
            elif dataset == "daytrade":
                row.update(BuyAmount=_value(item[bought_at]), Volume=_value(item[volume_at]))
            elif exchange == "twse":
                # T86: foreign excluding their dealers (net at 4), foreign dealers (7), investment trust (10)
                row.update(foreign_net=(_value(item[4]) or 0.0) + (_value(item[7]) or 0.0), trust_net=_value(item[10]) or 0.0)
            else:
                # dailyTrade: foreign excl. dealers 2-4, foreign dealers 5-7, foreign total 8-10, trust 11-13
                row.update(foreign_net=_value(item[10]) or 0.0, trust_net=_value(item[13]) or 0.0)
            rows.append(row)
        if rows:
            break
    return rows


def fetch(base: Path, client: OfficialHistoryClient, sessions: Iterable[date], today: date,
          exchanges: tuple[str, ...] = ("twse", "tpex")) -> dict[str, object]:
    """Request (or read from the cache) every dataset of every session; returns rows per dataset."""
    counts: dict[str, int] = {}
    days = sorted(sessions)
    for day in days:
        for exchange in exchanges:
            for dataset in DATASETS + (TWSE_ONLY if exchange == "twse" else ()):
                payload = client._cached(key(exchange, dataset, day), url(exchange, dataset, day),
                                         final=day < today, period_end=day)
                counts[f"{exchange}_{dataset}"] = counts.get(f"{exchange}_{dataset}", 0) + len(
                    parse(exchange, dataset, payload, day))
    return {"sessions": len(days), "first": days[0].isoformat() if days else None,
            "last": days[-1].isoformat() if days else None, "rows": counts, "requests": client.requests}


REVENUE = {"twse": "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
           "tpex": "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O"}


def _roc_date(text: object) -> date | None:
    digits = str(text or "").strip()
    if len(digits) != 7 or not digits.isdigit():
        return None
    try:
        return date(int(digits[:3]) + 1911, int(digits[3:5]), int(digits[5:7]))
    except ValueError:
        return None


def parse_revenue(payload: object) -> list[dict[str, object]]:
    """Rows in the chips revenue columns: the month (its first day), code, available (the table's
    compilation date, not before the month's 28th), revenue in NT$, year, month."""
    rows = []
    for item in payload if isinstance(payload, list) else []:
        code = str(item.get("公司代號") or "").strip()
        month_text = str(item.get("資料年月") or "").strip()
        compiled = _roc_date(item.get("出表日期"))
        revenue = _value(item.get("營業收入-當月營收"))
        if not STOCK.match(code) or len(month_text) != 5 or not month_text.isdigit() or compiled is None or revenue is None:
            continue
        year, month = int(month_text[:3]) + 1911, int(month_text[3:])
        if not 1 <= month <= 12:
            continue
        rows.append({"date": date(year, month, 1), "code": code, "available": max(compiled, date(year, month, 28)),
                     "revenue": revenue * 1000.0, "year": year, "month": month})
    return rows


def fetch_revenue(base: Path, client: OfficialHistoryClient) -> dict[str, object]:
    """Keep each distinct revenue table (month × compilation date) the exchanges publish."""
    import json

    saved = {}
    for exchange, address in REVENUE.items():
        payload = client._request(address)
        rows = parse_revenue(payload)
        if not rows:
            saved[exchange] = 0
            continue
        month = max(row["date"] for row in rows)
        compiled = max(row["available"] for row in rows)
        path = Path(base) / "raw" / "official_revenue" / exchange / f"{month:%Y%m}-{compiled:%Y%m%d}.json"
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(".json.partial")
            partial.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            partial.replace(path)
        saved[exchange] = len(rows)
    return saved


def cached_revenue_rows(base: Path) -> Iterator[dict[str, object]]:
    """Every kept revenue table's rows, earliest compilation first (so the first sighting wins)."""
    import json

    folder = Path(base) / "raw" / "official_revenue"
    paths = sorted(folder.rglob("*.json"), key=lambda path: path.stem.split("-")[-1]) if folder.is_dir() else []
    for path in paths:
        try:
            yield from parse_revenue(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            continue


def cached_rows(base: Path, dataset: str) -> Iterator[dict[str, object]]:
    """Every cached report's rows for one dataset (both exchanges), oldest first."""
    import json

    raw = Path(base) / "raw" / "official_chips"
    for exchange in ("twse", "tpex"):
        folder = raw / f"{exchange}_{dataset}"
        for path in sorted(folder.rglob("*.json")) if folder.is_dir() else []:
            stem = path.stem
            day = date(int(stem[:4]), int(stem[4:6]), int(stem[6:8]))
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            yield from parse(exchange, dataset, payload, day)
