"""E2 (2026-10-17; 使用者：繼續; docs/research_dl_exits_review.md §4.3): a weekly exit learned as optimal stopping.

exit-1.0.0 sold whenever a one-shot forecast of "holding minus replacement over 20 sessions" fell below a round trip,
every session: with no signal at all most holdings would cross that line within a month, it ignored that the decision
can be taken again next week, and it was trained on the rule's own holdings. This version, on the rule's weekly
decision days only:

- every stock in the rule's top ``ZONE`` (60) is looked at as if held; its replacement is the best stock outside
  the top ``TOP`` (the rule's next pick);
- the value of keeping a stock is learned by fitted Q iteration (Ernst et al. 2005): one week's gain above the day's
  median, plus ``GAMMA`` × the better of keeping it next week or swapping then (a stock that leaves the zone is
  swapped); swapping into the replacement is worth the replacement's own keep value less a round trip (``COST``).
  Our odd-lot orders do not move prices, so both branches come from history — no exploration, no bias from the
  rule's own holdings; ``ITERATIONS`` regressions, a gradient-boosted model each;
- a stock is swapped when the replacement's value less the round trip beats its own by more than ``Z`` × the
  spread of that gap across ``BOOTSTRAPS`` models fitted on resampled weeks, at most ``MAX_SWAPS`` a week.

Walk-forward: year Y's models learn only weeks whose next week ended ``EMBARGO`` sessions before Y's first session.

Registered model gate (research_method §7, before the results): over 2016–2020 out of sample, the stocks it swaps
did worse than their replacements over the next 60 sessions by at least ``GATE_GAP`` (two round trips) on average,
in at least ``GATE_COUNT`` of the 5 years worse than random swaps of as many stocks, and at most ``MAX_SWAPS``
swaps a week on average. Only then is it put in the engine.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

VERSION = "exit-2.0.0"
TOP, ZONE, STEP = 20, 60, 5
GAMMA, ITERATIONS, BOOTSTRAPS, Z = 0.97, 8, 5, 1.0
COST = 0.006
MAX_SWAPS = 3
EMBARGO = 60
LATER = 60                    # the gate looks at the next 60 sessions
MIN_TRAIN = 2_000             # samples a year's models need
GATE_YEARS = (2016, 2017, 2018, 2019, 2020)
GATE_GAP = -0.012
GATE_COUNT = 4
PARAMS = {"max_iter": 150, "learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 200,
          "l2_regularization": 1.0, "random_state": 0}
FEATURES = ("rank", "score_pct", "replacement_gap", "trend_pct", "momentum_pct", "last_week_pct", "volatility_pct",
            "volatility_20", "gain_20", "fall_from_peak_60", "weeks_in_zone", "breadth", "taiex_vs_200")


def base_rule(factor: str = "ml_deep2_top"):
    """The ranking it works on: the cost-aware rules' frame (weekly, at most 3 in 10 from one industry)."""
    from quant_platform.research.daily import DailyRule

    return DailyRule(name=f"{factor} weekly", factors={factor: 1.0}, check="weekly", industry_cap=0.3, keep=3,
                     min_hold=0)


