"""2026-10-21 (使用者 2026-10-14：確實檢查每個時期都照當時的台股規則; FinMind Sponsor 的每日漲跌停價): the engine's
limit-up and limit-down prices (FactorPanel.price_limits: the reference price ± 10%, ± 7% before 2015-06, rounded
inward to the tick; the exchange's own on ex-rights days) against the exchange's for every listed stock and session
from 2015-06 (FinMind TaiwanStockPriceLimit, raw/finmind/TaiwanStockPriceLimit/<code>.json.gz) — and, where it
matters, the sessions the engine calls locked at a limit (no buying at limit-up, no selling at limit-down) against
those the exchange's prices say were. A diagnostic: a difference is listed, the engine is changed in its own work
package."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
DATASET = "TaiwanStockPriceLimit"
TOLERANCE = 1e-6


def official(base: str | Path, codes: list[str]) -> dict[str, dict[str, tuple[float, float]]]:
    """code -> day -> (limit-down, limit-up) from the stored files; 0 means no limit that day (left out)."""
    from quant_platform.research.history.finmind import read_rows

    output: dict[str, dict[str, tuple[float, float]]] = {}
    for code in codes:
        for row in read_rows(Path(base), DATASET, code):
            try:
                low, high = float(row["limit_down"]), float(row["limit_up"])
            except (KeyError, TypeError, ValueError):
                continue
            if low > 0 and high > 0:
                output.setdefault(code, {})[str(row["date"])[:10]] = (low, high)
    return output


def compare(sessions: list[date], symbols: list[str], close: np.ndarray, up: np.ndarray, down: np.ndarray,
            exchange: dict[str, dict[str, tuple[float, float]]], start: date,
            locked: tuple[np.ndarray, np.ndarray] | None = None) -> dict[str, object]:
    """Every (stock, session) from ``start`` with both: the limits that differ and the locks that differ (the engine's
    own locks, ``locked`` = market_locks' up and down, when given)."""
    compared = differ = lock_differ = beyond = 0
    by_year: Counter = Counter()
    examples: list[dict] = []
    locks = Counter()
    for row, symbol in enumerate(symbols):
        days = exchange.get(symbol.split(".")[0])
        if not days:
            continue
        for column, day in enumerate(sessions):
            if day < start:
                continue
            found = days.get(day.isoformat())
            price = close[row, column]
            if not found or not np.isfinite(up[row, column]) or not np.isfinite(price):
                continue
            low, high = found
            compared += 1
            wrong = abs(up[row, column] - high) > TOLERANCE or abs(down[row, column] - low) > TOLERANCE
            if locked is not None:
                mine = "up" if locked[0][row, column] else "down" if locked[1][row, column] else None
            else:
                mine = ("up" if price >= up[row, column] - TOLERANCE else "down" if price <= down[row, column] + TOLERANCE
                        else None)
            # locked when the close sits on the exchange's limit (a close beyond it is a data mismatch, not a lock)
            theirs = "up" if abs(price - high) <= TOLERANCE else "down" if abs(price - low) <= TOLERANCE else None
            if price > high + TOLERANCE or price < low - TOLERANCE:
                beyond += 1
            if mine or theirs:
                locks[(mine, theirs)] += 1
            if wrong:
                differ += 1
                by_year[day.year] += 1
            if mine != theirs:
                lock_differ += 1
                if len(examples) < 40:
                    examples.append({"code": symbol.split(".")[0], "date": day.isoformat(), "close": float(price),
                                     "engine": [float(down[row, column]), float(up[row, column])], "exchange": [low, high],
                                     "engine_lock": mine, "exchange_lock": theirs})
    return {"compared": compared, "limits_differ": differ, "locks_differ": lock_differ, "close_beyond_exchange": beyond,
            "limits_differ_by_year": {str(year): count for year, count in sorted(by_year.items())},
            "locks": {f"{mine or '-'}/{theirs or '-'}": count for (mine, theirs), count in sorted(locks.items(), key=str)},
            "examples": examples}


def run(base: str | Path, research: str | Path) -> Path:
    from quant_platform.research import daily

    _data, fp = daily.load(Path(base))
    up, down = fp.price_limits()
    _traded, locked_up, locked_down = fp.market_locks()
    exchange = official(base, sorted({symbol.split(".")[0] for symbol in fp.symbols}))
    found = compare(list(fp.sessions), list(fp.symbols), fp.panel.close, up, down, exchange, daily.RECENT_START,
                    (locked_up, locked_down))
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "start": daily.RECENT_START.isoformat(),
              "stocks_with_limits": len(exchange), **found}
    folder = Path(research) / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"limits-check-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
