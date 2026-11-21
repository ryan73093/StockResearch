"""2026-10-22 (使用者：還有什麼要加入分析的; 0050 大跌時策略不要跌更多): do market-wide signals the platform already
has foresee 0050's next months — a diagnostic written before the run (research_method §7「大盤訊號」).

Signals, each known only from the session after its date: the whole market's margin maintenance ratio (FinMind
TaiwanTotalExchangeMarginMaintenance, from 2002) and its 20-session change; the foreign investors' net open interest
in TAIEX futures (TaiwanFuturesInstitutionalInvestors TX, long minus short contracts, 2018-06 to the download) and its
20-session change. Each day's value is placed by its percentile among every earlier value (no later data), cut in
fifths. Outcomes from 0050's total return index: the next 20 and 60 sessions, and whether 0050 falls 10% or more from
that day's level within 60 sessions.

Useful (the criterion written first) when in both 2015-06..2020-09 and 2020-10..2026-08 the worst fifth's next-60
return is at least ``GAP`` below the period's average and its 10%-fall rate at least ``RATE`` times the average.

2026-10-22 (八大行庫): the eight government banks' net buying over the whole listed market, as a share of the market's
turnover over 5 and 20 sessions (FinMind TaiwanStockGovernmentBankBuySell from 2021-06-30, history/paid). Known from
2021-07, its percentiles from 2022-07: judged on SHORT_PERIODS (2022-07..2023-12 and 2024-01..2026-08) by the same
criterion — written before the run (research_method §7「八大行庫」).

2026-11-21 (使用者：有把美股的資料也放進來一起跑嗎？美股有很多指標): the US and international daily series the main
database has held since 2020-01 and no research had read (Yahoo: ^VIX, ^SOX, ^IXIC, ^GSPC, TLT, TWD=X). A US session
dated D closes at 05:00 Taipei on D+1, so it is used from the Taiwan session after its date, as every signal here.
Percentiles from 2021-01; judged on US_PERIODS by the same criterion — written before the run (docs/research/results.md
「美股與國際指標能不能提早看出 0050 大跌」).

2026-11-21 (使用者：還有各種就業數據、通膨數據、利率數據，各種的指標——這些都沒有): the US statistics and market
series of ``history/us.py`` — employment, prices, rates, credit, liquidity, activity, and the TSMC ADR's premium. A
revised statistic's reading is computed from each vintage as published and dated by the vintage; every reading is
carried to each weekday (so that percentiles count days, not releases) and used from the Taiwan session after its
date. Judged on PERIODS, and the worst fifth must sit at the same end in both — written before the run
(docs/research/results.md「美股個股、美國就業／通膨／利率／景氣數據」)."""

from __future__ import annotations

import gzip
import json
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
PERIODS = {"2015-06～2020-09": (date(2015, 6, 1), date(2020, 9, 30)), "2020-10～2026-08": (date(2020, 10, 1), date(2026, 8, 31))}
SHORT_PERIODS = {"2022-07～2023-12": (date(2022, 7, 1), date(2023, 12, 31)), "2024-01～2026-08": (date(2024, 1, 1), date(2026, 8, 31))}
GOVBANK = "八大行庫淨買超"
US = "美股："
US_PERIODS = {"2021-01～2023-12": (date(2021, 1, 1), date(2023, 12, 31)), "2024-01～2026-08": (date(2024, 1, 1), date(2026, 8, 31))}
MACRO = "美國數據："
HORIZONS = (20, 60)
FALL = 0.10
GAP, RATE = 0.03, 1.5


def _rows(path: Path) -> list[dict]:
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8")).get("data") or [] if path.is_file() else []


def series(history: str | Path) -> dict[str, dict[date, float]]:
    """date → value of each signal (on the date the data is about; used from the next session)."""
    raw = Path(history) / "raw" / "finmind"
    margin = {date.fromisoformat(row["date"][:10]): float(row["TotalExchangeMarginMaintenance"])
              for row in _rows(raw / "TaiwanTotalExchangeMarginMaintenance" / "all.json.gz")
              if row.get("TotalExchangeMarginMaintenance")}
    futures: dict[date, float] = {}
    for row in _rows(raw / "TaiwanFuturesInstitutionalInvestors" / "TX.json.gz"):
        if row.get("institutional_investors") == "外資":
            futures[date.fromisoformat(row["date"][:10])] = float(row["long_open_interest_balance_volume"]) - float(
                row["short_open_interest_balance_volume"])
    return {"融資維持率": margin, "外資台指期淨部位": futures}


