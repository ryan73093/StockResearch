"""2026-10-24 (使用者：我要去測你的這些策略在每一次的大空頭的時候有沒有辦法撐過去，2008 年的金融海嘯你撐得過嗎;
research_method §7「大空頭壓力測試」, written before the run): the strategies picked on 2015-06 onwards, run on the years
they were never picked on — a test, not a selection (no rule is chosen or graded on these years).

- The strategies: the money-against-falls frontier, the five richest that beat 0050 in every period (research/eras.py)
  and each family's best (categories.best_of) — single rules and blends (each sleeve on its share of every amount).
- The models they read are trained again, year by year, on data from FIRST_DATA_YEAR only up to each year (the 2007
  model knows 2006's end and nothing after), into ``models_long`` beside the research models (which stay as they are).
- Two accounts each, 30 萬 and 1 萬 on every 5th like every rule: from START, and from the 2007 top (PEAK_START).
- The bear markets: 0050's falls of BEAR or more from a high (sessions, net of the money put in). For each: how far the
  strategy fell over the same days, and when it was back to where it stood on 0050's high, against 0050's own.

Verdict per strategy (written before the run; it changes no grade): it held through 2008 if over 0050's 2007-2008 fall
it fell at most WORSE further than 0050 and was back no later than 0050; in any bear, falling more than WORSE further is
marked. Limits: no capital reductions before 2011; the alerts, day trading and holder data start 2014, 2014 and 2010
(the conditions on them are idle before); the 2007 models learnt from two years."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
FIRST_DATA_YEAR = 2004
FIRST_MODEL_YEAR = 2007
START = date(2007, 1, 2)
PEAK_START = date(2007, 10, 31)
BEAR = 0.20
WORSE = 0.10
MODELS = "models_long"
CRISIS = (date(2007, 6, 1), date(2009, 12, 31))      # the 2008 bear is the one whose high falls in here


def models_dir(history: str | Path) -> Path:
    return Path(history).parent / MODELS


def chosen(research: str | Path) -> list[dict[str, object]]:
    """The strategies to test: the frontier, the five richest winning every period, each family's best passing rule —
    100% stocks, 2015-06 track; the T0 consensus list and the robots' own scores are left out (they read other rules'
    rankings or a robot trained from 2018)."""
    from quant_platform.research.categories import best_of
    from quant_platform.research.daily import PASSING_TIERS, short_track
    from quant_platform.research.dashboard import factor_names
    from quant_platform.research.downside import table
    from quant_platform.research.eras import analyze
    from quant_platform.research.pool import _spec_of, pool_view

    research = Path(research)
    rows = [row for row in pool_view(research, basis="seed")["daily"]
            if not row.get("uses_0050") and row.get("final_value") and not short_track({"start": row.get("start")})]
    by_hash = {row["spec_hash"]: row for row in rows}
    picked: dict[str, str] = {}
    for row in table(research):
        if row.get("frontier") and row["rule_hash"] in by_hash:
            picked[row["rule_hash"]] = "取捨前緣"
    for row in analyze(research)["consistent"][:5]:
        picked.setdefault(row["spec_hash"], "五個時期都贏 0050")
    families: dict[str, list[dict]] = {}
    for row in rows:
        families.setdefault((row.get("category") or {}).get("family") or "其他", []).append(row)
    for family, members in families.items():
        best = best_of([row for row in members if row["tier"] in PASSING_TIERS])
        if best:
            picked.setdefault(best["spec_hash"], f"{family}最好的")
    output = []
    for key, why in picked.items():
        spec = _spec_of(research, by_hash[key].get("report_file"))
        if not spec:
            continue
        names = factor_names(spec)
        if any(name.startswith(("t0_consensus", "rl_", "rolling", "block_", "llm_", "all_equal", "tech_")) for name in names):
            continue
        output.append({"trial": by_hash[key]["trial_id"], "spec_hash": key, "name": by_hash[key]["name"], "why": why,
                       "tier": by_hash[key]["tier"], "final_value": by_hash[key]["final_value"], "spec": spec})
    return sorted(output, key=lambda item: -(item["final_value"] or 0))


def versions(strategies: list[dict]) -> list[str]:
    from quant_platform.research.dashboard import factor_names
    from quant_platform.research.model import MODELS

    return sorted({MODELS[name]["version"] for item in strategies for name in factor_names(item["spec"]) if name in MODELS})


def load_long(history: str | Path):
    """Every listed stock from FIRST_DATA_YEAR, the factor panel reading the stress test's own models."""
    from quant_platform.research.daily import (
        RECENT_END, ChipStore, FactorPanel, Industries, Panel, load_industries, market_closes,
    )
    from quant_platform.research.stock_rules import load_stock_data

    data = load_stock_data(history, FIRST_DATA_YEAR, RECENT_END.year)
    panel = Panel(data)
    fp = FactorPanel(panel, Industries(load_industries(history)), ChipStore(history, panel.sessions, panel.symbols, panel.close),
                     market_closes(history, panel.sessions), models_dir(history))
    return data, fp


