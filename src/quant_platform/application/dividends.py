"""ETF distributions for the real account (S5-W03 股利入帳; first part of S7-W02).

The exchanges' ex-rights/ex-dividend previews — TWSE 除權除息預告表 (TWT48U) and TPEx
上櫃股票除權除息預告表 (OpenAPI ``tpex_exright_prepost``) — list the coming weeks' ex-dates and
the cash dividend per unit once it is announced. Every fetched event is kept in
``instance/events/ex_dividends.json``, so after the ex-date the holdings page can still say what
the account should receive and offer to record it. The tables carry no payment date; ETFs usually
pay three to five weeks after the ex-date.

Entitlement: units held at the close of the session before the ex-date, i.e. fills dated before
it. Each recorded dividend (with a symbol) settles the earliest open event of that symbol whose
ex-date is on or before the day it was recorded for.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")

TWSE_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT48U?response=json"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"
REMINDER_DAYS = 90       # how long after the ex-date the page asks to record the dividend
KEEP_DAYS = 400          # events older than this are dropped from the cache
REFRESH_EVERY = timedelta(hours=12)
ROC_TEXT = re.compile(r"(\d{2,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class ExDividend:
    symbol: str           # "0056", "00679B"
    name: str
    ex_date: date
    cash: float | None    # per unit; None until announced
    market: str           # "TWSE" or "TPEx"


def _roc(text: object) -> date | None:
    """115年10月08日 or 1151008 → 2026-10-08."""
    raw = str(text or "").strip()
    match = ROC_TEXT.search(raw)
    if match:
        year, month, day = (int(part) for part in match.groups())
    elif raw.isdigit() and len(raw) == 7:
        year, month, day = int(raw[:3]), int(raw[3:5]), int(raw[5:])
    else:
        return None
    try:
        return date(year + 1911, month, day)
    except ValueError:
        return None


def _cash(value: object) -> float | None:
    raw = TAG.sub("", str(value or "")).replace(",", "").strip()
    try:
        amount = float(raw)
    except ValueError:
        return None  # "待公告實際收益分配金額", "尚未公告"
    return amount if amount > 0 else None


def parse_twse(payload: object) -> list[ExDividend]:
    payload = payload if isinstance(payload, dict) else {}
    fields = [TAG.sub("", str(field)).strip() for field in payload.get("fields") or []]

    def column(name: str, default: int) -> int:
        return fields.index(name) if name in fields else default

    at_day, at_code, at_name = column("除權除息日期", 0), column("股票代號", 1), column("名稱", 2)
    at_kind, at_cash = column("除權息", 3), column("現金股利", 7)
    events = []
    for row in payload.get("data") or []:
        try:
            day, kind = _roc(row[at_day]), str(row[at_kind])
            symbol, name, cash = str(row[at_code]).strip(), str(row[at_name]).strip(), _cash(row[at_cash])
        except (IndexError, TypeError):
            continue
        if day is not None and symbol and "息" in kind:
            events.append(ExDividend(symbol, name, day, cash, "TWSE"))
    return events


def parse_tpex(payload: object) -> list[ExDividend]:
    events = []
    for row in payload if isinstance(payload, list) else []:
        if not isinstance(row, dict):
            continue
        day = _roc(row.get("ExRrightsExDividendDate"))
        symbol = str(row.get("SecuritiesCompanyCode") or "").strip()
        if day is not None and symbol and "息" in str(row.get("ExRrightsExDividend") or ""):
            events.append(ExDividend(
                symbol, str(row.get("CompanyName") or "").strip(), day, _cash(row.get("CashDividend")), "TPEx",
            ))
    return events


def _fetch_json(url: str) -> object:
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0 StockResearch/1.0"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


SOURCES = (("TWSE", TWSE_URL, parse_twse), ("TPEx", TPEX_URL, parse_tpex))


class DividendCalendar:
    """The cached ex-dividend events and their refresh."""

    def __init__(self, path: str | Path, fetch=None, clock=None) -> None:
        self._path = Path(path)
        self._fetch = fetch or _fetch_json
        self._clock = clock or (lambda: datetime.now(UTC))

    def _load(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            logger.warning("Ex-dividend cache %s is unreadable; starting over", self._path)
            return {}

    def updated_at(self) -> datetime | None:
        stamp = self._load().get("updated_at")
        return datetime.fromisoformat(stamp) if stamp else None

    def events(self) -> list[ExDividend]:
        events = []
        for item in self._load().get("events") or []:
            try:
                events.append(ExDividend(
                    item["symbol"], item.get("name", ""), date.fromisoformat(item["ex_date"]),
                    item.get("cash"), item.get("market", ""),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return events

    def refresh(self) -> dict[str, object]:
        now = self._clock()
        today = now.astimezone(TAIPEI).date()
        fetched: list[ExDividend] = []
        answered: set[str] = set()
        errors = []
        for market, url, parse in SOURCES:
            try:
                rows = parse(self._fetch(url))
            except Exception as exc:  # one exchange being down must not drop the other
                errors.append(f"{market}: {exc}")
                logger.warning("Ex-dividend preview from %s failed: %s", market, exc)
                continue
            if rows:
                answered.add(market)
                fetched += rows
        merged = {(event.symbol, event.ex_date): event for event in self.events()}
        incoming = {(event.symbol, event.ex_date): event for event in fetched}
        # A future event missing from its exchange's (non-empty) preview was moved or cancelled.
        removed = [key for key, event in merged.items()
                   if event.market in answered and event.ex_date > today and key not in incoming]
        for key in removed:
            del merged[key]
        added = updated = 0
        for key, event in incoming.items():
            old = merged.get(key)
            if old is None:
                added += 1
                merged[key] = event
            elif event.cash is not None and event.cash != old.cash:
                updated += 1  # an announced amount is never replaced by "not announced"
                merged[key] = event
        if not answered and not merged:
            return {"added": 0, "updated": 0, "removed": 0, "count": 0, "errors": errors}
        keep_from = today - timedelta(days=KEEP_DAYS)
        events = sorted((event for event in merged.values() if event.ex_date >= keep_from),
                        key=lambda event: (event.ex_date, event.symbol))
        self._save(events, now if answered else None)
        return {"added": added, "updated": updated, "removed": len(removed), "count": len(events), "errors": errors}

    def refresh_if_due(self) -> dict[str, object] | None:
        updated = self.updated_at()
        if updated is not None and self._clock() - updated < REFRESH_EVERY:
            return None
        return self.refresh()

    def _save(self, events: list[ExDividend], stamp: datetime | None) -> None:
        previous = self._load().get("updated_at")
        payload = {
            "updated_at": stamp.isoformat() if stamp else previous,
            "events": [
                {"symbol": event.symbol, "name": event.name, "ex_date": event.ex_date.isoformat(),
                 "cash": event.cash, "market": event.market}
                for event in events
            ],
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        partial = self._path.with_suffix(".json.partial")
        partial.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        partial.replace(self._path)

    def account_view(self, flows, trades, today: date) -> dict[str, object]:
        return account_view(self.events(), flows, trades, today, self.updated_at())


def _units(trades, symbol: str, before: date | None = None) -> int:
    """Units of ``symbol`` from fills dated before ``before`` (all fills when None)."""
    return sum(
        (trade.shares if trade.side == "BUY" else -trade.shares)
        for trade in trades
        if trade.symbol == symbol and (before is None or trade.day < before)
    )


def account_view(events, flows, trades, today: date, updated_at: datetime | None = None) -> dict[str, object]:
    """Ex-dates coming up for held ETFs and recent ones whose dividend is not recorded yet."""
    symbols = {trade.symbol for trade in trades}
    upcoming, due = [], []
    open_events: dict[str, list[ExDividend]] = {}
    for event in sorted(events, key=lambda item: item.ex_date):
        if event.symbol not in symbols:
            continue
        if event.ex_date > today:
            units = _units(trades, event.symbol)
            if units > 0:
                upcoming.append(_line(event, units))
        elif _units(trades, event.symbol, event.ex_date) > 0:
            open_events.setdefault(event.symbol, []).append(event)
    # Each recorded dividend settles the earliest open event on or before its day (older events
    # included, so a late entry for an old payment does not settle a newer one).
    for flow in sorted(flows, key=lambda item: item.day):
        if flow.kind != "dividend" or not flow.symbol:
            continue
        candidates = open_events.get(flow.symbol.strip().upper(), [])
        for event in candidates:
            if event.ex_date <= flow.day:
                candidates.remove(event)
                break
    since = today - timedelta(days=REMINDER_DAYS)
    for symbol_events in open_events.values():
        for event in symbol_events:
            if event.ex_date >= since:
                due.append(_line(event, _units(trades, event.symbol, event.ex_date)))
    due.sort(key=lambda line: line["ex_date"])
    return {"upcoming": upcoming, "due": due, "updated_at": updated_at}


def per_unit_text(cash: float | None) -> str:
    """1.0 → 1.00, 0.866 → 0.866; None → 待公告."""
    if cash is None:
        return "待公告"
    whole, _, decimals = f"{cash:.4f}".rstrip("0").partition(".")
    return f"{whole}.{decimals.ljust(2, '0')}"


def _line(event: ExDividend, units: int) -> dict[str, object]:
    return {
        "symbol": event.symbol, "name": event.name, "ex_date": event.ex_date, "cash": event.cash,
        "cash_text": per_unit_text(event.cash), "units": units,
        "estimate": None if event.cash is None else math.floor(units * event.cash),
    }
