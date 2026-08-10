from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

from quant_platform.application.ports import MarketBarRepository
from quant_platform.domain.entities import MarketBar


DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "IWM", "XLK", "XLF", "XLE", "XLV", "XLI",
    "XLP", "XLY", "TLT", "GLD", "0050.TW", "0056.TW", "006208.TW", "00878.TW",
    "^IXIC", "^GSPC", "^SOX", "GC=F", "TWD=X", "^TWII", "JPY=X", "^VIX", "BZ=F",
]

MAJOR_INDICATORS = ("^IXIC", "^GSPC", "^SOX", "GC=F", "TWD=X", "^TWII", "JPY=X", "^VIX", "BZ=F")


@dataclass(frozen=True, slots=True)
class AssetAnalytics:
    symbol: str
    market: str
    last_date: str
    last_close: float
    change_1d: float
    total_return: float
    annual_volatility: float
    max_drawdown: float
    regime: str
    volatility_regime: str
    sma_fast: float | None
    sma_slow: float | None
    observations: int
    price_curve: list[float]
    candle_dates: list[str]
    candle_data: list[list[float]]


@dataclass(frozen=True, slots=True)
class BacktestAnalytics:
    symbol: str
    strategy: str
    total_return: float
    benchmark_return: float
    sharpe: float
    max_drawdown: float
    win_rate: float
    trades: int
    cost_bps: float
    observations: int
    equity_curve: list[float]
    benchmark_curve: list[float]


@dataclass(frozen=True, slots=True)
class PortfolioAnalytics:
    weights: dict[str, float]
    observations: int
    total_return: float
    annual_volatility: float
    max_drawdown: float
    var_95: float
    cvar_95: float
    correlation: float | None
    beta_tw_to_spy: float | None
    equity_curve: list[float]


@dataclass(frozen=True, slots=True)
class FactorSnapshot:
    symbol: str
    momentum_20d: float | None
    momentum_60d: float | None
    volatility_20d: float | None
    trend_score: float | None
    composite_score: float | None
    rank: int | None
    quality_gate: str


def _returns(values: list[float]) -> list[float]:
    return [values[index] / values[index - 1] - 1 for index in range(1, len(values))]


def _equity_curve(returns: list[float]) -> list[float]:
    equity = 1.0
    curve = [equity]
    for value in returns:
        equity *= 1 + value
        curve.append(equity)
    return curve


def _max_drawdown(curve: list[float]) -> float:
    peak = curve[0] if curve else 1.0
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        worst = min(worst, value / peak - 1)
    return worst


def _annual_volatility(returns: list[float]) -> float:
    return statistics.stdev(returns) * math.sqrt(252) if len(returns) > 1 else 0.0


def _normalized_curve(values: list[float]) -> list[float]:
    if not values or values[0] == 0:
        return []
    return [value / values[0] for value in values]


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return ordered[index]