def train(history: str | Path, research: str | Path, job=None) -> list[str]:
    """Each model the strategies read, retrained year by year from FIRST_MODEL_YEAR into ``models_long``; a version
    whose years are all there is left as it is (the run resumes)."""
    from quant_platform.research import model as research_model
    from quant_platform.research.daily import RECENT_END

    wanted = versions(chosen(research))
    years = list(range(FIRST_MODEL_YEAR, RECENT_END.year + 1))
    _data, fp = load_long(history)
    done = []
    for number, version in enumerate(wanted):
        out = models_dir(history) / version
        if out.is_dir() and all((out / f"{year}.pkl").is_file() or (out / str(year)).is_dir() for year in years):
            done.append(version)
            continue
        if job:
            job.update(done=number, total=len(wanted), current=f"重訓 {version}（{years[0]}～{years[-1]}）", force=True)
        settings = research_model.VERSIONS[version]
        research_model.train(fp, out, "", years, label=settings["label"], version=version,
                             horizon=settings.get("horizon", research_model.HORIZON))
        done.append(version)
    return done


def _units(run) -> np.ndarray:
    from quant_platform.research.metrics import unit_values

    return np.asarray(unit_values(run.values, run.flows), dtype=float)


def bears(days: list[date], units: np.ndarray, depth: float = BEAR) -> list[dict[str, object]]:
    """0050's falls of ``depth`` or more: the high, the low, how far, and the day it was back at the high (or None)."""
    output, peak, position = [], 0, 0
    while position < len(units):
        if units[position] >= units[peak]:
            peak = position
        elif units[position] / units[peak] - 1 <= -depth:
            trough = position
            back = next((index for index in range(position, len(units)) if units[index] >= units[peak]), None)
            stop = back if back is not None else len(units)
            trough = peak + int(np.argmin(units[peak:stop]))
            output.append({"high": peak, "low": trough, "back": back, "fall": float(units[trough] / units[peak] - 1)})
            if back is None:
                break
            position, peak = back, back
            continue
        position += 1
    return output


def judge(days: list[date], mine: np.ndarray, base: np.ndarray, falls: list[dict]) -> list[dict[str, object]]:
    """Each bear: 0050's fall and the strategy's over the same days, the strategy's worst from 0050's high to its low,
    and the days each was back where it stood on 0050's high."""
    output = []
    for item in falls:
        high, low = item["high"], item["low"]
        fell = float(mine[low] / mine[high] - 1)
        bottom = high + int(np.argmin(mine[high:low + 1]))           # the strategy's own low in 0050's fall
        worst = float(mine[bottom] / mine[high] - 1)
        back = next((index for index in range(bottom + 1, len(mine)) if mine[index] >= mine[high]), None)
        output.append({"high": days[high].isoformat(), "low": days[low].isoformat(),
                       "base_fall": round(item["fall"], 4), "fall": round(fell, 4), "worst": round(worst, 4),
                       "gap": round(fell - item["fall"], 4),
                       "base_back": days[item["back"]].isoformat() if item["back"] is not None else None,
                       "back": days[back].isoformat() if back is not None else None,
                       "base_days": (days[item["back"]] - days[high]).days if item["back"] is not None else None,
                       "days": (days[back] - days[high]).days if back is not None else None})
    return output


