"""Corporate actions as a shareholder lives them (engine 1.4.0, 2026-10-13; 使用者：所有交易都要照台股的交易規則
和機制，不要買賣不存在的零碎股).

Engine 1.3 and before turned every ex-rights or ex-dividend event into more units of the same stock at the
reference price (the cash dividend reinvested on the ex-date, a cash capital increase taken as free shares),
so holdings carried fractions of a share that were later sold. A shareholder instead gets:

* a cash dividend as cash on its payment date (FinMind ``TaiwanStockDividend`` ``CashDividendPaymentDate``;
  without one, ``PAY_LAG`` sessions after the ex-date), less the supplementary health-insurance premium on a
  single payment at or above its threshold (``health_premium``);
* a stock dividend as whole new shares (NT$10 of par per share); the fraction of a share is lost (the law pays
  it at par, but many companies keep a book-entry holder's fractions as the transfer fee);
* nothing from a cash capital increase (現金增資) — the right to subscribe is not used, so its value is lost.

The official ex-rights tables (TWSE TWT49U, TPEx) are the list of events; FinMind's dividend policy gives each
one's cash and stock parts and the payment date. TPEx's table has the parts itself; a TWSE cash-only event's cash
is the table's value; a TWSE event with shares is a cash capital increase when FinMind has the code's policies
but none on that day, and otherwise (no policies for the code) counted as cash of the same value (``source``
"estimate").
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PAR = 10.0                    # NT$ of par value per share: a stock dividend of NT$1 is 0.1 share
PAY_LAG = 20                  # sessions from the ex-date to the payment when the payment date is unknown


@dataclass(frozen=True)
class Action:
    cash: float               # NT$ per share held at the ex-date, paid on ``paid``
    shares: float             # new shares per share held (stock dividends); whole shares, fractions at par
    paid: date | None         # the cash payment date; None: PAY_LAG sessions after the ex-date
    source: str               # "finmind", "tpex", "twse-cash", "rights", "estimate"


def delisting_dates(base: Path) -> dict[str, list[date]]:
    """code -> its delisting dates (FinMind TaiwanStockDelisting; a code can be reused)."""
    from quant_platform.research.history.finmind import read_rows

    output: dict[str, list[date]] = defaultdict(list)
    for row in read_rows(Path(base), "TaiwanStockDelisting", "all"):
        try:
            output[str(row["stock_id"])].append(date.fromisoformat(str(row["date"])[:10]))
        except (KeyError, ValueError):
            continue
    return dict(output)


def dividend_plans(base: Path, codes) -> dict[str, list[tuple[date, date, float]]]:
    """code -> [(announced, cash ex-date, NT$ per share)] from FinMind's policies that carry all three — what the
    market knew from the announcement on (time factor ``dividend_ahead``)."""
    from quant_platform.research.history.dividends_daily import announcements
    from quant_platform.research.history.finmind import read_rows

    official = announcements(Path(base))      # 2026-10-16: the exchanges' nightly ex-rights tables, after FinMind's
    output: dict[str, list[tuple[date, date, float]]] = {}
    for code in codes:
        items = []
        for row in read_rows(Path(base), "TaiwanStockDividend", code):
            announced, ex_day = _iso(row.get("AnnouncementDate")), _iso(row.get("CashExDividendTradingDate"))
            cash = _number(row.get("CashEarningsDistribution")) + _number(row.get("CashStatutorySurplus"))
            if announced and ex_day and cash > 0 and announced <= ex_day:
                items.append((announced, ex_day, cash))
        known = {ex_day for _announced, ex_day, _cash in items}
        for event in official.get(code, []):
            if event["cash_announced"] and event["ex_date"] not in known and event["cash_announced"] <= event["ex_date"]:
                items.append((event["cash_announced"], event["ex_date"], float(event["cash"])))
        if items:
            output[code] = items
    return output


def health_premium(amount: float, day: date) -> int:
    """二代健保補充保費 on one dividend payment: 2% at or above NT$5,000 (2013–2015), 1.91% at or above NT$20,000
    (2016–2020), 2.11% at or above NT$20,000 (2021 on)."""
    if day >= date(2021, 1, 1):
        rate, threshold = 0.0211, 20_000
    elif day >= date(2016, 1, 1):
        rate, threshold = 0.0191, 20_000
    else:
        rate, threshold = 0.02, 5_000
    return int(round(amount * rate)) if amount >= threshold else 0


def whole_shares(units: float, per_share: float) -> tuple[int, float]:
    """(new whole shares, the fraction of a share left over) for ``units`` shares and ``per_share`` new
    shares per share."""
    raw = units * per_share
    whole = int(math.floor(raw + 1e-9))
    return whole, max(0.0, raw - whole)


def _number(text: object) -> float:
    try:
        return float(str(text).replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


def _roc(text: str) -> date | None:
    cleaned = str(text).strip().replace("年", "/").replace("月", "/").replace("日", "")
    try:
        year, month, day = (int(part) for part in cleaned.split("/"))
        return date(year + 1911, month, day)
    except ValueError:
        return None


def official_events(raw_dir: Path, first_year: int) -> dict[str, dict[date, dict[str, float | str]]]:
    """symbol -> ex-date -> the official table's row: before, reference, value (before − reference), kind,
    and for TPEx the cash, stock (shares per share) and rights (new shares by subscription) parts."""
    events: dict[str, dict[date, dict[str, float | str]]] = defaultdict(dict)
    for path in sorted((raw_dir / "twse_ex_rights").rglob("*.json")):
        if not path.stem.isdigit() or int(path.stem) < first_year:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload = payload.get("payload", payload)
        fields = payload.get("fields") or []
        for row in payload.get("data") or []:
            record = dict(zip(fields, row))
            day = _roc(record.get("資料日期", ""))
            before, reference = _number(record.get("除權息前收盤價")), _number(record.get("除權息參考價"))
            kind = str(record.get("權/息", "")).strip()
            if day and before > 0 and reference > 0 and kind:
                events[f"{str(record.get('股票代號')).strip()}.TW"][day] = {
                    "market": "twse", "kind": kind, "before": before, "reference": reference,
                    "value": _number(record.get("權值+息值")),
                    "high": _number(record.get("漲停價格")), "low": _number(record.get("跌停價格"))}
    for path in sorted((raw_dir / "tpex_ex_rights").rglob("*.json")):
        if not path.stem.isdigit() or int(path.stem) < first_year:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for table in payload.get("tables") or []:
            fields = table.get("fields") or []
            for row in table.get("data") or []:
                record = dict(zip(fields, row))
                day = _roc(record.get("除權息日期", ""))
                before, reference = _number(record.get("除權息前收盤價")), _number(record.get("除權息參考價"))
                kind = str(record.get("權/息", "")).strip()
                if day and before > 0 and reference > 0 and kind:
                    events[f"{str(record.get('代號')).strip()}.TWO"][day] = {
                        "market": "tpex", "kind": kind, "before": before, "reference": reference,
                        "value": _number(record.get("權值+息值")), "cash": _number(record.get("現金股利")),
                        "stock": _number(record.get("每仟股無償配股")) / 1000,
                        "rights": _number(record.get("現金增資股數")),
                        "high": _number(record.get("漲停價")), "low": _number(record.get("跌停價"))}
    return events


def move_to_open_days(events: dict[str, dict[date, object]], open_days) -> tuple[dict[str, dict[date, object]], int]:
    """Each event on the first of ``open_days`` (a sorted list, or symbol -> sorted list) on or after its date:
    an ex-date on a day the market closed (typhoons: 2015-07-10, 2016-07-08, 2019-08-09, 2023-08-03,
    2024-07-24/25, 2026-07-10) is the next session. Returns the events and how many moved."""
    import bisect

    output: dict[str, dict[date, object]] = {}
    moved = 0
    for symbol, items in events.items():
        days = open_days.get(symbol) if isinstance(open_days, dict) else open_days
        if not days:
            output[symbol] = dict(items)
            continue
        present = set(days)
        placed: dict[date, object] = {day: event for day, event in items.items() if day in present}
        for day, event in sorted(items.items()):
            if day in present:
                continue
            place = bisect.bisect_left(days, day)
            if place < len(days):
                placed.setdefault(days[place], event)
                moved += 1
        output[symbol] = placed
    return output, moved


def dividend_policies(base: Path, code: str) -> tuple[dict[date, tuple[float, date | None]], dict[date, float], bool]:
    """FinMind's policy for one code: (cash ex-date -> (NT$ per share, payment date), stock ex-date -> new
    shares per share, whether FinMind has any policy for the code)."""
    from quant_platform.research.history.finmind import read_rows

    cash: dict[date, tuple[float, date | None]] = {}
    stock: dict[date, float] = defaultdict(float)
    rows = read_rows(base, "TaiwanStockDividend", code)
    for row in rows:
        amount = _number(row.get("CashEarningsDistribution")) + _number(row.get("CashStatutorySurplus"))
        when = _iso(row.get("CashExDividendTradingDate"))
        if when and amount > 0:
            paid = _iso(row.get("CashDividendPaymentDate"))
            total, earlier = cash.get(when, (0.0, None))
            cash[when] = (total + amount, paid or earlier)
        shares = (_number(row.get("StockEarningsDistribution")) + _number(row.get("StockStatutorySurplus"))) / PAR
        when = _iso(row.get("StockExDividendTradingDate"))
        if when and shares > 0:
            stock[when] += shares
    return cash, dict(stock), bool(rows)


def _iso(text: object) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10]) if text else None
    except ValueError:
        return None


def build_actions(base: Path, events: dict[str, dict[date, dict[str, float | str]]]) -> dict[str, dict[date, Action]]:
    """symbol -> ex-date -> what a holder gets, for every official event (see the module text)."""
    output: dict[str, dict[date, Action]] = {}
    policies: dict[str, tuple[dict, dict, bool]] = {}
    for symbol, days in events.items():
        code = symbol.split(".")[0]
        if code not in policies:
            policies[code] = dividend_policies(base, code)
        cash_policy, stock_policy, covered = policies[code]
        actions: dict[date, Action] = {}
        for day, event in days.items():
            known_cash, known_stock = cash_policy.get(day), stock_policy.get(day)
            if known_cash is not None or known_stock is not None:
                amount, paid = known_cash or (0.0, None)
                actions[day] = Action(amount, known_stock or 0.0, paid, "finmind")
            elif event["market"] == "tpex":
                rights_only = not event.get("cash") and not event.get("stock")
                actions[day] = Action(float(event.get("cash") or 0.0), float(event.get("stock") or 0.0), None,
                                      "rights" if rights_only else "tpex")
            elif event["kind"] == "息":
                actions[day] = Action(float(event["value"]), 0.0, None, "twse-cash")
            elif covered:                        # shares on a day with no dividend: a cash capital increase
                actions[day] = Action(0.0, 0.0, None, "rights")
            else:
                actions[day] = Action(float(event["before"]) - float(event["reference"]), 0.0, None, "estimate")
        output[symbol] = actions
    return output
