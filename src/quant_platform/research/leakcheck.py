"""2026-10-11 (全面研究「立即採用」第 3 項): the truncation check — no factor may know the future.

For a few sessions d drawn at random, every factor is computed again from data cut at d (prices and volumes up
to d; the daily chip tables before d, as they are published after the close; monthly revenue usable from the
day after it was available; statements usable from their availability date) and compared stock by stock with
the full-history value on d. Any difference means the full-history value used something after d. Statistics
cannot catch a leak (a strategy that peeks passes the deflated Sharpe and PBO); a tool can.

The check does not judge whether an availability date itself is right (a company filing late, a deadline
moved by a holiday); that is the data's job (research_method §3).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import numpy as np


def truncated_data(data, until: date):
    from quant_platform.research.legacy_challenger import LegacyData

    def cut(series: dict) -> dict:
        return {day: value for day, value in series.items() if day <= until}

    return LegacyData(sessions=[day for day in data.sessions if day <= until],
                      closes={symbol: cut(series) for symbol, series in data.closes.items() if cut(series)},
                      traded_value={symbol: cut(series) for symbol, series in data.traded_value.items()},
                      factors={symbol: cut(series) for symbol, series in data.factors.items()}, predictions={},
                      opens={symbol: cut(series) for symbol, series in (getattr(data, "opens", None) or {}).items()})


def truncated_panel(history: Path, data, until: date):
    from quant_platform.research.daily import ChipStore, FactorPanel, Industries, load_industries, market_closes
    from quant_platform.research.model import models_root
    from quant_platform.research.stock_rules import Panel

    cut = truncated_data(data, until)
    panel = Panel(cut)
    return cut, FactorPanel(panel, Industries(load_industries(history)),
                            ChipStore(history, panel.sessions, panel.symbols, panel.close, until=until),
                            market_closes(history, panel.sessions), models_root(history))


def compare(full, cut, factor: str, day: date) -> dict[str, object]:
    """Stock by stock on ``day``: the full-history value against the value from data cut at ``day``."""
    left = full.matrix(factor)[:, full.index[day]]
    right = cut.matrix(factor)[:, cut.index[day]]
    differ, checked, examples = 0, 0, []
    for symbol, row in cut.row.items():
        if symbol not in full.row:
            continue
        a, b = float(left[full.row[symbol]]), float(right[row])
        if not (np.isfinite(a) or np.isfinite(b)):
            continue
        checked += 1
        same = (np.isfinite(a) and np.isfinite(b) and abs(a - b) <= 1e-4 * (1 + abs(a)))
        if not same:
            differ += 1
            if len(examples) < 3:
                examples.append({"symbol": symbol, "full": a, "cut": b})
    return {"checked": checked, "differ": differ, "examples": examples}


def run(history: Path, research: Path, data, full, factors: list[str], days: list[date], job=None) -> dict:
    results = {}
    for number, day in enumerate(days):
        if job:
            job.update(done=number, current=f"截到 {day} 重算 {len(factors)} 個因子", force=True)
        _cut_data, cut = truncated_panel(history, data, day)
        results[day.isoformat()] = {factor: compare(full, cut, factor, day) for factor in factors}
    leaks = sorted({factor for item in results.values() for factor, found in item.items() if found["differ"]})
    report = {"built_at": datetime.now().isoformat(timespec="seconds"), "days": [day.isoformat() for day in days],
              "factors": factors, "leaks": leaks, "results": results}
    folder = research / "leakcheck"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"leakcheck-{datetime.now():%Y%m%d-%H%M%S}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                                           encoding="utf-8")
    return report


def sample_days(sessions: list[date], count: int, first: date, seed: int = 0) -> list[date]:
    pool = [day for day in sessions if day >= first][:-5]
    rng = np.random.default_rng(seed)
    return sorted(pool[index] for index in rng.choice(len(pool), size=min(count, len(pool)), replace=False))
