"""Survivorship-free daily quotes of every listed stock (REQUIREMENTS §7 股票池時點一致).

The TWSE 每日收盤行情 (MI_INDEX type=ALLBUT0999) and the TPEx 上櫃每日行情 list every
security that traded that day, so a stock that later delisted is in the files of the days it
existed and absent afterwards — the historical membership falls out of the data itself. One
cached JSON per session (`raw/twse_stock_all/<year>/<day>.json`, `raw/tpex_stock_all/...`),
then one Parquet per year and exchange under `stocks/` with (date, code, name, open, high,
low, close, volume, turnover, trades). Only common stocks (four-digit codes, 1101–9999) are
kept; ETFs, warrants, TDRs and preferred shares are not.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from quant_platform.research.history.official import TPEX_BULLETIN, TWSE, OfficialHistoryClient, integer, number

TAIPEI = ZoneInfo("Asia/Taipei")
TWSE_ALL_START = date(2004, 2, 11)      # the first session MI_INDEX answers for
STOCK_CODE = re.compile(r"^(?!91)[1-9]\d{3}$")   # common stocks; 00xx ETFs, 91xx TDRs, 2881A preferred are out
QUIET = (time(13, 30), time(14, 40))       # no requests while the after-hours auction runs

SCHEMA = pa.schema([
    ("date", pa.date32()), ("code", pa.string()), ("name", pa.string()),
    ("open", pa.float64()), ("high", pa.float64()), ("low", pa.float64()), ("close", pa.float64()),
    ("volume", pa.int64()), ("turnover", pa.int64()), ("trades", pa.int64()),
])


def twse_all_day(client: OfficialHistoryClient, day: date) -> object:
    from urllib.parse import urlencode

    query = urlencode({"date": f"{day:%Y%m%d}", "type": "ALLBUT0999", "response": "json"})
    return client._cached(
        f"twse_stock_all/{day:%Y}/{day:%Y%m%d}", f"{TWSE}/afterTrading/MI_INDEX?{query}",
        final=day < client._today(), period_end=day,
    )


def tpex_all_day(client: OfficialHistoryClient, day: date) -> object:
    from urllib.parse import urlencode

    query = urlencode({"date": f"{day:%Y/%m/%d}", "type": "EW", "response": "json"})  # EW: all but warrants
    return client._cached(
        f"tpex_stock_all/{day:%Y}/{day:%Y%m%d}", f"{TPEX_BULLETIN.rsplit('/', 1)[0]}/afterTrading/otc?{query}",
        final=day < client._today(), period_end=day,
    )


def parse_twse_all_day(payload: object, day: date) -> list[dict[str, object]]:
    """The table whose fields start with 證券代號: 代號, 名稱, 成交股數, 成交筆數, 成交金額, 開, 高, 低, 收, ..."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return []
    rows = []
    for table in payload.get("tables") or []:
        fields = (table or {}).get("fields") or []
        if fields[:2] != ["證券代號", "證券名稱"]:
            continue
        for item in table.get("data") or []:
            code = str(item[0]).strip()
            if not STOCK_CODE.match(code) or len(item) < 9:
                continue
            close = number(item[8])
            if close is None or close <= 0:
                continue  # no trade that day: the stock is listed but has no price
            rows.append({
                "date": day, "code": code, "name": str(item[1]).strip(),
                "open": number(item[5]), "high": number(item[6]), "low": number(item[7]), "close": close,
                "volume": integer(item[2]), "turnover": integer(item[4]), "trades": integer(item[3]),
            })
    return rows


