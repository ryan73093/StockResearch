"""Rule-based research batches (S4-W05 without the LLM researcher).

Each batch is a fixed, reviewable list of strategy specs that probes one
hypothesis from the roadmap. Every spec runs as a registered candidate trial,
so the Deflated Sharpe Ratio and PBO account for all of them.
"""

from __future__ import annotations

from itertools import product

from quant_platform.research.spec import Allocation, Contribution, Defensive, Rotation, Sizing, StrategySpec


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


def second_batch() -> list[StrategySpec]:
    """The directions spec v1 could not express (roadmap S4-W05, 2026-10-02): trend control, ETF
    rotation and core + satellite. Only 0050, 0056 (from 2007-12) and 006208 (from 2012-07) trade
    in the development period, and no bond ETF does, so the defensive side is cash or 0056 and the
    rotation is 0050 against 0056; trials holding 0056 start when it lists."""
    specs: list[StrategySpec] = []
    # 1. Trend control: below the moving average, hold cash or 0056 instead of 0050. "band" moves
    #    the holdings too; "with_new_money" only steers the month's money (and idle cash).
    for sessions, defensive, rebalance in product((60, 120, 200), ("cash", "0056"), ("band", "with_new_money")):
        target = "現金" if defensive == "cash" else "0056"
        action = "持股一起換" if rebalance == "band" else "只有新資金"
        specs.append(StrategySpec(
            name=f"趨勢 {sessions} 日：跌破改{target}（{action}）",
            description=f"0050 收盤低於 {sessions} 日均線時，目標改為{target}；"
                        + ("偏離超過 5 個百分點就賣出換過去，站回均線再換回 0050。" if rebalance == "band"
                           else "已持有的不賣，只有新資金與閒置現金依目標投入。"),
            allocation=Allocation(
                weights={"0050": 1.0}, rebalance=rebalance,
                defensive=Defensive(weights={} if defensive == "cash" else {"0056": 1.0}, ma_sessions=sessions),
            ),
        ))
    # 2. Rotation between the market-cap and the high-dividend ETF on past returns.
    for lookback, mode, rebalance in product((63, 126, 252), ("momentum", "reversal"), ("with_new_money", "band")):
        pick = "較強" if mode == "momentum" else "較弱"
        action = "持股一起換" if rebalance == "band" else "只有新資金"
        specs.append(StrategySpec(
            name=f"輪動 {lookback} 日：0050／0056 買{pick}的（{action}）",
            description=f"每個投入日比較 0050 與 0056 過去 {lookback} 個交易日的報酬，買{pick}的一檔；"
                        "0056 行情不足時買 0050。",
            allocation=Allocation(
                weights={"0050": 1.0}, rebalance=rebalance,
                rotation=Rotation(candidates=["0050", "0056"], lookback_sessions=lookback, top=1, mode=mode),
            ),
        ))
    # 3. Core + satellite: a fixed 0050 core; the satellite (10% or 20%) follows the rotation.
    for core, mode in product((0.8, 0.9), ("momentum", "reversal")):
        pick = "較強" if mode == "momentum" else "較弱"
        specs.append(StrategySpec(
            name=f"核心 0050 {core:.0%}＋衛星 {1 - core:.0%} 買{pick}的",
            description=f"0050 固定 {core:.0%}；其餘依 0050 與 0056 過去 126 個交易日的報酬買{pick}的一檔，"
                        "只有新資金依目標投入。",
            allocation=Allocation(
                weights={"0050": 1.0},
                rotation=Rotation(candidates=["0050", "0056"], lookback_sessions=126, top=1, mode=mode,
                                  core={"0050": core}),
            ),
        ))
    return specs


BATCHES = {"first": first_batch, "second": second_batch}
