"""2026-10-20: the owner's decisions checked as code (research/invariants.py)."""
import json
from datetime import date
from pathlib import Path

from quant_platform.research import invariants
from quant_platform.research.daily import DailyRule


def test_an_account_whose_rule_differs_from_the_months_list_is_caught(tmp_path):
    graded = DailyRule(name="a", factors={"trend_200": 1.0})
    converted = DailyRule(name="a、月線上續抱", factors={"trend_200": 1.0}, hold="trend")
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    accounts = [{"rule_hash": rule.rule_hash, "name": rule.name, "rule": rule.canonical(), "kind": "daily",
                 "since": "2026-09-01", "initial": 300_000, "monthly": 10_000} for rule in (graded, converted)]
    (folder / "tracked.json").write_text(json.dumps(accounts, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "rolling").mkdir()
    (tmp_path / "rolling" / "2026-08-31.json").write_text(json.dumps({"graded": [
        {"rule_hash": graded.rule_hash, "passed": True}]}), encoding="utf-8")
    checks = {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 20))}
    same = checks["前向帳戶的規則和每月名單上評過分的一模一樣（平台不轉換任何設定）"]
    assert not same["ok"] and "a、月線上續抱" in same["detail"]
    assert checks["前向帳戶沒有 0050"]["ok"] and checks["前向帳戶從當月截止日隔天起算"]["ok"]
    assert checks["前向帳戶現金流是 30 萬＋每月 1 萬"]["ok"]
    accounts[0]["since"] = "2026-10-01"
    (folder / "tracked.json").write_text(json.dumps(accounts, ensure_ascii=False), encoding="utf-8")
    assert not {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 20))}["前向帳戶從當月截止日隔天起算"]["ok"]
    tile = invariants.tile({"at": "2026-10-20T16:00:00", "checks": list(checks.values())})
    assert tile["badge"] == "badge--bad" and "不符" in tile["state"]


def test_an_account_paid_a_month_it_started_after_is_caught(tmp_path):
    from datetime import timedelta

    import pyarrow as pa
    import pyarrow.parquet as pq

    rule = DailyRule(name="LLM", factors={"llm_trader": 1.0})
    folder = tmp_path / "forward" / "stocks"
    folder.mkdir(parents=True)
    (folder / "tracked.json").write_text(json.dumps([{"rule_hash": rule.rule_hash, "name": rule.name, "kind": "daily",
                                                      "rule": rule.canonical(), "since": "2026-10-20",
                                                      "initial": 300_000, "monthly": 10_000}]), encoding="utf-8")
    calendar = [date(2026, 9, 1) + timedelta(days) for days in range(50)]
    (tmp_path / "history" / "daily").mkdir(parents=True)
    pq.write_table(pa.table({"date": calendar}), tmp_path / "history" / "daily" / "0050.parquet")
    name = "前向帳戶實際投入＝啟動 30 萬＋每月 5 日 1 萬（照交易日曆）"

    def paid(amount):
        (folder / "log.jsonl").write_text(json.dumps({"date": "2026-10-20", "rule_hash": rule.rule_hash,
                                                      "contributed": amount, "cash": amount, "value": amount,
                                                      "holdings": []}) + "\n", encoding="utf-8")
        return {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 20))}[name]["ok"]

    assert not paid(310_000)                    # October's 1 萬 on the 20th: the 5th had passed
    assert paid(300_000)


def test_a_rule_graded_t0_before_the_first_possible_day_is_caught(tmp_path, monkeypatch):
    from quant_platform.research import daily
    from quant_platform.research.registry import TrialRegistry

    (tmp_path / "forward" / "stocks").mkdir(parents=True)
    (tmp_path / "forward" / "stocks" / "tracked.json").write_text("[]", encoding="utf-8")
    TrialRegistry(tmp_path / "trials.jsonl").register(kind="candidate", period="recent", spec_hash="h", spec_name="早到的",
                                                       input_hash="h", data_fingerprint="x", metrics={})
    name = "11/30 以前沒有規則被標成 T0"          # the 60th session from 9/1 (使用者 2026-10-23：叫你要浮動的)
    monkeypatch.setattr(daily, "tier", lambda metrics, forward=None: ("T0 候選", ""))
    assert {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 21))}[name]["ok"]
    monkeypatch.setattr(daily, "tier", lambda metrics, forward=None: ("T0", ""))
    found = {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 21))}[name]
    assert not found["ok"] and "1 個" in found["detail"]
    assert not {item["name"]: item for item in invariants.run(tmp_path, date(2026, 11, 27))}[name]["ok"]
    assert {item["name"]: item for item in invariants.run(tmp_path, date(2026, 11, 30))}[name]["ok"]


