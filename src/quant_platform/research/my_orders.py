"""2026-11-14 (使用者：先做一個只能看的頁面「照這個規則，今天你的帳戶要買賣什麼」包括沒買到時隔天怎麼調整；D-045).

A read-only view. A rule's forward account after today's close (the 13:50 orders, ``after_close``) is the target; the
owner's own cash and holdings are brought to the same proportions in whole shares, and the difference is today's
orders. Nothing is carried over from yesterday: an order that did not fill simply shows as a difference again today
and is decided on today's state (使用者 2026-11-14：當天策略就當天做，隔天要參照前一天狀況提出策略) — a stock that
closed at its upper limit cannot be bought after the close and is shown as such, not as an order.

It places nothing, approves nothing and does not change Today's advice.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

from quant_platform.research.costs import PRICE_LIMIT, CostModel, _to_tick, order_limit, stock_tick

FOLDER = Path("forward") / "stocks" / "orders"
RESERVE = 0.012      # of the account kept out of the targets: the odd-lot limit is 1% above the close, and fees
SMALL = 2_000        # a difference under this many NT$ ...
SMALL_SHARE = 0.20   # ... or under this share of the target holding is left alone (a 20 NT$ minimum fee each way)


def days(research: str | Path) -> list[date]:
    """The days an orders file exists for, oldest first."""
    out = []
    for path in (Path(research) / FOLDER).glob("????-??-??.json"):
        try:
            out.append(date.fromisoformat(path.stem))
        except ValueError:
            continue
    return sorted(out)


def _table(research: Path, day: date) -> dict[str, float]:
    from quant_platform.research.after_close import today_table

    path = research / FOLDER / f"{day.isoformat()}.table.json"
    try:
        rows, _benchmark, _taiex = today_table(json.loads(path.read_text(encoding="utf-8")), day)
    except (OSError, ValueError):
        return {}
    return {str(row["code"]): float(row["close"]) for row in rows if row.get("close")}


def closes(research: str | Path, day: date) -> tuple[dict[str, float], dict[str, float]]:
    """({code: the close the 13:50 orders read that day}, {code: the session before's}) from the kept tables; empty
    when a table was not kept."""
    base = Path(research)
    earlier = [when for when in days(base) if when < day]
    return _table(base, day), (_table(base, earlier[-1]) if earlier else {})


def _state(close: float, before: float | None) -> str | None:
    """"up" when the close is at the day's upper limit, "down" at the lower one (10% around the session before's
    close, to the tick — trading rules §5); None when it is neither or the earlier close is not known."""
    if not before or not close:
        return None
    if close >= _to_tick(before * (1 + PRICE_LIMIT), False, stock_tick) - 1e-9:
        return "up"
    if close <= _to_tick(before * (1 - PRICE_LIMIT), True, stock_tick) + 1e-9:
        return "down"
    return None


def _parts(shares: int, side: str, close: float, before: float | None) -> list[dict[str, object]]:
    lots, odd = divmod(shares, 1000)
    parts: list[dict[str, object]] = []
    if lots:
        parts.append({"session": "盤後定價（14:00–14:30，以收盤價成交，整張）", "shares": lots * 1000, "price": close})
    if odd:
        parts.append({"session": "盤後零股（13:40–14:30 委託，14:30 撮合）", "shares": odd,
                      "limit": order_limit(close, side, before, tick_size=stock_tick)})
    return parts


def plan(entry: dict, cash: float, held: dict[str, int], today: dict[str, float] | None = None,
         before: dict[str, float] | None = None, costs: CostModel | None = None) -> dict[str, object]:
    """Today's orders for an account with ``cash`` and ``held`` ({code: shares}) following the rule whose forward
    account after today's close is ``entry`` (one of the orders file's ``rules``)."""
    today, before, costs = today or {}, before or {}, costs or CostModel()
    model = {str(row["code"]): row for row in entry.get("holdings") or [] if row.get("shares")}
    names = {code: row.get("name") or "" for code, row in model.items()}
    for order in entry.get("orders") or []:
        names.setdefault(str(order.get("code")), order.get("name") or "")
    price = {code: today.get(code) or (row["value"] / row["shares"]) for code, row in model.items()}
    unknown = sorted(code for code, shares in held.items() if shares > 0 and code not in price and not today.get(code))
    for code, shares in held.items():
        if shares > 0 and code not in price and today.get(code):
            price[code] = today[code]
    model_total = float(entry.get("cash") or 0) + sum(float(row["value"]) for row in model.values())
    mine = {code: int(shares) for code, shares in held.items() if shares > 0 and code in price}
    total = float(cash) + sum(shares * price[code] for code, shares in mine.items())
    if model_total <= 0 or total <= 0:
        return {"ok": False, "reason": "規則的前向帳戶今天沒有資產" if model_total <= 0 else "你的帳戶沒有現金也沒有持股",
                "total": round(total), "unknown": unknown}
    scale = total * (1 - RESERVE) / model_total
    target = {code: math.floor(row["shares"] * scale) for code, row in model.items()}
    acted = {str(order.get("code")): order for order in entry.get("orders") or []}
    orders, waiting, left = [], [], []
    for code in sorted(set(target) | set(mine), key=lambda item: -(target.get(item, 0) * price[item])):
        want, have, close = target.get(code, 0), mine.get(code, 0), price[code]
        diff = want - have
        if not diff:
            continue
        side, shares = ("BUY", diff) if diff > 0 else ("SELL", -diff)
        row = {"code": code, "name": names.get(code, ""), "side": side, "shares": shares, "close": close,
               "amount": round(shares * close), "have": have, "want": want}
        state = _state(close, before.get(code))
        if (side, state) in (("BUY", "up"), ("SELL", "down")):
            waiting.append({**row, "why": "今天收在漲停，盤後買不到" if side == "BUY" else "今天收在跌停，盤後賣不掉"})
            continue
        if want and have and shares * close < max(SMALL, SMALL_SHARE * want * close):
            left.append({**row, "why": "只差一點，不動（一筆最低手續費 20 元）"})
            continue
        rule_side = (acted.get(code) or {}).get("side")
        if not want:
            row["why"] = "規則今天賣掉了" if rule_side == "SELL" else "規則沒有這一檔"
        elif not have:
            row["why"] = "規則今天新買" if rule_side == "BUY" else "規則持有、你還沒有"
        elif side == "BUY":
            row["why"] = "規則今天加碼" if rule_side == "BUY" else "你的比規則少（之前沒買到，或你的錢變多）"
        else:
            row["why"] = "規則今天減碼" if rule_side == "SELL" else "你的比規則多"
        orders.append(row)
    sells = [row for row in orders if row["side"] == "SELL"]
    buys = [row for row in orders if row["side"] == "BUY"]

    def paid(row: dict) -> float:                      # at the odd-lot limit: what the order can cost at most
        limit = order_limit(row["close"], "BUY", before.get(row["code"]), tick_size=stock_tick)
        amount = row["shares"] * max(limit, row["close"])
        return amount + costs.fee(amount)

    def received(row: dict) -> float:
        amount = row["shares"] * order_limit(row["close"], "SELL", before.get(row["code"]), tick_size=stock_tick)
        return amount - costs.fee(amount) - costs.tax(amount, "stock", "SELL")

    available = float(cash) + sum(received(row) for row in sells)
    needed = sum(paid(row) for row in buys)
    if buys and needed > available:                     # not enough: every buy cut by the same share
        ratio = max(0.0, available / needed) * 0.999
        for row in buys:
            row["shares"] = math.floor(row["shares"] * ratio)
            row["amount"] = round(row["shares"] * row["close"])
            row["cut"] = True
        buys = [row for row in buys if row["shares"] > 0]
        needed = sum(paid(row) for row in buys)
    for row in sells + buys:
        row["parts"] = _parts(row["shares"], row["side"], row["close"], before.get(row["code"]))
    holdings = [{"code": code, "name": names.get(code, ""), "have": mine.get(code, 0), "want": target.get(code, 0),
                 "close": price[code], "value": round(target.get(code, 0) * price[code])}
                for code in sorted(set(target) | set(mine), key=lambda item: -(target.get(item, 0) * price[item]))]
    return {"ok": True, "total": round(total), "cash": round(float(cash)), "scale": scale,
            "model_total": round(model_total), "model_cash": round(float(entry.get("cash") or 0)),
            "sells": sells, "buys": buys, "waiting": waiting, "left": left, "holdings": holdings, "unknown": unknown,
            "sell_amount": round(sum(row["amount"] for row in sells)), "buy_amount": round(sum(row["amount"] for row in buys)),
            "cash_after": round(available - needed), "rule_orders": len(entry.get("orders") or [])}


def account(overview) -> tuple[float, dict[str, int], bool]:
    """(cash, {code: shares}, whether anything was recorded) from the holdings page's account."""
    held = {str(item.symbol).split(".")[0]: int(item.shares) for item in overview.holdings if item.shares > 0}
    return float(overview.cash), held, bool(overview.flows or overview.trades)


def choices(entries: list[dict], pool_rows: list[dict], count: int = 12) -> list[dict[str, object]]:
    """The rules offered on the page: the ones with a forward account today, the highest combined score first
    (pool.combine: the forward record weighs more, D-045)."""
    have = {str(entry["rule_hash"]) for entry in entries if not entry.get("error")}
    rows = [row for row in pool_rows if row.get("spec_hash") in have and row.get("combined") is not None]
    rows.sort(key=lambda row: (-(row.get("combined") or 0), -(row.get("final_value") or 0)))
    return [{"rule_hash": row["spec_hash"], "name": row["name"], "tier": row["tier"], "combined": row["combined"],
             "chain": row.get("chain")} for row in rows[:count]]
