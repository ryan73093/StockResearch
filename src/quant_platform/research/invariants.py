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
    console window whose title names the project, its .venv or its research scripts. 2026-10-30: the folder is the full
    path — the 16:00 check passed "." (a relative research folder), which is in every title and command line."""
    folder = str(Path(root).resolve()).lower()
    output = []
    for window in windows:
        path, title = str(window.get("path") or "").lower(), str(window.get("title") or "").lower()
        if path.startswith(folder) or (window.get("class") in CONSOLES
                                       and (folder in title or any(mark in title for mark in OWN_MARKS))):
            output.append(window)
    return output


def notification_channel() -> str | None:
    """Where a failure notification would go out, by the settings: "line", "email", or None (2026-11-12; the same rule
    as ``NotificationService.failure_channel`` — a mail host alone is not a channel)."""
    from quant_platform.config import settings as settings_module

    settings = settings_module.get_settings()
    if settings.line_enabled and settings.line_channel_access_token and settings.line_to:
        return "line"
    if settings.email_enabled and settings.smtp_host and settings.smtp_from and settings.report_email_to:
        return "email"
    return None


def memory_errors(research: str | Path, hours: int = 24, now: datetime | None = None) -> list[dict[str, str]]:
    """2026-11-12 (the memory answer of 11-11): the research steps and worker jobs whose error log of the last ``hours``
    says they ran out of memory. Windows' own event (``memory_events``) is written only when the whole machine runs out;
    seven steps died of MemoryError or CUDA out of memory between 10-20 and 11-03 on days it stayed silent."""
    import re

    research = Path(research)
    since = (now or datetime.now()).timestamp() - hours * 3600
    pattern = re.compile(r"CUDA out of memory|Unable to allocate|ArrayMemoryError|WinError 1455|(?<![A-Za-z_])MemoryError")
    paths = list((research / "logs").glob("*.err.log")) if (research / "logs").is_dir() else []
    paths.append(research.parent / "worker-jobs.log")
    found = []
    for path in sorted(paths):
        try:
            if path.stat().st_mtime < since:
                continue
            with path.open("rb") as handle:                 # the end is where a step dies
                handle.seek(max(0, path.stat().st_size - (1 << 20)))
                text = handle.read().decode("utf-8", errors="replace")
        except OSError:
            continue
        match = pattern.search(text)
        if match:
            found.append({"log": path.name, "what": match.group(0)})
    return found


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
          "-name liveflow",
          # 2026-10-31: the pages' background round (dashboard/warm.py, every 5 minutes) is the website's own work
          "quant_platform.dashboard.warm")


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
    idle class — the SERVED ones are left normal. 2026-10-30: a research process is one of the project's own programs
    (its .venv's Python, a quant_platform module, the pipeline scripts), the folder as a full path — the folder anywhere
    in the command line also caught the assistant's shells and the project's Tunnel (its credentials are in the
    folder); the tests (pytest) are development, not research."""
    folder = str(Path(root).resolve()).lower()
    own = (str(Path(folder) / ".venv").lower(), "quant_platform", "research-pipeline.ps1", "start-research.ps1")
    output = []
    for item in processes:
        command = str(item.get("command") or "").lower()
        if (any(mark in command for mark in own) and not any(mark in command for mark in SERVED)
                and "-m pytest" not in command and int(item.get("priority") or 0) > IDLE_PRIORITY):
            output.append(item)
    return output


def _check(name: str, source: str, ok: bool, detail: str) -> dict[str, object]:
    return {"name": name, "source": source, "ok": bool(ok), "detail": detail}


