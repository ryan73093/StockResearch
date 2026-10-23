"""2026-10-23 (使用者：2015 到現在賺的錢跟 2020 到現在差那麼多，代表 2020 年後交易市場的心態、交易方式都不一樣；
每個時期有沒有去看同樣的交易策略是否還適用？2015～2016 的在 2020～2021 適用嗎？哪些前期賺很大、到後面變得比較不賺):
every rule's own account split into periods — the same rule, the same money — against 0050 in the same period.

From each rule's newest backtest (2015-06 → 2026-09, 30 萬＋每月 1 萬; the report's month-end curve), each calendar year's
return net of the money put in (pool.yearly_returns), compounded within a period and annualized; the period's excess is
the rule's annualized return less 0050's. Early is 2015-06～2019, late 2020～2026-09. Kept until the registry changes."""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

PERIODS = (("2015-06～2016", 2015, 2016), ("2017～2019", 2017, 2019), ("2020～2021", 2020, 2021),
           ("2022～2023", 2022, 2023), ("2024～2026-09", 2024, 2026))
EARLY, LATE = (2015, 2019), (2020, 2026)
BIG = 0.05                              # "明顯": 5 points a year above (or below) 0050
_CACHE: dict[str, object] = {}


def _annual(yearly: dict[str, dict], first: int, last: int) -> tuple[float | None, float | None, int]:
    """The rule's and 0050's annualized return over the calendar years first..last, and the months."""
    rule = base = 1.0
    months = 0
    for year in range(first, last + 1):
        item = yearly.get(str(year))
        if not item:
            continue
        rule *= 1 + item["strategy"]
        base *= 1 + item["benchmark"]
        months += item["months"]
    if months < 6:
        return None, None, months
    return rule ** (12 / months) - 1, base ** (12 / months) - 1, months


def periods(yearly: dict[str, dict]) -> dict[str, dict[str, float | None]]:
    output = {}
    for label, first, last in PERIODS:
        rule, base, months = _annual(yearly, first, last)
        output[label] = {"rule": rule, "base": base, "excess": None if rule is None else rule - base, "months": months}
    for label, (first, last) in (("early", EARLY), ("late", LATE)):
        rule, base, months = _annual(yearly, first, last)
        output[label] = {"rule": rule, "base": base, "excess": None if rule is None else rule - base, "months": months}
    return output


def _rank(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda index: values[index])
    ranks = [0.0] * len(values)
    for position, index in enumerate(order):
        ranks[index] = float(position)
    return ranks


def spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3:
        return None
    a, b = _rank(left), _rank(right)
    mean_a, mean_b = statistics.fmean(a), statistics.fmean(b)
    top = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    bottom = math.sqrt(sum((x - mean_a) ** 2 for x in a) * sum((y - mean_b) ** 2 for y in b))
    return round(top / bottom, 3) if bottom else None


def summarize(rows: list[dict]) -> dict[str, object]:
    """Per period: rules, the share beating 0050, the median and best excess; early winners still winning late."""
    table = {}
    for label, _first, _last in PERIODS:
        values = [row["periods"][label]["excess"] for row in rows if row["periods"][label]["excess"] is not None]
        table[label] = {"rules": len(values), "beat": round(sum(value > 0 for value in values) / len(values), 3) if values else None,
                        "median": round(statistics.median(values), 4) if values else None,
                        "best": round(max(values), 4) if values else None}
    both = [row for row in rows if row["periods"]["early"]["excess"] is not None and row["periods"]["late"]["excess"] is not None]
    early_winners = [row for row in both if row["periods"]["early"]["excess"] > 0]
    big = [row for row in both if row["periods"]["early"]["excess"] >= BIG]
    return {"periods": table, "rules": len(rows),
            "early_winners": len(early_winners),
            "still_winning": sum(row["periods"]["late"]["excess"] > 0 for row in early_winners),
            "big_early": len(big), "big_still": sum(row["periods"]["late"]["excess"] >= BIG for row in big),
            "big_faded": sum(row["periods"]["late"]["excess"] <= 0 for row in big),
            "correlation": spearman([row["periods"]["early"]["excess"] for row in both],
                                    [row["periods"]["late"]["excess"] for row in both]),
            "median_early": round(statistics.median([row["periods"]["early"]["excess"] for row in both]), 4) if both else None,
            "median_late": round(statistics.median([row["periods"]["late"]["excess"] for row in both]), 4) if both else None}


def analyze(research: str | Path) -> dict[str, object]:
    """Every main-track 100%-stock rule (newest run) by period, by family, and the rules that faded or rose."""
    from quant_platform.research.pool import pool_view, yearly_returns

    research = Path(research)
    registry = research / "trials.jsonl"
    key = (str(research.resolve()), registry.stat().st_mtime_ns if registry.is_file() else 0)
    if _CACHE.get("key") == key:
        return _CACHE["value"]                          # type: ignore[return-value]
    saved = research / "eras" / "rules.json"            # spec hash + report file → its periods, read once
    stored: dict[str, dict] = {}
    if saved.is_file():
        try:
            stored = json.loads(saved.read_text(encoding="utf-8"))
        except ValueError:
            stored = {}
    rows, fresh = [], {}
    for row in pool_view(research, basis="seed")["daily"]:
        if row.get("uses_0050") or str(row.get("start") or "2015-06-01") > "2015-06-01" or not row.get("report_file"):
            continue
        marker = f"{row['spec_hash']}:{row['report_file']}"
        found = stored.get(marker)
        if found is None:
            try:
                report = json.loads((research / "reports" / str(row["report_file"])).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            yearly = yearly_returns(report.get("curve") or {})
            if not yearly:
                continue
            found = periods(yearly)
        fresh[marker] = found
        rows.append({"spec_hash": row["spec_hash"], "name": row["name"], "tier": row["tier"],
                     "family": (row.get("category") or {}).get("family") or "其他",
                     "final_value": row.get("final_value"), "periods": found})
    if fresh.keys() != stored.keys():
        saved.parent.mkdir(parents=True, exist_ok=True)
        partial = saved.with_suffix(".partial")
        partial.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
        partial.replace(saved)
    families = {}
    for row in rows:
        families.setdefault(row["family"], []).append(row)
    usable = [row for row in rows if row["periods"]["early"]["excess"] is not None and row["periods"]["late"]["excess"] is not None]
    faded = sorted((row for row in usable if row["periods"]["early"]["excess"] >= BIG and row["periods"]["late"]["excess"] <= 0),
                   key=lambda row: -row["periods"]["early"]["excess"])
    rose = sorted((row for row in usable if row["periods"]["early"]["excess"] <= 0 and row["periods"]["late"]["excess"] >= BIG),
                  key=lambda row: -row["periods"]["late"]["excess"])
    value = {"labels": [label for label, _first, _last in PERIODS], "all": summarize(rows),
             "families": {name: summarize(members) for name, members in families.items()},
             "rows": rows, "faded": faded, "rose": rose,
             "benchmark": {label: next((row["periods"][label]["base"] for row in rows
                                        if row["periods"][label]["base"] is not None), None)
                           for label in [*(label for label, _f, _l in PERIODS), "early", "late"]}}
    _CACHE.update(key=key, value=value)
    return value
