"""The page 研究 › AI 代理人 (/research/agents): read from the lab's files only — the decision stores, the board the
day's run wrote, the replays' boards and the memory probe. Nothing is computed at the visit."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from quant_platform.research.agent_lab import runner, variants
from quant_platform.research.agent_lab.evaluate import SESSIONS_TO_JUDGE, first_common_day, judge_day

TOOL_NAMES = {"market": "大盤", "consensus": "規則共識", "screen": "訊號篩選", "card": "個股資料", "portfolio": "持股",
              "journal": "日誌", "headlines": "新聞標題", "mood": "市場情緒", "lessons": "研究知識庫", "review": "交易檢討",
              "check": "名單檢查", "events": "公司事件"}


def _load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except ValueError:
        return None


def _names(research: Path) -> dict[str, str]:
    try:
        from datetime import date

        from quant_platform.research.stock_forward import _names as stock_names

        return stock_names(research / "history", date.today().year)
    except Exception:  # noqa: BLE001 - the page shows the codes alone
        return {}


def design(variant, made: list[dict], names: dict[str, str]) -> dict[str, object]:
    latest = made[-1] if made else None
    llm = (latest or {}).get("llm") or {}
    before = set(((made[-2].get("llm") or {}).get("hold") or [])) if len(made) > 1 else set()
    hold = list(llm.get("hold") or [])
    tools = Counter(str(step.get("tool")) for step in llm.get("trace") or [] if step.get("tool"))
    reasons = llm.get("reasons") or {}
    return {
        "key": variant.key, "label": variant.label, "question": variant.question, "against": variant.against,
        "mode": variant.mode, "date": (latest or {}).get("date"), "days": len(made),
        "holdings": [{"code": code, "name": names.get(code, ""), "reason": reasons.get(code, ""),
                      "new": bool(before) and code not in before} for code in hold],
        "sold": [{"code": code, "name": names.get(code, "")} for code in sorted(before - set(hold))],
        "vetoed": [{"code": code, "name": names.get(code, ""), "reason": reason} for code, reason in (llm.get("vetoed") or {}).items()],
        "view": llm.get("view") or "", "lesson": llm.get("lesson") or "", "fallback": llm.get("fallback") or "",
        "tools": [{"name": TOOL_NAMES.get(name, name), "count": count} for name, count in tools.most_common()],
        "runs": llm.get("runs") or [], "cost_usd": float(llm.get("cost_usd") or 0.0),
        "fallbacks": sum(1 for item in made if (item.get("llm") or {}).get("fallback")),
    }


def probe_summary(result: dict | None) -> dict | None:
    if not result:
        return None
    groups: dict[str, list[int]] = {}
    for row in result.get("months") or []:
        if "error" in row:
            continue
        month = str(row["month"])
        name = (month[:4] + " 上半年" if month[5:] <= "06" else month[:4] + " 下半年")
        total = groups.setdefault(name, [0, 0, 0])
        total[0] += int(row.get("right", 0))
        total[1] += int(row.get("right", 0)) + int(row.get("wrong", 0))
        total[2] += 1 if row.get("declined") else 0               # 2026-11-20: months it said it does not know
    rows = [{"period": name, "right": right, "asked": asked, "accuracy": right / asked if asked else 0.0,
             "errors": (right - asked / 2) / (0.5 * asked ** 0.5) if asked else 0.0, "declined": declined}
            for name, (right, asked, declined) in sorted(groups.items()) if asked or declined]
    return {"model": result.get("model"), "made_at": result.get("made_at"), "stocks": result.get("stocks"), "rows": rows}


def page(research: str | Path) -> dict[str, object]:
    research = Path(research)
    names = _names(research)
    root = runner.root(research)
    designs = [design(variant, runner.records(research, variant.key), names) for variant in variants.COHORT]
    board = _load(root / "board.json")
    labels = {variant.key: variant.label for variant in variants.COHORT} | dict(variants.OUTSIDE) | {"0050": "0050"}
    replays = []
    for folder in sorted((root / "replay").glob("*")) if (root / "replay").is_dir() else []:
        found = _load(folder / "board.json")
        if found and found.get("rows") and not folder.name.startswith(("dry-", "check-")):
            replays.append({"name": folder.name, **found})
    probes = [summary for path in sorted((root / "probe").glob("*.json")) if (root / "probe").is_dir()
              for summary in [probe_summary(_load(path))] if summary]
    decided = [item["date"] for item in designs if item["date"]]
    first = first_common_day(research, None)
    return {"designs": designs, "board": board, "replays": replays, "probes": probes, "labels": labels,
            "latest_day": max(decided) if decided else None, "sessions_to_judge": SESSIONS_TO_JUDGE,
            "first_decision": first, "judge_day": judge_day(research, first) if first else None,
            "month_cost": round(sum(float((item.get("llm") or {}).get("cost_usd") or 0) for variant in variants.COHORT
                                    for item in runner.records(research, variant.key)), 4)}
