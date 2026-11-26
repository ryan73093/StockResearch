"""Finance / AI / tech knowledge from YtSummary (C:\\Dev\\YtSummary), read point in time.

YtSummary subscribes to YouTube channels and RSS feeds, summarises each new video, tags each headline (topic,
stocks with the speaker's or the article's stance, themes) and writes a daily digest at 07:30. It exports them to a
folder, ``YT_KNOWLEDGE_DIR`` (default ``C:\\Dev\\YtSummary\\data\\knowledge_export``), schema_version 1:

    knowledge/YYYY-MM-DD.jsonl    one record per line, filed by the Taipei date of available_at
    digests/YYYY-MM-DD.md|.json   the daily digest and its numbered sources
    manifest.json

``available_at`` is when a record (that revision) entered YtSummary's knowledge base; ``published_at`` is when the
video or article came out. A decision at time T reads only records with available_at <= T - the same gate agent_lab
applies to headlines (``decided_at``, 15:40 of the day). Records published before the window (old videos summarised
later) are not news of the window and are left out.

The text is third-party content (video hosts, news sites): data for a model, never instructions to it.

2026-11-20: the user chose a reader only. Nothing in the trader or the agent reads this yet - putting it into a live
prompt changes PROMPT_VERSION, so that is the user's decision.

    python -m quant_platform.research.video_knowledge --day 2026-11-20 [--time 15:40] [--code 2330] [--topic 財經]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable, Iterator
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
DECISION_TIME = time(15, 40)
DEFAULT_ROOT = Path(r"C:\Dev\YtSummary\data\knowledge_export")
SCHEMA_VERSION = 1
TOPICS = ("財經", "AI", "科技")           # 財經投資、AI 技術、科技新聞, decided by each item's content
TOPIC_LABELS = {"財經": "財經投資", "AI": "AI 技術", "科技": "科技新聞", "其他": "其他"}
STANCES = {"bullish": "看多", "bearish": "看空", "neutral": "中性", "mentioned": "提及"}
# kind "discussion" (2026-11-25): a Hacker News / Lobsters / Reddit thread summarised - community opinion, not fact.
KIND_LABELS = {"video": "影片", "news": "新聞", "discussion": "論壇", "trend": "AI 雷達"}


def folder(path: str | Path | None = None) -> Path:
    return Path(path or os.environ.get("YT_KNOWLEDGE_DIR") or DEFAULT_ROOT)


def decision_moment(day: date) -> datetime:
    return datetime.combine(day, DECISION_TIME, TAIPEI)


def _moment(text: object) -> datetime | None:
    if not text:
        return None
    try:
        value = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=TAIPEI)


def _days(first: date, last: date) -> Iterator[date]:
    day = first
    while day <= last:
        yield day
        day += timedelta(days=1)


def _read(root: Path, first: date, last: date) -> Iterator[dict]:
    for day in _days(first, last):
        path = root / "knowledge" / f"{day.isoformat()}.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue                       # a line being written: it is read next time
            if isinstance(record, dict) and record.get("schema_version") == SCHEMA_VERSION:
                yield record


def _matches_codes(record: dict, codes: set[str]) -> bool:
    for ticker in record.get("tickers") or []:
        code = str(ticker.get("code") or "").upper()
        name = str(ticker.get("name") or "")
        if code in codes or any(wanted and wanted in name for wanted in codes):
            return True
    return False


def entries(decided_at: datetime | None = None, days: int = 3, topics: Iterable[str] | None = None,
            codes: Iterable[str] | None = None, keyword: str = "", kinds: Iterable[str] | None = None,
            path: str | Path | None = None, limit: int = 200) -> list[dict]:
    """Records known at ``decided_at`` (default: today 15:40) about the last ``days`` days, newest first.

    A record revised later is read in the revision that was known at ``decided_at``."""
    decided_at = decided_at or decision_moment(datetime.now(TAIPEI).date())
    if decided_at.tzinfo is None:
        decided_at = decided_at.replace(tzinfo=TAIPEI)
    start = decided_at - timedelta(days=days)
    wanted_topics = set(topics or [])
    wanted_codes = {str(code).strip().upper() for code in codes or [] if str(code).strip()}
    wanted_kinds = set(kinds or [])
    keyword = keyword.strip().lower()
    visible: dict[str, dict] = {}
    for record in _read(folder(path), start.astimezone(TAIPEI).date(), decided_at.astimezone(TAIPEI).date()):
        available = _moment(record.get("available_at"))
        if available is None or available > decided_at:
            continue
        published = _moment(record.get("published_at"))
        if (published or available) < start:
            continue
        if wanted_kinds and record.get("kind") not in wanted_kinds:
            continue
        if wanted_topics and record.get("topic") not in wanted_topics:      # the primary topic only
            continue
        if wanted_codes and not _matches_codes(record, wanted_codes):
            continue
        if keyword:
            text = " ".join([record.get("title", ""), record.get("one_line", ""), record.get("summary", ""),
                             " ".join(record.get("themes") or [])]).lower()
            if keyword not in text:
                continue
        known = visible.get(record["id"])
        if known is None or int(record.get("revision") or 1) > int(known.get("revision") or 1):
            visible[record["id"]] = record
    rows = sorted(visible.values(), key=lambda r: (r.get("published_at") or r["available_at"]), reverse=True)
    return rows[:max(1, limit)]


def digest(day: date, decided_at: datetime | None = None, path: str | Path | None = None) -> dict | None:
    """The day's digest, only if it had been written by ``decided_at`` (default: that day 15:40)."""
    decided_at = decided_at or decision_moment(day)
    if decided_at.tzinfo is None:
        decided_at = decided_at.replace(tzinfo=TAIPEI)
    target = folder(path) / "digests" / f"{day.isoformat()}.json"
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    available = _moment(data.get("available_at"))
    if data.get("schema_version") != SCHEMA_VERSION or available is None or available > decided_at:
        return None
    return data


