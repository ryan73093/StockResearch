"""2026-11-27 (docs/research/results.md「RL 第十版：機器人每個月挑規則」, written before the run; 使用者 11/26：RL 到底
優化了沒): nine versions learned a decision on top of one rule's scores (allocation, position, number of stocks, when
to switch, when to trade) and none beat the fixed way. This one's actions are the rules themselves: at each month's
end the robot holds PICK of the registered rules for the next month, a rule held stays while it is within the top
KEEP, at most PER_FAMILY of a family, and every rule replaced costs COST of its share. What it knows of a rule is
only what that month's end knew — its months against 0050 so far, its family and guard (from its definition), the
market's months. Its value of a rule (the place of its next month against 0050 among all rules) is a boosted-tree
model refitted every January on the months before. A contextual bandit in which every arm's reward is seen
afterwards; the holdings carried from month to month are its state.

Offline, on the month-end curves the engine kept for every rule (reports/*.json "curve"). Written before the run:
from FIRST the robot's account must end with at least the money of each fixed way of picking with the same
holdings rule — the best of the last 12 months, the best since 2015-06 so far, every rule equally — and beat the
best of those in at least YEARS_BETTER of the calendar years after 2020.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
FIRST = "2020-10"                       # the first month held (decided at the end of 2020-09)
PICK = 20
KEEP = 60
PER_FAMILY = 5
COST = 0.005                            # a rule replaced: its whole list sold and bought (RL 第四、五版 the same)
YEARS_BETTER = 4
WARM = 36                               # months of a rule's record before it is valued
ENGINE = "daily-1.6.1"
FEATURES = ("active_1", "active_3", "active_6", "active_12", "active_12_place", "active_3_place", "spread_12",
            "return_12", "behind_12", "beta_36", "down_36", "family", "guard", "blend", "base_1", "base_3", "base_12")


def load(research: str | Path) -> dict[str, object]:
    """Every rule on the current engine that is all stocks: months, month returns (rules × months), 0050's, and
    what its definition says (family, guard, blend)."""
    from quant_platform.research.categories import classify
    from quant_platform.research.downside import returns
    from quant_platform.research.registry import TrialRegistry, newest

    research = Path(research)
    records = newest([record for record in TrialRegistry(research / "trials.jsonl").records()
                      if record.kind == "candidate" and record.period == "recent"])
    months, base, rows, trials, families, guards, blends, names = None, None, [], [], [], [], [], []
    for record in records.values():
        if not str((record.metrics or {}).get("engine") or "").startswith(ENGINE):
            continue
        path = research / "reports" / record.report_file
        if not path.is_file():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        spec, curve = report.get("spec") or {}, report.get("curve") or {}
        sleeves = [sleeve.get("rule") or {} for sleeve in spec.get("sleeves") or []]
        if float(spec.get("core") or 0) > 0 or any(float(item.get("core") or 0) > 0 for item in sleeves):
            continue                                              # 100% stocks only (D-002)
        family = classify(spec)["family"]
        if family.startswith("反向"):
            continue
        its_months, rule, its_base = returns(curve)
        if months is None:
            months, base = its_months, its_base
        if its_months != months or not np.isfinite(rule).all():
            continue
        parts = sleeves or [spec]
        rows.append(rule)
        trials.append(record.trial_id)
        names.append(record.spec_name)
        families.append(family)
        guards.append(float(any(item.get("vol_scale") == "taiex_half" or item.get("market_filter") == "taiex_200"
                                for item in parts)))
        blends.append(float(bool(sleeves)))
    return {"months": months, "base": base, "returns": np.array(rows), "trials": trials, "names": names,
            "families": families, "guard": np.array(guards), "blend": np.array(blends)}


def _place(values: np.ndarray) -> np.ndarray:
    """Each value's place among the others, 0 to 1."""
    order = values.argsort().argsort().astype(float)
    return order / max(len(values) - 1, 1)


def features(data: dict, at: int) -> np.ndarray:
    """rules × FEATURES from the months up to and including ``at`` (index into the month returns)."""
    rule, base = data["returns"][:, :at + 1], data["base"][:at + 1]
    active = rule - base[None, :]
    last = {span: active[:, -span:].sum(axis=1) for span in (1, 3, 6, 12)}
    units = np.cumprod(1 + active[:, -12:], axis=1)
    behind = (units / np.maximum.accumulate(units, axis=1) - 1).min(axis=1)
    window, market = rule[:, -36:], base[-36:]
    spread = float(market.var()) or 1.0
    beta = ((window - window.mean(axis=1, keepdims=True)) * (market - market.mean())[None, :]).mean(axis=1) / spread
    fell = market < 0
    down = active[:, -36:][:, fell].mean(axis=1) if fell.any() else np.zeros(len(rule))
    codes = {name: number for number, name in enumerate(sorted(set(data["families"])))}
    family = np.array([codes[name] for name in data["families"]], dtype=float)
    count = len(rule)
    columns = [last[1], last[3], last[6], last[12], _place(last[12]), _place(last[3]), active[:, -12:].std(axis=1),
               np.prod(1 + rule[:, -12:], axis=1) - 1, behind, beta, down, family, data["guard"], data["blend"],
               np.full(count, base[-1]), np.full(count, base[-3:].sum()), np.full(count, base[-12:].sum())]
    return np.stack(columns, axis=1)


