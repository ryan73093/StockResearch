"""2026-10-22 (使用者：還有什麼要加入分析的; 0050 大跌時策略不要跌更多): do market-wide signals the platform already
has foresee 0050's next months — a diagnostic written before the run (research_method §7「大盤訊號」).

Signals, each known only from the session after its date: the whole market's margin maintenance ratio (FinMind
TaiwanTotalExchangeMarginMaintenance, from 2002) and its 20-session change; the foreign investors' net open interest
in TAIEX futures (TaiwanFuturesInstitutionalInvestors TX, long minus short contracts, 2018-06 to the download) and its
20-session change. Each day's value is placed by its percentile among every earlier value (no later data), cut in
fifths. Outcomes from 0050's total return index: the next 20 and 60 sessions, and whether 0050 falls 10% or more from
that day's level within 60 sessions.

Useful (the criterion written first) when in both 2015-06..2020-09 and 2020-10..2026-08 the worst fifth's next-60
return is at least ``GAP`` below the period's average and its 10%-fall rate at least ``RATE`` times the average."""

from __future__ import annotations

import gzip
import json
from bisect import bisect_right
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
PERIODS = {"2015-06～2020-09": (date(2015, 6, 1), date(2020, 9, 30)), "2020-10～2026-08": (date(2020, 10, 1), date(2026, 8, 31))}
HORIZONS = (20, 60)
FALL = 0.10
GAP, RATE = 0.03, 1.5


def _rows(path: Path) -> list[dict]:
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8")).get("data") or [] if path.is_file() else []


def series(history: str | Path) -> dict[str, dict[date, float]]:
    """date → value of each signal (on the date the data is about; used from the next session)."""
    raw = Path(history) / "raw" / "finmind"
    margin = {date.fromisoformat(row["date"][:10]): float(row["TotalExchangeMarginMaintenance"])
              for row in _rows(raw / "TaiwanTotalExchangeMarginMaintenance" / "all.json.gz")
              if row.get("TotalExchangeMarginMaintenance")}
    futures: dict[date, float] = {}
    for row in _rows(raw / "TaiwanFuturesInstitutionalInvestors" / "TX.json.gz"):
        if row.get("institutional_investors") == "外資":
            futures[date.fromisoformat(row["date"][:10])] = float(row["long_open_interest_balance_volume"]) - float(
                row["short_open_interest_balance_volume"])
    return {"融資維持率": margin, "外資台指期淨部位": futures}


def _changes(values: dict[date, float], sessions: int = 20) -> dict[date, float]:
    days = sorted(values)
    return {day: values[day] - values[days[index - sessions]] for index, day in enumerate(days) if index >= sessions}


def percentiles(values: dict[date, float]) -> dict[date, float]:
    """Each day's value as the share of earlier values at or below it (the first 250 days have none)."""
    output, seen = {}, []
    for day in sorted(values):
        if len(seen) >= 250:
            output[day] = bisect_right(seen, values[day]) / len(seen)
        position = bisect_right(seen, values[day])
        seen.insert(position, values[day])
    return output


def study(index: dict[date, float], signals: dict[str, dict[date, float]]) -> dict[str, object]:
    days = sorted(index)
    level = np.array([index[day] for day in days])
    position = {day: number for number, day in enumerate(days)}
    found: dict[str, object] = {}
    for name, values in signals.items():
        known = sorted(values)
        ranks = percentiles(values)
        report = {}
        for period, (start, end) in PERIODS.items():
            buckets: dict[int, list[tuple[float, float, bool]]] = {fifth: [] for fifth in range(5)}
            for day in days:
                if not (start <= day <= end):
                    continue
                at = position[day]
                if at + max(HORIZONS) >= len(days):
                    continue
                before = bisect_right(known, day) - 1          # the latest signal dated before this session
                while before >= 0 and known[before] >= day:
                    before -= 1
                if before < 0 or known[before] not in ranks:
                    continue
                fifth = min(int(ranks[known[before]] * 5), 4)
                ahead = level[at + 1:at + 61]
                buckets[fifth].append((level[at + 20] / level[at] - 1, level[at + 60] / level[at] - 1,
                                       bool(ahead.min() / level[at] - 1 <= -FALL)))
            everything = [item for items in buckets.values() for item in items]
            if not everything:
                continue
            mean60 = float(np.mean([item[1] for item in everything]))
            rate = float(np.mean([item[2] for item in everything]))
            report[period] = {"days": len(everything), "mean_60": round(mean60, 4), "fall_rate": round(rate, 4),
                              "fifths": [{"fifth": fifth + 1, "days": len(items),
                                          "mean_20": round(float(np.mean([item[0] for item in items])), 4) if items else None,
                                          "mean_60": round(float(np.mean([item[1] for item in items])), 4) if items else None,
                                          "fall_rate": round(float(np.mean([item[2] for item in items])), 4) if items else None}
                                         for fifth, items in buckets.items()]}
        verdicts = []
        for period, item in report.items():
            worst = min((fifth for fifth in item["fifths"] if fifth["mean_60"] is not None), key=lambda fifth: fifth["mean_60"])
            verdicts.append(worst["mean_60"] <= item["mean_60"] - GAP and item["fall_rate"] > 0
                            and (worst["fall_rate"] or 0) >= RATE * item["fall_rate"])
            item["worst"] = worst["fifth"]
        found[name] = {"periods": report, "useful": bool(verdicts) and len(verdicts) == len(PERIODS) and all(verdicts)}
    return found


def run(history: str | Path, research: str | Path) -> Path:
    import pandas as pd

    frame = pd.read_parquet(Path(history) / "total_return" / "0050.parquet")
    index = {value.date() if hasattr(value, "date") else value: float(level)
             for value, level in zip(pd.to_datetime(frame["date"]), frame["total_return_index"])}
    raw = series(history)
    signals = {}
    for name, values in raw.items():
        signals[f"{name}（水準）"] = values
        signals[f"{name}（20 日變化）"] = _changes(values)
    found = study(index, signals)
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "fall": FALL, "gap": GAP, "rate": RATE,
              "signals": found, "ranges": {name: [str(min(values)), str(max(values))] for name, values in raw.items() if values}}
    folder = Path(research) / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"market-signals-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
