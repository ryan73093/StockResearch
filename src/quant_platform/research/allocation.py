"""Target weights on an invest day — one implementation for the research engine and the Today
decision, so an approved rule trades exactly as it was tested.

``trailing(asset, sessions)`` returns up to ``sessions`` split-adjusted closes ending on the decision
day (the research engine adjusts with the recorded unit ratios, the Today decision with
``adjust_gaps``). Without ``defensive`` and ``rotation`` the target is simply ``weights``.
"""

from __future__ import annotations

from collections.abc import Callable

from quant_platform.research.spec import StrategySpec

Trailing = Callable[[str, int], list[float]]


def defensive_active(spec: StrategySpec, trailing: Trailing) -> bool:
    """Trend control: the signal closed below its moving average (too little history: no)."""
    defensive = spec.allocation.defensive
    if defensive is None:
        return False
    closes = trailing(defensive.signal_asset or spec.signal, defensive.ma_sessions)
    return len(closes) >= defensive.ma_sessions and closes[-1] < sum(closes) / len(closes)


def rotation_returns(spec: StrategySpec, trailing: Trailing) -> dict[str, float] | None:
    """Each candidate's return over the lookback; ``None`` while any candidate lacks the history."""
    rotation = spec.allocation.rotation
    if rotation is None:
        return None
    returns = {}
    for asset in rotation.candidates:
        closes = trailing(asset, rotation.lookback_sessions + 1)
        if len(closes) < rotation.lookback_sessions + 1 or closes[0] <= 0:
            return None
        returns[asset] = closes[-1] / closes[0] - 1
    return returns


def rotation_picks(spec: StrategySpec, returns: dict[str, float]) -> list[str]:
    rotation = spec.allocation.rotation
    best_first = rotation.mode == "momentum"
    ordered = sorted(returns, key=lambda asset: (-returns[asset] if best_first else returns[asset], asset))
    return ordered[: rotation.top]


def target_weights(spec: StrategySpec, trailing: Trailing) -> dict[str, float]:
    """Weight of every instrument of the spec (0 for those the rules do not want now)."""
    allocation = spec.allocation
    if defensive_active(spec, trailing):
        weights = dict(allocation.defensive.weights)
    else:
        returns = rotation_returns(spec, trailing)
        if returns is None:
            weights = dict(allocation.weights)
        else:
            weights = dict(allocation.rotation.core)
            picks = rotation_picks(spec, returns)
            share = (1 - sum(allocation.rotation.core.values())) / len(picks)
            for asset in picks:
                weights[asset] = weights.get(asset, 0.0) + share
    return {asset: weights.get(asset, 0.0) for asset in spec.assets}


def describe_target(spec: StrategySpec, trailing: Trailing) -> str | None:
    """Why the target is what it is, for the Today page's reasons."""
    if defensive_active(spec, trailing):
        defensive = spec.allocation.defensive
        target = "、".join(f"{asset} {weight:.0%}" for asset, weight in sorted(defensive.weights.items()))
        return (f"{defensive.signal_asset or spec.signal} 收盤低於 {defensive.ma_sessions} 日均線，"
                f"改用防守配置：{target or '保留現金、暫不買進'}。")
    returns = rotation_returns(spec, trailing)
    if returns is None:
        if spec.allocation.rotation is not None:
            return "輪動候選的行情還不夠長，先用預設配置。"
        return None
    rotation = spec.allocation.rotation
    ranked = "、".join(f"{asset} {value:+.1%}" for asset, value in sorted(returns.items(), key=lambda item: -item[1]))
    picks = "、".join(rotation_picks(spec, returns))
    rule = "最強" if rotation.mode == "momentum" else "最弱"
    return f"過去 {rotation.lookback_sessions} 個交易日報酬：{ranked}；輪動部位投入{rule}的 {picks}。"


def adjust_gaps(closes: list[float]) -> list[float]:
    """Undo splits in live closes. Taiwan limits a day's move to ±10%, so a move beyond ±40% is a
    split or a reverse split; earlier closes are divided by the ratio (rounded when it is close to a
    whole number) so moving averages and returns stay comparable."""
    adjusted = list(closes)
    for index in range(len(adjusted) - 1, 0, -1):
        previous, current = adjusted[index - 1], adjusted[index]
        if previous <= 0 or current <= 0:
            continue
        ratio = previous / current
        if 0.6 < ratio < 1 / 0.6:
            continue
        whole = round(ratio) if ratio >= 1 else 1 / round(1 / ratio)
        if abs(ratio - whole) <= 0.15 * whole:
            ratio = whole
        for earlier in range(index):
            adjusted[earlier] /= ratio
    return adjusted
