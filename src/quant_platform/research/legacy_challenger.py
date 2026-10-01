"""Legacy challenger evaluation (REQUIREMENTS §8, S5-W06).

The legacy ML system ranked about 500 Taiwan stocks every session by a model's predicted 5-day
return. Its stored walk-forward predictions are out of sample: each 63-session fold was predicted by a
model trained only on rows whose label was known before the fold began (application/model_research.py).
This module asks of it what the new research asks of every rule: with the same monthly cash flow,
costs and after-hours odd-lot fills, does buying what the model liked beat buying 0050 on the same
days?

Three uses of the signal are registered up front and all are reported:

* ``top5_hold``     — each month's money buys the five best-ranked stocks, held for good;
* ``top5_weekly``   — hold the five best, re-ranked every five sessions (the model's horizon);
* ``top10_monthly`` — hold the ten best, re-ranked on each contribution day.

Only stocks with at least NT$20 million traded per session over the last 20 sessions and a close of
NT$10 or more are eligible, so odd-lot fills are plausible. Both arms (the legacy picks and 0050) use
the same accounting: prices are Yahoo closes (already adjusted for splits); on an official ex-rights /
ex-dividend date units grow by close before ÷ reference price for cash-only events (TWSE / TPEx tables,
dividends reinvested at once) or by Yahoo's own adjustment on that day when shares were distributed;
an unexplained overnight move beyond ±40% (the daily limit is 10%) is a split or capital change.
Fills: close ± 20 bps rounded to the tick against the trader; fees and the 0.3% (stock) / 0.1% (ETF)
sell tax as in research/costs.py.
"""

from __future__ import annotations

import json
import math
import random
import sqlite3
import statistics
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_platform.research.cashflow import ContributionPlan
from quant_platform.research.costs import BROKERS, CostModel, affordable_shares, etf_tick, fill_price, stock_tick
from quant_platform.research.metrics import max_drawdown, unit_values, xirr

TAIPEI = ZoneInfo("Asia/Taipei")
BENCHMARK = "0050.TW"
SOURCE = "yahoo_finance"
LIQUIDITY_TWD = 20_000_000
LIQUIDITY_SESSIONS = 20
MIN_PRICE = 10.0
DECISION_CUTOFF = time(14, 30)        # predictions must be available before the after-hours auction
SPLIT_GAP = 0.6                       # an overnight ratio outside [0.6, 1/0.6] is a corporate action
STANDARD_PLAN = ContributionPlan(monthly_amount=10_000, day_of_month=5)
EVALUATION_VERSION = "1.1.0"


@dataclass(frozen=True)
class Variant:
    key: str
    name: str
    top: int
    rebalance: str       # never | every_5 | monthly
    order: str = "best"  # best | worst | random (controls)
    seed: int = 0


VARIANTS = (
    Variant("top5_hold", "新資金買預測前 5 名、長期持有", 5, "never"),
    Variant("top5_weekly", "持有預測前 5 名、每 5 個交易日換股", 5, "every_5"),
    Variant("top10_monthly", "持有預測前 10 名、每月換股", 10, "monthly"),
)
RANDOM_SEEDS = 10          # full-period runs of each random control
RANDOM_WINDOW_SEEDS = 5    # rolling-window runs of each random control
EARLY_LARGE = 200          # sensitivity: only stocks already among the most traded before the period
SCAN_SIZES = (100, 200, 300, 400)
SCAN_SEEDS = 3             # random-control runs per point of the size scan


def control(variant: Variant, order: str, seed: int = 0) -> Variant:
    """The same rule with random picks (what the universe alone gives) or the worst-ranked picks."""
    label = "隨機選" if order == "random" else "選預測最差的"
    return Variant(f"{order}_{variant.key}", f"對照：{variant.name}（改成{label}）", variant.top,
                   variant.rebalance, order, seed)


def picks_for(ranked: list[str], variant: Variant, day: date, previous: list[str] | None = None,
              model_previous: list[str] | None = None) -> list[str]:
    """``previous`` is this run's picks and ``model_previous`` the model's own picks at the last pick
    day. A random control changes as many names as the model does today, so its turnover and costs
    match the model's and only the choice of names differs."""
    if variant.order == "worst":
        return list(reversed(ranked[-variant.top:]))
    if variant.order == "random":
        generator = random.Random(f"{variant.seed}-{day.isoformat()}")
        if not previous or model_previous is None:
            return generator.sample(ranked, min(variant.top, len(ranked)))
        changes = len(set(ranked[: variant.top]) - set(model_previous))
        eligible = set(ranked)
        still = [symbol for symbol in previous if symbol in eligible]   # a name no longer eligible goes
        kept = generator.sample(still, min(len(still), max(0, variant.top - changes)))
        need = min(variant.top, len(ranked)) - len(kept)
        fresh = [symbol for symbol in ranked if symbol not in previous]
        chosen = generator.sample(fresh, min(need, len(fresh)))
        dropped = [symbol for symbol in still if symbol not in kept]     # only when fresh names run out
        return kept + chosen + generator.sample(dropped, min(need - len(chosen), len(dropped)))
    return ranked[: variant.top]


