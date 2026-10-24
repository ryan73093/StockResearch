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
# 2026-10-24 (research_method §7「空頭減碼檢驗」, fixed before the run; no setting tried on 2007-2014): the ways of
# holding fewer stocks in a bear, each added to every strategy (every sleeve of a blend); the money out waits in cash.
OVERLAYS = {
    "taiex_200": ("加權指數跌破 200 日均線就不持股", {"market_filter": "taiex_200"}),
    "taiex_10m": ("月底加權指數跌破 10 個月均線就不持股一個月", {"market_filter": "taiex_10m"}),
    "taiex_12m": ("月底加權指數比一年前低就不持股一個月", {"market_filter": "taiex_12m"}),
    "stop_15": ("跌離買進後最高價 15% 就賣", {"stop_loss": 0.15}),
    "vol_v1": ("波動大時減碼到現金", {"vol_scale": "v1"}),
    # 第二版 (research_method §7「空頭減碼第二版」, written before the run)
    "taiex_200s": ("加權指數跌破下彎的 200 日均線才不持股", {"market_filter": "taiex_200s"}),
    "taiex_half": ("加權指數跌破 200 日均線就減碼一半", {"vol_scale": "taiex_half"}),
    # 第三版 (research_method §7「空頭減碼第三版」, written before the run)
    "taiex_staged": ("跌破 200 日均線減碼一半、比一年高點低 20% 就不持股", {"vol_scale": "taiex_staged"}),
}
SHORT = {"none": "不加", "taiex_200": "200 日均線", "taiex_10m": "10 個月均線", "taiex_12m": "一年動能",
         "stop_15": "15% 停損", "vol_v1": "依波動減碼", "taiex_200s": "均線下彎才出", "taiex_half": "減碼一半",
         "taiex_staged": "分段減碼", "taiex_half_wide": "減碼一半（各類前 5 個）",
         "risk_bear": "空頭時賣高風險股", "risk_bear_half": "減碼一半＋賣高風險股",
         "risk_half": "模型決定減碼一半"}
RANKED = ("market_filter", "stop_loss")             # these act where the day's targets are decided: ranked again
HELPS = 2                                            # held at least this many more scenarios (the median strategy)
KEEPS = 0.8                                          # and keeps at least this share of its 2015-06 money


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


def fixed(research: str | Path, trials: tuple[int, ...]) -> list[dict[str, object]]:
    """The strategies ``trials`` as ``chosen`` gives them (2026-10-25: the overlay check reruns on the same 20 — read
    from the pool again, the list had taken in the guarded rules and dropped eight)."""
    from quant_platform.research.registry import TrialRegistry

    research = Path(research)
    records = {record.trial_id: record for record in TrialRegistry(research / "trials.jsonl").records()}
    output = []
    for trial in trials:
        record = records[trial]
        spec = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
        output.append({"trial": trial, "spec_hash": record.spec_hash, "name": record.spec_name, "why": "壓力測試的 20 個",
                       "tier": None, "final_value": (record.metrics or {}).get("final_value"), "spec": spec})
    return output


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
            "worse_bears": sum(row["gap"] < -WORSE for row in rows), "scenario_count": len(rows)}


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


def latest(research: str | Path, prefix: str = "stress") -> dict | None:
    folder = Path(research) / "stress"
    files = sorted(folder.glob(f"{prefix}-*.json")) if folder.is_dir() else []
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else None


def _has(spec: dict, fields: dict) -> bool:
    """The strategy already holds fewer stocks this way (a blend: any sleeve)."""
    rules = [sleeve.get("rule") or {} for sleeve in spec.get("sleeves") or []] if spec.get("kind") == "blend" else [spec]
    return any(rule.get(key) not in (None, "none", 0, 0.0) for rule in rules for key in fields)


def _overlaid(account, data, fp, costs, fields: dict):
    """A blend account whose sleeves all hold fewer stocks the given way; the rankings, weights and parking reused (only
    for a way outside RANKED)."""
    from quant_platform.research.daily import DailyRule, exposure_schedule

    copy = object.__new__(type(account))
    copy.__dict__.update(account.__dict__)
    copy.parts = []
    for sleeve, ranks, weights, parked, _exposure in account.parts:
        rule = DailyRule.model_validate({**sleeve.rule.model_dump(), **fields})
        copy.parts.append((sleeve.model_copy(update={"rule": rule}), ranks, weights, parked,
                           exposure_schedule(data, fp, rule, costs)))
    return copy


