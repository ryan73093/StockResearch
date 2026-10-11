"""2026-10-11 (全面研究「立即採用」第 1 項): the T0 consensus list — every T0 candidate votes, the account
trades one list.

Splitting the account into one sleeve per T0 candidate (#2189) passed the gate but placed about 437 orders a
month: NT$300,000 cut into 17 parts leaves under NT$1,000 a stock. The outside evidence (AQR's integrated
portfolios, Ghayur et al. 2018 at high tracking error, WorldQuant's and Numerai's "many weak signals, one
score") says combine at the signal level. Each session every member scores the stocks it can hold the way
its own rule ranks them (its factors, its universe: every listed stock or the 100 largest), turned into a
percentile among those stocks; a stock's consensus score is the average percentile over the members that
cover it — one member, one vote. A rule then holds the top of that single list.

The members are frozen when the version is registered (``consensus_members.MEMBERS_V1``, 25 rules on
2026-10-11); a later member set is a new version. The members were rated T0 after their 2020-10 onward
results were seen, so the list's 2020-10 onward backtest is not clean out-of-sample: its forward record
from registration is.

``dev_consensus`` (the same day) is the clean test of the method: its 180 members are every finalist of the
large scans, chosen with 2015-06..2020-09 results only (``consensus_dev_members.MEMBERS_DEV1``), whatever their
later tier — so its 2020-10 onward record says whether "many development-period winners voting" works, not
whether the vote of known winners looks good.
"""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd

CONSENSUS_VERSION = "t0-consensus-1"
VERSIONS = {"t0_consensus": "t0-consensus-1", "dev_consensus": "dev-consensus-1"}


def _raw(factor: str = "t0_consensus"):
    if factor == "dev_consensus":
        from quant_platform.research.consensus_dev_members import MEMBERS_DEV1

        return MEMBERS_DEV1
    from quant_platform.research.consensus_members import MEMBERS_V1

    return MEMBERS_V1


def members(factor: str = "t0_consensus"):
    """The frozen members as rules (trial, name, DailyRule)."""
    from quant_platform.research.daily import DailyRule

    return [(trial, name, DailyRule.model_validate(spec)) for trial, name, spec in _raw(factor)]


def members_digest(factor: str = "t0_consensus") -> str:
    body = json.dumps([[trial, spec] for trial, _name, spec in _raw(factor)], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _eligible(fp, rule) -> np.ndarray:
    """symbols × sessions: the rule's own eligibility (price, turnover, listing age, the largest N)."""
    mask = ((fp.panel.close >= rule.min_price) & (fp.turnover_20 >= rule.min_turnover) & (fp.age >= rule.min_history))
    if getattr(rule, "large_caps", 0):
        key = f"__largest{rule.large_caps}__"
        if key not in fp._cache:
            caps = pd.DataFrame(fp.matrix("market_cap").T)                     # sessions × symbols
            ranks = caps.rank(axis=1, ascending=False, method="min").to_numpy().T
            fp._cache[key] = np.nan_to_num(ranks, nan=np.inf) <= rule.large_caps
        mask = mask & fp._cache[key]
    return mask


def member_percentiles(fp, rule) -> np.ndarray:
    """symbols × sessions: the rule's ranking score as a percentile (0..1) among the stocks it can rank."""
    mask = _eligible(fp, rule)
    columns = {name: fp.smoothed(name, getattr(rule, "smooth", 0)) for name in rule.factors}
    for values in columns.values():
        mask = mask & np.isfinite(values)
    if len(columns) == 1:
        name, weight = next(iter(rule.factors.items()))
        score = np.where(mask, columns[name] * (1 if weight > 0 else -1), np.nan)
    else:
        score = np.zeros(mask.shape)
        for name, weight in rule.factors.items():
            part = pd.DataFrame(np.where(mask, columns[name], np.nan)).rank(axis=0, pct=True).to_numpy()
            score = score + weight * part
        score = np.where(mask, score, np.nan)
    return pd.DataFrame(score).rank(axis=0, pct=True).to_numpy()


def scores(fp, factor: str = "t0_consensus") -> np.ndarray:
    """sessions × symbols: the average member percentile over the members that cover the stock."""
    total = np.zeros((len(fp.symbols), len(fp.sessions)))
    votes = np.zeros((len(fp.symbols), len(fp.sessions)))
    for _trial, _name, rule in members(factor):
        part = member_percentiles(fp, rule)
        known = np.isfinite(part)
        total += np.where(known, part, 0.0)
        votes += known
    with np.errstate(invalid="ignore", divide="ignore"):
        consensus = np.where(votes > 0, total / votes, np.nan)
    return consensus.T.astype(np.float32)


def digest(history, factor: str = "t0_consensus") -> dict[str, object]:
    """The members and every trained model they read (part of a consensus rule's trial input)."""
    from quant_platform.research.daily import MODEL_FACTORS
    from quant_platform.research.model import MODELS, digest as model_digest, model_dir

    used = sorted({name for _trial, _name, rule in members(factor) for name in rule.factors if name in MODEL_FACTORS})
    return {"version": VERSIONS[factor], "members": members_digest(factor),
            "models": {name: model_digest(model_dir(history, MODELS[name]["version"])) for name in used}}
