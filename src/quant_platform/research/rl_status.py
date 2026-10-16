"""What the RL robots learned, year by year (2026-10-16; 使用者：我要怎麼看到學習的狀況？).

Read from each robot's ``models/<version>/meta.json`` (research/rl_timing.py): per test year, how well it did on the
years it trained on against the year it had not seen — a large gap is overfitting — its out-of-sample record against
the same factors alike, its average weights (do they jump from year to year?) and, for the robot 3.0, its learning
curves on the training and the held-out decisions. Rewards are log returns over 0050, per 20 sessions.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROBOTS = (
    ("block-rl-3.0.0", "RL 機器人 3.0", "8 個強訊號區塊、四組錯開的起點、留最後五分之一驗證，驗證沒贏平均就不押"),
    ("factor-rl-2.1.0", "RL 機器人 2.1", "47 個因子與模型分數"),
    ("tech-rl-2.0.0", "技術 RL 機器人 2.0", "20 個價量技術因子"),
)


def _spark(values: list[float], others: list[list[float]] | None = None, width: int = 160, height: int = 40) -> dict | None:
    """Polyline points for small curves drawn on one scale (the first series and any others)."""
    series = [values] + list(others or [])
    flat = [value for line in series for value in line if value is not None]
    if len(values) < 2 or not flat:
        return None
    low, high = min(flat), max(flat)
    span = (high - low) or 1.0

    def points(line: list[float]) -> str:
        step = width / max(1, len(line) - 1)
        return " ".join(f"{index * step:.1f},{height - (value - low) / span * height:.1f}" for index, value in enumerate(line))

    zero = height - (0 - low) / span * height if low <= 0 <= high else None
    return {"width": width, "height": height, "lines": [points(line) for line in series],
            "zero": None if zero is None else round(zero, 1)}


def view(models_root: str | Path) -> list[dict[str, object]]:
    """Every robot with a trained meta.json: its yearly rows, the cumulative out-of-sample chart and the weights."""
    output = []
    for version, title, note in ROBOTS:
        path = Path(models_root) / version / "meta.json"
        if not path.is_file():
            continue
        meta = json.loads(path.read_text(encoding="utf-8"))
        names = meta.get("factors") or []
        by_year: dict[str, list[list[float]]] = {}
        for day, row in meta.get("weights", {}).items():
            by_year.setdefault(day[:4], []).append(row)
        rows, robot_total, equal_total, cumulative_robot, cumulative_equal = [], 0.0, 0.0, [0.0], [0.0]
        for year, item in sorted(meta.get("yearly", {}).items()):
            decisions = max(1, item.get("decisions") or 1)
            robot_total += item["robot_excess"]
            equal_total += item["equal_excess"]
            cumulative_robot.append(robot_total)
            cumulative_equal.append(equal_total)
            trained = item.get("training_reward_last")                  # 2.x: the last 20 epochs' training reward
            if trained is None and item.get("curve_train"):                 # 3.0: at the kept (median best) epoch
                epochs = sorted(item.get("best_epochs") or [len(item["curve_train"]) * 10])
                kept = epochs[len(epochs) // 2] // 10 - 1
                trained = item["curve_train"][min(max(kept, 0), len(item["curve_train"]) - 1)]
            rows.append({
                "year": year, "train_decisions": item.get("train_decisions"), "held_decisions": item.get("held_decisions"),
                "trained": trained, "tested": item["robot_excess"] / decisions,
                "robot": item["robot_excess"], "equal": item["equal_excess"],
                "held_robot": item.get("held_robot"), "held_equal": item.get("held_equal"),
                "abstained": item.get("abstained"), "heaviest": item.get("heaviest") or [],
                "curve": _spark(item.get("curve_train") or [], [item.get("curve_held") or []]) if item.get("curve_train") else None,
            })
        weights = []
        for year in sorted(by_year):
            rows_of_year = by_year[year]
            average = [sum(row[index] for row in rows_of_year) / len(rows_of_year) for index in range(len(names))]
            weights.append({"year": year, "shares": [round(value, 3) for value in average]})
        output.append({
            "version": version, "title": title, "note": note, "names": names, "rows": rows, "weights": weights,
            "robot_total": robot_total, "equal_total": equal_total,
            "wins": sum(1 for row in rows if row["robot"] > row["equal"]),
            "cumulative": _spark(cumulative_robot, [cumulative_equal], width=640, height=160),
            "years": [row["year"] for row in rows], "trained_at": meta.get("trained_at"),
            "config": meta.get("config") or {}, "equal_share": 1 / len(names) if names else 0.0,
        })
    return output


def labels() -> dict[str, str]:
    from quant_platform.research.daily import FACTOR_LABELS, MODEL_FACTORS

    return {**FACTOR_LABELS, **MODEL_FACTORS}


def latest_day(models_root: str | Path) -> date | None:
    days = []
    for version, _title, _note in ROBOTS:
        path = Path(models_root) / version / "meta.json"
        if path.is_file():
            stamp = json.loads(path.read_text(encoding="utf-8")).get("trained_at")
            if stamp:
                days.append(date.fromisoformat(stamp[:10]))
    return max(days) if days else None