def run(research: str | Path, today: date, backups: Path | None = None,
        windows: list[dict[str, object]] | None = None, site: str | None = None,
        memory: list[dict[str, object]] | None = None,
        processes: list[dict[str, object]] | None = None, now: datetime | None = None) -> list[dict[str, object]]:
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
    checks.append(_check("前向帳戶沒有 0050", "D-003：策略一律 100% 個股", not held_0050,
                         f"{len(live)} 個帳戶；含 0050 的 {len(held_0050)} 個" + (f"：{held_0050[:3]}" if held_0050 else "")))

    start = common_start(today).isoformat()
    # 2026-10-21 (使用者：不要跟我說前向觀察只能做一天): no exception — the LLM trader's pair too
    wrong_start = [item["name"] for item in live if item.get("since") != start]
    checks.append(_check("前向帳戶從當月截止日隔天起算", "D-008：前向觀察與挑策略一律每月滾動",
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
    checks.append(_check("前向帳戶用到的模型都重訓到當月截止日", "D-008：只用截止日以前的資料挑策略、重訓模型（使用者 2026-10-16）",
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
    # 2026-11-03 (使用者：已經講幾次叫你要檢查、叫你要確認，就是不做): this check passed on any record at all — 11-02's
    # records were never written and it said 通過 (最新紀錄 2026-10-30). The session due comes from the exchange's
    # calendar, not from the research data (which had stopped too); the records and the closes must reach it
    latest = max((record["date"] for record in records), default=None)
    due = due_session(research, today, now)
    checks.append(_check("前向紀錄記到最近的交易日", "需求 §8：每個交易日 15:30 記錄",
                         latest is not None and latest >= due.isoformat(), f"應記到 {due}；最新紀錄 {latest or '沒有'}"))
    closes = _sessions(research)
    checks.append(_check("研究資料的收盤價更新到最近的交易日", "需求 §8：每個交易日 15:15 補抓當天收盤",
                         bool(closes) and closes[-1] >= due, f"應更新到 {due}；最新 {closes[-1] if closes else '沒有'}"))

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
    # 2026-10-29 (使用者：我拿到的才叫賺，拿到的才叫賠；未實現損益你也算賺錢哦): the board ranks by the result counted
    try:
        board_rows = json.loads((research / "forward" / "stocks" / "board.json").read_text(encoding="utf-8")).get("rows") or []
    except (OSError, ValueError):
        board_rows = []
    ranked = sorted((row for row in board_rows if row.get("rank")), key=lambda row: row["rank"])
    keys = [(row.get("kept") is None, -(row.get("kept") or {}).get("gain", 0.0)) for row in ranked]
    unranked = sum(1 for row in ranked if row.get("kept") is None)
    checks.append(_check("前向看板依實際結果排名（賺的只算賣掉的、賠的都算）", "使用者 2026-10-29：我拿到的才叫賺，拿到的才叫賠",
                         bool(ranked) and keys == sorted(keys) and unranked < len(ranked),
                         f"{len(ranked)} 個帳戶，{unranked} 個還沒有紀錄" if ranked else "看板沒有排名"))

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
                          f"不一樣的帳戶 {found.get('differing_accounts', 0)} 個" + _audit_split(found)) if found else "還沒有核對紀錄"))

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
        checks.append(_check("資料庫每日備份", "docs/rules/data.md 資料庫安全：每日 03:00、保留 7 份",
                             newest is not None and (datetime.now() - newest).days < 2,
                             f"{len(files)} 份，最新 {newest:%m-%d %H:%M}" if newest else "沒有備份"))
    # 2026-10-22 (交接：需求檢查再加): the month's list, the news, the second copy on the other disk
    from quant_platform.research import rolling as rolling_lists

    try:
        month = rolling_lists.summary(rolling_lists.latest(research)) or {}
    except (KeyError, TypeError, ValueError):            # a list file without its cutoff is no list
        month = {}
    wanted = cutoff_for(today).isoformat()
    checks.append(_check("本月的每月名單存在", "D-008：前向觀察與挑策略一律每月滾動（使用者 2026-10-16／10-17）",
                         month.get("cutoff") == wanted, f"應為截止 {wanted}；最新名單截止 {month.get('cutoff') or '沒有'}"))
    news = research / "history" / "raw" / "finmind" / "TaiwanStockNews"
    days = sorted(path.name for path in news.iterdir() if path.is_dir() and path.name.isdigit()) if news.is_dir() else []
    due = collected_due(research, today, now, (14, 45)).strftime("%Y%m%d")      # 2026-11-03: the exchange's calendar
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
    checks.append(_check("研究流程沒有中斷了沒恢復的", "D-032 長時間程式：重開機或關機後要接著跑", not stuck,
                         "；".join(stuck[:4]) if stuck else "沒有"))
    # 2026-11-03: regate221 waited from 10-30 09:18 for 40 GB of memory free and nothing said so for three and a half
    # days — a flow waiting more than a day is shown with what it waits for
    waiting = long_waits(research, now)
    checks.append(_check("研究流程沒有等超過一天的", "開發歷程 2026-11-03：regate221 等記憶體 3 天半沒人發現", not waiting,
                         "；".join(waiting[:4]) if waiting else "沒有"))
    # 2026-10-27 (使用者：現在是不是全部正確): the pool shows each rule's latest result — every 100%-stock one on the
    # current engine (#2189 stayed a T0 candidate from engine 1.2.0 while the list's 1.5.0 grading had it T3)
    from quant_platform.research.daily import ENGINE_VERSION, stale_engine

    stale = stale_engine(research) if (research / "trials.jsonl").is_file() else []
    checks.append(_check("選手池每個 100% 個股規則的最新結果都是現行引擎", "D-017：前向帳戶和回測一定是同一個策略（使用者 2026-10-25）",
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
    # 2026-11-13 (problem register P7): the weekly watch of the factors and models the forward accounts read ran
    from quant_platform.research import decay as decay_watch

    watch = decay_watch.latest(research)
    watched = date.fromisoformat(str(watch["at"])[:10]) if watch else None
    weak = decay_watch.flagged(watch)
    checks.append(_check("因子與模型衰減監控 8 天內算過", "docs/problem_register_2026-11-11.md P7：沒有衰退監控",
                         watched is not None and (today - watched).days <= 8,
                         f"{str(watch['at'])[:16]} 算：{len(watch.get('factors') or {})} 個因子與模型，變弱或反過來了 {len(weak)} 個"
                         if watch else "還沒算過（每週六 07:30）"))
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
    # 2026-11-03: the page check opened the first account — the LLM trader's news experiment, which has no number and no
    # rule page (404), so the check failed every day on that and said nothing of the numbered rules' pages. The pages
    # of a numbered rule are opened; the accounts without a number are their own check
    from quant_platform.research.numbers import experiments as forward_experiments
    from quant_platform.research.numbers import numbers as strategy_numbers

    numbered = strategy_numbers(research)
    experiment_numbers = forward_experiments()        # 2026-11-08: E1, E2, …; they have no rule page to open below
    unnumbered = [item["name"] for item in live if item["rule_hash"] not in numbered and item["rule_hash"] not in experiment_numbers]
    checks.append(_check("每個前向帳戶都有策略編號", "使用者 2026-10-28：麻煩每個策略給個編號", not unnumbered,
                         f"沒有編號的 {len(unnumbered)} 個" + (f"：{unnumbered[:4]}" if unnumbered else "")))
    if site:
        live_hashes = [item["rule_hash"] for item in live if item.get("kind") == "daily" and item["rule_hash"] in numbered]
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
        checks.append(_check("桌面沒有本專案開出的視窗", "D-013：背景程式不開視窗（使用者 2026-10-20）", not opened,
                             (f"{len(opened)} 個：" + "；".join(f"{item['title'] or item['class']}（PID {item['pid']}）"
                                                             for item in opened[:3])) if opened else "沒有"))
    except Exception as exc:                            # noqa: BLE001 - not able to look is reported, not hidden
        checks.append(_check("桌面沒有本專案開出的視窗", "D-013：背景程式不開視窗（使用者 2026-10-20）", False,
                             f"沒辦法檢查：{str(exc)[:120]}"))
    # 2026-10-30: at 05:41 the disk was full for a while and the full search stopped half written; room on C: for the
    # data, the backups and the searches
    import shutil

    free = shutil.disk_usage(research.anchor or "C:\\").free / 2**30
    checks.append(_check("C 槽至少 50 GB 可用", "開發歷程 2026-10-30：05:41 磁碟寫滿，全段大搜尋 b 段中斷", free >= 50,
                         f"{free:,.0f} GB 可用"))
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
    # 2026-11-12: the check above was green on the days seven steps died of MemoryError or CUDA out of memory
    died = memory_errors(research, now=now.replace(tzinfo=None) if now is not None and now.tzinfo else now)
    checks.append(_check("過去 24 小時沒有步驟因為記憶體不足而失敗", "docs/problem_register_2026-11-11.md P5：7 個步驟記憶體錯誤，檢查卻是綠的",
                         not died, (f"{len(died)} 個：" + "；".join(f"{item['log']}（{item['what']}）" for item in died[:3]))
                         if died else "沒有"))
    # 2026-11-12: the worker's jobs that ended on an error (scheduler/failures.py)
    from quant_platform.scheduler import failures as job_failures

    failed_jobs = job_failures.recent(research.parent, now=now)
    checks.append(_check("過去 24 小時排程工作沒有失敗", "docs/problem_register_2026-11-11.md P6：模型重訓連續九晚失敗沒有人知道",
                         not failed_jobs, (f"{len(failed_jobs)} 個：" + "；".join(
                             f"{item['name']}（{item['at'][5:10].replace('-', '/')} {item['at'][11:16]}）：{item['error'][:80]}"
                             + (f"（{item['times']} 次）" if int(item["times"]) > 1 else "") for item in failed_jobs[:3]))
                         if failed_jobs else "沒有"))
    # 2026-11-12: the pool's end is a constant moved by hand each month (daily.RECENT_END)
    from quant_platform.research import daily as daily_module

    wanted_end = cutoff_for(today)
    checks.append(_check("選手池的回測終點是當月的截止日", "docs/system_review_2026-11-10.md 根本原因 3：RECENT_END 寫死",
                         daily_module.RECENT_END == wanted_end,
                         wanted_end.isoformat() if daily_module.RECENT_END == wanted_end else
                         f"回測終點 {daily_module.RECENT_END}，當月截止日 {wanted_end}：改 daily.RECENT_END 並部署（選手池會跑新的一個月）"))
    # 2026-11-12: with no channel every failure notification ended as "disabled" — red until LINE or the mail is set
    try:
        channel = notification_channel()
        checks.append(_check("失敗通知有管道可以送出", "docs/problem_register_2026-11-11.md P6：失敗通知沒有管道",
                             channel is not None, {"line": "LINE", "email": "信箱"}.get(channel or "",
                             "LINE 與信箱都沒有設定：.env 要填 EMAIL_ENABLED、SMTP_HOST、SMTP_FROM、REPORT_EMAIL_TO（與帳號密碼），或 LINE 的三項")))
    except Exception as exc:                            # noqa: BLE001 - not able to look is reported, not hidden
        checks.append(_check("失敗通知有管道可以送出", "docs/problem_register_2026-11-11.md P6", False, f"沒辦法檢查：{str(exc)[:120]}"))
    # 2026-11-02: the paid tables were downloaded once and stopped on 10-21/22; 147 forward accounts read them (directly
    # or through models J-P) and decided on "no block trade, no branch buying" for eight sessions before anyone saw it
    checks.append(paid_check(research, today, now))
    # 2026-10-29 (使用者：你他媽當我電腦資源很多是不是): the research took the whole CPU and another project's UE build ran
    # 9% in 8 hours; research, pipelines and the worker run at the idle priority so other work goes first
    checks.append(priority_check(processes, research.parent.parent))
    # 2026-11-11 (使用者：D 要能確保後續文件開發後也要能維持文件的穩定、ai 要能讀對資料、省 token; D-028)
    checks.append(docs_check(research.parent.parent))
    return checks


DOCS_CHECK = "文件守門：字數上限、交接檔三欄、定義只一處、決定編號、提到的檔案、路線圖與架構沒落後"


def docs_check(root: Path) -> dict[str, object]:
    """The documents' guard (``quant_platform.docs_guard``, the same one ``tests/test_docs_guard.py`` runs) on the
    project's files: AGENTS.md and the rules files within their sizes, the handoff with its three fields only, T0,
    截止日 and 可以上線 defined once, every decision number there and cited, every named file there, the roadmap and
    the architecture not far behind the code."""
    from quant_platform import docs_guard

    source = "使用者 2026-11-11：D 要能確保後續文件開發後也要能維持文件的穩定、ai 要能讀對資料、省 token（D-028）"
    try:
        found = docs_guard.problems(root)
        return _check(DOCS_CHECK, source, not found, "；".join(found[:6]) + (f"（共 {len(found)} 項）" if len(found) > 6 else "")
                      if found else "全部符合")
    except Exception as exc:                            # noqa: BLE001 - not able to look is reported, not hidden
        return _check(DOCS_CHECK, source, False, f"沒辦法檢查：{str(exc)[:120]}")


def paid_check(research: Path, today: date, now: datetime | None = None) -> dict[str, object]:
    """While the FinMind plan (plan.json) is paid and not past its end, every paid table covers the previous session
    (paid/through.json) — what today's 13:50 orders read (paiddaily runs at 22:05 and 07:05)."""
    import json

    from quant_platform.research.chips import PAID_SOURCES

    name, source = "付費資料每天更新", "開發歷程 2026-11-02：付費資料 10/21 起沒更新，147 個前向帳戶用假資料決定"
    history = research / "history"
    try:
        plan = json.loads((history / "raw" / "finmind" / "plan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        plan = {}
    if not plan.get("limit") or int(plan["limit"]) <= 600 or str(plan.get("expires") or "") < today.isoformat():
        return _check(name, source, True, "付費方案沒有在用（或已到期），不檢查")
    due = collected_due(research, today, now, (7, 30)).isoformat()             # 2026-11-03: the exchange's calendar
    try:
        covered = json.loads((history / "paid" / "through.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        covered = {}
    late = {table: covered.get(table, "沒有") for table in sorted(set(PAID_SOURCES.values())) if covered.get(table, "") < due}
    return _check(name, source, not late, f"應涵蓋到 {due}" + ("；落後：" + "、".join(f"{table} {day}" for table, day in late.items())
                                                            if late else "，全部涵蓋"))


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


def _calendar(research: Path):
    """The exchange's calendar (market_calendar: the packaged schedule, the official fetches, the typhoon days) — not
    the research data's own dates, which stop when the data stops (2026-11-03)."""
    from quant_platform.market_calendar.store import MarketCalendarStore

    return MarketCalendarStore(research.parent).calendar("TW")


def due_session(research: Path, today: date, now: datetime | None = None) -> date:
    """The latest session whose closes and forward records are due: today's from 15:45 (the 15:15 and 15:30 jobs), else
    the one before."""
    from zoneinfo import ZoneInfo

    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    after = today < now.date() or (now.hour, now.minute) >= (15, 45)
    return _calendar(research).previous_trading_day(today, inclusive=after)


def long_waits(research: Path, now: datetime | None = None) -> list[str]:
    """The flows waiting more than a day, with what they wait for (a wait's record has the status "waiting"; 2026-11-08:
    this looked for "running", and passed while regate221b waited four days for the GPU)."""
    from quant_platform.research import jobs

    found = []
    for row in _jobs(research):
        if not str(row.get("id", "")).endswith("-wait") or row.get("status") != "waiting":
            continue
        try:
            began = datetime.fromisoformat(str(row.get("started_at")))
        except ValueError:
            continue
        hours = ((now or datetime.now(began.tzinfo)) - began).total_seconds() / 3600
        if hours > 24 and jobs._alive(int(row.get("pid") or 0), began):
            found.append(f"{str(row.get('command', '')).replace('research-pipeline.ps1 -Name ', '')} 等了 {hours:.0f} 小時：{row.get('current')}")
    return found


def _audit_split(found: dict) -> str:
    """2026-11-03: of the accounts the forward audit found differing, those whose first different day the after-close
    orders decided at 13:50 on numbers the close later changed (the audit's ``table``: 31 of 35 on 11-03) apart from
    those with no such reason — the ones to look into."""
    rows = [row for row in found.get("rows") or [] if row.get("differed")]
    if not rows:
        return ""
    unexplained = [row for row in rows if not (row.get("first") or {}).get("table")]
    return (f"：13:50 表和收盤資料不同 {len(rows) - len(unexplained)} 個、原因不明 {len(unexplained)} 個"
            + (f"（{'、'.join(str((row.get('first') or {}).get('day')) for row in unexplained[:4])}）" if unexplained else ""))


def collected_due(research: Path, today: date, now: datetime | None, runs: tuple[int, int]) -> date:
    """The session a job collecting the previous session's data must have reached: the news (14:40) and the paid
    tables (07:05) collect, each day, the session before it. Before that hour today's run has not come yet — the
    session before yesterday's is due. At the 16:00 check both are the session before today."""
    from zoneinfo import ZoneInfo

    now = now or datetime.now(ZoneInfo("Asia/Taipei"))
    ran = today if (today < now.date() or (now.hour, now.minute) >= runs) else today - timedelta(days=1)
    calendar = _calendar(research)                       # 2026-11-09: the last session it ran on (a weekend has none)
    return calendar.previous_trading_day(calendar.previous_trading_day(ran), inclusive=False)


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


HISTORY = "invariants-history.jsonl"


def _red_days(research: Path, checks: list[dict[str, object]], now: datetime) -> None:
    """2026-11-12 (problem register P6): how many days in a row each red check has been red, written on the check
    (``red_days``, ``red_since``) — the days are the days a run was kept (``HISTORY``, one line a run, the day's last
    run counting), today's included. Every run replaced the last one's file and nothing said a check had been red for
    a week."""
    days: dict[str, set[str]] = {}
    path = research / HISTORY
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                days[str(row["at"])[:10]] = set(row["red"])
            except (ValueError, KeyError, TypeError):
                continue
    today = now.date().isoformat()
    days[today] = {str(item["name"]) for item in checks if not item["ok"]}
    earlier = sorted((day for day in days if day < today), reverse=True)
    for item in checks:
        if item["ok"]:
            continue
        since = today
        for day in earlier:
            if item["name"] not in days[day]:
                break
            since = day
        item["red_days"] = 1 + sum(1 for day in earlier if day >= since)
        item["red_since"] = since
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": now.isoformat(timespec="seconds"), "red": sorted(days[today])}, ensure_ascii=False) + chr(10))


def save(research: str | Path, checks: list[dict[str, object]], now: datetime) -> Path:
    Path(research).mkdir(parents=True, exist_ok=True)
    _red_days(Path(research), checks, now)
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
                      + (f"・{'；'.join(item['name'] + _since(item) for item in failed[:2])}" if failed else "")}


def _since(item: dict[str, object]) -> str:
    return f"（連續 {item['red_days']} 天）" if int(item.get("red_days") or 0) > 1 else ""
