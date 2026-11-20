"""The simulation's guarantees — what makes a day in the lab the same whether it is lived or replayed.

- ``blind`` / ``leak_check``: a copy of the loaded panel in which every number after the day is scrambled; each tool is
  run on the real panel and on the blinded one, and a tool whose text differs has read past the day. (The factors
  themselves are checked by the platform's weekly truncation check, research/leakcheck.py; this checks the tools.)
  Headlines, scored news and knowledge entries are gated by time in the desk and tested with files from "later".
- ``ScriptedClient``: an agent that follows a script — the whole loop (tools, hard rules, stores, engine) runs with no
  model call and no cost.
- ``placebo``: random agents. Each day one draws as many stocks as the agent held from the stocks the agent looked at
  that day; where the agent's own picks stand among them says whether its choosing added anything to its looking.
  Close to close and equal parts, no fees on either side: a measure of selection, not money (the money is the
  engine's, ``evaluate``)."""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import numpy as np


def _scramble(array: np.ndarray, axis: int, after: int, rng) -> np.ndarray:
    output = np.array(array, copy=True)
    index = [slice(None)] * output.ndim
    index[axis] = slice(after + 1, None)
    future = output[tuple(index)]
    if future.size == 0:
        return output
    if output.dtype == bool:
        output[tuple(index)] = ~future
    elif np.issubdtype(output.dtype, np.floating):
        other = 1 - axis if output.ndim == 2 else None
        mixed = rng.permutation(future, axis=other) if other is not None else rng.permutation(future)
        output[tuple(index)] = mixed * 1.37 + 0.11
    elif np.issubdtype(output.dtype, np.integer):
        output[tuple(index)] = 0
    return output


def _blind_object(thing, sessions: int, after: int, rng):
    clone = copy.copy(thing)
    for name, value in list(vars(thing).items()):
        if isinstance(value, np.ndarray) and value.ndim in (1, 2) and sessions in value.shape:
            axis = value.ndim - 1 if value.shape[-1] == sessions else 0
            setattr(clone, name, _scramble(value, axis, after, rng))
    return clone


def blind(fp, position: int, seed: int = 20261120):
    """A copy of ``fp`` whose arrays hold other numbers after ``position`` (the factors computed so far, the closes,
    the market, the turnover): anything that reads only up to the day answers the same on both."""
    rng = np.random.default_rng(seed)
    sessions = len(fp.sessions)
    clone = _blind_object(fp, sessions, position, rng)
    if getattr(fp, "panel", None) is not None:
        clone.panel = _blind_object(fp.panel, sessions, position, rng)
    cache = getattr(fp, "_cache", None)
    if isinstance(cache, dict):
        clone._cache = {name: (_scramble(value, value.ndim - 1, position, rng)
                               if isinstance(value, np.ndarray) and value.ndim == 2 and value.shape[-1] == sessions else value)
                        for name, value in cache.items()}
    if getattr(fp, "_memo", None) is not None:
        clone._memo = {}
    return clone


def tool_outputs(desk, signals: tuple[str, ...] = ("momentum_6", "low_beta_250", "foreign_buy_20")) -> dict[str, str]:
    """Every tool once with ordinary arguments (the cards of the most agreed stocks)."""
    output = {"market": desk.tool_market({}), "mood": desk.tool_mood({}), "consensus": desk.tool_consensus({"top": 30})}
    for signal in signals:
        output[f"screen:{signal}"] = desk.tool_screen({"signal": signal, "top": 20})
    codes = [code for code, _count, _families in desk.agreed(8)] or sorted(desk.seen)[:8]
    output["card"] = desk.tool_card({"codes": codes})
    output["portfolio"] = desk.tool_portfolio({})
    output["journal"] = desk.tool_journal({})
    output["review"] = desk.tool_review({})
    output["check"] = desk.tool_check({"codes": codes})
    output["headlines"] = desk.tool_headlines({"days": 2})
    return output


