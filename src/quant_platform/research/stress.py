"""2026-10-24 (使用者：我要去測你的這些策略在每一次的大空頭的時候有沒有辦法撐過去，2008 年的金融海嘯你撐得過嗎;
research_method §7「大空頭壓力測試」, written before the run): the strategies picked on 2015-06 onwards, run on the years
they were never picked on — a test, not a selection (no rule is chosen or graded on these years).

- The strategies: the money-against-falls frontier, the five richest that beat 0050 in every period (research/eras.py)
  and each family's best (categories.best_of) — single rules and blends (each sleeve on its share of every amount).
- The models they read are trained again, year by year, on data from FIRST_DATA_YEAR only up to each year (the 2007
  model knows 2006's end and nothing after), into ``models_long`` beside the research models (which stay as they are).
- 30 萬 and 1 萬 on every 5th like every rule: one account from START, and for each scenario one opened on its high.
- The scenarios (使用者 2026-10-24：我不要只有 2008 的，應該要很多空頭情境，你都要分析給我): every 0050 fall of BEAR or
  more from a high (sessions, net of the money put in) — 大空頭 from BIG, 修正 below; 急殺 when the low came within FAST
  sessions, 慢跌 after. For each: how far the strategy fell over the same days, and when it was back to where it stood
  on 0050's high, against 0050's own; for the account opened on the high, its deepest loss against the money put in and
  when it was worth that money again.

Verdict (written before the run; it changes no grade): a strategy held through a scenario if it fell at most WORSE
further than 0050 and was back no later than 0050; held_2008 is the scenario whose high falls in CRISIS; held_all is
every scenario. Limits: no capital reductions before 2011; the alerts, day trading and holder data start 2014, 2014 and 2010
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
BEAR = 0.15                                          # every 0050 fall of 15% or more is a scenario
BIG = 0.20                                           # 大空頭 from here; 修正 below
FAST = 40                                            # sessions from the high to the low: 急殺 within
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
        missing = [year for year in years if not ((out / f"{year}.pkl").is_file() or (out / f"{year}-0.pt").is_file())]
        if not missing:                          # every year there (a deep model saves <year>-<seed>.pt)
            done.append(version)
            continue
        if job:
            job.update(done=number, total=len(wanted), current=f"重訓 {version}（{missing[0]}～{missing[-1]}）", force=True)
        settings = research_model.VERSIONS[version]
        meta = out / "meta.json"
        if meta.is_file():                       # 2026-10-24: a stop by the development gate does not apply here
            stored = json.loads(meta.read_text(encoding="utf-8"))
            stored.pop("stopped", None)
            meta.write_text(json.dumps(stored, ensure_ascii=False, indent=1), encoding="utf-8")
        research_model.train(fp, out, "", missing, label=settings["label"], version=version,
                             horizon=settings.get("horizon", research_model.HORIZON), gate=False)
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


def held(row: dict) -> bool:
    """Held through one bear: at most WORSE further than 0050 over its fall, and back no later than 0050."""
    late = row["days"] is None or (row["base_days"] is not None and row["days"] > row["base_days"])
    return row["gap"] >= -WORSE and not late


def verdict(rows: list[dict]) -> dict[str, object]:
    """Every bear held or not; the 2008 one (its high in CRISIS) on its own."""
    for row in rows:
        row["held"] = held(row)
    crisis = next((row for row in rows if CRISIS[0].isoformat() <= row["high"] <= CRISIS[1].isoformat()), None)
    return {"held_2008": crisis["held"] if crisis else None, "held_count": sum(row["held"] for row in rows),
            "held_all": bool(rows) and all(row["held"] for row in rows),
            "worse_bears": sum(row["gap"] < -WORSE for row in rows), "bears": len(rows)}


def kind(item: dict) -> dict[str, object]:
    """A bear's size (大空頭 from BIG, 修正 below) and speed (急殺 when the low came within FAST sessions)."""
    sessions = item["low"] - item["high"]
    return {"size": "大空頭" if item["fall"] <= -BIG else "修正", "speed": "急殺" if sessions <= FAST else "慢跌",
            "sessions": sessions}


def entry(result, until: date | None) -> dict[str, object]:
    """An account opened on a bear's high: its deepest loss against the money put in (up to ``until``, 0050's day
    back, or the end) and the days until it was worth the money put in again after that low."""
    paid, pointer, total = [], 0, 0.0
    flows = sorted(result.contributions)
    for day in result.days:
        while pointer < len(flows) and flows[pointer][0] <= day:
            total += flows[pointer][1]
            pointer += 1
        paid.append(total)
    ratio = np.asarray(result.values, dtype=float) / np.maximum(np.asarray(paid, dtype=float), 1.0) - 1
    stop = len(result.days) if until is None else next((index for index, day in enumerate(result.days) if day > until),
                                                       len(result.days))
    bottom = int(np.argmin(ratio[:max(stop, 1)]))
    back = next((index for index in range(bottom, len(ratio)) if ratio[index] >= 0), None)
    return {"worst": round(float(ratio[bottom]), 4), "worst_day": result.days[bottom].isoformat(),
            "even_days": (result.days[back] - result.days[0]).days if back is not None else None}


