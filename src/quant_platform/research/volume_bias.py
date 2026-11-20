"""2026-11-20 (D-046 第 3 項): the backtests decide on the day's close with the day's whole turnover; the after-close
orders decide at 13:50 on a table holding about 98.5% of it. Measured the way the decision asked: the same rules with
every day's turnover replaced by the previous session's (a bigger difference than 1.5%, so an upper bound), the
grade and the money since 2020-10 against the rule run as usual — both in the same process, nothing registered.

What reads the day's turnover: the 20- and 120-session averages (the NT$20 million eligibility line, the liquidity and
volume-surge factors, the tie-break), the price-volume correlation and the short-sale factors scaled by turnover. The
models' stored scores are not recomputed (their features were computed with the whole day's turnover when trained):
for a rule reading a model only the rule's own use of turnover is measured. The fills' share of the session's
turnover is the fill day's and stays as it is.
"""

from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
SAMPLE = 40


def lagged_panel(history: Path, data, ratios: np.ndarray | None = None, seed: int = 0):
    """A factor panel whose turnover on each session is the previous session's — or, with ``ratios`` (2026-11-20, the
    realistic version), the session's own times a ratio drawn at random for every stock and session from ``ratios``
    (what the 13:50 tables held against the day's final data, ``table_ratios``)."""
    from quant_platform.research.daily import ChipStore, FactorPanel, Industries, load_industries, market_closes
    from quant_platform.research.model import models_root
    from quant_platform.research.stock_rules import Panel

    panel = Panel(data)
    if ratios is None:
        shifted = np.full_like(panel.turnover, np.nan)
        shifted[:, 1:] = panel.turnover[:, :-1]
        panel.turnover = shifted
    else:
        drawn = np.random.default_rng(seed).choice(np.asarray(ratios, dtype=float), size=panel.turnover.shape)
        panel.turnover = panel.turnover * drawn
    return FactorPanel(panel, Industries(load_industries(history)), ChipStore(history, panel.sessions, panel.symbols, panel.close),
                       market_closes(history, panel.sessions), models_root(history))


def today_panel(history: Path, data, ratio: float):
    """A factor panel whose 20- and 120-session average turnovers take the session's own turnover at ``ratio`` (the
    earlier sessions whole): what a decision at 13:50 reads. The factors built on those averages (eligibility, the
    liquidity and the volume-surge factors, the tie-break) follow; the ones reading the day's turnover itself are left."""
    from quant_platform.research.daily import ChipStore, FactorPanel, Industries, load_industries, market_closes
    from quant_platform.research.model import models_root
    from quant_platform.research.stock_rules import Panel

    panel = Panel(data)
    fp = FactorPanel(panel, Industries(load_industries(history)), ChipStore(history, panel.sessions, panel.symbols, panel.close),
                     market_closes(history, panel.sessions), models_root(history))
    today = np.nan_to_num(panel.turnover, nan=0.0)
    fp.turnover_20 = fp.turnover_20 - (1 - ratio) * today / 20
    fp.turnover_120 = fp.turnover_120 - (1 - ratio) * today / 120
    return fp


def table_ratios(research: Path, history: Path) -> np.ndarray:
    """Every listed stock-day the after-close orders kept a 13:50 table for: its turnover in the table ÷ the day's final
    turnover (1.0 where they agree; a stock with no final turnover left out)."""
    from datetime import date

    from quant_platform.research.after_close import FOLDER, today_table
    from quant_platform.research.history.stocks import read_year

    output: list[float] = []
    years: dict[int, list[dict]] = {}
    for path in sorted((Path(research) / FOLDER).glob("*.table.json")):
        day = date.fromisoformat(path.name[:10])
        if day.year not in years:
            source = Path(history) / "stocks" / "twse" / f"{day.year}.parquet"
            years[day.year] = read_year(source) if source.is_file() else []
        final = {row["code"]: row for row in years[day.year] if row["date"] == day}
        if not final:
            continue
        kept, _benchmark, _taiex = today_table(json.loads(path.read_text(encoding="utf-8")), day)
        for row in kept:
            other = final.get(row["code"])
            if other and float(other.get("turnover") or 0) > 0 and row.get("turnover") is not None:
                output.append(float(row["turnover"]) / float(other["turnover"]))
    return np.array(output, dtype=float)


