"""2026-10-20 (使用者：專案需求跟 AGENTS.md 到底有沒有在看；之前不是都說改好了): the owner's decisions checked as code,
every day after the forward records — a decision that slips back (as the fixed holding did between 10-14 and 10-20) is
caught the next day, not weeks later.

Each check reads the files the platform writes and returns (ok, what was found). On the System page (需求檢查) and,
when one fails, a notification.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

# The roadmap's date table: the 60th session from 2026-09-01, the October list's forward start (使用者 2026-10-23：叫你要
# 浮動的 — not from 10-01): 35 sessions to 10-22, 39 by October's end (10/23 typhoon, 10/26 closed), the 21st of November
FIRST_T0 = date(2026, 11, 30)


CONSOLES = ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS")
OWN_MARKS = (".venv", "quant_platform", "quant-web", "quant-api", "quant-worker", "research-pipeline", "start-research")


def desktop_windows() -> list[dict[str, object]]:
    """2026-10-23 (AGENTS.md：背景程式不開視窗; 使用者 2026-10-20 看到一堆沒說明的空白 cmd 視窗): the visible top-level
    windows on this desktop — title, class, process id, the program's path (Windows only; the worker runs in the
    owner's session, so it sees the same desktop)."""
    import sys

    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    found: list[dict[str, object]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(window, _param):
        if not user32.IsWindowVisible(window):
            return True
        length = user32.GetWindowTextLengthW(window)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(window, title, length + 1)
        kind = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(window, kind, 256)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
        path = ""
        handle = kernel32.OpenProcess(0x1000, False, pid.value)          # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                path = buffer.value
            kernel32.CloseHandle(handle)
        found.append({"title": title.value, "class": kind.value, "pid": int(pid.value), "path": path})
        return True

    user32.EnumWindows(each, 0)
    return found


def own_windows(windows: list[dict[str, object]], root: Path) -> list[dict[str, object]]:
    """The windows this project opened: any of its own programs' (the .venv's python, quant-web/api/worker), or a
    console window whose title names the project, its .venv or its research scripts."""
    folder = str(root).lower()
    output = []
    for window in windows:
        path, title = str(window.get("path") or "").lower(), str(window.get("title") or "").lower()
        if path.startswith(folder) or (window.get("class") in CONSOLES
                                       and (folder in title or any(mark in title for mark in OWN_MARKS))):
            output.append(window)
    return output


def memory_events(hours: int = 24) -> list[dict[str, object]]:
    """2026-10-29: the times Windows ran out of memory to commit in the last ``hours`` (Resource-Exhaustion-Detector,
    event 2004), each with the program that held the most (Taipei time; Windows only, read with no window)."""
    import re
    import subprocess
    import sys

    if sys.platform != "win32":
        return []
    query = ("*[System[Provider[@Name='Microsoft-Windows-Resource-Exhaustion-Detector'] and (EventID=2004) and "
             f"TimeCreated[timediff(@SystemTime) <= {hours * 3_600_000}]]]")
    done = subprocess.run(["wevtutil", "qe", "System", f"/q:{query}", "/f:xml", "/c:50"], capture_output=True,
                          creationflags=0x08000000, timeout=60)                  # CREATE_NO_WINDOW
    if done.returncode != 0:
        raise RuntimeError(done.stderr.decode("utf-8", errors="replace")[:120] or f"wevtutil {done.returncode}")
    output = []
    for event in done.stdout.decode("utf-8", errors="replace").split("</Event>"):
        when = re.search(r"SystemTime='(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", event)
        if not when:
            continue
        top = re.search(r"<Process_1><Name>([^<]*)</Name><ID>(\d+)</ID>.*?<CommitCharge>(\d+)</CommitCharge>", event)
        local = datetime.fromisoformat(when.group(1)) + timedelta(hours=8)
        output.append({"time": local.strftime("%m/%d %H:%M"), "program": top.group(1) if top else "",
                       "pid": int(top.group(2)) if top else 0,
                       "gb": round(int(top.group(3)) / 2**30, 1) if top else 0.0})
    return output


IDLE_PRIORITY = 4          # Win32_Process.Priority of the idle class (below normal 6, normal 8)
# the pages, their supervisor, the worker (the 13:50 table, the after-close records) and the market-hours capture keep
# the normal priority: late, they would record the wrong moment (使用者 2026-10-29：不要為了省效能最後又給我錯的資料)
SERVED = ("quant-web.exe", "quant-api.exe", "quant-worker.exe", "run_local_services.ps1", "research live-flow",
          "-name liveflow")


def machine_processes() -> list[dict[str, object]]:
    """2026-10-29: every process with a command line — its PID, base priority and command line (Windows only, read with
    no window)."""
    import json
    import subprocess
    import sys

    if sys.platform != "win32":
        return []
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine } | "
              "Select-Object ProcessId, Priority, CommandLine | ConvertTo-Json -Compress")
    done = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True,
                          creationflags=0x08000000, timeout=120)                 # CREATE_NO_WINDOW
    if done.returncode != 0:
        raise RuntimeError(done.stderr.decode("utf-8", errors="replace")[:120] or f"powershell {done.returncode}")
    rows = json.loads(done.stdout.decode("utf-8", errors="replace") or "[]")
    return [{"pid": int(row["ProcessId"]), "priority": int(row.get("Priority") or 0), "command": row["CommandLine"]}
            for row in ([rows] if isinstance(rows, dict) else rows)]


