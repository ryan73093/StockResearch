"""2026-10-13 (使用者：前向策略的起跑時間基準？能否監控前向是否相較其他策略轉強、給調整建議): the
common-start board.

2026-10-17 (使用者：前向觀察的起點照現在的月份往前推兩個月): the common start rolls monthly — the day after the
month's cutoff (``common_start``: in October 09-01, in November 10-01), the first session no data chose any rule
with (research/rolling.py). The board replays every active strategy from it on the owner's account, against the same money in 0050, and holds each against what its own backtest
expects: its mean monthly excess × the months elapsed, with a 90% band of ±1.645 standard deviations × √months.
States: 強於預期 above the band, 符合預期 inside, 弱於預期 below, 警示 under the 1% line (−2.326 sd). The rank
and its change over five sessions show who is getting stronger. Written every trading day after the forward
record (``board.json``; a line per day in ``board_history.jsonl``). A short stretch says little — the band is
wide for weeks; the board is for spotting a strategy that is clearly off its course, not for picking winners
from a few days.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np



def common_start(today: date) -> date:
    """The day after the month's rolling cutoff (the end of the month two months back): 2026-10 → 2026-09-01."""
    from quant_platform.research.rolling import cutoff_for

    return cutoff_for(today) + timedelta(days=1)

BAND, ALERT = 1.645, 2.326
SESSIONS_PER_MONTH = 21
RECENT_SESSIONS = 63                      # 2026-10-13 (使用者：近三個月它會做什麼買賣、成效如何): about three months


def latest_candidates(research: Path) -> dict[str, object]:
    """rule hash -> its latest candidate record, the registry read once (2026-10-29: read again for each of the 3,391
    forward accounts, 20 MB each time)."""
    from quant_platform.research.registry import TrialRegistry

    return {record.spec_hash: record for record in TrialRegistry(research / "trials.jsonl").records()
            if record.kind == "candidate"}