@dataclass(frozen=True)
class LegacyData:
    sessions: list[date]
    closes: dict[str, dict[date, float]]
    traded_value: dict[str, dict[date, float]]
    factors: dict[str, dict[date, float]]               # units multiplier at that session's open
    predictions: dict[date, dict[str, float]]            # decision session → symbol → predicted return
    notes: dict[str, object] = field(default_factory=dict)

    def last_close(self, symbol: str, day: date) -> float | None:
        series = self.closes.get(symbol, {})
        if day in series:
            return series[day]
        earlier = [item for item in series if item <= day]
        return series[max(earlier)] if earlier else None


# --- loading ------------------------------------------------------------------------------------
def roc_date(text: str) -> date | None:
    """115年01月02日, 115/01/02 → 2026-01-02."""
    cleaned = str(text).strip().replace("年", "/").replace("月", "/").replace("日", "")
    try:
        year, month, day = (int(part) for part in cleaned.split("/"))
        return date(year + 1911, month, day)
    except ValueError:
        return None


def _number(text: object) -> float | None:
    try:
        return float(str(text).replace(",", "").strip())
    except ValueError:
        return None


def exchange_events(raw_dir: Path, first_year: int) -> dict[str, dict[date, tuple[str, float, float]]]:
    """Official ex-rights / ex-dividend events: symbol → day → (kind, close before, reference price).
    kind is "cash" for cash-only events and "shares" when shares were distributed."""
    events: dict[str, dict[date, tuple[str, float, float]]] = defaultdict(dict)
    for path in sorted((raw_dir / "twse_ex_rights").rglob("*.json")):
        if not path.stem.isdigit() or int(path.stem) < first_year:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload = payload.get("payload", payload)
        fields = payload.get("fields") or []
        for row in payload.get("data") or []:
            record = dict(zip(fields, row))
            day, kind = roc_date(record.get("資料日期", "")), str(record.get("權/息", "")).strip()
            before, reference = _number(record.get("除權息前收盤價")), _number(record.get("除權息參考價"))
            if day and before and reference and before > 0 and reference > 0 and kind:
                events[f"{str(record.get('股票代號')).strip()}.TW"][day] = (
                    "cash" if kind == "息" else "shares", before, reference)
    for path in sorted((raw_dir / "tpex_ex_rights").rglob("*.json")):
        if not path.stem.isdigit() or int(path.stem) < first_year:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for table in payload.get("tables") or []:
            fields = table.get("fields") or []
            for row in table.get("data") or []:
                record = dict(zip(fields, row))
                day, kind = roc_date(record.get("除權息日期", "")), str(record.get("權/息", "")).strip()
                before, reference = _number(record.get("除權息前收盤價")), _number(record.get("除權息參考價"))
                if day and before and reference and before > 0 and reference > 0 and kind:
                    events[f"{str(record.get('代號')).strip()}.TWO"][day] = (
                        "cash" if kind == "除息" else "shares", before, reference)
    return events


def unit_factors(closes: dict[date, float], adjusted: dict[date, float],
                 events: dict[date, tuple[str, float, float]]) -> dict[date, float]:
    """Units multiplier per session: official cash dividends, Yahoo's adjustment when shares were
    distributed, and splits or capital changes that explain an overnight move beyond the daily limit."""
    factors: dict[date, float] = {}
    days = sorted(closes)
    for previous, day in zip(days, days[1:]):
        event = events.get(day)
        if event is not None:
            kind, before, reference = event
            if kind == "cash" and before > reference:
                factors[day] = before / reference
            elif previous in adjusted and day in adjusted and closes[previous] > 0 and closes[day] > 0:
                implied = (adjusted[day] / closes[day]) / (adjusted[previous] / closes[previous])
                if implied > 1:
                    factors[day] = implied
            continue
        ratio = closes[previous] / closes[day] if closes[day] > 0 else 1.0
        if not SPLIT_GAP < ratio < 1 / SPLIT_GAP:
            whole = round(ratio) if ratio >= 1 else 1 / round(1 / ratio)
            factors[day] = whole if abs(ratio - whole) <= 0.15 * whole else ratio
    return factors


