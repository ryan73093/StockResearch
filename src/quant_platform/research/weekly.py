"""Weekly research report (S6-W04, REQUIREMENTS §8).

What the research did in one ISO week (Monday–Sunday, Taipei): the AI
researcher's rounds, hypotheses, new and rejected specs, the best new
development result, the multiple-testing state, promotion events, the
forward simulation and the model cost. Built from the append-only files
under instance/research/; the research page shows the current week and the
worker saves each finished week to instance/research/weekly/<year>-W<nn>.json.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_platform.research.registry import TrialRegistry, current_basis, distinct_rules
from quant_platform.research.summary import passes_development_gate

TAIPEI = ZoneInfo("Asia/Taipei")


def _jsonl(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _local_day(stamp: str) -> date | None:
    try:
        moment = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=TAIPEI)).astimezone(TAIPEI).date()


def week_bounds(day: date) -> tuple[date, date]:
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def weekly_report(research_dir: str | Path, day: date) -> dict[str, object]:
    base = Path(research_dir)
    start, end = week_bounds(day)

    def inside(stamp: str) -> bool:
        local = _local_day(stamp)
        return local is not None and start <= local <= end

    rounds = [entry for entry in _jsonl(base / "journal.jsonl") if inside(str(entry.get("started_at", "")))]
    accepted = [item for entry in rounds for item in entry.get("accepted") or [] if not item.get("reused")]
    rejected = [item for entry in rounds for item in entry.get("rejected") or []]
    reasons = Counter(str(item.get("reason", "")).split("：", 1)[0] for item in rejected)

    records = TrialRegistry(base / "trials.jsonl").records()
    new = [record for record in records if record.kind == "candidate" and inside(record.created_at)]
    current, older = current_basis(records, "development")
    current_ids = {record.trial_id for record in current}
    new_development = [record for record in new if record.trial_id in current_ids]

    def three(record) -> dict:
        return (record.metrics.get("windows") or {}).get("3y") or {}

    best = max(new_development, key=lambda record: three(record).get("median_excess") or float("-inf"), default=None)

    from quant_platform.research.reports import latest_stats

    stats = latest_stats(base / "stats", "development") or {}
    dsr_values = [item["dsr"]["deflated_sharpe"] for item in stats.get("candidates") or []]
    promotions = [event for event in _jsonl(base / "promotions.jsonl") if inside(str(event.get("at", "")))]
    usage = [entry for entry in _jsonl(base / "agent" / "usage.jsonl") if inside(str(entry.get("at", "")))]

    from quant_platform.research.forward import ForwardTracker

    forward_rows = ForwardTracker(base).summary()
    ideas: list[str] = []
    for entry in reversed(rounds):
        for idea in entry.get("extension_ideas") or []:
            if idea and idea not in ideas:
                ideas.append(idea)
    passing = sum(1 for record in new_development if passes_development_gate(record))
    if not rounds and not new:
        verdict = "本週沒有新的研究輪次或試驗。"
    elif passing:
        verdict = f"本週有 {passing} 個新設定通過開發期門檻，仍須通過多重檢定（DSR ≥ 0.95、PBO ≤ 20%）才會進驗證期。"
    else:
        verdict = "本週的新設定都沒有通過開發期門檻；目前仍沒有規則能證明勝過定期定額。"
    return {
        "week": f"{start.isocalendar().year}-W{start.isocalendar().week:02d}",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "rounds": len(rounds),
        "round_errors": sum(1 for entry in rounds if entry.get("status") != "ok"),
        "hypotheses": [str(entry.get("hypothesis")) for entry in rounds if entry.get("hypothesis")][:7],
        "ai_trials": len(accepted),
        "rejected": len(rejected),
        "top_rejections": reasons.most_common(3),
        "new_trials": len(new),
        "development_trials": len(current),
        "attempts": distinct_rules(current + older),
        "passing_new": passing,
        "best_new": None if best is None else {
            "name": best.spec_name, "trial_id": best.trial_id,
            "win_3y": three(best).get("win_ratio"), "median_3y": three(best).get("median_excess"),
        },
        "best_dsr": max(dsr_values) if dsr_values else None,
        "pbo": (stats.get("pbo") or {}).get("pbo"),
        "promotions": [
            {"name": event.get("name"), "stage": event.get("stage"), "outcome": event.get("outcome")}
            for event in promotions
        ],
        "forward": [
            {"name": row["name"], "sessions": row["sessions"], "excess": row["excess"]}
            for row in forward_rows if not row["benchmark"]
        ],
        "cost_usd": round(sum(float(entry.get("cost_usd") or 0) for entry in usage), 6),
        "next_ideas": ideas[:3],
        "verdict": verdict,
    }


def save_weekly_report(research_dir: str | Path, day: date) -> Path:
    report = weekly_report(research_dir, day)
    folder = Path(research_dir) / "weekly"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{report['week']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