class QuantAnalyticsService:
    """Read-only, as-of-safe analytics for the research dashboard."""

    def __init__(self, repository: MarketBarRepository) -> None:
        self._repository = repository

    def asset(self, symbol: str, as_of: datetime | None = None) -> AssetAnalytics | None:
        bars = self._bars(symbol, as_of)
        if not bars:
            return None
        closes = [float(bar.adjusted_close or bar.close) for bar in bars]
        returns = _returns(closes)
        fast = statistics.fmean(closes[-5:]) if len(closes) >= 5 else None
        slow = statistics.fmean(closes[-20:]) if len(closes) >= 20 else None
        if fast is None or slow is None:
            regime = "INSUFFICIENT"
        elif closes[-1] > slow and fast > slow:
            regime = "BULL TREND"
        elif closes[-1] < slow and fast < slow:
            regime = "BEAR TREND"
        else:
            regime = "RANGE"
        annual_vol = _annual_volatility(returns)
        return AssetAnalytics(
            symbol=bars[-1].symbol,
            market=bars[-1].market,
            last_date=bars[-1].event_time.date().isoformat(),
            last_close=closes[-1],
            change_1d=returns[-1] if returns else 0.0,
            total_return=closes[-1] / closes[0] - 1 if len(closes) > 1 else 0.0,
            annual_volatility=annual_vol,
            max_drawdown=_max_drawdown(_normalized_curve(closes)),
            regime=regime,
            volatility_regime="HIGH VOL" if annual_vol >= 0.25 else "LOW VOL",
            sma_fast=fast,
            sma_slow=slow,
            observations=len(bars),
            price_curve=_normalized_curve(closes),
            candle_dates=[bar.event_time.date().isoformat() for bar in bars],
            candle_data=[
                [float(bar.open), float(bar.close), float(bar.low), float(bar.high)]
                for bar in bars
            ],
        )

    def trend_backtest(
        self,
        symbol: str,
        as_of: datetime | None = None,
        fast_window: int = 5,
        slow_window: int = 20,
        cost_bps: float = 10.0,
    ) -> BacktestAnalytics | None:
        bars = self._bars(symbol, as_of)
        if len(bars) <= slow_window:
            return None
        closes = [float(bar.adjusted_close or bar.close) for bar in bars]
        raw_returns = _returns(closes)
        signals: list[float] = []
        for index in range(len(closes)):
            if index + 1 < slow_window:
                signals.append(0.0)
                continue
            fast = statistics.fmean(closes[index - fast_window + 1 : index + 1])
            slow = statistics.fmean(closes[index - slow_window + 1 : index + 1])
            signals.append(1.0 if fast > slow else 0.0)

        strategy_returns: list[float] = []
        previous_position = 0.0
        trades = 0
        for index, asset_return in enumerate(raw_returns, start=1):
            # Yesterday's completed signal is applied to today's return.
            position = signals[index - 1]
            turnover = abs(position - previous_position)
            if turnover:
                trades += 1
            strategy_returns.append(position * asset_return - turnover * cost_bps / 10_000)
            previous_position = position

        curve = _equity_curve(strategy_returns)
        benchmark = _equity_curve(raw_returns)
        volatility = statistics.stdev(strategy_returns) if len(strategy_returns) > 1 else 0.0
        sharpe = (
            statistics.fmean(strategy_returns) / volatility * math.sqrt(252)
            if volatility > 0
            else 0.0
        )
        active_days = [value for value in strategy_returns if value != 0]
        return BacktestAnalytics(
            symbol=symbol.upper(),
            strategy=f"SMA {fast_window}/{slow_window} — signal lagged 1 session",
            total_return=curve[-1] - 1,
            benchmark_return=benchmark[-1] - 1,
            sharpe=sharpe,
            max_drawdown=_max_drawdown(curve),
            win_rate=sum(value > 0 for value in active_days) / len(active_days) if active_days else 0.0,
            trades=trades,
            cost_bps=cost_bps,
            observations=len(bars),
            equity_curve=curve,
            benchmark_curve=benchmark,
        )

    def portfolio(self, symbols: list[str], as_of: datetime | None = None) -> PortfolioAnalytics | None:
        series: dict[str, dict[date, float]] = {}
        return_series: dict[str, dict[date, float]] = {}
        for symbol in symbols:
            bars = self._bars(symbol, as_of)
            prices = {bar.event_time.date(): float(bar.adjusted_close or bar.close) for bar in bars}
            ordered_dates = sorted(prices)
            series[symbol] = prices
            return_series[symbol] = {
                ordered_dates[index]: prices[ordered_dates[index]] / prices[ordered_dates[index - 1]] - 1
                for index in range(1, len(ordered_dates))
            }
        if not return_series or any(not values for values in return_series.values()):
            return None
        common_dates = sorted(set.intersection(*(set(values) for values in return_series.values())))
        if len(common_dates) < 2:
            return None
        vols = {
            symbol: _annual_volatility([return_series[symbol][day] for day in common_dates])
            for symbol in symbols
        }
        inverse_vols = {symbol: 1 / max(volatility, 1e-9) for symbol, volatility in vols.items()}
        scale = sum(inverse_vols.values())
        weights = {symbol: value / scale for symbol, value in inverse_vols.items()}
        portfolio_returns = [
            sum(weights[symbol] * return_series[symbol][day] for symbol in symbols)
            for day in common_dates
        ]
        curve = _equity_curve(portfolio_returns)
        threshold = _percentile(portfolio_returns, 0.05)
        tail = [value for value in portfolio_returns if value <= threshold]
        correlation = None
        beta = None
        if len(symbols) == 2:
            first = [return_series[symbols[0]][day] for day in common_dates]
            second = [return_series[symbols[1]][day] for day in common_dates]
            correlation = statistics.correlation(first, second) if len(first) > 1 else None
            if symbols[0] == "SPY":
                market_returns, asset_returns = first, second
            else:
                market_returns, asset_returns = second, first
            market_variance = statistics.variance(market_returns)
            beta = statistics.covariance(asset_returns, market_returns) / market_variance if market_variance else None
        return PortfolioAnalytics(
            weights=weights,
            observations=len(common_dates),
            total_return=curve[-1] - 1,
            annual_volatility=_annual_volatility(portfolio_returns),
            max_drawdown=_max_drawdown(curve),
            var_95=threshold,
            cvar_95=statistics.fmean(tail) if tail else threshold,
            correlation=correlation,
            beta_tw_to_spy=beta,
            equity_curve=curve,
        )

    def factors(self, symbols: list[str], as_of: datetime | None = None) -> list[FactorSnapshot]:
        snapshots: list[FactorSnapshot] = []
        for symbol in symbols:
            bars = self._bars(symbol, as_of)
            closes = [float(bar.adjusted_close or bar.close) for bar in bars]
            returns = _returns(closes[-21:])
            momentum = closes[-1] / closes[-21] - 1 if len(closes) >= 21 else None
            momentum_60 = closes[-1] / closes[-61] - 1 if len(closes) >= 61 else None
            volatility = _annual_volatility(returns) if len(returns) > 1 else None
            trend = None
            if len(closes) >= 20:
                slow = statistics.fmean(closes[-20:])
                trend = closes[-1] / slow - 1
            snapshots.append(
                FactorSnapshot(
                    symbol=symbol,
                    momentum_20d=momentum,
                    momentum_60d=momentum_60,
                    volatility_20d=volatility,
                    trend_score=trend,
                    composite_score=None,
                    rank=None,
                    quality_gate=(
                        "CROSS-SECTION RANKING / IC NOT YET VALIDATED"
                        if len(symbols) < 30
                        else "RESEARCHABLE"
                    ),
                )
            )
        complete = [
            item
            for item in snapshots
            if item.momentum_60d is not None
            and item.volatility_20d is not None
            and item.trend_score is not None
        ]
        if not complete:
            return snapshots

        def zscore(value: float, values: list[float]) -> float:
            deviation = statistics.stdev(values) if len(values) > 1 else 0.0
            return (value - statistics.fmean(values)) / deviation if deviation else 0.0

        momentum_values = [item.momentum_60d for item in complete]
        volatility_values = [item.volatility_20d for item in complete]
        trend_values = [item.trend_score for item in complete]
        scored = [
            replace(
                item,
                composite_score=(
                    0.45 * zscore(item.momentum_60d, momentum_values)
                    + 0.35 * zscore(item.trend_score, trend_values)
                    - 0.20 * zscore(item.volatility_20d, volatility_values)
                ),
            )
            for item in complete
        ]
        ranked = sorted(scored, key=lambda item: item.composite_score or 0.0, reverse=True)
        ranks = {item.symbol: index for index, item in enumerate(ranked, start=1)}
        scores = {item.symbol: item.composite_score for item in ranked}
        return [
            replace(item, composite_score=scores.get(item.symbol), rank=ranks.get(item.symbol))
            for item in snapshots
        ]

    def command_center(self, symbols: list[str]) -> dict[str, object]:
        now = datetime.now(UTC)
        assets = [asset for symbol in symbols if (asset := self.asset(symbol, now)) is not None]
        research_symbols = [symbol for symbol in symbols if symbol not in MAJOR_INDICATORS]
        research_assets = [asset for asset in assets if asset.symbol not in MAJOR_INDICATORS]
        backtests = [
            result
            for symbol in research_symbols
            if (result := self.trend_backtest(symbol, now)) is not None
        ]
        portfolio = self.portfolio(research_symbols, now)
        factors = self.factors(research_symbols, now)
        ranked_factors = sorted(
            [item for item in factors if item.rank is not None], key=lambda item: item.rank or 999
        )
        strategy_rankings = sorted(backtests, key=lambda item: item.sharpe, reverse=True)
        bull_count = sum(asset.regime == "BULL TREND" for asset in research_assets)
        bear_count = sum(asset.regime == "BEAR TREND" for asset in research_assets)
        range_count = len(research_assets) - bull_count - bear_count
        positive_count = sum(asset.total_return > 0 for asset in research_assets)
        return {
            "generated_at": now,
            "assets": assets,
            "taiwan_assets": [asset for asset in assets if asset.market == "TW" and asset.symbol != "^TWII"],
            "major_indicators": [asset for asset in assets if asset.symbol in MAJOR_INDICATORS],
            "research_assets": research_assets,
            "backtests": backtests,
            "strategy_rankings": strategy_rankings,
            "portfolio": portfolio,
            "factors": factors,
            "factor_rankings": ranked_factors,
            "breadth": {
                "bull": bull_count,
                "bear": bear_count,
                "range": range_count,
                "positive": positive_count,
                "total": len(research_assets),
                "positive_ratio": positive_count / len(research_assets) if research_assets else 0.0,
            },
            "limitations": [
                "目前是 16 檔 ETF、約兩年日線，尚未涵蓋個股與下市標的，不能宣稱策略具統計顯著性。",
                "因子研究已保存 IC、Rank IC、spread、turnover 與 decay，但因橫斷面不足 30 檔而禁止 promotion。",
                "回測已使用一日 lag 與 10 bps 成本，但尚未納入稅、流動性與 corporate actions。",
            ],
        }

    def _bars(self, symbol: str, as_of: datetime | None) -> list[MarketBar]:
        return self._repository.list_bars(
            symbol=symbol,
            interval="1d",
            source="yahoo_finance",
            as_of=as_of or datetime.now(UTC),
        )
