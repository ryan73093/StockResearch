"""How far does the 14:30 after-hours odd-lot price sit from the close? (S3-W03)

Samples one session in every ``every`` sessions of the TWSE after-hours
odd-lot report (TWT53U) and compares each catalog ETF's odd-lot price with
the regular close of the same day. The distribution sets the slippage the
engine charges; the fill rates of buy limits at close + 0 to 150 bps (the auction
fills every order at one price, so a limit only decides whether an order fills)
set today's order limit (research/costs.py ORDER_LIMIT). ``recent`` repeats the
summary from 2020-10-26, when intraday odd-lot trading began and odd-lot volume
grew.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_platform.research.costs import order_limit
from quant_platform.research.history.catalog import SERIES
from quant_platform.research.history.dataset import read_series
from quant_platform.research.history.official import OfficialHistoryClient, integer, number

TAIPEI = ZoneInfo("Asia/Taipei")
RECENT = date(2020, 10, 26)
LIMIT_PREMIUMS_BPS = (0, 10, 20, 30, 50, 100, 150)


def parse_odd_lot(payload: object, codes: set[str] | None) -> dict[str, dict[str, float | int | None]]:
    """TWT53U: 代號, 名稱, 成交股數, 筆數, 金額, 成交價, 買價, 買量, 賣價, 賣量 (the best bid and ask
    left after the auction). ``codes=None`` keeps every security."""
    if not isinstance(payload, dict) or payload.get("stat") != "OK":
        return {}
    output = {}
    for item in payload.get("data") or []:
        if not isinstance(item, list) or len(item) < 10:
            continue
        code = str(item[0]).strip()
        if codes is None or code in codes:
            output[code] = {
                "shares": integer(item[2]), "trades": integer(item[3]), "price": number(item[5]),
                "bid": number(item[6]), "bid_shares": integer(item[7]), "ask": number(item[8]),
                "ask_shares": integer(item[9]),
            }
    return output


def _percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))
    return ordered[index]


def fill_rates(fills: list[tuple[float, float]], sampled: int) -> dict[str, float]:
    """Share of sampled sessions on which a buy limit at close + x bps would have filled: the
    auction price (``fills`` holds close, price) at or below the limit. No trade counts as unfilled."""
    if not sampled:
        return {}
    return {
        str(bps): round(sum(1 for close, price in fills
                            if order_limit(close, "BUY", premium=bps / 10_000) >= price - 1e-9) / sampled, 4)
        for bps in LIMIT_PREMIUMS_BPS
    }


def summarize(premiums: list[float], sampled: int, fills: list[tuple[float, float]] | None = None) -> dict[str, object]:
    if not premiums:
        return {"sampled_sessions": sampled, "traded_sessions": 0}
    rates = {"fill_rates": fill_rates(fills, sampled)} if fills is not None else {}
    return {
        "sampled_sessions": sampled,
        "traded_sessions": len(premiums),
        "median_bps": round(statistics.median(premiums) * 10_000, 2),
        "mean_bps": round(statistics.fmean(premiums) * 10_000, 2),
        "p10_bps": round(_percentile(premiums, 0.10) * 10_000, 2),
        "p25_bps": round(_percentile(premiums, 0.25) * 10_000, 2),
        "p75_bps": round(_percentile(premiums, 0.75) * 10_000, 2),
        "p90_bps": round(_percentile(premiums, 0.90) * 10_000, 2),
        "at_or_below_close": round(sum(1 for value in premiums if value <= 1e-12) / len(premiums), 4),
        "within_10bps": round(sum(1 for value in premiums if abs(value) <= 0.001) / len(premiums), 4),
        "p95_bps": round(_percentile(premiums, 0.95) * 10_000, 2),
        "max_bps": round(max(premiums) * 10_000, 2),
        **rates,
    }


def build_odd_lot_report(
    base_dir: str | Path,
    client: OfficialHistoryClient,
    every: int = 5,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    base = Path(base_dir)
    say = progress or (lambda _message: None)
    etfs = [item for item in SERIES if item.kind == "twse_etf"]
    closes = {
        item.key: {row["date"]: row["close"] for row in read_series(base / "daily" / f"{item.key}.parquet") if row["close"]}
        for item in etfs if (base / "daily" / f"{item.key}.parquet").is_file()
    }
    sessions = sorted(row["date"] for row in read_series(base / "daily" / "TAIEX.parquet"))
    first = min((min(values) for values in closes.values() if values), default=None)
    sample = [day for index, day in enumerate(day for day in sessions if first and day >= first) if index % every == 0]
    say(f"盤後零股抽樣 {len(sample)} 個交易日")
    observed: dict[str, list[tuple[date, float, float | None]]] = {key: [] for key in closes}
    for index, day in enumerate(sample, 1):
        rows = parse_odd_lot(client.twse_odd_lot_day(day), set(closes))
        for key, values in closes.items():
            close = values.get(day)
            if close is None:
                continue
            row = rows.get(key)
            observed[key].append((day, close, row["price"] if row and row["price"] and row["shares"] else None))
        if index % 100 == 0:
            say(f"  {day} ({index}/{len(sample)})")
    report = {
        "generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "every_n_sessions": every,
        "series": {key: _summary(observed[key]) for key in closes},
    }
    (base / "odd_lot.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _summary(observed: list[tuple[date, float, float | None]]) -> dict[str, object]:
    def part(rows: list[tuple[date, float, float | None]]) -> dict[str, object]:
        fills = [(close, price) for _day, close, price in rows if price is not None]
        return summarize([price / close - 1 for close, price in fills], len(rows), fills)

    return {**part(observed), "recent": {"since": RECENT.isoformat(), **part([row for row in observed if row[0] >= RECENT])}}
