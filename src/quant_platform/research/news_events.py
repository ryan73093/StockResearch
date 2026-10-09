"""R15 D (2026-10-09, 使用者：兩個都做，上限每月 5 美元): every collected headline turned into fixed event fields.

The news collected each trading day (research/news.py: the top 150 stocks by turnover and the forward
holdings, headlines only) is scored by a language model into fields fixed in advance — which of the stocks
the headline is about, the event type, good or bad news, whether it carries a concrete figure, better or
worse than before, a risk flag. Identical headlines filed under several stocks are scored once; fifty
headlines share one call. The fields are written the evening they are scored, with the model and the
prompt version, and a day is never scored again (a newer model would know what happened next).

They feed one pre-registered experiment (research_method §9, R15 D): the weekly model rule half in 0050
with bad news vetoing new buys (``DailyRule.news_veto = "v1"``) against the same rule computing the same
flags without acting on them (``"v1-off"``), both recorded forward from the same day. A flag counts from
the first session after it was scored until 10 sessions after the news day. Judged after a year.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

TAIPEI = ZoneInfo("Asia/Taipei")
PROMPT_VERSION = "news-events-1.0.0"
OPERATION = "news_events"
FIRST_DAY = date(2026, 10, 5)              # the first collected day
BATCH = 50
VETO_SESSIONS = 10
EVENT_TYPES = ("營收", "財測展望", "股利", "獲利財報", "併購處分", "訂單產品", "訴訟違約", "處置警示", "人事董監", "籌碼法人", "其他")
NEWS = Path("raw") / "finmind" / "TaiwanStockNews"
EVENTS = Path("news_events")

INSTRUCTIONS = """你是台股新聞分類員，只依每則標題的字面內容分類，不使用標題以外的知識、不判斷股價會不會漲。
每則輸入有：編號、這則新聞被歸在哪些股票代號、標題。
對每一則輸出一個陣列 [編號, 相關代號, 類型, 方向, 有數字, 比較, 風險]：
- 相關代號：輸入的代號中，標題真正在講的公司（可以是空陣列，例如大盤、ETF 或產業總評）。
- 類型（只能選一個）：營收、財測展望、股利、獲利財報、併購處分、訂單產品、訴訟違約、處置警示、人事董監、籌碼法人、其他。
- 方向：對相關公司是利多 1、利空 -1、中性或看不出 0。
- 有數字：標題有具體數字（營收、EPS、成長率、股利金額、訂單金額）1，沒有 0。
- 比較：標題明說比去年、比上月或比預期好 1、差 -1，沒說 0。
- 風險：涉及違約、訴訟、處置或警示、停工、虧損擴大、下修財測、掏空、董監大量賣股等重大風險 1，否則 0。
只輸出 JSON 物件 {"r": [[...], ...]}，每則一個陣列、順序不限，不要其他文字。"""


def _clean(title: str) -> str:
    return re.sub(r"\s+", " ", str(title or "")).strip()


def build_client(settings, research_dir: str | Path):
    """The model client for the scoring, inside its own share of the monthly LLM budget; None when off or
    without a key."""
    from quant_platform.research.agent.llm import LLMError, ResponsesClient, UsageLedger, resolve_openai_key

    if not settings.news_events_enabled:
        return None
    try:
        key = resolve_openai_key(settings.openai_api_key)
    except LLMError:
        return None
    if not key:
        return None
    return ResponsesClient(key, settings.research_agent_model, UsageLedger(Path(research_dir) / "agent" / "usage.jsonl"),
                           settings.news_events_monthly_budget_usd, operations=(OPERATION,),
                           total_budget_usd=settings.llm_monthly_budget_usd, label="新聞事件評分")


def collected_days(history: str | Path) -> list[date]:
    folder = Path(history) / NEWS
    days = []
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        try:
            day = datetime.strptime(path.name, "%Y%m%d").date()
        except ValueError:
            continue
        if day >= FIRST_DAY and any(path.glob("*.json.gz")):
            days.append(day)
    return days


def events_path(history: str | Path, day: date) -> Path:
    return Path(history) / EVENTS / f"{day:%Y%m%d}.parquet"


def pending_days(history: str | Path) -> list[date]:
    return [day for day in collected_days(history) if not events_path(history, day).is_file()]


def headlines(history: str | Path, day: date) -> list[dict[str, object]]:
    """The day's headlines, each once, with every stock it was filed under."""
    from quant_platform.research.news import read

    merged: dict[str, dict[str, object]] = {}
    for code, rows in read(history, day).items():
        for row in rows or []:
            title = _clean(row.get("title"))
            if not title:
                continue
            item = merged.setdefault(title, {"title": title, "stocks": [], "link": row.get("link") or "",
                                             "source": row.get("source") or ""})
            stock = str(row.get("stock_id") or code)
            if stock not in item["stocks"]:
                item["stocks"].append(stock)
    return list(merged.values())


def _parse(payload: dict, batch: list[dict[str, object]]) -> dict[int, dict[str, object]]:
    """The model's rows, checked: unknown numbers dropped, codes kept only if the headline was filed under them."""
    output: dict[int, dict[str, object]] = {}
    for row in payload.get("r") or []:
        if not isinstance(row, list) or len(row) < 7:
            continue
        try:
            number = int(row[0])
        except (TypeError, ValueError):
            continue
        if not 0 <= number < len(batch):
            continue
        filed = set(batch[number]["stocks"])
        about = [str(code) for code in (row[1] or []) if str(code) in filed]

        def sign(value) -> int:
            try:
                return max(-1, min(1, int(value)))
            except (TypeError, ValueError):
                return 0

        output[number] = {"about": about, "type": row[2] if row[2] in EVENT_TYPES else "其他",
                          "direction": sign(row[3]), "numbers": bool(sign(row[4])), "surprise": sign(row[5]),
                          "risk": bool(sign(row[6]))}
    return output