def load_legacy_data(database: Path, raw_dir: Path, experiment_id: int) -> LegacyData:
    """Predictions of one legacy experiment, Yahoo daily bars and the official corporate actions,
    read from the production database in read-only mode."""
    uri = f"file:{Path(database).resolve().as_posix()}?mode=ro"
    predictions: dict[date, dict[str, float]] = defaultdict(dict)
    late = 0
    with closing(sqlite3.connect(uri, uri=True, timeout=30)) as db:
        model = db.execute("SELECT model_name, label_name FROM model_experiments WHERE id = ?", (experiment_id,)).fetchone()
        for symbol, event_time, available_time, value in db.execute(
            "SELECT symbol, event_time, available_time, predicted_value FROM model_predictions WHERE experiment_id = ?",
            (experiment_id,),
        ):
            event = datetime.fromisoformat(str(event_time)).replace(tzinfo=UTC).astimezone(TAIPEI)
            available = datetime.fromisoformat(str(available_time)).replace(tzinfo=UTC).astimezone(TAIPEI)
            cutoff = datetime.combine(event.date(), DECISION_CUTOFF, TAIPEI)
            if available > cutoff:
                late += 1  # not known before that day's after-hours auction
                continue
            predictions[event.date()][symbol] = float(value)
        symbols = sorted({symbol for day in predictions.values() for symbol in day} | {BENCHMARK})
        closes: dict[str, dict[date, float]] = defaultdict(dict)
        adjusted: dict[str, dict[date, float]] = defaultdict(dict)
        traded: dict[str, dict[date, float]] = defaultdict(dict)
        first_day = min(predictions) - timedelta(days=60)
        for start in range(0, len(symbols), 200):
            chunk = symbols[start:start + 200]
            marks = ",".join("?" for _ in chunk)
            for symbol, event_time, close, adjusted_close, volume in db.execute(
                f"SELECT symbol, event_time, close, adjusted_close, volume FROM market_bars WHERE interval = '1d' "
                f"AND source = ? AND event_time >= ? AND symbol IN ({marks})",
                (SOURCE, first_day.isoformat(), *chunk),
            ):
                day = datetime.fromisoformat(str(event_time)).replace(tzinfo=UTC).astimezone(TAIPEI).date()
                if close is None or float(close) <= 0:
                    continue
                closes[symbol][day] = float(close)
                if adjusted_close is not None:
                    adjusted[symbol][day] = float(adjusted_close)
                traded[symbol][day] = float(close) * float(volume or 0)
    events = exchange_events(Path(raw_dir), first_day.year)
    factors = {symbol: unit_factors(closes[symbol], adjusted[symbol], events.get(symbol, {})) for symbol in closes}
    sessions = sorted(closes[BENCHMARK])
    return LegacyData(
        sessions=sessions, closes=dict(closes), traded_value=dict(traded), factors=factors,
        predictions=dict(predictions),
        notes={"experiment_id": experiment_id, "model": model[0] if model else None,
               "label": model[1] if model else None, "late_predictions": late,
               "symbols": len(symbols), "official_events": sum(len(item) for item in events.values())},
    )


# --- ranking ------------------------------------------------------------------------------------
def average_traded_value(data: LegacyData) -> dict[str, dict[date, float]]:
    """Mean traded value of each symbol's last LIQUIDITY_SESSIONS sessions, up to and including the day."""
    output: dict[str, dict[date, float]] = {}
    for symbol, history in data.traded_value.items():
        window: list[float] = []
        total, averages = 0.0, {}
        for day in sorted(history):
            window.append(history[day])
            total += history[day]
            if len(window) > LIQUIDITY_SESSIONS:
                total -= window.pop(0)
            if len(window) == LIQUIDITY_SESSIONS:
                averages[day] = total / LIQUIDITY_SESSIONS
        output[symbol] = averages
    return output


def early_large(data: LegacyData, count: int = EARLY_LARGE) -> set[str]:
    """Stocks already among the ``count`` most traded in the sessions before the first prediction.
    The pool was chosen by market value in 2026, so a small stock in it is one that grew; a stock
    that was already large in 2021 owes less of its place to how it did afterwards."""
    first = min(data.predictions)
    averages = {}
    for symbol in {symbol for day in data.predictions.values() for symbol in day}:
        before = [value for day, value in data.traded_value.get(symbol, {}).items() if day < first]
        if before:
            averages[symbol] = statistics.fmean(before)
    return set(sorted(averages, key=lambda symbol: (-averages[symbol], symbol))[:count])


def rankings(data: LegacyData) -> dict[date, list[str]]:
    """Eligible symbols of each session, best predicted return first (ties by code)."""
    averages = average_traded_value(data)
    output = {}
    for day, values in data.predictions.items():
        eligible = [
            symbol for symbol in values
            if (data.closes.get(symbol, {}).get(day) or 0.0) >= MIN_PRICE
            and averages.get(symbol, {}).get(day, 0.0) >= LIQUIDITY_TWD
        ]
        output[day] = sorted(eligible, key=lambda symbol: (-values[symbol], symbol))
    return output


