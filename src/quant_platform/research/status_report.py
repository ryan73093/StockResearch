"""2026-11-11 (docs/problem_register_2026-11-11.md「D 的設計」第 3 點：狀態由程式印出；P9：回報只引用輸出):
``python -m quant_platform.research status`` — what the handoff used to say by hand ("現況"), read from the files
the platform keeps, each date set against what the exchange's calendar says it should be.

- 研究資料：the last close in ``history/daily/TAIEX.parquet``; due: ``invariants.due_session``.
- 前向紀錄：the last record of ``forward/stocks/log.jsonl`` (its tail only — the log is 400 MB); due: the same.
- 流程：the running and waiting jobs (``jobs.JobLog``, a job whose process is gone is not counted) and the pipelines
  marked running, interrupted or failed (``pipelines/*.json``).
- 16:00 檢查：the red lines of the latest ``invariants.json``, and whether today's run is there.
- 當月名單：the month's cutoff (``rolling.cutoff_for``) against the newest list, graded, passed and usable.
- 串接前向：sessions from the first list's day after its cutoff (2026-09-01) to the last forward record, the rules on
  every list since, and the day the first T0 can come (``invariants.FIRST_T0``).

Read-only; no data is loaded beyond these files."""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
LIST_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")          # a month's list (not the .before-… copies)


def last_close(research: Path) -> date | None:
    path = research / "history" / "daily" / "TAIEX.parquet"
    if not path.is_file():
        return None
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    found = pc.max(pq.read_table(path, columns=["date"])["date"]).as_py()
    return found if isinstance(found, date) else (date.fromisoformat(str(found)[:10]) if found else None)


