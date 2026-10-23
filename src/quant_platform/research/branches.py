"""2026-10-23 (research_method §7「分點」, written before the run; 使用者 10-22：還有什麼要加入分析的): which broker
branches are worth following, from FinMind's per-branch daily reports (history/finmind_daily.py: from 2021-06-30, the
listed stocks among the 300 largest by turnover in any year from 2021).

Two kinds, chosen again every month from what was known before it:

- informed (跟著買): a branch whose large net trades in a stock (at least EVENT_SHARE of the stock's money that day) were
  followed by the stock beating the average eligible listed stock over the next HORIZON sessions — from the close of
  the session after (the report comes out in the evening) — net selling before a fall counting the same way; at least
  MIN_EVENTS such trades with the outcome known, t of at least T_MIN, the MAX_INFORMED strongest, flippers left out.
- next-day flippers (隔日沖): a branch whose large net buying is sold back the next session — the next session's net
  selling (up to the buying) over the buying, at least FLIP_FLOOR over at least FLIP_DAYS such days, the FLIP_TOP
  highest.

A trade counts for a month's choice once its outcome is known before the month began (the informed: the close HORIZON
+ 1 sessions on; the flippers: the next session). The months before FIRST_USE only choose.

Written per stock and session (history/paid/BranchSignals.parquet, read like the other paid tables by
research/chips.py, a day's row used from the next session): the informed branches' net buying, the flippers' net buying
and the day's money traded (every trade has a buying branch). A report of the choices, how the chosen informed
branches did in the months after (out of sample) and the stocks after heavy flipper buying goes to
research/diagnostics/branches-*.json (described, not the verdict: the factors' strength is)."""

from __future__ import annotations

import gzip
import json
import math
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
SIGNALS = "BranchSignals"
EVENT_SHARE = 0.005
HORIZON = 20
MIN_EVENTS = 200
T_MIN = 3.0
MAX_INFORMED = 50
FLIP_FLOOR = 0.5
FLIP_DAYS = 100
FLIP_TOP = 30
FIRST_USE = date(2022, 7, 1)            # 2021-07～2022-06 only choose
FLIP_SHARE = 0.02                       # the report: the flippers' net buying at least this share of the day
PERIODS = {"2022-07～2023-12": (date(2022, 7, 1), date(2023, 12, 31)),
           "2024-01～2026-08": (date(2024, 1, 1), date(2026, 8, 31))}


def raw_folder(base: str | Path) -> Path:
    return Path(base) / "raw" / "finmind" / "branches"


def read_stock(folder: Path, index: dict[date, int], names: dict[str, int]):
    """One stock's day files: (columns, branch ids, money bought, money sold) — the session columns it has (ascending),
    the global id of each branch that traded it (``names`` grows), columns × branches."""
    days, rows = [], []
    for path in sorted(folder.glob("*.json.gz")):
        try:
            day = date.fromisoformat(path.name[:10])
        except ValueError:
            continue
        if day not in index:
            continue
        payload = json.loads(gzip.decompress(path.read_bytes()))
        days.append(index[day])
        rows.append(payload.get("rows") or [])
    local: dict[str, int] = {}
    for day_rows in rows:
        for row in day_rows:
            local.setdefault(str(row[0]), len(local))
    bought = np.zeros((len(days), len(local)))
    sold = np.zeros((len(days), len(local)))
    for number, day_rows in enumerate(rows):
        if day_rows:
            at = np.fromiter((local[str(row[0])] for row in day_rows), dtype=np.int64, count=len(day_rows))
            bought[number, at] = [float(row[3]) for row in day_rows]
            sold[number, at] = [float(row[4]) for row in day_rows]
    for name in local:
        names.setdefault(name, len(names))
    ids = np.array([names[name] for name in local], dtype=np.int64)
    order = np.argsort(np.array(days, dtype=np.int64), kind="stable")
    return np.array(days, dtype=np.int64)[order], ids, bought[order], sold[order]