# --- simulation ---------------------------------------------------------------------------------
@dataclass
class RunResult:
    name: str
    days: list[date]
    values: list[float]
    flows: list[float]
    contributions: list[tuple[date, float]]
    trades: int = 0
    fees: int = 0
    taxes: int = 0

    @property
    def final_value(self) -> float:
        return self.values[-1]

    @property
    def contributed(self) -> float:
        return sum(amount for _, amount in self.contributions)

    def summary(self) -> dict[str, object]:
        rate = xirr([(day, -amount) for day, amount in self.contributions] + [(self.days[-1], self.final_value)])
        drawdown, _peak, _trough = max_drawdown(unit_values(self.values, self.flows))
        return {
            "final_value": round(self.final_value, 2), "contributed": round(self.contributed, 2),
            "xirr": None if rate is None else round(rate, 6), "max_drawdown": round(drawdown, 6),
            "trades": self.trades, "fees": self.fees, "taxes": self.taxes,
        }


def _tick(symbol: str):
    return etf_tick if symbol.split(".")[0].startswith("00") else stock_tick


def _tax_kind(symbol: str) -> str:
    code = symbol.split(".")[0]
    if code.startswith("00"):
        return "bond_etf" if code.endswith("B") else "stock_etf"
    return "stock"


def simulate(data: LegacyData, variant: Variant | None, costs: CostModel, start: date, end: date,
             ranks: dict[date, list[str]] | None = None, plan: ContributionPlan = STANDARD_PLAN) -> RunResult:
    """``variant=None`` is the benchmark: every contribution buys 0050 the same day."""
    sessions = [day for day in data.sessions if start <= day <= end]
    contributions = plan.schedule(sessions, start, end)
    by_day = dict(contributions)
    ranks = ranks if ranks is not None else (rankings(data) if variant is not None else {})
    result = RunResult(variant.key if variant else "dca_0050", sessions, [], [], contributions)
    cash, units = 0.0, defaultdict(float)
    pending = False
    previous: list[str] | None = None
    model_previous: list[str] | None = None

    def buy(symbol: str, budget: float) -> None:
        nonlocal cash
        close = data.closes.get(symbol, {}).get(day)
        if close is None or budget <= 0:
            return
        price = fill_price(close, "BUY", costs.slippage_bps, _tick(symbol))
        shares = affordable_shares(min(budget, cash), price, costs)
        if shares > 0:
            fee = costs.fee(shares * price)
            cash -= shares * price + fee
            units[symbol] += shares
            result.trades += 1
            result.fees += fee

    def sell_all(symbol: str) -> None:
        nonlocal cash
        close = data.closes.get(symbol, {}).get(day)
        if close is None or units[symbol] <= 0:
            return  # no trade that session (suspended): keep it
        amount = units[symbol] * fill_price(close, "SELL", costs.slippage_bps, _tick(symbol))
        fee, tax = costs.fee(amount), costs.tax(amount, _tax_kind(symbol), "SELL")
        cash += amount - fee - tax
        units[symbol] = 0.0
        result.trades += 1
        result.fees += fee
        result.taxes += tax

    for index, day in enumerate(sessions):
        for symbol in [held for held, count in units.items() if count > 0]:
            units[symbol] *= data.factors.get(symbol, {}).get(day, 1.0)
        contribution = by_day.get(day, 0.0)
        cash += contribution
        if contribution:
            pending = True
        if variant is None:
            if pending and BENCHMARK in data.closes and day in data.closes[BENCHMARK]:
                buy(BENCHMARK, cash)
                pending = False
        else:
            ranked = ranks.get(day, [])
            rebalance = variant.rebalance == "monthly" and pending or (
                variant.rebalance == "every_5" and (pending or index % 5 == 0))
            if ranked and (pending or rebalance):
                picks = picks_for(ranked, variant, day, previous, model_previous)
                previous, model_previous = picks, ranked[: variant.top]
                if variant.rebalance != "never":
                    for symbol in [held for held, count in units.items() if count > 0 and held not in picks]:
                        sell_all(symbol)
                    total = cash + sum(units[symbol] * (data.last_close(symbol, day) or 0.0) for symbol in units)
                    for symbol in picks:
                        held_value = units[symbol] * data.closes[symbol][day]
                        buy(symbol, total / len(picks) - held_value)
                else:
                    budget = cash / len(picks)
                    for symbol in picks:
                        buy(symbol, budget)
                pending = False
        value = cash + sum(count * (data.last_close(symbol, day) or 0.0) for symbol, count in units.items())
        result.values.append(value)
        result.flows.append(contribution)
    return result


