"""2026-10-20 (使用者：專案需求跟 AGENTS.md 到底有沒有在看；之前不是都說改好了): the owner's decisions checked as code,
every day after the forward records — a decision that slips back (as the fixed holding did between 10-14 and 10-20) is
caught the next day, not weeks later.

Each check reads the files the platform writes and returns (ok, what was found). On the System page (需求檢查) and,
when one fails, a notification.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path


def _check(name: str, source: str, ok: bool, detail: str) -> dict[str, object]:
    return {"name": name, "source": source, "ok": bool(ok), "detail": detail}


def run(research: str | Path, today: date, backups: Path | None = None) -> list[dict[str, object]]:
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.forward_board import common_start
    from quant_platform.research.rolling import cutoff_for, folder as rolling_folder
    from quant_platform.research.stock_forward import StockForwardTracker, forward_only, reconcile
    from quant_platform.research.stock_rules import SEED_CAPITAL, SEED_MONTHLY

    research = Path(research)
    tracker = StockForwardTracker(research)
    items = tracker.tracked()
    live = [item for item in items if not item.get("stopped") and item.get("kind") in ("daily", "blend")]
    checks = []

    held_0050 = [item["name"] for item in live if uses_0050(item.get("rule") or {})]
    checks.append(_check("前向帳戶沒有 0050", "AGENTS.md 已核定方向：策略一律 100% 個股", not held_0050,
                         f"{len(live)} 個帳戶；含 0050 的 {len(held_0050)} 個" + (f"：{held_0050[:3]}" if held_0050 else "")))

    start = common_start(today).isoformat()
    # 2026-10-21 (使用者：不要跟我說前向觀察只能做一天): no exception — the LLM trader's pair too
    wrong_start = [item["name"] for item in live if item.get("since") != start]
    checks.append(_check("前向帳戶從當月截止日隔天起算", "AGENTS.md：前向觀察與挑策略一律每月滾動",
                         not wrong_start, f"起算日應為 {start}；不是的 {len(wrong_start)} 個"
                         + (f"：{wrong_start[:3]}" if wrong_start else "")))

    plans = [item["name"] for item in live
             if float(item.get("initial") or 0) != SEED_CAPITAL or float(item.get("monthly") or 0) != SEED_MONTHLY]
    checks.append(_check("前向帳戶現金流是 30 萬＋每月 1 萬", "需求 §8 策略帳戶（使用者 2026-10-04）", not plans,
                         f"不是的 {len(plans)} 個" + (f"：{plans[:3]}" if plans else "")))

    # 2026-10-20: the money actually put in, from the trading calendar (the setting alone missed the LLM pair given
    # October's contribution on its first day, the 20th)
    from quant_platform.research.history.dataset import read_series
    from quant_platform.research.stock_rules import SeedPlan

    calendar_path = research / "history" / "daily" / "0050.parquet"
    latest_of: dict[str, dict] = {}
    for record in tracker.records():
        if record["date"] >= latest_of.get(record["rule_hash"], {}).get("date", ""):
            latest_of[record["rule_hash"]] = record
    paid_wrong: list[str] = []
    if calendar_path.is_file():
        calendar = sorted(row["date"] for row in read_series(calendar_path))
        for item in live:
            record = latest_of.get(item["rule_hash"])
            if record is None:
                continue
            since, last = date.fromisoformat(item["since"]), date.fromisoformat(record["date"])
            sessions = [day for day in calendar if since <= day <= last]
            expected = sum(amount for _day, amount in SeedPlan(calendar=calendar).schedule(sessions, since, last))
            if abs(float(record["contributed"]) - expected) > 1:
                paid_wrong.append(item["name"])
    checks.append(_check("前向帳戶實際投入＝啟動 30 萬＋每月 5 日 1 萬（照交易日曆）", "需求 §8 策略帳戶（使用者 2026-10-04）",
                         calendar_path.is_file() and not paid_wrong,
                         f"投入金額不對的 {len(paid_wrong)} 個" + (f"：{paid_wrong[:3]}" if paid_wrong else "")
                         + ("" if calendar_path.is_file() else "；找不到交易日曆")))

    cutoff = cutoff_for(today)
    month = rolling_folder(research) / f"{cutoff.isoformat()}.json"
    graded: set[str] = set()
    if month.is_file():
        payload = json.loads(month.read_text(encoding="utf-8"))
        graded = {str(row.get("rule_hash")) for row in payload.get("graded") or []
                  if row.get("passed") or row.get("tier") == "T1"}             # T1 goes forward too (REQUIREMENTS)
    experiments = {rule.rule_hash for rule, _reason in forward_only()}
    foreign = [item["name"] for item in live if item["rule_hash"] not in graded | experiments]
    checks.append(_check("前向帳戶的規則和每月名單上評過分的一模一樣（平台不轉換任何設定）",
                         "需求 §7：只有交易所的規則是限制，其他都是策略的選擇（使用者 2026-10-16、10-20）",
                         bool(graded) and not foreign,
                         f"名單 {cutoff} 過門檻 {len(graded)} 個；帳戶不在名單也不是實驗的 {len(foreign)} 個"
                         + (f"：{foreign[:3]}" if foreign else "") + ("" if graded else "；這個月的名單還沒評")))

    # 2026-10-21 (使用者 2026-10-16：每月滾動，模型也要更新；稽核：深度學習、風險、後設標籤、RL 都只有逐年模型):
    # every model a live forward account reads has its refit to the month's cutoff
    from quant_platform.research import model as research_model
    from quant_platform.research.model_deep2 import MONTHLY as DEEP_MONTHLY

    models = research / "models"
    read = set().union(*[tracker.uses(item) for item in live] or [set()])
    stale: set[str] = set()
    for factor in sorted(read):
        spec = research_model.MODELS.get(factor)
        if spec is not None:
            version = spec["version"]
            if spec["label"] in ("deep2", "deep_risk", "deep_risk2"):   # networks: monthly/<cutoff>-<seed>.pt
                if version not in DEEP_MONTHLY and not (models / version / "monthly" / f"{cutoff.isoformat()}-0.pt").is_file():
                    stale.add(version)
            elif spec.get("refit") != "monthly" and not (models / version / "monthly" / f"{cutoff.isoformat()}.pkl").is_file():
                stale.add(version)
        elif factor in ("dl1", "dl1x"):
            if not (models / research_model.MODELS["dl_risk"]["version"] / "monthly" / f"{cutoff.isoformat()}-0.pt").is_file():
                stale.add("風險模型 " + factor)
        elif factor in ("dl2", "dl2x"):
            stale.add("風險模型 " + factor)
        elif factor == "meta1":
            if not (models / "meta-1.0.0" / "monthly" / f"{cutoff.isoformat()}.pkl").is_file():
                stale.add("後設標籤 meta-1.0.0")
        elif factor in ("block_equal", "tech_equal"):
            continue                          # fixed factors weighted alike: nothing learned, nothing to refit
        elif factor.startswith(("rl_", "rolling")):
            from quant_platform.research import rl_timing

            version = {"rl_blocks": rl_timing.BLOCK_VERSION, "rl_rolling": rl_timing.ROLLING_VERSION,
                       "rolling_equal": rl_timing.ROLLING_VERSION}.get(factor) or {
                "32": rl_timing.ROLLING32_VERSION, "33": rl_timing.ROLLING33_VERSION,
                "34": rl_timing.ROLLING34_VERSION}.get(factor[-2:] if factor.startswith("rl_rolling") else factor[7:9])
            meta_path = models / str(version) / "meta.json"
            try:
                refits = json.loads(meta_path.read_text(encoding="utf-8")).get("monthly") or {}
            except (OSError, ValueError):
                refits = {}
            if cutoff.isoformat() not in refits:
                stale.add("RL 機器人 " + factor)
    checks.append(_check("前向帳戶用到的模型都重訓到當月截止日", "AGENTS.md：只用截止日以前的資料挑策略、重訓模型（使用者 2026-10-16）",
                         not stale, f"截止日 {cutoff}；還沒重訓的 {len(stale)} 個" + (f"：{sorted(stale)[:6]}" if stale else "")))

    from quant_platform.research.stock_forward import FIXED_HOLD

    converted = [item["name"] for item in items if item.get("stop_reason") == FIXED_HOLD
                 and str(item.get("stopped") or "") > "2026-10-20"]
    checks.append(_check("沒有新的持有方式轉換", "使用者 2026-10-20：持有 20 天、月線上續抱都是策略，平台不轉換",
                         not converted, f"10/20 之後因轉換停止的帳戶 {len(converted)} 個"))

    records = tracker.records()
    problems = reconcile(records, tracker.rebases())
    broken = {key: value for key, value in problems.items() if any(item["rule_hash"] == key for item in live)}
    checks.append(_check("前向帳戶對帳 0 問題", "需求 §8 個股規則前向模擬：對帳", not broken,
                         f"有問題的帳戶 {len(broken)} 個"))
    latest = max((record["date"] for record in records), default=None)
    checks.append(_check("前向紀錄記到最近的交易日", "需求 §8：每個交易日 15:30 記錄", latest is not None,
                         f"最新紀錄 {latest}"))

    from quant_platform.research.daily import FORWARD_SESSIONS

    # 2026-10-21 (稽核：board.json 沒有 tier 欄位，這項原本永遠通過): the grades as the pool computes them — each
    # newest run with its chained forward record (pool.rule_rows)
    from quant_platform.research.daily import tier
    from quant_platform.research.registry import TrialRegistry, newest

    early = []
    registry_path = research / "trials.jsonl"
    if registry_path.is_file():
        latest = newest(TrialRegistry(registry_path).records(),
                        lambda record: record.kind == "candidate" and record.period == "recent")
        summary = {row["rule_hash"]: row for row in tracker.summary()}
        judged = {key: {**value, "problems": (summary.get(key) or {}).get("problems") or []}
                  for key, value in tracker.chained(today).items()}
        for key, record in latest.items():
            grade = tier(record.metrics, judged.get(key))[0]
            if grade.startswith("T0") and not grade.startswith("T0 候選"):
                early.append(record.spec_name)
    checks.append(_check("12/28 以前沒有規則被標成 T0", f"路線圖判定準則：串接前向滿 {FORWARD_SESSIONS} 個交易日",
                         today >= date(2026, 12, 28) or not early, f"被標成 T0 的 {len(early)} 個"))

    # 2026-10-21 (使用者 2026-10-14：確實檢查每個時期都照當時的台股規則): the latest trade audit, at most 8 days old
    audits = sorted((research / "audit").glob("trade-audit-*.json"), key=lambda path: path.stat().st_mtime) \
        if (research / "audit").is_dir() else []
    audit = json.loads(audits[-1].read_text(encoding="utf-8")) if audits else {}
    made = datetime.fromtimestamp(audits[-1].stat().st_mtime) if audits else None
    counts = {key: value for key, value in (audit.get("violation_counts") or {}).items() if value}
    checks.append(_check("最新的交易規則稽核 0 違規（8 天內）", "使用者 2026-10-14：每個時期都照當時的台股規則；每週日 06:00 稽核",
                         bool(audits) and not counts and made is not None and (datetime.now() - made).days <= 8,
                         (f"{made:%m-%d %H:%M}：{len(audit.get('rules') or [])} 個規則、{audit.get('orders', 0):,} 筆委託、"
                          f"違規 {sum(counts.values())}" + (f" {counts}" if counts else "")) if audits else "沒有稽核紀錄"))

    if backups is not None:
        files = sorted(backups.glob("*"), key=lambda path: path.stat().st_mtime) if backups.is_dir() else []
        newest = datetime.fromtimestamp(files[-1].stat().st_mtime) if files else None
        checks.append(_check("資料庫每日備份", "AGENTS.md 資料庫安全：每日 03:00、保留 7 份",
                             newest is not None and (datetime.now() - newest).days < 2,
                             f"{len(files)} 份，最新 {newest:%m-%d %H:%M}" if newest else "沒有備份"))
    return checks


def save(research: str | Path, checks: list[dict[str, object]], now: datetime) -> Path:
    path = Path(research) / "invariants.json"
    path.write_text(json.dumps({"at": now.isoformat(timespec="seconds"), "checks": checks}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return path


def latest(research: str | Path) -> dict[str, object] | None:
    try:
        return json.loads((Path(research) / "invariants.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def tile(payload: dict[str, object] | None) -> dict[str, str]:
    if not payload:
        return {"label": "需求檢查", "state": "尚未執行", "badge": "", "detail": "每天 16:00 對照需求與 AGENTS.md 檢查"}
    failed = [item for item in payload["checks"] if not item["ok"]]
    return {"label": "需求檢查", "state": "全部符合" if not failed else f"{len(failed)} 項不符",
            "badge": "badge--ok" if not failed else "badge--bad",
            "detail": f"{str(payload['at'])[5:16].replace('T', ' ')}・{len(payload['checks'])} 項"
                      + (f"・{'；'.join(item['name'] for item in failed[:2])}" if failed else "")}
