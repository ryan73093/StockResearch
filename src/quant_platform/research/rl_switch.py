"""2026-10-28 (research_method §7「RL 第五版：換股時機」; 使用者：RL 機器人有沒有要繼續優化): after three versions lost
to a fixed number of stocks, the robot learns when to switch instead. Each week the deep model's top 20 is the new
list; the robot either switches the holdings to it (paying 0.5% for every whole set turned over) or keeps them. The
state (past data only): how much better the new list scores than the holdings, how much of the set would change, the
market's trend and volatility, the deep risk model's market reading. LinUCB, updated every week, a week's fall squared
penalised as in RL v4. Offline first, written before the run: from 2021 the account must end with at least the money
of switching every week and of switching every fourth week, with a fall no deeper than 2 points more.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from quant_platform.research.rl_concentration import ALPHA, COST, PENALTY, STEP, account

TAIPEI = ZoneInfo("Asia/Taipei")
SIZE = 20
FEATURES = ("gain", "change", "taiex_trend", "taiex_vol", "market_risk")


def weeks(fp, factor: str = "ml_deep2_top", size: int = SIZE, step: int = STEP) -> list[dict]:
    """Per decision week: the new list, its scores, the prices to judge the next ``step`` sessions."""
    from quant_platform.research.model import eligibility

    scores = fp.matrix(factor)
    live = eligibility(fp) & np.isfinite(scores)
    market = np.asarray(fp.market, dtype=float)
    risk = fp.matrix("dl_risk")
    with np.errstate(all="ignore"):
        median = pd.Series([float(np.nanmedian(column)) if np.isfinite(column).any() else np.nan for column in risk.T])
    reading = ((median - median.rolling(750, min_periods=250).mean()) / median.rolling(750, min_periods=250).std()).to_numpy()
    output = []
    for position in range(250, len(fp.sessions) - step, step):
        alive = np.flatnonzero(live[:, position])
        if alive.size < 100:
            continue
        percent = pd.Series(scores[alive, position]).rank(pct=True).to_numpy()
        order = np.argsort(-percent, kind="stable")
        output.append({"position": position, "date": fp.sessions[position].isoformat(),
                       "new": [int(alive[index]) for index in order[:size]],
                       "score": {int(alive[index]): float(percent[index]) for index in range(len(alive))},
                       "taiex_trend": float(market[position] / np.nanmean(market[position - 199: position + 1]) - 1),
                       "taiex_vol": float(np.nanstd(np.diff(market[position - 20: position + 1])
                                                    / market[position - 20: position])),
                       "market_risk": float(reading[position])})
    return output


def run_policy(fp, found: list[dict], choose, step: int = STEP, learn=None) -> pd.DataFrame:
    """Walk the weeks with holdings: ``choose(index, state)`` says switch (True) or keep, ``learn(return)`` hears the
    week's result before the next choice; returns each week's state, action, the account's return after costs, and
    the return had the other action been taken."""
    prices = fp.panel.filled
    holdings: list[int] = []
    rows = []
    for index, week in enumerate(found):
        position = week["position"]
        new = week["new"]
        held = [symbol for symbol in holdings if symbol in week["score"]] or new
        change = len(set(new) - set(held)) / len(new)
        gain = float(np.mean([week["score"][symbol] for symbol in new]) - np.mean([week["score"][symbol] for symbol in held]))
        state = {"gain": gain, "change": change, "taiex_trend": week["taiex_trend"], "taiex_vol": week["taiex_vol"],
                 "market_risk": week["market_risk"]}
        ahead = prices[:, position + step] / prices[:, position] - 1

        def result(chosen, cost):
            return float(np.nanmean(ahead[chosen])) - cost

        switched, kept = result(new, COST * change), result(held, 0.0)
        action = bool(choose(index, state)) if holdings else True
        if learn is not None and holdings:
            learn(switched if action else kept)
        rows.append({"date": week["date"], **state, "switch": action, "ret": switched if action else kept,
                     "other": kept if action else switched, "ret_switch": switched, "ret_keep": kept})
        holdings = new if action else held
    return pd.DataFrame(rows)


def linucb_policy(alpha: float = ALPHA, penalty: float = PENALTY):
    """A LinUCB chooser (two arms: keep, switch) that learns from each week's result before the next choice."""
    names = list(FEATURES)
    width = len(names) + 1
    matrices = {arm: np.eye(width) for arm in (False, True)}
    vectors = {arm: np.zeros(width) for arm in (False, True)}
    seen: list[np.ndarray] = []
    last: dict = {}

    def standardise(state):
        raw = np.array([state[name] for name in names], dtype=float)
        if seen:
            stack = np.vstack(seen)
            mean, spread = np.nanmean(stack, axis=0), np.nanstd(stack, axis=0)
        else:
            mean, spread = np.zeros(len(names)), np.ones(len(names))
        seen.append(raw)
        vector = np.nan_to_num((raw - mean) / np.where(spread > 0, spread, 1.0))
        return np.append(np.clip(vector, -4, 4), 1.0)

    def choose(index, state):
        vector = standardise(state)
        values = {}
        for arm in (False, True):
            inverse = np.linalg.inv(matrices[arm])
            values[arm] = float((inverse @ vectors[arm]) @ vector + alpha * np.sqrt(vector @ inverse @ vector))
        action = values[True] >= values[False]
        last.update(vector=vector, action=action)
        return action

    def learn(ret: float):
        if not last:
            return
        fall = min(ret, 0.0)
        matrices[last["action"]] += np.outer(last["vector"], last["vector"])
        vectors[last["action"]] += 100 * (ret - penalty * fall * fall) * last["vector"]

    return choose, learn


def run(history: str | Path, research: str | Path) -> Path:
    from quant_platform.research import daily

    _data, fp = daily.load(Path(history))
    found = weeks(fp)
    choose, learn = linucb_policy()
    robot_frame = run_policy(fp, found, choose, learn=learn)
    always = run_policy(fp, found, lambda index, state: True)
    monthly = run_policy(fp, found, lambda index, state: index % 4 == 0)
    result = {}
    for label, start, end in (("early", "2018-01-01", "2020-10-26"), ("late", "2021-01-01", "9999")):
        def part(frame):
            return frame[(frame["date"] >= start) & (frame["date"] < end)]
        result[label] = {"robot": account(part(robot_frame)["ret"]), "every_week": account(part(always)["ret"]),
                         "every_4_weeks": account(part(monthly)["ret"]),
                         "switch_share": round(float(part(robot_frame)["switch"].mean()), 3)}
    late = result["late"]
    result["passed"] = bool(late["robot"]["multiple"] >= late["every_week"]["multiple"]
                            and late["robot"]["multiple"] >= late["every_4_weeks"]["multiple"]
                            and late["robot"]["max_drawdown"] >= late["every_week"]["max_drawdown"] - 0.02)
    folder = Path(research) / "rl"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"switch-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps({"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), **result}, ensure_ascii=False,
                               indent=1), encoding="utf-8")
    return path
