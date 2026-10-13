"""2026-10-13 (使用者：能否串 AI 讓使用者知道自己的假設是好還是壞，畢竟跑過那麼多策略了): the hypothesis check.

The owner writes an idea in a sentence ("外資連買又站上季線的股票，下個月會贏大盤"). The check:
1. a language model restates it and maps it onto the platform's factors (direction +1 or -1, at most four),
   naming any data the platform does not have — it never decides whether the idea is good;
2. the evidence already on file: every registered rule whose factors overlap it (the same factors, or most of
   them), with their tiers and results, and each factor's strength;
3. one new trial: the idea as a rule (top 20, at most 3 in 10 from one industry, every listed stock or the 100
   largest, daily or weekly), run by the full engine on the owner's account and registered like any rule;
4. a verdict from fixed thresholds (the gate's own tiers), and a short plain-language explanation written by
   the model from those numbers only.

Ideas wait in ``instance/research/hypotheses/`` (one JSON each); the worker starts a background run every few
minutes when one is waiting (scripts/start-research.ps1 -Name hypotheses), outside the closing and data hours.
The model shares the monthly LLM budget (operation ``hypothesis_check``).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION = "hypothesis-1.0.0"
OPERATION = "hypothesis_check"
TAIPEI = ZoneInfo("Asia/Taipei")
MAX_TEXT = 300
INSTRUCTIONS = (
    "你把使用者的一句台股選股假設，轉成交易研究平台能測的規則。平台每天收盤後替合格股票打分數，買分數最高的 20 檔。"
    "只能用下面清單裡的因子（名稱：說明），每個因子給方向：1 代表數值越大越好、-1 代表越小越好；最多 4 個。"
    "如果假設需要平台沒有的資料（例如券商分點、集保大戶持股、新聞情緒的歷史、盤中分鐘資料、分析師預估），寫在 missing。"
    "只是翻譯，不要評論假設好不好。回覆 JSON："
    '{"restatement": "一句話重述", "factors": {"因子名稱": 1}, "universe": "all 或 large（只在市值前 100 大）", '
    '"check": "daily 或 weekly", "missing": ["缺的資料"], "testable": true, "note": "翻譯時的取捨"}'
)
EXPLAIN = (
    "你是研究助理。根據下面的數字，用繁體中文寫 3 到 5 句話，告訴使用者他的假設在台股 2015-06 到 2026-09 的回測裡表現如何、"
    "為什麼（引用數字），和平台已經試過的類似策略比起來如何。不要預測未來報酬、不要給買賣建議，"
    "要提醒回測不代表未來。只能用給你的數字。回覆 JSON：{\"explanation\": \"...\"}"
)


def folder(research: Path) -> Path:
    return Path(research) / "hypotheses"


def submit(research: Path, text: str, now: datetime | None = None) -> dict[str, object]:
    text = " ".join(str(text).split())[:MAX_TEXT]
    if len(text) < 4:
        raise ValueError("假設太短了")
    moment = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    item = {"id": moment.strftime("%Y%m%d-%H%M%S"), "text": text, "status": "queued",
            "submitted_at": moment.isoformat(timespec="seconds"), "version": VERSION}
    target = folder(research)
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{item['id']}.json"
    suffix = 1
    while path.exists():
        suffix += 1
        path = target / f"{item['id']}-{suffix}.json"
        item["id"] = path.stem
    path.write_text(json.dumps(item, ensure_ascii=False, indent=1), encoding="utf-8")
    return item


def items(research: Path, limit: int = 30) -> list[dict[str, object]]:
    target = folder(research)
    if not target.is_dir():
        return []
    output = []
    for path in sorted(target.glob("*.json"), reverse=True)[:limit]:
        try:
            output.append(json.loads(path.read_text(encoding="utf-8")))
        except ValueError:
            continue
    return output


def pending(research: Path) -> list[dict[str, object]]:
    return [item for item in items(research, 200) if item.get("status") == "queued"]


def _save(research: Path, item: dict[str, object]) -> None:
    (folder(research) / f"{item['id']}.json").write_text(json.dumps(item, ensure_ascii=False, indent=1), encoding="utf-8")


def vocabulary() -> dict[str, str]:
    """The factors an idea may use: every rule factor except the consensus lists (chosen with later results)
    and the RL robot's scores."""
    from quant_platform.research.daily import CONSENSUS_FACTORS, RULE_FACTORS, TECH_FACTORS

    return {name: label for name, label in RULE_FACTORS.items()
            if name not in CONSENSUS_FACTORS and name not in TECH_FACTORS and name != "ml_gbm"}