def leak_check(fp, base: Path, research: Path, day: date, history: list[dict] | None = None) -> dict[str, object]:
    """The tools on ``day`` with the real panel and with the panel blinded after it: ``leaks`` names the tools whose
    answers differ (none expected)."""
    from quant_platform.research.agent_lab.desk import LabDesk

    position = fp.index[day]
    held = list((history[-1].get("llm") or {}).get("hold") or []) if history else []
    real = tool_outputs(LabDesk(fp, base, research, day, held, history or []))
    dark = tool_outputs(LabDesk(blind(fp, position), base, research, day, held, history or []))
    leaks = sorted(name for name in real if real[name] != dark.get(name))
    return {"date": day.isoformat(), "sessions_after": len(fp.sessions) - 1 - position, "tools": len(real), "leaks": leaks}


class ScriptedClient:
    """A client whose answers come from ``script(instructions, prompt, context)``: no call, no cost."""

    model = "scripted"

    def __init__(self, script) -> None:
        self._script = script
        self.calls = 0

    def json_call(self, instructions: str, prompt: str | list[str], operation: str, context: str = "") -> tuple[dict, dict]:
        self.calls += 1
        if isinstance(prompt, (list, tuple)):        # parts, as the model clients take them: the same text joined
            prompt = "\n\n".join(str(part) for part in prompt if part)
        return self._script(instructions, prompt, context), {}


def top_of_consensus(count: int = 10):
    """A scripted agent for dry runs: it holds the first ``count`` stocks of the consensus shown in its opening (the
    lines "2330 台積電｜半導體｜3 個規則…"), vetoes nothing and writes no view."""
    import re

    def script(instructions: str, prompt: str, _context: str) -> dict:
        if '"veto"' in instructions:
            return {"veto": [], "view": "腳本", "lesson": ""}
        codes = list(dict.fromkeys(re.findall(r"^(\d{4}) [^\n]*｜\d+ 個規則", prompt, flags=re.M)))[:count]
        if '"picks"' in instructions:
            return {"picks": [{"code": code, "reason": "腳本"} for code in codes], "case": "腳本"}
        if '"avoid"' in instructions:
            return {"avoid": [], "risk": "腳本"}
        return {"hold": [{"code": code, "reason": "腳本"} for code in codes], "view": "腳本", "lesson": ""}

    return script


def path_return(fp, decisions: list[tuple[int, list[str]]], last: int, close: np.ndarray | None = None) -> float | None:
    """Close to close, equal parts: each decision's stocks from the session after it until the session after the next
    decision (``decisions``: (position, codes) in order), through ``last``."""
    close = np.asarray(fp.panel.filled, dtype=float) if close is None else close
    total, counted = 1.0, False
    for number, (position, codes) in enumerate(decisions):
        start = position + 1
        end = min(decisions[number + 1][0] + 1, last) if number + 1 < len(decisions) else last
        rows = [fp.row[f"{code}.TW"] for code in codes if f"{code}.TW" in fp.row]
        if not rows or end <= start:
            continue
        with np.errstate(all="ignore"):
            moves = close[rows, end] / close[rows, start] - 1
        moves = moves[np.isfinite(moves)]
        if len(moves):
            total *= 1 + float(moves.mean())
            counted = True
    return total - 1 if counted else None


def placebo(fp, records: list[dict], last: int, draws: int = 500, seed: int = 7) -> dict[str, object] | None:
    """Where the agent's picks stand among ``draws`` random agents choosing as many stocks from what it looked at."""
    rng = np.random.default_rng(seed)
    close = np.asarray(fp.panel.filled, dtype=float)
    days = []
    for record in records:
        position = fp.index.get(date.fromisoformat(record["date"]))
        hold = list((record.get("llm") or {}).get("hold") or [])
        pool = sorted(set(record.get("seen") or []) | set(hold))
        if position is not None and hold and position + 1 < last:
            days.append((position, hold, pool))
    if not days:
        return None
    own = path_return(fp, [(position, hold) for position, hold, _pool in days], last, close)
    everything = path_return(fp, [(position, pool) for position, _hold, pool in days], last, close)
    results = []
    for _ in range(draws):
        drawn = [(position, list(rng.choice(pool, size=min(len(hold), len(pool)), replace=False)))
                 for position, hold, pool in days]
        value = path_return(fp, drawn, last, close)
        if value is not None:
            results.append(value)
    if own is None or not results:
        return None
    results_array = np.array(results)
    return {"own": own, "looked_at": everything, "median": float(np.median(results_array)),
            "percentile": float((results_array < own).mean()), "draws": len(results), "decisions": len(days)}
