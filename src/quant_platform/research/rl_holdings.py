"""2026-11-27 (docs/research/results.md「RL 第十一版：機器人看規則當下的持股挑規則」, written before the run): the
tenth version's robot knew a rule only by its own past months and lost to every fixed way of picking, twice. This
one also knows what each rule wants to hold at the month's end — where those stocks stand among the day's stocks
(momentum, the last month, volatility, beta, the 52-week high, turnover, the 200-day trend), how many they are, how
much sits in one industry, how much of last month's list is still there, and how crowded they are (the share of the
rules holding the same stocks).

The reports keep no holdings, so ``lists`` computes them with the engine's own ``daily.daily_rankings`` for every
distinct sleeve rule (a blend's sleeves are rules) and keeps the list in force on each month's last session. That
is the list the rule wants, not the account's shares; a guard's exposure is not in it.

Everything else is the tenth version's (``rl_select``): PICK rules a month, a family's cap, the cost of a rule
replaced, the boosted trees first fitted at the end of 2020-09 and every January after, the same three fixed ways
and the same judgment. The value is of the next month; a rule held stays within the top KEEP (500, for the robot
and the fixed ways alike). Beside it, the same robot without the holdings: what the new information adds.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from quant_platform.research import rl_select
from quant_platform.research.rl_select import TAIPEI

KEEP = 500
FOLDER = "holdings"
PLACES = ("momentum_12_1", "momentum_3", "reversal_1", "low_volatility_60", "low_beta_250", "high_52w", "liquidity",
          "trend_200")
HELD = (*(f"held_{name}" for name in PLACES), "held_count", "held_industry", "held_kept", "held_crowded")
_STATE: dict[str, object] = {}


def key_of(part: dict) -> str:
    return hashlib.sha1(json.dumps(part, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


def parts_of(research: str | Path, data: dict) -> tuple[dict[str, dict], list[list[tuple[str, float]]]]:
    """The distinct sleeve rules of the rules in ``data`` (key → definition) and, per rule, its (key, share)."""
    from quant_platform.research.registry import TrialRegistry, newest

    research = Path(research)
    records = {record.trial_id: record
               for record in newest([record for record in TrialRegistry(research / "trials.jsonl").records()
                                     if record.kind == "candidate" and record.period == "recent"]).values()}
    parts: dict[str, dict] = {}
    rules: list[list[tuple[str, float]]] = []
    for trial in data["trials"]:
        spec = json.loads((research / "reports" / records[trial].report_file).read_text(encoding="utf-8"))["spec"]
        sleeves = spec.get("sleeves") or []
        pieces = ([(sleeve.get("rule") or {}, float(sleeve.get("share") or 0.0)) for sleeve in sleeves]
                  or [(spec, 1.0)])
        mine = []
        for part, share in pieces:
            key = key_of(part)
            parts[key] = part
            mine.append((key, share))
        rules.append(mine)
    return parts, rules


def month_ends(sessions: list, first, last) -> dict[str, int]:
    """Month → the position of its last session, for the months that end within first..last."""
    ends: dict[str, int] = {}
    for position, day in enumerate(sessions):
        if first <= day <= last:
            ends[f"{day:%Y-%m}"] = position
    return ends


def in_force(ranks: dict, sessions: list, ends: dict[str, int]) -> dict[str, list[str]]:
    """The list decided last on or before each month's last session (nothing decided yet: no month)."""
    days = sorted(ranks)
    found, at = {}, -1
    for month, position in ends.items():
        while at + 1 < len(days) and days[at + 1] <= sessions[position]:
            at += 1
        if at >= 0:
            found[month] = list(ranks[days[at]])
    return found


def _begin(history: str) -> None:
    import os

    from quant_platform.research import daily

    try:                                                     # research runs below everything else (D-025)
        import psutil

        psutil.Process(os.getpid()).nice(psutil.IDLE_PRIORITY_CLASS)
    except Exception:  # noqa: BLE001 - the priority is a courtesy, never a reason to stop
        pass
    _STATE["data"], _STATE["fp"] = daily.load(history)
    fp = _STATE["fp"]
    _STATE["ends"] = month_ends(fp.sessions, daily.RECENT_START, daily.RECENT_END)


def _one(item: tuple[str, dict]) -> tuple[str, dict | None, str]:
    from quant_platform.research import daily

    key, part = item
    fp = _STATE["fp"]
    try:
        rule = daily.DailyRule(**part)
        ranks = daily.daily_rankings(fp, rule, daily.RECENT_START, daily.RECENT_END)
        lists = in_force(ranks, fp.sessions, _STATE["ends"])
        return key, {month: [fp.row[symbol] for symbol in names] for month, names in lists.items()}, ""
    except Exception as error:  # noqa: BLE001 - one rule's failure is written down, the others go on
        return key, None, f"{type(error).__name__}: {error}"[:300]


