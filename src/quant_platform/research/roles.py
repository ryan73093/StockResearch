"""Strategy roles and how they grew (2026-10-16; 使用者：策略就是我的角色——每次優化之後我要在首頁看到每種角色成長了
多少、跟之前訓練相比強化了多少，不只是現在多少錢，評估指標也不要只有這些).

Every 100%-stock daily rule in the trial registry belongs to one role (``role``): machine learning with factors, the
T0 consensus list, the RL robots, multi-factor sets, single factors by family, and so on. A role's strength is its
best rule's account: NT$300,000 from 2015-06 and NT$10,000 every 5th, traded only by the exchange's rules, against the
same money in 0050 — each rule as its newest engine ran it.

Growth is read from the registry itself: the role as it stood just before its latest optimization — the latest run
(a batch of trials started together, the stamp in their report files) that added a new rule to the role — against
now, every rule as its newest engine ran it by then. Engine re-runs are not optimizations, but their corrections
(1.4.0's market rules, 1.5.0's capital reductions) show in the numbers, which can fall as well as rise.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

ROBOT = {"rl_blocks", "rl_free", "rl_free41", "rl_all", "rl_rolling", "rl_timing", "rl_tech", "rl_tech_weekly", "rl_worst"}
YARDSTICK = {"block_equal", "all_equal", "rolling_equal", "tech_equal"}
ORDER = ("機器學習＋因子", "T0 共識清單", "多因子組合", "組合帳戶", "單一因子：趨勢動能", "機器學習", "RL 機器人（學權重）",
         "RL 對照（同樣訊號平均）", "深度學習", "RL 機器人（完全自由）", "技術分析機器人（學賣點）", "反向（學著賠）")
NOTES = {
    "機器學習＋因子": "機器學習模型的分數和其他因子一起排名",
    "T0 共識清單": "所有 T0 候選各自排名、平均成一張清單",
    "多因子組合": "2～4 個因子的百分位相加排名（大規模搜尋找出來的）",
    "組合帳戶": "帳戶分成幾份，各自照不同規則操作",
    "機器學習": "只用機器學習模型的分數選股",
    "RL 機器人（學權重）": "強化學習決定各訊號區塊的權重",
    "RL 對照（同樣訊號平均）": "RL 機器人的對照組：同樣的訊號、權重平均",
    "深度學習": "深度學習模型的分數選股",
    "RL 機器人（完全自由）": "強化學習每天自己決定每檔比重與現金",
    "技術分析機器人（學賣點）": "強化學習決定什麼時候賣",
    "反向（學著賠）": "把訊號反過來用、或學著賠最多（趣味與檢查）",
}
_STAMP = re.compile(r"(\d{8}-\d{6})")
_cache: dict[str, object] = {}


def role(spec: dict) -> str:
    from quant_platform.research.categories import classify

    if spec.get("kind") == "blend":
        return "組合帳戶"
    factors = spec.get("factors") or {}
    names = set(factors)
    if "rl_worst" in names or any(float(weight) < 0 and (name.startswith("ml_") or name in YARDSTICK)
                                  for name, weight in factors.items()):
        return "反向（學著賠）"
    if names & {"rl_free", "rl_free41"}:
        return "RL 機器人（完全自由）"
    if names & ROBOT or any(name.startswith("rl_") for name in names):
        return "RL 機器人（學權重）"
    if names & YARDSTICK:
        return "RL 對照（同樣訊號平均）"
    if any(name.startswith("ml_deep") for name in names):
        return "深度學習"
    if any(name.startswith("ml_") for name in names):
        return "機器學習＋因子" if len(names) > 1 else "機器學習"
    if any(name.endswith("_consensus") for name in names):
        return "T0 共識清單"
    if spec.get("exit_model", "none") != "none":
        return "技術分析機器人（學賣點）"
    if len(names) > 1:
        return "多因子組合"
    return f"單一因子：{classify(spec)['family']}"


def _entries(research: Path) -> list[dict]:
    """Every daily-rule run of a 100%-stock rule: its role, time, run and metrics (cached by the registry's size)."""
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.registry import TrialRegistry

    path = research / "trials.jsonl"
    if not path.is_file():
        return []
    key = f"{path.stat().st_size}:{path.stat().st_mtime_ns}"
    if _cache.get("key") == key:
        return _cache["entries"]                                    # type: ignore[return-value]
    saved = research / "roles" / "spec_roles.json"          # spec hash → role, so the reports are read once
    roles: dict[str, str | None] = dict(_cache.get("roles") or {})
    if not roles and saved.is_file():
        try:
            roles = json.loads(saved.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            roles = {}
    known = len(roles)
    output = []
    for record in TrialRegistry(path).records():
        engine = str((record.metrics or {}).get("engine") or "")
        if record.kind != "candidate" or record.period != "recent" or not engine.startswith("daily-"):
            continue
        if record.spec_hash not in roles:
            try:
                spec = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
                roles[record.spec_hash] = None if uses_0050(spec) else role(spec)
            except (OSError, ValueError, KeyError, TypeError):
                roles[record.spec_hash] = None
        if roles[record.spec_hash] is None:
            continue
        found = _STAMP.search(record.report_file or "")
        output.append({"spec": record.spec_hash, "role": roles[record.spec_hash], "record": record,
                       "created": record.created_at, "run": found.group(1) if found else record.created_at[:10]})
    if len(roles) != known:
        try:
            saved.parent.mkdir(parents=True, exist_ok=True)
            partial = saved.with_suffix(".partial")
            partial.write_text(json.dumps(roles, ensure_ascii=False), encoding="utf-8")
            partial.replace(saved)
        except OSError:
            pass
    _cache.update(key=key, entries=output, roles=roles)
    return output


def _best(entries: list[dict], until: str | None = None) -> dict[str, dict]:
    """role → its best rule (by final value) among every rule's newest-engine record created before ``until``."""
    from quant_platform.research.registry import newest

    known = [entry for entry in entries if until is None or entry["created"] < until]
    latest = newest([entry["record"] for entry in known])
    by_spec = {entry["spec"]: entry for entry in known}
    output: dict[str, dict] = {}
    for spec, record in latest.items():
        value = (record.metrics or {}).get("final_value")
        if not value:
            continue
        name = by_spec[spec]["role"]
        if name not in output or value > (output[name]["record"].metrics or {}).get("final_value", 0):
            output[name] = {"record": record, "role": name}
    return output


def _view(record) -> dict[str, object]:
    from quant_platform.research.daily import tier

    metrics = record.metrics or {}
    windows = metrics.get("windows") or {}
    grade, _why = tier(metrics)
    drawdown = metrics.get("max_drawdown")
    xirr = metrics.get("xirr")
    return {"trial": record.trial_id, "spec": record.spec_hash, "name": record.spec_name, "engine": metrics.get("engine"), "tier": grade,
            "start": metrics.get("start"), "final": metrics.get("final_value"),
            "bench_final": metrics.get("benchmark_final_value"), "contributed": metrics.get("contributed"),
            "xirr": xirr, "bench_xirr": metrics.get("benchmark_xirr"),
            "excess_full": metrics.get("full_period_excess"), "excess_recent": metrics.get("since_2020_excess"),
            "drawdown": drawdown, "bench_drawdown": metrics.get("benchmark_max_drawdown"),
            "calmar": round(xirr / abs(drawdown), 2) if xirr is not None and drawdown else None,
            "win_1y": (windows.get("1y") or {}).get("win_ratio"), "win_3y": (windows.get("3y") or {}).get("win_ratio"),
            "worst_1y": (windows.get("1y") or {}).get("worst_excess"),
            "orders": metrics.get("orders_per_month"), "cost_share": metrics.get("cost_share"),
            "turnover": metrics.get("turnover")}


def now_day() -> str:
    from datetime import datetime

    return datetime.now().date().isoformat()


def summary(research_dir: str | Path) -> dict[str, object]:
    """Every role: its best now, before its latest run, the change, its grades and a few earlier points."""
    research = Path(research_dir)
    entries = _entries(research)
    if not entries:
        return {"roles": [], "latest_run": None}
    memo: dict[str | None, dict[str, dict]] = {}

    def as_of(until: str | None = None) -> dict[str, dict]:
        if until not in memo:
            memo[until] = _best(entries, until)
        return memo[until]

    now = as_of()
    started: dict[str, str] = {}
    first_seen: dict[str, dict] = {}
    for entry in entries:                             # a run's first record; a rule's first run
        started[entry["run"]] = min(started.get(entry["run"], entry["created"]), entry["created"])
        if entry["spec"] not in first_seen or entry["created"] < first_seen[entry["spec"]]["created"]:
            first_seen[entry["spec"]] = entry
    by_role: dict[str, list[dict]] = defaultdict(list)
    for entry in entries:
        by_role[entry["role"]].append(entry)
    from quant_platform.research.daily import tier
    from quant_platform.research.registry import newest

    rows = []
    for name, best in now.items():
        mine = by_role[name]
        newest_rule = max((first_seen[entry["spec"]] for entry in mine), key=lambda entry: entry["created"])
        last_run = newest_rule["run"]                             # the latest run that added a rule to the role
        before = as_of(started[last_run]).get(name)
        days = sorted({first_seen[entry["spec"]]["created"][:10] for entry in mine} | {now_day()})
        history = []
        for day in days:
            point = as_of(f"{day}T99").get(name)
            if point:
                history.append({"day": day, "final": (point["record"].metrics or {}).get("final_value")})
        grades: dict[str, int] = defaultdict(int)
        latest = newest([entry["record"] for entry in mine])
        for record in latest.values():
            grades[tier(record.metrics or {})[0]] += 1
        view = _view(best["record"])
        previous = _view(before["record"]) if before else None
        change = (view["final"] / previous["final"] - 1) if previous and previous["final"] else None
        rows.append({"role": name, "note": NOTES.get(name, ""), "now": view, "before": previous, "change": change,
                     "first": history[0] if history else None,
                     "growth_total": (view["final"] / history[0]["final"] - 1) if history and history[0]["final"] else None,
                     "last_run": started[last_run], "rules": len(latest), "grades": dict(grades),
                     "history": history[-12:]})
    rank = {name: index for index, name in enumerate(ORDER)}
    rows.sort(key=lambda row: (-(row["now"]["final"] or 0)))
    for row in rows:
        row["order"] = rank.get(row["role"], len(ORDER))
    return {"roles": rows, "latest_run": max(started.values()) if started else None,
            "benchmark": rows[0]["now"]["bench_final"] if rows else None}