def sample_rules(research: Path, count: int = SAMPLE, seed: int = 0) -> list[tuple[int, object]]:
    """(trial, rule) of ``count`` single rules passing on the month's list, spread over the kinds (round robin, the
    richest first within a kind; ``seed`` breaks nothing — kept for a different draw)."""
    from quant_platform.research.categories import classify
    from quant_platform.research.daily import DailyRule

    lists = sorted((research / "rolling").glob("20??-??-??.json"))
    graded = json.loads(lists[-1].read_text(encoding="utf-8"))["graded"] if lists else []
    kinds: dict[str, list[tuple[float, int, dict]]] = {}
    for row in graded:
        spec = row.get("spec") or {}
        if not row.get("passed") or spec.get("kind") == "blend" or spec.get("universe", "twse") != "twse" or spec.get("free"):
            continue
        family = str(classify(spec).get("family"))
        kinds.setdefault(family, []).append((float((row.get("metrics") or {}).get("since_2020_final_value") or 0),
                                             int(row["trial"]), spec))
    for rows in kinds.values():
        rows.sort(key=lambda item: -item[0])
    order = sorted(kinds, key=lambda name: -len(kinds[name]))
    random.Random(seed).shuffle(order)
    picked: list[tuple[int, object]] = []
    place = 0
    while len(picked) < count and any(place < len(kinds[name]) for name in order):
        for name in order:
            if place < len(kinds[name]) and len(picked) < count:
                _money, trial, spec = kinds[name][place]
                try:
                    picked.append((trial, DailyRule.model_validate(spec)))
                except ValueError:
                    continue
        place += 1
    return picked


def run(history: Path, research: Path, costs, count: int = SAMPLE, job=None, mode: str = "lag") -> dict[str, object]:
    """The sample run as usual and on the lagged panel (``mode`` "table": the realistic one); written to
    research/diagnostics/volume-bias-<time>.json."""
    from quant_platform.research.daily import ENGINE_VERSION, SEED_PLAN, evaluate, load, report_metrics, tier

    data, fp = load(history)
    # 2026-11-20: "table" draws each stock-day's ratio from what the 13:50 tables held; "scale" multiplies every
    # stock-day by their median (the bias alone, no noise); "noise" draws them divided by that median (the noise alone)
    measured = table_ratios(research, history) if mode in ("table", "scale", "noise", "today") else None
    if mode == "scale":
        ratios = np.array([float(np.median(measured))])
    elif mode == "noise":
        ratios = measured / float(np.median(measured))
    else:
        ratios = measured
    if mode == "today":           # 2026-11-20: only the deciding session's own turnover short, as at 13:50
        lagged = today_panel(history, data, float(np.median(measured)))
    else:
        lagged = lagged_panel(history, data, ratios)
    rules = sample_rules(research, count)
    rows = []
    for number, (trial, rule) in enumerate(rules):
        if job:
            job.update(done=number, total=len(rules), current=f"{rule.name[:40]}", force=True)
        usual = report_metrics(evaluate(data, fp, rule, costs), ENGINE_VERSION, SEED_PLAN)
        moved = report_metrics(evaluate(data, lagged, rule, costs), ENGINE_VERSION, SEED_PLAN)
        rows.append({"trial": trial, "name": rule.name, "tier": tier(usual)[0], "tier_lagged": tier(moved)[0],
                     "money": usual.get("since_2020_final_value"), "money_lagged": moved.get("since_2020_final_value"),
                     "drawdown": usual.get("max_drawdown"), "drawdown_lagged": moved.get("max_drawdown")})
    # 2026-11-20: the money ratios had the name of the drawn ratios — the first "table" run's file shows the money's
    # median under "table_ratios"
    money = [row["money_lagged"] / row["money"] for row in rows if row["money"] and row["money_lagged"]]
    summary = {"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "mode": mode, "rules": len(rows),
               "table_ratios": {"count": int(len(measured)), "median": float(np.median(measured)),
                                "p5": float(np.quantile(measured, 0.05)), "p1": float(np.quantile(measured, 0.01))}
               if measured is not None and len(measured) else None,
               "tier_changed": sum(row["tier"] != row["tier_lagged"] for row in rows),
               "money_ratio": {"median": float(np.median(money)), "min": float(min(money)), "max": float(max(money))}
               if money else None, "rows": rows}
    folder = research / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"volume-bias-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    summary["path"] = str(path)
    return summary