def verdict(rows: list[dict]) -> dict[str, object]:
    """Held through 2008: in the bear whose high falls in CRISIS, at most WORSE further than 0050 and back no later."""
    crisis = next((row for row in rows if CRISIS[0].isoformat() <= row["high"] <= CRISIS[1].isoformat()), None)
    held = None
    if crisis:
        late = crisis["days"] is None or (crisis["base_days"] is not None and crisis["days"] > crisis["base_days"])
        held = crisis["gap"] >= -WORSE and not late
    return {"held_2008": held, "worse_bears": sum(row["gap"] < -WORSE for row in rows),
            "bears": len(rows)}


def run(history: str | Path, research: str | Path, job=None) -> Path:
    from quant_platform.research.blend import BlendAccount, parse_spec
    from quant_platform.research.costs import broker_costs
    from quant_platform.research.daily import RECENT_END, DailyRule, account_parking, daily_weights, exposure_schedule
    from quant_platform.research.daily import simulate_daily
    from quant_platform.research.decisions import rankings

    strategies = chosen(research)
    data, fp = load_long(history)
    costs = broker_costs("cathay")
    end = RECENT_END
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "start": START.isoformat(),
              "peak_start": PEAK_START.isoformat(), "bear": BEAR, "worse": WORSE, "models": versions(strategies),
              "accounts": {}}
    for key, first in (("from_2007", START), ("from_2007_top", PEAK_START)):
        base = simulate_daily(data, None, costs, first, end)
        base_units = _units(base)
        falls = bears(base.days, base_units)
        rows = []
        for number, item in enumerate(strategies):
            if job:
                job.update(done=number, total=len(strategies), current=f"{'2007-01' if key == 'from_2007' else '2007-10 高點'} 起：#{item['trial']}")
            spec = item["spec"]
            try:
                if spec.get("kind") == "blend":
                    account = BlendAccount(data, fp, parse_spec(spec), costs, first, end)
                    result = account.run(first, end)
                else:
                    rule = DailyRule.model_validate(spec)
                    ranks = rankings(fp, rule, first, end)
                    result = simulate_daily(data, rule, costs, first, end, ranks, weights=daily_weights(fp, rule, ranks),
                                            parked=account_parking(data, fp, rule, costs),
                                            exposure=exposure_schedule(data, fp, rule, costs))
            except Exception as exc:                    # noqa: BLE001 - one strategy's failure is reported, not fatal
                rows.append({**{k: v for k, v in item.items() if k != "spec"}, "error": f"{type(exc).__name__}: {exc}"[:300]})
                continue
            units = _units(result)
            if result.days != base.days:
                units = np.interp([day.toordinal() for day in base.days], [day.toordinal() for day in result.days], units)
            judged = judge(base.days, units, base_units, falls)
            rows.append({**{k: v for k, v in item.items() if k != "spec"},
                         "final": round(result.final_value, 2), "base_final": round(base.final_value, 2),
                         "contributed": round(result.contributed, 2),
                         "max_drawdown": round(float((units / np.maximum.accumulate(units) - 1).min()), 4),
                         "base_max_drawdown": round(float((base_units / np.maximum.accumulate(base_units) - 1).min()), 4),
                         "bears": judged, **verdict(judged)})
        report["accounts"][key] = {"bears": [{"high": base.days[item["high"]].isoformat(), "low": base.days[item["low"]].isoformat(),
                                              "fall": round(item["fall"], 4),
                                              "back": base.days[item["back"]].isoformat() if item["back"] is not None else None}
                                             for item in falls],
                                   "base_final": round(base.final_value, 2), "rows": rows}
    folder = Path(research) / "stress"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"stress-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def latest(research: str | Path) -> dict | None:
    folder = Path(research) / "stress"
    files = sorted(folder.glob("stress-*.json")) if folder.is_dir() else []
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None