# --- evaluation ---------------------------------------------------------------------------------
def _month_starts(sessions: list[date], start: date, end: date, months: int) -> list[date]:
    starts, seen = [], set()
    for day in sessions:
        key = (day.year, day.month)
        if day < start or key in seen:
            continue
        seen.add(key)
        total = day.year * 12 + day.month - 1 + months
        if date(total // 12, total % 12 + 1, 1) - timedelta(days=1) > end:
            break
        starts.append(day)
    return starts


def windows(data: LegacyData, variant: Variant, costs: CostModel, start: date, end: date, months: int,
            ranks: dict[date, list[str]], benchmarks: dict | None = None) -> dict[str, object]:
    """Rolling windows starting each month: excess = (legacy − 0050) ÷ the window's contributions.
    ``benchmarks`` caches the 0050 runs per window (the same for every variant of a cost profile)."""
    benchmarks = benchmarks if benchmarks is not None else {}
    excesses = []
    for first in _month_starts(data.sessions, start, end, months):
        total = first.year * 12 + first.month - 1 + months
        last = date(total // 12, total % 12 + 1, 1) - timedelta(days=1)
        legacy = simulate(data, variant, costs, first, last, ranks)
        if (first, last) not in benchmarks:
            benchmarks[(first, last)] = simulate(data, None, costs, first, last)
        benchmark = benchmarks[(first, last)]
        if legacy.contributed > 0:
            excesses.append((legacy.final_value - benchmark.final_value) / legacy.contributed)
    if not excesses:
        return {"count": 0}
    return {
        "count": len(excesses), "win_ratio": round(sum(item > 0 for item in excesses) / len(excesses), 4),
        "median_excess": round(statistics.median(excesses), 6), "worst_excess": round(min(excesses), 6),
        "best_excess": round(max(excesses), 6),
    }


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(order):
        end = position
        while end + 1 < len(order) and values[order[end + 1]] == values[order[position]]:
            end += 1
        for item in order[position:end + 1]:
            ranks[item] = (position + end) / 2
        position = end + 1
    return ranks


def signal_quality(data: LegacyData, ranks: dict[date, list[str]], horizon: int = 5) -> dict[str, object]:
    """Daily rank correlation between prediction and the next ``horizon`` sessions' total return,
    and how much the top five beat the eligible average before costs."""
    position = {day: index for index, day in enumerate(data.sessions)}
    correlations, spreads = [], []
    for day, ranked in ranks.items():
        if day not in position or position[day] + horizon >= len(data.sessions) or len(ranked) < 20:
            continue
        later = data.sessions[position[day] + horizon]
        window = data.sessions[position[day] + 1: position[day] + horizon + 1]
        realized, predicted = [], []
        for symbol in ranked:
            start, finish = data.closes[symbol].get(day), data.closes[symbol].get(later)
            if not start or not finish:
                continue
            growth = math.prod(data.factors.get(symbol, {}).get(item, 1.0) for item in window)
            realized.append(finish * growth / start - 1)
            predicted.append(data.predictions[day][symbol])
        if len(realized) < 20:
            continue
        a, b = _ranks(predicted), _ranks(realized)
        mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
        numerator = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
        denominator = math.sqrt(sum((x - mean_a) ** 2 for x in a) * sum((y - mean_b) ** 2 for y in b))
        if denominator:
            correlations.append(numerator / denominator)
        top = sorted(zip(predicted, realized), key=lambda item: -item[0])[:5]
        spreads.append(statistics.fmean(item[1] for item in top) - statistics.fmean(realized))
    if not correlations:
        return {"days": 0}
    deviation = statistics.pstdev(correlations)
    return {
        "days": len(correlations), "mean_rank_ic": round(statistics.fmean(correlations), 5),
        "rank_ic_t": round(statistics.fmean(correlations) / (deviation / math.sqrt(len(correlations))), 2)
        if deviation else None,
        "top5_minus_average_5d": round(statistics.fmean(spreads), 6),
    }


def _excess(run: RunResult, benchmark: RunResult) -> float:
    return (run.final_value - benchmark.final_value) / run.contributed


def evaluate(data: LegacyData, brokers: tuple[str, ...] = ("conservative", "cathay"), months: int = 36,
             variants: tuple[Variant, ...] = VARIANTS, random_seeds: int = RANDOM_SEEDS,
             random_window_seeds: int = RANDOM_WINDOW_SEEDS, early_count: int = EARLY_LARGE,
             scan_sizes: tuple[int, ...] = SCAN_SIZES) -> dict[str, object]:
    """Each variant against 0050 and against the same rule with random or worst-ranked picks: what the
    universe alone gives versus what the model adds."""
    start, end = min(data.predictions), max(data.predictions)
    ranks = rankings(data)
    results = []
    for broker in brokers:
        results.extend(_rows(data, variants, broker, ranks, start, end, months, random_seeds, random_window_seeds))
    judged = "cathay" if "cathay" in brokers else brokers[0]
    subset = early_large(data, early_count)
    subset_ranks = {day: [symbol for symbol in ranked if symbol in subset] for day, ranked in ranks.items()}
    sensitivity = {
        "name": f"只用預測開始（{start:%Y-%m}）前已是成交值前 {early_count} 名的股票", "broker": judged,
        "symbols": len(subset), "signal_quality": signal_quality(data, subset_ranks),
        "results": _rows(data, variants, judged, subset_ranks, start, end, months, random_seeds, random_window_seeds),
        "scan": size_scan(data, variants, judged, ranks, start, end, scan_sizes),
    }
    quality = signal_quality(data, ranks)
    sessions = [day for day in data.sessions if start <= day <= end]
    period = {
        "start": start.isoformat(), "end": end.isoformat(), "sessions": len(sessions),
        "years": round((end - start).days / 365.25, 2),
        # 0050 with dividends reinvested: what kind of market the period was
        "benchmark_growth": round(data.closes[BENCHMARK][sessions[-1]] / data.closes[BENCHMARK][sessions[0]] * math.prod(
            data.factors.get(BENCHMARK, {}).get(day, 1.0) for day in sessions[1:]), 4),
    }
    parts, headline = _verdict(results, variants, brokers, sensitivity, quality, period)
    return {
        "version": EVALUATION_VERSION, "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "experiment": data.notes, "period": period,
        "assumptions": {
            "contribution": "每月 5 日 10,000 元（遇休市順延）", "fill": "收盤 ± 20 bps，依跳動單位不利進位",
            "eligible": f"近 {LIQUIDITY_SESSIONS} 日日均成交值 ≥ {LIQUIDITY_TWD:,} 元、收盤 ≥ {MIN_PRICE:g} 元",
            "dividends": "官方除權息日依前收盤 ÷ 參考價（含配股時用 Yahoo 調整）增加持有單位，兩邊相同",
            "controls": "隨機對照每次換掉的檔數與模型當天相同（周轉與成本可比，只差在挑哪幾檔）；"
                        "最差對照改買預測最低的同樣檔數",
            "variants": [{"key": variant.key, "name": variant.name} for variant in variants],
        },
        "signal_quality": quality, "results": results, "sensitivity": sensitivity, "verdict": " ".join(parts),
        "verdict_parts": parts, "headline": headline,
    }


def size_scan(data: LegacyData, variants: tuple[Variant, ...], broker: str, ranks: dict[date, list[str]],
              start: date, end: date, sizes: tuple[int, ...] = SCAN_SIZES, seeds: int = SCAN_SEEDS) -> list[dict]:
    """Full-period excess over 0050 when only the ``size`` stocks most traded before the period may
    be picked: how much of a result rests on stocks that were small then and in the pool now."""
    costs = BROKERS[broker].cost_model()
    benchmark = simulate(data, None, costs, start, end)
    output = []
    for size in sizes:
        subset = early_large(data, size)
        sized = {day: [symbol for symbol in ranked if symbol in subset] for day, ranked in ranks.items()}
        row: dict[str, object] = {"size": size, "symbols": len(subset)}
        for variant in variants:
            randoms = [_excess(simulate(data, control(variant, "random", seed), costs, start, end, sized), benchmark)
                       for seed in range(1, seeds + 1)]
            row[variant.key] = {
                "model": round(_excess(simulate(data, variant, costs, start, end, sized), benchmark), 6),
                "random": round(statistics.fmean(randoms), 6),
                "worst": round(_excess(simulate(data, control(variant, "worst"), costs, start, end, sized), benchmark), 6),
            }
        output.append(row)
    return output


def _rows(data: LegacyData, variants: tuple[Variant, ...], broker: str, ranks: dict[date, list[str]], start: date,
          end: date, months: int, random_seeds: int, random_window_seeds: int) -> list[dict[str, object]]:
    costs = BROKERS[broker].cost_model()
    benchmark = simulate(data, None, costs, start, end)
    cache: dict = {}
    results: list[dict[str, object]] = [
        {"variant": "dca_0050", "name": "0050 定期定額（對照）", "broker": broker, **benchmark.summary()}]
    for variant in variants:
        run = simulate(data, variant, costs, start, end, ranks)
        excess = _excess(run, benchmark)
        random_runs = [simulate(data, control(variant, "random", seed), costs, start, end, ranks)
                       for seed in range(1, random_seeds + 1)]
        randoms = [_excess(item, benchmark) for item in random_runs]
        random_windows = [windows(data, control(variant, "random", seed), costs, start, end, months, ranks, cache)
                          for seed in range(1, random_window_seeds + 1)]
        worst = simulate(data, control(variant, "worst"), costs, start, end, ranks)
        results.append({
            "variant": variant.key, "name": variant.name, "broker": broker, **run.summary(),
            "full_excess": round(excess, 6),
            "windows_3y": windows(data, variant, costs, start, end, months, ranks, cache),
            "random_control": {
                "seeds": random_seeds, "full_excess_mean": round(statistics.fmean(randoms), 6),
                "full_excess_min": round(min(randoms), 6), "full_excess_max": round(max(randoms), 6),
                "trades_mean": round(statistics.fmean(item.trades for item in random_runs), 1),
                "costs_mean": round(statistics.fmean(item.fees + item.taxes for item in random_runs), 1),
                "max_drawdown_mean": round(statistics.fmean(
                    item.summary()["max_drawdown"] for item in random_runs), 6),
                "windows_3y_win_ratio": round(statistics.fmean(item.get("win_ratio", 0) for item in random_windows), 4),
                "windows_3y_median_excess": round(
                    statistics.fmean(item.get("median_excess", 0) for item in random_windows), 6),
            },
            "worst_control": {"full_excess": round(_excess(worst, benchmark), 6), **worst.summary()},
            "model_contribution": round(excess - statistics.fmean(randoms), 6),
        })
    return results


def _verdict(results: list[dict], variants: tuple[Variant, ...], brokers: tuple[str, ...],
             sensitivity: dict | None = None, quality: dict | None = None,
             period: dict | None = None) -> tuple[list[str], str]:
    """Plain-word paragraphs and a one-line headline, judged on the user's broker when it was run
    (else the first profile)."""
    broker = "cathay" if "cathay" in brokers else brokers[0]
    rows = {row["variant"]: row for row in results if row["broker"] == broker}
    benchmark = rows["dca_0050"]

    def amount(value: float) -> str:
        return f"{'多' if value >= 0 else '少'} {abs(value):.0%}"

    def names(keys: list[str]) -> str:
        return "、".join(f"「{rows[key]['name']}」" for key in keys)   # names contain 、 themselves

    lines, promising, tilted = [], [], []
    for variant in variants:
        row = rows[variant.key]
        random_mean, worst = row["random_control"]["full_excess_mean"], row["worst_control"]["full_excess"]
        lines.append(
            f"「{variant.name}」比 0050 定期定額{amount(row['full_excess'])}（以投入金額計，3 年視窗勝率 "
            f"{row['windows_3y'].get('win_ratio', 0):.0%}，最大回撤 {row['max_drawdown']:.0%}）；"
            f"同規則隨機選股{amount(random_mean)}、選預測最差的{amount(worst)}。"
        )
        if (row["full_excess"] > 0 and (row["windows_3y"].get("median_excess") or -1) > 0
                and row["full_excess"] > random_mean and row["full_excess"] > worst):
            promising.append(variant.key)
        if worst > random_mean:
            tilted.append(variant.key)
    parts = list(lines)
    if promising:
        only = "只有" if len(promising) < len(variants) else ""
        parts.append(f"{only}{names(promising)}同時勝過 0050、同規則隨機選股與選預測最差的。")
        headline = f"{names(promising)}勝過 0050 與兩種對照，但尚未檢查倖存者偏差。"
    else:
        parts.append("沒有一種用法同時勝過 0050、同規則隨機選股與選預測最差的，不晉級為候選"
                     "（股票池是 2026 年依市值選的，倖存者偏差通常讓結果偏好看）。")
        headline = "沒有一種用法同時勝過 0050 定期定額與兩種對照，不晉級為候選。"
    if tilted:
        parts.append(f"{names(tilted)}選預測最差的也勝過隨機選股：一部分優勢來自偏好波動大的股票。股票池只留下"
                     "活到 2026 年、市值夠大的公司，波動大的股票在其中多半是贏家，加上期間大漲，這部分會被高估，回撤也較深。")
    held = []
    if sensitivity and promising:
        subset = {row["variant"]: row for row in sensitivity["results"]}
        inner = sensitivity.get("signal_quality") or {}
        if quality and quality.get("mean_rank_ic") is not None and inner.get("mean_rank_ic") is not None:
            parts.append(f"排序本身有資訊：預測和之後 5 天報酬的排序相關，全股票池 {quality['mean_rank_ic']:.3f}"
                         f"（t={quality['rank_ic_t']}），{sensitivity['name']}也有 {inner['mean_rank_ic']:.3f}"
                         f"（t={inner['rank_ic_t']}），但這是未扣成本、5 天的平均。")
        for key in promising:
            row = subset[key]
            random_mean, worst = row["random_control"]["full_excess_mean"], row["worst_control"]["full_excess"]
            if row["full_excess"] > 0 and row["full_excess"] > random_mean and row["full_excess"] > worst:
                held.append(key)
            scan = "、".join(f"前 {item['size']} 名{amount(item[key]['model'])}" for item in sensitivity.get("scan") or [])
            parts.append(
                f"{sensitivity['name']}重算（倖存者偏差較小）時，{names([key])}比 0050 {amount(row['full_excess'])}、"
                f"同規則隨機選股{amount(random_mean)}、選預測最差的{amount(worst)}"
                + (f"；依預測開始前的成交值只取{scan}（全部：{amount(rows[key]['full_excess'])}）" if scan else "") + "。")
    if promising and sensitivity and not held:
        headline = (f"{names(promising)}看起來勝過 0050，但優勢要靠後來長大的公司（倖存者偏差）；"
                    "證據不足以晉級為候選。")
        parts.append("結論：贏 0050 的部分要靠當時還小、後來長大進入股票池的公司；股票池是 2026 年選的，這些公司是事後挑過的"
                     "贏家，所以目前的證據不足以讓舊版晉級為候選。要再確認，須用當時完整的上市櫃名單（含後來下市或變小的公司）"
                     "讓舊版模型重新預測。")
    elif held:
        headline = f"{names(held)}在倖存者偏差較小的股票池仍有加分，值得用當時完整的上市櫃名單正式評估。"
        parts.append(f"結論：{headline}")
    elif promising:
        parts.append("要成為候選，須用當時完整的上市櫃名單重算，並通過與新研究相同的關卡。")
    market = (f"期間 {period['years']:.1f} 年、0050 含息漲了 {period['benchmark_growth']:.1f} 倍，結果偏向上漲行情；"
              if period else "")
    parts.append(f"0050 定期定額的最大回撤是 {benchmark['max_drawdown']:.0%}。其他限制：{market}"
                 f"個股盤後零股成交假設偏樂觀；{len(variants)} 種用法同時比較、尚未做多重檢定與保留期。")
    return parts, headline


def save_report(report: dict[str, object], folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TAIPEI).strftime("%Y%m%d-%H%M%S")
    path = folder / f"challenger-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def challenger_view(report: dict[str, object] | None) -> dict[str, object] | None:
    """What the research page shows of a report: the judged broker's rows and the size scan."""
    if not report or not report.get("sensitivity"):
        return None
    sensitivity = report["sensitivity"]
    broker = sensitivity["broker"]
    inner = {row["variant"]: row for row in sensitivity["results"]}
    scan = sensitivity.get("scan") or []
    rows = []
    for row in report["results"]:
        if row["broker"] != broker:
            continue
        item = {"key": row["variant"], "name": row["name"], "final": row["final_value"], "xirr": row["xirr"],
                "drawdown": row["max_drawdown"], "trades": row["trades"], "costs": row["fees"] + row["taxes"],
                "excess": row.get("full_excess"), "win_3y": None, "random": None, "worst": None, "early": None,
                "scan": []}
        if "full_excess" in row:
            item.update(
                win_3y=(row.get("windows_3y") or {}).get("win_ratio"),
                random=row["random_control"]["full_excess_mean"], worst=row["worst_control"]["full_excess"],
                early=inner.get(row["variant"], {}).get("full_excess"),
                scan=[point[row["variant"]]["model"] for point in scan] + [row["full_excess"]],
            )
        rows.append(item)
    generated = datetime.fromisoformat(str(report["generated_at"])).astimezone(TAIPEI)
    profile = BROKERS.get(broker)
    return {
        "headline": report.get("headline"), "verdict": report.get("verdict_parts") or [report["verdict"]], "rows": rows,
        "scan_sizes": [point["size"] for point in scan], "quality": report.get("signal_quality") or {},
        "inner_quality": sensitivity.get("signal_quality") or {}, "sensitivity_name": sensitivity["name"],
        "period": report["period"], "experiment": report.get("experiment") or {},
        "broker": profile.name if profile else broker, "generated": generated.strftime("%Y-%m-%d %H:%M"),
        "version": report.get("version"), "controls": (report.get("assumptions") or {}).get("controls"),
    }


def latest_report(folder: Path) -> dict[str, object] | None:
    paths = sorted(Path(folder).glob("challenger-*.json"))
    if not paths:
        return None
    try:
        return json.loads(paths[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