def govbank(history: str | Path, sessions: int) -> dict[date, float]:
    """date → the eight banks' net buying over the last ``sessions`` sessions as a share of the listed market's
    turnover over them (none before the table)."""
    import pandas as pd

    history = Path(history)
    path = history / "paid" / "TaiwanStockGovernmentBankBuySell.parquet"
    if not path.is_file():
        return {}
    table = pd.read_parquet(path, columns=["date", "buy_amount", "sell_amount"])
    net = (table["buy_amount"] - table["sell_amount"]).groupby(pd.to_datetime(table["date"])).sum()
    if not len(net):
        return {}
    turnover = []
    for file in sorted((history / "stocks" / "twse").glob("*.parquet")):
        if int(file.stem) >= net.index.min().year:
            frame = pd.read_parquet(file, columns=["date", "turnover"])
            turnover.append(frame.groupby(pd.to_datetime(frame["date"]))["turnover"].sum())
    if not turnover:
        return {}
    market = pd.concat(turnover).groupby(level=0).sum()
    market = market[market.index >= net.index.min()]
    net = net.reindex(market.index).fillna(0.0)
    share = net.rolling(sessions, min_periods=sessions).sum() / market.rolling(sessions, min_periods=sessions).sum()
    return {day.date(): float(value) for day, value in share.dropna().items()}


def us_series(database: str | Path) -> dict[str, dict[date, float]]:
    """US session date → value, from the main database's daily bars (read-only): the VIX and its 5-session change, the
    semiconductor index's 20-session return and its distance from its 200-session average, the Nasdaq's 20-session
    return, the S&P 500 against its 200-session average, the long Treasury ETF's 20-session return, and the 20-session
    change of the US dollar in Taiwan dollars. {} without the database."""
    import sqlite3

    path = Path(database)
    if not path.is_file():
        return {}
    wanted = ("^VIX", "^SOX", "^IXIC", "^GSPC", "TLT", "TWD=X")
    closes: dict[str, dict[date, float]] = {symbol: {} for symbol in wanted}
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "select symbol, event_time, coalesce(adjusted_close, close) from market_bars where market = 'US' and "
            f"interval = '1d' and symbol in ({','.join('?' * len(wanted))})", wanted).fetchall()
    for symbol, when, close in rows:
        if close is not None and float(close) > 0:
            closes[symbol][date.fromisoformat(str(when)[:10])] = float(close)

    def moved(values: dict[date, float], sessions: int) -> dict[date, float]:
        days = sorted(values)
        return {day: values[day] / values[days[index - sessions]] - 1 for index, day in enumerate(days) if index >= sessions}

    def above(values: dict[date, float], sessions: int = 200) -> dict[date, float]:
        days = sorted(values)
        levels = np.array([values[day] for day in days])
        return {day: float(levels[index] / levels[index - sessions + 1:index + 1].mean() - 1)
                for index, day in enumerate(days) if index >= sessions - 1}

    output = {f"{US}VIX（水準）": closes["^VIX"], f"{US}VIX（5 日變化）": _changes(closes["^VIX"], 5),
              f"{US}費城半導體（20 日報酬）": moved(closes["^SOX"], 20), f"{US}費城半導體（對 200 日均線）": above(closes["^SOX"]),
              f"{US}那斯達克（20 日報酬）": moved(closes["^IXIC"], 20), f"{US}S&P 500（對 200 日均線）": above(closes["^GSPC"]),
              f"{US}美國長債 TLT（20 日報酬）": moved(closes["TLT"], 20), f"{US}美元兌台幣（20 日變化）": moved(closes["TWD=X"], 20)}
    return {name: values for name, values in output.items() if values}


def _month(observation: str, back: int) -> str:
    year, month = int(observation[:4]), int(observation[5:7]) - back
    while month < 1:
        year, month = year - 1, month + 12
    return f"{year:04d}-{month:02d}-01"


def _yearly(known: dict[str, float], at: str | None = None) -> float | None:
    """The latest month (or ``at``) against the same month a year before, as published in one vintage."""
    at = at or (max(known) if known else None)
    if at is None or at not in known or _month(at, 12) not in known or not known[_month(at, 12)]:
        return None
    return known[at] / known[_month(at, 12)] - 1


