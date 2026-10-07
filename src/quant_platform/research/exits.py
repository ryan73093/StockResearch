"""R15 stage C1b (2026-10-07): a learned exit for each holding of the best trend rule.

The rule keeps choosing its 20 stocks; for every stock it holds, every session, the agent decides keep or
sell — and a sold stock is replaced by the rule's next pick, as when a holding drops out of the ranking.
This is one-step Q-learning (a contextual bandit): the value of keeping a holding rather than replacing it is
its next-20-session return minus the replacement's, and selling also pays a round trip (about 0.6%), so
the agent sells only when it expects the replacement to beat the holding by more than that. The value is a
gradient-boosted regression on what is known at that close: sessions held, the gain and the fall from the
peak since buying, the stock's place in the rule's ranking, its percentile among eligible stocks on trend,
3-month momentum, last week's return and volatility, its 20-session volatility, the market's breadth and
TAIEX against its 200-day average, and the trend gap to the stock that would replace it.

Walk-forward like the supervised baseline: the model for year Y learns only from weekly samples (the rule's
own holdings, replayed by the engine) whose 20-session outcome ended before Y. A stock the agent sold is not
bought back for 20 sessions. Used through ``DailyRule.exit_model`` and judged by the same gate as the rule.
"""

from __future__ import annotations

import json
import pickle
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

EXIT_VERSION = "exit-1.0.0"
HORIZON = 20
STEP = 5
FIRST_YEAR = 2016
ROUND_TRIP = 0.006
BAR_SESSIONS = 20
PARAMS = {"max_iter": 200, "learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 100,
          "l2_regularization": 1.0, "random_state": 0}
FEATURES = ("held_sessions", "gain_since_buy", "fall_from_peak", "place_in_rule", "trend_pct", "momentum_pct",
            "last_week_pct", "volatility_pct", "volatility_20", "breadth", "taiex_vs_200", "replacement_trend_gap")


def base_rule():
    from quant_platform.research.rl import base_rule as rl_base

    return rl_base()


def exits_dir(history: str | Path) -> Path:
    return Path(history).parent / "models" / EXIT_VERSION


class Context:
    """The market-wide pieces of the state, computed once per factor panel."""

    def __init__(self, fp) -> None:
        from quant_platform.research.model import _rank, eligibility

        self.fp = fp
        eligible = eligibility(fp)
        self.percentiles = {}
        for name in ("trend_200", "momentum_3", "reversal_5d", "low_volatility_60"):
            values = np.where(eligible, fp.matrix(name), np.nan).T                  # sessions × symbols
            self.percentiles[name] = _rank(values).T                                 # symbols × sessions
        returns = pd.DataFrame(fp.panel.filled.T).pct_change(fill_method=None)
        self.volatility = returns.rolling(20).std().to_numpy().T
        trend = fp.matrix("trend_200")
        with np.errstate(invalid="ignore"):
            usable = eligible & np.isfinite(trend)
            above = np.where(usable, trend > 0, False).sum(axis=0)
            count = usable.sum(axis=0)
        self.breadth = np.where(count > 0, above / np.maximum(count, 1), np.nan)
        if fp.market is not None:
            average = pd.Series(fp.market).rolling(200).mean().to_numpy()
            self.taiex = fp.market / average - 1
        else:
            self.taiex = np.full(len(fp.sessions), np.nan)

    def rows(self, position: int, holdings: list[str], held: dict[str, int], buy_price: dict[str, float],
             peak: dict[str, float], ranked: list[str]) -> np.ndarray:
        """One state row per holding at ``position`` (``held`` in sessions; prices adjusted)."""
        fp = self.fp
        place = {symbol: index for index, symbol in enumerate(ranked)}
        replacement = next((symbol for symbol in ranked if symbol not in set(holdings)), None)
        replacement_trend = (self.percentiles["trend_200"][fp.row[replacement], position]
                             if replacement is not None else np.nan)
        output = np.full((len(holdings), len(FEATURES)), np.nan, dtype=np.float32)
        for index, symbol in enumerate(holdings):
            row = fp.row[symbol]
            price = fp.panel.filled[row, position]
            trend = self.percentiles["trend_200"][row, position]
            output[index] = (
                held.get(symbol, 0), price / buy_price[symbol] - 1 if buy_price.get(symbol) else np.nan,
                price / peak[symbol] - 1 if peak.get(symbol) else np.nan,
                place.get(symbol, len(ranked)) / max(len(ranked), 1), trend,
                self.percentiles["momentum_3"][row, position], self.percentiles["reversal_5d"][row, position],
                self.percentiles["low_volatility_60"][row, position], self.volatility[row, position],
                self.breadth[position], self.taiex[position], replacement_trend - trend,
            )
        return output