def off_share(fp, kind: str, days: list[date]) -> dict[str, object]:
    """How much of the time a market filter kept the money out, and how many times it switched."""
    flags = [fp.risk_on(fp.index[day], kind) for day in days]
    switches = sum(1 for before, after in zip(flags, flags[1:]) if before != after)
    return {"off": round(1 - sum(flags) / max(len(flags), 1), 4), "switches": switches}


def overlay_verdict(rows: list[dict], key: str, base_final: float) -> dict[str, object]:
    """One way of holding fewer stocks over the strategies that took it (research_method §7, written before the run):
    helps = the median strategy held HELPS more scenarios and half or more held 2008; keeps = the median 2015-06 money
    at least KEEPS of the strategy's own without it, and the median 2007 money still beats 0050."""
    pairs = [(row["variants"]["none"], row["variants"][key]) for row in rows
             if "final" in row["variants"].get("none", {}) and "final" in row["variants"].get(key, {})]
    if not pairs:
        return {"tested": 0}
    more = float(np.median([mine["held_count"] - plain["held_count"] for plain, mine in pairs]))
    crisis = sum(1 for _plain, mine in pairs if mine.get("held_2008"))
    kept = float(np.median([mine["recent_final"] / plain["recent_final"] for plain, mine in pairs]))
    long = float(np.median([mine["final"] for _plain, mine in pairs]))
    helps = more >= HELPS and crisis * 2 >= len(pairs)
    keeps = kept >= KEEPS and long > base_final
    return {"tested": len(pairs), "more_held": more, "held_2008": crisis, "kept": round(kept, 4),
            "long_final": round(long, 2), "helps": helps, "keeps": keeps,
            "verdict": "值得做成規則" if helps and keeps else ("空頭有幫助、代價太大" if helps else "沒有幫助")}


def protect_view(report: dict | None) -> dict | None:
    """The overlay check for the page: per way, the median scenarios held with and without it and the verdict; per
    scenario how many held with each way; per strategy each way's scenarios held and 2007 money."""
    if not report or not report.get("rows"):
        return None
    rows = [row for row in report["rows"] if not row.get("error")]
    ways = []
    for key, meta in report["overlays"].items():
        pairs = [(row["variants"]["none"], row["variants"][key]) for row in rows
                 if "final" in row["variants"].get("none", {}) and "final" in row["variants"].get(key, {})]
        ways.append({"key": key, "short": SHORT.get(key, key), **meta, **(report.get("verdicts") or {}).get(key, {}),
                     "plain_held": float(np.median([plain["held_count"] for plain, _mine in pairs])) if pairs else None,
                     "held": float(np.median([mine["held_count"] for _plain, mine in pairs])) if pairs else None,
                     "skipped": sum(1 for row in rows if row["variants"].get(key, {}).get("skip")),
                     "failed": sum(1 for row in rows if row["variants"].get(key, {}).get("error"))})
    return {"generated_at": report["generated_at"], "ways": ways, "columns": ["none", *report["overlays"]], "short": SHORT,
            "scenarios": report["scenarios"], "rows": rows, "base_final": report["base_final"],
            "base_recent_final": report.get("base_recent_final"), "errors": [row for row in report["rows"] if row.get("error")]}


def guard_ways() -> list[tuple[str, str, dict, tuple[int, ...]]]:
    """(key, words, settings, the original rules) of every guard graded so far: the second version's three on the stress
    strategies, the third version's staged guard on them and the half guard on each family's five richest."""
    from quant_platform.research.daily import (
        BEAR_GUARD_ORIGINS, BEAR_GUARDS, BEAR_HALF_WIDE, BEAR_RISK, BEAR_RISK_HALF, BEAR_STAGED,
    )

    ways = [(key, word, fields, BEAR_GUARD_ORIGINS) for key, (word, fields) in BEAR_GUARDS.items()]
    ways.append(("taiex_staged", BEAR_STAGED[0], BEAR_STAGED[1], BEAR_GUARD_ORIGINS))
    ways.append(("taiex_half_wide", BEAR_GUARDS["taiex_half"][0] + "（各類錢最多的 5 個）", BEAR_GUARDS["taiex_half"][1],
                 BEAR_HALF_WIDE))
    for key, (word, fields) in zip(("risk_bear", "risk_bear_half"), BEAR_RISK):     # 第四版
        ways.append((key, word, fields, BEAR_GUARD_ORIGINS))
    ways.append(("risk_half", BEAR_RISK_HALF[0], BEAR_RISK_HALF[1], BEAR_GUARD_ORIGINS))   # 第五版
    return ways