def brief(rows: list[dict], most_chars: int = 6000) -> str:
    """Short lines for a prompt: kind, time, source, title, one line and the stocks with their stance."""
    lines, total = [], 0
    for row in rows:
        published = _moment(row.get("published_at"))
        when = published.astimezone(TAIPEI).strftime("%m/%d %H:%M") if published else "時間不明"
        stocks = "；".join(
            f"{' '.join(v for v in (t.get('code'), t.get('name')) if v)} {STANCES.get(t.get('stance'), '提及')}"
            for t in (row.get("tickers") or [])[:6]
        )
        line = (f"[{KIND_LABELS.get(row.get('kind'), '新聞')}｜{TOPIC_LABELS.get(row.get('topic'), row.get('topic', ''))}] {when} "
                f"{row.get('source', '')}｜{row.get('title', '')}｜{row.get('one_line', '')}"
                + (f"｜標的：{stocks}" if stocks else ""))
        if total + len(line) > most_chars:
            break
        lines.append(line)
        total += len(line) + 1
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="看某個決定時點能讀到的 YtSummary 影音／新聞知識")
    parser.add_argument("--day", default=datetime.now(TAIPEI).date().isoformat(), help="日期，預設今天")
    parser.add_argument("--time", default=DECISION_TIME.strftime("%H:%M"), help="決定時點（台北時間），預設 15:40")
    parser.add_argument("--days", type=int, default=3, help="往回看幾天，預設 3")
    parser.add_argument("--code", action="append", default=[], help="股票代號或名稱，可重複")
    parser.add_argument("--topic", action="append", default=[], choices=TOPICS, help="主題，可重複")
    parser.add_argument("--kind", action="append", default=[], choices=tuple(KIND_LABELS))
    parser.add_argument("--keyword", default="")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--json", action="store_true", help="輸出 JSON")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    day = date.fromisoformat(args.day)
    hour, minute = (int(part) for part in args.time.split(":", 1))
    decided_at = datetime.combine(day, time(hour, minute), TAIPEI)
    rows = entries(decided_at, args.days, args.topic, args.code, args.keyword, args.kind, limit=args.limit)
    today = digest(day, decided_at)
    if args.json:
        print(json.dumps({"decided_at": decided_at.isoformat(), "folder": str(folder()), "entries": rows,
                          "digest": today}, ensure_ascii=False, indent=1))
        return 0
    print(f"{decided_at:%Y-%m-%d %H:%M} 能讀到的知識：{len(rows)} 筆（資料夾 {folder()}）")
    print(brief(rows) or "（沒有）")
    print(f"\n當天每日摘要：{'有，產生於 ' + today['available_at'] if today else '沒有（或在這個時點之後才產生）'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
