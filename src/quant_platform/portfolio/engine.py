from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime

import numpy as np

from quant_platform.domain.entities import (
    EnsembleArtifact,
    PortfolioAllocationPoint,
    PortfolioArtifact,
    PortfolioEquityPoint,
    PortfolioRun,
)


PORTFOLIO_VERSION = "1.1.0"
PORTFOLIO_METHODS = (
    "equal_weight",
    "inverse_volatility",
    "minimum_variance",
    "mean_variance",
    "risk_parity",
    "hierarchical_risk_parity",
    "cvar",
    "kelly",
    "black_litterman",
    "benchmark_core_satellite",
)


def _sharpe(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    deviation = statistics.stdev(values)
    return statistics.fmean(values) / deviation * math.sqrt(252) if deviation else 0.0


def _sortino(values: list[float]) -> float:
    downside = [min(value, 0.0) for value in values]
    deviation = math.sqrt(statistics.fmean(value * value for value in downside)) if downside else 0.0
    return statistics.fmean(values) / deviation * math.sqrt(252) if deviation else 0.0


def _max_drawdown(values: list[float]) -> float:
    equity = peak = 1.0
    worst = 0.0
    for value in values:
        equity *= 1 + value
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    return worst


def _tail_metrics(values: list[float], probability: float = 0.05) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    ordered = sorted(values)
    count = max(1, math.ceil(len(ordered) * probability))
    tail = ordered[:count]
    return tail[-1], statistics.fmean(tail)


def _capped_simplex(raw: np.ndarray, target: float, cap: float) -> np.ndarray:
    size = len(raw)
    if size == 0 or target <= 0:
        return np.zeros(size)
    cap = max(cap, target / size)
    preference = np.clip(np.asarray(raw, dtype=float), 0.0, None)
    if not np.isfinite(preference).all() or preference.sum() <= 1e-15:
        preference = np.ones(size)
    result = np.zeros(size)
    active = set(range(size))
    remaining = float(target)
    while active and remaining > 1e-12:
        total = float(sum(preference[index] for index in active))
        proposed = {
            index: remaining / len(active)
            if total <= 0
            else remaining * preference[index] / total
            for index in active
        }
        capped = [index for index, value in proposed.items() if value >= cap - 1e-12]
        if not capped:
            for index, value in proposed.items():
                result[index] = value
            remaining = 0.0
        else:
            for index in capped:
                result[index] = cap
                remaining -= cap
            active.difference_update(capped)
    return result


def _cluster_variance(covariance: np.ndarray, indices: list[int]) -> float:
    sub = covariance[np.ix_(indices, indices)]
    inverse = 1.0 / np.clip(np.diag(sub), 1e-10, None)
    weights = inverse / inverse.sum()
    return float(weights @ sub @ weights)


def _hrp_weights(covariance: np.ndarray) -> np.ndarray:
    count = covariance.shape[0]
    if count == 1:
        return np.ones(1)
    volatility = np.sqrt(np.clip(np.diag(covariance), 1e-12, None))
    correlation = covariance / np.outer(volatility, volatility)
    correlation = np.clip(correlation, -1.0, 1.0)
    clusters: list[list[int]] = [[index] for index in range(count)]
    while len(clusters) > 1:
        best: tuple[float, int, int] | None = None
        for left in range(len(clusters)):
            for right in range(left + 1, len(clusters)):
                distance = statistics.fmean(
                    math.sqrt(max(0.0, (1 - correlation[i, j]) / 2))
                    for i in clusters[left]
                    for j in clusters[right]
                )
                candidate = (distance, left, right)
                if best is None or candidate < best:
                    best = candidate
        assert best is not None
        _, left, right = best
        merged = clusters[left] + clusters[right]
        clusters = [cluster for index, cluster in enumerate(clusters) if index not in {left, right}]
        clusters.append(merged)
    order = clusters[0]
    weights = np.ones(count)
    pending = [order]
    while pending:
        cluster = pending.pop()
        if len(cluster) <= 1:
            continue
        midpoint = len(cluster) // 2
        left, right = cluster[:midpoint], cluster[midpoint:]
        left_variance = _cluster_variance(covariance, left)
        right_variance = _cluster_variance(covariance, right)
        alpha = 1 - left_variance / max(left_variance + right_variance, 1e-12)
        weights[left] *= alpha
        weights[right] *= 1 - alpha
        pending.extend([left, right])
    return weights / weights.sum()


class PortfolioRiskEngine:
    """Rolling, long-only portfolio research with explicit constraints and risk diagnostics."""

    version = PORTFOLIO_VERSION

    def __init__(
        self,
        training_window: int = 60,
        rebalance_every: int = 21,
        transaction_cost_bps: float = 5.0,
        max_asset_weight: float = 0.35,
        max_sector_weight: float = 0.50,
    ) -> None:
        self.training_window = training_window
        self.rebalance_every = rebalance_every
        self.transaction_cost_bps = transaction_cost_bps
        self.max_asset_weight = max_asset_weight
        self.max_sector_weight = max_sector_weight

    def run(
        self,
        method: str,
        ensembles: list[EnsembleArtifact],
        benchmark_returns: dict[date, float],
        sectors: dict[str, str],
        liquidity: dict[str, float],
        computed_at: datetime | None = None,
    ) -> PortfolioArtifact | None:
        if method not in PORTFOLIO_METHODS:
            raise ValueError(f"unsupported portfolio method: {method}")
        if len(ensembles) < 2:
            return None
        symbols = sorted({artifact.run.symbol for artifact in ensembles})
        markets = {artifact.run.market for artifact in ensembles}
        if len(markets) != 1 or len(symbols) != len(ensembles):
            raise ValueError("portfolio inputs must contain one ensemble per symbol and market")
        by_symbol = {artifact.run.symbol: artifact for artifact in ensembles}
        return_maps = {
            symbol: {point.event_time: point.daily_return for point in by_symbol[symbol].weights}
            for symbol in symbols
        }
        common_dates = sorted(set.intersection(*(set(values) for values in return_maps.values())))
        common_dates = [item for item in common_dates if item.date() in benchmark_returns]
        if len(common_dates) <= self.training_window:
            return None
        returns = np.array(
            [[return_maps[symbol][event_time] for symbol in symbols] for event_time in common_dates],
            dtype=float,
        )
        benchmark = np.array([benchmark_returns[item.date()] for item in common_dates], dtype=float)
        cap = max(self.max_asset_weight, 1 / len(symbols))
        equal_target = np.full(len(symbols), 1 / len(symbols))
        current = np.zeros(len(symbols))
        current_benchmark = 0.0
        current_cash = 1.0
        equal_current = np.zeros(len(symbols))
        equal_cash = 1.0
        portfolio_returns: list[float] = []
        equal_returns: list[float] = []
        benchmark_oos: list[float] = []
        allocations: list[PortfolioAllocationPoint] = []
        equity_points: list[PortfolioEquityPoint] = []
        equity = equal_equity = benchmark_equity = peak = 1.0
        total_turnover = 0.0

        for index in range(self.training_window, len(common_dates)):
            rebalance = (index - self.training_window) % self.rebalance_every == 0
            if rebalance:
                history = returns[index - self.training_window : index]
                benchmark_history = benchmark[index - self.training_window : index]
                raw, exposure = self._allocate(method, history, benchmark_history)
                benchmark_target = 0.0
                if method == "benchmark_core_satellite":
                    benchmark_60d = float(np.prod(1 + benchmark_history) - 1)
                    benchmark_20d = float(
                        np.prod(1 + benchmark_history[-20:]) - 1
                    )
                    benchmark_target = (
                        0.80
                        if benchmark_60d > 0 and benchmark_20d > 0
                        else 0.60
                        if benchmark_60d > 0 or benchmark_20d > 0
                        else 0.30
                    )
                    exposure = 1.0 - benchmark_target
                target = _capped_simplex(raw, exposure, cap)
                target, flags = self._apply_sector_limits(target, symbols, sectors, cap)
                cash = max(
                    0.0, 1 - benchmark_target - float(target.sum())
                )
                turnover = float(
                    np.abs(target - current).sum()
                    + abs(benchmark_target - current_benchmark)
                    + abs(cash - current_cash)
                )
                cost = turnover * self.transaction_cost_bps / 10_000
                equal_turnover = float(
                    np.abs(equal_target - equal_current).sum() + abs(equal_cash)
                )
                equal_cost = equal_turnover * self.transaction_cost_bps / 10_000
                current, current_benchmark, current_cash = (
                    target, benchmark_target, cash
                )
                equal_current, equal_cash = equal_target.copy(), 0.0
                total_turnover += turnover
                allocations.append(
                    PortfolioAllocationPoint(
                        event_time=common_dates[index],
                        weights_json=json.dumps(
                            dict(zip(symbols, map(float, current), strict=True)),
                            sort_keys=True,
                        ),
                        cash_weight=current_cash,
                        turnover=turnover,
                        cost=cost,
                        constraint_flags_json=json.dumps(flags),
                    )
                )
            else:
                cost = 0.0
                equal_cost = 0.0

            current_returns = returns[index]
            benchmark_daily = float(benchmark[index])
            gross_return = float(
                current @ current_returns
                + current_benchmark * benchmark_daily
            )
            equal_gross = float(equal_current @ current_returns)
            daily_return = gross_return - cost
            equal_daily = equal_gross - equal_cost
            equity *= 1 + daily_return
            equal_equity *= 1 + equal_daily
            benchmark_equity *= 1 + benchmark_daily
            peak = max(peak, equity)
            drawdown = equity / peak - 1
            portfolio_returns.append(daily_return)
            equal_returns.append(equal_daily)
            benchmark_oos.append(benchmark_daily)
            equity_points.append(
                PortfolioEquityPoint(
                    event_time=common_dates[index],
                    equity=equity,
                    equal_weight_equity=equal_equity,
                    benchmark_equity=benchmark_equity,
                    drawdown=drawdown,
                    daily_return=daily_return,
                )
            )
            denominator = max(1 + gross_return, 1e-12)
            current = current * (1 + current_returns) / denominator
            current_benchmark = (
                current_benchmark * (1 + benchmark_daily) / denominator
            )
            current_cash = current_cash / denominator
            equal_current, equal_cash = self._drift(
                equal_current, equal_cash, current_returns, equal_gross
            )

        latest_weights = (
            json.loads(allocations[-1].weights_json)
            if allocations
            else dict(zip(symbols, map(float, equal_target), strict=True))
        )
        weight_array = np.array([latest_weights[symbol] for symbol in symbols])
        risk_window = returns[-self.training_window :]
        covariance = np.cov(risk_window, rowvar=False, ddof=1)
        volatility = np.sqrt(np.clip(np.diag(covariance), 1e-12, None))
        portfolio_variance = float(weight_array @ covariance @ weight_array)
        portfolio_volatility = math.sqrt(max(portfolio_variance, 0.0))
        diversification_ratio = (
            float(weight_array @ volatility) / portfolio_volatility
            if portfolio_volatility > 0
            else 0.0
        )
        correlation = np.nan_to_num(np.corrcoef(risk_window, rowvar=False), nan=0.0)
        average_correlation = float(
            (correlation.sum() - len(symbols)) / (len(symbols) * (len(symbols) - 1))
        )
        var_95, cvar_95 = _tail_metrics(portfolio_returns)
        beta = self._beta(portfolio_returns, benchmark_oos)
        total_return = equity - 1
        equal_return = equal_equity - 1
        benchmark_return = benchmark_equity - 1
        observations = len(portfolio_returns)
        annual_return = equity ** (252 / observations) - 1 if observations else 0.0
        annual_volatility = (
            statistics.stdev(portfolio_returns) * math.sqrt(252)
            if len(portfolio_returns) > 1
            else 0.0
        )
        sector_exposure = self._sector_exposure(symbols, weight_array, sectors)
        factor_exposure = self._factor_exposure(
            risk_window, benchmark[-self.training_window :], weight_array
        )
        if method == "benchmark_core_satellite":
            latest_cash = allocations[-1].cash_weight if allocations else 0.0
            factor_exposure["benchmark_core"] = max(
                0.0, 1 - float(weight_array.sum()) - latest_cash
            )
            factor_exposure["cash"] = max(
                0.0,
                1
                - float(weight_array.sum())
                - factor_exposure["benchmark_core"],
            )
        stress_tests = self._stress_tests(
            portfolio_returns, weight_array, volatility, beta
        )
        liquidity_risk = self._liquidity_risk(symbols, weight_array, liquidity)
        component_blocked = any(
            artifact.run.promotion_gate != "CANDIDATE" for artifact in ensembles
        )
        survivorship_blocked = any(
            "survivorship-safe" in artifact.run.limitations_json.lower()
            for artifact in ensembles
        )
        gates_pass = (
            observations >= 252
            and total_return > equal_return
            and total_return > benchmark_return
            and _sharpe(portfolio_returns) >= 0.75
            and _max_drawdown(portfolio_returns) >= -0.20
            and cvar_95 >= -0.05
            and not component_blocked
            and not survivorship_blocked
        )
        limitations = [
            "Each rebalance at t uses only the trailing window ending at t-1.",
            "Long-only allocation; shorting, leverage, taxes and borrow costs are excluded.",
            "Liquidity uses average dollar volume as a proxy, not an executable capacity model.",
            "Sector labels are research metadata and do not yet use a licensed classification history.",
        ]
        if component_blocked:
            limitations.append("One or more ensemble components are not promotion candidates.")
        if survivorship_blocked:
            limitations.append("The component universe is not survivorship-safe.")
        calculated_at = computed_at or datetime.now(UTC)
        run = PortfolioRun(
            id=None,
            market=next(iter(markets)),
            method=method,
            portfolio_version=self.version,
            promotion_gate="CANDIDATE" if gates_pass else "RESEARCH",
            data_start=equity_points[0].event_time,
            data_end=equity_points[-1].event_time,
            observation_count=observations,
            asset_count=len(symbols),
            total_return=total_return,
            annual_return=annual_return,
            equal_weight_return=equal_return,
            benchmark_return=benchmark_return,
            excess_to_equal=total_return - equal_return,
            excess_to_benchmark=total_return - benchmark_return,
            annual_volatility=annual_volatility,
            sharpe=_sharpe(portfolio_returns),
            sortino=_sortino(portfolio_returns),
            max_drawdown=_max_drawdown(portfolio_returns),
            var_95=var_95,
            cvar_95=cvar_95,
            beta=beta,
            turnover=total_turnover,
            transaction_cost_bps=self.transaction_cost_bps,
            effective_asset_count=1 / max(float(weight_array @ weight_array), 1e-12),
            diversification_ratio=diversification_ratio,
            average_correlation=average_correlation,
            liquidity_risk=liquidity_risk,
            latest_weights_json=json.dumps(latest_weights, sort_keys=True),
            sector_exposure_json=json.dumps(sector_exposure, sort_keys=True),
            factor_exposure_json=json.dumps(factor_exposure, sort_keys=True),
            stress_tests_json=json.dumps(stress_tests, sort_keys=True),
            limitations_json=json.dumps(limitations),
            computed_at=calculated_at,
        )
        return PortfolioArtifact(run=run, allocations=allocations, equity=equity_points)

    def _allocate(
        self, method: str, history: np.ndarray, benchmark: np.ndarray
    ) -> tuple[np.ndarray, float]:
        count = history.shape[1]
        covariance = np.cov(history, rowvar=False, ddof=1)
        covariance = np.atleast_2d(covariance) + np.eye(count) * 1e-8
        expected = np.clip(history.mean(axis=0), -0.01, 0.01) * 0.50
        volatility = np.sqrt(np.clip(np.diag(covariance), 1e-12, None))
        if method == "equal_weight":
            return np.ones(count), 1.0
        if method == "inverse_volatility":
            return 1 / volatility, 1.0
        if method == "hierarchical_risk_parity":
            return _hrp_weights(covariance), 1.0
        if method == "risk_parity":
            weights = np.full(count, 1 / count)
            for _ in range(200):
                marginal = covariance @ weights
                contributions = np.clip(weights * marginal, 1e-12, None)
                target = float(weights @ covariance @ weights) / count
                updated = weights * np.sqrt(target / contributions)
                updated = _capped_simplex(updated, 1.0, 1.0)
                if np.max(np.abs(updated - weights)) < 1e-8:
                    break
                weights = updated
            return weights, 1.0
        if method == "minimum_variance":
            weights = np.full(count, 1 / count)
            step = 0.15 / max(float(np.linalg.norm(covariance, ord=2)), 1e-8)
            for _ in range(300):
                weights = _capped_simplex(weights - step * (covariance @ weights), 1.0, 1.0)
            return weights, 1.0
        if method == "mean_variance":
            weights = np.full(count, 1 / count)
            risk_aversion = 8.0
            step = 0.05 / max(float(np.linalg.norm(covariance, ord=2)), 1e-8)
            for _ in range(300):
                gradient = risk_aversion * (covariance @ weights) - expected
                weights = _capped_simplex(weights - step * gradient, 1.0, 1.0)
            return weights, 1.0
        if method == "cvar":
            weights = np.full(count, 1 / count)
            for iteration in range(250):
                portfolio = history @ weights
                threshold = np.quantile(portfolio, 0.05)
                worst = history[portfolio <= threshold]
                gradient = -worst.mean(axis=0)
                step = 0.08 / math.sqrt(iteration + 1)
                weights = _capped_simplex(weights - step * gradient, 1.0, 1.0)
            return weights, 1.0
        if method == "kelly":
            full_kelly = np.linalg.pinv(covariance) @ expected
            positive = np.clip(full_kelly * 0.5, 0.0, None)
            exposure = min(1.0, float(positive.sum()))
            return positive, exposure
        if method == "black_litterman":
            prior_weights = (1 / volatility) / (1 / volatility).sum()
            equilibrium = 2.5 * covariance @ prior_weights
            tau = 0.05
            uncertainty = np.diag(np.clip(np.diag(tau * covariance), 1e-10, None))
            posterior_covariance = np.linalg.pinv(tau * covariance) + np.linalg.pinv(uncertainty)
            posterior = np.linalg.pinv(posterior_covariance) @ (
                np.linalg.pinv(tau * covariance) @ equilibrium
                + np.linalg.pinv(uncertainty) @ expected
            )
            raw = np.linalg.pinv(covariance) @ posterior
            return np.clip(raw, 0.0, None), 1.0
        if method == "benchmark_core_satellite":
            active = history - benchmark[:, None]
            tracking_error = np.std(active, axis=0, ddof=1)
            active_score = np.mean(active, axis=0) / np.clip(
                tracking_error, 1e-6, None
            )
            # Keep every satellite eligible while tilting toward components
            # with positive trailing excess return. The benchmark core is
            # applied by run() using only t-1 market information.
            preference = np.exp(np.clip(active_score, -4.0, 4.0))
            return preference, 1.0
        raise AssertionError("unreachable")

    def _apply_sector_limits(
        self,
        weights: np.ndarray,
        symbols: list[str],
        sectors: dict[str, str],
        asset_cap: float,
    ) -> tuple[np.ndarray, list[str]]:
        groups: dict[str, list[int]] = defaultdict(list)
        for index, symbol in enumerate(symbols):
            groups[sectors.get(symbol, "UNKNOWN")].append(index)
        if len(groups) * self.max_sector_weight < weights.sum() - 1e-12:
            return weights, ["SECTOR_LIMIT_INFEASIBLE"]
        target_sum = float(weights.sum())
        flags: list[str] = []
        output = weights.copy()
        for _ in range(20):
            excess = 0.0
            for sector, indices in groups.items():
                exposure = float(output[indices].sum())
                if exposure > self.max_sector_weight + 1e-10:
                    scale = self.max_sector_weight / exposure
                    excess += exposure - self.max_sector_weight
                    output[indices] *= scale
                    flags.append(f"SECTOR_CAP:{sector}")
            if excess <= 1e-10:
                break
            eligible = [
                index
                for index, symbol in enumerate(symbols)
                if output[index] < asset_cap - 1e-10
                and output[groups[sectors.get(symbol, "UNKNOWN")]].sum()
                < self.max_sector_weight - 1e-10
            ]
            if not eligible:
                flags.append("UNALLOCATED_CASH")
                break
            capacity = np.array([asset_cap - output[index] for index in eligible])
            additions = capacity * min(excess, float(capacity.sum())) / max(float(capacity.sum()), 1e-12)
            for index, addition in zip(eligible, additions, strict=True):
                sector = sectors.get(symbols[index], "UNKNOWN")
                sector_room = self.max_sector_weight - float(output[groups[sector]].sum())
                output[index] += min(float(addition), sector_room)
        if output.sum() > target_sum + 1e-8:
            output *= target_sum / output.sum()
        return output, sorted(set(flags))

    @staticmethod
    def _drift(
        weights: np.ndarray, cash: float, returns: np.ndarray, portfolio_return: float
    ) -> tuple[np.ndarray, float]:
        denominator = max(1 + portfolio_return, 1e-12)
        return weights * (1 + returns) / denominator, cash / denominator

    @staticmethod
    def _beta(values: list[float], benchmark: list[float]) -> float | None:
        if len(values) < 2 or len(values) != len(benchmark):
            return None
        variance = float(np.var(benchmark, ddof=1))
        return float(np.cov(values, benchmark, ddof=1)[0, 1] / variance) if variance else None

    @staticmethod
    def _sector_exposure(
        symbols: list[str], weights: np.ndarray, sectors: dict[str, str]
    ) -> dict[str, float]:
        output: dict[str, float] = defaultdict(float)
        for symbol, weight in zip(symbols, weights, strict=True):
            output[sectors.get(symbol, "UNKNOWN")] += float(weight)
        return dict(output)

    @staticmethod
    def _factor_exposure(
        history: np.ndarray, benchmark: np.ndarray, weights: np.ndarray
    ) -> dict[str, float]:
        momentum = np.prod(1 + history, axis=0) - 1
        volatility = np.std(history, axis=0, ddof=1) * math.sqrt(252)
        benchmark_variance = float(np.var(benchmark, ddof=1))
        betas = np.zeros(history.shape[1])
        if benchmark_variance:
            betas = np.array(
                [np.cov(history[:, index], benchmark, ddof=1)[0, 1] / benchmark_variance for index in range(history.shape[1])]
            )
        return {
            "market_beta": float(weights @ betas),
            "momentum_60d": float(weights @ momentum),
            "volatility": float(weights @ volatility),
            "cash": max(0.0, 1 - float(weights.sum())),
        }

    @staticmethod
    def _liquidity_risk(
        symbols: list[str], weights: np.ndarray, liquidity: dict[str, float]
    ) -> float:
        values = np.array([max(liquidity.get(symbol, 0.0), 0.0) for symbol in symbols])
        if values.max() <= 0:
            return 1.0
        ranks = np.argsort(np.argsort(values)).astype(float)
        risk = 1 - ranks / max(len(values) - 1, 1)
        return float(weights @ risk)

    @staticmethod
    def _stress_tests(
        returns: list[float], weights: np.ndarray, volatility: np.ndarray, beta: float | None
    ) -> dict[str, float]:
        worst_five_day = min(
            (math.prod(1 + value for value in returns[index : index + 5]) - 1 for index in range(max(1, len(returns) - 4))),
            default=0.0,
        )
        top_weight = float(weights.max()) if len(weights) else 0.0
        return {
            "historical_worst_5d": worst_five_day,
            "equity_shock_20pct": -0.20 * float(weights.sum()),
            "benchmark_crash_20pct": -0.20 * max(beta or 0.0, 0.0),
            "volatility_two_sigma": -2 * float(weights @ volatility) / math.sqrt(252),
            "largest_position_shock_30pct": -0.30 * top_weight,
            "correlation_one_two_sigma": -2 * float(weights @ volatility) / math.sqrt(252),
        }