def test_the_latest_trade_audit_must_be_clean(tmp_path):
    (tmp_path / "forward" / "stocks").mkdir(parents=True)
    (tmp_path / "forward" / "stocks" / "tracked.json").write_text("[]", encoding="utf-8")
    folder = tmp_path / "audit"
    folder.mkdir()
    name = "最新的交易規則稽核 0 違規（8 天內）"

    def checked():
        return {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 21))}[name]

    assert not checked()["ok"] and "沒有稽核紀錄" in checked()["detail"]
    (folder / "trade-audit-1.json").write_text(json.dumps({"rules": [{}], "orders": 10, "violation_counts":
                                                           {"turnover": 3}}), encoding="utf-8")
    assert not checked()["ok"]
    (folder / "trade-audit-2.json").write_text(json.dumps({"rules": [{}], "orders": 10, "violation_counts": {}}),
                                               encoding="utf-8")
    assert checked()["ok"]


def test_the_news_and_the_second_copy_are_checked(tmp_path, monkeypatch):
    """2026-10-22: the previous session's news on disk; the copy on the other disk done within two days."""
    import pandas as pd

    (tmp_path / "forward" / "stocks").mkdir(parents=True)
    (tmp_path / "forward" / "stocks" / "tracked.json").write_text("[]", encoding="utf-8")
    (tmp_path / "history" / "daily").mkdir(parents=True)
    pd.DataFrame({"date": [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)], "close": [1.0, 1.0, 1.0]}).to_parquet(
        tmp_path / "history" / "daily" / "TAIEX.parquet", index=False)
    news = tmp_path / "history" / "raw" / "finmind" / "TaiwanStockNews"
    (news / "20261020").mkdir(parents=True)

    def checked(name):
        return {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 22))}[name]

    assert not checked("新聞收到前一個交易日")["ok"]
    (news / "20261021").mkdir()
    assert checked("新聞收到前一個交易日")["ok"]
    copy = "研究資料第二份備份（另一顆硬碟，2 天內）"
    monkeypatch.delenv("RESEARCH_MIRROR_DIR", raising=False)        # 2026-10-24: no second disk set, no check
    assert copy not in {item["name"] for item in invariants.run(tmp_path, date(2026, 10, 22))}
    monkeypatch.setenv("RESEARCH_MIRROR_DIR", "E:/elsewhere")
    assert not checked(copy)["ok"]
    (tmp_path / "jobs").mkdir()
    (tmp_path / "jobs" / "1.json").write_text(json.dumps({"name": "研究資料第二份備份（另一顆硬碟）", "status": "done",
                                                         "started_at": "2026-10-22T03:30:00+08:00"}), encoding="utf-8")
    assert checked(copy)["ok"]
    assert not checked("本月的每月名單存在")["ok"]


def test_the_worker_skips_the_checks_on_a_closed_day(tmp_path, monkeypatch):
    """2026-10-23 (颱風休市): no news or forward records are due on a closed day, so nothing to check or notify."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from quant_platform.config import Settings
    from quant_platform.container import build_container
    from quant_platform.scheduler.runner import check_invariants

    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'r.db'}"))
    container.market_calendar.add_manual_closure(date(2026, 10, 23), "颱風休市")
    ran = []
    monkeypatch.setattr(invariants, "run", lambda *args, **kwargs: ran.append(args) or [])
    monkeypatch.setattr(invariants, "save", lambda *args, **kwargs: None)
    zone = ZoneInfo("Asia/Taipei")
    assert check_invariants(container, datetime(2026, 10, 23, 16, 0, tzinfo=zone)) is None and not ran
    assert check_invariants(container, datetime(2026, 10, 22, 16, 0, tzinfo=zone)) == [] and len(ran) == 1


def test_a_window_the_project_opened_is_caught(tmp_path):
    """2026-10-23 (AGENTS.md：背景程式不開視窗): a console titled with the project's .venv, or any window of its own
    programs, fails the check; the owner's own windows do not."""
    root = tmp_path / "proj"
    research = root / "instance" / "research"
    (research / "forward" / "stocks").mkdir(parents=True)
    (research / "forward" / "stocks" / "tracked.json").write_text("[]", encoding="utf-8")
    name = "桌面沒有本專案開出的視窗"
    windows = [{"title": "Claude", "class": "Chrome_WidgetWin_1", "pid": 1, "path": r"C:\Apps\claude.exe"},
               {"title": "Windows PowerShell", "class": "ConsoleWindowClass", "pid": 2, "path": r"C:\Windows\System32\conhost.exe"}]

    def found():
        return {item["name"]: item for item in invariants.run(research, date(2026, 10, 23), windows=windows)}[name]
    assert found()["ok"] and found()["detail"] == "沒有"
    windows.append({"title": str(root / ".venv" / "Scripts" / "python.exe"), "class": "ConsoleWindowClass", "pid": 3,
                    "path": r"C:\Windows\System32\conhost.exe"})
    assert not found()["ok"] and "PID 3" in found()["detail"]
    windows[-1] = {"title": "", "class": "TkTopLevel", "pid": 4, "path": str(root / ".venv" / "Scripts" / "pythonw.exe")}
    assert not found()["ok"] and "PID 4" in found()["detail"]


