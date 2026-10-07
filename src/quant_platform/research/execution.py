"""R4 (2026-10-07): can the daily rules' stock orders fill in the 14:30 after-hours odd-lot auction?

The engine fills every order at the close plus or minus 0.2% (``costs.DEFAULT_SLIPPAGE_BPS``) and never
misses. The TWSE after-hours odd-lot report (TWT53U; research/history/odd_lot.py caches one session in ten
since 2004) lists each security's auction: shares traded, the one price every order filled at, and the
best bid and ask left unfilled. For every stock order a rule made on a cached session this compares the
order with that auction:

- no trade: the stock did not trade in the auction at all;
- filled: the auction price sat strictly inside today's order limit (close plus or minus 1%, kept inside
  the price limit; ``costs.order_limit``), so an order at that limit had price priority and filled;
- at the limit: the auction cleared exactly at the limit, where orders may fill only in part (a buy on a
  limit-up close is the usual case);
- not filled: the auction cleared beyond the limit;
- cost: how far the auction price sat from the close against the order, in basis points;
- size: the order's odd shares against the auction's volume (whole lots would go to the 14:00 fixed-price
  session at the close instead).

TWT53U covers TWSE securities only; orders in TPEx stocks are counted and left out.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_platform.research.costs import DEFAULT_SLIPPAGE_BPS, PRICE_LIMIT, _to_tick, order_limit, stock_tick
from quant_platform.research.history.odd_lot import parse_odd_lot

TAIPEI = ZoneInfo("Asia/Taipei")
RAW = Path("raw") / "twse_odd_lot"
LOT = 1000
BENCHMARK_CODE = "0050"


def load_auctions(history: str | Path, since: date | None = None) -> dict[date, dict[str, dict]]:
    """Every cached TWT53U session (one in ten), all securities."""
    output: dict[date, dict[str, dict]] = {}
    for path in sorted((Path(history) / RAW).glob("*/*.json")):
        try:
            day = datetime.strptime(path.stem, "%Y%m%d").date()
        except ValueError:
            continue
        if since and day < since:
            continue
        try:
            rows = parse_odd_lot(json.loads(path.read_text(encoding="utf-8")), None)
        except (OSError, ValueError):
            continue
        if rows:
            output[day] = rows
    return output


def judge(side: str, shares: float, close: float, previous_close: float | None, auction: dict | None) -> dict:
    """One stock order against its session's auction (see the module notes)."""
    limit = order_limit(close, side, previous_close, tick_size=stock_tick)
    locked = False
    if previous_close:
        if side == "BUY":
            locked = close >= _to_tick(previous_close * (1 + PRICE_LIMIT), False, stock_tick) - 1e-9
        else:
            locked = close <= _to_tick(previous_close * (1 - PRICE_LIMIT), True, stock_tick) + 1e-9
    price = auction.get("price") if auction else None
    volume = int(auction.get("shares") or 0) if auction else 0
    odd = int(round(shares)) % LOT
    row = {"side": side, "close": close, "limit": limit, "shares": int(round(shares)), "odd": odd,
           "locked": locked, "price": price, "auction_shares": volume}
    if not price or volume <= 0:
        return {**row, "outcome": "no_trade", "cost_bps": None, "size_share": None}
    cost = (price / close - 1) * 10_000 if side == "BUY" else (1 - price / close) * 10_000
    inside = price < limit - 1e-9 if side == "BUY" else price > limit + 1e-9
    at_limit = abs(price - limit) < 1e-9
    outcome = "filled" if inside else ("at_limit" if at_limit else "not_filled")
    return {**row, "outcome": outcome, "cost_bps": round(cost, 2),
            "size_share": round(odd / volume, 4) if odd else 0.0}


def _quantile(values: list[float], share: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, round(share * (len(ordered) - 1))))], 2)


def summarise(rows: list[dict]) -> dict[str, object]:
    count = len(rows)
    if not count:
        return {"orders": 0}
    traded = [row for row in rows if row["outcome"] != "no_trade"]
    costs = [row["cost_bps"] for row in traded]
    sizes = [row["size_share"] for row in traded if row["odd"]]
    locked = [row for row in rows if row["locked"]]

    def share(items, outcome):
        return round(sum(1 for row in items if row["outcome"] == outcome) / len(items), 4) if items else None

    return {
        "orders": count,
        "no_trade": share(rows, "no_trade"), "filled": share(rows, "filled"),
        "at_limit": share(rows, "at_limit"), "not_filled": share(rows, "not_filled"),
        "cost_bps": {"median": _quantile(costs, 0.5), "mean": round(statistics.fmean(costs), 2) if costs else None,
                     "p75": _quantile(costs, 0.75), "p90": _quantile(costs, 0.9)},
        "engine_bps": DEFAULT_SLIPPAGE_BPS,
        "with_whole_lots": round(sum(1 for row in rows if row["shares"] >= LOT) / count, 4),
        "size_share": {"median": _quantile(sizes, 0.5), "p90": _quantile(sizes, 0.9),
                       "over_half": round(sum(1 for value in sizes if value > 0.5) / len(sizes), 4) if sizes else None},
        "locked": {"orders": len(locked), "no_trade": share(locked, "no_trade"), "filled": share(locked, "filled"),
                   "at_limit": share(locked, "at_limit")},
    }


