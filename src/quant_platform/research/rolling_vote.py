"""滾動共識名單 (2026-11-28, docs/research/results.md「滾動共識名單：投票的規則每月重選，票數看實際持股」, written
before the run). RL's twelfth version measured that the stocks the 20 to-date-best rules hold in common are the ones
that earn the most (passing them over took the multiple from 12.12 to 7.76). Here they are held directly.

Each month end the voters are chosen as RL's tenth version's fixed way does (``rl_select.choose`` on the excess over
0050 summed from 2015-06 to that month: 20 rules, at most PER_FAMILY of a family, the keep zone) from the month-end
returns up to that month only. A stock's votes are the voters whose month-end list holds it (a blend holds what any
of its parts holds); among equal votes the stock held by the better-placed voters comes first. The votes are in
force from the first session of a later month until the next vote's are.

The pool of rules itself was searched with the whole history in view: the backtest flatters this factor; the forward
record is the test. The lists are ``rl_holdings``'s (positions in the panel's symbols when they were computed; the
codes are written here, with the panel they were read against)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from quant_platform.research import rl_select, rl_spread
from quant_platform.research.rl_holdings import KEEP
from quant_platform.research.rl_select import TAIPEI

VERSION = "rolling-vote-1"
FILE = "votes.json"
WARM = 12                    # months of record a rule has before the first vote (2015-06 … 2016-05)
FACTOR = "rolling_consensus"


def tally(chosen: list[int], scores: np.ndarray, stocks) -> dict[int, float]:
    """Stock → its votes: one for each voter holding it, plus a part under one that only orders equal votes (the
    better a voter's place by ``scores``, the more)."""
    order = sorted(chosen, key=lambda rule: -scores[rule])
    count = max(len(order), 1)
    votes: dict[int, float] = {}
    for place, rule in enumerate(order):
        for stock in stocks(rule):
            votes[stock] = votes.get(stock, 0.0) + 1.0 + (count - place) / (count * 1000.0)
    return votes


def monthly(data: dict, rules: list[list[tuple[str, float]]], have: dict) -> dict[str, dict[str, object]]:
    """Month → the voters (rows of ``data``) and the votes (stock position → votes), from that month's end only."""
    active = data["returns"] - data["base"][None, :]
    held: list[int] = []
    found: dict[str, dict[str, object]] = {}
    for at in range(WARM - 1, len(data["months"])):
        month = data["months"][at]
        scores = active[:, :at + 1].sum(axis=1)
        held = rl_select.choose(scores, held, data["families"], KEEP)

        def stocks(rule: int, month: str = month) -> frozenset:
            return frozenset().union(*[have[key].get(month) or [] for key, _share in rules[rule]])

        found[month] = {"voters": list(held), "votes": tally(held, scores, stocks)}
    return found


def build(research: str | Path, history: str | Path, models: str | Path) -> Path:
    from quant_platform.research import daily

    research = Path(research)
    data, rules, have, total = rl_spread.ready(research)
    _legacy, fp = daily.load(history)
    symbols = list(fp.symbols)
    months = {}
    for month, item in monthly(data, rules, have).items():
        if item["votes"] and max(item["votes"]) >= len(symbols):
            raise ValueError(f"{month}: a list names position {max(item['votes'])}, the panel has {len(symbols)} stocks")
        months[month] = {"voters": [data["trials"][rule] for rule in item["voters"]],
                         "votes": {symbols[stock]: round(value, 5) for stock, value in sorted(item["votes"].items())}}
    folder = Path(models) / VERSION
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / FILE
    path.write_text(json.dumps({"version": VERSION, "built_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
                                "rules": len(rules), "rules_left_out": total - len(rules), "pick": rl_select.PICK,
                                "per_family": rl_select.PER_FAMILY, "keep": KEEP, "warm": WARM,
                                "panel_symbols": len(symbols), "months": months}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def digest(models: str | Path) -> str:
    path = Path(models) / VERSION / FILE
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.is_file() else "missing"


def matrix(fp, models: str | Path | None) -> np.ndarray:
    """sessions × symbols: a session's votes are those of the last vote of an earlier calendar month (none before
    the first; a stock nobody voted for has no value)."""
    output = np.full((len(fp.sessions), len(fp.symbols)), np.nan, dtype=np.float32)
    path = Path(models) / VERSION / FILE if models is not None else None
    if path is None or not path.is_file():
        return output
    months = json.loads(path.read_text(encoding="utf-8"))["months"]
    place = {symbol: number for number, symbol in enumerate(fp.symbols)}
    rows: dict[str, np.ndarray] = {}
    for month, item in months.items():
        row = np.full(len(fp.symbols), np.nan, dtype=np.float32)
        for symbol, value in item["votes"].items():
            if symbol in place:
                row[place[symbol]] = value
        rows[month] = row
    ordered = sorted(rows)
    for position, day in enumerate(fp.sessions):
        mine = f"{day.year}-{day.month:02d}"
        earlier = [month for month in ordered if month < mine]
        if earlier:
            output[position] = rows[earlier[-1]]
    return output
