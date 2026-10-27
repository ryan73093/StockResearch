"""2026-10-28 (research_method §7「RL 第四版」; 使用者：RL 機器人有沒有要繼續優化): the robots 3.0-3.5 learned how to
weight their slots and never beat the slots' plain average (the literature's finding too: learned weights rarely beat
equal ones). The next version learns something else — how concentrated to be. Each week a contextual bandit (one-step
reinforcement learning: a state, an action, its reward) chooses to hold the deep model's top 10, 20 or 30 stocks:
concentrated when the model has been right and the market is calm, spread when not.

This first step is offline, written before the run: on the deep model's own weekly scores (ml_deep2_top, each year's
networks trained before it), every week's state (TAIEX against its 200-session average, its 20-session volatility, the
score's spread between the top 10 and ranks 21-30, the top 10's lead over the top 30 in the last 12 matured weeks, the
share of stocks up over 20 sessions) and each arm's reward (the set's next-5-session return above the eligible median,
less 0.5% for every whole set turned over). Walk-forward by year: the bandit (a gradient-boosted value per arm) learns
from the years before and chooses for the year; it must beat every fixed arm and "last year's best arm" from 2021 to
2026-08 to be built into the engine as a rule.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

TAIPEI = ZoneInfo("Asia/Taipei")
ARMS = (10, 20, 30)
STEP = 5                  # sessions between decisions (a week)
COST = 0.005              # a whole set turned over: about a round trip's fees and tax
FEATURES = ("taiex_trend", "taiex_vol", "spread", "lead", "breadth")
FIRST_TEST = 2018


def table(fp, factor: str = "ml_deep2_top", arms=ARMS, step: int = STEP) -> pd.DataFrame:
    """One row per decision week: its state (past data only) and each arm's reward over the next ``step`` sessions."""
    from quant_platform.research.model import eligibility

    scores = fp.matrix(factor)
    live = eligibility(fp) & np.isfinite(scores)
    prices = fp.panel.filled
    market = np.asarray(fp.market, dtype=float)
    previous: dict[int, set] = {arm: set() for arm in arms}
    rows = []
    sessions = fp.sessions
    for position in range(250, len(sessions) - step, step):
        alive = np.flatnonzero(live[:, position])
        if alive.size < 100:
            continue
        order = alive[np.argsort(-scores[alive, position], kind="stable")]
        ahead = prices[:, position + step] / prices[:, position] - 1
        middle = float(np.nanmedian(ahead[alive]))
        row = {"position": position, "date": sessions[position].isoformat()}
        for arm in arms:
            chosen = order[:arm]
            gained = float(np.nanmean(ahead[chosen])) - middle
            turned = len(set(chosen.tolist()) - previous[arm]) / arm if previous[arm] else 1.0
            previous[arm] = set(chosen.tolist())
            row[f"reward_{arm}"] = gained - COST * turned
            row[f"gross_{arm}"] = gained
        window = market[position - 199: position + 1]
        row["taiex_trend"] = float(market[position] / np.nanmean(window) - 1)
        row["taiex_vol"] = float(np.nanstd(np.diff(market[position - 20: position + 1]) / market[position - 20: position]))
        percent = pd.Series(scores[alive, position]).rank(pct=True).to_numpy()
        ranked = np.sort(percent)[::-1]
        row["spread"] = float(ranked[:10].mean() - ranked[20:30].mean())
        past = prices[alive, position] / prices[alive, position - 20] - 1
        row["breadth"] = float(np.nanmean(past > 0))
        rows.append(row)
    frame = pd.DataFrame(rows)
    # the top 10's lead over the top 30 in the last 12 weeks whose rewards were known at the decision
    lead = (frame[f"gross_{arms[0]}"] - frame[f"gross_{arms[-1]}"]).shift(1).rolling(12, min_periods=4).mean()
    frame["lead"] = lead.to_numpy()
    frame["year"] = frame["date"].str[:4].astype(int)
    return frame


def walk_forward(frame: pd.DataFrame, arms=ARMS, first_test: int = FIRST_TEST) -> pd.DataFrame:
    """Per test year: the bandit's choice each week (a value model per arm learned from the earlier years), and the
    rewards of every fixed arm and of last year's best arm."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    rows = []
    for year in sorted(frame["year"].unique()):
        if year < first_test:
            continue
        train = frame[frame["year"] < year].dropna(subset=list(FEATURES))
        test = frame[frame["year"] == year]
        if len(train) < 100 or not len(test):
            continue
        models = {arm: HistGradientBoostingRegressor(max_iter=100, learning_rate=0.05, max_leaf_nodes=7,
                                                     min_samples_leaf=20, random_state=0)
                  .fit(train[list(FEATURES)].to_numpy(), train[f"reward_{arm}"].to_numpy()) for arm in arms}
        values = np.stack([models[arm].predict(test[list(FEATURES)].fillna(train[list(FEATURES)].median()).to_numpy())
                           for arm in arms], axis=1)
        choice = np.array(arms)[values.argmax(axis=1)]
        last = frame[frame["year"] == year - 1]
        best_last = max(arms, key=lambda arm: last[f"reward_{arm}"].mean()) if len(last) else arms[1]
        for index, (_key, row) in enumerate(test.iterrows()):
            out = {"date": row["date"], "year": year, "choice": int(choice[index]), "best_last": best_last,
                   "bandit": row[f"reward_{choice[index]}"], "last_best": row[f"reward_{best_last}"]}
            out.update({f"fixed_{arm}": row[f"reward_{arm}"] for arm in arms})
            rows.append(out)
    return pd.DataFrame(rows)


def summary(choices: pd.DataFrame, arms=ARMS) -> dict[str, object]:
    """Mean weekly reward (%) per period for the bandit, each fixed arm and last year's best; the judgement."""
    def period(part):
        columns = ["bandit", "last_best", *[f"fixed_{arm}" for arm in arms]]
        return {column: round(float(part[column].mean()) * 100, 4) for column in columns} | {"weeks": int(len(part))}

    early = choices[choices["date"] < "2020-10-26"]
    late = choices[choices["date"] >= "2021-01-01"]
    found = {"early": period(early) if len(early) else None, "late": period(late) if len(late) else None,
             "choices": {str(arm): int((choices["choice"] == arm).sum()) for arm in arms},
             "by_year": {str(year): period(part) for year, part in choices.groupby("year")}}
    if len(late):
        best_fixed = max(found["late"][f"fixed_{arm}"] for arm in arms)
        found["passed"] = bool(found["late"]["bandit"] > best_fixed and found["late"]["bandit"] > found["late"]["last_best"])
    return found


def run(history: str | Path, research: str | Path) -> Path:
    from quant_platform.research import daily

    _data, fp = daily.load(Path(history))
    frame = table(fp)
    choices = walk_forward(frame)
    result = summary(choices)
    folder = Path(research) / "rl"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"concentration-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "arms": list(ARMS), "step": STEP,
                                "cost": COST, "features": list(FEATURES), **result,
                                "weeks": choices.to_dict("records")}, ensure_ascii=False, indent=1, default=float),
                    encoding="utf-8")
    return path
