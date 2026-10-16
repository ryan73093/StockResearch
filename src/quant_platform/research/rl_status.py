"""What the RL robots learned, year by year (2026-10-16; 使用者：我要怎麼看到學習的狀況？).

Read from each robot's ``models/<version>/meta.json`` (research/rl_timing.py): per test year, how well it did on the
years it trained on against the year it had not seen — a large gap is overfitting — its out-of-sample record against
the same factors alike, its average weights (do they jump from year to year?) and, for the robot 3.0, its learning
curves on the training and the held-out decisions. Rewards are log returns over 0050, per 20 sessions (the robots 4.x:
per session, against 0050 itself — they pick stocks, there is no "same factors alike"). ``engine`` adds each robot's
rule as the engine ran it (the exchange's rules, the owner's account) from the trial registry.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

ROBOTS = (
    ("free-rl-4.1.0", "RL 機器人 4.1", "每天自己決定買哪些、各多少、留多少現金；訓練帳戶＝30 萬＋每月 1 萬、最低手續費、整股"),
    ("free-rl-4.0.0", "RL 機器人 4.0", "每天自己決定買哪些、各多少、留多少現金（訓練時只有比例成本）"),
    ("block-rl-3.1.0", "RL 機器人 3.1", "同 3.0，但 8 個槽位的因子每年只用那年以前的資料挑（3.0 的區塊用到了測試年的資料）"),
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
        free = version.startswith("free-")          # 4.x: every session, against 0050 itself
        for year, item in sorted(meta.get("yearly", {}).items()):
            decisions = max(1, item.get("decisions") or item.get("sessions") or 1)
            item = {**item, "equal_excess": item.get("equal_excess", 0.0),
                    "train_decisions": item.get("train_decisions", item.get("train_sessions")),
                    "held_decisions": item.get("held_decisions", item.get("held_sessions"))}
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
                "holdings": item.get("holdings"), "cash": item.get("cash"),
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
            "free": free, "unit": "每天" if free else "每 20 天",
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


ENGINE_FACTORS = {"free-rl-4.1.0": ("rl_free41",), "free-rl-4.0.0": ("rl_free",),
                  "block-rl-3.0.0": ("rl_blocks", "block_equal"), "block-rl-3.1.0": ("rl_rolling", "rolling_equal"),
                  "factor-rl-2.1.0": ("rl_all", "all_equal")}


def engine(research_dir: str | Path) -> dict[str, list[dict[str, object]]]:
    """version → its rules (and its baseline's) as the engine ran them, the newest engine's run of each: 2015-06 and
    2020-10 excess over 0050, the grade."""
    from quant_platform.research.daily import tier
    from quant_platform.research.registry import TrialRegistry, newest

    base = Path(research_dir)
    if not (base / "trials.jsonl").is_file():
        return {}
    wanted = {factor: version for version, factors in ENGINE_FACTORS.items() for factor in factors}
    output: dict[str, list[dict[str, object]]] = {}
    records = newest(TrialRegistry(base / "trials.jsonl").records(),
                     lambda record: record.kind == "candidate" and record.period == "recent"
                     and ("機器人" in record.spec_name or "區塊" in record.spec_name or "因子平均" in record.spec_name
                          or "槽位" in record.spec_name))
    for record in sorted(records.values(), key=lambda record: record.trial_id):
        try:
            spec = json.loads((base / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        version = next((wanted[name] for name in spec.get("factors") or {} if name in wanted), None)
        if version is None:
            continue
        metrics = record.metrics or {}
        grade, _why = tier(metrics)
        output.setdefault(version, []).append({
            "name": record.spec_name, "engine": metrics.get("engine"), "full": metrics.get("full_period_excess"),
            "recent": metrics.get("since_2020_excess"), "tier": grade})
    return output
