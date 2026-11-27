"""2026-11-22 (使用者：SMC加到策略研究裡; docs/research/results.md「SMC（Smart Money Concepts）」): the price-action
method traders call Smart Money Concepts, as factors of the daily engine.

SMC reads the candles' highs and lows — the columns of the stock files no factor had read (every price factor came
from the adjusted closes, the opens since 2026-10-10). Its building blocks, each decided on the day's bar alone with
what was confirmed before it:

* swing points — a bar whose high (low) is the highest (lowest) of the SWING bars before and after it; known SWING
  sessions later, and used only from the session after that;
* structure — a close above the last confirmed swing high is a break upward (BOS; CHoCH when the structure before it
  was bearish), a close below the last confirmed swing low a break downward. The structure is bullish after the
  former, bearish after the latter;
* the bullish order block — the last down candle (close below open) before the bar that broke upward, its low to its
  high; the first return of the price into it is the entry, a close under its low the failure;
* the bullish fair value gap (FVG) — three bars whose third low is above the first high: the empty stretch between;
  the first return into it is the entry, a close under its lower edge the failure;
  One order block and one gap are followed per stock — the newest; an older one not yet retested is dropped;
* the liquidity sweep — a bar that trades below the last confirmed swing low and closes back at or above it;
* premium and discount — where the close sits between the swing low the bullish leg rests on and the leg's high; SMC
  buys the lower half.

Long only (the platform's rules buy). Prices are dividend-adjusted (the bar's raw prices times adjusted/close of the
day), so an ex-dividend open is not a gap. An event factor is 1 on the event's session and falls in a straight line
toward 0 over its window (the newest first), and has no value (NaN) without an event in force: a rule ranks only the
stocks with a signal. The trial of 11/22 counted a median of 25 stocks a day with a sweep in force and 38 with a
setup; with 0 for the others (the engine's convention for its other event factors) a rule would have filled its 20
places with the most liquid stocks that have no signal at all. With fewer signals than places the account holds fewer
stocks, equal amounts. Measuring (the event study, the novelty gate) reads the others as 0 (``measured``).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

SMC_FACTORS = {
    "smc_break_up_20": "SMC 結構向上突破（BOS／CHoCH：收盤站上最近一個確認的波段高點，20 日內越新分數越高；結構轉空就歸零）",
    "smc_choch_up_20": "SMC 結構由空翻多（CHoCH：原本是收盤跌破波段低點的空方結構，收盤站上波段高點；20 日內，結構再轉空就歸零）",
    "smc_ob_retest_10": "SMC 回測多方訂單塊（向上突破前最後一根黑 K 的高低範圍，突破後第一次回到這個範圍；10 日內，收盤跌破範圍下緣就歸零）",
    "smc_fvg_retest_10": "SMC 回測多方缺口（FVG：三根 K 棒第三根的最低價高於第一根的最高價留下的空檔，第一次回到空檔；10 日內，收盤跌破空檔下緣就歸零）",
    "smc_sweep_low_10": "SMC 掃低回收（流動性獵取：盤中跌破最近確認的波段低點、收盤收回來；10 日內，收盤跌破那天最低價就歸零）",
    "smc_discount": "SMC 多方結構的折價區（結構向上時，收盤在這一段漲幅裡的位置，越靠近起漲的波段低點分數越高；不是多方結構的沒有分數）",
    "smc_setup_10": "SMC 完整進場（多方結構、收盤在這段漲幅的下半部，當天又回測訂單塊或缺口；10 日內，收盤跌破回測的下緣就歸零）",
}
SWING = 5            # bars each side of a swing point (the "internal structure" length of the common SMC indicators)
BREAK_WINDOW = 20    # sessions a break upward counts
ENTRY_WINDOW = 10    # sessions a retest, a sweep or a setup counts
ZONE_LIFE = 60       # sessions an order block or a gap waits for its retest
BLOCK_REACH = 20     # the order block's down candle is at most this many sessions before the break
WARMUP = 60          # bars of a stock's own before its values count
VERSION = "smc-1.1.0"
EVENTS = tuple(name for name in SMC_FACTORS if name != "smc_discount")
# 2026-11-25 (使用者：做第二版; docs/research/results.md「SMC 第二版」, written before the run): the structure as a state
# (the first version only said how new the last break upward was) and the higher timeframe SMC trades along — the
# same structure read from weekly bars. A day of week W reads the weeks that ended before W: the week in progress is
# never a bar, so cutting the data in the middle of a week changes nothing before the cut.
SMC_V2 = {
    "smc_structure": "SMC 日線結構（最近一次是收盤站上波段高點＝多方 +1，最近一次是收盤跌破波段低點＝空方 −1；還沒有突破過的沒有分數）",
    "smc_bearish": "SMC 日線結構是空方（最近一次的結構突破是收盤跌破波段低點；多方或還沒突破過的沒有分數）",
    "smc_w_structure": "SMC 週線結構（用到上週為止的週 K 棒，前後各 3 週的波段高低點：多方 +1、空方 −1）",
    "smc_w_break_up_8": "SMC 週線結構向上突破（週收盤站上最近一個確認的週線波段高點，8 週內越新分數越高；週線結構轉空就歸零；用到上週為止）",
    "smc_break_up_w": "SMC 結構向上突破、週線結構也是多方（日線 20 日內的向上突破，只算週線結構是多方的股票）",
    "smc_fvg_retest_w": "SMC 回測多方缺口、週線結構也是多方（日線 10 日內第一次回到缺口，只算週線結構是多方的股票）",
}
EVENTS_V2 = ("smc_bearish", "smc_w_break_up_8", "smc_break_up_w", "smc_fvg_retest_w")
WEEK_SWING = 3       # weekly bars each side of a weekly swing point
WEEK_BREAK = 8       # weeks a weekly break upward counts
WEEK_WARMUP = 26     # weekly bars of a stock's own before its weekly values count


def measured(name: str, values: np.ndarray) -> np.ndarray:
    """An event factor with 0 where no event is in force — the stocks with a signal against every stock; the others
    (and ``smc_discount``, a place within the bullish stocks) as they are."""
    return np.nan_to_num(values, nan=0.0) if name in EVENTS or name in EVENTS_V2 else values


def swings(high: np.ndarray, low: np.ndarray, swing: int = SWING) -> tuple[np.ndarray, np.ndarray]:
    """sessions × symbols: the price of a swing high (low) on the session it is confirmed — ``swing`` sessions after
    its bar, whose high (low) is the highest (lowest) of the ``swing`` bars each side — NaN elsewhere."""
    window = 2 * swing + 1
    top = pd.DataFrame(high).rolling(window, min_periods=swing + 1).max().to_numpy()
    bottom = pd.DataFrame(low).rolling(window, min_periods=swing + 1).min().to_numpy()
    pivot_high = np.full(high.shape, np.nan)
    pivot_low = np.full(low.shape, np.nan)
    pivot_high[swing:] = high[:-swing]
    pivot_low[swing:] = low[:-swing]
    pivot_high[:window - 1] = np.nan          # a swing point needs its ``swing`` bars before it too
    pivot_low[:window - 1] = np.nan
    with np.errstate(invalid="ignore"):
        return (np.where(pivot_high >= top, pivot_high, np.nan), np.where(pivot_low <= bottom, pivot_low, np.nan))


def structure(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, swing: int = SWING,
              break_window: int = BREAK_WINDOW, entry_window: int = ENTRY_WINDOW, zone_life: int = ZONE_LIFE,
              warmup: int = WARMUP) -> dict[str, np.ndarray]:
    """Every SMC factor, sessions × symbols, from the bars (sessions × symbols, NaN where a stock did not trade).
    Session t's values come from the bars up to t."""
    sessions, symbols = close.shape
    valid = np.isfinite(close) & np.isfinite(high) & np.isfinite(low)
    new_high, new_low = swings(np.where(valid, high, np.nan), np.where(valid, low, np.nan), swing)
    output = {name: np.full((sessions, symbols), np.nan, dtype=np.float32)
              for name in (*SMC_FACTORS, "smc_structure", "smc_bearish")}
    never = -10 ** 9
    swing_high = np.full(symbols, np.nan)        # the last confirmed swing high not yet closed above
    swing_low = np.full(symbols, np.nan)         # the last confirmed swing low not yet traded below
    anchor = np.full(symbols, np.nan)            # the last confirmed swing low: the leg's foot
    leg_high = np.full(symbols, np.nan)          # the highest high since the last break upward
    trend = np.zeros(symbols, dtype=np.int8)     # +1 after a break upward, -1 after a break downward
    down_low, down_high = np.full(symbols, np.nan), np.full(symbols, np.nan)     # the last down candle
    down_at = np.full(symbols, never)
    block_low, block_high = np.full(symbols, np.nan), np.full(symbols, np.nan)
    block_born = np.full(symbols, never)
    gap_low, gap_high = np.full(symbols, np.nan), np.full(symbols, np.nan)
    gap_born = np.full(symbols, never)
    events = ("break", "choch", "block", "gap", "sweep", "setup")
    last = {name: np.full(symbols, never) for name in events}
    floor = {name: np.full(symbols, np.nan) for name in ("block", "gap", "sweep", "setup")}
    seen = np.zeros(symbols, dtype=np.int64)
    with np.errstate(invalid="ignore", divide="ignore"):
        for t in range(sessions):
            o, h, l, c, v = open_[t], high[t], low[t], close[t], valid[t]
            # the swing low: traded below and closed back = swept; closed below = the structure breaks downward
            pierced = v & (l < swing_low)
            broke_down = pierced & (c < swing_low)
            swept = pierced & ~broke_down
            last["sweep"][swept] = t
            floor["sweep"][swept] = l[swept]
            trend[broke_down] = -1
            swing_low[pierced] = np.nan
            # the swing high: a close above it breaks the structure upward; its order block is the last down candle
            broke_up = v & (c > swing_high)
            last["break"][broke_up] = t
            last["choch"][broke_up & (trend == -1)] = t
            recent = broke_up & (t - down_at <= BLOCK_REACH)
            block_low = np.where(broke_up, np.where(recent, down_low, np.nan), block_low)
            block_high = np.where(broke_up, np.where(recent, down_high, np.nan), block_high)
            block_born = np.where(broke_up, t, block_born)
            trend[broke_up] = 1
            leg_high = np.where(broke_up, h, np.where(v & (trend == 1), np.fmax(leg_high, h), leg_high))
            swing_high[broke_up] = np.nan
            # the first return into a zone made before today; a close under its lower edge is its failure
            block_live = np.isfinite(block_low) & (block_born < t) & (t - block_born <= zone_life)
            block_failed = block_live & v & (c < block_low)
            block_touch = block_live & v & (l <= block_high) & ~block_failed
            gap_live = np.isfinite(gap_low) & (gap_born < t) & (t - gap_born <= zone_life)
            gap_failed = gap_live & v & (c < gap_low)
            gap_touch = gap_live & v & (l <= gap_high) & ~gap_failed
            last["block"][block_touch] = t
            floor["block"][block_touch] = block_low[block_touch]
            last["gap"][gap_touch] = t
            floor["gap"][gap_touch] = gap_low[gap_touch]
            span = leg_high - anchor
            place = np.where(span > 0, (c - anchor) / span, np.nan)       # 0 at the leg's foot, 1 at its high
            setup = (trend == 1) & (block_touch | gap_touch) & (place <= 0.5)
            last["setup"][setup] = t
            floor["setup"][setup] = np.fmin(np.where(block_touch, block_low, np.nan),
                                            np.where(gap_touch, gap_low, np.nan))[setup]
            used = block_touch | block_failed | (np.isfinite(block_low) & (t - block_born > zone_life))
            block_low[used] = np.nan
            used = gap_touch | gap_failed | (np.isfinite(gap_low) & (t - gap_born > zone_life))
            gap_low[used] = np.nan
            if t >= 2:                         # a new gap: this bar's low above the high two bars before
                opened = v & valid[t - 1] & valid[t - 2] & (l > high[t - 2])
                gap_low = np.where(opened, high[t - 2], gap_low)
                gap_high = np.where(opened, l, gap_high)
                gap_born = np.where(opened, t, gap_born)
            for name in floor:                 # an entry whose level is closed under no longer counts
                last[name][v & (c < floor[name])] = never
            fell = v & np.isfinite(o) & (c < o)
            down_low, down_high = np.where(fell, l, down_low), np.where(fell, h, down_high)
            down_at = np.where(fell, t, down_at)
            # swing points confirmed today count from the next session
            swing_high = np.where(np.isfinite(new_high[t]), new_high[t], swing_high)
            swing_low = np.where(np.isfinite(new_low[t]), new_low[t], swing_low)
            anchor = np.where(np.isfinite(new_low[t]), new_low[t], anchor)
            seen += v
            ready = seen >= warmup
            bullish = trend == 1

            def fresh(name: str, window: int) -> np.ndarray:
                return np.clip(1.0 - (t - last[name]) / window, 0.0, 1.0)

            output["smc_break_up_20"][t] = np.where(ready & bullish, fresh("break", break_window), 0.0)
            output["smc_choch_up_20"][t] = np.where(ready & bullish, fresh("choch", break_window), 0.0)
            output["smc_ob_retest_10"][t] = np.where(ready, fresh("block", entry_window), 0.0)
            output["smc_fvg_retest_10"][t] = np.where(ready, fresh("gap", entry_window), 0.0)
            output["smc_sweep_low_10"][t] = np.where(ready, fresh("sweep", entry_window), 0.0)
            output["smc_setup_10"][t] = np.where(ready, fresh("setup", entry_window), 0.0)
            output["smc_discount"][t] = np.where(ready & v & bullish & np.isfinite(place),
                                                 np.clip(1.0 - place, 0.0, 1.0), np.nan)
            output["smc_structure"][t] = np.where(ready & (trend != 0), trend, np.nan)
            output["smc_bearish"][t] = np.where(ready & (trend == -1), 1.0, np.nan)
    for name in EVENTS:                        # no event in force: no value
        output[name] = np.where(output[name] > 0, output[name], np.nan).astype(np.float32)
    return output