def _steps(points: dict[date, float], until: date) -> dict[date, float]:
    """A value dated on some days, carried to every weekday up to ``until`` (the next value replaces it)."""
    output: dict[date, float] = {}
    days = sorted(points)
    for index, day in enumerate(days):
        end = days[index + 1] if index + 1 < len(days) else until + timedelta(days=1)
        cursor = day
        while cursor < end:
            if cursor.weekday() < 5:
                output[cursor] = points[day]
            cursor += timedelta(days=1)
    return output


def _vintage_reading(vintages: dict[str, dict[str, float]], reading) -> dict[date, float]:
    output = {}
    for vintage in sorted(vintages):
        value = reading(vintages[vintage])
        if value is not None and np.isfinite(value):
            output[date.fromisoformat(vintage)] = float(value)
    return output


def _sahm(known: dict[str, float]) -> float | None:
    """The 3-month average unemployment rate above its lowest of the 12 months before (Sahm's reading)."""
    months = sorted(known)
    if len(months) < 15:
        return None
    averages = [float(np.mean([known[month] for month in months[index - 2:index + 1]])) for index in range(2, len(months))]
    return averages[-1] - min(averages[-13:-1])


def _last_change(known: dict[str, float], back: int, average: bool = False) -> float | None:
    months = sorted(known)
    if len(months) <= back:
        return None
    change = known[months[-1]] - known[months[-1 - back]]
    return change / back if average else change


def _claims(known: dict[str, float]) -> float | None:
    weeks = sorted(known)
    if len(weeks) < 26:
        return None
    return float(np.mean([known[week] for week in weeks[-4:]]) / np.mean([known[week] for week in weeks[-26:]]) - 1)


def _yearly_change(known: dict[str, float], back: int = 3) -> float | None:
    now, then = _yearly(known), _yearly(known, _month(max(known), back)) if known else None
    return None if now is None or then is None else now - then


def us_macro(history: str | Path) -> dict[str, dict[date, float]]:
    """name → {date the reading is published: value}, each carried to every weekday. {} without the files."""
    import pandas as pd

    from quant_platform.research.history import us

    history = Path(history)
    revised, market, stocks = us.revised(history), us.daily(history), us.prices(history)
    until = datetime.now(TAIPEI).date()
    output: dict[str, dict[date, float]] = {}

    def keep(name: str, points: dict[date, float]) -> None:
        if points:
            output[f"{MACRO}{name}"] = _steps(points, until)

    yearly = {"JTSJOL": "職缺數（年增率）", "CES0500000003": "平均時薪（年增率）", "CPIAUCSL": "消費者物價（年增率）",
              "PCEPILFE": "核心 PCE 物價（年增率）", "PPIFIS": "生產者物價（年增率）", "INDPRO": "工業生產（年增率）",
              "IPG3344S": "半導體與電子零組件生產（年增率）", "RSAFS": "零售銷售（年增率）", "NEWORDER": "核心資本財訂單（年增率）",
              "A34SNO": "電腦與電子產品新訂單（年增率）", "PERMIT": "營建許可（年增率）"}
    for series, name in yearly.items():
        if series in revised:
            keep(name, _vintage_reading(revised[series], _yearly))
    special = {"UNRATE": ("失業率（3 個月平均比前 12 個月低點高多少）", _sahm),
               "PAYEMS": ("非農就業（3 個月平均增加）", lambda known: _last_change(known, 3, average=True)),
               "ICSA": ("初領失業金（4 週對 26 週）", _claims),
               "CPIAUCSL": ("消費者物價年增率（3 個月變化）", _yearly_change),
               "MICH": ("密大一年通膨預期（水準）", lambda known: known[max(known)] if known else None),
               "UMCSENT": ("密大消費者信心（3 個月變化）", lambda known: _last_change(known, 3))}
    for series, (name, reading) in special.items():
        if series in revised:
            keep(name, _vintage_reading(revised[series], reading))

    def moved(values: dict[date, float], sessions: int) -> dict[date, float]:
        days = sorted(values)
        return {day: values[day] / values[days[index - sessions]] - 1 for index, day in enumerate(days)
                if index >= sessions and values[days[index - sessions]]}

    def spread(left: str, right: str) -> dict[date, float]:
        return {day: market[left][day] - market[right][day] for day in market.get(left, {}) if day in market.get(right, {})}

    for series, sessions, name in (("DGS10", 60, "10 年期殖利率（60 日變化）"), ("DGS2", 60, "2 年期殖利率（60 日變化）"),
                                   ("DFII10", 60, "10 年期實質殖利率（60 日變化）"), ("DFF", 126, "聯邦基金利率（126 日變化）"),
                                   ("T5YIE", 60, "5 年期損益兩平通膨率（60 日變化）"), ("BAMLH0A0HYM2", 20, "高收益債利差（20 日變化）")):
        if series in market:
            keep(name, _changes(market[series], sessions))
    keep("10 年減 3 個月殖利率（水準）", spread("DGS10", "DGS3MO"))
    keep("10 年減 2 年殖利率（水準）", spread("DGS10", "DGS2"))
    for series, name in (("BAMLH0A0HYM2", "高收益債利差（水準）"), ("NFCI", "金融狀況指數（水準）")):
        if series in market:
            keep(name, market[series])
    for series, sessions, name in (("DTWEXBGS", 60, "美元廣義指數（60 日變化）"), ("DCOILWTICO", 60, "西德州原油（60 日變化）"),
                                   ("WALCL", 13, "聯準會資產負債表（13 週變化）")):
        if series in market:
            keep(name, moved(market[series], sessions))
    # the TSMC ADR's premium: one ADR is five shares; US session D against the Taiwan close of the same date
    adr, dollar = stocks.get("TSM") or {}, stocks.get("TWD=X") or {}
    local: dict[date, float] = {}
    if adr and dollar:
        for file in sorted((history / "stocks" / "twse").glob("*.parquet")):
            if int(file.stem) >= min(adr).year:
                frame = pd.read_parquet(file, columns=["date", "code", "close"])
                frame = frame[frame["code"] == "2330"]
                local.update({value.date(): float(close) for value, close in zip(pd.to_datetime(frame["date"]), frame["close"])
                              if close and close > 0})
    premium = {day: adr[day]["close"] * dollar[day]["close"] / 5 / local[day] - 1
               for day in adr if day in dollar and day in local}
    if premium:
        keep("台積電 ADR 溢價（水準）", premium)
        keep("台積電 ADR 溢價（5 日變化）", _changes(premium, 5))
    return output


# 2026-11-22 (docs/research/results.md「空頭防護第十四版」, written before the run): the five readings the study found
# useful at their highest fifth - one story, the US economy overheating
HOT_READINGS = ("核心資本財訂單（年增率）", "零售銷售（年增率）", "工業生產（年增率）", "職缺數（年增率）", "2 年期殖利率（60 日變化）")
HOT_SHARE = 0.8                      # a reading is high at or above this share of its own earlier values
HOT_COUNT = 3                        # overheating: more than half of the five are high


def us_hot(history: str | Path, sessions: list[date]) -> dict[date, int]:
    """Taiwan session → how many of HOT_READINGS are high, as known before it (the latest reading dated before the
    session, against its own earlier values: ``percentiles``). A session with no reading known yet is left out."""
    macro = us_macro(history)
    output: dict[date, int] = {}
    for name in HOT_READINGS:
        values = macro.get(f"{MACRO}{name}") or {}
        ranks = percentiles(values)
        known = sorted(ranks)
        for day in sessions:
            before = bisect_left(known, day) - 1               # dated before the session
            if before >= 0:
                output[day] = output.get(day, 0) + int(ranks[known[before]] >= HOT_SHARE)
    return output


def _changes(values: dict[date, float], sessions: int = 20) -> dict[date, float]:
    days = sorted(values)
    return {day: values[day] - values[days[index - sessions]] for index, day in enumerate(days) if index >= sessions}


def percentiles(values: dict[date, float]) -> dict[date, float]:
    """Each day's value as the share of earlier values at or below it (the first 250 days have none)."""
    output, seen = {}, []
    for day in sorted(values):
        if len(seen) >= 250:
            output[day] = bisect_right(seen, values[day]) / len(seen)
        position = bisect_right(seen, values[day])
        seen.insert(position, values[day])
    return output


def study(index: dict[date, float], signals: dict[str, dict[date, float]]) -> dict[str, object]:
    """Each signal on PERIODS, the government banks' on SHORT_PERIODS."""
    days = sorted(index)
    level = np.array([index[day] for day in days])
    position = {day: number for number, day in enumerate(days)}
    found: dict[str, object] = {}
    for name, values in signals.items():
        known = sorted(values)
        ranks = percentiles(values)
        report = {}
        periods = SHORT_PERIODS if name.startswith(GOVBANK) else US_PERIODS if name.startswith(US) else PERIODS
        for period, (start, end) in periods.items():
            buckets: dict[int, list[tuple[float, float, bool]]] = {fifth: [] for fifth in range(5)}
            for day in days:
                if not (start <= day <= end):
                    continue
                at = position[day]
                if at + max(HORIZONS) >= len(days):
                    continue
                before = bisect_right(known, day) - 1          # the latest signal dated before this session
                while before >= 0 and known[before] >= day:
                    before -= 1
                if before < 0 or known[before] not in ranks:
                    continue
                fifth = min(int(ranks[known[before]] * 5), 4)
                ahead = level[at + 1:at + 61]
                buckets[fifth].append((level[at + 20] / level[at] - 1, level[at + 60] / level[at] - 1,
                                       bool(ahead.min() / level[at] - 1 <= -FALL), day))
            everything = [item for items in buckets.values() for item in items]
            if not everything:
                continue
            mean60 = float(np.mean([item[1] for item in everything]))
            rate = float(np.mean([item[2] for item in everything]))
            report[period] = {"days": len(everything), "mean_60": round(mean60, 4), "fall_rate": round(rate, 4),
                              "fifths": [{"fifth": fifth + 1, "days": len(items),
                                          "mean_20": round(float(np.mean([item[0] for item in items])), 4) if items else None,
                                          "mean_60": round(float(np.mean([item[1] for item in items])), 4) if items else None,
                                          "fall_rate": round(float(np.mean([item[2] for item in items])), 4) if items else None}
                                         for fifth, items in buckets.items()]}
            # 2026-11-21: the period cut in two at its middle day — is the period's worst fifth below each half's average
            worst_fifth = min((fifth for fifth, items in buckets.items() if items),
                              key=lambda fifth: float(np.mean([item[1] for item in buckets[fifth]])))
            middle = sorted(item[3] for item in everything)[len(everything) // 2]
            halves = []
            for early in (True, False):
                part = [item for item in everything if (item[3] < middle) == early]
                mine = [item for item in buckets[worst_fifth] if (item[3] < middle) == early]
                halves.append(bool(part) and len(mine) >= 20
                              and float(np.mean([item[1] for item in mine])) < float(np.mean([item[1] for item in part])))
            report[period]["halves_below"] = halves
        verdicts = []
        for period, item in report.items():
            worst = min((fifth for fifth in item["fifths"] if fifth["mean_60"] is not None), key=lambda fifth: fifth["mean_60"])
            verdicts.append(worst["mean_60"] <= item["mean_60"] - GAP and item["fall_rate"] > 0
                            and (worst["fall_rate"] or 0) >= RATE * item["fall_rate"])
            item["worst"] = worst["fifth"]
        ends = {"low" if item["worst"] <= 2 else "high" if item["worst"] >= 4 else "middle" for item in report.values()}
        same_end = len(report) == len(periods) and len(ends) == 1 and "middle" not in ends
        useful = bool(verdicts) and len(verdicts) == len(periods) and all(verdicts)
        found[name] = {"periods": report, "same_end": same_end,
                       "halves_below": sum(sum(item["halves_below"]) for item in report.values()),
                       "useful": useful and same_end if name.startswith(MACRO) else useful}
    return found


def run(history: str | Path, research: str | Path) -> Path:
    import pandas as pd

    frame = pd.read_parquet(Path(history) / "total_return" / "0050.parquet")
    index = {value.date() if hasattr(value, "date") else value: float(level)
             for value, level in zip(pd.to_datetime(frame["date"]), frame["total_return_index"])}
    raw = series(history)
    signals = {}
    for name, values in raw.items():
        signals[f"{name}（水準）"] = values
        signals[f"{name}（20 日變化）"] = _changes(values)
    for sessions in (5, 20):                    # 2026-10-22
        values = govbank(history, sessions)
        if values:
            signals[f"{GOVBANK}（{sessions} 日，佔全市場成交值）"] = values
            raw[f"{GOVBANK}（{sessions} 日）"] = values
    # 2026-11-21: the US and international series (research is instance/research, the database beside it)
    american = us_series(Path(research).parent / "quant_platform.db")
    signals.update(american)
    raw.update(american)
    macro = us_macro(history)                   # 2026-11-21: the US statistics and market series (history/us.py)
    signals.update(macro)
    raw.update(macro)
    found = study(index, signals)
    report = {"generated_at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "fall": FALL, "gap": GAP, "rate": RATE,
              "signals": found, "ranges": {name: [str(min(values)), str(max(values))] for name, values in raw.items() if values}}
    folder = Path(research) / "diagnostics"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"market-signals-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
