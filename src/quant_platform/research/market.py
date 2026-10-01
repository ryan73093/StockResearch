"""Load the long-history dataset for simulations (S3-W04)."""

from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from quant_platform.research.history.catalog import SERIES_BY_KEY
from quant_platform.research.history.dataset import read_series, sha256

DEFAULT_BASE = Path("instance") / "research" / "history"


@dataclass(frozen=True)
class MarketData:
    sessions: list[date]
    closes: dict[str, dict[date, float]]
    dividends: dict[str, dict[date, float]] = field(default_factory=dict)  # ex-date → cash per unit
    unit_ratios: dict[str, dict[date, float]] = field(default_factory=dict)  # splits, stock dividends
    tax_kind: dict[str, str] = field(default_factory=dict)
    fingerprint: str = ""

    def __post_init__(self) -> None:
        ordered = {asset: sorted(values) for asset, values in self.closes.items()}
        object.__setattr__(self, "_days", ordered)
        object.__setattr__(
            self, "_prices", {asset: [self.closes[asset][day] for day in days] for asset, days in ordered.items()}
        )
        object.__setattr__(self, "_window_digests", None)

    def fingerprint_until(self, end: date) -> str:
        """Hash of everything a simulation ending on ``end`` can see.

        Sessions, closes, dividends and unit ratios up to ``end`` are chained
        day by day, so rows appended after ``end`` (the nightly refresh) leave it
        unchanged while a correction to earlier data changes it. Trials are
        keyed on this, not on the whole-file ``fingerprint``.
        """
        if self._window_digests is None:
            rows: dict[date, list[list[object]]] = {}
            for day in self.sessions:
                rows.setdefault(day, []).append(["session"])
            for label, table in (("close", self.closes), ("dividend", self.dividends), ("ratio", self.unit_ratios)):
                for asset, values in table.items():
                    for day, value in values.items():
                        rows.setdefault(day, []).append([label, asset, value])
            hasher = hashlib.sha256(json.dumps(sorted(self.tax_kind.items())).encode("utf-8"))
            empty = hasher.hexdigest()
            days, digests = [], []
            for day in sorted(rows):
                entries = sorted(rows[day], key=lambda entry: json.dumps(entry))
                hasher.update(json.dumps([day.isoformat(), entries]).encode("utf-8"))
                days.append(day)
                digests.append(hasher.copy().hexdigest())
            object.__setattr__(self, "_window_digests", (days, digests, empty))
        days, digests, empty = self._window_digests
        position = bisect.bisect_right(days, end) - 1
        return digests[position] if position >= 0 else empty

    def first_day(self, asset: str) -> date:
        return self._days[asset][0]

    def last_day(self, asset: str) -> date:
        return self._days[asset][-1]

    def close(self, asset: str, day: date) -> float | None:
        return self.closes.get(asset, {}).get(day)

    def last_close(self, asset: str, day: date) -> float | None:
        """Latest close on or before ``day`` (for valuation on no-trade days)."""
        days = self._days.get(asset, [])
        position = bisect.bisect_right(days, day) - 1
        return self._prices[asset][position] if position >= 0 else None

    def trailing(self, asset: str, day: date, sessions: int) -> list[float]:
        """Up to ``sessions`` closes ending on ``day`` (inclusive)."""
        days = self._days.get(asset, [])
        end = bisect.bisect_right(days, day)
        return self._prices[asset][max(0, end - sessions):end]

    def adjusted_trailing(self, asset: str, day: date, sessions: int) -> list[float]:
        """``trailing`` with each close divided by the splits and stock dividends (unit ratios) that
        took effect after it, up to ``day``: signals must not read a split as a fall (0050 split 1:4
        in June 2025; the raw close then sat far below every moving average for months)."""
        days = self._days.get(asset, [])
        end = bisect.bisect_right(days, day)
        start = max(0, end - sessions)
        closes = self._prices[asset][start:end] if asset in self._prices else []
        ratios = self.unit_ratios.get(asset) or {}
        window = days[start:end]
        effective = sorted((when, ratio) for when, ratio in ratios.items() if window and window[0] < when <= window[-1])
        if not effective:
            return closes
        adjusted = []
        for when_close, close in zip(window, closes):
            factor = 1.0
            for when, ratio in effective:
                if when > when_close:
                    factor *= ratio
            adjusted.append(close / factor)
        return adjusted

    def has_unit_ratios(self, assets: set[str], until: date) -> bool:
        return any(day <= until for asset in assets for day in self.unit_ratios.get(asset, {}))


def available_assets(base_dir: str | Path = DEFAULT_BASE) -> list[str]:
    """Every catalog series with a daily file; research runs load all of them so
    that all trials of a period share one data fingerprint."""
    base = Path(base_dir)
    return [key for key in SERIES_BY_KEY if (base / "daily" / f"{key}.parquet").is_file()]


def load_market(assets: list[str], base_dir: str | Path = DEFAULT_BASE) -> MarketData:
    base = Path(base_dir)
    closes: dict[str, dict[date, float]] = {}
    digests = []
    for asset in sorted(set(assets)):
        path = base / "daily" / f"{asset}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"{path} 不存在；先執行 python -m quant_platform.research.history fetch")
        closes[asset] = {row["date"]: float(row["close"]) for row in read_series(path) if row["close"]}
        digests.append((asset, sha256(path)))
    calendar_path = base / "daily" / "TAIEX.parquet"
    if calendar_path.is_file():
        sessions = sorted(row["date"] for row in read_series(calendar_path))
        digests.append(("TAIEX", sha256(calendar_path)))
    else:
        sessions = sorted({day for values in closes.values() for day in values})
    dividends: dict[str, dict[date, float]] = {}
    ratios: dict[str, dict[date, float]] = {}
    actions_path = base / "actions.json"
    if actions_path.is_file():
        report = json.loads(actions_path.read_text(encoding="utf-8"))
        for asset in closes:
            entry = (report.get("series") or {}).get(asset) or {}
            for action in entry.get("actions") or []:
                day = date.fromisoformat(action["day"])
                if action["kind"] == "cash_dividend":
                    dividends.setdefault(asset, {})[day] = dividends.get(asset, {}).get(day, 0.0) + float(action["cash"])
                elif action["kind"] in {"split", "stock_dividend"}:
                    ratios.setdefault(asset, {})[day] = ratios.get(asset, {}).get(day, 1.0) * float(action["ratio"])
            digests.append((f"{asset}:actions", entry.get("sha256", "")))
            digests.append((f"{asset}:action_list", json.dumps(entry.get("actions") or [], sort_keys=True)))
    fingerprint = hashlib.sha256(json.dumps(digests, sort_keys=True).encode("utf-8")).hexdigest()
    return MarketData(
        sessions=sessions,
        closes=closes,
        dividends=dividends,
        unit_ratios=ratios,
        tax_kind={asset: SERIES_BY_KEY[asset].tax_kind for asset in closes if asset in SERIES_BY_KEY},
        fingerprint=fingerprint,
    )