def test_an_interrupted_or_dead_pipeline_is_caught(tmp_path):
    """2026-10-24 (10-23 03:27 a restart stopped half of the branch download unnoticed for 36 hours): a pipeline record
    "interrupted", or "running" without its process, fails the check; "done" and a live "running" do not."""
    import os

    (tmp_path / "forward" / "stocks").mkdir(parents=True)
    (tmp_path / "forward" / "stocks" / "tracked.json").write_text("[]", encoding="utf-8")
    folder = tmp_path / "pipelines"
    folder.mkdir()
    name = "研究流程沒有中斷了沒恢復的"

    def write(record_name, status, pid):                  # started now: a live PID is this test's own process
        from datetime import datetime
        from zoneinfo import ZoneInfo

        started = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%dT%H:%M:%S")
        (folder / f"{record_name}.json").write_text(json.dumps({"name": record_name, "pid": pid, "status": status,
                                                                "started_at": started}), encoding="utf-8")

    def found():
        return {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 24), windows=[])}[name]
    write("ok", "done", 0)
    write("alive", "running", os.getpid())
    assert found()["ok"], found()["detail"]
    write("fmdaily_a", "interrupted", 0)
    assert not found()["ok"] and "fmdaily_a（中斷" in found()["detail"]
    write("fmdaily_a", "done", 0)
    write("fmdaily_b", "running", 999999)
    assert not found()["ok"] and "fmdaily_b（程序已不在" in found()["detail"]


def test_placement_flags_the_user_folder_and_embedded_vectors(tmp_path):
    """2026-10-26 (使用者：不要再放在皮咪資料夾下；向量資料庫吃 DBQdrant)."""
    import json

    home = tmp_path / "home"
    root = home / "Project" / "StockResearch"
    research = root / "instance" / "research"
    (research / "knowledge").mkdir(parents=True)
    assert invariants.placement(root, research, str(home)) == ["專案還在使用者資料夾（" + str(root) + "）"]
    (research / "knowledge" / "vectors.json").write_text(json.dumps({"collection": "a", "server": False}), encoding="utf-8")
    assert len(invariants.placement(tmp_path / "Dev" / "StockResearch", research, str(home))) == 1
    (research / "knowledge" / "vectors.json").write_text(json.dumps({"collection": "b", "server": True}), encoding="utf-8")
    assert invariants.placement(tmp_path / "Dev" / "StockResearch", research, str(home)) == []


def test_a_rule_whose_latest_result_is_an_older_engine_is_listed(tmp_path):
    """2026-10-27: the pool showed #2189 as a T0 candidate from engine 1.2.0 while the October list (1.5.0) had it T3."""
    from quant_platform.research.daily import ENGINE_VERSION, stale_engine
    from quant_platform.research.registry import TrialRegistry

    (tmp_path / "reports").mkdir()
    registry = TrialRegistry(tmp_path / "trials.jsonl")
    for number, (engine, half) in enumerate((("daily-1.2.0", False), (ENGINE_VERSION, False), ("daily-1.1.0", True))):
        rule = DailyRule(name=f"規則{number}", factors={"trend_200": 1.0}, top=20 + number,
                         **({"core": 0.5} if half else {}))
        (tmp_path / "reports" / f"r{number}.json").write_text(json.dumps({"spec": rule.canonical()}), encoding="utf-8")
        registry.register(kind="candidate", period="recent", spec_hash=rule.rule_hash, spec_name=rule.name,
                          input_hash=f"i{number}", data_fingerprint="f", metrics={"engine": engine}, report_file=f"r{number}.json")
    assert [rule.name for rule in stale_engine(tmp_path)] == ["規則0"]               # the 0050 half is not a strategy
    registry.register(kind="candidate", period="recent", spec_hash=stale_engine(tmp_path)[0].rule_hash, spec_name="規則0",
                      input_hash="i9", data_fingerprint="f", metrics={"engine": ENGINE_VERSION}, report_file="r0.json")
    assert stale_engine(tmp_path) == []


