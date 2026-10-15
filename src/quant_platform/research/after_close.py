"""Today's orders before the after-hours sessions close (2026-10-14; 使用者：要提供在 14:30 前就能算出建議的策略——
隔天成交是另一種價格、另一種策略，不是平台該留給你的限制).

The backtests decide on a session's close and fill in that session's after-hours sessions, which take orders
until 14:30: the fixed-price session (14:00–14:30, round lots only, at the close) and the odd-lot auction
(13:40–14:30, one price at 14:30). TWSE's all-market table (MI_INDEX ALLBUT0999) is out 19–31 minutes after the
13:30 close (13:49–14:01 measured, ``instance/close_availability.jsonl``) and holds every listed stock, 0050 and
TAIEX — everything a listed-stock rule reads that day (chips are the previous session's, statements and
revenue are older). ``prepare`` adds that table to the research data as one more session (the stock files
and index series are written after 15:14), runs every rule of the common-start board (COMMON_START, listed
stocks) to today with the engine the backtests use, and writes today's orders to
``forward/stocks/orders/<date>.json``: per rule, each order's round lots for the fixed-price session and its
odd lot for the odd-lot auction with a limit price, and the holdings after. Rules on TPEx stocks are left
out: TPEx's table is not out before 14:30.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
FOLDER = Path("forward") / "stocks" / "orders"
MIN_ROWS = 500                    # a table with fewer listed stocks is not today's full table yet


def today_table(payload: object, day: date) -> tuple[list[dict], float | None, float | None]:
    """(the listed common stocks' rows, 0050's close, TAIEX's close) from TWSE's MI_INDEX payload."""
    from quant_platform.research.history.official import number
    from quant_platform.research.history.stocks import parse_twse_all_day

    rows = parse_twse_all_day(payload, day)
    benchmark = taiex = None
    if isinstance(payload, dict):
        for table in payload.get("tables") or []:
            fields = (table or {}).get("fields") or []
            for item in table.get("data") or []:
                if fields[:2] == ["證券代號", "證券名稱"] and str(item[0]).strip() == "0050" and len(item) > 8:
                    benchmark = number(item[8])
                if fields[:1] == ["指數"] and str(item[0]).strip() == "發行量加權股價指數":
                    taiex = number(item[1])
    return rows, benchmark, taiex


def order_lines(entry: dict, close: float, previous: float | None, names: dict[str, str]) -> dict[str, object]:
    """One engine order as the owner places it: the round lots in the fixed-price session at the close, the odd
    lot in the odd-lot auction with a limit (close ± 1% inside the day's limits, ``costs.order_limit``)."""
    from quant_platform.research.costs import order_limit
    from quant_platform.research.legacy_challenger import _tick

    symbol = str(entry["symbol"])
    code = symbol.split(".")[0]
    row: dict[str, object] = {"code": code, "name": names.get(code, ""), "close": close}
    if entry["side"] == "BLOCKED":
        row.update(side=entry.get("order"), refused=entry.get("reason"))
        return row
    shares = int(round(float(entry["shares"])))
    lots, odd = divmod(shares, 1000)
    side = entry["side"]
    row.update(side=side, shares=shares, model_price=entry["price"], fee=entry["fee"], tax=entry["tax"],
               amount=round(shares * float(entry["price"])))
    parts = []
    if lots:
        parts.append({"session": "盤後定價（14:00–14:30，以收盤價成交，整張）", "shares": lots * 1000, "price": close})
    if odd:
        parts.append({"session": "盤後零股（13:40–14:30 委託，14:30 撮合）", "shares": odd,
                      "limit": order_limit(close, side, previous, tick_size=_tick(symbol))})
    row["parts"] = parts
    return row


def prepare(history: Path, research: Path, day: date, payload: object, costs=None,
            now: datetime | None = None, min_rows: int = MIN_ROWS) -> dict[str, object] | None:
    """Today's orders of every board rule (see the module text); None while the table is not complete."""
    from quant_platform.research.blend import BlendAccount, BlendRule
    from quant_platform.research.costs import broker_costs
    from quant_platform.research.daily import (
        ENGINE_VERSION, RECENT_START, ChipStore, DailyRule, FactorPanel, Industries, account_parking, daily_rankings,
        daily_weights, exposure_schedule, load_industries, market_closes, simulate_daily,
    )
    from quant_platform.research.forward_board import COMMON_START
    from quant_platform.research.model import models_root
    from quant_platform.research.stock_forward import StockForwardTracker, _names
    from quant_platform.research.stock_rules import WARMUP_YEARS, Panel, SeedPlan, load_stock_data

    rows, benchmark, taiex = today_table(payload, day)
    if len(rows) < min_rows or not benchmark or not taiex or day < COMMON_START:
        return None
    started = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    costs = costs or broker_costs("cathay")
    items = [item for item in StockForwardTracker(research).tracked()
             if not item.get("stopped") and item.get("kind") in ("daily", "blend")
             and (item.get("rule") or {}).get("universe", "twse") == "twse"]
    from quant_platform.research.forward_board import load as board_load

    ranks_now = {row["rule_hash"]: row.get("rank", 10**6) for row in (board_load(research) or {}).get("rows", [])}
    items.sort(key=lambda item: ranks_now.get(item["rule_hash"], 10**6))     # the board's best first
    data = load_stock_data(history, RECENT_START.year - WARMUP_YEARS, day.year, extra=(day, rows, benchmark))
    panel = Panel(data)
    market = market_closes(history, panel.sessions).copy()
    if panel.sessions and panel.sessions[-1] == day:
        market[-1] = taiex                        # TAIEX's series is written after 15:14
    fp = FactorPanel(panel, Industries(load_industries(history)), ChipStore(history, panel.sessions, panel.symbols, panel.close),
                     market, models_root(history))
    names = {row["code"]: row["name"] for row in rows} | _names(history, day.year)
    plan = SeedPlan()
    from quant_platform.research.decisions import DecisionStore
    from quant_platform.research.decisions import key as decision_key
    from quant_platform.research.decisions import rankings as kept_rankings

    # 2026-10-15: the days already decided stay as they were; today's decisions are kept, so the 15:30 record and
    # the board hold what these orders say
    store = DecisionStore(research)
    output: list[dict] = []
    folder = research / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{day.isoformat()}.json"
    report = {"date": day.isoformat(), "common_start": COMMON_START.isoformat(), "engine": ENGINE_VERSION,
              "table_rows": len(rows), "taiex": taiex, "benchmark_close": benchmark,
              "started_at": started.isoformat(timespec="seconds"), "total": len(items), "rules": output}

    def save(finished: bool) -> None:          # written after every rule: the page shows the first ones early
        report["done"] = len(output)
        report["finished_at"] = datetime.now(TAIPEI).isoformat(timespec="seconds") if finished else None
        partial = path.with_suffix(".partial")
        partial.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        partial.replace(path)

    for item in items:
        ledger: list = []
        snapshots: dict = {}
        try:
            if item.get("kind") == "blend":
                rule = BlendRule.model_validate(item["rule"])
                BlendAccount(data, fp, rule, costs, COMMON_START, day, store=store,
                             name=decision_key(item["rule_hash"], COMMON_START), through=day).run(
                    COMMON_START, day, plan, ledger=ledger, snapshots=snapshots)
            else:
                rule = DailyRule.model_validate(item["rule"])
                ranks = kept_rankings(fp, rule, COMMON_START, day, store, decision_key(item["rule_hash"], COMMON_START), day)
                simulate_daily(data, rule, costs, COMMON_START, day, ranks, plan, ledger=ledger, snapshots=snapshots,
                               weights=daily_weights(fp, rule, ranks), parked=account_parking(data, fp, rule, costs),
                               exposure=exposure_schedule(data, fp, rule, costs))
        except Exception as error:                # one bad rule does not stop the others
            output.append({"rule_hash": item["rule_hash"], "name": item["name"], "error": str(error)[:200]})
            save(False)
            continue
        orders = []
        for entry in ledger:
            if entry["day"] != day or entry["side"] not in ("BUY", "SELL", "BLOCKED"):
                continue
            series = data.closes.get(str(entry["symbol"]), {})
            earlier = [when for when in series if when < day]
            orders.append(order_lines(entry, series.get(day, 0.0), series[max(earlier)] if earlier else None, names))
        cash, units = snapshots.get(day, (0.0, {}))
        holdings = sorted(({"code": symbol.split(".")[0], "name": names.get(symbol.split(".")[0], ""), "shares": int(count),
                            "value": round(count * data.closes.get(symbol, {}).get(day, 0.0))}
                           for symbol, count in units.items() if count > 0), key=lambda row: -row["value"])
        output.append({"rule_hash": item["rule_hash"], "name": item["name"], "rank": ranks_now.get(item["rule_hash"]),
                       "orders": orders, "holdings": holdings, "cash": round(cash)})
        save(False)
    store.save()
    save(True)
    return report


def load(research: Path, day: date) -> dict[str, object] | None:
    path = research / FOLDER / f"{day.isoformat()}.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
