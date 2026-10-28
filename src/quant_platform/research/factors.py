"""Factor strength: which factors predict next month's stock returns, and in which years (2026-10-04).

The owner asked for many different factors and to find out which are strong and which are weak,
instead of many rules that differ by a parameter. On every rank day (the first session on or after
the 5th of each month) every eligible listed stock (the same eligibility as the rules: close >= 10,
20-session average turnover >= NT$20 million, listed >= 252 sessions) is ranked by each factor, and
the ranks are compared with the return to the next rank day (adjusted for dividends and splits; a
stock that stops trading keeps its last price):

- rank IC: the rank correlation between factor and next-month return, averaged over months, with a
  t-statistic (mean ÷ standard error) and the share of months it was positive;
- top minus bottom: the average next-month return of the top fifth minus the bottom fifth (gross of
  costs), shown per year;
- top minus all: the top fifth against the average eligible stock.

Reported per period: development, validation, final validation, before / after 2020 and from 2024
(the TSMC-led market). This is description, not a rule trial: nothing is registered, but every
period has now been seen, so rules built from it can only be judged honestly by forward observation.
"""

from __future__ import annotations

import json
import math
import statistics
import warnings
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from quant_platform.research.stock_rules import FACTORS, STANDARD_PLAN, Panel, StockRule

TAIPEI = ZoneInfo("Asia/Taipei")
PERIODS = {
    "development": ("開發期 2005–2016", date(2005, 1, 1), date(2016, 12, 31)),
    "validation": ("驗證期 2017–2021", date(2017, 1, 1), date(2021, 12, 31)),
    "final": ("最終驗證期 2022–2026/9", date(2022, 1, 1), date(2026, 9, 30)),
    "before_2020": ("2020 年前", date(2005, 1, 1), date(2019, 12, 31)),
    "after_2020": ("2020 年後", date(2020, 1, 1), date(2026, 9, 30)),
    "since_2024": ("2024 年後（台積電獨漲）", date(2024, 1, 1), date(2026, 9, 30)),
}
STRONG_T = 2.0


def _ranks(values: np.ndarray) -> np.ndarray:
    """Ranks 1..n, equal values sharing their average (2026-10-15: they were ranked by stock code)."""
    return pd.Series(values).rank(method="average").to_numpy()