def parse(client, text: str, context: str) -> tuple[dict[str, object], dict]:
    names = vocabulary()
    lines = [f"- {name}：{label}" for name, label in names.items()]
    payload, usage = client.json_call(INSTRUCTIONS, "因子清單：\n" + "\n".join(lines) + f"\n\n假設：{text}", OPERATION, context)
    factors = {}
    for name, weight in (payload.get("factors") or {}).items():
        if name in names and weight in (1, -1, 1.0, -1.0):
            factors[name] = float(weight)
    payload["factors"] = dict(list(factors.items())[:4])
    payload["universe"] = "large" if payload.get("universe") == "large" else "all"
    payload["check"] = "weekly" if payload.get("check") == "weekly" else "daily"
    payload["missing"] = [str(item)[:80] for item in (payload.get("missing") or [])][:5]
    return payload, usage


def similar(research: Path, factors: dict[str, float], limit: int = 8) -> list[dict[str, object]]:
    """Registered daily rules sharing at least half of the factors (Jaccard), best overlap first."""
    from quant_platform.research.daily import tier
    from quant_platform.research.registry import TrialRegistry

    wanted = set(factors)
    latest = {}
    for record in TrialRegistry(research / "trials.jsonl").records():
        if record.kind == "candidate" and record.period == "recent":
            latest[record.spec_hash] = record
    found = []
    for record in latest.values():
        try:
            report = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        spec = report.get("spec") or {}
        theirs = set((spec.get("factors") or {}).keys())
        if not theirs:
            continue
        overlap = len(wanted & theirs) / len(wanted | theirs)
        if overlap < 0.5:
            continue
        found.append({"trial": record.trial_id, "name": record.spec_name, "overlap": round(overlap, 2),
                      "tier": tier(record.metrics)[0], "excess": report.get("full_period_excess"),
                      "since_2020": (report.get("since_2020") or {}).get("excess"),
                      "drawdown": (report.get("strategy") or {}).get("max_drawdown"), "spec_hash": record.spec_hash})
    found.sort(key=lambda item: (-item["overlap"], -(item["excess"] or -9)))
    return found[:limit]


def strengths(research: Path, factors: dict[str, float]) -> dict[str, object]:
    import glob
    import os

    files = sorted(glob.glob(str(research / "factors" / "strength-*-recent.json")), key=os.path.getmtime)
    if not files:
        return {}
    summary = (json.loads(Path(files[-1]).read_text(encoding="utf-8")).get("factors") or {})
    return {name: {"label": (summary.get(name) or {}).get("label"), "verdict": (summary.get(name) or {}).get("verdict")}
            for name in factors if name in summary}


VERDICTS = {
    "T0 候選": ("好", "兩段期間都贏同樣的錢買 0050，視窗勝率與回撤都在門檻內（還要前向觀察確認）。"),
    "T1": ("部分好", "兩段期間都贏 0050，但回撤或視窗勝率沒過門檻：報酬有、太顛簸。"),
    "T2": ("不穩定", "只在部分期間贏 0050，或贏得不穩定。"),
    "T3": ("不好", "輸給同樣的錢買 0050。"),
}


