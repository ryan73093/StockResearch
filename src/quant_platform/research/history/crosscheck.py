"""Compare the official history with Yahoo (S3-W01).

Yahoo's daily closes are split-adjusted while the official files keep the
traded prices, so official closes before each Yahoo split date are scaled by
denominator / numerator before comparing. Dividends are not adjusted on
either side (Yahoo's ``close`` is not dividend-adjusted).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from quant_platform.research.history.catalog import SERIES, HistorySeries
from quant_platform.research.history.dataset import read_series

TAIPEI = ZoneInfo("Asia/Taipei")
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"


def _yahoo_json(url: str) -> object:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 StockResearch/1.0"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def yahoo_history(
    symbol: str, start: date, end: date, fetch_json: Callable[[str], object] = _yahoo_json
) -> tuple[dict[date, float], list[tuple[date, float]]]:
    """Daily closes by Taipei date and split events as (date, numerator / denominator)."""
    query = urlencode({
        "period1": int(datetime(start.year, start.month, start.day, tzinfo=TAIPEI).timestamp()),
        "period2": int(datetime(end.year, end.month, end.day, tzinfo=TAIPEI).timestamp()) + 86400,
        "interval": "1d",
        "events": "split",
    })
    payload = fetch_json(f"{YAHOO}{quote(symbol)}?{query}")
    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result:
        return {}, []
    stamps = result[0].get("timestamp") or []
    closes = (((result[0].get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    history = {
        datetime.fromtimestamp(stamp, TAIPEI).date(): float(close)
        for stamp, close in zip(stamps, closes)
        if close is not None
    }
    splits = []
    for event in ((result[0].get("events") or {}).get("splits") or {}).values():
        numerator, denominator = event.get("numerator"), event.get("denominator")
        if numerator and denominator:
            splits.append((
                datetime.fromtimestamp(int(event["date"]), TAIPEI).date(),
                float(numerator) / float(denominator),
            ))
    return history, sorted(splits)


def yahoo_adjusted(
    symbol: str, start: date, end: date, fetch_json: Callable[[str], object] = _yahoo_json
) -> dict[date, float]:
    """Yahoo's split- and dividend-adjusted closes by Taipei date."""
    query = urlencode({
        "period1": int(datetime(start.year, start.month, start.day, tzinfo=TAIPEI).timestamp()),
        "period2": int(datetime(end.year, end.month, end.day, tzinfo=TAIPEI).timestamp()) + 86400,
        "interval": "1d",
        "events": "div,split",
        "includeAdjustedClose": "true",
    })
    payload = fetch_json(f"{YAHOO}{quote(symbol)}?{query}")
    result = ((payload or {}).get("chart") or {}).get("result") or []
    if not result:
        return {}
    stamps = result[0].get("timestamp") or []
    adjusted = (((result[0].get("indicators") or {}).get("adjclose") or [{}])[0]).get("adjclose") or []
    return {
        datetime.fromtimestamp(stamp, TAIPEI).date(): float(value)
        for stamp, value in zip(stamps, adjusted)
        if value is not None
    }


def compare_total_return(tr: dict[date, float], adjusted: dict[date, float]) -> dict[str, object]:
    """Our total-return index against Yahoo's adjusted close: the ratio should stay flat."""
    common = sorted(set(tr) & set(adjusted))
    if len(common) < 2:
        return {"common_days": len(common)}
    base = tr[common[0]] / adjusted[common[0]]
    deviations = [(tr[day] / adjusted[day]) / base - 1 for day in common]
    years = (common[-1] - common[0]).days / 365.25
    drift = deviations[-1]
    worst = max(range(len(common)), key=lambda index: abs(deviations[index]))
    return {
        "common_days": len(common),
        "first": common[0].isoformat(),
        "last": common[-1].isoformat(),
        "end_drift": round(drift, 6),
        "annualized_drift": round((1 + drift) ** (1 / years) - 1, 6) if years > 0 else None,
        "max_abs_deviation": round(abs(deviations[worst]), 6),
        "max_deviation_date": common[worst].isoformat(),
    }


def split_factor(day: date, splits: list[tuple[date, float]]) -> float:
    """Divide an official price on ``day`` by this to match split-adjusted prices."""
    factor = 1.0
    for split_day, ratio in splits:
        if day < split_day:
            factor *= ratio
    return factor


def compare(
    official: dict[date, float], yahoo: dict[date, float], splits: list[tuple[date, float]]
) -> dict[str, object]:
    common = sorted(set(official) & set(yahoo))
    only_official = sorted(set(official) - set(yahoo))
    only_yahoo = sorted(set(yahoo) - set(official))
    differences = []
    for day in common:
        expected = official[day] / split_factor(day, splits)
        if expected:
            differences.append((abs(yahoo[day] - expected) / expected, day, expected, yahoo[day]))
    differences.sort(reverse=True)
    return {
        "common_days": len(common),
        "only_official": len(only_official),
        "only_official_examples": [day.isoformat() for day in only_official[:10]],
        "only_yahoo": len(only_yahoo),
        "only_yahoo_examples": [day.isoformat() for day in only_yahoo[:10]],
        "over_0_5pct": sum(1 for item in differences if item[0] > 0.005),
        "over_2pct": sum(1 for item in differences if item[0] > 0.02),
        "max_relative_difference": round(differences[0][0], 6) if differences else None,
        "largest": [
            {"date": day.isoformat(), "official_adjusted": round(expected, 4), "yahoo": round(value, 4),
             "relative": round(relative, 6)}
            for relative, day, expected, value in differences[:5]
        ],
        "splits": [{"date": day.isoformat(), "ratio": ratio} for day, ratio in splits],
        "first_yahoo_day": min(yahoo).isoformat() if yahoo else None,
    }


def crosscheck(
    base_dir: str | Path,
    catalog: tuple[HistorySeries, ...] = SERIES,
    keys: list[str] | None = None,
    fetch_json: Callable[[str], object] = _yahoo_json,
    pause: float = 1.0,
) -> dict[str, object]:
    base = Path(base_dir)
    report: dict[str, object] = {
        "generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "series": {},
    }
    for item in catalog:
        if item.yahoo is None or (keys and item.key not in keys):
            continue
        path = base / "daily" / f"{item.key}.parquet"
        if not path.is_file():
            continue
        official = {row["date"]: row["close"] for row in read_series(path) if row["close"]}
        if not official:
            continue
        yahoo, splits = yahoo_history(item.yahoo, min(official), max(official) + timedelta(days=1), fetch_json)
        entry = {"yahoo": item.yahoo, **compare(official, yahoo, splits)}
        tr_path = base / "total_return" / f"{item.key}.parquet"
        if tr_path.is_file():
            time.sleep(pause)
            tr = {row["date"]: row["total_return_index"] for row in read_series(tr_path)}
            adjusted = yahoo_adjusted(item.yahoo, min(tr), max(tr) + timedelta(days=1), fetch_json)
            entry["total_return_vs_yahoo_adjclose"] = compare_total_return(tr, adjusted)
        report["series"][item.key] = entry
        time.sleep(pause)
    (base / "crosscheck.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