def parse_tpex_all_day(payload: object, day: date) -> list[dict[str, object]]:
    """TPEx 上櫃每日行情: 代號, 名稱, 收盤, 漲跌, 開盤, 最高, 最低, 成交股數, 成交金額, 成交筆數, ..."""
    if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
        return []
    rows = []
    for table in payload.get("tables") or [payload]:
        fields = (table or {}).get("fields") or []
        if not fields or "代號" not in str(fields[0]):
            continue
        for item in table.get("data") or []:
            code = str(item[0]).strip()
            if not STOCK_CODE.match(code) or len(item) < 10:
                continue
            close = number(item[2])
            if close is None or close <= 0:
                continue
            rows.append({
                "date": day, "code": code, "name": str(item[1]).strip(),
                "open": number(item[4]), "high": number(item[5]), "low": number(item[6]), "close": close,
                "volume": integer(item[7]), "turnover": integer(item[8]), "trades": integer(item[9]),
            })
    return rows


def write_year(rows: list[dict[str, object]], path: Path) -> None:
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".parquet.partial")
    pq.write_table(table, partial, compression="zstd")
    partial.replace(path)


def read_year(path: Path) -> list[dict[str, object]]:
    return pq.read_table(path).to_pylist() if path.is_file() else []


def quiet_now(now: datetime | None = None) -> bool:
    moment = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    return moment.weekday() < 5 and QUIET[0] <= moment.time() <= QUIET[1]