def test_the_trading_page_names_the_engine_and_is_on_the_project_list(tmp_path):
    """2026-10-28 (使用者：專案資訊有把所有的交易事項需知、開發需知、規則寫清楚嗎)."""
    from quant_platform.research.daily import ENGINE_VERSION

    assert "沒有 docs/trading_rules.md" in invariants.trading_page(tmp_path)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "trading_rules.md").write_text("現行引擎：**daily-0.0.1**", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("x", encoding="utf-8")
    assert any("引擎版本" in item for item in invariants.trading_page(tmp_path))
    (tmp_path / "docs" / "trading_rules.md").write_text(f"現行引擎：**{ENGINE_VERSION}**", encoding="utf-8")
    assert invariants.trading_page(tmp_path) == []
    project = Path(__file__).resolve().parents[1]
    assert invariants.trading_page(project) == []                 # the repository's own page and list


def test_the_research_pages_are_opened_and_timed(monkeypatch):
    """2026-10-28 (使用者：研究的載入也太慢了吧；每一筆交易都要顯示買入的價格跟賣出的價格)."""
    import io
    import urllib.request

    bodies = {"/research": "ok", "/research/rules/h": '<span class="rule-no">#1</span>',
              "/research/forward/h": '<span class="rule-no">#1</span> 買進價'}

    def opened(url, timeout=0):
        path = url.split("5000", 1)[1]
        return io.BytesIO(bodies[path].encode("utf-8"))

    monkeypatch.setattr(urllib.request, "urlopen", opened)
    found = invariants.research_pages("http://127.0.0.1:5000", "h")
    assert found == ["前向頁沒有「賣出價」"]
    bodies["/research/forward/h"] += " 賣出價"
    assert invariants.research_pages("http://127.0.0.1:5000", "h") == []
    assert invariants.research_pages("http://127.0.0.1:5000", "h", limit=-1.0)[0].startswith("研究總覽 ")



def test_a_research_process_above_the_idle_priority_is_caught(tmp_path):
    """2026-10-29 (使用者：你他媽當我電腦資源很多是不是): research and worker at the idle class; the pages may be normal."""
    root = str(tmp_path.parent.parent)
    rows = [{"pid": 1, "priority": 4, "command": f"{root}\\.venv\\Scripts\\python.exe -m quant_platform.research forward"},
            {"pid": 2, "priority": 8, "command": f"{root}\\.venv\\Scripts\\quant-web.exe"},
            {"pid": 3, "priority": 8, "command": "C:\\Other\\UnrealBuildTool.exe"},
            {"pid": 4, "priority": 8, "command": "python.exe -m quant_platform.research scan-full --phase b"}]
    rows.append({"pid": 5, "priority": 8, "command": f"{root}\\.venv\\Scripts\\quant-worker.exe"})
    rows.append({"pid": 6, "priority": 8, "command": "python.exe -m quant_platform.research live-flow"})
    assert [item["pid"] for item in invariants.greedy(rows, tmp_path.parent.parent)] == [4]
    found = invariants.priority_check(rows, tmp_path.parent.parent)
    assert found["name"] == "本專案的研究程式都用最低優先權" and not found["ok"] and "PID 4" in found["detail"]
    assert invariants.priority_check([row for row in rows if row["pid"] != 4], tmp_path.parent.parent)["ok"]


def test_a_day_the_machine_ran_out_of_memory_is_caught(tmp_path):
    """2026-10-28: seven times; a forward, two boards, a training and the full search died."""
    name = "過去 24 小時記憶體沒有用完"
    events = [{"time": "10/28 21:28", "program": "python.exe", "pid": 32120, "gb": 36.0}]
    found = {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 29), windows=[], memory=events)}[name]
    assert not found["ok"] and "PID 32120" in found["detail"] and "36.0 GB" in found["detail"]
    assert {item["name"]: item for item in invariants.run(tmp_path, date(2026, 10, 29), windows=[], memory=[])}[name]["ok"]