class Weeks:
    """The weekly samples: one per stock in the zone each decision week, its features, its keep reward, the index of
    its own sample next week (−1 when it left the zone), its replacement's sample this week, and the later gaps."""

    def __init__(self, fp, rule, start, end) -> None:
        from quant_platform.research.exits import Context

        context = Context(fp)
        prices = fp.panel.filled
        score = fp.matrix(next(iter(rule.factors)))
        positions = [position for position, day in enumerate(fp.sessions) if start <= day <= end][::STEP]
        positions = [position for position in positions if position + STEP < len(fp.sessions)]
        rows, weeks, keep_reward, symbols = [], [], [], []
        replacement_of_week, member, streak = [], [], {}
        gap_later = []
        for week, position in enumerate(positions):
            ranked = fp.ranked(rule, position)
            zone = ranked[:ZONE]
            if len(ranked) <= TOP:
                replacement_of_week.append(-1)
                member.append({})
                continue
            later = positions[week + 1] if week + 1 < len(positions) else position + STEP
            with np.errstate(invalid="ignore", divide="ignore"):
                moves = prices[:, later] / prices[:, position] - 1
                median = float(np.nanmedian([moves[fp.row[symbol]] for symbol in ranked]))
                far = min(position + LATER, prices.shape[1] - 1)
                ahead = prices[:, far] / prices[:, position] - 1 if position + LATER < prices.shape[1] else None
            known = np.array([score[fp.row[symbol], position] for symbol in ranked], dtype=float)
            order = np.argsort(np.argsort(known))
            pct = {symbol: (order[index] + 0.5) / len(ranked) for index, symbol in enumerate(ranked)}
            replacement = ranked[TOP]
            streak = {symbol: streak.get(symbol, 0) + 1 for symbol in zone}
            here = {}
            for place, symbol in enumerate(zone):
                row = fp.row[symbol]
                price = prices[row, position]
                window = prices[row, max(0, position - 60): position + 1]
                feature = (
                    place / ZONE, pct[symbol], pct[replacement] - pct[symbol],
                    context.percentiles["trend_200"][row, position], context.percentiles["momentum_3"][row, position],
                    context.percentiles["reversal_5d"][row, position], context.percentiles["low_volatility_60"][row, position],
                    context.volatility[row, position],
                    price / prices[row, max(0, position - 20)] - 1 if position >= 20 else np.nan,
                    price / np.nanmax(window) - 1 if np.isfinite(window).any() else np.nan,
                    streak[symbol], context.breadth[position], context.taiex[position])
                here[symbol] = len(rows)
                rows.append(feature)
                weeks.append(week)
                symbols.append(symbol)
                keep_reward.append(float(np.nan_to_num(moves[row] - median)))
                gap_later.append(float(ahead[row] - ahead[fp.row[replacement]]) if ahead is not None else np.nan)
            replacement_of_week.append(here[replacement])
            member.append(here)
        self.positions = positions
        self.years = np.array([fp.sessions[positions[week]].year for week in weeks])
        self.x = np.nan_to_num(np.array(rows, dtype=np.float32), nan=0.0)
        self.week = np.array(weeks)
        self.symbol = symbols
        self.keep_reward = np.array(keep_reward, dtype=np.float64)
        self.gap_later = np.array(gap_later, dtype=np.float64)
        self.replacement = np.array(replacement_of_week)          # by week: the replacement's sample, −1 none
        nxt = np.full(len(rows), -1)
        for index, (week, symbol) in enumerate(zip(weeks, symbols, strict=True)):
            if week + 1 < len(member):
                nxt[index] = member[week + 1].get(symbol, -1)
        self.next = nxt


def fitted_q(weeks: Weeks, chosen: np.ndarray, seed: int = 0):
    """ITERATIONS rounds of fitted Q on the samples in ``chosen`` (a boolean mask); returns the last model."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    q = np.zeros(len(weeks.x))
    model = None
    last_week = weeks.week[chosen].max() if chosen.any() else -1
    for _round in range(ITERATIONS):
        swap_value = np.full(len(weeks.replacement) + 1, 0.0)
        for week, index in enumerate(weeks.replacement):
            swap_value[week] = q[index] - COST if index >= 0 else 0.0
        later_week = np.minimum(weeks.week + 1, len(weeks.replacement))
        stay = np.where(weeks.next >= 0, q[np.maximum(weeks.next, 0)], -np.inf)
        value_next = np.maximum(stay, swap_value[later_week])
        value_next = np.where(weeks.week >= last_week, 0.0, value_next)      # the end of the training weeks
        target = weeks.keep_reward + GAMMA * value_next
        model = HistGradientBoostingRegressor(**{**PARAMS, "random_state": seed})
        model.fit(weeks.x[chosen], target[chosen])
        q = model.predict(weeks.x)
    return model


def decide(weeks: Weeks, models: list, test: np.ndarray) -> np.ndarray:
    """Boolean per sample: swapped (in the test weeks) — the replacement's value less the round trip beats the stock's
    by more than Z × the models' spread of that gap; at most MAX_SWAPS a week, the largest gaps first."""
    values = np.array([model.predict(weeks.x) for model in models])         # model × sample
    swapped = np.zeros(len(weeks.x), dtype=bool)
    for week in np.unique(weeks.week[test]):
        index = weeks.replacement[week]
        if index < 0:
            continue
        rows = np.flatnonzero(test & (weeks.week == week))
        rows = rows[rows != index]
        gaps = values[:, index][:, None] - COST - values[:, rows]            # model × candidate
        mean, spread = gaps.mean(axis=0), gaps.std(axis=0)
        wanted = np.flatnonzero(mean > Z * spread)
        wanted = wanted[np.argsort(-mean[wanted])][:MAX_SWAPS]
        swapped[rows[wanted]] = True
    return swapped


