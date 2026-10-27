"""2026-10-21 (使用者 2026-10-16：盤前或盤中買會不會比較有效？購買時機；FinMind Sponsor 的分 K): where in the next
session a decision made after a close would best be filled — a diagnostic on the strongest single rules' real
decisions (research_method §7「盤中成交時機」, written before the run).

The ten single daily rules (no blend, no free robot) that end with the most money among the passing tiers, ranked
from 2021-01 to the end of the backtest; every stock a rule's holdings gained (a buy) or lost (a sell) from one
session to the next is a decision taken after that session's close. SAMPLE of them, half buys and half sells, drawn
at random with a fixed seed; for each, FinMind's one-minute bars of the next session (TaiwanStockKBar, from 2018-12;
one request a stock and day, kept under raw/finmind/TaiwanStockKBar/). Prices compared with that session's close:
the open, the average of the first 30 minutes, the day's average (09:00–13:24), the last minute before the closing
auction (13:24). A buy gains when it pays less than the close, a sell when it gets more."""

from __future__ import annotations

import gzip
import json
import math
import random
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
DATASET = "TaiwanStockKBar"
START = date(2021, 1, 4)
RULES = 10
SAMPLE = 4000
SEED = 20261021
POINTS = {"open": "開盤", "first30": "開盤 30 分鐘均價", "day": "全天均價（09:00–13:24）", "before_close": "13:24 最後一分鐘"}
WORTH = 0.001               # the criterion written first: 0.1% a trade, both sides, t >= 2


def path_for(base: str | Path, code: str, day: date) -> Path:
    return Path(base) / "raw" / "finmind" / DATASET / f"{code}-{day.isoformat()}.json.gz"


def points(rows: list[dict]) -> dict[str, float] | None:
    """The prices of one session's minute bars: the open, the first 30 minutes' and the day's volume-weighted typical
    price ((high + low + close) / 3), the 13:24 bar's close and the close (the 13:30 auction's bar, else the last
    before 14:00). None without bars or a close."""
    bars = sorted((row for row in rows if str(row.get("minute", "")) < "14:00:00"), key=lambda row: row["minute"])
    if not bars:
        return None

    def average(chosen: list[dict]) -> float | None:
        volume = sum(float(row["volume"]) for row in chosen)
        if volume <= 0:
            return None
        return sum((float(row["high"]) + float(row["low"]) + float(row["close"])) / 3 * float(row["volume"])
                   for row in chosen) / volume

    close = float(bars[-1]["close"])
    continuous = [row for row in bars if "09:00:00" <= row["minute"] <= "13:24:59"]
    before = [row for row in continuous if row["minute"] <= "13:24:59"]
    found = {"open": float(bars[0]["open"]), "first30": average([row for row in continuous if row["minute"] < "09:30:00"]),
             "day": average(continuous), "before_close": float(before[-1]["close"]) if before else None, "close": close}
    return found if close > 0 else None


def decisions(base: str | Path, research: str | Path, job=None) -> list[dict[str, object]]:
    """Every buy and sell decision of the RULES strongest single rules from START (their rankings' holdings)."""
    from quant_platform.research import daily
    from quant_platform.research.blend import BlendRule, parse_spec
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.registry import TrialRegistry, newest

    research = Path(research)
    latest = newest(TrialRegistry(research / "trials.jsonl").records(),
                    lambda record: record.kind == "candidate" and record.period == "recent"
                    and str((record.metrics or {}).get("engine", "")).startswith("daily-"))
    chosen = []
    for record in sorted(latest.values(), key=lambda record: -((record.metrics or {}).get("final_value") or 0)):
        if daily.tier(record.metrics)[0] not in daily.PASSING_TIERS:
            continue
        try:
            spec = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
            rule = parse_spec(spec)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(rule, BlendRule) or uses_0050(spec) or rule.free:
            continue
        chosen.append((record.trial_id, rule))
        if len(chosen) >= RULES:
            break
    if job:
        job.update(current="載入行情與因子", force=True)
    _data, fp = daily.load(Path(base))
    output = []
    for number, (trial, rule) in enumerate(chosen):
        if job:
            job.update(current=f"第 {number + 1}／{len(chosen)} 個規則的每天持股：#{trial}", force=True)
        ranks = daily.daily_rankings(fp, rule, START, daily.RECENT_END)
        days = sorted(ranks)
        sessions = fp.sessions
        for earlier, later in zip(days, days[1:]):
            position = fp.index.get(later)
            if position is None or position + 1 >= len(sessions):
                continue
            before, after = set(ranks[earlier]), set(ranks[later])
            for symbol, side in [(symbol, "buy") for symbol in after - before] + [(symbol, "sell") for symbol in before - after]:
                output.append({"trial": trial, "decided": later.isoformat(), "filled": sessions[position + 1].isoformat(),
                               "code": symbol.split(".")[0], "side": side})
    return output


def sample(rows: list[dict], size: int = SAMPLE, seed: int = SEED) -> list[dict]:
    """Half buys and half sells, drawn at random (one per stock and fill day)."""
    unique = {(row["code"], row["filled"], row["side"]): row for row in rows}
    picker = random.Random(seed)
    output = []
    for side in ("buy", "sell"):
        mine = sorted((row for row in unique.values() if row["side"] == side), key=lambda row: (row["filled"], row["code"]))
        output += picker.sample(mine, min(size // 2, len(mine)))
    return output


def fetch(base: str | Path, rows: list[dict], token: str, job=None, per_hour: int = 2400) -> dict[str, int]:
    """One request a stock and fill day (stored; a stored day is not asked again); at most ``per_hour``."""
    from quant_platform.research.history.finmind import URL, QuotaReached

    wanted = sorted({(row["code"], row["filled"]) for row in rows})
    asked = errors = 0
    for number, (code, day) in enumerate(wanted):
        target = path_for(base, code, date.fromisoformat(day))
        if target.is_file():
            continue
        if job:
            job.update(done=number, total=len(wanted), current=f"分 K：{code} {day}")
        query = urllib.parse.urlencode({"dataset": DATASET, "data_id": code, "start_date": day, "end_date": day})
        request = urllib.request.Request(f"{URL}?{query}", headers={"Authorization": f"Bearer {token}",
                                                                     "User-Agent": "StockResearch/1.0"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if payload.get("status") in (402, 429):
                    raise QuotaReached(str(payload.get("msg")))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(gzip.compress(json.dumps(payload.get("data") or []).encode("utf-8")))
                asked += 1
                break
            except QuotaReached:
                time.sleep(3600)
            except (urllib.error.URLError, TimeoutError, ValueError):
                if attempt == 2:
                    errors += 1
                time.sleep(30)
        time.sleep(3600 / per_hour)
    return {"wanted": len(wanted), "asked": asked, "errors": errors}


def forward_buys(research: str | Path, since: date | None = None, until: date | None = None) -> list[dict[str, object]]:
    """2026-10-28 (S9-W37; research_method §7「RL 第七版」的下一版): every stock the forward accounts bought on a day
    (``forward/stocks/log.jsonl``'s trades), once a stock and day, with the fill price — the out-of-sample buys whose
    minute bars test the 13:00-or-close choice."""
    path = Path(research) / "forward" / "stocks" / "log.jsonl"
    found: dict[tuple[str, str], dict[str, object]] = {}
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as lines:
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            day = str(record.get("date", ""))
            if not day or (since and day < since.isoformat()) or (until and day > until.isoformat()):
                continue
            for trade in record.get("trades_today") or []:
                if trade.get("side") == "BUY" and trade.get("code"):
                    found.setdefault((str(trade["code"]), day), {"code": str(trade["code"]), "filled": day, "side": "buy",
                                                                 "price": trade.get("price")})
    return [found[key] for key in sorted(found)]


def fetch_forward(base: str | Path, research: str | Path, token: str, since: date | None = None, job=None) -> dict[str, int]:
    """The minute bars of the forward buys from ``since`` (stored ones are not asked again)."""
    rows = forward_buys(research, since)
    found = fetch(base, rows, token, job)
    return {**found, "days": len({row["filled"] for row in rows})}


def gains(rows: list[dict], prices: dict[tuple[str, str], dict[str, float]]) -> dict[str, object]:
    """Per point and side: the mean gain against the close (a buy: close / price - 1; a sell: price / close - 1), its t
    and how often it beat the close; and the verdict of the criterion written first."""
    summary: dict[str, object] = {}
    for point in POINTS:
        item: dict[str, object] = {}
        for side in ("buy", "sell", "both"):
            values = []
            for row in rows:
                if side != "both" and row["side"] != side:
                    continue
                found = prices.get((row["code"], row["filled"]))
                if not found or not found.get(point) or not found.get("close"):
                    continue
                price, close = found[point], found["close"]
                values.append(close / price - 1 if row["side"] == "buy" else price / close - 1)
            if len(values) < 2:
                item[side] = {"count": len(values)}
                continue
            mean, deviation = statistics.fmean(values), statistics.stdev(values)
            item[side] = {"count": len(values), "mean": round(mean, 6),
                          "t": round(mean / (deviation / math.sqrt(len(values))), 2) if deviation else None,
                          "beat": round(sum(value > 0 for value in values) / len(values), 3)}
        both, buy, sell = item.get("both") or {}, item.get("buy") or {}, item.get("sell") or {}
        item["worth"] = bool((both.get("mean") or 0) >= WORTH and (both.get("t") or 0) >= 2
                             and (buy.get("mean") or 0) > 0 and (sell.get("mean") or 0) > 0)
        summary[point] = item
    return summary


def run(base: str | Path, research: str | Path, token: str, job=None) -> Path:
    base, research = Path(base), Path(research)
    every = decisions(base, research, job)
    chosen = sample(every)
    fetched = fetch(base, chosen, token, job)
    prices = {}
    for code, day in {(row["code"], row["filled"]) for row in chosen}:
        target = path_for(base, code, date.fromisoformat(day))
        if target.is_file():
            found = points(json.loads(gzip.decompress(target.read_bytes()).decode("utf-8")))
            if found:
                prices[(code, day)] = found
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "start": START.isoformat(),
              "rules": sorted({row["trial"] for row in every}), "decisions": len(every), "sampled": len(chosen),
              "with_bars": sum((row["code"], row["filled"]) in prices for row in chosen), "fetch": fetched,
              "points": POINTS, "worth": WORTH, "gains": gains(chosen, prices)}
    folder = research / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"intraday-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


ACCOUNTS = (300_000, 3_000_000, 30_000_000, 67_500_000)
HOLDINGS = (10, 20)


def opening(rows: list[dict]) -> dict[str, float] | None:
    """2026-10-22 (開盤賣做不做得到): the opening auction's price and money (the 09:00 bar; volume in lots), the first
    odd-lot match's price (intraday odd lots match from 09:10: the 09:10 bar's close, else the last bar before it), the
    day's money (bars' volume × typical price) and the close."""
    bars = sorted((row for row in rows if str(row.get("minute", "")) < "14:00:00"), key=lambda row: row["minute"])
    if not bars or bars[0]["minute"] != "09:00:00":
        return None
    first = bars[0]
    upto = [row for row in bars if row["minute"] <= "09:10:00"]
    day = sum(float(row["volume"]) * 1000 * (float(row["high"]) + float(row["low"]) + float(row["close"])) / 3 for row in bars)
    return {"open": float(first["open"]), "auction": float(first["volume"]) * 1000 * float(first["open"]),
            "odd_first": float(upto[-1]["close"]), "day": day, "close": float(bars[-1]["close"])}


def feasibility(base: str | Path, research: str | Path, job=None) -> Path:
    """For the sampled sells (the same seed as ``run``): the open against the first odd-lot match; the opening
    auction's share of the day's money; the engine's cap (NEXT_DAY_PARTICIPATION of the day) as a share of the
    auction; and how often a whole position of an account (ACCOUNTS over HOLDINGS stocks) is more than a tenth, a
    quarter or half of the auction."""
    from quant_platform.research.daily import NEXT_DAY_PARTICIPATION

    base, research = Path(base), Path(research)
    chosen = [row for row in sample(decisions(base, research, job)) if row["side"] == "sell"]
    found = []
    for row in chosen:
        target = path_for(base, row["code"], date.fromisoformat(row["filled"]))
        if target.is_file():
            point = opening(json.loads(gzip.decompress(target.read_bytes()).decode("utf-8")))
            if point and point["close"] > 0 and point["auction"] > 0 and point["day"] > 0:
                found.append(point)

    def stats(values: list[float]) -> dict[str, float]:
        values = sorted(values)
        return {"count": len(values), "mean": round(statistics.fmean(values), 6),
                "median": round(values[len(values) // 2], 6), "p25": round(values[len(values) // 4], 6),
                "p75": round(values[3 * len(values) // 4], 6)} if values else {"count": 0}

    gain_open = [point["open"] / point["close"] - 1 for point in found]
    gain_odd = [point["odd_first"] / point["close"] - 1 for point in found]
    sizes = {}
    for account in ACCOUNTS:
        for holdings in HOLDINGS:
            shares = [account / holdings / point["auction"] for point in found]
            sizes[f"{account // 10_000} 萬／{holdings} 檔"] = {
                "over_10pct": round(sum(value > 0.10 for value in shares) / len(shares), 3) if shares else None,
                "over_25pct": round(sum(value > 0.25 for value in shares) / len(shares), 3) if shares else None,
                "over_50pct": round(sum(value > 0.50 for value in shares) / len(shares), 3) if shares else None}
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "sells": len(found),
              "gain_open": stats(gain_open), "gain_first_odd_lot": stats(gain_odd),
              "auction_share_of_day": stats([point["auction"] / point["day"] for point in found]),
              "engine_cap_share_of_auction": stats([NEXT_DAY_PARTICIPATION * point["day"] / point["auction"] for point in found]),
              "position_vs_auction": sizes}
    folder = research / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"open-feasibility-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