def week_numbers(sessions: list[date]) -> np.ndarray:
    """Each session's week, 0 for the first session's (ISO weeks: Monday to Sunday)."""
    monday = np.array([(day - date(1970, 1, 5)).days // 7 for day in sessions], dtype=np.int64)
    return monday - monday[0] if len(monday) else monday


def weekly_bars(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray,
                weeks: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """weeks × symbols: each week's first open, highest high, lowest low and last close among the days a stock traded
    (NaN for a week it did not trade at all); ``weeks`` from ``week_numbers`` (a week without a session is NaN)."""
    count = int(weeks[-1]) + 1 if len(weeks) else 0
    shape = (count, close.shape[1])
    week_open, week_high = np.full(shape, np.nan), np.full(shape, np.nan)
    week_low, week_close = np.full(shape, np.nan), np.full(shape, np.nan)
    traded = np.isfinite(close) & np.isfinite(high) & np.isfinite(low)
    for t in range(close.shape[0]):
        w, v = int(weeks[t]), traded[t]
        first = v & ~np.isfinite(week_close[w])
        week_open[w] = np.where(first, np.where(np.isfinite(open_[t]), open_[t], close[t]), week_open[w])
        week_high[w] = np.where(v, np.fmax(week_high[w], high[t]), week_high[w])
        week_low[w] = np.where(v, np.fmin(week_low[w], low[t]), week_low[w])
        week_close[w] = np.where(v, close[t], week_close[w])
    return week_open, week_high, week_low, week_close


def weekly_structure(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, sessions: list[date],
                     daily: dict[str, np.ndarray], swing: int = WEEK_SWING, break_window: int = WEEK_BREAK,
                     warmup: int = WEEK_WARMUP) -> dict[str, np.ndarray]:
    """The weekly factors of SMC_V2, sessions × symbols: the structure of the weekly bars as of the end of the week
    before each session's own, and the daily break and gap retest (``daily``: ``structure``'s output) of the stocks
    whose weekly structure is bullish."""
    weeks = week_numbers(sessions)
    found = structure(*weekly_bars(open_, high, low, close, weeks), swing=swing, break_window=break_window,
                      warmup=warmup)
    before = weeks - 1
    known = before >= 0

    def as_of_last_week(values: np.ndarray) -> np.ndarray:
        output = np.full(close.shape, np.nan, dtype=np.float32)
        output[known] = values[before[known]]
        return output

    state = as_of_last_week(found["smc_structure"])
    bullish = state == 1
    return {"smc_w_structure": state,
            "smc_w_break_up_8": as_of_last_week(found["smc_break_up_20"]),
            "smc_break_up_w": np.where(bullish, daily["smc_break_up_20"], np.nan).astype(np.float32),
            "smc_fvg_retest_w": np.where(bullish, daily["smc_fvg_retest_10"], np.nan).astype(np.float32)}


def _days(days) -> np.ndarray:
    return np.array([(day - date(1970, 1, 1)).days for day in days], dtype=np.int64)


def raw_high_low(history: str | Path, sessions: list[date], symbols: list[str],
                 extra: dict | None = None) -> tuple[np.ndarray, np.ndarray]:
    """sessions × symbols: each day's raw high and low from the yearly stock files (NaN where the file has none);
    ``extra``: {day: {symbol: (high, low)}} — the after-close table's session before the files have it. A stock that
    moved from TPEx to TWSE is one ``code.TW`` series, as in ``stock_rules.load_stock_data``."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    high = np.full((len(sessions), len(symbols)), np.nan)
    low = np.full((len(sessions), len(symbols)), np.nan)
    if not sessions:
        return high, low
    session_days = _days(sessions)
    columns = pd.Index(symbols)
    base = Path(history) / "stocks"

    def read(exchange: str, year: int):
        path = base / exchange / f"{year}.parquet"
        if not path.is_file():
            return None
        table = pq.read_table(path, columns=["date", "code", "high", "low"])
        days = table["date"].cast(pa.int32()).to_numpy().astype(np.int64)
        position = np.searchsorted(session_days, days)
        position[position >= len(session_days)] = 0
        return (days, np.where(session_days[position] == days, position, -1), table["code"].to_pandas().to_numpy(),
                table["high"].to_numpy(zero_copy_only=False).astype(float),
                table["low"].to_numpy(zero_copy_only=False).astype(float))

    def place(position: np.ndarray, column: np.ndarray, highs: np.ndarray, lows: np.ndarray) -> None:
        keep = (position >= 0) & (column >= 0) & (highs > 0) & (lows > 0)
        high[position[keep], column[keep]] = highs[keep]
        low[position[keep], column[keep]] = lows[keep]

    years = range(sessions[0].year, sessions[-1].year + 1)
    listed: dict[str, int] = {}                  # code -> its first TWSE day
    for year in years:
        found = read("twse", year)
        if found is None:
            continue
        days, position, codes, highs, lows = found
        first = pd.Series(days).groupby(codes).min()
        for code, day in first.items():
            listed[code] = min(int(day), listed.get(code, int(day)))
        place(position, columns.get_indexer(pd.Index(codes) + ".TW"), highs, lows)
    if any(symbol.endswith(".TWO") for symbol in symbols):
        for year in years:
            found = read("tpex", year)
            if found is None:
                continue
            days, position, codes, highs, lows = found
            moved = np.array([code in listed and day < listed[code] for code, day in zip(codes, days)], dtype=bool)
            names = np.where(moved, pd.Index(codes) + ".TW", pd.Index(codes) + ".TWO")
            place(position, columns.get_indexer(names), highs, lows)
    index = {day: row for row, day in enumerate(sessions)}
    for day, rows in (extra or {}).items():
        row = index.get(day)
        if row is None:
            continue
        for symbol, (top, bottom) in rows.items():
            column = columns.get_indexer([symbol])[0]
            if column >= 0 and top and bottom and not np.isfinite(high[row, column]):
                high[row, column], low[row, column] = float(top), float(bottom)
    return high, low


def bars(panel, history: str | Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(open, high, low, close), sessions × symbols, dividend-adjusted; NaN where the stock did not trade. A traded
    day without a high or a low in the files takes the larger (smaller) of its open and close."""
    close = panel.close.T
    with np.errstate(invalid="ignore", divide="ignore"):
        ratio = panel.adjusted.T / close
    raw_open = np.where(np.isfinite(panel.open.T), panel.open.T, close)
    raw_high, raw_low = raw_high_low(history, list(panel.sessions), list(panel.symbols),
                                     (getattr(panel, "notes", None) or {}).get("extra_bars"))
    raw_high = np.fmax(np.where(np.isfinite(raw_high), raw_high, close), np.fmax(raw_open, close))
    raw_low = np.fmin(np.where(np.isfinite(raw_low), raw_low, close), np.fmin(raw_open, close))
    traded = np.isfinite(close)
    return (np.where(traded, raw_open * ratio, np.nan), np.where(traded, raw_high * ratio, np.nan),
            np.where(traded, raw_low * ratio, np.nan), np.where(traded, close * ratio, np.nan))


def matrix(fp, history: str | Path, factor: str) -> np.ndarray:
    """sessions × symbols; every SMC factor is computed once per panel."""
    cache = getattr(fp, "_smc", None)
    if cache is None:
        candles = bars(fp.panel, history)
        cache = structure(*candles)
        cache.update(weekly_structure(*candles, list(fp.panel.sessions), cache))
        fp._smc = cache
    return cache[factor]


# 2026-11-27 (使用者：別人用 SMC 就用得不錯; docs/research/results.md「SMC 第四版：當時機用」, written before the run). The
# three versions before ranked every stock by an SMC reading and bought the first 20 — a stock picker. The people who
# trade by SMC time one instrument: in after its entry, out when its structure breaks. Measured here stock by stock,
# against holding that same stock: a policy says after each close whether it wants the stock, the order fills at the
# next session's open.
TIMING_BUY = 0.001425 + 0.002                 # the fee and the slippage daily.ROUND_TRIP counts, on the way in
TIMING_SELL = 0.001425 + 0.003 + 0.002        # the fee, the tax and the slippage on the way out
TIMING_POLICIES = {
    "structure": "日線結構是多方就持有（收盤站上波段高點的隔天開盤買，收盤跌破波段低點的隔天開盤賣）",
    "structure_w": "日線、週線結構都是多方才持有（其中一個轉空的隔天開盤賣）",
    "block": "多方結構裡回測訂單塊的隔天開盤買，日線結構轉空的隔天開盤賣",
    "gap": "多方結構裡回測缺口（FVG）的隔天開盤買，日線結構轉空的隔天開盤賣",
    "setup": "完整進場（多方結構、折價區、回測訂單塊或缺口）的隔天開盤買，日線結構轉空的隔天開盤賣",
    "sweep": "掃低回收的隔天開盤買，訊號失效（滿 10 天或收盤跌破掃低那天的最低價）的隔天開盤賣",
}
TIMING_ENTRIES = {"block": "smc_ob_retest_10", "gap": "smc_fvg_retest_10", "setup": "smc_setup_10"}


def timing_wants(found: dict[str, np.ndarray], policy: str) -> np.ndarray:
    """sessions × symbols: after session t's close the policy wants to hold the stock (``found``: ``structure``'s
    output with ``weekly_structure``'s). An entry policy starts on its event's own session inside a bullish
    structure and lasts until the structure is no longer bullish."""
    bullish = found["smc_structure"] == 1
    if policy == "structure":
        return bullish
    if policy == "structure_w":
        return bullish & (found["smc_w_structure"] == 1)
    if policy == "sweep":
        return np.isfinite(found["smc_sweep_low_10"])
    fired = (found[TIMING_ENTRIES[policy]] >= 1.0) & bullish        # an event factor is 1 on its event's session
    wants = np.zeros(bullish.shape, dtype=bool)
    held = np.zeros(bullish.shape[1], dtype=bool)
    for t in range(bullish.shape[0]):
        held = np.where(held, bullish[t], fired[t])
        wants[t] = held
    return wants


def timing_returns(open_: np.ndarray, close: np.ndarray, wants: np.ndarray, buy: float = TIMING_BUY,
                   sell: float = TIMING_SELL) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """sessions × symbols: each session's return of an account that is in the stock when the policy wanted it at the
    close before (bought and sold at the session's open, costs taken on the day), whether it is in a trade that
    session (the selling session included) and whether it bought that session. A session the stock did not trade
    changes nothing: the order waits for the next one it trades."""
    sessions, symbols = close.shape
    returns = np.zeros(close.shape)
    inside = np.zeros(close.shape, dtype=bool)
    entered = np.zeros(close.shape, dtype=bool)
    held = np.zeros(symbols, dtype=bool)
    last = np.full(symbols, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        for t in range(sessions):
            o, c = open_[t], close[t]
            traded = np.isfinite(o) & np.isfinite(c) & (o > 0)
            target = wants[t - 1] if t else np.zeros(symbols, dtype=bool)
            enter = traded & target & ~held
            leave = traded & ~target & held
            stay = traded & held & ~leave & np.isfinite(last)
            row = np.zeros(symbols)
            row[stay] = c[stay] / last[stay] - 1
            row[enter] = c[enter] / o[enter] * (1 - buy) - 1
            row[leave] = o[leave] / last[leave] * (1 - sell) - 1
            returns[t], inside[t], entered[t] = row, held | enter, enter
            held = (held | enter) & ~leave
            last = np.where(traded, c, last)
    return returns, inside, entered
