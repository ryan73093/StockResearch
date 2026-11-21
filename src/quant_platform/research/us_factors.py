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
# 2026-11-22 (docs/research/results.md「模型 V」, written before the run): readings a model reads beside its factors —
# not rule factors (measured 11/21: the peer momentum is strong only after 2020-10, the gap not yet followed is
# reversed, the rate and dollar sensitivities are not strong; a tree may still use them together)
US_FEATURES = {
    "us_peer_mom_20": "美股同業 20 日動能（最跟得上的美股同業群近 20 個美股交易日贏 S&P 500 多少 × 這檔的跟隨斜率）",
    "us_peer_mom_60": "美股同業 60 日動能（同上，60 個美股交易日）",
    "us_peer_gap_5": "美股同業漲了、這檔還沒跟上的差距（同業群近 5 日 × 斜率，減這檔自己近 5 日比大盤多的）",
    "us_rate_move_60": "利率敏感度 × 美國 10 年期殖利率近 60 日變化",
    "us_dollar_move_60": "美元敏感度 × 美元指數近 60 日變化",
}
US_MARKET = ("market:us_sox_20", "market:us_vix", "market:us_yield2_60", "market:us_curve", "market:us_dollar_60",
             "market:us_hot")
PEER_WINDOW, PEER_LEAST, PEER_FOLLOWS = 250, 200, 0.1


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


def rolling_slope(x: np.ndarray, y: np.ndarray, window: int = WINDOW, least: int = LEAST, correlation: bool = False):
    """Slope of each column of y (sessions x stocks) on x over the last ``window`` sessions, from the days both are
    known; NaN with fewer than ``least``. ``correlation``: (slope, correlation over the same days)."""
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
    if correlation:
        syy = rolled(yk * yk)
        with np.errstate(invalid="ignore", divide="ignore"):
            related = (n * sxy - sx * sy) / np.sqrt((n * sxx - sx * sx) * (n * syy - sy * sy))
        related[(n < least) | ~np.isfinite(related)] = np.nan
        return slope, related
    return slope


def matrix(panel, history: str | Path, factor: str) -> np.ndarray:
    """sessions x symbols."""
    if factor in US_FEATURES:
        return features(panel, history)[factor]
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


HOT_REVISED = ("NEWORDER", "RSAFS", "INDPRO", "JTSJOL")     # market_signals.HOT_READINGS: the statistics behind them
HOT_DAILY = ("DGS2",)


def refresh_hot(history: str | Path, get=None) -> dict[str, object]:
    """Before a Taiwan session's orders, for an account whose share held reads the US overheating count (vol_scale
    us_hot_*): the vintages published since the last look and the 2-year yield. A request that fails keeps the files
    (the count moves on a publication, a few days a month)."""
    from quant_platform.research.history import us

    extra = {"get": get} if get else {}
    try:
        vintages = us.fetch_revised(Path(history), pause=0.0, only=HOT_REVISED, **extra)
        yields = us.fetch_daily(Path(history), pause=0.0, only=HOT_DAILY, **extra)
    except Exception as error:  # noqa: BLE001
        return {"fetched": False, "error": str(error)[:200]}
    return {"fetched": True, "vintages": vintages, "yields": yields}


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


def _known(days: list[date], sessions: list[date]) -> np.ndarray:
    """Per Taiwan session: the position in ``days`` of the last one dated before it (-1: none)."""
    return np.array([bisect_left(days, day) - 1 for day in sessions])


def _between(values: np.ndarray, last: np.ndarray, difference: bool) -> np.ndarray:
    """What a US series (by US session) did between the US sessions known at consecutive Taiwan sessions."""
    output = np.full(len(last), np.nan)
    for t in range(1, len(last)):
        a, b = last[t - 1], last[t]
        if a >= 0 and b >= 0 and np.isfinite(values[a]) and np.isfinite(values[b]) and (difference or values[a]):
            output[t] = values[b] - values[a] if difference else values[b] / values[a] - 1
    return output


def _back(values: np.ndarray, last: np.ndarray, steps: int, difference: bool) -> np.ndarray:
    """Per Taiwan session: the series' change over the last ``steps`` US sessions known."""
    output = np.full(len(last), np.nan)
    for t, b in enumerate(last):
        if b - steps >= 0 and np.isfinite(values[b]) and np.isfinite(values[b - steps]) and (difference or values[b - steps]):
            output[t] = values[b] - values[b - steps] if difference else values[b] / values[b - steps] - 1
    return output