def last_forward(research: Path, tail: int = 4 << 20) -> date | None:
    """The date of the last complete line of the forward log, read from its end (a longer piece while the last line
    is longer than the one read)."""
    path = research / "forward" / "stocks" / "log.jsonl"
    if not path.is_file():
        return None
    with open(path, "rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        while True:
            handle.seek(max(0, size - tail))
            lines = handle.read().splitlines()
            for raw in reversed(lines if size <= tail else lines[1:]):    # the first piece may be cut
                try:
                    return date.fromisoformat(str(json.loads(raw)["date"])[:10])
                except (ValueError, KeyError, TypeError):
                    continue
            if size <= tail:
                return None
            tail *= 4


def flows(research: Path, now: datetime) -> dict[str, list[dict[str, object]]]:
    from quant_platform.research.jobs import JobLog, _alive

    running, waiting = [], []
    rows = JobLog(research).jobs(limit=400)
    for row in rows:
        if row["status"] == "running":
            running.append({"name": row.get("name"), "since": row.get("started_at"), "percent": row.get("percent"),
                            "eta": row.get("eta"), "current": row.get("current")})
        elif row["status"] == "waiting":
            waiting.append({"name": row.get("name"), "since": row.get("started_at"), "reason": row.get("current")})
    broken = []
    for path in sorted((research / "pipelines").glob("*.json")) if (research / "pipelines").is_dir() else []:
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        status = record.get("status")
        started = None
        try:
            started = datetime.fromisoformat(str(record.get("started_at")))
            started = started if started.tzinfo else started.replace(tzinfo=TAIPEI)
        except ValueError:
            pass
        if status in ("interrupted", "failed") or (status == "running" and not _alive(int(record.get("pid") or 0), started)):
            broken.append({"name": record.get("name") or path.stem, "status": status if status != "running" else "程序已不在",
                           "since": record.get("started_at")})
    # 2026-11-11: the worker's and the flows' jobs that failed in the last 24 hours and that no later run of the same job
    # (the same name) has done since — the nightly model refit failed nine nights and nothing here said so
    failed, done = {}, set()
    for row in sorted(rows, key=lambda row: str(row.get("started_at") or ""), reverse=True):
        try:
            began = datetime.fromisoformat(str(row.get("started_at")))
        except ValueError:
            continue
        began = began if began.tzinfo else began.replace(tzinfo=TAIPEI)
        name = str(row.get("name"))
        if row["status"] == "done":
            done.add(name)
        elif row["status"] == "failed" and name not in done and name not in failed and now - began <= timedelta(hours=24):
            failed[name] = {"name": name, "since": row.get("started_at"), "summary": row.get("summary")}
    return {"running": running, "waiting": waiting, "broken": broken, "failed": list(failed.values())}


def checks(research: Path, now: datetime) -> dict[str, object]:
    from quant_platform.research.invariants import latest

    payload = latest(research) or {}
    at = str(payload.get("at") or "")
    found = payload.get("checks") or []
    due_today = (now.hour, now.minute) >= (16, 10)                     # the check runs at 16:00
    return {"at": at, "count": len(found), "red": [item for item in found if not item.get("ok")],
            "today": at[:10] == now.date().isoformat() if due_today else None}


def lists(research: Path, today: date) -> dict[str, object]:
    """The month's list and every earlier one: which rules passed each (``rolling`` folder)."""
    from quant_platform.research.rolling import cutoff_for, folder

    found = sorted(path for path in folder(research).glob("*.json") if LIST_NAME.match(path.name)) \
        if folder(research).is_dir() else []
    months = []
    for path in found:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        graded = payload.get("graded") or []
        months.append({"cutoff": payload.get("cutoff") or path.stem, "graded": len(graded),
                       "passed": {str(row.get("rule_hash")) for row in graded if row.get("passed")},
                       "usable": sum(1 for row in payload.get("holdout") or [] if row.get("usable")),
                       "data_through": payload.get("data_through")})
    return {"wanted": cutoff_for(today).isoformat(), "months": months}


def chain(research: Path, months: list[dict[str, object]], through: date | None) -> dict[str, object]:
    """The longest chained forward record possible: from the first list's day after its cutoff to ``through``."""
    from quant_platform.research.daily import FORWARD_SESSIONS
    from quant_platform.research.invariants import FIRST_T0, _calendar

    if not months or through is None:
        return {"start": None, "sessions": 0, "needed": FORWARD_SESSIONS, "rules": 0, "first_t0": FIRST_T0.isoformat()}
    start = date.fromisoformat(str(months[0]["cutoff"])) + timedelta(days=1)
    sessions = _calendar(research).sessions_after(start - timedelta(days=1), through)
    every = set.intersection(*(month["passed"] for month in months)) if months else set()
    return {"start": start.isoformat(), "sessions": sessions, "needed": FORWARD_SESSIONS, "rules": len(every),
            "lists": len(months), "first_t0": FIRST_T0.isoformat()}


def collect(research: str | Path, now: datetime | None = None) -> dict[str, object]:
    from quant_platform.research.invariants import due_session

    research = Path(research)
    now = now or datetime.now(TAIPEI)
    due = due_session(research, now.date(), now)
    month = lists(research, now.date())
    forward = last_forward(research)
    return {"at": now.isoformat(timespec="minutes"), "due": due.isoformat(),
            "close": (last_close(research) or "").__str__(), "forward": (forward or "").__str__(),
            "flows": flows(research, now), "checks": checks(research, now),
            "list": {"wanted": month["wanted"],
                     "latest": ({key: value for key, value in month["months"][-1].items() if key != "passed"}
                                | {"passed": len(month["months"][-1]["passed"])}) if month["months"] else None},
            "chain": chain(research, month["months"], forward)}


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def render(state: dict[str, object]) -> str:
    due = state["due"]
    lines = [f"狀態（{state['at'][:16].replace('T', ' ')}；日期照交易所日曆，應有的交易日 {due}）", "", "資料"]
    for label, key in (("研究資料（收盤）", "close"), ("前向紀錄", "forward")):
        value = state[key] or "沒有"
        lines.append(f"  {label}：{value}（應有 {due}）{_mark(bool(state[key]) and state[key] >= due)}")
    flow = state["flows"]
    lines += ["", f"流程：在跑 {len(flow['running'])} 個、等待中 {len(flow['waiting'])} 個、中斷或失敗沒恢復的 {len(flow['broken'])} 個"]
    for row in flow["running"]:
        progress = f"，{row['percent']}%" if row.get("percent") is not None else ""
        lines.append(f"  在跑：{row['name']}（{str(row['since'])[:16].replace('T', ' ')} 起{progress}"
                     + (f"，預計 {row['eta']}" if row.get("eta") else "") + "）" + (f"：{row['current']}" if row.get("current") else ""))
    for row in flow["waiting"]:
        lines.append(f"  等待：{row['name']}（{str(row['since'])[:16].replace('T', ' ')} 起）：{row['reason']}")
    for row in flow["broken"]:
        lines.append(f"  {row['status']}：{row['name']}（{str(row['since'])[:16].replace('T', ' ')} 開始）")
    if flow.get("failed"):
        lines.append(f"  最近 24 小時失敗、之後沒有成功過的工作 {len(flow['failed'])} 個")
    for row in flow.get("failed") or []:
        lines.append(f"  失敗：{row['name']}（{str(row['since'])[:16].replace('T', ' ')}）：{row['summary']}")
    found = state["checks"]
    if not found["at"]:
        lines += ["", "16:00 需求檢查：還沒有結果"]
    else:
        stale = "" if found["today"] in (None, True) else "；✗ 今天 16:00 的還沒有"
        lines += ["", f"16:00 需求檢查（{found['at'][:16].replace('T', ' ')}，{found['count']} 項）：紅燈 {len(found['red'])} 項{stale}"]
        lines += [f"  ✗ {item['name']}：{item.get('detail', '')}" for item in found["red"]]
    month = state["list"]
    lines += ["", "當月名單"]
    if month["latest"]:
        latest = month["latest"]
        lines.append(f"  應為截止 {month['wanted']}；最新名單截止 {latest['cutoff']} {_mark(latest['cutoff'] == month['wanted'])}："
                     f"評 {latest['graded']:,} 個、過門檻 {latest['passed']:,} 個、當月可用 {latest['usable']:,} 個"
                     + (f"（資料到 {latest['data_through']}）" if latest.get("data_through") else ""))
    else:
        lines.append(f"  應為截止 {month['wanted']}；沒有名單 ✗")
    linked = state["chain"]
    lines += ["", "串接前向（T0 的定義見 docs/trading_rules.md §7）"]
    if linked["start"]:
        lines.append(f"  從 {linked['start']}（第一份名單截止日的隔天）到 {state['forward']}：{linked['sessions']} 個交易日／要 {linked['needed']} 個")
        lines.append(f"  每一份名單（{linked['lists']} 份）都過門檻的規則：{linked['rules']:,} 個")
    else:
        lines.append("  還沒有名單或前向紀錄")
    lines.append(f"  第一次可能有 T0：{linked['first_t0']} 收盤後（路線圖日期表）")
    return "\n".join(lines)


def emit(text: str, stream=None) -> None:
    """Print the report as UTF-8 whatever the console's code page. 2026-11-11: run from a tool (stdout a pipe, code
    page 950) the command died on the first ✓ with a UnicodeEncodeError, so the takeover step could not be run."""
    stream = stream or sys.stdout
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")
    print(text, file=stream)