def stored(folder: Path) -> dict[str, dict[str, list[int]]]:
    found: dict[str, dict[str, list[int]]] = {}
    for path in sorted(folder.glob("lists-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                found[row["key"]] = row["lists"]
    return found


def lists(research: str | Path, history: str | Path, workers: int = 6, progress=None) -> dict[str, object]:
    """Every distinct sleeve rule's month-end lists, kept in ``rl/holdings/lists-*.jsonl`` (a run that stopped goes
    on from what is kept)."""
    from concurrent.futures import ProcessPoolExecutor

    folder = Path(research) / "rl" / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    parts, _rules = parts_of(research, rl_select.load(research))
    have = stored(folder)
    todo = [(key, part) for key, part in parts.items() if key not in have]
    # the rules that read the same factors beside each other: a worker's factor matrices are computed once
    todo.sort(key=lambda item: json.dumps(item[1].get("factors"), sort_keys=True, ensure_ascii=False))
    failed: dict[str, str] = {}
    path = folder / f"lists-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.jsonl"
    done = 0
    if todo:
        with ProcessPoolExecutor(max_workers=workers, initializer=_begin, initargs=(str(history),)) as pool, \
                path.open("a", encoding="utf-8") as output:
            for key, found, error in pool.map(_one, todo, chunksize=8):
                done += 1
                if found is None:
                    failed[key] = error
                else:
                    output.write(json.dumps({"key": key, "lists": found}, ensure_ascii=False) + "\n")
                    output.flush()
                if progress is not None and done % 50 == 0:
                    progress(done, len(todo), len(failed))
    (folder / "failed.json").write_text(json.dumps(failed, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"parts": len(parts), "kept_before": len(have), "computed": done - len(failed), "failed": len(failed),
            "folder": str(folder)}


def places(fp, position: int) -> np.ndarray:
    """stocks × PLACES: each stock's place (0 to 1) among the day's stocks with a value; none where it has none."""
    columns = []
    for name in PLACES:
        column = fp.matrix(name)[:, position].astype(float)
        known = np.isfinite(column)
        place = np.full(len(column), np.nan)
        if known.sum() > 1:
            place[known] = column[known].argsort().argsort() / (known.sum() - 1)
        columns.append(place)
    return np.stack(columns, axis=1)


def held_features(month_lists: list[dict[str, list[int]]], rules: list[list[tuple[int, float]]], months: list[str],
                  stock_places: dict[str, np.ndarray], industries: list[str]) -> np.ndarray:
    """months × rules × HELD from each part's list in force at each month's end (``month_lists[part][month]``),
    a rule's parts weighted by their shares. A rule with nothing held that month: its count 0, the rest none."""
    codes = {name: number for number, name in enumerate(sorted(set(industries)))}
    industry = np.array([codes[name] for name in industries])
    count_parts, width = len(month_lists), len(PLACES)
    output = np.full((len(months), len(rules), len(HELD)), np.nan)
    for number, month in enumerate(months):
        table = stock_places[month]
        current = [np.array(sorted(set(item.get(month) or [])), dtype=int) for item in month_lists]
        before = ([set(item.get(months[number - 1]) or []) for item in month_lists] if number else None)
        holders = np.zeros(len(table))
        for mine in rules:                                   # a rule holds a stock when any of its parts does
            stocks = (current[mine[0][0]] if len(mine) == 1
                      else np.unique(np.concatenate([current[part] for part, _share in mine])))
            holders[stocks] += 1
        holders /= max(len(rules), 1)
        part = np.full((count_parts, len(HELD)), np.nan)
        for index, stocks in enumerate(current):
            part[index, width] = len(stocks)
            if not len(stocks):
                continue
            with np.errstate(all="ignore"):
                part[index, :width] = np.nanmean(table[stocks], axis=0) if np.isfinite(table[stocks]).any() else np.nan
            part[index, width + 1] = np.bincount(industry[stocks]).max() / len(stocks)
            if before is not None and before[index]:
                part[index, width + 2] = len(before[index].intersection(stocks.tolist())) / len(stocks)
            part[index, width + 3] = holders[stocks].mean()
        for row, mine in enumerate(rules):
            if len(mine) == 1:
                output[number, row] = part[mine[0][0]]
                continue
            values = np.stack([part[index] for index, _share in mine])
            shares = np.array([share for _index, share in mine])[:, None] * np.isfinite(values)
            total = shares.sum(axis=0)
            with np.errstate(all="ignore"):
                output[number, row] = np.where(total > 0, np.nansum(values * shares, axis=0) / total, np.nan)
    return output


def robot(data: dict, start: int, block, ahead: int = 1):
    """``rl_select.robot`` on the inputs ``block(at)`` gives (rules × inputs; the family's column the same)."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    state: dict[str, object] = {"model": None, "fitted_at": None}

    def score(at: int) -> np.ndarray:
        if state["model"] is None or (data["months"][at + 1].endswith("-01") and state["fitted_at"] != at):
            learn = list(range(rl_select.WARM - 1, at - ahead + 1))
            inputs = np.concatenate([block(month) for month in learn])
            target = np.concatenate([rl_select._place((data["returns"][:, month + 1:month + 1 + ahead]
                                                       - data["base"][None, month + 1:month + 1 + ahead]).sum(axis=1))
                                     for month in learn])
            model = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                                  min_samples_leaf=200, l2_regularization=1.0, random_state=0,
                                                  categorical_features=[rl_select.FEATURES.index("family")])
            state["model"], state["fitted_at"] = model.fit(inputs, target), at
        return state["model"].predict(block(at))

    return score


def follows(data: dict, held: np.ndarray, start: int) -> tuple[dict, dict]:
    """The accounts (robot with the holdings, robot without, the three fixed ways) and their summaries."""
    kept: dict[int, np.ndarray] = {}

    def plain(at: int) -> np.ndarray:
        if at not in kept:
            kept[at] = rl_select.features(data, at)
        return kept[at]

    def wide(at: int) -> np.ndarray:
        return np.concatenate([plain(at), held[at]], axis=1)

    active = data["returns"] - data["base"][None, :]
    ways = {"robot": robot(data, start, wide), "robot_plain": robot(data, start, plain),
            "last12": lambda at: active[:, at - 11:at + 1].sum(axis=1),
            "to_date": lambda at: active[:, :at + 1].sum(axis=1), "all": lambda at: None}
    runs = {name: rl_select.follow(data, start, way, KEEP) for name, way in ways.items()}
    base = data["base"][start + 1:]
    return runs, {name: rl_select.summary(item, base) for name, item in runs.items()}


def relations(data: dict, held: np.ndarray, start: int) -> dict[str, float]:
    """Each holdings input's rank correlation with the next month's place against 0050, month by month, averaged."""
    active = data["returns"] - data["base"][None, :]
    found: dict[str, list[float]] = {name: [] for name in HELD}
    for at in range(start, len(data["months"]) - 1):
        target = rl_select._place(active[:, at + 1])
        for column, name in enumerate(HELD):
            values = held[at][:, column]
            known = np.isfinite(values)
            if known.sum() > 50 and np.ptp(values[known]) > 0:
                found[name].append(float(np.corrcoef(rl_select._place(values[known]), target[known])[0, 1]))
    return {name: round(float(np.mean(values)), 4) if values else None for name, values in found.items()}


def run(research: str | Path, history: str | Path) -> Path:
    from quant_platform.research import daily

    research = Path(research)
    data = rl_select.load(research)
    parts, rules = parts_of(research, data)
    have = stored(research / "rl" / FOLDER)
    missing = sorted(key for key in parts if key not in have)
    usable = [row for row, mine in enumerate(rules) if all(key in have for key, _share in mine)]
    total = len(rules)
    if len(usable) < len(rules):                             # a rule whose list could not be computed is left out
        data = {**data, "returns": data["returns"][usable], "trials": [data["trials"][row] for row in usable],
                "names": [data["names"][row] for row in usable],
                "families": [data["families"][row] for row in usable], "guard": data["guard"][usable],
                "blend": data["blend"][usable]}
        rules = [rules[row] for row in usable]
    keys = sorted({key for mine in rules for key, _share in mine})
    place = {key: number for number, key in enumerate(keys)}
    _legacy, fp = daily.load(history)
    ends = month_ends(fp.sessions, daily.RECENT_START, daily.RECENT_END)
    months = data["months"]
    stock_places = {month: places(fp, ends[month]) for month in months}
    industries = [fp.industry(symbol) for symbol in fp.symbols]
    held = held_features([have[key] for key in keys], [[(place[key], share) for key, share in mine] for mine in rules],
                         months, stock_places, industries)
    start = months.index(rl_select.FIRST) - 1
    runs, found = follows(data, held, start)
    judgment = rl_select.judge(found)
    last = [{"trial": data["trials"][rule], "name": data["names"][rule], "family": data["families"][rule]}
            for rule in runs["robot"]["held"]]
    result = {"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "rules": len(rules),
              "rules_left_out": total - len(rules),
              "parts": len(keys), "parts_missing": len(missing), "first": rl_select.FIRST, "last": months[-1],
              "pick": rl_select.PICK, "keep": KEEP, "months_ahead": 1, "per_family": rl_select.PER_FAMILY,
              "cost": rl_select.COST, "features": [*rl_select.FEATURES, *HELD],
              "base_multiple": round(float(np.prod(1 + data["base"][start + 1:])), 4), **found,
              "judgment": judgment, "passed": judgment["passed"],
              "better_than_plain": bool(found["robot"]["multiple"] > found["robot_plain"]["multiple"]),
              "relations": relations(data, held, start),
              "held_known": round(float(np.isfinite(held[start:, :, len(PLACES)]).mean()), 4),
              "held_empty": round(float((held[start:, :, len(PLACES)] == 0).mean()), 4), "robot_holds_last": last}
    folder = research / "rl"
    path = folder / f"holdings-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
