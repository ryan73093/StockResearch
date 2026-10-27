"""2026-10-28 (research_method §7「RL 第六版：成交執行」; 使用者：RL 機器人有沒有要繼續優化): nine RL versions on what
to hold lost to simple fixed choices; execution is where the literature and the platform's own minute bars show
structure — overnight gaps partly reverse during the day (Lou, Polk & Skouras 2019, "A tug of war"), and the strongest
rules' sells fetched 0.46% more at the next open than the next close, their buys paid 0.27% less at the close.

The robot acts one minute after the open, when the gap is known: fill now (the 09:00 bar's close) or wait for the close.
On the 4,000 stock-days whose minute bars were fetched for the timing study (2021 on; the side was not kept, so each
day is studied as a sale and as a buy — the same prices, the gain mirrored), a value model per side learns from the
years before each year (walk-forward) what filling now gains against the close given the gap, the day before's return,
the 20-session volatility and the 20-session momentum, and fills now when that is positive. Written before the run:
from 2023 it must gain at least 0.05% a fill more than the rules' fixed way (sell at the opening auction, buy at the
close), t >= 2, on both sides combined, to be built (forward and live only: the minute bars start 2018-12).
"""

from __future__ import annotations

import gzip
import json
import math
import statistics
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

TAIPEI = ZoneInfo("Asia/Taipei")
FEATURES = ("gap", "yesterday", "volatility", "momentum")
WORTH = 0.0005
FIRST_TEST = 2023


def bars_of(path: Path) -> dict[str, float] | None:
    """The open (the 09:00 auction), the first minute's close and the close of one stock-day's minute bars."""
    rows = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    bars = sorted((row for row in rows if str(row.get("minute", "")) < "14:00:00"), key=lambda row: row["minute"])
    if len(bars) < 2:
        return None
    found = {"open": float(bars[0]["open"]), "minute": float(bars[0]["close"]), "close": float(bars[-1]["close"])}
    return found if min(found.values()) > 0 else None


def table(fp, base: str | Path) -> pd.DataFrame:
    """One row per cached stock-day: the prices and the state known one minute after the open."""
    folder = Path(base) / "raw" / "finmind" / "TaiwanStockKBar"
    closes = fp.panel.close
    rows = []
    for path in sorted(folder.glob("*.json.gz")):
        code, _sep, day = path.name.removesuffix(".json.gz").partition("-")
        symbol = f"{code}.TW"
        position = fp.index.get(date.fromisoformat(day))
        if symbol not in fp.row or position is None or position < 21:
            continue
        found = bars_of(path)
        if not found:
            continue
        row_index = fp.row[symbol]
        before = closes[row_index, position - 1]
        history = closes[row_index, position - 21: position]
        if not np.isfinite(before) or before <= 0 or not np.isfinite(history).all():
            continue
        returns = np.diff(history) / history[:-1]
        rows.append({"day": day, "year": int(day[:4]), "code": code, **found,
                     "gap": found["open"] / before - 1, "yesterday": float(returns[-1]),
                     "volatility": float(np.std(returns)), "momentum": float(history[-1] / history[0] - 1)})
    return pd.DataFrame(rows)


def gains(frame: pd.DataFrame) -> pd.DataFrame:
    """Each stock-day's gain against the close: as a sale (price / close - 1) and as a buy (close / price - 1), at the
    open auction and at the first minute."""
    out = frame.copy()
    out["sell_open"] = out["open"] / out["close"] - 1
    out["sell_minute"] = out["minute"] / out["close"] - 1
    out["buy_open"] = out["close"] / out["open"] - 1
    out["buy_minute"] = out["close"] / out["minute"] - 1
    return out


def walk_forward(frame: pd.DataFrame, first_test: int = FIRST_TEST) -> pd.DataFrame:
    """Per test year, per side: a value model of filling at the first minute (its gain against the close) learned
    from the years before; fill then when the predicted gain is positive, else at the close (gain 0)."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    rows = []
    for year in sorted(frame["year"].unique()):
        if year < first_test:
            continue
        train, test = frame[frame["year"] < year], frame[frame["year"] == year]
        if len(train) < 300 or not len(test):
            continue
        for side in ("sell", "buy"):
            model = HistGradientBoostingRegressor(max_iter=150, learning_rate=0.05, max_leaf_nodes=7, min_samples_leaf=40,
                                                  random_state=0).fit(train[list(FEATURES)], train[f"{side}_minute"])
            predicted = model.predict(test[list(FEATURES)])
            robot = np.where(predicted > 0, test[f"{side}_minute"], 0.0)
            fixed = test["sell_open"] if side == "sell" else np.zeros(len(test))     # the rules: sell at the open, buy at the close
            for (_index, row), mine, theirs, now in zip(test.iterrows(), robot, fixed, predicted > 0):
                rows.append({"day": row["day"], "year": year, "side": side, "robot": float(mine), "fixed": float(theirs),
                             "minute": bool(now)})
    return pd.DataFrame(rows)


def summary(choices: pd.DataFrame) -> dict[str, object]:
    def stats(values):
        values = list(values)
        if len(values) < 2:
            return {"count": len(values)}
        mean, spread = statistics.fmean(values), statistics.stdev(values)
        return {"count": len(values), "mean": round(mean, 6), "t": round(mean / (spread / math.sqrt(len(values))), 2) if spread else None}

    found = {}
    for side in ("sell", "buy", "both"):
        part = choices if side == "both" else choices[choices["side"] == side]
        found[side] = {"robot": stats(part["robot"]), "fixed": stats(part["fixed"]),
                       "lead": stats(part["robot"] - part["fixed"]), "minute_share": round(float(part["minute"].mean()), 3)}
    lead = found["both"]["lead"]
    found["passed"] = bool((lead.get("mean") or 0) >= WORTH and (lead.get("t") or 0) >= 2)
    return found


def run(history: str | Path, research: str | Path) -> Path:
    from quant_platform.research import daily

    _data, fp = daily.load(Path(history))
    frame = gains(table(fp, history))
    choices = walk_forward(frame)
    result = summary(choices)
    folder = Path(research) / "rl"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"execution-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "days": int(len(frame)),
                                "years": sorted(int(year) for year in frame["year"].unique()), **result},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return path