def choose(scores: np.ndarray, held: list[int], families: list[str]) -> list[int]:
    """The month's holdings: the ones held still within the top KEEP stay, the rest filled from the top, at most
    PER_FAMILY of a family."""
    order = np.argsort(-scores)
    zone = set(order[:KEEP].tolist())
    chosen, counts = [], {}
    for rule in [item for item in held if item in zone] + order.tolist():
        if len(chosen) >= PICK:
            break
        if rule in chosen or counts.get(families[rule], 0) >= PER_FAMILY:
            continue
        chosen.append(rule)
        counts[families[rule]] = counts.get(families[rule], 0) + 1
    return chosen


def follow(data: dict, start: int, score) -> dict[str, object]:
    """The account that holds ``choose`` of ``score(at)`` each month from the month after ``start``; costs taken."""
    held, months, gains, swapped = [], [], [], []
    for at in range(start, len(data["months"]) - 1):
        scores = score(at)
        if scores is None:                                       # every rule equally, nothing to replace
            gain, turned = float(data["returns"][:, at + 1].mean()), 0.0
        else:
            chosen = choose(scores, held, data["families"])
            turned = len(set(chosen) - set(held)) / PICK
            gain = float(data["returns"][chosen, at + 1].mean()) - COST * turned
            held = chosen
        months.append(data["months"][at + 1])
        gains.append(gain)
        swapped.append(turned)
    return {"months": months, "gains": gains, "swapped": swapped, "held": held}


def summary(run: dict, base: np.ndarray) -> dict[str, object]:
    gains = np.array(run["gains"])
    units = np.cumprod(1 + gains)
    years = {}
    for month, gain in zip(run["months"], gains, strict=True):
        years[month[:4]] = (1 + years.get(month[:4], 0.0)) * (1 + gain) - 1
    fell = base < 0
    return {"multiple": round(float(units[-1]), 4),
            "max_drawdown": round(float((units / np.maximum.accumulate(np.concatenate([[1.0], units]))[1:] - 1).min()), 4),
            "years": {year: round(value, 4) for year, value in years.items()},
            "months_behind_0050": int((gains < base).sum()), "months": len(gains),
            "active_when_0050_fell": round(float((gains - base)[fell].mean()), 5) if fell.any() else None,
            "swapped_a_month": round(float(np.mean(run["swapped"][1:])), 4) if len(run["swapped"]) > 1 else 0.0}


def robot(data: dict, start: int):
    """The robot's value of each rule at a month's end; refitted at ``start`` and every January after on the
    (rule, month) samples whose next month had ended."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    kept: dict[int, np.ndarray] = {}
    state: dict[str, object] = {"model": None, "fitted_at": None}

    def block(at: int) -> np.ndarray:
        if at not in kept:
            kept[at] = features(data, at)
        return kept[at]

    def score(at: int) -> np.ndarray:
        if state["model"] is None or (data["months"][at + 1].endswith("-01") and state["fitted_at"] != at):
            learn = list(range(WARM - 1, at))                    # a sample's next month is at most ``at``: known
            inputs = np.concatenate([block(month) for month in learn])
            target = np.concatenate([_place(data["returns"][:, month + 1] - data["base"][month + 1]) for month in learn])
            model = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                                  min_samples_leaf=200, l2_regularization=1.0, random_state=0,
                                                  categorical_features=[FEATURES.index("family")])
            state["model"], state["fitted_at"] = model.fit(inputs, target), at
        return state["model"].predict(block(at))

    return score


def judge(found: dict[str, dict]) -> dict[str, object]:
    """The registered judgment on the summaries (robot, last12, to_date, all)."""
    fixed = {name: found[name] for name in ("last12", "to_date", "all")}
    best = max(fixed, key=lambda name: fixed[name]["multiple"])
    years = [year for year in found["robot"]["years"] if year > "2020"]
    better = sum(1 for year in years if found["robot"]["years"][year] > fixed[best]["years"][year])
    money = all(found["robot"]["multiple"] >= item["multiple"] for item in fixed.values())
    return {"best_fixed": best, "money_not_less": bool(money), "years_better": better, "years": len(years),
            "passed": bool(money and better >= YEARS_BETTER)}


def run(research: str | Path) -> Path:
    data = load(research)
    months = data["months"]
    start = months.index(FIRST) - 1
    active = data["returns"] - data["base"][None, :]
    ways = {"robot": robot(data, start),
            "last12": lambda at: active[:, at - 11:at + 1].sum(axis=1),
            "to_date": lambda at: active[:, :at + 1].sum(axis=1),
            "all": lambda at: None}
    base = data["base"][start + 1:]
    runs = {name: follow(data, start, way) for name, way in ways.items()}
    found = {name: summary(item, base) for name, item in runs.items()}
    held = [{"trial": data["trials"][rule], "name": data["names"][rule], "family": data["families"][rule]}
            for rule in runs["robot"]["held"]]
    families: dict[str, int] = {}
    for name in data["families"]:
        families[name] = families.get(name, 0) + 1
    result = {"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "rules": len(data["trials"]),
              "families": families, "first": FIRST, "last": months[-1], "pick": PICK, "keep": KEEP,
              "per_family": PER_FAMILY, "cost": COST, "features": list(FEATURES),
              "base_multiple": round(float(np.prod(1 + base)), 4), **found, "judgment": judge(found),
              "passed": judge(found)["passed"], "robot_holds_last": held}
    folder = Path(research) / "rl"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"select-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