def _fifths(s: np.ndarray, r: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The returns of the top and the bottom fifth by score; a group of equal scores is in only when its shared
    rank is (2026-10-15: a fifth cut through equal scores by stock code). Either may be empty."""
    ranks = _ranks(s)
    fifth = max(1, len(s) // 5)
    return r[ranks > len(s) - fifth + 0.5], r[ranks < fifth + 0.5]


def monthly_factor_table(panel: Panel, factors: list[str], start: date, end: date,
                         benchmark: dict[date, float] | None = None) -> list[dict[str, object]]:
    """One row per rank day and factor: IC, top-minus-bottom and top-minus-all of next month's return,
    and the top fifth against 0050 (``benchmark``: 0050's adjusted close per session)."""
    eligibility = StockRule(name="資格", factor="high_52w")
    sessions = [day for day in panel.sessions if start <= day <= end]
    days = [day for day, _amount in STANDARD_PLAN.schedule(sessions, start, end)]
    rows = []
    for current, following in pairwise(days):
        position, later = panel.index[current], panel.index[following]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            turnover = np.nanmean(panel.turnover[:, max(0, position - 19):position + 1], axis=1)
            eligible = ((panel.close[:, position] >= eligibility.min_price) & (turnover >= eligibility.min_turnover)
                        & (position - panel.first >= eligibility.min_history))
            forward = panel.filled[:, later] / panel.filled[:, position] - 1
            index_return = (benchmark[following] / benchmark[current] - 1
                            if benchmark and current in benchmark and following in benchmark else None)
            for factor in factors:
                score = panel._score(factor, position)
                mask = eligible & np.isfinite(score) & np.isfinite(forward)
                count = int(mask.sum())
                if count < 50:
                    continue
                s, r = score[mask], forward[mask]
                with np.errstate(invalid="ignore", divide="ignore"):
                    ic = float(np.corrcoef(_ranks(s), _ranks(r))[0, 1])
                if not np.isfinite(ic):
                    continue                # every score equal that day (shared ranks): nothing to rank
                top, bottom = _fifths(s, r)
                rows.append({"date": current.isoformat(), "factor": factor, "n": count, "ic": ic,
                             "spread": float(top.mean() - bottom.mean()) if len(top) and len(bottom) else None,
                             "top_excess": float(top.mean() - r.mean()) if len(top) else None,
                             "top_vs_0050": None if index_return is None or not len(top) else float(top.mean() - index_return)})
    return rows


def summarize(rows: list[dict[str, object]], factors: list[str]) -> dict[str, object]:
    """Per factor and period: mean IC, t, share of positive months, top-minus-bottom a year, verdict."""
    output: dict[str, object] = {}
    for factor in factors:
        mine = [row for row in rows if row["factor"] == factor]
        periods = {}
        for key, (label, start, end) in PERIODS.items():
            chosen = [row for row in mine if start.isoformat() <= row["date"] <= end.isoformat()]
            if len(chosen) < 6:
                continue
            ics = [row["ic"] for row in chosen]
            mean = statistics.fmean(ics)
            deviation = statistics.stdev(ics) if len(ics) > 1 else 0.0
            spreads = [row["spread"] for row in chosen if row["spread"] is not None]
            tops = [row["top_excess"] for row in chosen if row["top_excess"] is not None]
            versus = [row["top_vs_0050"] for row in chosen if row.get("top_vs_0050") is not None]
            periods[key] = {
                "label": label, "months": len(chosen), "ic": round(mean, 4),
                "t": round(mean / (deviation / math.sqrt(len(ics))), 2) if deviation else None,
                "positive": round(sum(value > 0 for value in ics) / len(ics), 3),
                "spread_year": round(statistics.fmean(spreads) * 12, 4) if spreads else None,
                "top_excess_year": round(statistics.fmean(tops) * 12, 4) if tops else None,
                "top_vs_0050_year": round(statistics.fmean(versus) * 12, 4) if versus else None,
            }
        by_year: dict[str, list[float]] = {}
        for row in mine:
            if row["spread"] is not None:
                by_year.setdefault(row["date"][:4], []).append(row["spread"])
        output[factor] = {"label": FACTORS[factor], "periods": periods, "verdict": verdict(periods),
                          "spread_by_year": {year: round(sum(values), 4) for year, values in sorted(by_year.items())}}
    return output


EDGE = 0.02   # 2 percentage points a year


def verdict(periods: dict[str, dict[str, object]]) -> str:
    """Judged on what a long-only rule buys, the top fifth against the average eligible stock, in the
    three periods: 強 = at least 2 points a year better in all three; 反向 = at least 2 points worse in
    all three (the bottom is where to look); 不穩 = better in some and worse in others; 弱 = otherwise."""
    tops = [periods.get(key, {}).get("top_excess_year") for key in ("development", "validation", "final")]
    if any(value is None for value in tops):
        return "弱"
    if all(value >= EDGE for value in tops):
        return "強"
    if all(value <= -EDGE for value in tops):
        return "反向"
    if any(value >= EDGE for value in tops) and any(value <= -EDGE for value in tops):
        return "不穩"
    return "弱"


def run(history: str | Path, out_dir: str | Path, job=None, factors: list[str] | None = None,
        first_year: int = 2003, last_year: int | None = None) -> Path:
    from quant_platform.research.stock_rules import load_stock_data

    factors = factors or list(FACTORS)
    last_year = last_year or datetime.now(TAIPEI).year
    if job:
        job.update(current="載入 2003 年起全市場行情", force=True)
    data = load_stock_data(history, first_year, last_year)
    panel = Panel(data)
    from quant_platform.research.legacy_challenger import BENCHMARK

    growth, benchmark = 1.0, {}
    for day in sorted(data.closes.get(BENCHMARK, {})):
        growth *= data.factors.get(BENCHMARK, {}).get(day, 1.0)
        benchmark[day] = data.closes[BENCHMARK][day] * growth
    rows = []
    years = list(range(2005, last_year + 1))
    if job:
        job.update(total=len(years), done=0, force=True)
    for index, year in enumerate(years):
        if job:
            job.update(done=index, current=f"{year} 年")
        rows += monthly_factor_table(panel, factors, date(year, 1, 1), date(year + 1, 1, 10), benchmark)
    # the year boundaries overlap by one rank day; keep the first copy of each (date, factor)
    seen, unique = set(), []
    for row in rows:
        key = (row["date"], row["factor"])
        if key not in seen:
            seen.add(key)
            unique.append(row)
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
              "universe": "證交所上市普通股（含後來下市），資格同個股規則", "factors": summarize(unique, factors),
              "periods": {key: label for key, (label, _s, _e) in PERIODS.items()}, "months": len({r["date"] for r in unique})}
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"strength-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if job:
        job.update(done=len(years), force=True)
        job.payload["summary"] = f"{len(factors)} 個因子、{report['months']} 個月"
    return path


def latest(out_dir: str | Path) -> dict[str, object] | None:
    folder = Path(out_dir)
    files = sorted(folder.glob("strength-*.json")) if folder.is_dir() else []
    for path in reversed(files):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


VERDICT_ORDER = ("強", "不穩", "弱", "反向")


def table(report: dict[str, object] | None) -> list[dict[str, object]]:
    """Rows for the website: strong factors first, then by development t."""
    if not report:
        return []
    rows = []
    keys = [key for key, _label in columns(report)]
    recent = report.get("design") == "recent"
    for key, item in (report.get("factors") or {}).items():
        periods = item.get("periods") or {}
        cells = [periods.get(column) for column in keys]
        rows.append({"factor": key, "label": item.get("label", key),
                     "verdict": recent_verdict(periods) if recent else verdict(periods), "cells": cells,
                     "ic": cells[0], "by_year": item.get("spread_by_year") or {}})
    rows.sort(key=lambda row: (VERDICT_ORDER.index(row["verdict"]) if row["verdict"] in VERDICT_ORDER else 9,
                               -sum(((cell or {}).get("top_excess_year") or 0) for cell in row["cells"][1:3])))
    return rows


# --- the recent market, daily data (roadmap S9-W02, owner's decision 2026-10-04) ------------------
RECENT_COLUMNS = (
    ("recent_all", "2015-06 起", date(2015, 6, 1), date(2026, 9, 30)),
    ("before_regime", "2015-06～2020-10", date(2015, 6, 1), date(2020, 10, 25)),
    ("regime", "2020-10 起", date(2020, 10, 26), date(2026, 9, 30)),
    ("since_2024", "2024 起", date(2024, 1, 1), date(2026, 9, 30)),
)
HORIZON = 20     # sessions ahead
STEP = 5         # a ranking every week


# 2026-10-22 (八大行庫, written before the run): a factor known only after 2020-10 (no 2015～2020 column) is judged on
# two periods that do not overlap — 2020-10～2023 and 2024 on — the same edge; its verdict says 短歷史, and only its
# own batches use it (a 強 for the whole history stays the 2015～2020 and 2020-10 test)
SHORT_FIRST = ("short_first", "2020-10～2023", date(2020, 10, 26), date(2023, 12, 31))


def short_verdict(periods: dict[str, dict[str, object]]) -> str:
    tops = [periods.get(key, {}).get("top_excess_year") for key in ("short_first", "since_2024")]
    if any(value is None for value in tops):
        return "弱（短歷史）"
    if all(value >= EDGE for value in tops):
        return "強（短歷史）"
    if all(value <= -EDGE for value in tops):
        return "反向（短歷史）"
    if any(value >= EDGE for value in tops) and any(value <= -EDGE for value in tops):
        return "不穩（短歷史）"
    return "弱（短歷史）"


def recent_verdict(periods: dict[str, dict[str, object]]) -> str:
    """The top fifth against the average stock before and after the 2020-10 rule changes."""
    tops = [periods.get(key, {}).get("top_excess_year") for key in ("before_regime", "regime")]
    if any(value is None for value in tops):
        return "弱"
    if all(value >= EDGE for value in tops):
        return "強"
    if all(value <= -EDGE for value in tops):
        return "反向"
    if any(value >= EDGE for value in tops) and any(value <= -EDGE for value in tops):
        return "不穩"
    return "弱"


def run_recent(history: str | Path, out_dir: str | Path, job=None, names: list[str] | None = None) -> Path:
    """Every factor of the daily engine, ranked every week from 2015-06, against the next 20 sessions. 2026-10-21:
    ``names`` — only those (a new family), written over the latest full report's other factors."""
    from quant_platform.research import daily
    from quant_platform.research.legacy_challenger import BENCHMARK

    if job:
        job.update(current="載入 2013 年起全市場行情", force=True)
    data, fp = daily.load(history)
    growth, benchmark = 1.0, {}
    for day in sorted(data.closes.get(BENCHMARK, {})):
        growth *= data.factors.get(BENCHMARK, {}).get(day, 1.0)
        benchmark[day] = data.closes[BENCHMARK][day] * growth
    eligibility = daily.DailyRule(name="資格", factors={"momentum_3": 1.0})
    labels = {**daily.FACTOR_LABELS, **daily.EARNINGS_FACTORS}       # 2026-10-28: the earnings family by name
    factors = [name for name in (names or daily.FACTOR_LABELS) if name in labels]
    sessions = fp.sessions
    positions = [index for index, day in enumerate(sessions)
                 if daily.RECENT_START <= day <= daily.RECENT_END and index + HORIZON < len(sessions)][::STEP]
    if job:
        job.update(total=len(factors), done=0, force=True)
    rows = []
    for count, factor in enumerate(factors):
        if job:
            job.update(done=count, current=labels[factor])
        matrix = fp.matrix(factor)
        for position in positions:
            day, later = sessions[position], sessions[position + HORIZON]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                forward = fp.panel.filled[:, position + HORIZON] / fp.panel.filled[:, position] - 1
            score = matrix[:, position].astype(float)
            mask = fp.eligible(eligibility, position) & np.isfinite(score) & np.isfinite(forward)
            usable = int(mask.sum())
            if usable < 50:
                continue
            s, r = score[mask], forward[mask]
            with np.errstate(invalid="ignore", divide="ignore"):
                ic = float(np.corrcoef(_ranks(s), _ranks(r))[0, 1])
            if not np.isfinite(ic):
                continue                    # 2026-10-15: every score equal that day (shared ranks), nothing to rank
            top, bottom = _fifths(s, r)
            index_return = benchmark[later] / benchmark[day] - 1 if day in benchmark and later in benchmark else None
            rows.append({"date": day.isoformat(), "factor": factor, "ic": ic,
                         "spread": float(top.mean() - bottom.mean()) if len(top) and len(bottom) else None,
                         "top_excess": float(top.mean() - r.mean()) if len(top) else None,
                         "top_vs_0050": None if index_return is None or not len(top) else float(top.mean() - index_return)})
    scale = 252 / HORIZON
    summary: dict[str, object] = {}
    for factor in factors:
        mine = [row for row in rows if row["factor"] == factor]
        periods = {}
        short = not any(row["date"] <= RECENT_COLUMNS[1][3].isoformat() for row in mine)   # nothing before 2020-10
        for key, label, start, end in (*RECENT_COLUMNS, SHORT_FIRST) if short else RECENT_COLUMNS:
            chosen = [row for row in mine if start.isoformat() <= row["date"] <= end.isoformat()]
            if len(chosen) < 10:
                continue
            ics = [row["ic"] for row in chosen]
            versus = [row["top_vs_0050"] for row in chosen if row["top_vs_0050"] is not None]
            spreads = [row["spread"] for row in chosen if row["spread"] is not None]       # 2026-10-15: a fifth of
            tops = [row["top_excess"] for row in chosen if row["top_excess"] is not None]  # equal scores is none
            deviation = statistics.stdev(ics) if len(ics) > 1 else 0.0
            periods[key] = {"label": label, "observations": len(chosen), "ic": round(statistics.fmean(ics), 4),
                            "t": round(statistics.fmean(ics) / (deviation / math.sqrt(len(ics))), 2) if deviation else None,
                            "positive": round(sum(value > 0 for value in ics) / len(ics), 3),
                            "spread_year": round(statistics.fmean(spreads) * scale, 4) if spreads else None,
                            "top_excess_year": round(statistics.fmean(tops) * scale, 4) if tops else None,
                            "top_share": round(len(tops) / len(chosen), 3),
                            "top_vs_0050_year": round(statistics.fmean(versus) * scale, 4) if versus else None}
        by_year: dict[str, list[float]] = {}
        for row in mine:
            if row["top_excess"] is not None:
                by_year.setdefault(row["date"][:4], []).append(row["top_excess"])
        summary[factor] = {"label": labels[factor], "periods": periods,
                           "verdict": short_verdict(periods) if short and mine else recent_verdict(periods),
                           "spread_by_year": {year: round(statistics.fmean(values) * scale, 4)
                                              for year, values in sorted(by_year.items())}}
    if names:                                   # the other factors as the latest full report has them
        earlier = sorted(Path(out_dir).glob("strength-*-recent.json")) if Path(out_dir).is_dir() else []
        kept = json.loads(earlier[-1].read_text(encoding="utf-8")).get("factors") or {} if earlier else {}
        summary = {**{key: value for key, value in kept.items() if key not in summary}, **summary}
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "design": "recent",
              "universe": "證交所上市普通股（含後來下市），資格同規則；每週排名一次，看之後 20 個交易日",
              "columns": [[key, label] for key, label, _start, _end in RECENT_COLUMNS], "factors": summary,
              "months": len({row["date"] for row in rows}), "horizon": HORIZON, "step": STEP}
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"strength-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}-recent.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if job:
        job.update(done=len(factors), force=True)
        job.payload["summary"] = f"{len(factors)} 個因子、{report['months']} 次排名（每週）"
    return path