def fetch_exchange(
    exchange: str, client: OfficialHistoryClient, sessions: Iterable[date], base: Path,
    progress: Callable[[str], None] | None = None, sleep: Callable[[float], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, object]:
    """Fetch and store every session's quotes for one exchange, a year at a time. Resumable: cached
    days are not requested again; a year already on disk with every session is skipped."""
    import time as clock

    say = progress or (lambda _message: None)
    pause = sleep or clock.sleep
    getter, parser = ((twse_all_day, parse_twse_all_day) if exchange == "twse" else (tpex_all_day, parse_tpex_all_day))
    by_year: dict[int, list[date]] = {}
    for day in sessions:
        by_year.setdefault(day.year, []).append(day)
    written, requests_before = [], client.requests
    for year, days in sorted(by_year.items()):
        path = base / "stocks" / exchange / f"{year}.parquet"
        existing = read_year(path)
        have = {row["date"] for row in existing}
        if all(day in have for day in days):
            say(f"{exchange} {year} 已完整（{len(existing):,} 列）")
            continue
        wanted = set(days)
        rows = [row for row in existing if row["date"] in wanted]
        for index, day in enumerate(days, 1):
            if day in have:
                continue
            if should_stop and should_stop():
                write_year(rows, path)
                return {"stopped": True, "years": written, "requests": client.requests - requests_before}
            while quiet_now():
                pause(60)
            payload = getter(client, day)
            day_rows = parser(payload, day)
            rows.extend(day_rows)
            have.add(day)  # a holiday or an empty answer is remembered through the cache, not the parquet
            if index % 50 == 0:
                say(f"  {exchange} {day} {index}/{len(days)}（{len(rows):,} 列）")
                write_year(rows, path)
        write_year(rows, path)
        written.append(year)
        say(f"{exchange} {year} 完成：{len(rows):,} 列、{len({row['code'] for row in rows})} 檔")
    return {"stopped": False, "years": written, "requests": client.requests - requests_before}


def append_current_year(base: Path, client: OfficialHistoryClient, today: date,
                        exchange: str = "twse") -> dict[str, object]:
    """After the close (research roadmap R2): add the sessions of this year that the year file
    lacks, today included once the exchange has published it. The sessions come from the index
    series the nightly refresh has just rebuilt; earlier years and cached days are not requested."""
    from quant_platform.research.history.dataset import read_series

    sessions = [row["date"] for row in read_series(base / "daily" / "TAIEX.parquet")
                if row["date"].year == today.year and row["date"] <= today]
    result = fetch_exchange(exchange, client, sessions, base)
    rows = read_year(base / "stocks" / exchange / f"{today.year}.parquet")
    result["last_day"] = max((row["date"] for row in rows), default=None)
    result["today_rows"] = sum(1 for row in rows if row["date"] == today)
    return result


def _positive(value: object) -> float | None:
    return float(value) if value and float(value) > 0 else None


def build_tpex_from_finmind(base: Path) -> dict[str, object]:
    """R6 (2026-10-05): TPEx common stocks' daily quotes from FinMind's TaiwanStockPrice downloads
    (today's TPEx stocks and the delisted ones TWSE never quoted), one Parquet per year under
    stocks/tpex/ like the TWSE files. Days without a trade are left out; names from TaiwanStockInfo.
    FinMind answers a code's whole history whatever the market, so for a stock that moved to TWSE the
    days TWSE already has are left out (the TPEx file keeps its years before the move)."""
    from quant_platform.research.history.finmind import read_rows, tpex_codes

    on_twse: dict[str, tuple[date, date]] = {}
    for path in sorted((base / "stocks" / "twse").glob("*.parquet")):
        frame = pq.read_table(path, columns=["date", "code"]).to_pandas()
        for code, (first, last) in frame.groupby("code")["date"].agg(["min", "max"]).iterrows():
            known = on_twse.get(code)
            on_twse[code] = (min(known[0], first), max(known[1], last)) if known else (first, last)

    info_path = base / "raw" / "finmind" / "TaiwanStockInfo.json"
    names = {}
    if info_path.is_file():
        for row in json.loads(info_path.read_text(encoding="utf-8")).get("data") or []:
            names.setdefault(str(row.get("stock_id")), str(row.get("stock_name") or ""))
    by_year: dict[int, list[dict[str, object]]] = {}
    codes = 0
    for code in tpex_codes(base):
        rows = read_rows(base, "TaiwanStockPrice", code)
        if rows:
            codes += 1
        listed = on_twse.get(code)
        for row in rows:
            close, volume = row.get("close"), row.get("Trading_Volume")
            if not close or close <= 0 or not volume:
                continue
            day = date.fromisoformat(row["date"])
            if listed and listed[0] <= day <= listed[1]:
                continue
            by_year.setdefault(day.year, []).append({
                "date": day, "code": code, "name": names.get(code, ""), "open": _positive(row.get("open")),
                "high": _positive(row.get("max")), "low": _positive(row.get("min")), "close": float(close), "volume": int(volume),
                "turnover": int(row.get("Trading_money") or 0), "trades": int(row.get("Trading_turnover") or 0),
            })
    for year, rows in by_year.items():
        rows.sort(key=lambda item: (item["date"], item["code"]))
        write_year(rows, base / "stocks" / "tpex" / f"{year}.parquet")
    return {"codes": codes, "years": sorted(by_year), "rows": sum(len(rows) for rows in by_year.values())}


def universe_summary(base: Path) -> dict[str, object]:
    """Per exchange: years on disk, rows, distinct codes, and codes that stopped trading before the
    last session (the delisted and suspended — the point of the whole exercise)."""
    summary: dict[str, object] = {}
    for exchange in ("twse", "tpex"):
        folder = base / "stocks" / exchange
        paths = sorted(folder.glob("*.parquet")) if folder.is_dir() else []
        last_seen: dict[str, date] = {}
        rows = 0
        last_day = None
        for path in paths:
            for row in read_year(path):
                rows += 1
                last_seen[row["code"]] = max(last_seen.get(row["code"], row["date"]), row["date"])
                last_day = max(last_day or row["date"], row["date"])
        gone = sorted(code for code, seen in last_seen.items() if last_day and seen < last_day)
        summary[exchange] = {
            "years": [path.stem for path in paths], "rows": rows, "codes": len(last_seen),
            "last_day": last_day.isoformat() if last_day else None, "no_longer_trading": len(gone),
        }
    return summary


def save_summary(base: Path) -> Path:
    summary = universe_summary(base)
    summary["generated_at"] = datetime.now(TAIPEI).isoformat(timespec="seconds")
    path = base / "stocks" / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