def score_day(client, history: str | Path, day: date, now: datetime | None = None) -> pd.DataFrame:
    """Score one day's headlines (50 per call) and write the fields; nothing is written unless every call
    succeeded (a budget stop or a model error leaves the day for the next run)."""
    items = headlines(history, day)
    rows = []
    stamp = (now or datetime.now(UTC)).astimezone(TAIPEI).isoformat(timespec="seconds")
    for start in range(0, len(items), BATCH):
        batch = items[start: start + BATCH]
        lines = [json.dumps([number, item["stocks"], item["title"]], ensure_ascii=False) for number, item in enumerate(batch)]
        payload, _usage = client.json_call(INSTRUCTIONS, "以 JSON 回覆。\n" + "\n".join(lines), OPERATION,
                                           f"{day:%Y%m%d}-{start // BATCH}")
        scored = _parse(payload, batch)
        for number, item in enumerate(batch):
            fields = scored.get(number)
            for stock in item["stocks"]:
                rows.append({
                    "news_day": day.isoformat(), "stock_id": stock, "title": item["title"], "link": item["link"],
                    "source": item["source"], "scored": fields is not None,
                    "about": bool(fields and stock in fields["about"]),
                    "type": fields["type"] if fields else "", "direction": fields["direction"] if fields else 0,
                    "numbers": bool(fields and fields["numbers"]), "surprise": fields["surprise"] if fields else 0,
                    "risk": bool(fields and fields["risk"]), "model": getattr(client, "model", ""),
                    "prompt_version": PROMPT_VERSION, "scored_at": stamp,
                })
    frame = pd.DataFrame(rows, columns=["news_day", "stock_id", "title", "link", "source", "scored", "about", "type",
                                        "direction", "numbers", "surprise", "risk", "model", "prompt_version", "scored_at"])
    path = events_path(history, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial")
    frame.to_parquet(partial, index=False)
    partial.replace(path)
    return frame


def run(client, history: str | Path, job=None, now: datetime | None = None) -> dict[str, object]:
    """Score every collected day not scored yet, oldest first; stop at the first budget stop or error."""
    from quant_platform.research.agent.llm import LLMError

    days = pending_days(history)
    done, error = [], ""
    if job:
        job.update(total=len(days), force=True)
    for number, day in enumerate(days):
        if job:
            job.update(done=number, current=f"{day}：{len(headlines(history, day))} 則標題", force=True)
        try:
            frame = score_day(client, history, day, now)
        except LLMError as exc:
            error = str(exc)
            break
        done.append({"day": day.isoformat(), "rows": len(frame), "about": int(frame["about"].sum()) if len(frame) else 0})
    return {"scored": done, "pending": len(days) - len(done), "error": error,
            "budget": client.budget_status() if hasattr(client, "budget_status") else None}


def load(history: str | Path) -> pd.DataFrame:
    folder = Path(history) / EVENTS
    frames = [pd.read_parquet(path) for path in sorted(folder.glob("*.parquet"))] if folder.is_dir() else []
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def is_bad(frame: pd.DataFrame) -> pd.Series:
    """The pre-registered veto (v1): news about the stock that is bad and either concrete or a risk."""
    return frame["about"] & (frame["direction"] == -1) & (frame["numbers"] | frame["risk"])


def veto_matrix(history: str | Path, sessions: list[date], symbols: list[str]) -> np.ndarray:
    """symbols × sessions: True where a bad-news flag is in force — from the first session after it was
    scored until VETO_SESSIONS sessions after the news day."""
    output = np.zeros((len(symbols), len(sessions)), dtype=bool)
    frame = load(history)
    if frame.empty:
        return output
    frame = frame[is_bad(frame)]
    rows = {symbol.split(".")[0]: row for row, symbol in enumerate(symbols)}
    index = pd.Index(pd.to_datetime(sessions))
    for item in frame.itertuples():
        row = rows.get(str(item.stock_id))
        if row is None:
            continue
        scored = datetime.fromisoformat(str(item.scored_at)).date()
        first = int(index.searchsorted(pd.Timestamp(scored + timedelta(days=1))))
        news = int(index.searchsorted(pd.Timestamp(date.fromisoformat(str(item.news_day)))))
        last = min(news + VETO_SESSIONS, len(sessions) - 1)
        if first <= last:
            output[row, first: last + 1] = True
    return output


def summary(history: str | Path, recent: int = 30) -> dict[str, object]:
    """What the research page shows: days scored, counts by type, the latest bad-news flags."""
    frame = load(history)
    if frame.empty:
        return {"days": [], "types": {}, "flags": [], "pending": [day.isoformat() for day in pending_days(history)]}
    about = frame[frame["about"]]
    days = [{"day": day, "headlines": int(part["title"].nunique()), "about": int(part["about"].sum()),
             "good": int(((part["direction"] == 1) & part["about"]).sum()),
             "bad": int(((part["direction"] == -1) & part["about"]).sum()), "veto": int(is_bad(part).sum()),
             "unscored": int((~part["scored"]).sum())}
            for day, part in frame.groupby("news_day")]
    flags = frame[is_bad(frame)].sort_values(["news_day", "stock_id"], ascending=[False, True]).head(recent)
    return {"days": sorted(days, key=lambda item: item["day"], reverse=True),
            "types": about["type"].value_counts().to_dict(),
            "flags": flags[["news_day", "stock_id", "title", "type", "numbers", "risk", "link"]].to_dict("records"),
            "pending": [day.isoformat() for day in pending_days(history)]}