def run_one(research: Path, history: Path, item: dict, client, data, fp, fingerprint: str, costs) -> dict[str, object]:
    from quant_platform.research import daily
    from quant_platform.research.registry import TrialRegistry

    item["status"] = "running"
    _save(research, item)
    parsed, usage = parse(client, item["text"], item["id"])
    item["parsed"] = parsed
    item["usage"] = usage
    if not parsed["factors"] or not parsed.get("testable", True):
        item.update(status="done", verdict="無法測試",
                    reason="平台的因子對不上這個假設" + (f"；缺少：{'、'.join(parsed['missing'])}" if parsed["missing"] else ""))
        _save(research, item)
        return item
    item["similar"] = similar(research, parsed["factors"])
    item["strengths"] = strengths(research, parsed["factors"])
    name = f"使用者假設：{str(parsed.get('restatement') or item['text'])[:60]}"
    rule = daily.DailyRule(name=name[:80], factors=parsed["factors"], top=20, check=parsed["check"], industry_cap=0.3,
                           large_caps=100 if parsed["universe"] == "large" else 0)
    record, report = daily.run_trial(rule, history, TrialRegistry(research / "trials.jsonl"), research / "reports", costs,
                                     data, fp, fingerprint, {}, item["id"])
    grade = daily.tier(record.metrics)[0]
    verdict, reason = VERDICTS.get(grade, ("不明", ""))
    result = {"trial": record.trial_id, "spec_hash": record.spec_hash, "tier": grade,
              "final_value": report["strategy"]["final_value"], "benchmark_value": report["benchmark"]["final_value"],
              "excess": report["full_period_excess"], "since_2020": report["since_2020"]["excess"],
              "drawdown": report["strategy"]["max_drawdown"], "benchmark_drawdown": report["benchmark"]["max_drawdown"],
              "win_1y": report["windows"]["1y"].get("win_ratio"), "win_3y": report["windows"]["3y"].get("win_ratio"),
              "orders_per_month": (report.get("activity") or {}).get("orders_per_month"),
              "reasons": record.metrics.get("reasons") or []}
    peers = item["similar"]
    good = sum(1 for peer in peers if peer["tier"] in ("T0 候選", "T1"))
    item.update(result=result, verdict=verdict,
                reason=reason + (f" 平台試過 {len(peers)} 個類似的策略，其中 {good} 個是 T0 候選或 T1。" if peers else ""))
    try:
        explained, explain_usage = client.json_call(
            EXPLAIN, json.dumps({"假設": item["text"], "重述": parsed.get("restatement"), "結果": result,
                                 "判定": verdict, "類似策略": peers, "因子強弱": item["strengths"]}, ensure_ascii=False),
            OPERATION, item["id"] + "-explain")
        item["explanation"] = str(explained.get("explanation") or "")[:800]
        item["usage_explain"] = explain_usage
    except Exception as error:                          # the numbers stand without the explanation
        item["explanation_error"] = str(error)[:200]
    item["status"] = "done"
    item["finished_at"] = datetime.now(TAIPEI).isoformat(timespec="seconds")
    _save(research, item)
    return item


def build_client(settings, research: Path):
    from quant_platform.research.agent.llm import LLMError, ResponsesClient, UsageLedger, resolve_openai_key

    try:
        key = resolve_openai_key(settings.openai_api_key)
    except LLMError:
        return None
    if not key:
        return None
    return ResponsesClient(key, settings.research_agent_model, UsageLedger(Path(research) / "agent" / "usage.jsonl"),
                           settings.hypothesis_monthly_budget_usd, operations=(OPERATION,),
                           total_budget_usd=settings.llm_monthly_budget_usd, label="假設檢驗")


def run_pending(research: Path, history: Path, settings, costs, job=None) -> list[dict[str, object]]:
    from quant_platform.research import daily

    waiting = pending(research)
    if not waiting:
        return []
    client = build_client(settings, research)
    if client is None:
        for item in waiting:
            item.update(status="failed", reason="沒有可用的語言模型金鑰或已超過每月預算")
            _save(research, item)
        return waiting
    data, fp = daily.load(history)
    fingerprint = daily.fingerprint(history)
    done = []
    for index, item in enumerate(waiting):
        if job:
            job.update(done=index, current=item["text"][:40], force=True)
        try:
            done.append(run_one(research, history, item, client, data, fp, fingerprint, costs))
        except Exception as error:
            item.update(status="failed", reason=str(error)[:300])
            _save(research, item)
            done.append(item)
    return done
