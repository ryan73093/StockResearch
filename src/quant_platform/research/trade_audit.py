"""Every simulated order checked against the market's rules (2026-10-13; 使用者：確實檢查所有交易都照台股的交易規則
和機制——以前沒有零股你還買零股、漲停買不到你還買、下市了你還賣，都不可以).

``audit`` replays a run's ledger and lists each order that breaks one of:

* traded — a buy or sell on a session the stock had no trade (suspended, delisted, not listed yet);
* limit_up_buy / limit_down_sell — a buy at a close locked limit-up, a sell at a close locked limit-down;
* inside_limits — a fill above the day's limit-up or below its limit-down price (``price_limits``);
* tick — a fill price off the exchange's tick for its price range;
* whole — a fraction of a share bought, sold or held;
* turnover — an order above the rule's share of the stock's turnover that day (``daily.participation``);
* after_delisting — a buy or sell after the stock's write-off day (other than the write-off itself);
* negative — a holding or the cash below zero;
* fee / tax — a fee below the period's minimum per order part, a sell tax that is not the instrument's rate;
* instrument — an order in something other than a listed common stock or 0050.

``run`` audits the rules the forward board tracks (and the 0050 account) on the research period.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from quant_platform.research.costs import CostModel, etf_tick, stock_tick

CHECKS = {
    "traded": "當天沒有成交還買賣", "limit_up_buy": "收盤鎖漲停還買進", "limit_down_sell": "收盤鎖跌停還賣出",
    "inside_limits": "成交價超出當天漲跌停", "tick": "成交價不是升降單位的整數倍", "whole": "有不到 1 股的零碎股",
    "turnover": "單筆超過當天成交值上限", "after_delisting": "下市後還買賣", "negative": "持股或現金小於 0",
    "fee": "手續費低於當時的最低收費", "tax": "交易稅不是該商品的稅率", "instrument": "不是上市櫃普通股或 0050",
}


def _on_tick(price: float, symbol: str) -> bool:
    tick = (etf_tick if symbol.split(".")[0].startswith("00") else stock_tick)(price)
    steps = price / tick
    return abs(steps - round(steps)) < 1e-6


def _instrument(symbol: str) -> bool:
    code = symbol.split(".")[0]
    return code == "0050" or (len(code) == 4 and code.isdigit() and code[0] != "0" and not code.startswith("91"))


def audit(data, ledger: list[dict], costs: CostModel, limit: float | None = None,
          execution: str | None = "same") -> dict[str, list[dict]]:
    """check -> the orders that break it (each with the day, the symbol and what was wrong); ``limit`` is the
    order's largest share of the day's turnover (the rule's ``daily.participation``). ``execution``: where the rule
    fills — "open" is judged at the session's open (the engine's market_locks("open")), "same"/"next" at the close;
    None (a blend whose sleeves fill differently) takes, order by order, whichever of the two the fill is nearer.
    2026-10-21 (稽核：1.5.0 的稽核把隔天開盤成交的 452 筆買進當成收盤鎖漲停還買進)."""
    from quant_platform.research import daily
    from quant_platform.research.legacy_challenger import _tax_kind

    found: dict[str, list[dict]] = defaultdict(list)
    gone = daily.write_off_days(data)
    limit = daily.SAME_DAY_PARTICIPATION if limit is None else limit
    held: dict[str, float] = defaultdict(float)

    def flag(check: str, entry: dict, detail: str) -> None:
        found[check].append({"day": entry["day"].isoformat(), "symbol": entry["symbol"], "detail": detail})

    for entry in ledger:
        side, symbol, day = entry["side"], entry["symbol"], entry["day"]
        if side == "ADJUST":
            if entry.get("shares") is not None:            # the change in shares (a blend's sleeves each log theirs)
                held[symbol] += entry["shares"]
            else:
                held[symbol] = round(held[symbol] * entry["factor"])
            if abs(held[symbol] - round(held[symbol])) > 1e-6:
                flag("whole", entry, f"除權後持有 {held[symbol]:.4f} 股")
            continue
        if side not in ("BUY", "SELL"):
            continue
        shares, fill = float(entry["shares"]), float(entry["price"])
        if entry.get("reason") == "下市，持股歸零":
            held[symbol] -= shares
            continue
        close = data.closes.get(symbol, {}).get(day)
        opened = (getattr(data, "opens", None) or {}).get(symbol, {}).get(day)
        if execution == "open" or (execution is None and opened is not None and close is not None
                                   and abs(fill - opened) < abs(fill - close)):
            at, word = opened, "開盤"
        else:
            at, word = close, "收盤"
        if not _instrument(symbol):
            flag("instrument", entry, symbol)
        if close is None:
            flag("traded", entry, "沒有收盤價")
        else:
            low, high = daily.price_limits(data, symbol, day)
            if side == "BUY" and high is not None and at is not None and at >= high - 1e-6:
                flag("limit_up_buy", entry, f"{word} {at}＝漲停 {high}")
            if side == "SELL" and low is not None and at is not None and at <= low + 1e-6:
                flag("limit_down_sell", entry, f"{word} {at}＝跌停 {low}")
            if (high is not None and fill > high + 1e-6) or (low is not None and fill < low - 1e-6):
                flag("inside_limits", entry, f"成交 {fill}，範圍 {low}～{high}")
        if not _on_tick(fill, symbol):
            flag("tick", entry, f"成交價 {fill}")
        if abs(shares - round(shares)) > 1e-6:
            flag("whole", entry, f"{shares:.4f} 股")
        turnover = data.traded_value.get(symbol, {}).get(day)
        if turnover and symbol != "0050.TW" and shares * fill > limit * turnover + fill + 1:
            flag("turnover", entry, f"{shares * fill:,.0f} 元，當天成交值 {turnover:,.0f} 元")
        if symbol in gone and day >= gone[symbol]:
            flag("after_delisting", entry, f"歸零日 {gone[symbol]}")
        lots, odd = divmod(int(round(shares)), 1000)
        if costs.fee_rate > 0:
            odd_minimum = costs.minimum_fee if day >= daily.ODD_LOT_FEE_FROM else max(costs.minimum_fee, 20)
            least = (max(costs.minimum_fee, 20) if lots else 0) + (odd_minimum if odd else 0)
            if entry["fee"] < least:
                flag("fee", entry, f"手續費 {entry['fee']}，最低 {least}")
        if side == "SELL":
            expected = math.floor(shares * fill * dict(costs.tax_rates)[_tax_kind(symbol)])
            if abs(entry["tax"] - expected) > 1:
                flag("tax", entry, f"交易稅 {entry['tax']}，應為 {expected}")
        held[symbol] += shares if side == "BUY" else -shares
        if held[symbol] < -1e-6:
            flag("negative", entry, f"持股 {held[symbol]:.0f} 股")
    return dict(found)


def run(history: Path, research_dir: Path, costs: CostModel, job=None) -> dict[str, object]:
    """Audit every rule the forward board tracks and the 0050 account, 2015-06-01..RECENT_END."""
    from quant_platform.research import daily
    from quant_platform.research.stock_forward import qualifying_daily
    from quant_platform.research.stock_rules import SeedPlan

    from quant_platform.research.blend import BlendAccount

    data, fp = daily.load(history)
    rules = [rule for rule, _reason in qualifying_daily(research_dir)]          # daily rules and blends of them
    report: dict[str, object] = {"engine": daily.ENGINE_VERSION, "start": daily.RECENT_START.isoformat(),
                                 "end": daily.RECENT_END.isoformat(), "checks": CHECKS, "rules": []}
    totals: dict[str, int] = defaultdict(int)
    orders = 0
    accounts = [(None, "0050（同樣的錢）")] + [(rule, rule.name) for rule in rules]
    if job:
        job.update(total=len(accounts), done=0, force=True)
    for number, (rule, name) in enumerate(accounts):
        ledger: list[dict] = []
        if rule is None:
            run_ = daily.simulate_daily(data, None, costs, daily.RECENT_START, daily.RECENT_END, plan=SeedPlan(),
                                        ledger=ledger)
        elif not isinstance(rule, daily.DailyRule):          # a blend: every sleeve's orders are in the ledger
            run_ = BlendAccount(data, fp, rule, costs, daily.RECENT_START, daily.RECENT_END).run(
                daily.RECENT_START, daily.RECENT_END, SeedPlan(), ledger=ledger)
        else:
            ranks = daily.daily_rankings(fp, rule, daily.RECENT_START, daily.RECENT_END)
            run_ = daily.simulate_daily(data, rule, costs, daily.RECENT_START, daily.RECENT_END, ranks, plan=SeedPlan(),
                                        ledger=ledger, weights=daily.daily_weights(fp, rule, ranks),
                                        parked=daily.account_parking(data, fp, rule, costs),
                                        exposure=daily.exposure_schedule(data, fp, rule, costs))
        if rule is None:
            execution = "same"
        elif isinstance(rule, daily.DailyRule):
            execution = rule.execution
        else:
            kinds = {sleeve.rule.execution for sleeve in rule.sleeves}
            execution = kinds.pop() if len(kinds) == 1 else None
        found = audit(data, ledger, costs, daily.participation(rule) if isinstance(rule, daily.DailyRule)
                      else daily.SAME_DAY_PARTICIPATION, execution)
        trades = [entry for entry in ledger if entry["side"] in ("BUY", "SELL")]
        orders += len(trades)
        for check, items in found.items():
            totals[check] += len(items)
        odd = sum(1 for entry in trades if int(round(entry["shares"])) % 1000)
        report["rules"].append({
            "name": name, "rule_hash": rule.rule_hash if rule else "dca_0050", "orders": len(trades),
            "odd_lot_orders": odd, "before_intraday_odd_lots": sum(1 for entry in trades if entry["day"] < daily.REGIME_START),
            "refused": sum(1 for entry in ledger if entry["side"] == "BLOCKED"),
            "dividends": sum(1 for entry in ledger if entry["side"] == "DIVIDEND"),
            "written_off": sum(1 for entry in ledger if entry.get("reason") == "下市，持股歸零"),
            "final_value": round(run_.values[-1]) if run_.values else None,
            "violations": {check: items[:20] for check, items in found.items()},
            "violation_counts": {check: len(items) for check, items in found.items()}})
        if job:
            job.update(done=number + 1, current=name[:60])
    report["orders"] = orders
    report["violation_counts"] = dict(totals)
    report["created_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    folder = research_dir / "audit"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"trade-audit-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    report["path"] = str(path)
    return report
