"""The latest session's factors for every stock: what the stock page shows (roadmap S9-W05, 2026-10-05).

Ranking one stock means computing every factor over the whole market (about a minute), so the page reads
this snapshot instead. It is built every trading day at 15:45 by the worker (``stock_snapshot``) and by
``python -m quant_platform.research snapshot``: listed and TPEx stocks, every factor's value on the latest
session with quotes and its percentile among the stocks that traded that day and have a value
(1 = the highest, i.e. the strongest on that factor's own terms; low volatility is stored negated so
the calmest stock ranks highest). Chip and fundamental values are the ones published before that
session's close (the research's point-in-time rule). The page also reads the stock's own daily bars
(``stock_bars``) from the research history.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pyarrow.compute as pc
import pyarrow.parquet as pq

SNAPSHOT_FILE = Path("snapshots") / "stocks-latest.json"

# What the page groups together (every factor of research/daily.py exactly once).
GROUPS = (
    ("趨勢與動能", ("trend_200", "high_52w", "momentum_12_1", "momentum_6", "momentum_3", "ma_cross_20_60",
                   "breakout_55")),
    ("短線與技術指標", ("rsi_14", "kd_k", "macd_hist", "bollinger_b", "reversal_1", "reversal_5d", "low_max_return")),
    ("波動、成交與股利", ("low_volatility_60", "low_volatility_250", "liquidity", "volume_surge", "dividend_yield")),
    ("籌碼", ("foreign_holding", "foreign_holding_change", "foreign_buy_20", "trust_buy_20", "margin_growth_20",
             "short_margin_ratio")),
    ("基本面", ("earnings_yield", "book_to_price", "revenue_yoy", "revenue_yoy_3m", "market_cap")),
)


def _money(value: float) -> str:
    if value >= 1e12:
        return f"{value / 1e12:,.2f} 兆"
    if value >= 1e8:
        return f"{value / 1e8:,.1f} 億"
    return f"{value / 1e4:,.0f} 萬"


def display(factor: str, value: float | None) -> str:
    """The factor's value in plain words (the stored value is what the ranking uses)."""
    if value is None or not math.isfinite(value):
        return "—"
    if factor in ("momentum_12_1", "momentum_6", "momentum_3", "trend_200", "ma_cross_20_60", "revenue_yoy",
                  "revenue_yoy_3m", "margin_growth_20"):
        return f"{value:+.1%}"
    if factor in ("reversal_1", "reversal_5d"):
        return f"{-value:+.1%}"                       # stored negated: the period's own return
    if factor in ("low_volatility_60", "low_volatility_250"):
        return f"日波動 {-value:.1%}"
    if factor == "low_max_return":
        return f"最大單日漲幅 {-value:.1%}"
    if factor in ("high_52w", "breakout_55"):
        return f"高點的 {value:.0%}"
    if factor in ("dividend_yield", "short_margin_ratio"):
        return f"{value:.1%}"
    if factor == "liquidity":
        return f"日均 {_money(math.exp(value))}"
    if factor == "volume_surge":
        return f"{value:.2f} 倍"
    if factor in ("rsi_14", "kd_k"):
        return f"{value:.0f}"
    if factor in ("macd_hist", "bollinger_b"):
        return f"{value:.2f}"
    if factor == "foreign_holding":
        return f"{value:.1f}%"                        # FinMind gives the ratio in percent
    if factor == "foreign_holding_change":
        return f"{value:+.2f} 個百分點"
    if factor in ("foreign_buy_20", "trust_buy_20"):
        return f"{value:+.2%}"
    if factor == "earnings_yield":
        return f"本益比 {1 / value:.1f}" if value > 0 else "—"
    if factor == "book_to_price":
        return f"淨值比 {1 / value:.2f}" if value > 0 else "—"
    if factor == "market_cap":
        return _money(math.exp(value))
    return f"{value:.3g}"


def _number(value) -> float | None:
    value = float(value)
    return round(value, 6) if math.isfinite(value) else None


