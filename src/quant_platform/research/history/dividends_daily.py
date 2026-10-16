"""Nightly dividend and cash-capital-increase announcements (2026-10-16).

FinMind's dividend policies (TaiwanStockDividend) were downloaded once (2026-10-13/14). The rules under forward
observation that use them — the dividend changes and cash capital increases (research/chips.py CORPORATE_FACTORS) and
the coming ex-dividend (daily.py ``dividend_ahead``) — need the announcements made since. TWSE (TWT48U) and TPEx
(open data ``tpex_exright_prepost``) list every coming ex-rights and ex-dividend date with its cash dividend and the
cash capital increase's subscription; each night's table is kept under ``raw/official_dividends/<exchange>/<day>.json``
and an event counts as announced on the first night it is listed — the announcement FinMind dates too (its
AnnouncementDate is the ex-rights announcement), at most a day later. A dividend listed as 待公告 counts from the
night its amount appears.

TWSE lists only the subscription per share held, so a TWSE cash capital increase is that ratio × the shares issued
÷ 0.8 (existing shareholders usually take about 80%; the rest goes to the public and the employees). TPEx lists the
three parts.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

TWSE_URL = "https://www.twse.com.tw/rwd/zh/exRight/TWT48U?response=json"
TPEX_URL = "https://www.tpex.org.tw/openapi/v1/tpex_exright_prepost"
STOCK = re.compile(r"^(?!91)[1-9]\d{3}$")
EXISTING_SHARE = 0.8


def fetch(client, today: date) -> dict[str, int]:
    """Tonight's two tables (kept per night); returns the stock rows each lists."""
    counts = {}
    for exchange, url in (("twse", TWSE_URL), ("tpex", TPEX_URL)):
        payload = client._cached(f"official_dividends/{exchange}/{today:%Y%m%d}", url, final=False, period_end=today)
        counts[exchange] = len(parse(exchange, payload))
    return counts


def _roc(text: object) -> date | None:
    digits = re.findall(r"\d+", str(text or ""))
    try:
        if len(digits) == 3:
            return date(int(digits[0]) + 1911, int(digits[1]), int(digits[2]))
        if len(digits) == 1 and len(digits[0]) == 7:
            return date(int(digits[0][:3]) + 1911, int(digits[0][3:5]), int(digits[0][5:]))
    except ValueError:
        return None
    return None


def _number(text: object) -> float | None:
    cleaned = str(text if text is not None else "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None                                    # 待公告實際收益分配金額 and the like


def parse(exchange: str, payload: object) -> list[dict[str, object]]:
    """Stock rows: code, ex-date, cash per share (None until announced), subscription per share held, and for TPEx
    the new shares in all (None for TWSE)."""
    rows = []
    if exchange == "twse":
        if not isinstance(payload, dict) or str(payload.get("stat", "")).lower() != "ok":
            return []
        fields = [str(field).replace(" ", "") for field in payload.get("fields") or []]
        try:
            at = {name: fields.index(name) for name in ("除權除息日期", "股票代號", "現金增資配股率", "現金股利")}
        except ValueError:
            return []
        for item in payload.get("data") or []:
            code = str(item[at["股票代號"]]).strip()
            ex_date = _roc(item[at["除權除息日期"]])
            if STOCK.match(code) and ex_date:
                rows.append({"code": code, "ex_date": ex_date, "cash": _number(item[at["現金股利"]]),
                             "issue_ratio": _number(item[at["現金增資配股率"]]) or 0.0, "issue_shares": None})
        return rows
    for item in payload if isinstance(payload, list) else []:
        code = str(item.get("SecuritiesCompanyCode", "")).strip()
        ex_date = _roc(item.get("ExRrightsExDividendDate"))
        if not STOCK.match(code) or not ex_date:
            continue
        parts = [_number(item.get(name)) or 0.0 for name in
                 ("AllocatedForPublicUnderwriting", "SubscribedByEmployees", "SubscribedByExistingShareholders")]
        rows.append({"code": code, "ex_date": ex_date, "cash": _number(item.get("CashDividend")),
                     "issue_ratio": _number(item.get("SubscriptionRatioToNewSharesIssued")) or 0.0,
                     "issue_shares": sum(parts) or None})
    return rows


def announcements(base: Path) -> dict[str, list[dict[str, object]]]:
    """code -> [{ex_date, cash, cash_announced, issue_ratio, issue_shares, issue_announced}] from every night kept:
    each the first night its cash amount (or its capital increase) was listed."""
    raw = Path(base) / "raw" / "official_dividends"
    events: dict[tuple[str, date], dict[str, object]] = {}
    nights = []
    for exchange in ("twse", "tpex"):
        for path in sorted((raw / exchange).glob("*.json")) if (raw / exchange).is_dir() else []:
            try:
                night = date(int(path.stem[:4]), int(path.stem[4:6]), int(path.stem[6:8]))
                nights.append((night, exchange, json.loads(path.read_text(encoding="utf-8"))))
            except (ValueError, OSError):
                continue
    for night, exchange, payload in sorted(nights, key=lambda item: item[0]):
        for row in parse(exchange, payload):
            event = events.setdefault((row["code"], row["ex_date"]), {"ex_date": row["ex_date"], "cash": None,
                                                                      "cash_announced": None, "issue_ratio": 0.0,
                                                                      "issue_shares": None, "issue_announced": None})
            if event["cash_announced"] is None and (row["cash"] or 0) > 0:
                event.update(cash=row["cash"], cash_announced=night)
            if event["issue_announced"] is None and row["issue_ratio"] > 0:
                event.update(issue_ratio=row["issue_ratio"], issue_shares=row["issue_shares"], issue_announced=night)
    output: dict[str, list[dict[str, object]]] = {}
    for (code, _ex_date), event in sorted(events.items(), key=lambda item: (item[0][0], item[0][1])):
        if event["cash_announced"] or event["issue_announced"]:
            output.setdefault(code, []).append(event)
    return output


def policy_rows(events: list[dict[str, object]], known_ex_dates: set[date], shares_at) -> list[dict[str, object]]:
    """FinMind-shaped policy rows for the announcements FinMind does not have (its ex-dates left out);
    ``shares_at(day)`` gives the shares issued for a TWSE capital increase."""
    rows = []
    for event in events:
        if event["ex_date"] in known_ex_dates:
            continue
        if event["cash_announced"]:
            rows.append({"AnnouncementDate": event["cash_announced"].isoformat(), "CashEarningsDistribution": event["cash"],
                         "CashStatutorySurplus": 0.0, "CashExDividendTradingDate": event["ex_date"].isoformat(),
                         "source": "official"})
        if event["issue_announced"]:
            shares = event["issue_shares"]
            if shares is None:
                held = shares_at(event["issue_announced"])
                shares = event["issue_ratio"] * held / EXISTING_SHARE if held else None
            if shares:
                rows.append({"AnnouncementDate": event["issue_announced"].isoformat(),
                             "TotalNumberOfCashCapitalIncrease": shares, "source": "official"})
    return rows
