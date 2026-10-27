"""2026-10-28 (research_method §7「選股條件的緩衝」, the next step after two versions without a consistent gain): was each
forced sale right? Every forward account's sale for 「不再符合選股條件」 (the stock fell under the price or turnover
line, out of the large caps, or lost its score) against the holdings the account kept that day: the sold stock's total
return over the next 20 and 60 sessions minus the kept holdings' average. Negative means the sale avoided a loss the
holdings did not have — the lines earned their keep. Read weekly (the audit pipeline); judged in 2027-01.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
FORCED = "不再符合選股條件"
HORIZONS = (20, 60)


def events(decisions: dict) -> list[dict]:
    """(account, day, sold, kept) for every forced sale in the forward decisions (``decisions.json``: per account the
    holdings it wanted each session, ``days``, and why each stock came in or left, ``why``)."""
    output = []
    for account, item in decisions.items():
        days = item.get("days") or {}
        for day, notes in sorted((item.get("why") or {}).items()):
            sold = sorted(symbol for symbol, text in notes.items() if FORCED in text)
            if not sold:
                continue
            kept = sorted(symbol for symbol in days.get(day) or [] if symbol not in notes)
            output.append({"account": account, "day": day, "sold": sold, "kept": kept})
    return output


def follow(fp, found: list[dict]) -> dict[str, object]:
    """Each forced sale's return over HORIZONS from the decision's close, minus the kept holdings' average; the
    summary per horizon over the sales whose horizon has passed (each stock and day counted once too)."""
    rows = []
    for event in found:
        start = fp.index.get(date.fromisoformat(event["day"]))
        if start is None:
            continue
        kept = [symbol for symbol in event["kept"] if symbol in fp.row]
        for symbol in event["sold"]:
            if symbol not in fp.row:
                continue
            row = {"account": event["account"], "day": event["day"], "symbol": symbol}
            for horizon in HORIZONS:
                end = start + horizon
                if end >= len(fp.sessions):
                    continue
                base = fp.price(symbol, start)
                if not base or not np.isfinite(base):
                    continue
                gained = fp.price(symbol, end) / base - 1
                others = [fp.price(other, end) / fp.price(other, start) - 1 for other in kept
                          if fp.price(other, start) and np.isfinite(fp.price(other, start))]
                row[f"sold_{horizon}"] = round(gained, 5)
                row[f"gap_{horizon}"] = round(gained - float(np.mean(others)), 5) if others else None
            rows.append(row)
    summary = {}
    for horizon in HORIZONS:
        gaps = [row[f"gap_{horizon}"] for row in rows if row.get(f"gap_{horizon}") is not None]
        unique = {(row["day"], row["symbol"]): row[f"gap_{horizon}"] for row in rows if row.get(f"gap_{horizon}") is not None}
        summary[str(horizon)] = {"sales": len(gaps), "stocks": len(unique),
                                 "median_gap": round(float(np.median(gaps)), 5) if gaps else None,
                                 "mean_gap": round(float(np.mean(gaps)), 5) if gaps else None,
                                 "right": round(sum(gap < 0 for gap in gaps) / len(gaps), 4) if gaps else None,
                                 "stock_median_gap": round(float(np.median(list(unique.values()))), 5) if unique else None}
    return {"events": len(found), "sales": len(rows), "horizons": summary, "rows": rows}


def run(history: str | Path, research: str | Path) -> Path:
    from quant_platform.research import daily

    research = Path(research)
    decisions = json.loads((research / "forward" / "stocks" / "decisions.json").read_text(encoding="utf-8"))
    _data, fp = daily.load(Path(history))
    result = follow(fp, events(decisions))
    path = research / "forward" / "stocks" / f"forced-followup-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
                                "through": fp.sessions[-1].isoformat(), **result}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path
