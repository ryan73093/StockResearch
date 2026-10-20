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
    from quant_platform.research.stock_forward import llm_trader_start

    llm_start = llm_trader_start(research)       # the LLM trader's pair start at its first decision (no history)
    wrong_start = [item["name"] for item in live if item.get("since") != (
        llm_start if any(name in (item.get("rule") or {}).get("factors", {}) for name in ("llm_trader", "llm_control"))
        else start)]
    checks.append(_check("前向帳戶從當月截止日隔天起算", "AGENTS.md：前向觀察與挑策略一律每月滾動",
                         not wrong_start, f"起算日應為 {start}；不是的 {len(wrong_start)} 個"
                         + (f"：{wrong_start[:3]}" if wrong_start else "")))

    plans = [item["name"] for item in live
             if float(item.get("initial") or 0) != SEED_CAPITAL or float(item.get("monthly") or 0) != SEED_MONTHLY]
    checks.append(_check("前向帳戶現金流是 30 萬＋每月 1 萬", "需求 §8 策略帳戶（使用者 2026-10-04）", not plans,
                         f"不是的 {len(plans)} 個" + (f"：{plans[:3]}" if plans else "")))

    cutoff = cutoff_for(today)
    month = rolling_folder(research) / f"{cutoff.isoformat()}.json"
    graded: set[str] = set()
    if month.is_file():
        payload = json.loads(month.read_text(encoding="utf-8"))
        graded = {str(row.get("rule_hash")) for row in payload.get("graded") or [] if row.get("passed")}
    experiments = {rule.rule_hash for rule, _reason in forward_only()}
    foreign = [item["name"] for item in live if item["rule_hash"] not in graded | experiments]
    checks.append(_check("前向帳戶的規則和每月名單上評過分的一模一樣（平台不轉換任何設定）",
                         "需求 §7：只有交易所的規則是限制，其他都是策略的選擇（使用者 2026-10-16、10-20）",
                         bool(graded) and not foreign,
                         f"名單 {cutoff} 過門檻 {len(graded)} 個；帳戶不在名單也不是實驗的 {len(foreign)} 個"
                         + (f"：{foreign[:3]}" if foreign else "") + ("" if graded else "；這個月的名單還沒評")))

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

    early = []
    pool_path = research / "forward" / "stocks" / "board.json"
    if pool_path.is_file():
        board = json.loads(pool_path.read_text(encoding="utf-8"))
        early = [row.get("name") for row in board.get("rows") or [] if str(row.get("tier", "")).startswith("T0（")
                 or row.get("tier") == "T0"]
    checks.append(_check("12/28 以前沒有規則被標成 T0", f"路線圖判定準則：串接前向滿 {FORWARD_SESSIONS} 個交易日",
                         today >= date(2026, 12, 28) or not early, f"被標成 T0 的 {len(early)} 個"))

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