def guard_summary(rows: list[dict]) -> list[dict[str, object]]:
    """Per way (guard_ways), the graded versions against their originals: how many pass, the medians of the money
    kept, the drawdown and the worst 0050-fall gap changed (points), how many fell less than the original."""
    from quant_platform.research.daily import PASSING_TIERS

    output = []
    for key, word, _fields, _trials in guard_ways():
        part = [row for row in rows if row["guard"] == key]
        if not part:
            continue
        output.append({"key": key, "label": word, "short": SHORT.get(key, key), "graded": len(part),
                       "passing": sum(row["tier"] in PASSING_TIERS for row in part),
                       "kept": round(float(np.median([row["final"] / row["origin_final"] for row in part])), 4),
                       "drawdown": round(float(np.median([(row["dd"] - row["origin_dd"]) * 100 for row in part])), 1),
                       "gap": round(float(np.median([((row["gap"] or 0) - (row["origin_gap"] or 0)) * 100 for row in part])), 1),
                       "less": sum((row["gap"] or 0) > (row["origin_gap"] or 0) for row in part),
                       "frontier": [row["trial"] for row in part if row["frontier"]],
                       "best": max(part, key=lambda row: row["final"])})
    return output


def grade_guards(research: str | Path) -> Path:
    """2026-10-25 (research_method §7「空頭減碼第二版」): every guarded version (BEAR_GUARD_ORIGINS x BEAR_GUARDS, matched
    by settings) against its original — the newest run on this engine — saved for the dashboard and the window."""
    from quant_platform.research.blend import parse_spec
    from quant_platform.research.daily import ENGINE_VERSION, PASSING_TIERS, DailyRule, tier
    from quant_platform.research.downside import frontier, profile, table
    from quant_platform.research.registry import TrialRegistry, newest

    research = Path(research)
    records = TrialRegistry(research / "trials.jsonl").records()
    latest_runs = newest([record for record in records if record.kind == "candidate" and record.period == "recent"
                          and str(record.metrics.get("engine", "")).startswith(ENGINE_VERSION.split("+")[0])])
    by_hash = {record.spec_hash: record for record in latest_runs.values()}
    by_trial = {record.trial_id: record for record in records}

    def spec_of(record):
        return json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]

    def gap(record):
        report = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))
        return (profile(report.get("curve") or {}).get("worst_fall") or {}).get("gap")

    front = frontier(table(research), PASSING_TIERS)
    rows = []
    for key, _word, fields, trials in guard_ways():
        for trial in trials:
            origin = by_hash.get(by_trial[trial].spec_hash)
            spec = spec_of(by_trial[trial])
            if spec.get("kind") == "blend":
                rule = parse_spec({**spec, "sleeves": [{**sleeve, "rule": {**sleeve["rule"], **fields}}
                                                       for sleeve in spec["sleeves"]]})
            else:
                rule = DailyRule.model_validate({**spec, **fields})
            record = by_hash.get(rule.rule_hash)
            if record is None or origin is None:
                continue
            rows.append({"trial": record.trial_id, "origin": trial, "guard": key, "tier": tier(record.metrics)[0],
                         "origin_tier": tier(origin.metrics)[0], "final": record.metrics["final_value"],
                         "origin_final": origin.metrics["final_value"], "dd": record.metrics["max_drawdown"],
                         "origin_dd": origin.metrics["max_drawdown"], "gap": gap(record), "origin_gap": gap(origin),
                         "frontier": record.spec_hash in front, "spec_hash": record.spec_hash})
    folder = research / "stress"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"guards-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
                                "ways": guard_summary(rows), "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def protect(history: str | Path, research: str | Path, job=None) -> Path:
    """2026-10-24 (research_method §7「空頭減碼檢驗」): each strategy again with each way of holding fewer stocks in a
    bear (OVERLAYS), from START through the same scenarios and from 2015-06, against the strategy as it is."""
    from quant_platform.research.blend import BlendAccount, parse_spec
    from quant_platform.research.costs import broker_costs
    from quant_platform.research.daily import (
        RECENT_END, RECENT_START, DailyRule, account_parking, daily_weights, exposure_schedule, simulate_daily,
    )
    from quant_platform.research.decisions import rankings

    from quant_platform.research.daily import BEAR_GUARD_ORIGINS

    strategies = fixed(research, BEAR_GUARD_ORIGINS)
    data, fp = load_long(history)
    costs = broker_costs("cathay")
    end = RECENT_END
    base = simulate_daily(data, None, costs, START, end)
    base_units = _units(base)
    falls = bears(base.days, base_units)
    recent = simulate_daily(data, None, costs, RECENT_START, end)
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "start": START.isoformat(),
              "recent_start": RECENT_START.isoformat(), "helps": HELPS, "keeps": KEEPS, "worse": WORSE,
              "overlays": {key: {"label": label, **(off_share(fp, fields["market_filter"], base.days)
                                                     if "market_filter" in fields else {})}
                           for key, (label, fields) in OVERLAYS.items()},
              "scenarios": [{"high": base.days[item["high"]].isoformat(), "low": base.days[item["low"]].isoformat(),
                             "fall": round(item["fall"], 4), **kind(item)} for item in falls],
              "base_final": round(base.final_value, 2), "base_recent_final": round(recent.final_value, 2), "rows": []}
    variants = {"none": {}, **{key: fields for key, (_label, fields) in OVERLAYS.items()}}
    for number, item in enumerate(strategies):
        spec = item["spec"]
        row = {**{k: v for k, v in item.items() if k != "spec"}, "variants": {}}
        try:
            if spec.get("kind") == "blend":
                account = BlendAccount(data, fp, parse_spec(spec), costs, START, end)

                def make(fields, account=account, spec=spec):
                    if any(key in fields for key in RANKED):  # the market filter and the stop decide the targets
                        sleeves = [{**sleeve, "rule": {**sleeve["rule"], **fields}} for sleeve in spec["sleeves"]]
                        changed = BlendAccount(data, fp, parse_spec({**spec, "sleeves": sleeves}), costs, START, end)
                    else:
                        changed = _overlaid(account, data, fp, costs, fields) if fields else account
                    return lambda first: changed.run(first, end)
            else:
                rule = DailyRule.model_validate(spec)
                ranks = rankings(fp, rule, START, end)
                weights, parked = daily_weights(fp, rule, ranks), account_parking(data, fp, rule, costs)

                def make(fields, rule=rule, ranks=ranks, weights=weights, parked=parked):
                    changed = DailyRule.model_validate({**rule.model_dump(), **fields}) if fields else rule
                    mine, sizes = ranks, weights
                    if any(key in fields for key in RANKED):  # the market filter and the stop decide the targets
                        mine = rankings(fp, changed, START, end)
                        sizes = daily_weights(fp, changed, mine)
                    exposure = exposure_schedule(data, fp, changed, costs)
                    return lambda first: simulate_daily(data, changed, costs, first, end, mine, weights=sizes,
                                                        parked=parked, exposure=exposure)
        except Exception as exc:                            # noqa: BLE001 - one strategy's failure is reported
            report["rows"].append({**row, "error": f"{type(exc).__name__}: {exc}"[:300]})
            continue
        for key, fields in variants.items():
            if job:
                job.update(done=number, total=len(strategies), current=f"#{item['trial']}：{OVERLAYS[key][0] if key in OVERLAYS else '不加'}")
            if fields and _has(spec, fields):
                row["variants"][key] = {"skip": "本來就有"}
                continue
            try:
                runner = make(fields)
                result = runner(START)
                units = _units(result)
                if result.days != base.days:
                    units = np.interp([day.toordinal() for day in base.days], [day.toordinal() for day in result.days], units)
                judged = judge(base.days, units, base_units, falls)
                summary = verdict(judged)
                later = runner(RECENT_START)
            except Exception as exc:                        # noqa: BLE001
                row["variants"][key] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
                print(item["trial"], key, row["variants"][key]["error"], flush=True)
                continue
            row["variants"][key] = {
                "final": round(result.final_value, 2), "recent_final": round(later.final_value, 2),
                "max_drawdown": round(float((units / np.maximum.accumulate(units) - 1).min()), 4),
                "bears": [{"fall": bear["fall"], "gap": bear["gap"], "days": bear["days"], "held": bear["held"]}
                          for bear in judged],
                **{k: summary[k] for k in ("held_count", "held_2008", "held_all", "scenario_count")}}
            print(item["trial"], key, round(result.final_value), round(later.final_value), summary["held_count"],
                  summary["held_2008"], flush=True)
        report["rows"].append(row)
    rows = [row for row in report["rows"] if not row.get("error")]
    report["verdicts"] = {key: overlay_verdict(rows, key, report["base_final"]) for key in OVERLAYS}
    for position, scenario in enumerate(report["scenarios"]):     # each scenario: how many held, with each way
        scenario["held"] = {key: sum(1 for row in rows if row["variants"].get(key, {}).get("bears")
                                     and row["variants"][key]["bears"][position]["held"]) for key in variants}
    folder = Path(research) / "stress"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"protect-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