def gate_check(diagnostics: dict) -> dict[str, object]:
    years = [diagnostics.get(str(year)) or {} for year in GATE_YEARS]
    gaps = [item["swapped_gap"] for item in years if item.get("swapped_gap") is not None]
    beat = sum(1 for item in years if item.get("swapped_gap") is not None and item.get("random_gap") is not None
               and item["swapped_gap"] < item["random_gap"])
    swaps = [item["swaps_per_week"] for item in years if item.get("swaps_per_week") is not None]
    gap = float(np.mean(gaps)) if len(gaps) == len(GATE_YEARS) else None
    return {"version": VERSION, "years": list(GATE_YEARS), "swapped_gap": gap, "years_worse_than_random": beat,
            "swaps_per_week": float(np.mean(swaps)) if swaps else None,
            "passed": gap is not None and gap <= GATE_GAP and beat >= GATE_COUNT
            and float(np.mean(swaps)) <= MAX_SWAPS}


def train(fp, out_dir: str | Path, factor: str = "ml_deep2_top", years: list[int] | None = None, job=None) -> dict:
    """The models of each year (walk-forward, BOOTSTRAPS each) and the model gate's diagnostics."""
    import pickle

    from quant_platform.research.daily import RECENT_START

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rule = base_rule(factor)
    weeks = Weeks(fp, rule, RECENT_START, fp.sessions[-1])
    years = years or list(range(GATE_YEARS[0], fp.sessions[-1].year + 1))
    rng = np.random.default_rng(0)
    diagnostics = {}
    for number, year in enumerate(years):
        first = next((index for index, day in enumerate(fp.sessions) if day.year >= year), len(fp.sessions))
        week_end = np.array([weeks.positions[week] + STEP for week in weeks.week])
        usable = week_end + EMBARGO < first
        if usable.sum() < MIN_TRAIN:
            continue
        train_weeks = np.unique(weeks.week[usable])
        models = []
        for seed in range(BOOTSTRAPS):
            drawn = set(rng.choice(train_weeks, size=len(train_weeks), replace=True).tolist())
            models.append(fitted_q(weeks, usable & np.isin(weeks.week, list(drawn)), seed))
        (out / f"{year}.pkl").write_bytes(pickle.dumps(models, protocol=5))
        test = weeks.years == year
        if not test.any():
            continue
        swapped = decide(weeks, models, test)
        later = np.isfinite(weeks.gap_later)
        chosen = swapped & later
        week_count = len(np.unique(weeks.week[test]))
        random_gaps = []
        for draw in range(20):                 # random swaps of as many stocks in the same weeks
            picks = []
            for week in np.unique(weeks.week[chosen]):
                rows = np.flatnonzero(test & later & (weeks.week == week))
                count = int((chosen & (weeks.week == week)).sum())
                picks.extend(np.random.default_rng(draw * 10_000 + week).choice(rows, size=min(count, rows.size),
                                                                                   replace=False).tolist())
            if picks:
                random_gaps.append(float(weeks.gap_later[picks].mean()))
        diagnostics[str(year)] = {
            "train_samples": int(usable.sum()), "test_samples": int(test.sum()),
            "swaps_per_week": round(float(swapped.sum() / max(week_count, 1)), 3),
            "swapped_gap": round(float(weeks.gap_later[chosen].mean()), 5) if chosen.any() else None,
            "random_gap": round(float(np.mean(random_gaps)), 5) if random_gaps else None,
            "all_gap": round(float(np.nanmean(weeks.gap_later[test])), 5)}
        if job:
            item = diagnostics[str(year)]
            job.update(done=number + 1, total=len(years),
                       current=f"{year}：每週換 {item['swaps_per_week']} 檔、換掉的比替補 {item['swapped_gap']}（隨機 {item['random_gap']}）")
    meta = {"version": VERSION, "factor": factor, "base_rule": rule.canonical(), "features": list(FEATURES),
            "gamma": GAMMA, "iterations": ITERATIONS, "bootstraps": BOOTSTRAPS, "z": Z, "cost": COST,
            "max_swaps": MAX_SWAPS, "params": PARAMS, "years": diagnostics, "gate": gate_check(diagnostics),
            "trained_at": datetime.now(UTC).isoformat(timespec="seconds")}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta
