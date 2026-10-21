"""Chip and fundamental factors from FinMind's free tier (research roadmap R13, 2026-10-04).

``build`` turns the downloaded files (raw/finmind/<dataset>/<code>.json.gz) into one compact Parquet per
dataset under history/chips/ (date, code and the numbers used). ``ChipStore`` pivots them onto the factor
panel's sessions and stocks and computes the factors, point in time:

- shareholding, institutional trading, margin and P/E are published in the evening of their date, after
  the after-hours odd-lot auction the rules trade in, so a value dated day t is used from day t+1;
- a monthly revenue is used from the session after its announcement (``create_time``; the 11th of the
  following month when the record has none: the legal deadline is the 10th).

Factors (higher = what the factor favours; the strength analysis tells which direction pays):
外資持股比率、外資持股增加（20 日）、外資買超（20 日，佔發行股數）、投信買超（20 日）、融資增加（20 日）、
券資比、本益比倒數（盈餘殖利率）、淨值比倒數、月營收年增、近 3 個月營收年增、市值（大型股）.
"""

from __future__ import annotations

import gzip
import json
import warnings
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

CHIP_FACTORS = {
    "foreign_holding": "外資持股比率",
    "foreign_holding_change": "外資持股比率增加（20 日）",
    "foreign_buy_20": "外資買超（20 日，佔發行股數）",
    "trust_buy_20": "投信買超（20 日，佔發行股數）",
    "margin_growth_20": "融資餘額增加（20 日）",
    "short_margin_ratio": "券資比",
    "earnings_yield": "本益比倒數（盈餘殖利率）",
    "book_to_price": "淨值比倒數",
    "revenue_yoy": "月營收年增率",
    "revenue_yoy_3m": "近 3 個月營收年增率",
    "market_cap": "市值（大型股）",
}
# 2026-10-16 (自營商): the securities dealers' own trading and their hedging (mostly of the call warrants they
# issued, so it follows retail warrant buying), 20 sessions against the shares issued — separate since 2014-12 in
# FinMind, used from the next session. Kept apart from the 2026-10-04 chip batch.
DEALER_FACTORS = {
    "dealer_buy_20": "自營商自行買賣買超（20 日，佔發行股數）",
    "dealer_hedge_20": "自營商避險買超（20 日，佔發行股數；多半是權證避險）",
}
# 2026-10-09: an event factor, fresh revenue news only. Quantified fundamental news drifts for weeks after
# it is public while the 70-day revenue_yoy holds stale months (AI-methods review); this one exists only in
# the 20 sessions after a monthly revenue is announced. Kept apart so the 2026-10-04 chip batch stays as run.
EVENT_FACTORS = {"revenue_accel": "月營收加速（公布後 20 個交易日內：最新年增率減前 3 個月平均）"}
ACCEL_SESSIONS = 20
# 2026-10-14 (資料缺口：借券賣出餘額、當沖比例): short selling through securities lending, and day trading, from
# FinMind's daily tables (published after the close: used from the next session). Kept apart from the 2026-10-04
# chip batch. Higher = what the factor favours: little lent-and-sold stock, shorts covering, little day trading.
SHORT_FACTORS = {
    "low_sbl_short": "借券賣出餘額低（佔發行股數，反向）",
    "sbl_cover_20": "借券賣出回補（20 日餘額減少，佔發行股數）",
    "low_day_trade_20": "當沖比例低（20 日當沖買進金額佔成交值，反向）",
}
# 2026-10-14 (資料缺口：處置／注意股; research/history/alerts.py): how often the exchange flagged the stock lately — an
# attention notice is published after the close (used from the next session), a disposition on its announcement.
ALERT_FACTORS = {
    "low_attention_20": "不過熱（20 日內被列注意股的次數，反向）",
    "low_disposition_60": "未被處置（60 日內被公告處置的次數，反向）",
}
# 2026-10-15 (借券賣出餘額低是強因子，沿著借券這條線): what borrowing a stock costs and how much of it is borrowed, from
# FinMind's securities-lending deals (TaiwanStockSecuritiesLending: each deal's lots and fee rate, published after the
# close: used from the next session). A stock short sellers want is borrowed a lot and costs more to borrow (Drechsler
# & Drechsler 2014, the shorting premium). The fee is unknown for a stock without a deal in the window (left out of
# the ranking); new borrowing is 0 on a session without a deal once the data starts.
LENDING = "TaiwanStockSecuritiesLending"
LENDING_FACTORS = {
    "low_lending_fee_60": "借券費率低（60 日成交量加權平均費率，反向）",
    "low_lending_20": "新借券少（20 日借券成交量佔發行股數，反向）",
    "lending_fee_not_rising": "借券費率沒有升高（近 20 日比前 60 日，反向）",
}
# 2026-10-15 (公司財務政策): what a company announces it pays out or raises, from FinMind's dividend policies
# (TaiwanStockDividend, the date of the ex-rights announcement — later than the board's decision, never early; used
# from the next session). A raised dividend is followed by better returns and a cash capital increase by worse
# (Michaely, Thaler & Womack 1995; Loughran & Ritter 1995). Each holds 250 sessions; a stock without one is 0.
CORPORATE = "CorporateEvents"
CORPORATE_FACTORS = {
    "dividend_raise": "股利調升（現金股利比一年前同期多多少，佔股價；公告後 250 日）",
    "no_cash_issue": "沒有現金增資（現增新股佔發行股數，反向；公告後 250 日）",
}
CORPORATE_SESSIONS = 250
# 2026-10-19 (使用者：資料先用免費的；還有什麼方法可以找到更好的策略): what insiders and the company announce on the
# Market Observation Post System — insiders' advance notices to sell on the market (research/history/insider.py; a
# gift, a trust or a sale to a named person is not a sale on the market) and buyback programs (history/buyback.py,
# only what the board's announcement says), each from the session after its date, summed over 60 sessions against the
# shares issued. Insider sales are followed by weaker returns and buyback announcements by stronger (Lakonishok & Lee
# 2001; Ikenberry, Lakonishok & Vermaelen 1995; Taiwan event studies of 申報轉讓 and 庫藏股).
DISCLOSURE_FACTORS = {
    "low_insider_sell_60": "內部人申報在市場賣出少（60 日內申報要在市場賣的股數佔發行股數，反向）",
    "buyback_60": "庫藏股買回（60 日內董事會決議要買回的股數佔發行股數）",
}
DISCLOSURE_SESSIONS = 60
# 2026-10-20: directors' and supervisors' pledged shares against what they hold (research/history/pledge.py), each
# month's from the first session after the 15th of the next month (the filing deadline), until the next month's; a
# company missing from a month's page has none that month. Heavy pledging deepens falls (margin calls).
PLEDGE_FACTORS = {
    "low_pledge": "董監質押低（董監持股中設質的比例，反向）",
    "pledge_heavy": "董監質押過半（董監持股設質 ≥ 50% 記為 1）",
}
PLEDGE_HEAVY = 50.0
# 2026-10-16 (股本膨脹): new shares over 250 sessions against the shares issued — cash capital increases, convertible
# bonds and employee shares turned into stock, shares issued in mergers (net share issuance, Pontiff & Woodgate 2008).
# A stock dividend's new shares are not issuance: the first jump in the shares issued within 70 sessions after its
# ex-date that is 0.5–1.5 times log(1 + new shares per share) is taken out (TSMC-style listings follow the ex-date by
# 1–10 weeks). A fall (capital reduction, cancelled treasury shares) is left out: a reduction that offsets losses is
# not a buyback. Higher = fewer new shares.
ISSUANCE_FACTORS = {"low_issuance_250": "股本沒有膨脹（250 日內新增股數佔發行股數，扣掉配股；反向）"}
# 2026-10-21 (FinMind Sponsor 計畫，路線圖 S9-W22): the depository's weekly holder brackets (集保戶股權分散表,
# research/history/tdcc.py: the free open data kept from 2026-10-16, FinMind's TaiwanStockHoldingSharesPer from 2010
# once bought). A week's table (the Friday's) comes out over the weekend: used from the first session at least
# HOLDER_DELAY_DAYS after its date, until the next week's (at most HOLDER_HOLD sessions). The share held in lots of
# 1,000 and more (bracket 15) rising, fewer holders and the small holders' share (under 10 lots, brackets 1-3) falling
# — chips concentrating — are Taiwan's folk signals; tested like any other factor.
HOLDER_FACTORS = {
    "big_holder_pct": "千張大戶持股比例（集保，每週）",
    "big_holder_up_4w": "千張大戶持股比例增加（4 週變化，集保）",
    "holders_down_4w": "股東人數減少（4 週變化率，反向，集保）",
    "retail_down_4w": "散戶持股比例減少（未滿 10 張，4 週變化，反向，集保）",
}
HOLDER_DELAY_DAYS = 3
HOLDER_HOLD = 15
# 2026-10-21 (使用者付了 FinMind Sponsor：請繼續研究): factors from the paid tables (research/history/paid.py), each
# day's table used from the next session. Margin buyers near a margin call sell (Taiwan's 斷頭 below 130%) and those
# under water sell into rallies; borrowing against shares for any purpose is leverage that unwinds in falls; block
# trades at a premium are informed buying; the top branches' net buying is the "main force" (主力) — known only from
# 2021-06-28, so a rule using it is graded on the short track (daily.short_track).
PAID_FACTORS = {
    "margin_safe": "融資維持率高（個股融資維持率；沒有融資的股票不排）",
    "margin_in_profit": "融資戶沒有套牢（前一天收盤價 ÷ 融資平均成本 − 1）",
    "low_pledge_loan_20": "不限用途借貸沒有增加（20 日餘額增加佔發行股數，反向）",
    "block_premium_20": "鉅額交易溢價（20 日內鉅額成交均價相對當天收盤，依股數加權）",
    "block_volume_20": "鉅額交易量（20 日內鉅額成交股數佔發行股數）",
    "broker_concentration_5": "主力買超（5 日前 15 大分點買減賣 ÷ 買加賣；2021-06-28 起，短歷史賽道）",
}
PAID_SOURCES = {"margin_safe": "TaiwanStockMarginMaintenance", "margin_in_profit": "TaiwanStockMarginMaintenance",
                "low_pledge_loan_20": "TaiwanStockLoanCollateralBalance", "block_premium_20": "TaiwanStockBlockTrade",
                "block_volume_20": "TaiwanStockBlockTrade", "broker_concentration_5": "TaiwanStockBrokerDailyConcentration"}

STOCK_DIVIDENDS = "StockDividends"
DIVIDEND_LISTING_SESSIONS = 70
SAME_PERIOD_DAYS = (305, 425)          # the payout "a year earlier": announced 305–425 days before
FIELDS = {
    "TaiwanStockShareholding": ("ForeignInvestmentSharesRatio", "NumberOfSharesIssued"),
    "TaiwanStockPER": ("PER", "PBR"),
    "TaiwanStockMarginPurchaseShortSale": ("MarginPurchaseTodayBalance", "ShortSaleTodayBalance"),
}
SHORT_FIELDS = {
    "TaiwanDailyShortSaleBalances": ("SBLShortSalesCurrentDayBalance", "MarginShortSalesCurrentDayBalance"),
    "TaiwanStockDayTrading": ("BuyAmount", "Volume"),
}


def _rows(raw: Path, dataset: str):
    for path in sorted((raw / dataset).glob("*.json.gz")):
        body = json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
        yield path.name.split(".")[0], body.get("data") or []


OFFICIAL = {"TaiwanStockShareholding": "holding", "TaiwanStockPER": "per",
            "TaiwanStockMarginPurchaseShortSale": "margin", "TaiwanStockInstitutionalInvestorsBuySell": "insti",
            # 2026-10-15: the short-selling datasets are extended night by night too (the forward rules use them)
            "TaiwanDailyShortSaleBalances": "sbl", "TaiwanStockDayTrading": "daytrade",
            "TaiwanStockSecuritiesLending": "lending"}


def _add_official(base: Path, dataset: str, columns: dict[str, list]) -> int:
    """S9-W04 (2026-10-06): the exchanges' daily reports for the (day, code) pairs FinMind's files lack."""
    from quant_platform.research.history.chips_daily import cached_revenue_rows, cached_rows

    if dataset == "TaiwanStockMonthRevenue":
        rows = cached_revenue_rows(base)          # the month's first table that lists the company
    elif dataset in OFFICIAL:
        rows = cached_rows(base, OFFICIAL[dataset])
    else:
        return 0
    seen = set(zip(columns["date"], columns["code"], strict=True))
    added = 0
    for row in rows:
        if (row["date"], row["code"]) in seen:
            continue
        seen.add((row["date"], row["code"]))
        for name in columns:
            columns[name].append(row.get(name))
        added += 1
    return added


def replace_parquet(table, path: Path, tries: int = 60) -> None:
    """2026-10-16: written next to the file and moved over it, so a run reading the chip tables meanwhile sees the
    old or the new table, never half of one (pipelines no longer wait out the 21:30 update). Windows refuses the
    move while a reader holds the file open: tried again for up to a minute."""
    import os
    import time

    partial = path.with_suffix(".parquet.partial")
    pq.write_table(table, partial, compression="zstd")
    for attempt in range(tries):
        try:
            os.replace(partial, path)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(1)


def build(history: str | Path, job=None) -> dict[str, int]:
    """One Parquet per dataset with the columns the factors use; returns rows written. FinMind's download
    first, then the exchanges' daily reports for the days it does not have (research/history/chips_daily.py)."""
    base = Path(history)
    raw, out = base / "raw" / "finmind", base / "chips"
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    steps = list(FIELDS) + ["TaiwanStockInstitutionalInvestorsBuySell", "TaiwanStockMonthRevenue"]
    steps += [dataset for dataset in SHORT_FIELDS if (raw / dataset).is_dir()]      # once downloaded
    steps += [LENDING] if (raw / LENDING).is_dir() else []
    steps += [CORPORATE, STOCK_DIVIDENDS] if (raw / "TaiwanStockDividend").is_dir() else []
    if job:
        job.update(total=len(steps), done=0, force=True)
    for index, dataset in enumerate(steps):
        if job:
            job.update(done=index, current=dataset)
        columns: dict[str, list] = {"date": [], "code": []}
        fields = FIELDS.get(dataset) or SHORT_FIELDS.get(dataset)
        if fields:
            for name in fields:
                columns[name] = []
            for code, rows in _rows(raw, dataset):
                for row in rows:
                    columns["date"].append(date.fromisoformat(row["date"]))
                    columns["code"].append(code)
                    for name in fields:
                        value = row.get(name)
                        columns[name].append(float(value) if isinstance(value, (int, float)) else None)
        elif dataset == CORPORATE:            # one row per announcement: a dividend change or new shares
            columns.update({"dividend_change": [], "cash_issue_shares": []})
            policies = {code: rows for code, rows in _rows(raw, "TaiwanStockDividend")}
            for code, rows in _official_policies(base, out, policies).items():   # 2026-10-16: the nightly tables
                policies[code] = policies.get(code, []) + rows
            for code in sorted(policies):
                for day, change, shares in corporate_events(policies[code]):
                    columns["date"].append(day)
                    columns["code"].append(code)
                    columns["dividend_change"].append(change)
                    columns["cash_issue_shares"].append(shares)
        elif dataset == STOCK_DIVIDENDS:      # 2026-10-16: (ex-date, new shares per share held) per stock dividend
            columns.update({"ratio": []})
            for code, items in stock_dividends(base, raw).items():
                for ex_date, ratio in items:
                    columns["date"].append(ex_date)
                    columns["code"].append(code)
                    columns["ratio"].append(ratio)
        elif dataset == LENDING:              # one row per stock and day: lots lent, and lots × fee rate (%)
            columns.update({"LendingVolume": [], "FeeVolume": []})
            for code, rows in _rows(raw, dataset):
                days: dict[str, list[float]] = {}
                for row in rows:
                    lots, fee = row.get("volume"), row.get("fee_rate")
                    if not isinstance(lots, (int, float)) or lots <= 0 or not isinstance(fee, (int, float)):
                        continue
                    total = days.setdefault(row["date"], [0.0, 0.0])
                    total[0] += lots
                    total[1] += lots * fee
                for day in sorted(days):
                    columns["date"].append(date.fromisoformat(day))
                    columns["code"].append(code)
                    columns["LendingVolume"].append(days[day][0])
                    columns["FeeVolume"].append(days[day][1])
        elif dataset == "TaiwanStockInstitutionalInvestorsBuySell":
            columns.update({"foreign_net": [], "trust_net": [], "dealer_self_net": [], "dealer_hedge_net": []})
            for code, rows in _rows(raw, dataset):
                net: dict[str, list] = {}
                for row in rows:
                    who = row.get("name") or ""
                    slot = net.setdefault(row["date"], [0.0, 0.0, None, None])
                    amount = float(row.get("buy") or 0) - float(row.get("sell") or 0)
                    if who in ("Foreign_Investor", "Foreign_Dealer_Self"):
                        slot[0] += amount
                    elif who == "Investment_Trust":
                        slot[1] += amount
                    elif who == "Dealer_self":         # 2026-10-16: the dealers, split since 2014-12 ("Dealer"
                        slot[2] = (slot[2] or 0.0) + amount                      # before that is left out)
                    elif who == "Dealer_Hedging":
                        slot[3] = (slot[3] or 0.0) + amount
                for day, (foreign, trust, own, hedge) in net.items():
                    columns["date"].append(date.fromisoformat(day))
                    columns["code"].append(code)
                    columns["foreign_net"].append(foreign)
                    columns["trust_net"].append(trust)
                    columns["dealer_self_net"].append(own)
                    columns["dealer_hedge_net"].append(hedge)
        else:  # monthly revenue: the month it is for and the day it became public
            columns.update({"available": [], "revenue": [], "year": [], "month": []})
            for code, rows in _rows(raw, dataset):
                for row in rows:
                    year, month = int(row.get("revenue_year") or 0), int(row.get("revenue_month") or 0)
                    if not year or not month or row.get("revenue") is None:
                        continue
                    created = row.get("create_time")
                    following = date(year + (month == 12), month % 12 + 1, 10)
                    try:
                        public = date.fromisoformat(str(created)[:10]) if created else following
                    except ValueError:
                        public = following
                    columns["date"].append(date(year, month, 1))
                    columns["code"].append(code)
                    columns["available"].append(max(public, date(year, month, 28)))
                    columns["revenue"].append(float(row["revenue"]))
                    columns["year"].append(year)
                    columns["month"].append(month)
        _add_official(base, dataset, columns)
        table = pa.table(columns)
        replace_parquet(table, out / f"{dataset}.parquet")
        written[dataset] = table.num_rows
    if job:
        job.update(done=len(steps), force=True)
        job.payload["summary"] = "、".join(f"{name.removeprefix('TaiwanStock')} {rows:,} 列" for name, rows in written.items())
    return written


def _official_policies(base: Path, out: Path, policies: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """2026-10-16 (research/history/dividends_daily.py): the announcements in the exchanges' nightly ex-rights tables
    that FinMind's one-time download does not have (its ex-dates left out), as FinMind-shaped policy rows."""
    from quant_platform.research.history.dividends_daily import announcements, policy_rows

    official = announcements(base)
    if not official:
        return {}
    path = out / "TaiwanStockShareholding.parquet"
    table = (pq.read_table(path, columns=["date", "code", "NumberOfSharesIssued"]).to_pandas()
             if path.is_file() else pd.DataFrame(columns=["date", "code", "NumberOfSharesIssued"]))
    table = table[table["code"].isin(official)].dropna().sort_values("date")
    issued = {code: frame for code, frame in table.groupby("code")}

    def shares_of(code: str):
        def at(day: date) -> float | None:
            frame = issued.get(code)
            before = frame[frame["date"] <= day] if frame is not None else None
            return float(before["NumberOfSharesIssued"].iloc[-1]) if before is not None and len(before) else None
        return at

    output = {}
    for code, events in official.items():
        known = set()
        for row in policies.get(code, []):
            for name in ("CashExDividendTradingDate", "StockExDividendTradingDate"):
                try:
                    known.add(date.fromisoformat(str(row.get(name) or "")[:10]))
                except ValueError:
                    pass
        rows = policy_rows(events, known, shares_of(code))
        if rows:
            output[code] = rows
    return output


def stock_dividends(base: Path, raw: Path) -> dict[str, list[tuple[date, float]]]:
    """code -> [(ex-date, new shares per share held)]: FinMind's policies (par NT$10 a share), then the exchanges'
    nightly ex-rights tables for the ex-dates FinMind does not have."""
    from quant_platform.research.history.dividends_daily import announcements

    output: dict[str, dict[date, float]] = {}
    for code, rows in _rows(raw, "TaiwanStockDividend"):
        for row in rows:
            ratio = sum(float(row.get(name) or 0.0) for name in ("StockEarningsDistribution", "StockStatutorySurplus")) / 10
            try:
                ex_date = date.fromisoformat(str(row.get("StockExDividendTradingDate") or "")[:10])
            except ValueError:
                continue
            if ratio > 0:
                output.setdefault(code, {})[ex_date] = output.get(code, {}).get(ex_date, 0.0) + ratio
    for code, events in announcements(base).items():
        for event in events:
            if event.get("stock_ratio", 0.0) > 0 and event["ex_date"] not in output.get(code, {}):
                output.setdefault(code, {})[event["ex_date"]] = event["stock_ratio"]
    return {code: sorted(days.items()) for code, days in output.items()}


def corporate_events(rows: list[dict]) -> list[tuple[date, float | None, float | None]]:
    """(announcement date, change of the cash dividend per share against the payout announced 305–425 days before —
    None when there is none to compare with, new shares from a cash capital increase — None when none) per row."""
    def number(value) -> float:
        return float(value) if isinstance(value, (int, float)) else 0.0

    cash: list[tuple[date, float]] = []
    events: list[tuple[date, float | None, float | None]] = []
    for row in rows:
        try:
            day = date.fromisoformat(str(row.get("AnnouncementDate") or "")[:10])
        except ValueError:
            continue
        amount = number(row.get("CashEarningsDistribution")) + number(row.get("CashStatutorySurplus"))
        if amount > 0:
            cash.append((day, amount))
        shares = number(row.get("TotalNumberOfCashCapitalIncrease"))
        if shares > 0:
            events.append((day, None, shares))
    cash.sort()
    low, high = SAME_PERIOD_DAYS
    for day, amount in cash:
        earlier = [(abs((day - when).days - 365), paid) for when, paid in cash if low <= (day - when).days <= high]
        if earlier:
            events.append((day, amount - min(earlier)[1], None))
    return sorted(events, key=lambda event: event[0])


class ChipStore:
    """The chip and fundamental factors on a factor panel's sessions × stocks (symbols × sessions)."""

    def __init__(self, history: str | Path, sessions: list[date], symbols: list[str], close: np.ndarray,
                 until: date | None = None) -> None:
        self._folder = Path(history) / "chips"
        # 2026-10-11 (research/leakcheck.py): only what was public by the close of ``until`` — daily tables
        # dated before it, monthly revenue available before it; None reads everything
        self.until = until
        self.sessions, self.symbols = sessions, symbols
        self._codes = [symbol.split(".")[0] for symbol in symbols]
        self._close = close                                     # symbols × sessions, unadjusted
        self._frames: dict[str, pd.DataFrame] = {}

    def available(self) -> bool:
        return (self._folder / "TaiwanStockShareholding.parquet").is_file()

    def alerts_available(self) -> bool:
        return all((self._folder / name).is_file() for name in ("AlertNotice.parquet", "AlertDisposition.parquet"))

    def alert_matrix(self, factor: str) -> np.ndarray:
        """symbols × sessions, ALERT_FACTORS: minus the count of notices (20 sessions) or dispositions announced
        (60 sessions) dated before the session."""
        name, window = (("AlertNotice.parquet", 20) if factor == "low_attention_20" else ("AlertDisposition.parquet", 60))
        table = pq.read_table(self._folder / name, columns=["date", "code"]).to_pandas()
        if self.until is not None:
            table = table[pd.to_datetime(table["date"]) < pd.Timestamp(self.until)]
        table["count"] = 1.0
        frame = table.pivot_table(index="date", columns="code", values="count", aggfunc="sum")
        frame.index = pd.to_datetime(frame.index)
        sessions = pd.to_datetime(pd.Index(self.sessions))
        # an announcement on a closed day counts on the next session
        frame = frame.groupby(sessions[sessions.searchsorted(frame.index).clip(max=len(sessions) - 1)]).sum()
        frame = frame.reindex(sessions, fill_value=0.0).reindex(columns=self._codes, fill_value=0.0).fillna(0.0)
        counts = frame.shift(1, fill_value=0.0).rolling(window, min_periods=1).sum()   # dated before the session
        return np.ascontiguousarray((-counts).to_numpy(dtype=np.float32).T)

    def corporate_available(self) -> bool:
        return (self._folder / f"{CORPORATE}.parquet").is_file()

    def corporate_matrix(self, factor: str) -> np.ndarray:
        """symbols × sessions, CORPORATE_FACTORS: the latest announcement's value from the session after it for
        CORPORATE_SESSIONS sessions, 0 otherwise (once the data starts)."""
        field = "dividend_change" if factor == "dividend_raise" else "cash_issue_shares"
        table = pq.read_table(self._folder / f"{CORPORATE}.parquet").to_pandas()
        if self.until is not None:
            table = table[pd.to_datetime(table["date"]) < pd.Timestamp(self.until)]
        sessions = pd.to_datetime(pd.Index(self.sessions))
        if not len(table):
            return np.full((len(self._codes), len(sessions)), np.nan, dtype=np.float32)
        first = pd.to_datetime(table["date"]).min()
        table = table[table[field].notna()]
        # announced on day d (after its close, or on a holiday): used from the first session after d
        table = table.assign(at=sessions.searchsorted(pd.to_datetime(table["date"]).to_numpy(), side="right"))
        table = table[(table["at"] < len(sessions)) & table["code"].isin(self._codes)]
        # 2026-10-18: one column per code first — on the listed-and-TPEx panel a stock that moved from TPEx to the
        # exchange is two symbols with one code, and writing into repeated columns failed — then one per symbol
        frame = pd.DataFrame(np.nan, index=sessions, columns=list(dict.fromkeys(self._codes)))
        if len(table):
            values = table.groupby(["at", "code"])[field].sum().unstack()
            frame.loc[sessions[values.index], values.columns] = values.to_numpy()
        announced = frame.reindex(columns=self._codes).to_numpy(dtype=np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            if factor == "dividend_raise":       # NT$ per share against the close of the session it is used from
                close = self._close.T
                share = announced / np.where(close > 0, close, np.nan)
            else:                                 # new shares against the shares issued, reversed
                shares = self._daily("TaiwanStockShareholding", "NumberOfSharesIssued").to_numpy(dtype=np.float64)
                share = -announced / np.where(shares > 0, shares, np.nan)
        frame = pd.DataFrame(share, index=sessions).ffill(limit=CORPORATE_SESSIONS - 1).fillna(0.0)
        frame.loc[frame.index <= first] = np.nan  # before the first announcement nothing is known
        return np.ascontiguousarray(frame.to_numpy(dtype=np.float32).T)

    def pledge_available(self) -> bool:
        return (self._folder.parent / "pledge" / "ratios.parquet").is_file()

    def pledge_matrix(self, factor: str) -> np.ndarray:
        """symbols × sessions, PLEDGE_FACTORS: the latest usable month's percent (from its ``usable_from`` session on),
        reversed (low_pledge) or 1 when at least PLEDGE_HEAVY (pledge_heavy); NaN before the data and for a company
        not on the latest usable month's page."""
        table = pq.read_table(self._folder.parent / "pledge" / "ratios.parquet").to_pandas()
        if self.until is not None:
            table = table[pd.to_datetime(table["usable_from"]) <= pd.Timestamp(self.until)]
        sessions = pd.to_datetime(pd.Index(self.sessions))
        output = np.full((len(self._codes), len(sessions)), np.nan, dtype=np.float32)
        if not len(table):
            return output
        table = table.assign(at=sessions.searchsorted(pd.to_datetime(table["usable_from"]).to_numpy(), side="left"),
                             code=table["code"].astype(str))
        starts = sorted(set(table["at"]))
        column: dict[str, list[int]] = {}                 # a code may be two symbols (listed and TPEx)
        for index, code in enumerate(self._codes):
            column.setdefault(code, []).append(index)
        for number, start in enumerate(starts):
            if start >= len(sessions):
                continue
            end = starts[number + 1] if number + 1 < len(starts) else len(sessions)
            month = table[table["at"] == start].drop_duplicates("code", keep="last")
            values = month["percent"].to_numpy(dtype=np.float64)
            values = -values / 100 if factor == "low_pledge" else (values >= PLEDGE_HEAVY).astype(np.float64)
            for code, value in zip(month["code"], values, strict=True):
                for row in column.get(code, ()):
                    output[row, start:end] = value
        return output

    def holder_path(self) -> Path:
        return self._folder.parent / "tdcc" / "holding.parquet"

    def holder_available(self) -> bool:
        return self.holder_path().is_file()

    def holder_matrix(self, factor: str) -> np.ndarray:
        """symbols × sessions, HOLDER_FACTORS: each week's value from the first session HOLDER_DELAY_DAYS after its
        date until the next week's (at most HOLDER_HOLD sessions); a 4-week change against the table four weeks
        earlier. NaN before the data and for a code without the brackets."""
        table = pq.read_table(self.holder_path(), columns=["date", "code", "level", "holders", "percent"]).to_pandas()
        sessions = pd.to_datetime(pd.Index(self.sessions))
        output = np.full((len(self._codes), len(sessions)), np.nan, dtype=np.float32)
        table = table.assign(day=pd.to_datetime(table["date"].astype(str), format="%Y%m%d"), code=table["code"].astype(str))
        delay = pd.Timedelta(days=HOLDER_DELAY_DAYS)
        if self.until is not None:
            table = table[table["day"] + delay <= pd.Timestamp(self.until)]
        if not len(table):
            return output
        if factor in ("big_holder_pct", "big_holder_up_4w"):
            weekly = table[table["level"] == 15].pivot_table(index="day", columns="code", values="percent", aggfunc="last")
        elif factor == "retail_down_4w":
            weekly = table[table["level"].isin((1, 2, 3))].pivot_table(index="day", columns="code", values="percent",
                                                                        aggfunc="sum")
        else:
            weekly = table[table["level"] == 17].pivot_table(index="day", columns="code", values="holders", aggfunc="last")
        weekly = weekly.reindex(sorted(table["day"].unique())).astype(float)     # every week a row: shift(4) = 4 weeks
        if factor == "big_holder_pct":
            weekly = weekly / 100
        elif factor == "big_holder_up_4w":
            weekly = (weekly - weekly.shift(4)) / 100
        elif factor == "retail_down_4w":
            weekly = -(weekly - weekly.shift(4)) / 100
        else:
            earlier = weekly.shift(4)
            weekly = -(weekly / earlier.where(earlier > 0) - 1)
        at = sessions.searchsorted((weekly.index + delay).to_numpy(), side="left")
        weekly = weekly[at < len(sessions)]
        weekly.index = at[at < len(sessions)]
        frame = weekly.groupby(level=0).last().reindex(range(len(sessions))).ffill(limit=HOLDER_HOLD)
        frame = frame.reindex(columns=list(dict.fromkeys(self._codes)))   # a code may be two symbols
        where = {code: number for number, code in enumerate(frame.columns)}
        values = frame.to_numpy(dtype=np.float32)
        return np.ascontiguousarray(values[:, [where[code] for code in self._codes]].T)

    def paid_available(self, factor: str) -> bool:
        return (self._folder.parent / "paid" / f"{PAID_SOURCES[factor]}.parquet").is_file()

    def _paid(self, dataset: str, field: str, fill: int = 10, zero: bool = False) -> pd.DataFrame:
        """sessions × codes from history/paid: the value known on each session (the day before's table), carried
        up to ``fill`` sessions over gaps; ``zero``: a session without a row is 0 once the data starts."""
        key = f"paid:{dataset}:{field}:{zero}"
        if key not in self._frames:
            table = pq.read_table(self._folder.parent / "paid" / f"{dataset}.parquet",
                                  columns=["date", "code", field]).to_pandas()
            if self.until is not None:
                table = table[pd.to_datetime(table["date"]) < pd.Timestamp(self.until)]
            frame = table.pivot_table(index="date", columns="code", values=field, aggfunc="last")
            frame.index = pd.to_datetime(frame.index)
            sessions = pd.to_datetime(pd.Index(self.sessions))
            if zero:
                first = frame.index.min() if len(frame) else None
                frame = frame.reindex(sessions).fillna(0.0)
                if first is not None:
                    frame.loc[frame.index < first] = np.nan
            elif fill:
                frame = frame.reindex(sessions.union(frame.index)).ffill(limit=fill).reindex(sessions)
            else:
                frame = frame.reindex(sessions)
            self._frames[key] = frame.reindex(columns=self._codes).shift(1)       # after the close: next session
        return self._frames[key]

    def paid_matrix(self, factor: str) -> np.ndarray:
        """symbols × sessions, PAID_FACTORS."""
        dataset = PAID_SOURCES[factor]
        if factor == "margin_safe":
            ratio = self._paid(dataset, "margin_maintenance")
            frame = ratio.where(ratio > 0)
        elif factor == "margin_in_profit":
            cost = self._paid(dataset, "margin_cost")
            close = pd.DataFrame(self._close.T, index=cost.index, columns=cost.columns).shift(1)
            frame = close / cost.where(cost > 0) - 1
        elif factor == "low_pledge_loan_20":
            balance = self._paid(dataset, "UnrestrictedLoanCurrentDayBalance")
            shares = self._daily("TaiwanStockShareholding", "NumberOfSharesIssued")
            frame = -(balance - balance.shift(20)) * 1000 / shares.where(shares > 0)
        elif factor in ("block_premium_20", "block_volume_20"):
            volume = self._paid(dataset, "volume", zero=True)
            if factor == "block_volume_20":
                shares = self._daily("TaiwanStockShareholding", "NumberOfSharesIssued")
                frame = volume.rolling(20, min_periods=20).sum() / shares.where(shares > 0)
            else:
                money = self._paid(dataset, "trading_money", zero=True)
                close = pd.DataFrame(self._close.T, index=volume.index, columns=volume.columns).shift(1)
                # each day's block shares at their average price against that day's close, summed over 20 sessions
                gap = (money - volume * close).rolling(20, min_periods=20).sum()
                worth = (volume * close).rolling(20, min_periods=20).sum()
                frame = gap / worth.where(worth > 0)
        elif factor == "broker_concentration_5":
            bought = self._paid(dataset, "top_buy_volume", fill=0).rolling(5, min_periods=5).sum()
            sold = self._paid(dataset, "top_sell_volume", fill=0).rolling(5, min_periods=5).sum()
            frame = (bought - sold) / (bought + sold).where(bought + sold > 0)
        else:
            raise ValueError(factor)
        return np.ascontiguousarray(frame.to_numpy(dtype=np.float32).T)

    def disclosure_path(self, factor: str) -> Path:
        history = self._folder.parent
        return history / ("insider/transfers.parquet" if factor == "low_insider_sell_60" else "buyback/programs.parquet")

    def disclosure_available(self, factor: str) -> bool:
        return self.disclosure_path(factor).is_file()

    def disclosure_matrix(self, factor: str) -> np.ndarray:
        """symbols × sessions, DISCLOSURE_FACTORS: the shares announced over the last DISCLOSURE_SESSIONS sessions
        (each from the session after its date) against the shares issued; insiders' sales reversed. NaN before the
        data starts."""
        from quant_platform.research.history.insider import MARKET_METHODS

        table = pq.read_table(self.disclosure_path(factor)).to_pandas()
        dated = table["notice_date" if factor == "low_insider_sell_60" else "board_date"].dropna()
        if self.until is not None:
            dated = dated[pd.to_datetime(dated) < pd.Timestamp(self.until)]
        first = pd.to_datetime(dated).min() if len(dated) else None     # the data's start, every kind of notice
        if factor == "low_insider_sell_60":
            table = table[table["method"].astype(str).str.contains("|".join(MARKET_METHODS))]
            table = table.assign(when=table["notice_date"],
                                 amount=table["total_own"].fillna(0.0) + table["total_trust"].fillna(0.0))
        else:
            table = table.assign(when=table["board_date"], amount=table["planned_shares"].fillna(0.0))
        table = table[table["when"].notna()]
        if self.until is not None:
            table = table[pd.to_datetime(table["when"]) < pd.Timestamp(self.until)]
        sessions = pd.to_datetime(pd.Index(self.sessions))
        if first is None:
            return np.full((len(self._codes), len(sessions)), np.nan, dtype=np.float32)
        table = table.assign(at=sessions.searchsorted(pd.to_datetime(table["when"]).to_numpy(), side="right"),
                             code=table["code"].astype(str))
        table = table[(table["at"] < len(sessions)) & table["code"].isin(self._codes)]
        frame = pd.DataFrame(0.0, index=sessions, columns=list(dict.fromkeys(self._codes)))
        if len(table):
            values = table.groupby(["at", "code"])["amount"].sum().unstack(fill_value=0.0)
            frame.loc[sessions[values.index], values.columns] = values.to_numpy()
        window = frame.rolling(DISCLOSURE_SESSIONS, min_periods=1).sum().reindex(columns=self._codes)
        shares = self._daily("TaiwanStockShareholding", "NumberOfSharesIssued").to_numpy(dtype=np.float64)
        with np.errstate(invalid="ignore", divide="ignore"):
            share = window.to_numpy(dtype=np.float64) / np.where(shares > 0, shares, np.nan)
        if factor == "low_insider_sell_60":
            share = -share
        result = pd.DataFrame(share, index=sessions)
        result.loc[result.index <= first] = np.nan        # before the first announcement nothing is known
        return np.ascontiguousarray(result.to_numpy(dtype=np.float32).T)

    def dealer_available(self) -> bool:
        path = self._folder / "TaiwanStockInstitutionalInvestorsBuySell.parquet"
        return path.is_file() and "dealer_self_net" in pq.read_schema(path).names

    def issuance_available(self) -> bool:
        return (self._folder / f"{STOCK_DIVIDENDS}.parquet").is_file()

    def issuance_matrix(self) -> np.ndarray:
        """symbols × sessions, ``low_issuance_250``: minus the log growth of the shares issued over 250 sessions from
        rises only, a stock dividend's listing taken out (dated the day before, like the shares)."""
        shares = self._daily("TaiwanStockShareholding", "NumberOfSharesIssued")
        jumps = np.log(shares.where(shares > 0)).diff()
        table = pq.read_table(self._folder / f"{STOCK_DIVIDENDS}.parquet").to_pandas()
        if self.until is not None:
            table = table[pd.to_datetime(table["date"]) < pd.Timestamp(self.until)]
        sessions = pd.to_datetime(pd.Index(self.sessions))
        values = jumps.to_numpy(copy=True)
        column = {code: index for index, code in enumerate(jumps.columns)}
        for code, ex_date, ratio in table[["code", "date", "ratio"]].itertuples(index=False):
            if code not in column or ratio <= 0:
                continue
            target = float(np.log1p(ratio))
            first = int(sessions.searchsorted(pd.Timestamp(ex_date)))
            series = values[:, column[code]]
            for index in range(first, min(first + DIVIDEND_LISTING_SESSIONS, len(series))):
                if np.isfinite(series[index]) and 0.5 * target <= series[index] <= 1.5 * target:
                    series[index] = 0.0            # the dividend's new shares listed: not issuance
                    break
        rises = pd.DataFrame(np.clip(values, 0.0, None), index=jumps.index, columns=jumps.columns)
        known = shares.notna()
        issued = rises.fillna(0.0).rolling(250, min_periods=250).sum().where(known)
        return np.ascontiguousarray((-issued).to_numpy(dtype=np.float32).T)

    def lending_available(self) -> bool:
        return (self._folder / f"{LENDING}.parquet").is_file()

    def lending_matrix(self, factor: str) -> np.ndarray:
        """symbols × sessions, LENDING_FACTORS (dated the day before)."""
        lots, weighted = self._lending()

        def fee(start: int, length: int) -> pd.DataFrame:     # lots-weighted fee of sessions t-start-length+1 .. t-start
            lent = lots.shift(start).rolling(length, min_periods=length).sum()
            return weighted.shift(start).rolling(length, min_periods=length).sum() / lent.where(lent > 0)

        if factor == "low_lending_fee_60":
            frame = -fee(0, 60)
        elif factor == "low_lending_20":
            shares = self._daily("TaiwanStockShareholding", "NumberOfSharesIssued")
            frame = -(lots.rolling(20, min_periods=20).sum() * 1000 / shares.where(shares > 0))
        elif factor == "lending_fee_not_rising":
            frame = -(fee(0, 20) - fee(20, 60))
        else:
            raise ValueError(factor)
        return np.ascontiguousarray(frame.to_numpy(dtype=np.float32).T)

    def _lending(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        """sessions × codes: lots lent and lots × fee each session — 0 without a deal once the data starts —
        dated the day before."""
        if "lending" not in self._frames:
            table = pq.read_table(self._folder / f"{LENDING}.parquet").to_pandas()
            if self.until is not None:
                table = table[pd.to_datetime(table["date"]) < pd.Timestamp(self.until)]
            sessions = pd.to_datetime(pd.Index(self.sessions))
            first = pd.to_datetime(table["date"]).min()
            frames = []
            for field in ("LendingVolume", "FeeVolume"):
                frame = table.pivot_table(index="date", columns="code", values=field, aggfunc="sum")
                frame.index = pd.to_datetime(frame.index)
                frame = frame.reindex(sessions).reindex(columns=self._codes).fillna(0.0)
                frame.loc[frame.index < first] = np.nan
                frames.append(frame.shift(1))                       # published after the close: next session
            self._frames["lending"] = frames
        return self._frames["lending"]

    def short_available(self) -> bool:
        return all((self._folder / f"{dataset}.parquet").is_file() for dataset in SHORT_FIELDS)

    def short_matrix(self, factor: str, turnover: np.ndarray) -> np.ndarray:
        """symbols × sessions, SHORT_FACTORS; ``turnover`` (symbols × sessions, NT$) for the day-trading share."""
        if factor in ("low_sbl_short", "sbl_cover_20"):
            shares = self._daily("TaiwanStockShareholding", "NumberOfSharesIssued")
            balance = self._daily("TaiwanDailyShortSaleBalances", "SBLShortSalesCurrentDayBalance")
            ratio = balance / shares.where(shares > 0)
            frame = -ratio if factor == "low_sbl_short" else -(ratio - ratio.shift(20))
        elif factor == "low_day_trade_20":
            bought = self._daily("TaiwanStockDayTrading", "BuyAmount")             # dated the day before
            value = pd.DataFrame(turnover.T, index=bought.index, columns=bought.columns).shift(1)
            frame = -(bought.rolling(20, min_periods=10).sum() / value.rolling(20, min_periods=10).sum().where(lambda v: v > 0))
        else:
            raise ValueError(factor)
        return np.ascontiguousarray(frame.to_numpy(dtype=np.float32).T)

    def _daily(self, dataset: str, field: str) -> pd.DataFrame:
        """sessions × codes, the value known on each session (dated the day before), carried forward
        up to 10 sessions over gaps."""
        key = f"{dataset}:{field}"
        if key not in self._frames:
            table = pq.read_table(self._folder / f"{dataset}.parquet", columns=["date", "code", field]).to_pandas()
            if self.until is not None:
                table = table[pd.to_datetime(table["date"]) < pd.Timestamp(self.until)]
            frame = table.pivot_table(index="date", columns="code", values=field, aggfunc="last")
            frame.index = pd.to_datetime(frame.index)
            sessions = pd.to_datetime(pd.Index(self.sessions))
            frame = frame.reindex(sessions.union(frame.index)).ffill(limit=10).reindex(sessions)
            frame = frame.reindex(columns=self._codes)
            self._frames[key] = frame.shift(1)                  # published after the close: next session
        return self._frames[key]

    def _flow(self, field: str, window: int) -> pd.DataFrame:
        table = pq.read_table(self._folder / "TaiwanStockInstitutionalInvestorsBuySell.parquet",
                              columns=["date", "code", field]).to_pandas()
        if self.until is not None:
            table = table[pd.to_datetime(table["date"]) < pd.Timestamp(self.until)]
        frame = table.pivot_table(index="date", columns="code", values=field, aggfunc="sum")
        frame.index = pd.to_datetime(frame.index)
        sessions = pd.to_datetime(pd.Index(self.sessions))
        frame = frame.reindex(sessions).reindex(columns=self._codes)
        # a session without a record is zero net buying once the data starts (2012-05)
        started = frame.notna().cumsum() > 0
        frame = frame.fillna(0).where(started)
        return frame.rolling(window, min_periods=window).sum().shift(1)

    def _revenue(self, accel: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Latest month's revenue year-on-year growth and the 3-month sum's, as known on each session; with
        ``accel`` the latest growth minus the previous three months' average, for ACCEL_SESSIONS after it
        is announced (first element)."""
        table = pq.read_table(self._folder / "TaiwanStockMonthRevenue.parquet").to_pandas()
        if self.until is not None:
            table = table[pd.to_datetime(table["available"]) < pd.Timestamp(self.until)]
        table = table.sort_values(["code", "date"])
        table["key"] = table["year"] * 12 + table["month"]
        by_key = table.set_index(["code", "key"])["revenue"]
        previous = by_key.reindex(list(zip(table["code"], table["key"] - 12))).to_numpy()
        table["yoy"] = table["revenue"].to_numpy() / previous - 1
        three = table.groupby("code")["revenue"].transform(lambda values: values.rolling(3).sum())
        table["three"] = three
        three_by_key = table.set_index(["code", "key"])["three"]
        previous_three = three_by_key.reindex(list(zip(table["code"], table["key"] - 12))).to_numpy()
        table["yoy3"] = table["three"].to_numpy() / previous_three - 1
        sessions = pd.to_datetime(pd.Index(self.sessions))
        columns = [("yoy", 70), ("yoy3", 70)]
        if accel:
            yoy_by_key = table.set_index(["code", "key"])["yoy"]
            earlier = np.column_stack([yoy_by_key.reindex(list(zip(table["code"], table["key"] - lag))).to_numpy()
                                       for lag in (1, 2, 3)])
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)       # no earlier month known: unknown
                table["accel"] = table["yoy"].to_numpy() - np.nanmean(earlier, axis=1)
            columns = [("accel", ACCEL_SESSIONS)]
        outputs = []
        for column, limit in columns:
            frame = table.pivot_table(index="available", columns="code", values=column, aggfunc="last")
            frame.index = pd.to_datetime(frame.index) + pd.Timedelta(days=1)   # usable the day after
            frame = frame.reindex(sessions.union(frame.index)).ffill(limit=limit).reindex(sessions)
            outputs.append(frame.reindex(columns=self._codes))
        return (outputs[0], outputs[0]) if accel else (outputs[0], outputs[1])

    def matrix(self, factor: str) -> np.ndarray:
        """symbols × sessions."""
        if factor == "foreign_holding":
            frame = self._daily("TaiwanStockShareholding", "ForeignInvestmentSharesRatio")
        elif factor == "foreign_holding_change":
            level = self._daily("TaiwanStockShareholding", "ForeignInvestmentSharesRatio")
            frame = level - level.shift(20)
        elif factor in ("foreign_buy_20", "trust_buy_20", "dealer_buy_20", "dealer_hedge_20"):
            shares = self._daily("TaiwanStockShareholding", "NumberOfSharesIssued")
            field = {"foreign_buy_20": "foreign_net", "trust_buy_20": "trust_net", "dealer_buy_20": "dealer_self_net",
                     "dealer_hedge_20": "dealer_hedge_net"}[factor]
            flow = self._flow(field, 20)
            frame = flow / shares.where(shares > 0)
        elif factor == "margin_growth_20":
            balance = self._daily("TaiwanStockMarginPurchaseShortSale", "MarginPurchaseTodayBalance")
            frame = balance / balance.shift(20).where(balance.shift(20) > 0) - 1
        elif factor == "short_margin_ratio":
            margin = self._daily("TaiwanStockMarginPurchaseShortSale", "MarginPurchaseTodayBalance")
            short = self._daily("TaiwanStockMarginPurchaseShortSale", "ShortSaleTodayBalance")
            frame = short / margin.where(margin > 0)
        elif factor == "earnings_yield":
            per = self._daily("TaiwanStockPER", "PER")
            frame = 1 / per.where(per > 0)
        elif factor == "book_to_price":
            pbr = self._daily("TaiwanStockPER", "PBR")
            frame = 1 / pbr.where(pbr > 0)
        elif factor in ("revenue_yoy", "revenue_yoy_3m"):
            latest, three = self._revenue()
            frame = latest if factor == "revenue_yoy" else three
        elif factor == "revenue_accel":
            frame = self._revenue(accel=True)[0]
        elif factor == "market_cap":
            shares = self._daily("TaiwanStockShareholding", "NumberOfSharesIssued")
            close = pd.DataFrame(self._close.T, index=shares.index, columns=shares.columns)
            frame = np.log((shares * close.shift(1)).where(shares > 0))
        else:
            raise ValueError(factor)
        return np.ascontiguousarray(frame.to_numpy(dtype=np.float32).T)