def outcomes(close: np.ndarray, eligible, columns: range, horizon: int = HORIZON) -> dict[str, np.ndarray]:
    """symbols × sessions: ``excess`` — from the close of the session after to ``horizon`` sessions later, less the
    average eligible stock's; ``next1`` — the next session, less the average; ``next5`` — from the session after over
    5 sessions, less the average. ``eligible(column)``: the eligible symbols (bool) at a column."""
    symbols, sessions = close.shape

    def ahead(span: int) -> np.ndarray:
        out = np.full(close.shape, np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            out[:, :sessions - span] = close[:, span:] / close[:, :sessions - span] - 1
        return out

    masks = {column: eligible(column) for column in columns if column < sessions}

    def less_average(values: np.ndarray) -> np.ndarray:
        out = np.full(values.shape, np.nan)
        for column, allowed in masks.items():
            mask = allowed & np.isfinite(values[:, column])
            if mask.any():
                out[:, column] = values[:, column] - values[mask, column].mean()
        return out

    later, one, five = less_average(ahead(horizon)), less_average(ahead(1)), less_average(ahead(5))
    shifted = lambda values: np.concatenate([values[:, 1:], np.full((symbols, 1), np.nan)], axis=1)   # noqa: E731
    return {"excess": shifted(later), "next1": one, "next5": shifted(five)}


class Tally:
    """Sums per (month, branch), the columns growing with the branches seen."""

    KEYS = ("n", "s", "q", "flip_buy", "flip_back", "flip_days")

    def __init__(self, months: int) -> None:
        self.months, self.width = months, 1024
        self.data = {key: np.zeros((months, self.width)) for key in self.KEYS}

    def add(self, key: str, month_rows: np.ndarray, ids: np.ndarray, values: np.ndarray) -> None:
        """Adds ``values`` (rows × branches) to the months ``month_rows`` (one per row, ascending) and the ``ids``."""
        if ids.size and ids.max() >= self.width:
            grow = max(self.width * 2, int(ids.max()) + 1)
            for name in self.KEYS:
                wider = np.zeros((self.months, grow))
                wider[:, :self.width] = self.data[name]
                self.data[name] = wider
            self.width = grow
        if not len(month_rows):
            return
        starts = np.flatnonzero(np.r_[True, month_rows[1:] != month_rows[:-1]])
        sums = np.add.reduceat(values, starts, axis=0)
        for start, total in zip(starts, sums):
            self.data[key][month_rows[start], ids] += total


def choose(tally: Tally, branches: int) -> tuple[np.ndarray, np.ndarray, list[dict[str, object]]]:
    """For each month: (informed, flippers) as months × branches booleans from the months before it, and the detail."""
    width = branches
    cumulative = {key: np.vstack([np.zeros((1, width)), np.cumsum(value[:, :width], axis=0)[:-1]])
                  for key, value in tally.data.items()}
    informed = np.zeros((tally.months, width), dtype=bool)
    flippers = np.zeros((tally.months, width), dtype=bool)
    detail = []
    for month in range(tally.months):
        n, s, q = cumulative["n"][month], cumulative["s"][month], cumulative["q"][month]
        bought, back, days = cumulative["flip_buy"][month], cumulative["flip_back"][month], cumulative["flip_days"][month]
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = np.where(bought > 0, back / bought, 0.0)
            mean = np.where(n > 0, s / n, 0.0)
            variance = np.where(n > 1, (q - n * mean ** 2) / (n - 1), np.nan)
            t = np.where((n >= MIN_EVENTS) & (variance > 0), mean / np.sqrt(variance / n), np.nan)
        flip_ok = (days >= FLIP_DAYS) & (ratio >= FLIP_FLOOR)
        for at in np.argsort(-np.where(flip_ok, ratio, -1.0))[:FLIP_TOP]:
            if flip_ok[at]:
                flippers[month, at] = True
        good = np.isfinite(t) & (t >= T_MIN) & ~flippers[month]
        for at in np.argsort(-np.where(good, t, -np.inf))[:MAX_INFORMED]:
            if good[at]:
                informed[month, at] = True
        detail.append({"informed": int(informed[month].sum()), "flippers": int(flippers[month].sum()),
                       "top_informed": [(int(at), round(float(t[at]), 2), int(n[at]), round(float(mean[at]), 4))
                                        for at in np.argsort(-np.where(informed[month], t, -np.inf))[:5]
                                        if informed[month, at]],
                       "top_flippers": [(int(at), round(float(ratio[at]), 3), int(days[at]))
                                        for at in np.argsort(-np.where(flippers[month], ratio, -1.0))[:5]
                                        if flippers[month, at]]})
    return informed, flippers, detail


def _stats(values: list[float] | tuple[float, float, float]) -> dict[str, object]:
    count, total, squares = values
    if count < 2:
        return {"events": int(count), "mean": None, "t": None}
    mean = total / count
    variance = (squares - count * mean ** 2) / (count - 1)
    return {"events": int(count), "mean": round(mean, 5),
            "t": round(mean / math.sqrt(variance / count), 2) if variance > 0 else None}


def run(history: str | Path, research: str | Path, job=None) -> Path:
    import pandas as pd

    from quant_platform.research import daily

    history, research = Path(history), Path(research)
    _data, fp = daily.load(history)
    sessions = list(fp.sessions)
    index = {day: column for column, day in enumerate(sessions)}
    rows_of = {}
    for row, symbol in enumerate(fp.symbols):
        code, _dot, market = str(symbol).partition(".")
        if market == "TW":
            rows_of.setdefault(code, row)
    first_day = date(2021, 6, 30)
    first = next(column for column, day in enumerate(sessions) if day >= first_day)
    base = daily.DailyRule(name="資格", factors={"trend_200": 1.0})
    found = outcomes(np.asarray(fp.panel.filled, dtype=float), lambda column: fp.eligible(base, column),
                     range(first, len(sessions)))
    start_key = sessions[first].year * 12 + sessions[first].month - 1
    keys = np.array([day.year * 12 + day.month - 1 - start_key for day in sessions])
    months = int(keys[-1]) + 2
    first_use = FIRST_USE.year * 12 + FIRST_USE.month - 1 - start_key
    tally = Tally(months)
    names: dict[str, int] = {}
    folder = raw_folder(history)
    codes = sorted(path.name for path in folder.iterdir() if path.is_dir() and path.name in rows_of) if folder.is_dir() else []
    last = len(sessions) - 1
    for number, code in enumerate(codes):                       # first pass: what each branch's trades were worth
        if job and number % 10 == 0:
            job.update(done=number, total=2 * len(codes), current=f"挑分點 {code}")
        columns, ids, bought, sold = read_stock(folder / code, index, names)
        if not len(columns):
            continue
        row = rows_of[code]
        net = bought - sold
        total = bought.sum(axis=1)
        big = (np.abs(net) >= EVENT_SHARE * total[:, None]) & (total[:, None] > 0)
        later = found["excess"][row, columns]
        known = np.isfinite(later)
        sign = np.sign(net) * (big & known[:, None])
        product = sign * np.where(known, later, 0.0)[:, None]
        when = keys[np.minimum(columns + HORIZON + 1, last)]
        tally.add("n", when, ids, np.abs(sign))
        tally.add("s", when, ids, product)
        tally.add("q", when, ids, product ** 2)
        follows = np.zeros(len(columns), dtype=bool)
        follows[:-1] = columns[1:] == columns[:-1] + 1
        buying = np.where(big & (net > 0) & follows[:, None], net, 0.0)
        after = np.vstack([np.maximum(-net[1:], 0.0), np.zeros((1, net.shape[1]))])
        tally.add("flip_buy", keys[np.minimum(columns + 1, last)], ids, buying)
        tally.add("flip_back", keys[np.minimum(columns + 1, last)], ids, np.minimum(buying, after))
        tally.add("flip_days", keys[np.minimum(columns + 1, last)], ids, (buying > 0).astype(float))
    informed, flippers, detail = choose(tally, len(names))
    period_of = {}
    for name, (start, end) in PERIODS.items():
        for column, day in enumerate(sessions):
            if start <= day <= end:
                period_of[column] = name
    persist = {name: {"chosen": [0.0, 0.0, 0.0], "others": [0.0, 0.0, 0.0]} for name in PERIODS}
    flips = {name: {"next1": [0.0, 0.0, 0.0], "next5": [0.0, 0.0, 0.0]} for name in PERIODS}
    out_rows = []
    for number, code in enumerate(codes):                       # second pass: the signals, the report
        if job and number % 10 == 0:
            job.update(done=len(codes) + number, total=2 * len(codes), current=f"分點訊號 {code}")
        columns, ids, bought, sold = read_stock(folder / code, index, names)
        if not len(columns):
            continue
        row = rows_of[code]
        month = keys[columns]
        use = month >= first_use
        if not use.any():
            continue
        columns, bought, sold, month = columns[use], bought[use], sold[use], month[use]
        net = bought - sold
        total = bought.sum(axis=1)
        seen = ids < informed.shape[1]                # a branch first seen after the choice: never chosen
        chosen = np.zeros(net.shape, dtype=bool)
        flipping = np.zeros(net.shape, dtype=bool)
        chosen[:, seen] = informed[month][:, ids[seen]]
        flipping[:, seen] = flippers[month][:, ids[seen]]
        informed_net = (net * chosen).sum(axis=1)
        flip_net = (np.maximum(net, 0.0) * flipping).sum(axis=1)
        for column, value, flip, money in zip(columns, informed_net, flip_net, total):
            out_rows.append((sessions[column], code, float(value), float(flip), float(money)))
        big = (np.abs(net) >= EVENT_SHARE * total[:, None]) & (total[:, None] > 0)
        later = found["excess"][row, columns]
        for position, column in enumerate(columns):
            period = period_of.get(int(column))
            if period is None:
                continue
            if np.isfinite(later[position]):
                product = np.sign(net[position]) * later[position]
                for side, mask in (("chosen", big[position] & chosen[position]), ("others", big[position] & ~chosen[position])):
                    values = product[mask]
                    tally_row = persist[period][side]
                    tally_row[0] += values.size
                    tally_row[1] += float(values.sum())
                    tally_row[2] += float((values ** 2).sum())
            if total[position] > 0 and flip_net[position] >= FLIP_SHARE * total[position]:
                for key in ("next1", "next5"):
                    value = found[key][row, column]
                    if np.isfinite(value):
                        flips[period][key][0] += 1
                        flips[period][key][1] += float(value)
                        flips[period][key][2] += float(value) ** 2
    frame = pd.DataFrame(out_rows, columns=["date", "code", "informed_net", "flip_net", "total"])
    target = history / "paid" / f"{SIGNALS}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    frame.to_parquet(partial, index=False)
    partial.replace(target)
    ids_named = {number: name for name, number in names.items()}
    month_names = {offset: f"{(start_key + offset) // 12}-{(start_key + offset) % 12 + 1:02d}" for offset in range(months)}
    report = {
        "generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
        "parameters": {"event_share": EVENT_SHARE, "horizon": HORIZON, "min_events": MIN_EVENTS, "t_min": T_MIN,
                       "max_informed": MAX_INFORMED, "flip_floor": FLIP_FLOOR, "flip_days": FLIP_DAYS,
                       "flip_top": FLIP_TOP, "first_use": FIRST_USE.isoformat(), "flip_share": FLIP_SHARE},
        "stocks": len(codes), "branches": len(names), "signal_rows": len(frame),
        "months": [{"month": month_names[offset],
                    **{key: value for key, value in item.items() if not key.startswith("top_")},
                    "top_informed": [(ids_named[at], *rest) for at, *rest in item["top_informed"]],
                    "top_flippers": [(ids_named[at], *rest) for at, *rest in item["top_flippers"]]}
                   for offset, item in enumerate(detail) if offset >= first_use],
        "persistence": {name: {side: _stats(values) for side, values in sides.items()} for name, sides in persist.items()},
        "after_flipper_buying": {name: {key: _stats(values) for key, values in sides.items()} for name, sides in flips.items()},
    }
    folder_out = research / "diagnostics"
    folder_out.mkdir(parents=True, exist_ok=True)
    path = folder_out / f"branches-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if job:
        job.update(done=2 * len(codes), total=2 * len(codes), force=True)
    return path
