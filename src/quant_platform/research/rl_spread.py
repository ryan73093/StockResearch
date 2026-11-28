"""RL 第十二版 (2026-11-28): nothing is learned. The fixed way that won versions ten and eleven (the rules ahead of
0050 by the most from 2015-06 to that month) held 20 rules that had 48 to 73 different stocks between them; rules of
different families shared 34 to 57 percent (``docs/research/results.md``, 規則的名單實際重疊多少). This version walks
the same order and skips a rule whose month-end list shares LIMIT or more with a rule already chosen.

Same rules, months, cost, PICK, KEEP and PER_FAMILY as ``rl_holdings``; the lists are the ones it stored. Offline;
the multiples only compare the ways with each other."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from quant_platform.research import rl_holdings, rl_select
from quant_platform.research.rl_holdings import KEEP
from quant_platform.research.rl_select import TAIPEI

LIMIT = 0.5                 # judged; same-family rules shared 75-80%, other families 34-57%, random pairs 22-33%
OTHERS = (0.3, 0.7)         # run beside it for reference only: never chosen between


def shared(first: frozenset, second: frozenset) -> float:
    """Stocks both hold over the smaller list's count; a rule holding nothing that month shares nothing."""
    if not first or not second:
        return 0.0
    return len(first & second) / min(len(first), len(second))


def choose(scores: np.ndarray, held: list[int], families: list[str], stocks, limit: float | None,
           keep: int = KEEP) -> tuple[list[int], int]:
    """``rl_select.choose`` with one more reason to pass a rule over: its list (``stocks(rule)``) shares ``limit``
    or more with a rule already chosen. Short of PICK at the order's end, the places left are filled in the same
    order without that reason. Returns the rules and how many were filled that way."""
    order = np.argsort(-scores)
    zone = set(order[:keep].tolist())
    walk = [item for item in held if item in zone] + order.tolist()
    chosen, counts, passed = [], {}, []
    for rule in walk:
        if len(chosen) >= rl_select.PICK:
            break
        if rule in chosen or counts.get(families[rule], 0) >= rl_select.PER_FAMILY:
            continue
        if limit is not None and any(shared(stocks(rule), stocks(other)) >= limit for other in chosen):
            passed.append(rule)
            continue
        chosen.append(rule)
        counts[families[rule]] = counts.get(families[rule], 0) + 1
    filled = 0
    for rule in passed:
        if len(chosen) >= rl_select.PICK:
            break
        if rule in chosen or counts.get(families[rule], 0) >= rl_select.PER_FAMILY:
            continue
        chosen.append(rule)
        counts[families[rule]] = counts.get(families[rule], 0) + 1
        filled += 1
    return chosen, filled


def follow(data: dict, start: int, score, stocks_at, limit: float | None) -> dict[str, object]:
    """``rl_select.follow`` with ``choose`` above; also how many different stocks the chosen rules hold together."""
    held, months, gains, swapped, different, filled = [], [], [], [], [], 0
    for at in range(start, len(data["months"]) - 1):
        stocks = stocks_at(at)
        chosen, extra = choose(score(at), held, data["families"], stocks, limit)
        turned = len(set(chosen) - set(held)) / rl_select.PICK
        gains.append(float(data["returns"][chosen, at + 1].mean()) - rl_select.COST * turned)
        months.append(data["months"][at + 1])
        swapped.append(turned)
        different.append(len(frozenset().union(*[stocks(rule) for rule in chosen])))
        filled += extra
        held = chosen
    return {"months": months, "gains": gains, "swapped": swapped, "held": held, "different": different,
            "filled": filled}


def ready(research: Path) -> tuple[dict, list[list[tuple[str, float]]], dict, int]:
    """The rules whose lists are all stored (as ``rl_holdings.run`` leaves the others out), their parts, the lists."""
    data = rl_select.load(research)
    _parts, rules = rl_holdings.parts_of(research, data)
    have = rl_holdings.stored(research / "rl" / rl_holdings.FOLDER)
    usable = [row for row, mine in enumerate(rules) if all(key in have for key, _share in mine)]
    total = len(rules)
    if len(usable) < total:
        data = {**data, "returns": data["returns"][usable], "trials": [data["trials"][row] for row in usable],
                "names": [data["names"][row] for row in usable],
                "families": [data["families"][row] for row in usable], "guard": data["guard"][usable],
                "blend": data["blend"][usable]}
        rules = [rules[row] for row in usable]
    return data, rules, have, total


def follows(data: dict, rules: list[list[tuple[str, float]]], have: dict, start: int) -> tuple[dict, dict]:
    """The accounts (the to-date way with each limit, the three fixed ways) and their summaries."""
    kept: dict[tuple[int, int], frozenset] = {}

    def stocks_at(at: int):
        month = data["months"][at]

        def stocks(rule: int) -> frozenset:
            if (at, rule) not in kept:
                kept[(at, rule)] = frozenset().union(*[have[key].get(month) or [] for key, _share in rules[rule]])
            return kept[(at, rule)]

        return stocks

    active = data["returns"] - data["base"][None, :]

    def to_date(at: int) -> np.ndarray:
        return active[:, :at + 1].sum(axis=1)

    runs = {"spread": follow(data, start, to_date, stocks_at, LIMIT),
            **{f"spread_{round(limit * 100)}": follow(data, start, to_date, stocks_at, limit) for limit in OTHERS},
            "to_date": follow(data, start, to_date, stocks_at, None),
            "last12": follow(data, start, lambda at: active[:, at - 11:at + 1].sum(axis=1), stocks_at, None),
            "all": rl_select.follow(data, start, lambda at: None, KEEP)}
    base = data["base"][start + 1:]
    found = {}
    for name, item in runs.items():
        found[name] = rl_select.summary(item, base)
        if "different" in item:
            found[name]["different_stocks"] = round(float(np.mean(item["different"])), 1)
            found[name]["filled"] = item["filled"]
    return runs, found


def run(research: str | Path) -> Path:
    research = Path(research)
    data, rules, have, total = ready(research)
    months = data["months"]
    start = months.index(rl_select.FIRST) - 1
    runs, found = follows(data, rules, have, start)
    judgment = rl_select.judge({"robot": found["spread"], **{name: found[name] for name in ("last12", "to_date", "all")}})
    last = [{"trial": data["trials"][rule], "name": data["names"][rule], "family": data["families"][rule]}
            for rule in runs["spread"]["held"]]
    result = {"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "rules": len(rules),
              "rules_left_out": total - len(rules), "first": rl_select.FIRST, "last": months[-1],
              "pick": rl_select.PICK, "keep": KEEP, "per_family": rl_select.PER_FAMILY, "cost": rl_select.COST,
              "limit": LIMIT, "others": list(OTHERS),
              "base_multiple": round(float(np.prod(1 + data["base"][start + 1:])), 4), **found,
              "judgment": judgment, "passed": judgment["passed"], "spread_holds_last": last}
    path = research / "rl" / f"spread-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
