"""Performance metrics for contribution-based portfolios (S3-W04).

Account value mixes returns with new money, so drawdown and CAGR are measured
on a unit value: every contribution buys units at the current unit value,
like a fund. XIRR measures the investor's money-weighted return.
"""

from __future__ import annotations

from datetime import date

from scipy.optimize import brentq


def xirr(cash_flows: list[tuple[date, float]]) -> float | None:
    """Annual rate r with Σ cf / (1 + r)^(days / 365) = 0.

    Investor payments are negative, the final value is positive.
    """
    flows = [(day, amount) for day, amount in cash_flows if amount]
    if len(flows) < 2 or all(amount >= 0 for _, amount in flows) or all(amount <= 0 for _, amount in flows):
        return None
    origin = min(day for day, _ in flows)

    def npv(rate: float) -> float:
        return sum(amount / (1 + rate) ** ((day - origin).days / 365.0) for day, amount in flows)

    try:
        return float(brentq(npv, -0.9999, 10.0, xtol=1e-10, maxiter=500))
    except ValueError:
        return None


def unit_values(values: list[float], flows: list[float]) -> list[float]:
    """Unit value series given end-of-day values and same-day external flows.

    A flow on day t is assumed to arrive before the close and to buy units at
    the previous unit value, so the return of day t is
    (V_t - F_t) / V_{t-1}.
    """
    output: list[float] = []
    unit = 1.0
    previous = None
    for value, flow in zip(values, flows):
        if previous is not None and previous > 0:
            unit *= (value - flow) / previous
        output.append(unit)
        previous = value
    return output


def max_drawdown(series: list[float]) -> tuple[float, int, int]:
    """Deepest peak-to-trough fall as a negative fraction, with indices."""
    peak_value, peak_index = float("-inf"), 0
    worst, worst_peak, worst_trough = 0.0, 0, 0
    for index, value in enumerate(series):
        if value > peak_value:
            peak_value, peak_index = value, index
        if peak_value > 0:
            drawdown = value / peak_value - 1
            if drawdown < worst:
                worst, worst_peak, worst_trough = drawdown, peak_index, index
    return worst, worst_peak, worst_trough


def annualized(growth: float, days: int) -> float | None:
    if days <= 0 or growth <= 0:
        return None
    return growth ** (365.0 / days) - 1
