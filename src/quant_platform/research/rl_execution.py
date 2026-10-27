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

    def last_by(stamp: str) -> float:
        upto = [row for row in bars if str(row["minute"]) <= stamp]
        return float(upto[-1]["close"]) if upto else float(bars[0]["close"])

    # 2026-10-28: the intraday odd-lot market first matches at 09:10 — the earliest an odd-lot order can fill; RL v7
    # adds 09:30 (the first half hour known) and 13:00 (the last half hour ahead)
    found = {"open": float(bars[0]["open"]), "minute": float(bars[0]["close"]), "close": float(bars[-1]["close"]),
             "ten": last_by("09:10:59"), "half": last_by("09:30:59"), "thirteen": last_by("13:00:59")}
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


def gains(frame: pd.DataFrame, at: str = "minute") -> pd.DataFrame:
    """Each stock-day's gain against the close: as a sale (price / close - 1) and as a buy (close / price - 1), at the
    open auction and at ``at`` (the first minute; "ten": 09:10, the odd lots' first match, with the move since the open
    as one more state)."""
    out = frame.copy()
    if at == "ten":
        out["minute"] = out["ten"]
        out["since_open"] = out["ten"] / out["open"] - 1
    out["sell_open"] = out["open"] / out["close"] - 1
    out["sell_minute"] = out["minute"] / out["close"] - 1
    out["buy_open"] = out["close"] / out["open"] - 1
    out["buy_minute"] = out["close"] / out["minute"] - 1
    return out


def walk_forward(frame: pd.DataFrame, first_test: int = FIRST_TEST, features=FEATURES) -> pd.DataFrame:
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
                                                  random_state=0).fit(train[list(features)], train[f"{side}_minute"])
            predicted = model.predict(test[list(features)])
            robot = np.where(predicted > 0, test[f"{side}_minute"], 0.0)
            fixed = test["sell_open"] if side == "sell" else np.zeros(len(test))     # the rules: sell at the open, buy at the close
            for (_index, row), mine, theirs, now in zip(test.iterrows(), robot, fixed, predicted > 0):
                rows.append({"day": row["day"], "year": year, "side": side, "robot": float(mine), "fixed": float(theirs),
                             "minute": bool(now)})
    return pd.DataFrame(rows)


SELL_FEATURES = FEATURES + ("since_open",)
BUY_FEATURES = FEATURES + ("first_half", "since_open_13")


def multi(frame: pd.DataFrame, first_test: int = FIRST_TEST) -> pd.DataFrame:
    """2026-10-28 (research_method §7「RL 第七版」): odd lots. A sale decided at 09:10 (the odd lots' first match: what
    「開盤賣」 gets them) — sell then, at 13:00 or at the close: a value model per later time (its gain over 09:10)
    learned from the years before, the best predicted taken (09:10 when neither is positive). A buy decided at 13:00,
    knowing the first half hour and the morning — buy then or at the close (the rules' way)."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    data = frame.copy()
    data["since_open"] = data["ten"] / data["open"] - 1
    data["first_half"] = data["half"] / data["open"] - 1
    data["since_open_13"] = data["thirteen"] / data["open"] - 1
    data["sell_thirteen"] = data["thirteen"] / data["ten"] - 1
    data["sell_close"] = data["close"] / data["ten"] - 1
    data["buy_thirteen"] = data["close"] / data["thirteen"] - 1

    def predict(train, test, target, features):
        model = HistGradientBoostingRegressor(max_iter=150, learning_rate=0.05, max_leaf_nodes=7, min_samples_leaf=40,
                                              random_state=0).fit(train[list(features)], train[target])
        return model.predict(test[list(features)])

    rows = []
    for year in sorted(data["year"].unique()):
        if year < first_test:
            continue
        train, test = data[data["year"] < year], data[data["year"] == year]
        if len(train) < 300 or not len(test):
            continue
        later = np.column_stack([np.zeros(len(test)), predict(train, test, "sell_thirteen", SELL_FEATURES),
                                 predict(train, test, "sell_close", SELL_FEATURES)])
        best = np.argmax(later, axis=1)
        sold = np.choose(best, [np.zeros(len(test)), test["sell_thirteen"].to_numpy(), test["sell_close"].to_numpy()])
        now = predict(train, test, "buy_thirteen", BUY_FEATURES) > 0
        bought = np.where(now, test["buy_thirteen"].to_numpy(), 0.0)
        for day, value, choice, late, closing in zip(test["day"], sold, best, test["sell_thirteen"], test["sell_close"]):
            rows.append({"day": day, "year": int(year), "side": "sell", "robot": float(value), "fixed": 0.0,
                         "minute": bool(choice), "choice": ("09:10", "13:00", "close")[choice],
                         "always_1300": float(late), "always_close": float(closing)})
        for day, value, chosen, late in zip(test["day"], bought, now, test["buy_thirteen"]):
            rows.append({"day": day, "year": int(year), "side": "buy", "robot": float(value), "fixed": 0.0,
                         "minute": bool(chosen), "choice": "13:00" if chosen else "close", "always_1300": float(late),
                         "always_close": 0.0})
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
    if "always_1300" in choices:            # RL v7: the fixed ways too, and neither side may lose
        for side in ("sell", "buy", "both"):
            part = choices if side == "both" else choices[choices["side"] == side]
            found[side]["always"] = {name: stats(part[f"always_{name}"]) for name in ("1300", "close")}
            found[side]["choices"] = {name: int(count) for name, count in part["choice"].value_counts().items()}
        found["passed"] = bool(found["passed"] and all((found[side]["lead"].get("mean") or 0) >= 0 for side in ("sell", "buy")))
    return found


def run(history: str | Path, research: str | Path, at: str = "minute") -> Path:
    from quant_platform.research import daily

    _data, fp = daily.load(Path(history))
    if at == "multi":                       # RL v7: odd lots, 09:10 / 13:00 / the close
        frame = table(fp, history)
        choices = multi(frame)
    else:
        frame = gains(table(fp, history), at)
        choices = walk_forward(frame, features=FEATURES + (("since_open",) if at == "ten" else ()))
    result = summary(choices)
    folder = Path(research) / "rl"
    folder.mkdir(parents=True, exist_ok=True)
    tag = {"ten": "-0910", "multi": "-multi"}.get(at, "")
    path = folder / f"execution{tag}-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "days": int(len(frame)),
                                "years": sorted(int(year) for year in frame["year"].unique()), **result},
                               ensure_ascii=False, indent=1), encoding="utf-8")
    return path