def dataset(fp, rule, start, end) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Weekly samples of the rule's own holdings: state, outcome (holding minus replacement over the next
    HORIZON sessions) and the session position of each sample."""
    from quant_platform.research.daily import daily_rankings

    context = Context(fp)
    ranks = daily_rankings(fp, rule, start, end)
    prices = fp.panel.filled
    held: dict[str, int] = {}
    buy_price: dict[str, float] = {}
    peak: dict[str, float] = {}
    features, outcomes, positions = [], [], []
    for count, day in enumerate(sorted(ranks)):
        position = fp.index[day]
        holdings = ranks[day]
        for symbol in holdings:
            price = prices[fp.row[symbol], position]
            if symbol not in held:
                held[symbol], buy_price[symbol], peak[symbol] = 0, price, price
            else:
                held[symbol] += 1
                peak[symbol] = max(peak[symbol], price)
        for symbol in list(held):
            if symbol not in holdings:
                held.pop(symbol), buy_price.pop(symbol), peak.pop(symbol)
        if count % STEP or position + HORIZON >= len(fp.sessions) or not holdings:
            continue
        ranked = fp.ranked(rule, position)
        replacement = next((symbol for symbol in ranked if symbol not in set(holdings)), None)
        if replacement is None:
            continue
        later = position + HORIZON

        def gain(symbol):
            row = fp.row[symbol]
            return prices[row, later] / prices[row, position] - 1

        replacement_gain = gain(replacement)
        rows = context.rows(position, holdings, held, buy_price, peak, ranked)
        for index, symbol in enumerate(holdings):
            outcome = gain(symbol) - replacement_gain
            if np.isfinite(outcome):
                features.append(rows[index])
                outcomes.append(float(np.clip(outcome, -0.5, 0.5)))
                positions.append(position)
    return np.array(features, dtype=np.float32), np.array(outcomes, dtype=np.float32), positions


def train(fp, out_dir: str | Path, rule=None, years: list[int] | None = None, job=None) -> dict[str, object]:
    """One value model per year (walk-forward); saves them and the out-of-sample diagnostics."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    from quant_platform.research.daily import SHADOW_WARMUP

    rule = (rule or base_rule()).model_copy(update={"core": 0.0, "exit_model": "none"})
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    start = fp.sessions[min(SHADOW_WARMUP, len(fp.sessions) - 1)]
    x, y, positions = dataset(fp, rule, start, fp.sessions[-1])
    positions = np.array(positions)
    years = years or list(range(FIRST_YEAR, fp.sessions[-1].year + 1))
    diagnostics = {}
    for number, year in enumerate(years):
        if job:
            job.update(done=number, current=f"{year} 年的出場模型", force=True)
        first = next((index for index, day in enumerate(fp.sessions) if day.year >= year), len(fp.sessions))
        train_rows = positions + HORIZON < first
        if train_rows.sum() < 500:
            continue
        model = HistGradientBoostingRegressor(**PARAMS)
        features = x[train_rows].copy()
        features[:, ~np.isfinite(features).any(axis=0)] = 0.0     # a state with no value yet: never used
        model.fit(features, y[train_rows])
        (out / f"{year}.pkl").write_bytes(pickle.dumps(model, protocol=5))
        tests = np.array([fp.sessions[position].year == year for position in positions])
        if tests.any():
            predicted = model.predict(x[tests])
            sell = predicted < -ROUND_TRIP
            diagnostics[str(year)] = {
                "train_rows": int(train_rows.sum()), "test_rows": int(tests.sum()),
                "sell_share": round(float(sell.mean()), 3),
                # the outcome of the holdings it would sell: negative = selling them helped
                "sold_outcome": round(float(y[tests][sell].mean()), 4) if sell.any() else None,
                "kept_outcome": round(float(y[tests][~sell].mean()), 4) if (~sell).any() else None,
            }
    meta = {"version": EXIT_VERSION, "base_rule": rule.canonical(), "features": list(FEATURES), "horizon": HORIZON,
            "round_trip": ROUND_TRIP, "params": PARAMS, "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "years": diagnostics}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return meta


class ExitAgent:
    """Decides, for a rule's holdings on a session, which to sell (used inside daily_rankings)."""

    def __init__(self, fp, folder: str | Path) -> None:
        self.fp = fp
        self.context = Context(fp)
        self.models = {int(path.stem): path for path in Path(folder).glob("*.pkl")} if Path(folder).is_dir() else {}
        self._loaded: dict[int, object] = {}

    def model_for(self, year: int):
        usable = [model_year for model_year in self.models if model_year <= year]
        if not usable:
            return None
        chosen = max(usable)
        if chosen not in self._loaded:
            self._loaded[chosen] = pickle.loads(self.models[chosen].read_bytes())
        return self._loaded[chosen]

    def sells(self, position: int, holdings: list[str], held: dict[str, int], buy_price: dict[str, float],
              peak: dict[str, float], ranked: list[str]) -> set[str]:
        model = self.model_for(self.fp.sessions[position].year)
        if model is None or not holdings:
            return set()
        rows = self.context.rows(position, holdings, held, buy_price, peak, ranked)
        predicted = model.predict(rows)
        return {symbol for symbol, value in zip(holdings, predicted, strict=True) if value < -ROUND_TRIP}


def digest(folder: str | Path) -> str:
    from quant_platform.research.model import digest as files_digest

    return files_digest(folder)