def event_weeks(matrix: np.ndarray, forward: np.ndarray, eligible: np.ndarray) -> tuple[float, int] | None:
    """One week of an event factor: the stocks with an event (a nonzero value) against every eligible stock, their
    mean next-20-session return less the eligible mean, and how many had one; None without an event."""
    mask = eligible & np.isfinite(matrix) & np.isfinite(forward)
    if mask.sum() < 50:
        return None
    hit = mask & (matrix != 0)
    if not hit.any():
        return None
    return float(forward[hit].mean() - forward[mask].mean()), int(hit.sum())


def event_study(history: str | Path, out_dir: str | Path, factors: list[str], job=None) -> Path:
    """2026-10-19 (使用者：還有什麼方法可以找到更好的策略): event factors (insiders' notices to sell, buyback programs)
    are zero for most stocks most weeks, so ranking them by fifths says little. Every week from 2015-06: the stocks with
    an event in the last 60 sessions against every eligible stock — their mean next-20-session return less the
    eligible mean — by the same periods as the strength table and by year, with a t on the weekly differences."""
    from quant_platform.research import daily

    if job:
        job.update(current="載入 2013 年起全市場行情", force=True)
    _data, fp = daily.load(history)
    eligibility = daily.DailyRule(name="資格", factors={"momentum_3": 1.0})
    sessions = fp.sessions
    positions = [index for index, day in enumerate(sessions)
                 if daily.RECENT_START <= day <= daily.RECENT_END and index + HORIZON < len(sessions)][::STEP]
    summary: dict[str, object] = {}
    for count, factor in enumerate(factors):
        if job:
            job.update(done=count, total=len(factors), current=daily.FACTOR_LABELS[factor], force=True)
        matrix = fp.matrix(factor)
        weeks = []
        for position in positions:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                forward = fp.panel.filled[:, position + HORIZON] / fp.panel.filled[:, position] - 1
            found = event_weeks(matrix[:, position].astype(float), forward, fp.eligible(eligibility, position))
            if found:
                weeks.append({"date": sessions[position].isoformat(), "excess": found[0], "stocks": found[1]})
        periods = {}
        for key, label, start, end in RECENT_COLUMNS:
            chosen = [row["excess"] for row in weeks if start.isoformat() <= row["date"] <= end.isoformat()]
            if len(chosen) < 10:
                continue
            deviation = statistics.stdev(chosen)
            periods[key] = {"label": label, "weeks": len(chosen), "excess_20": round(statistics.fmean(chosen), 5),
                            "t": round(statistics.fmean(chosen) / (deviation / math.sqrt(len(chosen))), 2) if deviation else None,
                            "share_positive": round(sum(value > 0 for value in chosen) / len(chosen), 3)}
        by_year: dict[str, list[float]] = {}
        for row in weeks:
            by_year.setdefault(row["date"][:4], []).append(row["excess"])
        summary[factor] = {"label": daily.FACTOR_LABELS[factor], "periods": periods,
                           "stocks_per_week": round(statistics.fmean(row["stocks"] for row in weeks), 1) if weeks else 0,
                           "by_year": {year: round(statistics.fmean(values), 5) for year, values in sorted(by_year.items())}}
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "design": "events",
              "universe": "證交所上市普通股（含後來下市），資格同規則；每週一次，有事件的股票之後 20 個交易日減全部合格股票平均",
              "factors": summary, "horizon": HORIZON, "step": STEP}
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"events-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if job:
        job.payload["summary"] = f"{len(factors)} 個事件因子、{len(positions)} 週"
    return path


