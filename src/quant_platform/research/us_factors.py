"""2026-11-21 (docs/research/results.md「美股個股、美國就業／通膨／利率／景氣數據」第二部分): factors read from the US side.

``us_sox_beta`` — how closely a stock follows the US semiconductor index: the slope of its daily return on what the
index did between the US sessions known at consecutive Taiwan sessions, over the last 120 Taiwan sessions (at least 100
with both). A US session dated D closes at 05:00 Taipei on D+1, so a Taiwan session reads only US sessions dated before
it. With 2015 on, the highest fifth was ahead of the average stock by 6.1 points a year before 2020-10 and 10.9 after
(the afternoon's reading, 2021 on, had caught the three flat years). It is an exposure, not a lead: when the index falls
this fifth falls more — the novelty gate and the falling-week gates judge it as any factor.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left
from datetime import date
from pathlib import Path

import numpy as np

US_FACTORS = {
    "us_sox_beta": "跟費城半導體連動高（這檔日報酬對前一晚費城半導體指數報酬的 120 日斜率；美股日期早於當天的才用）",
}
SOX = "^SOX"
WINDOW, LEAST = 120, 100


def _closes(history: str | Path, symbol: str = SOX) -> dict[date, float]:
    from quant_platform.research.history import us

    path = Path(history) / "raw" / "yahoo_us" / f"{us._file(symbol)}.json.gz"
    if not path.is_file():
        return {}
    return {day: row["close"] for day, row in us.read_bars(path).items()}


def lead(closes: dict[date, float], sessions: list[date]) -> np.ndarray:
    """Per Taiwan session: the index's return between the last US session dated before the previous Taiwan session and
    the last one dated before this one (0 when no US session lies between; NaN before the series starts)."""
    days = sorted(closes)
    last = [bisect_left(days, day) - 1 for day in sessions]
    output = np.full(len(sessions), np.nan)
    for t in range(1, len(sessions)):
        a, b = last[t - 1], last[t]
        if a >= 0 and b >= 0:
            output[t] = closes[days[b]] / closes[days[a]] - 1
    return output


def rolling_slope(x: np.ndarray, y: np.ndarray, window: int = WINDOW, least: int = LEAST) -> np.ndarray:
    """Slope of each column of y (sessions x stocks) on x over the last ``window`` sessions, from the days both are
    known; NaN with fewer than ``least``."""
    known = np.isfinite(y) & np.isfinite(x)[:, None]
    xk = np.where(known, x[:, None], 0.0)
    yk = np.where(known, y, 0.0)

    def rolled(values: np.ndarray) -> np.ndarray:
        total = np.cumsum(values, axis=0)
        output = total.copy()
        output[window:] = total[window:] - total[:-window]
        return output

    n, sx, sy = rolled(known.astype(float)), rolled(xk), rolled(yk)
    sxx, sxy = rolled(xk * xk), rolled(xk * yk)
    with np.errstate(invalid="ignore", divide="ignore"):
        slope = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    slope[(n < least) | ~np.isfinite(slope)] = np.nan
    return slope


def matrix(panel, history: str | Path, factor: str) -> np.ndarray:
    """sessions x symbols."""
    if factor not in US_FACTORS:
        raise KeyError(factor)
    sessions = list(panel.sessions)
    closes = _closes(history)
    if not closes:
        return np.full((len(sessions), len(panel.symbols)), np.nan)
    prices = panel._prices.to_numpy(dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        returns = np.vstack([np.full((1, prices.shape[1]), np.nan), prices[1:] / prices[:-1] - 1])
    return rolling_slope(lead(closes, sessions), returns)


def digest(history: str | Path, end: date) -> str | None:
    """The index's closes up to ``end`` — a run's input beside the prices."""
    closes = _closes(history)
    if not closes:
        return None
    text = "".join(f"{day}:{value:.4f};" for day, value in sorted(closes.items()) if day <= end)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def refresh(history: str | Path, day: date, get=None) -> dict[str, object]:
    """Before a Taiwan session's orders: the index must hold the last US weekday before ``day`` (a US holiday shows as
    stale and is harmless — nothing traded). One request when it does not; an answer that fails keeps the file, and the
    report says how stale it is (the slope over 120 sessions moves little in a day, but the page must not hide it)."""
    from datetime import timedelta

    from quant_platform.research.history import us

    wanted = day - timedelta(days=1)
    while wanted.weekday() >= 5:
        wanted -= timedelta(days=1)
    closes = _closes(history)
    fetched = False
    if not closes or max(closes) < wanted:
        try:
            fetched = bool(us.fetch_prices(Path(history), pause=0.0, today=day, only=(SOX,), **({"get": get} if get else {})))
        except Exception:  # noqa: BLE001
            fetched = False
        closes = _closes(history)
    last = max(closes) if closes else None
    return {"wanted": wanted.isoformat(), "last": last.isoformat() if last else None, "fetched": fetched,
            "stale": last is None or last < wanted}
