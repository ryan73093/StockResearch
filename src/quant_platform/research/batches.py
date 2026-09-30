"""Rule-based research batches (S4-W05 without the LLM researcher).

Each batch is a fixed, reviewable list of strategy specs that probes one
hypothesis from the roadmap. Every spec runs as a registered candidate trial,
so the Deflated Sharpe Ratio and PBO account for all of them.
"""

from __future__ import annotations

from itertools import product

from quant_platform.research.spec import Allocation, Contribution, Sizing, StrategySpec


def _single(asset: str = "0050") -> Allocation:
    return Allocation(weights={asset: 1.0})


def first_batch() -> list[StrategySpec]:
    specs: list[StrategySpec] = []
    # 1. Timing of a plain DCA inside the month.
    for day in (6, 16, 26):
        specs.append(StrategySpec(
            name=f"時點：每月 {day} 日全數買進 0050",
            description="薪資 5 日入帳，延到固定日才投入。",
            allocation=_single(),
            contribution=Contribution(invest_on="day_of_month", day_of_month=day),
        ))
    # 2. Value averaging on the moving average.
    for sessions, weak, strong in product((60, 120, 200), (1.5, 2.0, 3.0), (0.5, 0.75, 1.0)):
        specs.append(StrategySpec(
            name=f"均線 {sessions} 日：弱勢 ×{weak:g}、強勢 ×{strong:g}",
            description="0050 收盤低於均線時多投入、高於均線時少投入；閒置現金上限 6 個月。",
            allocation=_single(),
            sizing=Sizing(
                type="moving_average", ma_sessions=sessions,
                weak_multiplier=weak, strong_multiplier=strong, max_reserve_months=6,
            ),
        ))
    # 3. Value averaging on the drawdown from the one-year high.
    for threshold, weak, strong in product((0.05, 0.10, 0.15, 0.20), (1.5, 2.0, 3.0), (0.5, 0.75, 1.0)):
        specs.append(StrategySpec(
            name=f"回撤 {threshold:.0%}：弱勢 ×{weak:g}、強勢 ×{strong:g}",
            description="0050 自一年高點回落超過門檻時多投入；閒置現金上限 6 個月。",
            allocation=_single(),
            sizing=Sizing(
                type="drawdown", drawdown_threshold=threshold,
                weak_multiplier=weak, strong_multiplier=strong, max_reserve_months=6,
            ),
        ))
    # 4. Which ETF to accumulate.
    for asset in ("0056", "006208"):
        specs.append(StrategySpec(
            name=f"標的：入帳日全數買進 {asset}",
            description="與基準相同的投入方式，只換標的。",
            allocation=_single(asset),
        ))
    return specs


BATCHES = {"first": first_batch}