def expectation(research: Path, rule_hash: str, latest_by_hash: dict | None = None) -> dict[str, float] | None:
    """The backtest's mean and standard deviation of the monthly excess over 0050 (its report)."""
    latest = (latest_candidates(research) if latest_by_hash is None else latest_by_hash).get(rule_hash)
    if latest is None:
        return None
    try:
        report = json.loads((research / "reports" / latest.report_file).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    months = [float(value) for value in (report.get("monthly_active_returns") or {}).values()]
    if len(months) < 12:
        return None
    return {"trial": latest.trial_id, "mean": float(np.mean(months)), "sd": float(np.std(months, ddof=1))}


def state(excess: float, months: float, expected: dict | None) -> tuple[str, float | None, float | None, float | None]:
    """(state, expected excess, band low, band high) for a cumulative forward excess over ``months``."""
    if expected is None or months <= 0:
        return "沒有回測可比", None, None, None
    centre = expected["mean"] * months
    width = expected["sd"] * np.sqrt(months)
    low, high = centre - BAND * width, centre + BAND * width
    if excess < centre - ALERT * width:
        label = "警示"
    elif excess < low:
        label = "弱於預期"
    elif excess > high:
        label = "強於預期"
    else:
        label = "符合預期"
    return label, round(centre, 4), round(low, 4), round(high, 4)


ADVICE = {
    "警示": "表現落在回測幾乎不會出現的範圍：先查持股是否擠在同一族群、資料或成交是否異常；一個月後仍在警示線下就停止追蹤、回頭檢討規則。",
    "弱於預期": "低於回測的一般範圍，但還在可能的波動內：照規則繼續，不要因短期弱勢改參數；看是否和其他策略同時轉弱（市場因素）。",
    "符合預期": "照規則繼續。",
    "強於預期": "高於回測的一般範圍：不要因短期強勢加碼或改規則；確認不是單一兩檔股票撐起來的。",
    "沒有回測可比": "這個策略沒有可比的回測月超額，只看它和 0050 的差距。",
}


def newer_on_disk(research: Path, last: date, began: datetime) -> bool:
    """2026-10-29 (使用者：不要為了省效能最後又給我錯的資料): the board written is from a later session, or from the same
    session by a run that began later — it read a later state, and this run's result is not written over it. A board
    written before 10/29 has no start; only its session counts."""
    try:
        board = json.loads((research / "forward" / "stocks" / "board.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    written = str(board.get("date") or "")
    if written != last.isoformat():
        return written > last.isoformat()
    try:
        return datetime.fromisoformat(str(board["started_at"])) > began
    except (KeyError, ValueError):
        return False


def build(research: Path, history: Path, today: date, costs=None) -> dict[str, object] | None:
    """Replay every forward account from the common start and write the board. 2026-10-29: the run's outputs (the
    board, its history line, every account's last three months) are written together at the end, and only if no run
    that began later — or saw a later session — wrote its board meanwhile (``newer_on_disk``; a run that sees one stops
    early): a board that began at 13:06 on the morning's data and finished after the 15:30 one would otherwise have put
    the earlier session back on the page."""
    from quant_platform.research.blend import BlendAccount, BlendRule
    from quant_platform.research.costs import broker_costs
    from quant_platform.research.daily import (
        RECENT_START,
        ChipStore,
        DailyRule,
        FactorPanel,
        Industries,
        account_parking,
        daily_rankings,
        daily_weights,
        exposure_schedule,
        load_industries,
        market_closes,
        simulate_daily,
    )
    from quant_platform.research.legacy_challenger import BENCHMARK
    from quant_platform.research.metrics import max_drawdown, unit_values
    from quant_platform.research.model import models_root
    from quant_platform.research.stock_forward import StockForwardTracker
    from quant_platform.research.stock_rules import WARMUP_YEARS, Panel, SeedPlan, load_stock_data

    began = datetime.now()
    costs = costs or broker_costs("cathay")
    items = [item for item in StockForwardTracker(research).tracked()
             if not item.get("stopped") and item.get("kind") in ("daily", "blend")
             and (item.get("rule") or {}).get("universe", "twse") == "twse"]
    if not items:
        return None
    first = common_start(today)                  # 2026-10-17: rolls monthly
    data = load_stock_data(history, RECENT_START.year - WARMUP_YEARS, today.year)
    days = [day for day in data.sessions if first <= day <= today and day in data.closes.get(BENCHMARK, {})]
    if not days:
        return None
    last = days[-1]
    panel = Panel(data)
    fp = FactorPanel(panel, Industries(load_industries(history)), ChipStore(history, panel.sessions, panel.symbols, panel.close),
                     market_closes(history, panel.sessions), models_root(history))
    plan = SeedPlan()
    from quant_platform.research.decisions import DecisionStore
    from quant_platform.research.decisions import key as decision_key
    from quant_platform.research.decisions import rankings as kept_rankings

    store = DecisionStore(research)                 # 2026-10-15: decisions already taken stay as they were made
    bench = simulate_daily(data, None, costs, first, last, plan=plan)
    bench_units = np.array(unit_values(bench.values, bench.flows))
    months = len(days) / SESSIONS_PER_MONTH
    latest_by_hash = latest_candidates(research)
    rows = []
    for index, item in enumerate(items):
        if index % 200 == 0 and newer_on_disk(research, last, began):
            return {"superseded": True, "date": last.isoformat(), "started_at": began.isoformat()}
        ledger: list = []
        try:
            if item.get("kind") == "blend":
                rule = BlendRule.model_validate(item["rule"])
                run = BlendAccount(data, fp, rule, costs, first, last, store=store,
                                   name=decision_key(item["rule_hash"], first), through=last).run(
                    first, last, plan, ledger=ledger)
            else:
                rule = DailyRule.model_validate(item["rule"])
                ranks = kept_rankings(fp, rule, first, last, store, decision_key(item["rule_hash"], first), last)
                run = simulate_daily(data, rule, costs, first, last, ranks, plan, ledger=ledger,
                                     weights=daily_weights(fp, rule, ranks), parked=account_parking(data, fp, rule, costs),
                                     exposure=exposure_schedule(data, fp, rule, costs))
        except Exception as error:                    # one bad item does not stop the board
            rows.append({"rule_hash": item["rule_hash"], "name": item["name"], "error": str(error)[:200]})
            continue
        units = np.array(unit_values(run.values, run.flows))
        excess = float(units[-1] / bench_units[-1] - 1)
        expected = expectation(research, item["rule_hash"], latest_by_hash)
        label, centre, low, high = state(excess, months, expected)
        drawdown, _peak, _trough = max_drawdown(list(units))
        rows.append({
            "rule_hash": item["rule_hash"], "name": item["name"], "trial": (expected or {}).get("trial"),
            "since": item["since"], "value": round(run.values[-1], 2), "benchmark_value": round(bench.values[-1], 2),
            "contributed": round(run.contributed, 2), "return": round(float(units[-1] - 1), 4),
            "benchmark_return": round(float(bench_units[-1] - 1), 4), "excess": round(excess, 4),
            "max_drawdown": round(float(drawdown), 4), "state": label, "expected": centre, "band_low": low,
            "band_high": high, "advice": ADVICE[label],
            "buys": sum(1 for entry in ledger if entry["side"] == "BUY"),
            "sells": sum(1 for entry in ledger if entry["side"] == "SELL")})
    # 2026-10-13: the last three months of every strategy, every order (research/forward_board.py ``recent``)
    from quant_platform.research.stock_forward import _names

    names = _names(history, last.year)
    window = [day for day in data.sessions if day <= last][-RECENT_SESSIONS:]
    folder = research / "forward" / "stocks"
    target = folder / "recent"
    target.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("recent.staging-*"):      # left by a run that was stopped (a day old: none still running)
        if old.is_dir() and time.time() - old.stat().st_mtime > 86400:
            shutil.rmtree(old, ignore_errors=True)
    staging = folder / f"recent.staging-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    by_hash = {row["rule_hash"]: row for row in rows}
    for index, item in enumerate(items):
        if index % 200 == 0 and newer_on_disk(research, last, began):
            shutil.rmtree(staging, ignore_errors=True)
            return {"superseded": True, "date": last.isoformat(), "started_at": began.isoformat()}
        try:
            replay = recent(research, history, item, data, fp, costs, window, names)
        except Exception as error:
            replay = {"rule_hash": item["rule_hash"], "name": item["name"], "error": str(error)[:200]}
        if replay:
            (staging / f"{item['rule_hash']}.json").write_text(json.dumps(replay, ensure_ascii=False), encoding="utf-8")
            row = by_hash.get(item["rule_hash"])
            if row is not None and "periods" in replay:
                row["recent"] = replay["periods"].get("all")
    ranked = sorted((row for row in rows if "excess" in row), key=lambda row: -row["excess"])
    for place, row in enumerate(ranked, start=1):
        row["rank"] = place
    store.save()                                      # merges; never moves an account's decisions back
    from quant_platform.research.shared_steps import exclusive

    with exclusive(folder / "board.write.lock"):     # the check and the writes, one run at a time
        if newer_on_disk(research, last, began):
            shutil.rmtree(staging, ignore_errors=True)
            return {"superseded": True, "date": last.isoformat(), "started_at": began.isoformat()}
        history_path = folder / "board_history.jsonl"
        earlier = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()] \
            if history_path.is_file() else []
        earlier = [entry for entry in earlier if entry.get("date") != last.isoformat()]
        before = earlier[-5] if len(earlier) >= 5 else (earlier[0] if earlier else None)
        for row in ranked:
            previous = (before or {}).get("ranks", {}).get(row["rule_hash"])
            row["rank_change"] = (previous - row["rank"]) if previous else None      # positive: moved up
        board = {"built_at": datetime.now().isoformat(timespec="seconds"), "started_at": began.isoformat(),
                 "common_start": first.isoformat(), "date": last.isoformat(), "sessions": len(days),
                 "months": round(months, 2), "rows": rows,
                 "counts": {label: sum(1 for row in rows if row.get("state") == label) for label in ADVICE}}
        for path in staging.glob("*.json"):
            os.replace(path, target / path.name)
        shutil.rmtree(staging, ignore_errors=True)
        partial = folder / f"board.json.{os.getpid()}.partial"
        partial.write_text(json.dumps(board, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(partial, folder / "board.json")
        partial = folder / f"board_history.jsonl.{os.getpid()}.partial"
        with partial.open("w", encoding="utf-8") as handle:
            for entry in [*earlier, {"date": last.isoformat(), "ranks": {row["rule_hash"]: row["rank"] for row in ranked},
                                     "excess": {row["rule_hash"]: row["excess"] for row in ranked}}]:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        os.replace(partial, history_path)
    return board


def _period(values: list[float], flows: list[float], bench_values: list[float], bench_flows: list[float]) -> dict:
    from quant_platform.research.metrics import max_drawdown, unit_values

    if len(values) < 2:
        return {}
    units, base = unit_values(values, flows), unit_values(bench_values, bench_flows)
    drawdown, _peak, _trough = max_drawdown(units)
    return {"return": round(units[-1] / units[0] - 1, 4), "benchmark_return": round(base[-1] / base[0] - 1, 4),
            "excess": round((units[-1] / units[0]) / (base[-1] / base[0]) - 1, 4), "max_drawdown": round(drawdown, 4)}


def recent(research: Path, history: Path, item: dict, data, fp, costs, days: list, names: dict) -> dict | None:
    """The last RECENT_SESSIONS sessions of one strategy on the owner's account (NT$300,000 at the start,
    NT$10,000 on the 5th), every order it would have placed — the ones the market refused too — its holdings
    and its value against the same money in 0050; split at the month's rolling cutoff (before it the rule was
    chosen with that data, after it nothing chose it)."""
    from quant_platform.research.blend import BlendAccount, BlendRule
    from quant_platform.research.daily import (
        DailyRule,
        account_parking,
        daily_rankings,
        daily_weights,
        exposure_schedule,
        simulate_daily,
    )
    from quant_platform.research.stock_rules import SeedPlan

    from quant_platform.research.rolling import cutoff_for

    if len(days) < 2:
        return None
    start, last = days[0], days[-1]
    research_end = cutoff_for(last)
    plan, ledger, snapshots = SeedPlan(), [], {}
    if item.get("kind") == "blend":
        rule = BlendRule.model_validate(item["rule"])
        run = BlendAccount(data, fp, rule, costs, start, last).run(start, last, plan, ledger=ledger, snapshots=snapshots)
    else:
        rule = DailyRule.model_validate(item["rule"])
        ranks = daily_rankings(fp, rule, start, last)
        run = simulate_daily(data, rule, costs, start, last, ranks, plan, ledger=ledger, snapshots=snapshots,
                             weights=daily_weights(fp, rule, ranks), parked=account_parking(data, fp, rule, costs),
                             exposure=exposure_schedule(data, fp, rule, costs))
    bench = simulate_daily(data, None, costs, start, last, plan=plan)
    contributed = 0.0
    curve = []
    for day, value, flow, base in zip(run.days, run.values, run.flows, bench.values):
        contributed += flow
        curve.append({"date": day.isoformat(), "value": round(value, 2), "benchmark": round(base, 2),
                      "contributed": round(contributed, 2)})
    trades = []
    for entry in ledger:
        code = str(entry["symbol"]).split(".")[0]
        if (entry["side"] in ("DIVIDEND", "REFUND")
                or (entry["side"] == "ADJUST" and entry.get("kind") in ("stock_dividend", "reduction"))):
            # engine 1.4.0: cash dividends (paid later, less the health-insurance premium) and stock dividends;
            # 1.5.0: a capital reduction's shares exchanged and money back
            if entry["side"] == "DIVIDEND":
                note = (f"現金股利（{entry['paid'].isoformat() if entry.get('paid') and entry['paid'] != date.max else '日後'}入帳"
                        f"{'，扣補充保費 ' + format(entry['premium'], ',') + ' 元' if entry.get('premium') else ''}）")
            elif entry["side"] == "REFUND":
                note = "減資退還股款"
            elif entry.get("kind") == "reduction":
                note = f"減資換股 {int(entry['shares']):,} 股"
            else:
                credited = entry.get("credited")
                note = f"配股 {int(entry['shares']):,} 股" + (
                    f"（{credited.isoformat()} 撥入）" if credited and credited != date.max else "")
            trades.append({"date": entry["day"].isoformat(), "code": code, "name": names.get(code, ""),
                           "side": entry["side"], "income": note,
                           "amount": round(float(entry.get("net", entry.get("cash", 0.0)) or 0.0), 0)})
            continue
        if entry["side"] not in ("BUY", "SELL", "BLOCKED"):
            continue
        row = {"date": entry["day"].isoformat(), "code": code, "name": names.get(code, ""),
               "side": entry.get("order", entry["side"]) if entry["side"] == "BLOCKED" else entry["side"]}
        if entry["side"] == "BLOCKED":
            row["refused"] = entry["reason"]
        elif entry.get("reason"):
            row["note"] = entry["reason"]                 # 1.3.1: 下市，持股歸零
        else:
            row.update(shares=round(float(entry["shares"]), 4), price=entry["price"], fee=entry["fee"], tax=entry["tax"],
                       amount=round(float(entry["shares"]) * float(entry["price"]), 0))
        trades.append(row)
    cash, units = snapshots[last]
    holdings = sorted(({"code": symbol.split(".")[0], "name": names.get(symbol.split(".")[0], ""),
                        "value": round(count * (data.last_close(symbol, last) or 0.0), 0)}
                       for symbol, count in units.items()), key=lambda row: -row["value"])
    split = next((index for index, day in enumerate(run.days) if day > research_end), None)
    periods = {"all": _period(run.values, run.flows, bench.values, bench.flows)}
    if split:
        periods["research"] = _period(run.values[:split], run.flows[:split], bench.values[:split], bench.flows[:split])
        periods["unseen"] = _period(run.values[split - 1:], [0.0] + run.flows[split:], bench.values[split - 1:],
                                    [0.0] + bench.flows[split:])
    return {"rule_hash": item["rule_hash"], "name": item["name"], "start": start.isoformat(), "end": last.isoformat(),
            "research_end": research_end.isoformat(), "periods": periods, "curve": curve, "trades": trades,
            "holdings": holdings, "cash": round(cash, 0),
            "counts": {"buys": sum(1 for row in trades if row["side"] == "BUY" and "refused" not in row),
                       "sells": sum(1 for row in trades if row["side"] == "SELL" and "refused" not in row),
                       "refused": sum(1 for row in trades if "refused" in row)},
            "costs": int(run.fees + run.taxes), "built_at": datetime.now().isoformat(timespec="seconds")}


def load_recent(research: Path, rule_hash: str) -> dict | None:
    path = research / "forward" / "stocks" / "recent" / f"{rule_hash}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except ValueError:
        return None


def load(research: Path) -> dict[str, object] | None:
    path = research / "forward" / "stocks" / "board.json"
    try:
        board = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except ValueError:
        return None
    return group_twins(board) if board else None


def group_twins(board: dict) -> dict:
    """2026-10-25 (使用者：可悲 — the top four were the same picks under four names): accounts whose results are the same
    to the yuan (value, buys, sells, drawdown) are one row, the best-ranked named, the others listed as twins — their
    differing settings did not come into play yet. Places run over the distinct results; ``distinct`` and ``ahead``
    count them."""
    rows = board.get("rows") or []
    ranked = sorted((row for row in rows if row.get("rank")), key=lambda row: row["rank"])
    groups: dict[tuple, dict] = {}
    for row in ranked:
        key = (round(row.get("value") or 0), row.get("buys"), row.get("sells"), round(row.get("max_drawdown") or 0, 4),
               row.get("excess"))
        if key in groups:
            groups[key].setdefault("twins", []).append({"rule_hash": row["rule_hash"], "name": row["name"],
                                                         "trial": row.get("trial")})
            row["twin_of"] = groups[key]["rule_hash"]
        else:
            groups[key] = row
    for place, row in enumerate(groups.values(), start=1):
        row["place"] = place
    board["distinct"] = len(groups)
    board["ahead"] = sum(1 for row in groups.values() if row.get("excess", 0) > 0)
    return board
