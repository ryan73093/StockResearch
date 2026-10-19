"""2026-10-20 (使用者：我也要你自動每日抓取財經新聞): the day's financial headlines from public RSS feeds.

The per-stock news (research/news.py, FinMind, 14:40 on trading days) covers the most traded stocks one day late;
these are the market's headlines — the market, the economy, policy, the big names — as the feeds publish them, read
several times a day (a feed keeps its latest 20–57 items). Each read is merged into the day's file by link, so a
headline is kept once: ``raw/news/market/<YYYYMMDD>.json`` (title, source, link, published time, a short summary).
Only titles and the feeds' own summaries are kept, with the link to the article.

A feed is XML from the publisher: a document with a DTD or entities is refused (no entity expansion), and only the
first MAX_BYTES are read.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ElementTree
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
FEEDS = {
    "yahoo_market": ("Yahoo 股市・台股盤勢", "https://tw.stock.yahoo.com/rss?category=tw-market"),
    "yahoo_news": ("Yahoo 股市・財經新聞", "https://tw.stock.yahoo.com/rss?category=news"),
    "cna_finance": ("中央社・財經", "https://feeds.feedburner.com/rsscna/finance"),
    "udn_money": ("經濟日報・證券", "https://money.udn.com/rssfeed/news/1001/5590?ch=money"),
    "cnyes_tw": ("鉅亨網・台股", "https://news.cnyes.com/rss/v1/news/category/tw_stock"),
}
MAX_BYTES = 2_000_000
SUMMARY = 160


def _get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 StockResearch/1.0 (personal research)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read(MAX_BYTES)


def _text(node, name: str) -> str:
    found = node.find(name)
    return (found.text or "").strip() if found is not None and found.text else ""


def parse(feed: str, raw: bytes) -> list[dict[str, str]]:
    """The feed's items: title, link, published (ISO, Taipei time), a short plain-text summary."""
    head = raw[:4096].decode("utf-8", "ignore").upper()
    if "<!DOCTYPE" in head or "<!ENTITY" in raw.decode("utf-8", "ignore").upper():
        raise ValueError(f"{feed}：拒絕有 DTD／ENTITY 的文件")
    root = ElementTree.fromstring(raw)
    items = []
    for item in root.iter("item"):
        title, link = _text(item, "title"), _text(item, "link")
        if not title or not link:
            continue
        published = _text(item, "pubDate")
        try:
            when = parsedate_to_datetime(published).astimezone(TAIPEI).isoformat(timespec="minutes") if published else ""
        except (TypeError, ValueError):
            when = ""
        summary = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _text(item, "description"))).strip()
        items.append({"feed": feed, "source": FEEDS[feed][0] if feed in FEEDS else feed, "title": title, "link": link,
                      "published": when, "summary": summary[:SUMMARY]})
    return items


def path_for(base: str | Path, day: str) -> Path:
    return Path(base) / "raw" / "news" / "market" / f"{day.replace('-', '')}.json"


def collect(base: str | Path, now: datetime | None = None, get=_get) -> dict[str, int]:
    """Every feed read once; each item filed under the day it was published (else today), merged by link."""
    now = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    by_day: dict[str, list[dict]] = {}
    read, failed = 0, []
    for feed, (_label, url) in FEEDS.items():
        try:
            items = parse(feed, get(url))
        except Exception as error:  # noqa: BLE001 - one feed down does not stop the others
            failed.append(f"{feed}: {str(error)[:80]}")
            continue
        read += 1
        for item in items:
            day = (item["published"] or now.isoformat())[:10]
            by_day.setdefault(day, []).append(item)
    added = 0
    for day, items in by_day.items():
        target = path_for(base, day)
        kept = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else []
        links = {item["link"] for item in kept}
        fresh = [item for item in items if item["link"] not in links and not links.add(item["link"])]
        if not fresh:
            continue
        kept.extend({**item, "read_at": now.isoformat(timespec="minutes")} for item in fresh)
        kept.sort(key=lambda item: item.get("published") or "", reverse=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".partial")
        partial.write_text(json.dumps(kept, ensure_ascii=False, indent=0), encoding="utf-8")
        os.replace(partial, target)
        added += len(fresh)
    return {"feeds": read, "added": added, "failed": len(failed), "errors": failed}


def latest(base: str | Path, days: int = 2) -> list[dict]:
    """The newest ``days`` days of headlines, newest first (the research page)."""
    folder = Path(base) / "raw" / "news" / "market"
    files = sorted(folder.glob("*.json"))[-days:] if folder.is_dir() else []
    items = []
    for path in files:
        try:
            items.extend(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    items.sort(key=lambda item: item.get("published") or "", reverse=True)
    return items