DEFAULT_COLUMNS = [["development", "開發期 2005–2016"], ["validation", "驗證期 2017–2021"],
                   ["final", "最終驗證期 2022–2026"], ["since_2024", "2024 後"]]


def columns(report: dict[str, object] | None) -> list[list[str]]:
    return list((report or {}).get("columns") or DEFAULT_COLUMNS)


CORRELATED = 0.7        # factors whose rankings move together at least this much on average are one family


def correlations(history: str | Path, out_dir: str | Path, job=None, step: int = 20) -> Path:
    """2026-10-20 (使用者：那些因子有高度相關，可否利用這些因子獲得更高的報酬): every pair of the daily engine's factors
    (the models' scores too) — their rankings' correlation across the eligible stocks, every ``step`` sessions from
    2015-06, averaged; the families of factors whose rankings move together (at least CORRELATED, linked), and each
    factor's strongest partner. Description only: nothing is chosen with it."""
    from quant_platform.research import daily

    if job:
        job.update(current="載入 2013 年起全市場行情", force=True)
    _data, fp = daily.load(history)
    eligibility = daily.DailyRule(name="資格", factors={"momentum_3": 1.0})
    names = [*daily.FACTOR_LABELS, *daily.MODEL_FACTORS]
    positions = [index for index, day in enumerate(fp.sessions) if daily.RECENT_START <= day <= daily.RECENT_END][::step]
    matrices: dict[str, np.ndarray] = {}
    for count, name in enumerate(names):
        if job:
            job.update(done=count, total=len(names), current=f"讀因子 {name}", force=count % 10 == 0)
        try:
            matrix = fp.matrix(name)
        except Exception:                       # a model not trained here, a table not downloaded
            continue
        if np.isfinite(matrix[:, positions]).sum() > 0:
            matrices[name] = matrix
    kept = list(matrices)
    totals = np.zeros((len(kept), len(kept)))
    counts = np.zeros((len(kept), len(kept)))
    for position in positions:
        mask = fp.eligible(eligibility, position)
        ranked = []
        for name in kept:
            values = matrices[name][:, position].astype(float)
            values = np.where(mask & np.isfinite(values), values, np.nan)
            ranked.append(pd.Series(values).rank().to_numpy())
        frame = pd.DataFrame(np.array(ranked).T, columns=kept)
        if len(frame.dropna(how="all")) < 50:
            continue
        corr = frame.corr(min_periods=50).to_numpy()
        valid = np.isfinite(corr)
        totals[valid] += corr[valid]
        counts[valid] += 1
    with np.errstate(invalid="ignore"):
        average = np.where(counts > 0, totals / np.maximum(counts, 1), np.nan)
    family = list(range(len(kept)))

    def root(index: int) -> int:
        while family[index] != index:
            family[index] = family[family[index]]
            index = family[index]
        return index
    for left, right in zip(*np.nonzero(np.triu(np.abs(np.nan_to_num(average)) >= CORRELATED, 1))):
        family[root(int(left))] = root(int(right))
    groups: dict[int, list[str]] = {}
    for index, name in enumerate(kept):
        groups.setdefault(root(index), []).append(name)
    labels = {**daily.FACTOR_LABELS, **daily.MODEL_FACTORS}
    families = sorted(([{"factors": members, "labels": [labels[name] for name in members]}
                        for members in groups.values() if len(members) > 1]), key=lambda item: -len(item["factors"]))
    pairs = []
    for left in range(len(kept)):
        for right in range(left + 1, len(kept)):
            if np.isfinite(average[left, right]):
                pairs.append({"a": kept[left], "b": kept[right], "correlation": round(float(average[left, right]), 3)})
    pairs.sort(key=lambda item: -abs(item["correlation"]))
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "factors": len(kept),
              "dates": len(positions), "threshold": CORRELATED, "families": families,
              "alone": [name for members in groups.values() if len(members) == 1 for name in members],
              "pairs": pairs[:200], "labels": {name: labels[name] for name in kept}}
    folder = Path(out_dir)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"correlations-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    if job:
        job.payload["summary"] = f"{len(kept)} 個因子、{len(families)} 群高度相關（≥ {CORRELATED}）"
    return path