def build(history: str | Path, research_dir: str | Path, today: date | None = None) -> dict[str, object]:
    """Compute and save the snapshot; returns its summary (date, stocks)."""
    from quant_platform.research.daily import (
        FACTOR_LABELS,
        ChipStore,
        DailyRule,
        FactorPanel,
        Industries,
        _percentile,
        load_industries,
        market_closes,
    )
    from quant_platform.research.stock_forward import _names
    from quant_platform.research.stock_rules import Panel, load_stock_data

    history, research_dir = Path(history), Path(research_dir)
    year = (today or datetime.now(UTC).date()).year
    data = load_stock_data(history, year - 2, year, universe="all")      # two years before: 252-day factors
    panel = Panel(data)
    traded_count = np.isfinite(panel.close).sum(axis=0)
    if not traded_count.any():
        return {}
    # the latest session with a full market: a day only one exchange has appended yet is skipped
    usual = traded_count[-20:].max()
    quoted = [position for position, count in enumerate(traded_count) if count >= 0.8 * usual]
    position = quoted[-1]
    day = panel.sessions[position]
    chips = ChipStore(history, panel.sessions, panel.symbols, panel.close)
    fp = FactorPanel(panel, Industries(load_industries(history)), chips, market_closes(history, panel.sessions))
    traded = np.isfinite(panel.close[:, position])
    eligible = fp.eligible(DailyRule(name="資格", factors={"trend_200": 1.0}), position)
    values: dict[str, np.ndarray] = {}
    ranks: dict[str, np.ndarray] = {}
    for factor in FACTOR_LABELS:
        column = fp.matrix(factor)[:, position].astype(float)
        mask = traded & np.isfinite(column)
        rank = np.full(len(column), np.nan)
        if mask.sum() > 1:
            rank[mask] = _percentile(column[mask])
        values[factor], ranks[factor] = column, rank
    names = _names(history, day.year)
    stocks: dict[str, dict[str, object]] = {}
    for row, symbol in enumerate(panel.symbols):
        if not traded[row]:
            continue
        code = symbol.split(".")[0]
        closes = panel.close[row, : position + 1]
        earlier = closes[:-1][np.isfinite(closes[:-1])]
        stocks[code] = {
            "symbol": symbol, "name": names.get(code, ""), "exchange": "上櫃" if symbol.endswith(".TWO") else "上市",
            "industry": fp.industry(symbol), "close": _number(panel.close[row, position]),
            "previous": _number(earlier[-1]) if len(earlier) else None,
            "turnover_20": _number(fp.turnover_20[row, position]), "eligible": bool(eligible[row]),
            "values": {factor: _number(values[factor][row]) for factor in FACTOR_LABELS},
            "ranks": {factor: _number(ranks[factor][row]) for factor in FACTOR_LABELS},
        }
    chips_as_of = None
    shareholding = history / "chips" / "TaiwanStockShareholding.parquet"
    if shareholding.is_file():
        chips_as_of = pc.max(pq.read_table(shareholding, columns=["date"])["date"]).as_py()
    snapshot = {"date": day.isoformat(), "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "chips_as_of": chips_as_of.isoformat() if chips_as_of else None,
                "labels": dict(FACTOR_LABELS), "stocks": stocks}
    path = research_dir / SNAPSHOT_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    partial.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    partial.replace(path)
    return {"date": snapshot["date"], "stocks": len(stocks), "chips_as_of": snapshot["chips_as_of"]}


_CACHE: dict[str, tuple[float, dict]] = {}


