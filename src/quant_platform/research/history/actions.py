"""Dividends, splits and total-return series (S3-W02).

Sources:
- TWSE-listed ETFs: TWT49U 除權除息計算結果表 (ex-date, pre-ex close, reference
  price, cash and stock value), official, from 2003-05-05.
- Splits: the first trading day after a TWSE split carries ``**`` in the
  STOCK_DAY note; the ratio comes from the close before the halt and that
  day's open, rounded to a whole or reciprocal ratio.
- TPEx-listed ETFs: Yahoo dividend events until an official TPEx source is
  added (flagged ``yahoo_dividends``).

Total return: r_t = (close_t × ratio_t + cash_t) / close_{t-1}, i.e. the cash
dividend is reinvested at the ex-date close and a split or stock dividend
multiplies the units held. Payment dates are not modelled here.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from quant_platform.research.history.catalog import SERIES, HistorySeries
from quant_platform.research.history.dataset import read_series, sha256
from quant_platform.research.history.official import (
    OfficialHistoryClient,
    parse_twse_ex_rights,
)

TAIPEI = ZoneInfo("Asia/Taipei")
EX_RIGHTS_START_YEAR = 2003
TR_SCHEMA = pa.schema([
    ("date", pa.date32()),
    ("close", pa.float64()),
    ("cash_dividend", pa.float64()),
    ("unit_ratio", pa.float64()),
    ("total_return_index", pa.float64()),
])


@dataclass(frozen=True, slots=True)
class CorporateAction:
    day: date
    key: str
    kind: str          # cash_dividend | stock_dividend | split
    cash: float = 0.0  # per unit held before the event
    ratio: float = 1.0  # units after / units before
    pre_close: float | None = None
    reference: float | None = None
    source: str = ""


def nice_ratio(raw: float) -> float:
    """Round a price-implied split ratio to n or 1/n."""
    if raw >= 1:
        return float(round(raw))
    return 1.0 / round(1.0 / raw)


def splits_from_markers(key: str, rows: list[dict[str, object]]) -> list[CorporateAction]:
    actions = []
    for previous, row in zip(rows, rows[1:]):
        if "*" not in str(row.get("note") or ""):
            continue
        if not previous["close"] or not row["open"]:
            continue
        raw = float(previous["close"]) / float(row["open"])
        ratio = nice_ratio(raw)
        if abs(raw / ratio - 1) > 0.05 or ratio == 1.0:
            continue  # a marker without a clear ratio is left for manual review
        actions.append(CorporateAction(
            row["date"], key, "split", ratio=ratio, pre_close=float(previous["close"]),
            source="twse_stock_day_marker",
        ))
    return actions


def actions_from_ex_rights(events) -> list[CorporateAction]:
    output = []
    for event in events:
        if event.cash > 0:
            output.append(CorporateAction(
                event.day, event.code, "cash_dividend", cash=event.cash,
                pre_close=event.pre_close, reference=event.reference, source=event.source,
            ))
        if event.rights_value > 0 and event.pre_close and event.reference:
            ratio = (event.pre_close - event.cash) / event.reference
            output.append(CorporateAction(
                event.day, event.code, "stock_dividend", ratio=round(ratio, 6),
                pre_close=event.pre_close, reference=event.reference, source=event.source,
            ))
    return output


def yahoo_dividends(
    symbol: str, start: date, end: date, fetch_json: Callable[[str], object]
) -> list[tuple[date, float]]:
    from urllib.parse import quote, urlencode

    query = urlencode({
        "period1": int(datetime(start.year, start.month, start.day, tzinfo=TAIPEI).timestamp()),
        "period2": int(datetime(end.year, end.month, end.day, tzinfo=TAIPEI).timestamp()) + 86400,
        "interval": "1d",
        "events": "div",
    })
    payload = fetch_json(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol)}?{query}")
    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result:
        return []
    dividends = ((result[0].get("events") or {}).get("dividends") or {}).values()
    return sorted(
        (datetime.fromtimestamp(int(item["date"]), TAIPEI).date(), float(item["amount"]))
        for item in dividends if item.get("amount")
    )


def total_return(
    rows: list[dict[str, object]], actions: list[CorporateAction], base: float = 100.0
) -> list[dict[str, object]]:
    by_day: dict[date, list[CorporateAction]] = {}
    for action in actions:
        by_day.setdefault(action.day, []).append(action)
    output = []
    previous_close: float | None = None
    level = base
    for row in rows:
        close = float(row["close"])
        cash = sum(item.cash for item in by_day.get(row["date"], ()) if item.kind == "cash_dividend")
        ratio = 1.0
        for item in by_day.get(row["date"], ()):
            if item.kind in {"split", "stock_dividend"}:
                ratio *= item.ratio
        if previous_close:
            level *= (close * ratio + cash) / previous_close
        output.append({
            "date": row["date"], "close": close, "cash_dividend": cash,
            "unit_ratio": ratio, "total_return_index": level,
        })
        previous_close = close
    return output


def build_actions(
    base_dir: str | Path,
    client: OfficialHistoryClient,
    catalog: tuple[HistorySeries, ...] = SERIES,
    yahoo_fetch: Callable[[str], object] | None = None,
    today: Callable[[], date] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Collect corporate actions and write total-return files for every ETF series."""
    base = Path(base_dir)
    say = progress or (lambda _message: None)
    now = (today or (lambda: datetime.now(TAIPEI).date()))()
    etfs = [item for item in catalog if item.kind in {"twse_etf", "tpex_etf"}]
    twse_codes = {item.key for item in etfs if item.kind == "twse_etf"}
    ex_rights = []
    if twse_codes:
        first_year = max(EX_RIGHTS_START_YEAR, min(item.first_month.year for item in etfs))
        for year in range(first_year, now.year + 1):
            say(f"除權息 {year}")
            ex_rights.extend(parse_twse_ex_rights(client.twse_ex_rights_year(year), twse_codes))
    official_actions = actions_from_ex_rights(ex_rights)
    report: dict[str, object] = {
        "generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "series": {},
    }
    for item in etfs:
        path = base / "daily" / f"{item.key}.parquet"
        if not path.is_file():
            continue
        rows = [row for row in read_series(path) if row["close"]]
        if not rows:
            continue
        actions = [action for action in official_actions if action.key == item.key]
        if item.kind == "twse_etf":
            actions += splits_from_markers(item.key, rows)
        elif item.yahoo and yahoo_fetch is not None:
            say(f"{item.key} 股利（Yahoo）")
            actions += [
                CorporateAction(day, item.key, "cash_dividend", cash=amount, source="yahoo_dividends")
                for day, amount in yahoo_dividends(
                    item.yahoo, rows[0]["date"], rows[-1]["date"] + timedelta(days=1), yahoo_fetch
                )
            ]
        actions.sort(key=lambda action: (action.day, action.kind))
        series = total_return(rows, actions)
        tr_path = base / "total_return" / f"{item.key}.parquet"
        tr_path.parent.mkdir(parents=True, exist_ok=True)
        partial = tr_path.with_suffix(".parquet.partial")
        pq.write_table(
            pa.Table.from_pylist(series, schema=TR_SCHEMA), partial, compression="zstd"
        )
        partial.replace(tr_path)
        years = (rows[-1]["date"] - rows[0]["date"]).days / 365.25
        growth = series[-1]["total_return_index"] / series[0]["total_return_index"]
        price_growth = float(rows[-1]["close"]) / float(rows[0]["close"])
        report["series"][item.key] = {
            "first": rows[0]["date"].isoformat(),
            "last": rows[-1]["date"].isoformat(),
            "cash_dividends": sum(1 for action in actions if action.kind == "cash_dividend"),
            "splits": [
                {"date": action.day.isoformat(), "ratio": action.ratio}
                for action in actions if action.kind == "split"
            ],
            "stock_dividends": sum(1 for action in actions if action.kind == "stock_dividend"),
            "sources": sorted({action.source for action in actions}),
            "total_return_cagr": round(growth ** (1 / years) - 1, 6) if years > 0 else None,
            "price_only_cagr_unadjusted": round(price_growth ** (1 / years) - 1, 6) if years > 0 else None,
            "file": str(tr_path.relative_to(base)).replace("\\", "/"),
            "sha256": sha256(tr_path),
            "actions": [
                {**asdict(action), "day": action.day.isoformat()} for action in actions
            ],
        }
    (base / "actions.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