def greedy(processes: list[dict[str, object]], root: Path) -> list[dict[str, object]]:
    """2026-10-29 (使用者：你他媽當我電腦資源很多是不是): this project's research and pipeline processes running above the
    idle class — the SERVED ones are left normal."""
    folder = str(root).lower()
    output = []
    for item in processes:
        command = str(item.get("command") or "").lower()
        if ((folder in command or "quant_platform" in command) and not any(mark in command for mark in SERVED)
                and int(item.get("priority") or 0) > IDLE_PRIORITY):
            output.append(item)
    return output


def _check(name: str, source: str, ok: bool, detail: str) -> dict[str, object]:
    return {"name": name, "source": source, "ok": bool(ok), "detail": detail}


def run(research: str | Path, today: date, backups: Path | None = None,
        windows: list[dict[str, object]] | None = None, site: str | None = None,
        memory: list[dict[str, object]] | None = None,
        processes: list[dict[str, object]] | None = None) -> list[dict[str, object]]:
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
        elif factor in ("dl1", "dl1x", "dl1m", "risk_half"):
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

            # 2026-10-28: each robot version by its number (ROLLING<nn>_VERSION) — RL 3.5 was missing from a fixed list
            # and its accounts were reported as not refitted although they were
            version = {"rl_blocks": rl_timing.BLOCK_VERSION, "rl_rolling": rl_timing.ROLLING_VERSION,
                       "rolling_equal": rl_timing.ROLLING_VERSION}.get(factor) or getattr(
                rl_timing, f"ROLLING{factor[-2:] if factor.startswith('rl_rolling') else factor[7:9]}_VERSION", None)
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
    checks.append(_check(f"{FIRST_T0:%m/%d} 以前沒有規則被標成 T0", f"路線圖判定準則：串接前向滿 {FORWARD_SESSIONS} 個交易日",
                         today >= FIRST_T0 or not early, f"被標成 T0 的 {len(early)} 個"))

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

    # 2026-10-25 (使用者：你所有策略確定都是合理的嗎): the latest check that every forward account traded as its rule says
    # (forward-audit: each account ranked again the way a backtest ranks it), at most 8 days old, no account differing
    checked = sorted((research / "forward" / "stocks").glob("audit-*.json")) if (research / "forward" / "stocks").is_dir() else []
    found = json.loads(checked[-1].read_text(encoding="utf-8")) if checked else {}
    when = datetime.fromisoformat(found["at"]).replace(tzinfo=None) if found.get("at") else None
    checks.append(_check("前向帳戶照規則交易（用回測引擎重算決策，8 天內）",
                         "使用者 2026-10-25：前向帳戶 9/7 到 10/5 都沒交易，策略確定合理嗎（看成本才換股從不換股的事件）",
                         bool(found) and not found.get("differing_accounts") and when is not None
                         and (datetime.now() - when).days <= 8,
                         (f"{when:%m-%d %H:%M}：{found.get('accounts', 0)} 個帳戶、{found.get('checked_days', 0):,} 個決策日，"
                          f"不一樣的帳戶 {found.get('differing_accounts', 0)} 個") if found else "還沒有核對紀錄"))

    # 2026-10-28 (research_method §7「重現性」: 222 registered results stale for a week): the latest sample of the pool's
    # results run again by the engine, at most 8 days old, none with other money
    from quant_platform.research import reproduce_check

    path, found = reproduce_check.latest(research)
    when = datetime.fromisoformat(found["at"]).replace(tzinfo=None) if found.get("at") else None
    checks.append(_check("選手池的結果重算得出來（抽樣，8 天內）",
                         "2026-10-28 事件：深度模型的檔案沒進輸入指紋，222 個登錄結果過期一週沒人發現",
                         bool(found) and not found.get("differing") and when is not None and (datetime.now() - when).days <= 8,
                         (f"{when:%m-%d %H:%M}：抽 {found.get('compared', 0)} 個，不一樣 {found.get('differing', 0)} 個")
                         if found else "還沒有重算紀錄"))

    if backups is not None:
        files = sorted(backups.glob("*"), key=lambda path: path.stat().st_mtime) if backups.is_dir() else []
        newest = datetime.fromtimestamp(files[-1].stat().st_mtime) if files else None
        checks.append(_check("資料庫每日備份", "AGENTS.md 資料庫安全：每日 03:00、保留 7 份",
                             newest is not None and (datetime.now() - newest).days < 2,
                             f"{len(files)} 份，最新 {newest:%m-%d %H:%M}" if newest else "沒有備份"))
    # 2026-10-22 (交接：需求檢查再加): the month's list, the news, the second copy on the other disk
    from quant_platform.research import rolling as rolling_lists

    try:
        month = rolling_lists.summary(rolling_lists.latest(research)) or {}
    except (KeyError, TypeError, ValueError):            # a list file without its cutoff is no list
        month = {}
    wanted = cutoff_for(today).isoformat()
    checks.append(_check("本月的每月名單存在", "AGENTS.md：前向觀察與挑策略一律每月滾動（使用者 2026-10-16／10-17）",
                         month.get("cutoff") == wanted, f"應為截止 {wanted}；最新名單截止 {month.get('cutoff') or '沒有'}"))
    news = research / "history" / "raw" / "finmind" / "TaiwanStockNews"
    days = sorted(path.name for path in news.iterdir() if path.is_dir() and path.name.isdigit()) if news.is_dir() else []
    sessions = _sessions(research)
    before = [day for day in sessions if day < today]
    due = before[-1].strftime("%Y%m%d") if before else None
    checks.append(_check("新聞收到前一個交易日", "需求 §8：每個交易日 14:40 收前一個交易日的新聞（新聞否決、LLM 交易員用）",
                         bool(due) and bool(days) and days[-1] >= due, f"應有 {due or '—'}；最新 {days[-1] if days else '沒有'}"))
    copies = [row for row in _jobs(research) if str(row.get("name", "")).startswith("研究資料第二份備份")]
    newest = max(copies, key=lambda row: str(row.get("started_at") or ""), default=None)
    fresh = bool(newest) and newest.get("status") == "done" and str(newest.get("started_at") or "") >= (
        today - timedelta(days=2)).isoformat()
    # 2026-10-24 (使用者：我從來就沒有說 D 槽要給你備份): only when a second disk is set (RESEARCH_MIRROR_DIR)
    import os

    if os.getenv("RESEARCH_MIRROR_DIR", "").strip():
        checks.append(_check("研究資料第二份備份（另一顆硬碟，2 天內）", "使用者 2026-10-19：評估專案可能失敗的原因並排除（每日 03:30）",
                             fresh, f"最近一次 {str(newest.get('started_at'))[:16] if newest else '沒有'}，{newest.get('status') if newest else '—'}"
                             + (f"：{newest.get('summary')}" if newest and newest.get("summary") else "")))
    # 2026-10-22 (使用者：我所有的失敗紀錄都要記起來，作為知識庫，讓後面的人可以用 AI 問): every finished experiment
    # has its result written (research method or development history), and the knowledge base holds every trial
    from quant_platform.research import knowledge
    from quant_platform.research.registry import TrialRegistry, newest

    root = research.parent.parent
    missing = knowledge.unrecorded(root, research)
    checks.append(_check("每個做完的研究都寫了結果（含失敗）", "使用者 2026-10-22：失敗紀錄都要記起來，作為知識庫", not missing,
                         f"{knowledge.RECORDED_FROM} 起做完的研究流程沒寫結果的 {len(missing)} 個" + (f"：{missing[:5]}" if missing else "")))
    try:
        built = knowledge.ensure(root, research)
        registered = len(newest(TrialRegistry(research / "trials.jsonl").records())) if (research / "trials.jsonl").is_file() else 0
        held = int((built.get("kinds") or {}).get("試驗", 0))
        checks.append(_check("研究知識庫收了每一個試驗", "使用者 2026-10-22：失敗紀錄都要記起來，作為知識庫", held == registered,
                             f"{built.get('entries', 0):,} 筆紀錄；試驗 {held:,}／{registered:,}（每個規則最新一次）"))
    except Exception as exc:                            # noqa: BLE001 - a broken build is itself the finding
        checks.append(_check("研究知識庫收了每一個試驗", "使用者 2026-10-22：失敗紀錄都要記起來，作為知識庫", False,
                             f"建立失敗：{str(exc)[:120]}"))
    # 2026-10-24 (10-23 03:27 the restart from the Start menu stopped half of the branch download and nothing noticed it
    # for 36 hours): every research pipeline is done, running, or resumed — none interrupted or dead while "running"
    from quant_platform.research.jobs import _alive

    stuck = []
    for path in sorted((research / "pipelines").glob("*.json")) if (research / "pipelines").is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        status = record.get("status")
        started = None
        try:
            started = datetime.fromisoformat(str(record.get("started_at")))
            if started.tzinfo is None:                     # the pipeline writes Taipei time without its offset
                from zoneinfo import ZoneInfo

                started = started.replace(tzinfo=ZoneInfo("Asia/Taipei"))
        except ValueError:
            pass
        if status in ("interrupted", "failed") or (status == "running" and not _alive(int(record.get("pid") or 0), started)):
            stuck.append(f"{record.get('name')}（{'中斷' if status == 'interrupted' else '失敗' if status == 'failed' else '程序已不在'}，{str(record.get('started_at'))[:16]} 開始）")
    checks.append(_check("研究流程沒有中斷了沒恢復的", "AGENTS.md 長時間程式：重開機或關機後要接著跑", not stuck,
                         "；".join(stuck[:4]) if stuck else "沒有"))
    # 2026-10-27 (使用者：現在是不是全部正確): the pool shows each rule's latest result — every 100%-stock one on the
    # current engine (#2189 stayed a T0 candidate from engine 1.2.0 while the list's 1.5.0 grading had it T3)
    from quant_platform.research.daily import ENGINE_VERSION, stale_engine

    stale = stale_engine(research) if (research / "trials.jsonl").is_file() else []
    checks.append(_check("選手池每個 100% 個股規則的最新結果都是現行引擎", "AGENTS.md：前向帳戶和回測一定是同一個策略（使用者 2026-10-25）",
                         not stale, f"現行 {ENGINE_VERSION}；舊引擎的 {len(stale)} 個"
                         + (f"：{[rule.name[:30] for rule in stale[:3]]}" if stale else "")))
    # 2026-10-27 (使用者 10/26：不要再用 2015～2020 作為篩選；短歷史賽道加重; 10/27：問我幹嘛): every grade is on the
    # 2020-10 track — the latest results and the month's rows all have their windows from 2020-11
    from quant_platform.research.windows2020 import missing as windows_missing

    lacking = windows_missing(research, cutoff_for(today))
    checks.append(_check("每個規則都用 2020-10 以後的視窗分級", "使用者 2026-10-26／10-27：不要再用 2015～2020 作為篩選、短歷史賽道加重",
                         not (lacking["registry"] or lacking["list"]),
                         f"選手池缺的 {lacking['registry']} 個、本月名單缺的 {lacking['list']} 個"))
    # 2026-10-27 (使用者：再加一個議題追蹤 — AI 科技巨頭支出的風險、台灣房市跟股市；首頁做初步警示): the lights are
    # rebuilt daily at 18:20; a page more than two days old is reported
    from quant_platform.research.issues import read as issues_read

    lights = issues_read(research)
    built = date.fromisoformat(lights["built_at"][:10]) if lights else None
    checks.append(_check("議題追蹤兩天內更新過", "使用者 2026-10-27：議題追蹤與首頁的初步警示",
                         built is not None and (today - built).days <= 2,
                         f"{lights['built_at'][:16]} 更新：告警 {lights['counts']['alert']}、警示 {lights['counts']['watch']}"
                         if lights else "還沒有議題資料"))
    # 2026-10-28 (使用者：每個議題弄成一個專案去追蹤): every project has its written reading, rewritten within 7 days of a
    # light changing
    from quant_platform.research.issues import changes as issue_changes
    from quant_platform.research.issues import note as issue_note

    missing, old = [], []
    logged = issue_changes(research, limit=100000)
    for topic in (lights or {}).get("topics") or []:
        found = issue_note(topic)
        if not found:
            missing.append(topic["title"])
            continue
        moved = {item["id"] for item in topic["indicators"]
                 if found["levels"].get(item["id"]) not in (None, item["level"])}
        since = [row["at"][:10] for row in logged if row["id"] in moved and row["at"][:10] >= found["written"]]
        if since and (today - date.fromisoformat(min(since))).days > 7:
            old.append(topic["title"])
    checks.append(_check("每個議題專案都有判讀，燈號變了 7 天內重寫", "使用者 2026-10-28：每個議題弄成一個專案去追蹤",
                         bool(lights) and not missing and not old,
                         ("沒有判讀：" + "、".join(missing) if missing else "") + ("；判讀過期：" + "、".join(old) if old else "")
                         or f"{len((lights or {}).get('topics') or [])} 個專案都有判讀"))

    # 2026-10-27 (使用者：深度學習、RL、多因子、趨勢動能這些類別你要把點標註出來，我就是要知道這些類別的分佈在哪一個位置)
    from quant_platform.research.distribution import unmarked

    try:
        bare = unmarked(research)
        checks.append(_check("研究總覽的分佈圖標出每一類策略", "使用者 2026-10-27：每一類策略落在分佈圖的哪裡要標出來", not bare,
                             "；".join(bare[:4]) if bare else "每一類都有一列"))
    except Exception as exc:                            # noqa: BLE001 - not able to look is reported, not hidden
        checks.append(_check("研究總覽的分佈圖標出每一類策略", "使用者 2026-10-27：每一類策略落在分佈圖的哪裡要標出來", False,
                             f"沒辦法檢查：{str(exc)[:120]}"))
    # 2026-10-28 (使用者：研究的載入也太慢了吧；每個策略給個編號；每一筆交易都要顯示買入的價格跟賣出的價格): the worker
    # passes the local site; the pages are opened as the owner would
    if site:
        live_hashes = [item["rule_hash"] for item in live if item.get("kind") == "daily"]
        slow = research_pages(site, live_hashes[0] if live_hashes else None)
        checks.append(_check("研究頁 5 秒內打開、規則有編號、每一筆交易有買進價與賣出價",
                             "使用者 2026-10-28：研究的載入也太慢了吧；每個策略給個編號；每一筆交易都要顯示買入的價格跟賣出的價格",
                             not slow, "；".join(slow) if slow else "研究總覽、規則頁、前向頁都在 5 秒內"))
    # 2026-10-28 (使用者：專案資訊有把所有的交易事項需知、開發需知、規則寫清楚嗎)
    found = trading_page(research.parent.parent)
    checks.append(_check("專案資訊有交易需知與開發規則、交易需知寫的引擎版本和程式一致",
                         "使用者 2026-10-28：專案資訊有把所有的交易事項需知、開發需知、規則寫清楚嗎", not found,
                         "；".join(found) if found else "是"))
    # 2026-10-26 (使用者：我不要再放在這個皮咪資料夾下；向量資料庫吃 C:\\Dev\\DBQdrant)
    place = placement(research.parent.parent, research)
    checks.append(_check("專案在 C:\\Dev、知識庫向量在共用的 DBQdrant", "使用者 2026-10-26：比照其他專案搬到 C:\\Dev",
                         not place, "；".join(place) if place else "是"))
    # 2026-10-23 (AGENTS.md：背景程式不開視窗; 使用者 2026-10-20 關掉一堆沒說明的空白 cmd 視窗，研究跟著中斷)
    try:
        opened = own_windows(desktop_windows() if windows is None else windows, research.parent.parent)
        checks.append(_check("桌面沒有本專案開出的視窗", "AGENTS.md：背景程式不開視窗（使用者 2026-10-20）", not opened,
                             (f"{len(opened)} 個：" + "；".join(f"{item['title'] or item['class']}（PID {item['pid']}）"
                                                             for item in opened[:3])) if opened else "沒有"))
    except Exception as exc:                            # noqa: BLE001 - not able to look is reported, not hidden
        checks.append(_check("桌面沒有本專案開出的視窗", "AGENTS.md：背景程式不開視窗（使用者 2026-10-20）", False,
                             f"沒辦法檢查：{str(exc)[:120]}"))
    # 2026-10-29: on 10-28 the machine ran out of memory seven times (a deep training and the forward accounts' replays
    # beside the engine); a forward, two boards, a training and the full search died, other programs with them
    name, source = "過去 24 小時記憶體沒有用完", "開發歷程 2026-10-29：記憶體用完七次，研究與其他程式被終止"
    try:
        events = memory_events() if memory is None else memory
        checks.append(_check(name, source, not events, (f"{len(events)} 次：" + "；".join(
            f"{item['time']} 最大的是 {item['program']}（PID {item['pid']}，{item['gb']} GB）" for item in events[:3]))
            if events else "沒有"))
    except Exception as exc:                            # noqa: BLE001 - not able to look is reported, not hidden
        checks.append(_check(name, source, False, f"沒辦法檢查：{str(exc)[:120]}"))
    # 2026-10-29 (使用者：你他媽當我電腦資源很多是不是): the research took the whole CPU and another project's UE build ran
    # 9% in 8 hours; research, pipelines and the worker run at the idle priority so other work goes first
    checks.append(priority_check(processes, research.parent.parent))
    return checks


