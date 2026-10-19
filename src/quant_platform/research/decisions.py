"""Forward decisions kept as they were made (2026-10-15).

The forward record, the common-start board and the after-close orders each replay a rule from its start with
today's data. Data that arrives after a decision — the evening's revenue table, the next night's news flags, a
model retrained, a corrected report — then changed a decision already taken, and a recorded account jumped to
holdings it never traded into (10-15: 108 accounts failed the reconciliation). Here each replay's decisions — the
list the rule wants on every session it decides, after the market's limits, and the sessions it must decide again
— are written down for the days it has recorded; a later replay takes those days as they are and ranks only the
new ones (``daily.daily_rankings(fixed=...)``). The after-close orders write the day's decisions at 13:50, so the
15:30 record keeps what the orders said.

One file, ``forward/stocks/decisions.json``: ``{key: {"through": day, "days": {day: [symbols]}, "pending": {...}}}``
(``pending``, engine 1.5.0: the orders the market refused that are still to be tried — the sells and the number of
places to fill; entries written before 1.5.0 have ``again_after``: the next session decides again in full), the key
being the account (rule hash and start day, a blend's sleeve numbered after ``#``).
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path


def key(rule_hash: str, start: date, sleeve: int | None = None) -> str:
    return f"{rule_hash}@{start.isoformat()}" + (f"#{sleeve}" if sleeve is not None else "")


class DecisionStore:
    def __init__(self, research: str | Path) -> None:
        self.path = Path(research) / "forward" / "stocks" / "decisions.json"
        self._kept: dict[str, dict] = {}
        try:
            self._data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        except (OSError, ValueError):
            self._data = {}

    def fixed(self, name: str) -> dict | None:
        """The decisions kept for an account: through (last day), days (day -> symbols), again_after."""
        entry = self._kept.get(name) or self._data.get(name)
        if not entry:
            return None
        return {"through": date.fromisoformat(entry["through"]),
                "days": {date.fromisoformat(day): list(symbols) for day, symbols in entry["days"].items()},
                "pending": entry.get("pending"), "again_after": bool(entry.get("again_after"))}

    def why(self, name: str) -> dict[str, dict[str, str]]:
        """2026-10-19 (使用者：每一筆買賣要有原因): day (ISO) -> symbol -> why it came in or left, as decided."""
        entry = self._kept.get(name) or self._data.get(name) or {}
        return dict(entry.get("why") or {})

    def add_why(self, name: str, why: dict) -> None:
        """2026-10-19: reasons for days kept before reasons were written down (``explain_backfill``); a day that has
        its reason keeps it."""
        entry = self._kept.get(name) or self._data.get(name)
        if not entry:
            return
        reasons = dict(entry.get("why") or {})
        for day, notes in why.items():
            reasons.setdefault(day.isoformat() if isinstance(day, date) else str(day), dict(notes))
        self._kept[name] = {**entry, "why": reasons}

    def keep(self, name: str, ranks: dict[date, list[str]], pending: dict | None, through: date,
             why: dict | None = None) -> None:
        """Write down an account's decisions up to ``through`` (never moving an earlier ``through`` back); ``why``
        (day -> symbol -> reason) is added to what was kept, a day's reasons written once."""
        entry = self._kept.get(name) or self._data.get(name)
        if entry and date.fromisoformat(entry["through"]) > through:
            return
        reasons = dict((entry or {}).get("why") or {})
        for day, notes in (why or {}).items():
            stamp = day.isoformat() if isinstance(day, date) else str(day)
            if stamp <= through.isoformat() and stamp not in reasons:
                reasons[stamp] = dict(notes)
        self._kept[name] = {"through": through.isoformat(),
                            "days": {day.isoformat(): list(symbols) for day, symbols in sorted(ranks.items()) if day <= through},
                            "pending": pending or None, "why": reasons}

    def save(self) -> None:
        """Merge what this run kept into the file as it is now (another process may have written meanwhile)."""
        if not self._kept:
            return
        try:
            current = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {}
        except (OSError, ValueError):
            current = {}
        for name, entry in self._kept.items():
            old = current.get(name)
            if old and old["through"] > entry["through"]:
                continue
            current[name] = entry
        self.path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_suffix(".partial")
        partial.write_text(json.dumps(current, ensure_ascii=False), encoding="utf-8")
        os.replace(partial, self.path)
        self._data, self._kept = current, {}


def rankings(fp, rule, start: date, end: date, store: DecisionStore | None = None, name: str | None = None,
             through: date | None = None) -> dict[date, list[str]]:
    """``daily_rankings`` taking the decisions kept under ``name`` as made, then keeping the new ones up to
    ``through``."""
    from quant_platform.research.daily import daily_rankings

    if store is None or name is None:
        return daily_rankings(fp, rule, start, end)
    trace: dict = {}
    ranks = daily_rankings(fp, rule, start, end, fixed=store.fixed(name), trace=trace)
    if through is not None:
        sessions = [day for day in fp.sessions if start <= day <= end]
        lag = 1 if getattr(rule, "execution", "same") in ("next", "open") else 0
        resume = next((day for number, day in enumerate(sessions)            # the first session decided live
                       if number + lag < len(sessions) and sessions[number + lag] > through), None)
        pending = trace["pending"].get(resume) if resume is not None else trace["pending_after"]
        store.keep(name, ranks, pending, through, trace.get("why"))
    return ranks