def run(history: str | Path, research: str | Path, job=None) -> Path:
    """2026-10-24 (使用者：我不要只有 2008 的，應該要很多空頭情境，你都要分析給我): one account per strategy from START
    through every 0050 fall of BEAR or more (each held or not), and for each fall an account opened on its high."""
    from quant_platform.research.blend import BlendAccount, parse_spec
    from quant_platform.research.costs import broker_costs
    from quant_platform.research.daily import RECENT_END, DailyRule, account_parking, daily_weights, exposure_schedule
    from quant_platform.research.daily import simulate_daily
    from quant_platform.research.decisions import rankings

    strategies = chosen(research)
    data, fp = load_long(history)
    costs = broker_costs("cathay")
    end = RECENT_END
    base = simulate_daily(data, None, costs, START, end)
    base_units = _units(base)
    falls = bears(base.days, base_units)
    scenarios = [{"high": base.days[item["high"]].isoformat(), "low": base.days[item["low"]].isoformat(),
                  "fall": round(item["fall"], 4), **kind(item),
                  "back": base.days[item["back"]].isoformat() if item["back"] is not None else None,
                  "base_days": (base.days[item["back"]] - base.days[item["high"]]).days if item["back"] is not None else None}
                 for item in falls]
    base_tops = []
    for item in falls:                           # 0050 opened on each high, judged to its own day back
        opened = simulate_daily(data, None, costs, base.days[item["high"]], end)
        base_tops.append(entry(opened, base.days[item["back"]] if item["back"] is not None else None))
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "start": START.isoformat(),
              "bear": BEAR, "big": BIG, "fast": FAST, "worse": WORSE, "models": versions(strategies),
              "scenarios": scenarios, "base_final": round(base.final_value, 2),
              "base_max_drawdown": round(float((base_units / np.maximum.accumulate(base_units) - 1).min()), 4),
              "base_tops": base_tops, "rows": []}
    for number, item in enumerate(strategies):
        if job:
            job.update(done=number, total=len(strategies), current=f"#{item['trial']}：2007 起與每次空頭高點起")
        spec = item["spec"]
        try:
            if spec.get("kind") == "blend":
                account = BlendAccount(data, fp, parse_spec(spec), costs, START, end)

                def runner(first, account=account):
                    return account.run(first, end)
            else:
                rule = DailyRule.model_validate(spec)
                ranks = rankings(fp, rule, START, end)
                weights, parked = daily_weights(fp, rule, ranks), account_parking(data, fp, rule, costs)
                exposure = exposure_schedule(data, fp, rule, costs)

                def runner(first, rule=rule, ranks=ranks, weights=weights, parked=parked, exposure=exposure):
                    return simulate_daily(data, rule, costs, first, end, ranks, weights=weights, parked=parked,
                                          exposure=exposure)
            result = runner(START)
            units = _units(result)
            if result.days != base.days:
                units = np.interp([day.toordinal() for day in base.days], [day.toordinal() for day in result.days], units)
            judged = judge(base.days, units, base_units, falls)
            summary = verdict(judged)
            tops = []
            for fall, mine_base in zip(falls, base_tops):
                opened = runner(base.days[fall["high"]])
                found = entry(opened, base.days[fall["back"]] if fall["back"] is not None else None)
                tops.append({**found, "base_worst": mine_base["worst"], "base_even_days": mine_base["even_days"]})
        except Exception as exc:                        # noqa: BLE001 - one strategy's failure is reported, not fatal
            report["rows"].append({**{k: v for k, v in item.items() if k != "spec"},
                                   "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        report["rows"].append({**{k: v for k, v in item.items() if k != "spec"},
                               "final": round(result.final_value, 2), "base_final": round(base.final_value, 2),
                               "contributed": round(result.contributed, 2),
                               "max_drawdown": round(float((units / np.maximum.accumulate(units) - 1).min()), 4),
                               "base_max_drawdown": report["base_max_drawdown"], "bears": judged, "tops": tops, **summary})
    rows = [row for row in report["rows"] if not row.get("error")]
    for position, scenario in enumerate(scenarios):   # each scenario: how many held, the least and the most fallen
        items = [(row, row["bears"][position]) for row in rows]
        scenario["held"] = sum(1 for _row, item in items if item["held"])
        scenario["tested"] = len(items)
        if items:
            best = max(items, key=lambda pair: pair[1]["fall"])
            worst = min(items, key=lambda pair: pair[1]["fall"])
            scenario["least"] = {"trial": best[0]["trial"], "fall": best[1]["fall"]}
            scenario["most"] = {"trial": worst[0]["trial"], "fall": worst[1]["fall"]}
    folder = Path(research) / "stress"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"stress-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def latest(research: str | Path) -> dict | None:
    folder = Path(research) / "stress"
    files = sorted(folder.glob("stress-*.json")) if folder.is_dir() else []
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None