def priority_check(processes: list[dict[str, object]] | None, root: Path) -> dict[str, object]:
    """2026-10-29 (使用者：你他媽當我電腦資源很多是不是): the research and pipeline processes at the idle priority."""
    name, source = "本專案的研究程式都用最低優先權", "使用者 2026-10-29：研究程式不能搶這台電腦其他工作的 CPU"
    try:
        found = greedy(machine_processes() if processes is None else processes, root)
        return _check(name, source, not found, (f"{len(found)} 個不是最低：" + "；".join(
            f"PID {item['pid']}（{str(item['command'])[-60:]}）" for item in found[:3])) if found else "是")
    except Exception as exc:                            # noqa: BLE001 - not able to look is reported, not hidden
        return _check(name, source, False, f"沒辦法檢查：{str(exc)[:120]}")


PAGE_LIMIT = 5.0           # seconds a research page may take (使用者 2026-10-28：研究的載入也太慢了吧)


def research_pages(site: str, rule_hash: str | None, limit: float = PAGE_LIMIT) -> list[str]:
    """What is wrong with the research pages as the owner opens them: slower than ``limit``, not opening, a strategy's
    page without its number, a forward page without the buy and sell prices."""
    import time
    import urllib.request

    pages = [("研究總覽", "/research", ())]
    if rule_hash:
        pages += [("規則頁", f"/research/rules/{rule_hash}", ('class="rule-no"',)),
                  ("前向頁", f"/research/forward/{rule_hash}", ('class="rule-no"', "買進價", "賣出價"))]
    wrong = []
    for name, path, needed in pages:
        started = time.monotonic()
        try:
            with urllib.request.urlopen(site.rstrip("/") + path, timeout=60) as response:
                body = response.read().decode("utf-8", "replace")
        except Exception as exc:                        # noqa: BLE001 - a page that does not open is the finding
            wrong.append(f"{name}打不開：{str(exc)[:80]}")
            continue
        spent = time.monotonic() - started
        if spent > limit:
            wrong.append(f"{name} {spent:.1f} 秒")
        wrong += [f"{name}沒有「{text}」" for text in needed if text not in body]
    return wrong


