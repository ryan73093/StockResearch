"""2026-10-20 (使用者：研究這些策略是否能夠輔助策略——因子負責什麼、策略負責什麼、DL、RL、ML 各負責什麼): the division of
labour, read from the registered accounts (no trial).

Each role's representative (REPRESENTATIVES, the best of its kind on 10/20): month by month, its account's return
(month-end values less the month's money) against 0050's, by the market's state that month — 0050 up strongly, flat,
down; inside a 10% fall from its high; calm or volatile — and by the state known at the month's start (0050 above or
below its 10-month average), in the development period and after; the monthly excess's correlations; the forward
accounts' holdings in common. Shown on the strategy roles page (研究 › 策略角色 › 分工).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

REPRESENTATIVES = (
    ("深度學習", 4941, "進攻選股"),
    ("機器學習", 5040, "第二個進攻手"),
    ("因子共識", 4589, "防守"),
    ("因子穩健", 5052, "防守"),
    ("跌市穩", 4547, "防守"),
    ("RL 機器人", 5044, "分配比重"),
    ("組合 #5054", 5054, "攻守同帳戶"),
)
STRONG = 0.03                      # a month of 0050 up or down more than this
_cache: dict[str, object] = {}


def _months(research: Path, report_file: str) -> pd.DataFrame:
    report = json.loads((research / "reports" / report_file).read_text(encoding="utf-8"))
    curve = report.get("curve") or {}
    months = sorted(curve)
    frame = pd.DataFrame([curve[month] for month in months], index=months, columns=["value", "bench", "paid"])
    added = frame["paid"].diff().fillna(0)
    return pd.DataFrame({"rule": (frame["value"] - added) / frame["value"].shift(1) - 1,
                         "bench": (frame["bench"] - added) / frame["bench"].shift(1) - 1}).dropna()


def _cell(values: pd.Series) -> dict[str, object] | None:
    values = values.dropna()
    if not len(values):
        return None
    return {"mean": round(float(values.mean()), 5), "win": round(float((values > 0).mean()), 3), "months": len(values)}


def diagnose(research: str | Path) -> dict[str, object]:
    """The states table, the known-state table, the correlations and the holdings in common (cached by the registry)."""
    from quant_platform.research.registry import TrialRegistry
    from quant_platform.research.stock_forward import StockForwardTracker

    research = Path(research)
    path = research / "trials.jsonl"
    key = f"{path.stat().st_mtime_ns}" if path.is_file() else "none"
    if _cache.get("key") == key:
        return _cache["value"]                                       # type: ignore[return-value]
    records = {record.trial_id: record for record in TrialRegistry(path).records()} if path.is_file() else {}
    roles, data = [], {}
    for name, trial, job in REPRESENTATIVES:
        record = records.get(trial)
        if record is None or not record.report_file:
            continue
        try:
            data[name] = _months(research, record.report_file)
        except (OSError, ValueError, KeyError):
            continue
        roles.append({"name": name, "trial": trial, "job": job, "rule_hash": record.spec_hash,
                      "final_value": record.metrics.get("final_value")})
    if not data:
        value = {"roles": [], "states": [], "known": [], "correlations": [], "overlap": []}
        _cache.update(key=key, value=value)
        return value
    bench = max(data.values(), key=len)["bench"]
    excess = pd.DataFrame({name: frame["rule"] - frame["bench"] for name, frame in data.items()})
    level = (1 + bench).cumprod()
    trend = level / level.rolling(10, min_periods=6).mean() - 1
    falling = level / level.cummax() - 1 <= -0.10
    volatility = bench.rolling(6, min_periods=4).std()
    states = [("0050 大漲月（漲超過 3%）", bench > STRONG), ("0050 小漲小跌月（±3% 內）", bench.abs() <= STRONG),
              ("0050 大跌月（跌超過 3%）", bench < -STRONG), ("0050 從高點跌 10% 以上的期間", falling),
              ("高波動月（前 6 個月波動最高的三分之一）", volatility >= volatility.quantile(2 / 3)),
              ("低波動月（最低的三分之一）", volatility <= volatility.quantile(1 / 3))]
    table = []
    for label, mask in states:
        mask = mask.reindex(excess.index).fillna(False).astype(bool)
        table.append({"state": label, "months": int(mask.sum()),
                      "cells": {name: _cell(excess.loc[mask, name]) for name in excess}})
    known = trend.shift(1).reindex(excess.index)
    rows = []
    for period, chosen in (("開發期 2015-06～2020-09", excess.index <= "2020-09"), ("2020-10 起", excess.index >= "2020-10")):
        for label, mask in (("月初 0050 在 10 個月均線上", known > 0), ("月初 0050 在 10 個月均線下", known <= 0)):
            picked = chosen & mask.fillna(False).to_numpy()
            rows.append({"state": f"{period}・{label}", "months": int(picked.sum()),
                         "cells": {name: _cell(excess.loc[picked, name]) for name in excess}})
    correlation = excess.corr()
    correlations = [{"name": name, "values": {other: round(float(correlation.loc[name, other]), 2)
                                              for other in correlation.columns}} for name in correlation.index]
    held: dict[str, set[str]] = {}
    try:
        accounts = {row["rule_hash"]: row for row in StockForwardTracker(research).summary() if not row["stopped"]}
        for role in roles:
            account = accounts.get(role["rule_hash"])
            if account and account.get("holdings") and "組合" not in role["name"]:
                held[role["name"]] = {str(item["code"]) for item in account["holdings"]}
    except (OSError, ValueError, KeyError):
        held = {}
    overlap = [{"name": name, "count": len(codes), "values": {other: len(codes & held[other]) for other in held
                                                              if other != name}} for name, codes in held.items()]
    value = {"roles": roles, "names": list(excess.columns), "states": table, "known": rows,
             "correlations": correlations, "overlap": overlap,
             "since": str(excess.index[0]), "until": str(excess.index[-1])}
    _cache.update(key=key, value=value)
    return value