def load(research_dir: str | Path) -> dict[str, object] | None:
    """The saved snapshot (kept in memory until the file changes)."""
    path = Path(research_dir) / SNAPSHOT_FILE
    if not path.is_file():
        return None
    mtime = path.stat().st_mtime
    cached = _CACHE.get(str(path))
    if cached is None or cached[0] != mtime:
        try:
            cached = (mtime, json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return cached[1] if cached else None
        _CACHE[str(path)] = cached
    return cached[1]


def search(snapshot: dict[str, object] | None, query: str, limit: int = 20) -> list[dict[str, object]]:
    """Stocks whose code starts with, or whose name contains, the query; exact code first."""
    query = (query or "").strip().upper().removesuffix(".TW").removesuffix(".TWO")
    if not snapshot or not query:
        return []
    found = []
    for code, item in snapshot["stocks"].items():
        name = str(item.get("name") or "")
        if code == query:
            found.append((0, code))
        elif code.startswith(query):
            found.append((1, code))
        elif query in name.upper():
            found.append((2, code))
    return [{"code": code, **snapshot["stocks"][code]} for _order, code in sorted(found)[:limit]]


def stock_bars(history: str | Path, code: str, sessions: int = 260) -> list[dict[str, object]]:
    """The stock's last daily bars (unadjusted, as traded) from the research history, both markets."""
    base = Path(history)
    rows: dict[date, dict[str, object]] = {}
    for exchange in ("tpex", "twse"):                       # a stock that moved to TWSE: TWSE wins a shared day
        files = sorted((base / "stocks" / exchange).glob("*.parquet"))[-2:]
        for path in files:
            table = pq.read_table(path, columns=["date", "code", "open", "high", "low", "close", "volume", "turnover"],
                                  filters=[("code", "=", code)])
            for item in table.to_pylist():
                rows[item["date"]] = item
    return [rows[day] for day in sorted(rows)][-sessions:]


def view(history: str | Path, research_dir: str | Path, code: str, snapshot: dict | None,
         holders: list[dict[str, object]] | None = None, verdicts: dict[str, str] | None = None) -> dict | None:
    """Everything the stock page shows, or None when the code is neither in the snapshot nor the history."""
    item = (snapshot or {}).get("stocks", {}).get(code)
    bars = stock_bars(history, code)
    if item is None and not bars:
        return None
    groups = []
    labels = (snapshot or {}).get("labels", {})
    if item:
        for title, factors in GROUPS:
            rows = []
            for factor in factors:
                rank = item["ranks"].get(factor)
                rows.append({"factor": factor, "label": labels.get(factor, factor),
                             "value": display(factor, item["values"].get(factor)), "rank": rank,
                             "top": None if rank is None else max(1, round((1 - rank) * 100)),
                             "verdict": (verdicts or {}).get(factor)})
            groups.append({"title": title, "rows": rows})
    close = previous = None
    if bars:
        close = bars[-1]["close"]
        previous = bars[-2]["close"] if len(bars) > 1 else None
    return {"code": code, "item": item, "groups": groups, "chart": candles(bars[-120:], bars), "close": close,
            "previous": previous, "last_day": bars[-1]["date"].isoformat() if bars else None,
            "snapshot_date": (snapshot or {}).get("date"), "chips_as_of": (snapshot or {}).get("chips_as_of"),
            "holders": holders or []}


def candles(shown: list[dict[str, object]], every: list[dict[str, object]], width: int = 720,
            height: int = 260) -> dict[str, object] | None:
    """SVG geometry of a candlestick chart with the 20- and 60-day averages (red up, green down)."""
    if len(shown) < 2:
        return None
    closes = [row["close"] for row in every]
    offset = len(every) - len(shown)

    def average(window: int) -> list[float | None]:
        output = []
        for index in range(offset, len(every)):
            part = closes[max(0, index - window + 1): index + 1]
            output.append(sum(part) / window if len(part) == window else None)
        return output

    ma20, ma60 = average(20), average(60)
    lows = [row["low"] or row["close"] for row in shown] + [value for value in ma20 + ma60 if value]
    highs = [row["high"] or row["close"] for row in shown] + [value for value in ma20 + ma60 if value]
    low, high = min(lows), max(highs)
    span = (high - low) or 1.0
    step = width / len(shown)

    def y(price: float) -> float:
        return round(6 + (high - price) / span * (height - 12), 1)

    bodies = []
    for index, row in enumerate(shown):
        open_ = row["open"] or row["close"]
        x = round(index * step + step / 2, 1)
        top, bottom = y(max(open_, row["close"])), y(min(open_, row["close"]))
        bodies.append({"x": x, "left": round(x - step * 0.35, 1), "width": round(max(step * 0.7, 1), 1),
                       "top": top, "height": max(round(bottom - top, 1), 0.8),
                       "high": y(row["high"] or max(open_, row["close"])), "low": y(row["low"] or min(open_, row["close"])),
                       "up": row["close"] >= open_})

    def line(values: list[float | None]) -> str:
        return " ".join(f"{round(index * step + step / 2, 1)},{y(value)}" for index, value in enumerate(values) if value)

    return {"bodies": bodies, "ma20": line(ma20), "ma60": line(ma60), "width": width, "height": height,
            "first": shown[0]["date"].isoformat(), "last": shown[-1]["date"].isoformat(), "high": high, "low": low}