def features(panel, history: str | Path) -> dict[str, np.ndarray]:
    """US_FEATURES, each sessions x symbols, computed together once per panel. A stock's excess return is its daily
    return less the day's average stock's; a US group's is its members' average return less the S&P 500's. The peer
    group is the one whose previous-night excess the stock's own followed most closely over PEER_WINDOW sessions (at
    least PEER_LEAST known, correlation at least PEER_FOLLOWS — otherwise no reading)."""
    cache = panel.__dict__.setdefault("_us_features", {})
    if cache:
        return cache
    from quant_platform.research.history import us

    sessions = list(panel.sessions)
    shape = (len(sessions), len(panel.symbols))
    american = us.prices(Path(history))
    if "^GSPC" not in american:
        cache.update({name: np.full(shape, np.nan) for name in US_FEATURES})
        return cache
    calendar = sorted(american["^GSPC"])
    at = {day: number for number, day in enumerate(calendar)}

    def level(symbol: str, field: str = "adjusted", carry: bool = False) -> np.ndarray:
        values = np.full(len(calendar), np.nan)
        for day, row in (american.get(symbol) or {}).items():
            if day in at and row.get(field) is not None:
                values[at[day]] = row[field]
        if carry:                                              # a missing day carries the last value
            for index in range(1, len(values)):
                if not np.isfinite(values[index]):
                    values[index] = values[index - 1]
        return values

    def change(values: np.ndarray) -> np.ndarray:
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.concatenate([[np.nan], values[1:] / values[:-1] - 1])

    last = _known(calendar, sessions)
    prices = panel._prices.to_numpy(dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        returns = np.vstack([np.full((1, prices.shape[1]), np.nan), prices[1:] / prices[:-1] - 1])
        excess = returns - np.nanmean(returns, axis=1)[:, None]
    own = np.where(np.isfinite(excess), excess, 0.0)
    total = np.cumsum(own, axis=0)
    own5 = total.copy()
    own5[5:] = total[5:] - total[:-5]
    spx = change(level("^GSPC"))
    groups: dict[str, list[str]] = {}
    for symbol, (_name, group) in us.US_STOCKS.items():
        if group not in ("ETF", "指數") and symbol in american:
            groups.setdefault(group, []).append(symbol)
    best = np.full(shape, -np.inf)
    picked = {name: np.full(shape, np.nan) for name in ("us_peer_mom_20", "us_peer_mom_60", "us_peer_gap_5")}
    for members in groups.values():
        with np.errstate(invalid="ignore"):
            group = np.nanmean(np.vstack([change(level(symbol)) for symbol in members]), axis=0) - spx
        cumulative = np.nancumsum(np.nan_to_num(group))
        slope, related = rolling_slope(_between(cumulative, last, True), excess, PEER_WINDOW, PEER_LEAST, correlation=True)
        better = np.isfinite(related) & (related > best)
        best = np.where(better, related, best)
        for name, steps in (("us_peer_mom_20", 20), ("us_peer_mom_60", 60), ("us_peer_gap_5", 5)):
            moved = slope * _back(cumulative, last, steps, True)[:, None]
            picked[name] = np.where(better, moved - own5 if name == "us_peer_gap_5" else moved, picked[name])
    follows = best >= PEER_FOLLOWS
    for name, values in picked.items():
        cache[name] = np.where(follows, values, np.nan)
    for name, symbol, difference in (("us_rate_move_60", "^TNX", True), ("us_dollar_move_60", "DX-Y.NYB", False)):
        series = level(symbol, "close", carry=True)
        slope = rolling_slope(_between(series, last, difference), excess, PEER_WINDOW, PEER_LEAST)
        cache[name] = slope * _back(series, last, 60, difference)[:, None]
    return cache


def market_reading(history: str | Path, name: str, sessions: list[date]) -> np.ndarray:
    """One of US_MARKET per Taiwan session, from what was dated (the market series: published) before it; NaN when
    nothing is known. model.market_state places it against its own last sessions."""
    from quant_platform.research.history import us

    history = Path(history)
    output = np.full(len(sessions), np.nan)
    if name == "market:us_hot":
        from quant_platform.research.market_signals import us_hot

        counts = us_hot(history, sessions)
        return np.array([counts.get(day, np.nan) for day in sessions], dtype=float)

    def series(values: dict[date, float]) -> tuple[np.ndarray, np.ndarray]:
        days = sorted(values)
        return np.array([values[day] for day in days], dtype=float), _known(days, sessions)

    if name in ("market:us_sox_20", "market:us_vix"):
        closes = _closes(history, SOX if name == "market:us_sox_20" else "^VIX")
        if not closes:
            return output
        values, last = series(closes)
        if name == "market:us_sox_20":
            return _back(values, last, 20, False)
        return np.array([values[b] if b >= 0 else np.nan for b in last])
    market = us.daily(history)
    if name == "market:us_yield2_60" and market.get("DGS2"):
        values, last = series(market["DGS2"])
        return _back(values, last, 60, True)
    if name == "market:us_dollar_60" and market.get("DTWEXBGS"):
        values, last = series(market["DTWEXBGS"])
        return _back(values, last, 60, False)
    if name == "market:us_curve" and market.get("DGS10") and market.get("DGS3MO"):
        both = {day: market["DGS10"][day] - market["DGS3MO"][day] for day in market["DGS10"] if day in market["DGS3MO"]}
        values, last = series(both)
        return np.array([values[b] if b >= 0 else np.nan for b in last])
    return output
