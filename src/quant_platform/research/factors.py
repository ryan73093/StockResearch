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
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values))
    ranks[order] = np.arange(len(values))
    return ranks


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
                ic = float(np.corrcoef(_ranks(s), _ranks(r))[0, 1])
                order = np.argsort(-s, kind="stable")
                fifth = max(1, count // 5)
                top, bottom = r[order[:fifth]], r[order[-fifth:]]
                rows.append({"date": current.isoformat(), "factor": factor, "n": count, "ic": ic,
                             "spread": float(top.mean() - bottom.mean()), "top_excess": float(top.mean() - r.mean()),
                             "top_vs_0050": None if index_return is None else float(top.mean() - index_return)})
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
            spreads = [row["spread"] for row in chosen]
            tops = [row["top_excess"] for row in chosen]
            versus = [row["top_vs_0050"] for row in chosen if row.get("top_vs_0050") is not None]
            periods[key] = {
                "label": label, "months": len(chosen), "ic": round(mean, 4),
                "t": round(mean / (deviation / math.sqrt(len(ics))), 2) if deviation else None,
                "positive": round(sum(value > 0 for value in ics) / len(ics), 3),
                "spread_year": round(statistics.fmean(spreads) * 12, 4),
                "top_excess_year": round(statistics.fmean(tops) * 12, 4),
                "top_vs_0050_year": round(statistics.fmean(versus) * 12, 4) if versus else None,
            }
        by_year: dict[str, list[float]] = {}
        for row in mine:
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
    for key, item in (report.get("factors") or {}).items():
        periods = item.get("periods") or {}
        rows.append({"factor": key, "label": item.get("label", key), "verdict": verdict(periods),
                     "dev": periods.get("development"), "val": periods.get("validation"), "final": periods.get("final"),
                     "before": periods.get("before_2020"), "after": periods.get("after_2020"),
                     "recent": periods.get("since_2024"), "by_year": item.get("spread_by_year") or {}})
    rows.sort(key=lambda row: (VERDICT_ORDER.index(row["verdict"]) if row["verdict"] in VERDICT_ORDER else 9,
                               -sum(((row[key] or {}).get("top_excess_year") or 0) for key in ("dev", "val", "final"))))
    return rows
