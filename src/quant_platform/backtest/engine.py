from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from quant_platform.domain.entities import (
    BacktestArtifact,
    BacktestEquityPoint,
    BacktestFold,
    BacktestRun,
    BacktestTrade,
    MarketBar,
    RegimeState,
)
from quant_platform.strategy.base import SignalStrategy


RESEARCH_VERSION = "1.0.0"


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    commission_bps: float = 5.0
    slippage_bps: float = 5.0


@dataclass(frozen=True, slots=True)
class RiskPolicy:
    stop_loss: float = 0.08
    take_profit: float = 0.20
    max_position: float = 1.0
    target_volatility: float = 0.18


@dataclass(frozen=True, slots=True)
class WalkForwardPolicy:
    train_sessions: int = 252
    test_sessions: int = 63
    step_sessions: int = 63
    minimum_folds: int = 2


@dataclass(frozen=True, slots=True)
class ResearchIntegrityPolicy:
    survivorship_safe: bool = False


@dataclass(slots=True)
class _Simulation:
    returns: list[float]
    benchmark_returns: list[float]
    positions: list[float]
    event_times: list[datetime]
    trades: list[BacktestTrade]
    turnover: float


def _equity(returns: list[float]) -> list[float]:
    value = 1.0
    output = []
    for item in returns:
        value *= 1 + item
        output.append(value)
    return output


def _max_drawdown(curve: list[float]) -> float:
    peak = 1.0
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def _metrics(returns: list[float], benchmark: list[float]) -> dict[str, float | None]:
    if not returns:
        return {key: 0.0 for key in ("total", "annual", "benchmark", "excess", "sharpe", "sortino", "calmar", "max_drawdown", "win_rate")}
    curve = _equity(returns)
    benchmark_curve = _equity(benchmark)
    total = curve[-1] - 1
    benchmark_total = benchmark_curve[-1] - 1
    annual = (1 + total) ** (252 / len(returns)) - 1 if total > -1 else -1.0
    deviation = statistics.stdev(returns) if len(returns) > 1 else 0.0
    sharpe = statistics.fmean(returns) / deviation * math.sqrt(252) if deviation else 0.0
    downside = [min(value, 0.0) for value in returns]
    downside_deviation = math.sqrt(statistics.fmean([value * value for value in downside]))
    sortino = statistics.fmean(returns) / downside_deviation * math.sqrt(252) if downside_deviation else 0.0
    max_drawdown = _max_drawdown(curve)
    active = [value for value in returns if abs(value) > 1e-12]
    gains = sum(value for value in active if value > 0)
    losses = abs(sum(value for value in active if value < 0))
    active_returns = [value - base for value, base in zip(returns, benchmark)]
    active_deviation = statistics.stdev(active_returns) if len(active_returns) > 1 else 0.0
    beta = None
    alpha = None
    if len(returns) > 1 and statistics.variance(benchmark) > 0:
        beta = statistics.covariance(returns, benchmark) / statistics.variance(benchmark)
        alpha = (statistics.fmean(returns) - beta * statistics.fmean(benchmark)) * 252
    return {
        "total": total,
        "annual": annual,
        "benchmark": benchmark_total,
        "excess": total - benchmark_total,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": annual / abs(max_drawdown) if max_drawdown else 0.0,
        "max_drawdown": max_drawdown,
        "win_rate": sum(value > 0 for value in active) / len(active) if active else 0.0,
        "profit_factor": gains / losses if losses else None,
        "alpha": alpha,
        "beta": beta,
        "information_ratio": (
            statistics.fmean(active_returns) / active_deviation * math.sqrt(252)
            if active_deviation
            else None
        ),
    }