def check_orders(ledger: Iterable[dict], closes: dict[str, dict[date, float]], auctions: dict[date, dict[str, dict]],
                 regime_start: date) -> dict[str, object]:
    """Judge every stock order on a cached session; summaries by side and period."""
    rows, tpex, amount = [], 0, 0.0
    for order in ledger:
        if order.get("side") not in ("BUY", "SELL"):
            continue
        symbol = str(order["symbol"])
        code, _, market = symbol.partition(".")
        if code == BENCHMARK_CODE:
            continue
        amount += float(order["shares"]) * float(order["price"])
        if market != "TW":
            tpex += 1
            continue
        day = order["day"]
        if day not in auctions:
            continue
        series = closes.get(symbol, {})
        close = series.get(day)
        if not close:
            continue
        earlier = [when for when in series if when < day]
        previous = series[max(earlier)] if earlier else None
        row = judge(str(order["side"]), float(order["shares"]), close, previous, auctions[day].get(code))
        rows.append({**row, "day": day.isoformat(), "code": code})
    parts = {
        "all": rows,
        "buy": [row for row in rows if row["side"] == "BUY"],
        "sell": [row for row in rows if row["side"] == "SELL"],
        "before_2020_10": [row for row in rows if row["day"] < regime_start.isoformat()],
        "since_2020_10": [row for row in rows if row["day"] >= regime_start.isoformat()],
    }
    report: dict[str, object] = {name: summarise(items) for name, items in parts.items()}
    mean = (report["all"].get("cost_bps") or {}).get("mean")
    report["traded_amount"] = round(amount)
    report["tpex_orders"] = tpex
    # the cost the engine did not charge: (measured mean minus 20 bps) on everything the rule traded
    report["extra_cost"] = round(amount * (mean - DEFAULT_SLIPPAGE_BPS) / 10_000) if mean is not None else None
    report["worst"] = sorted((row for row in rows if row["cost_bps"] is not None),
                             key=lambda row: -row["cost_bps"])[:10]
    return report


def replay(data, fp, rule, costs) -> tuple[list[dict], object]:
    """The rule's ledger over the selection period (as ``daily.evaluate`` runs it)."""
    from quant_platform.research.daily import (
        RECENT_END,
        RECENT_START,
        account_parking,
        daily_rankings,
        daily_weights,
        simulate_daily,
    )

    ranks = daily_rankings(fp, rule, RECENT_START, RECENT_END)
    weights, parked = daily_weights(fp, rule, ranks), account_parking(data, fp, rule, costs)
    first = next((day for day in sorted(ranks) if ranks[day]), RECENT_START)
    ledger: list[dict] = []
    result = simulate_daily(data, rule, costs, first, RECENT_END, ranks, ledger=ledger, weights=weights, parked=parked)
    return ledger, result


def run(history: str | Path, research_dir: str | Path, costs, job=None) -> dict[str, object]:
    """Every rule the forward observation tracks automatically (T0 候選 and T1), checked against the auctions."""
    from quant_platform.research.daily import RECENT_START, REGIME_START, load
    from quant_platform.research.stock_forward import qualifying_daily

    rules = qualifying_daily(research_dir)
    auctions = load_auctions(history, RECENT_START)
    output: dict[str, object] = {
        "generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "sessions": len(auctions),
        "first": min(auctions).isoformat() if auctions else None, "engine_bps": DEFAULT_SLIPPAGE_BPS, "rules": []}
    if job:
        job.update(total=len(rules), force=True)
    loaded: dict[str, tuple] = {}
    for number, (rule, reason) in enumerate(rules):
        if job:
            job.update(done=number, current=rule.name, force=True)
        if rule.universe not in loaded:
            loaded[rule.universe] = load(Path(history), rule.universe)
        data, fp = loaded[rule.universe]
        ledger, result = replay(data, fp, rule, costs)
        report = check_orders(ledger, data.closes, auctions, REGIME_START)
        final = result.values[-1] if result.values else None
        report.update({"name": rule.name, "rule_hash": rule.rule_hash, "reason": reason,
                       "final_value": round(final) if final else None,
                       "extra_cost_share": round(report["extra_cost"] / final, 4)
                       if final and report.get("extra_cost") is not None else None})
        output["rules"].append(report)
    return output