def trading_page(root: Path) -> list[str]:
    """What is wrong with the trading rules' page (docs/trading_rules.md) and the 專案資訊 list: missing, naming another
    engine than ``daily.ENGINE_VERSION``, or the page or AGENTS.md left off the list."""
    from quant_platform.research.daily import ENGINE_VERSION

    wrong = []
    page = Path(root) / "docs" / "trading_rules.md"
    if not page.is_file():
        wrong.append("沒有 docs/trading_rules.md")
    elif f"**{ENGINE_VERSION}**" not in page.read_text(encoding="utf-8"):
        wrong.append(f"交易需知寫的引擎版本不是程式的 {ENGINE_VERSION}")
    if not (Path(root) / "AGENTS.md").is_file():
        wrong.append("沒有 AGENTS.md")
    try:
        from quant_platform.dashboard.v2 import DOCS, PROJECT_DOCS

        wrong += [f"專案資訊沒有「{label}」" for key, label in (("trading", "交易需知"), ("agents", "開發規則"))
                  if key not in PROJECT_DOCS or key not in DOCS]
    except Exception as exc:                            # noqa: BLE001 - not able to look is reported, not hidden
        wrong.append(f"沒辦法讀專案資訊的文件清單：{str(exc)[:80]}")
    return wrong


def placement(root: Path, research: Path, home: str | None = None) -> list[str]:
    """What is not where the owner put it on 2026-10-26: the project under the user folder, or the knowledge base's
    vectors outside the shared Qdrant server (C:\\Dev\\DBQdrant)."""
    from quant_platform.research import knowledge_vectors

    home = home if home is not None else os.environ.get("USERPROFILE", "")
    found = []
    if home and str(Path(root).resolve()).lower().startswith(str(Path(home).resolve()).lower() + os.sep):
        found.append(f"專案還在使用者資料夾（{root}）")
    status = knowledge_vectors.status(research)
    if status and not status.get("server"):
        found.append(f"知識庫向量還在專案內嵌的 Qdrant（{status.get('collection')}）")
    return found


def _sessions(research: Path) -> list[date]:
    """The market's sessions from the stored index (history/daily/TAIEX.parquet); [] without it."""
    path = research / "history" / "daily" / "TAIEX.parquet"
    if not path.is_file():
        return []
    import pyarrow.parquet as pq

    return sorted(set(pq.read_table(path, columns=["date"])["date"].to_pylist()))


def _jobs(research: Path) -> list[dict[str, object]]:
    rows = []
    for path in (research / "jobs").glob("*.json") if (research / "jobs").is_dir() else []:
        try:
            rows.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return rows


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
