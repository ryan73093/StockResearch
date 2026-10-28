"""Every strategy's number (2026-10-28; 使用者：麻煩每個策略給個編號好嗎，我都不知道你總覽第一個規則跟前向觀察第一個規則
是不是一樣的).

A strategy is one rule setting (its spec hash: the same in the trial registry, the pool, the forward accounts and the
monthly lists). Its number is the first trial it was registered as — it never changes, however often the rule is run
again (a new engine, a fix, the monthly roll give it newer trials, not a new number). The pages show it as "#7339" in
front of the name everywhere, and /research/no/<number> opens it.
"""

from __future__ import annotations

import re
from pathlib import Path

_HEAD = re.compile(r'"trial_id":\s*(\d+).*?"spec_hash":\s*"([0-9a-f]{16,64})"')
_CACHE: dict[str, object] = {}


def numbers(research: str | Path) -> dict[str, int]:
    """spec hash -> its first trial id, from trials.jsonl (kept until the file changes; only each line's head is read)."""
    path = Path(research) / "trials.jsonl"
    if not path.is_file():
        return {}
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    if _CACHE.get("key") != key:
        found: dict[str, int] = {}
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                match = _HEAD.search(line[:400])
                if match:
                    trial, spec = int(match.group(1)), match.group(2)
                    if spec not in found or trial < found[spec]:
                        found[spec] = trial
        _CACHE.update(key=key, found=found, by_number={trial: spec for spec, trial in found.items()})
    return dict(_CACHE["found"])  # type: ignore[arg-type]


def number_of(research: str | Path, spec_hash: str | None) -> int | None:
    if not spec_hash:
        return None
    numbers(research)
    return (_CACHE.get("found") or {}).get(spec_hash)  # type: ignore[union-attr]


def spec_of(research: str | Path, number: int) -> str | None:
    """The spec hash a strategy number stands for (None when no rule was registered as that trial first)."""
    numbers(research)
    return (_CACHE.get("by_number") or {}).get(number)  # type: ignore[union-attr]


def label(research: str | Path, spec_hash: str | None) -> str:
    """"#7339" or "" (a forward-only account, e.g. the AI researcher's, has no number)."""
    found = number_of(research, spec_hash)
    return f"#{found}" if found is not None else ""