class BiasSafeBacktestEngine:
    """Walk-forward parameter selection with next-open execution and OOS-only reporting."""

    def __init__(
        self,
        execution_policy: ExecutionPolicy | None = None,
        risk_policy: RiskPolicy | None = None,
        walk_forward_policy: WalkForwardPolicy | None = None,
        integrity_policy: ResearchIntegrityPolicy | None = None,
    ) -> None:
        self.execution = execution_policy or ExecutionPolicy()
        self.risk = risk_policy or RiskPolicy()
        self.walk_forward = walk_forward_policy or WalkForwardPolicy()
        self.integrity = integrity_policy or ResearchIntegrityPolicy()

    def run(
        self,
        bars: list[MarketBar],
        strategy: SignalStrategy,
        regimes: list[RegimeState] | None = None,
        computed_at: datetime | None = None,
        survivorship_safe: bool | None = None,
    ) -> BacktestArtifact | None:
        if len(bars) < self.walk_forward.train_sessions + self.walk_forward.test_sessions:
            return None
        calculated_at = computed_at or datetime.now(UTC)
        ordered = sorted(bars, key=lambda item: item.event_time)
        frame = self._frame(ordered)
        regimes_by_date = {item.event_time.date(): item.composite_regime for item in regimes or []}
        folds: list[BacktestFold] = []
        simulations: list[_Simulation] = []
        selected_parameters: list[dict[str, float | int]] = []
        test_start = self.walk_forward.train_sessions
        sequence = 1
        while test_start + self.walk_forward.test_sessions <= len(frame):
            train_end = test_start - 1
            train_start = max(0, train_end - self.walk_forward.train_sessions + 1)
            test_end = test_start + self.walk_forward.test_sessions - 1
            best_parameters, train_metrics = self._select_parameters(
                frame, strategy, regimes_by_date, train_start, train_end
            )
            signals = strategy.generate(frame, best_parameters, regimes_by_date)
            simulation = self._simulate(frame, signals, test_start, test_end)
            test_metrics = _metrics(simulation.returns, simulation.benchmark_returns)
            folds.append(
                BacktestFold(
                    sequence=sequence,
                    train_start=frame.iloc[train_start]["event_time"],
                    train_end=frame.iloc[train_end]["event_time"],
                    test_start=frame.iloc[test_start]["event_time"],
                    test_end=frame.iloc[test_end]["event_time"],
                    train_observations=train_end - train_start + 1,
                    test_observations=test_end - test_start + 1,
                    selected_parameters_json=json.dumps(best_parameters, sort_keys=True),
                    train_sharpe=float(train_metrics["sharpe"] or 0),
                    test_return=float(test_metrics["total"] or 0),
                    benchmark_return=float(test_metrics["benchmark"] or 0),
                    test_sharpe=float(test_metrics["sharpe"] or 0),
                    max_drawdown=float(test_metrics["max_drawdown"] or 0),
                    trade_count=len(simulation.trades),
                )
            )
            simulations.append(simulation)
            selected_parameters.append(best_parameters)
            sequence += 1
            test_start += self.walk_forward.step_sessions
        if len(folds) < self.walk_forward.minimum_folds:
            return None

        returns = [value for simulation in simulations for value in simulation.returns]
        benchmark_returns = [value for simulation in simulations for value in simulation.benchmark_returns]
        positions = [value for simulation in simulations for value in simulation.positions]
        event_times = [value for simulation in simulations for value in simulation.event_times]
        trades = [value for simulation in simulations for value in simulation.trades]
        metrics = _metrics(returns, benchmark_returns)
        curve = _equity(returns)
        benchmark_curve = _equity(benchmark_returns)
        peak = 1.0
        equity_points = []
        for index, event_time in enumerate(event_times):
            peak = max(peak, curve[index])
            equity_points.append(
                BacktestEquityPoint(
                    event_time=event_time,
                    equity=curve[index],
                    benchmark_equity=benchmark_curve[index],
                    drawdown=curve[index] / peak - 1,
                    daily_return=returns[index],
                    position=positions[index],
                )
            )
        positive_fold_rate = sum(item.test_return > 0 for item in folds) / len(folds)
        integrity_safe = self.integrity.survivorship_safe if survivorship_safe is None else survivorship_safe
        promotion_gate = self._promotion_gate(metrics, folds, len(returns), integrity_safe)
        limitations = [
            "OOS folds are non-overlapping, but candidate strategies share the same research universe.",
            "Corporate-action accuracy depends on the adjusted OHLC supplied by the data provider.",
            "Liquidity participation, borrow constraints and market-impact curves are not yet modeled.",
        ]
        if not integrity_safe:
            limitations.append(
                "Historical universe membership is versioned but has incomplete boundaries, so cross-asset conclusions are not survivorship-safe."
            )
        first = ordered[0]
        run = BacktestRun(
            id=None,
            symbol=first.symbol,
            market=first.market,
            strategy_name=strategy.name,
            strategy_version=strategy.version,
            research_version=RESEARCH_VERSION,
            promotion_gate=promotion_gate,
            data_start=event_times[0],
            data_end=event_times[-1],
            fold_count=len(folds),
            observation_count=len(returns),
            total_return=float(metrics["total"] or 0),
            annual_return=float(metrics["annual"] or 0),
            benchmark_return=float(metrics["benchmark"] or 0),
            excess_return=float(metrics["excess"] or 0),
            sharpe=float(metrics["sharpe"] or 0),
            sortino=float(metrics["sortino"] or 0),
            calmar=float(metrics["calmar"] or 0),
            max_drawdown=float(metrics["max_drawdown"] or 0),
            win_rate=float(metrics["win_rate"] or 0),
            profit_factor=metrics["profit_factor"],
            turnover=sum(item.turnover for item in simulations),
            exposure=statistics.fmean(positions) if positions else 0.0,
            alpha=metrics["alpha"],
            beta=metrics["beta"],
            information_ratio=metrics["information_ratio"],
            trade_count=len(trades),
            positive_fold_rate=positive_fold_rate,
            commission_bps=self.execution.commission_bps,
            slippage_bps=self.execution.slippage_bps,
            parameters_json=json.dumps(selected_parameters, sort_keys=True),
            limitations_json=json.dumps(limitations),
            computed_at=calculated_at,
        )
        return BacktestArtifact(run=run, folds=folds, trades=trades, equity=equity_points)

    def _select_parameters(self, frame, strategy, regimes_by_date, start, end):
        best_parameters = strategy.parameter_grid[0]
        best_metrics = {"sharpe": -math.inf}
        for parameters in strategy.parameter_grid:
            signals = strategy.generate(frame, parameters, regimes_by_date)
            simulation = self._simulate(frame, signals, start, end)
            metrics = _metrics(simulation.returns, simulation.benchmark_returns)
            if float(metrics["sharpe"] or 0) > float(best_metrics["sharpe"] or 0):
                best_parameters = parameters
                best_metrics = metrics
        return best_parameters, best_metrics

    def _simulate(self, frame, signals, start, end) -> _Simulation:
        returns: list[float] = []
        benchmark_returns: list[float] = []
        positions: list[float] = []
        event_times: list[datetime] = []
        trades: list[BacktestTrade] = []
        position = 0.0
        entry_price: float | None = None
        entry_time: datetime | None = None
        entry_index: int | None = None
        entry_size = 0.0
        accumulated_cost = 0.0
        total_turnover = 0.0
        cost_per_turn = (self.execution.commission_bps + self.execution.slippage_bps) / 10_000
        volatility = frame["close"].pct_change(fill_method=None).rolling(20, min_periods=20).std(ddof=1) * math.sqrt(252)
        for index in range(max(1, start), end + 1):
            previous_close = float(frame.iloc[index - 1]["close"])
            open_price = float(frame.iloc[index]["open"])
            close_price = float(frame.iloc[index]["close"])
            desired_signal = float(signals.iloc[index - 1])
            current_volatility = float(volatility.iloc[index - 1])
            size = (
                min(self.risk.max_position, self.risk.target_volatility / current_volatility)
                if math.isfinite(current_volatility) and current_volatility > 0
                else self.risk.max_position
            )
            desired = desired_signal * size
            exit_reason = "SIGNAL"
            if position > 0 and entry_price is not None:
                known_return = previous_close / entry_price - 1
                if known_return <= -self.risk.stop_loss:
                    desired = 0.0
                    exit_reason = "STOP_LOSS"
                elif known_return >= self.risk.take_profit:
                    desired = 0.0
                    exit_reason = "TAKE_PROFIT"
            overnight = position * (open_price / previous_close - 1)
            turnover = abs(desired - position)
            cost = turnover * cost_per_turn
            total_turnover += turnover
            accumulated_cost += cost
            if position <= 1e-12 and desired > 1e-12:
                entry_price = open_price
                entry_time = frame.iloc[index]["event_time"]
                entry_index = index
                entry_size = desired
                accumulated_cost = cost
            elif position > 1e-12 and desired <= 1e-12 and entry_price is not None:
                trades.append(
                    BacktestTrade(
                        entry_time=entry_time,
                        exit_time=frame.iloc[index]["event_time"],
                        entry_price=entry_price,
                        exit_price=open_price,
                        size=entry_size,
                        net_return=(open_price / entry_price - 1) * entry_size - accumulated_cost,
                        holding_sessions=index - int(entry_index),
                        exit_reason=exit_reason,
                        costs=accumulated_cost,
                    )
                )
                entry_price = entry_time = entry_index = None
                entry_size = 0.0
                accumulated_cost = 0.0
            intraday = desired * (close_price / open_price - 1)
            daily_return = (1 + overnight) * (1 + intraday) * (1 - cost) - 1
            returns.append(daily_return)
            benchmark_returns.append(close_price / previous_close - 1)
            positions.append(desired)
            event_times.append(frame.iloc[index]["event_time"])
            position = desired
        if position > 1e-12 and entry_price is not None:
            close_price = float(frame.iloc[end]["close"])
            closing_cost = position * cost_per_turn
            returns[-1] = (1 + returns[-1]) * (1 - closing_cost) - 1
            total_turnover += position
            trades.append(
                BacktestTrade(
                    entry_time=entry_time,
                    exit_time=frame.iloc[end]["event_time"],
                    entry_price=entry_price,
                    exit_price=close_price,
                    size=entry_size,
                    net_return=(close_price / entry_price - 1) * entry_size - accumulated_cost - closing_cost,
                    holding_sessions=end - int(entry_index),
                    exit_reason="END_OF_FOLD",
                    costs=accumulated_cost + closing_cost,
                )
            )
            positions[-1] = 0.0
        return _Simulation(returns, benchmark_returns, positions, event_times, trades, total_turnover)

    def _promotion_gate(self, metrics, folds, observations, survivorship_safe: bool) -> str:
        positive_rate = sum(item.test_return > 0 for item in folds) / len(folds)
        conditions = (
            len(folds) >= 4,
            observations >= 252,
            float(metrics["excess"] or 0) > 0,
            float(metrics["sharpe"] or 0) >= 0.75,
            float(metrics["max_drawdown"] or 0) >= -0.25,
            float(metrics["profit_factor"] or 0) >= 1.10,
            positive_rate >= 2 / 3,
            survivorship_safe,
        )
        return "CANDIDATE" if all(conditions) else "RESEARCH"

    @staticmethod
    def _frame(bars: list[MarketBar]) -> pd.DataFrame:
        adjustment = [
            float(item.adjusted_close / item.close)
            if item.adjusted_close is not None and item.close != 0
            else 1.0
            for item in bars
        ]
        return pd.DataFrame(
            {
                "event_time": [item.event_time for item in bars],
                "available_time": [item.available_time for item in bars],
                "open": [float(item.open) * adjustment[index] for index, item in enumerate(bars)],
                "high": [float(item.high) * adjustment[index] for index, item in enumerate(bars)],
                "low": [float(item.low) * adjustment[index] for index, item in enumerate(bars)],
                "close": [float(item.adjusted_close or item.close) for item in bars],
                "volume": [item.volume for item in bars],
            }
        )
