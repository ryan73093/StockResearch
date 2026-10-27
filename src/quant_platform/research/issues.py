"""2026-10-27 (使用者：左邊今日、持倉、計畫、研究、系統再加一個議題追蹤，針對現在市場上的議題進行追蹤；第一個議題是所有
AI 科技巨頭支出可能帶來的風險、營收、任何分析市場景氣的，目前相關的資本支出是什麼，盡可能用圖、表格讓人理解，可以追蹤
什麼項目、什麼項目可以警示、什麼項目可以告警；第二個議題是台灣房市跟股市之間，幫我想幾個重點議題去追蹤；首頁做初步警示).

Each issue is a list of indicators. An indicator says what it tracks and why, reads its numbers from the public data
(research/history/issues_data.py) and the research data already kept (monthly revenue, the exchange's sector
indices), and is graded: 正常, 警示 (watch: worth a look) or 告警 (alert: the risk the issue is about is showing).
The thresholds are written here, before the numbers come in, and shown on the page; they are not fitted to the past.
``build`` writes ``issues/latest.json`` (the numbers, the grades, the series the charts draw) and appends every change of
grade to ``issues/log.jsonl``; the page and the home page read them.
"""

from __future__ import annotations

import json
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
LEVELS = {"ok": "正常", "watch": "警示", "alert": "告警", "none": "沒有資料"}
RANK = {"none": 0, "ok": 1, "watch": 2, "alert": 3}
# Taiwan's AI server supply chain (assembly, boards, cooling, power, rails, connectors, PCBs, ASIC design); TSMC apart
AI_CHAIN = {"2317": "鴻海", "2382": "廣達", "3231": "緯創", "6669": "緯穎", "2356": "英業達", "2376": "技嘉",
            "2345": "智邦", "3017": "奇鋐", "3324": "雙鴻", "2308": "台達電", "2059": "川湖", "3533": "嘉澤",
            "2368": "金像電", "3037": "欣興", "6274": "台燿", "3661": "世芯-KY", "3443": "創意"}
TSMC = "2330"
AI_NEWS = (("AI", "泡沫"), ("資本支出", "下修"), ("資本支出", "放緩"), ("資本支出", "縮減"), ("資本支出", "削減"),
           ("砍單",), ("capex", "cut"), ("AI", "降溫"))
HOUSING_NEWS = (("信用管制",), ("打房",), ("限貸",), ("房貸", "緊縮"), ("青安",), ("房市", "降溫"), ("預售屋", "解約"),
                ("不動產", "集中度"))


def _pct(value: float | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value * 100:+.{digits}f}%"


def _yoy(series: dict, key) -> float | None:
    """series[key] / series[key one year earlier] - 1 (keys: dates or 'YYYYQn')."""
    if isinstance(key, str):
        earlier = f"{int(key[:4]) - 1}{key[4:]}"
    else:
        earlier = key.replace(year=key.year - 1)
    now, then = series.get(key), series.get(earlier)
    return now / then - 1 if now is not None and then not in (None, 0) else None


def _months_back(month: date, count: int) -> date:
    index = month.year * 12 + month.month - 1 - count
    return date(index // 12, index % 12 + 1, 1)


def _sum_window(series: dict[date, float], end: date, months: int = 3) -> float | None:
    values = [series.get(_months_back(end, back)) for back in range(months)]
    return sum(values) if all(value is not None for value in values) else None


def _yoy_window(series: dict[date, float], end: date, months: int = 3) -> float | None:
    now, then = _sum_window(series, end, months), _sum_window(series, _months_back(end, 12), months)
    return now / then - 1 if now is not None and then else None


def grade(value: float | None, watch=None, alert=None, below: bool = True) -> str:
    """'alert' past ``alert``, 'watch' past ``watch``; ``below``: the risk is the value falling under the line."""
    if value is None:
        return "none"
    past = (lambda line: line is not None and value < line) if below else (lambda line: line is not None and value > line)
    return "alert" if past(alert) else ("watch" if past(watch) else "ok")


def worst(*levels: str) -> str:
    return max(levels, key=lambda level: RANK[level])


# --- the AI spending issue ------------------------------------------------------------------------------------------------
def spenders_by_quarter(sec: dict) -> dict[str, dict[str, dict[str, float]]]:
    """concept -> calendar quarter -> ticker -> US$ (the fiscal quarters placed in the calendar quarter most of them
    falls in)."""
    from quant_platform.research.history.issues_data import CONCEPTS, INSTANTS, SPENDERS, calendar_quarter

    output: dict[str, dict[str, dict[str, float]]] = {concept: {} for concept in CONCEPTS if concept not in INSTANTS}
    for ticker in SPENDERS:
        for concept in output:
            for end, value in (sec.get(ticker) or {}).get(concept, {}).items():
                output[concept].setdefault(calendar_quarter(end), {})[ticker] = value
    return output


def combined(by_quarter: dict[str, dict[str, float]], count: int) -> dict[str, float]:
    """The quarters every one of ``count`` companies reported, summed."""
    return {quarter: sum(values.values()) for quarter, values in sorted(by_quarter.items()) if len(values) == count}


def _debt_growth(sec: dict, tickers) -> tuple[float | None, list[dict]]:
    """Each company's long-term debt now against a year before (the balance nearest 365 days earlier, within 45
    days); the total's growth."""
    rows, now_total, then_total = [], 0.0, 0.0
    for ticker in tickers:
        debt = (sec.get(ticker) or {}).get("debt") or {}
        if not debt:
            continue
        last = max(debt)
        target = last - timedelta(days=365)
        earlier = min(debt, key=lambda day: abs((day - target).days))
        if abs((earlier - target).days) > 45:
            continue
        rows.append({"ticker": ticker, "end": last.isoformat(), "debt": debt[last], "before": debt[earlier],
                     "growth": debt[last] / debt[earlier] - 1 if debt[earlier] else None})
        now_total += debt[last]
        then_total += debt[earlier]
    return (now_total / then_total - 1 if then_total else None), rows


def monthly_revenue(history: Path, codes) -> tuple[dict[date, float], date | None]:
    """Month -> the codes' revenue summed (NT$), only months every code reported; the latest such month."""
    import pandas as pd

    path = Path(history) / "chips" / "TaiwanStockMonthRevenue.parquet"
    if not path.is_file():
        return {}, None
    frame = pd.read_parquet(path, columns=["date", "code", "revenue"])
    frame = frame[frame["code"].isin(list(codes))]
    output = {}
    for month, part in frame.groupby("date"):
        if part["code"].nunique() >= len(set(codes)):
            output[month if isinstance(month, date) else pd.Timestamp(month).date()] = float(part["revenue"].sum())
    output = dict(sorted(output.items()))
    return output, (max(output) if output else None)


def news_count(history: Path, today: date, words) -> dict[str, object]:
    """Headlines matching any of ``words`` (each a tuple that must all appear) in the last 7 days against the four
    weeks before, and the latest ones."""
    folder = Path(history) / "raw" / "news" / "market"
    recent, before, latest, days_before = 0, 0, [], 0
    for back in range(35):
        day = today - timedelta(days=back)
        path = folder / f"{day:%Y%m%d}.json"
        if not path.is_file():
            continue
        days_before += back >= 7
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for item in items:
            title = str(item.get("title") or "")
            text = (title + " " + str(item.get("summary") or "")).lower()
            if not any(all(word.lower() in text for word in group) for group in words):
                continue
            if back < 7:
                recent += 1
                if len(latest) < 12:
                    latest.append({"title": title, "link": item.get("link"), "source": item.get("source"),
                                   "published": (item.get("published") or "")[:16]})
            else:
                before += 1
    # the weekly count before, from the days collected (the feeds were first read on 2026-10-20); under two weeks of
    # them, no comparison
    baseline = before / days_before * 7 if days_before >= 14 else None
    return {"recent": recent, "baseline": None if baseline is None else round(baseline, 1), "days_before": days_before,
            "ratio": (recent / baseline) if baseline else None, "latest": latest}


def news_indicator(found: dict, today: date) -> tuple[str, str]:
    """(grade, value text) of a news count."""
    ratio = found["ratio"]
    if found["baseline"] is None:
        return "none", f"近 7 天 {found['recent']} 則（之前只收集了 {found['days_before']} 天，滿 14 天才比較）"
    level = ("alert" if ratio is not None and ratio >= 3 and found["recent"] >= 10 else
             "watch" if ratio is not None and ratio >= 2 and found["recent"] >= 5 else "ok")
    return level, f"近 7 天 {found['recent']} 則（之前平均每週 {found['baseline']:g} 則）"


def ai_issue(history: Path, today: date) -> dict[str, object]:
    from quant_platform.research.history.issues_data import BORROWERS, SPENDERS, SUPPLIERS, calendar_quarter, read_sec

    sec = read_sec(history)
    quarterly = spenders_by_quarter(sec)
    capex = combined(quarterly["capex"], len(SPENDERS))
    revenue = combined(quarterly["revenue"], len(SPENDERS))
    ocf = combined(quarterly["ocf"], len(SPENDERS))
    depreciation = combined(quarterly["depreciation"], len(SPENDERS))
    quarters = [quarter for quarter in capex if quarter in revenue and quarter in ocf]
    last = quarters[-1] if quarters else None
    indicators = []

    growth = {quarter: _yoy(capex, quarter) for quarter in quarters}
    recent_growth = [growth[quarter] for quarter in quarters[-3:]]
    slowing = (len(recent_growth) == 3 and None not in recent_growth
               and recent_growth[2] < recent_growth[1] < recent_growth[0])
    level = grade(growth.get(last), watch=0.20, alert=0.0)
    if level == "ok" and slowing:
        level = "watch"
    indicators.append({
        "id": "ai_capex_growth", "name": "雲端五巨頭合計資本支出年增率",
        "value": growth.get(last), "value_text": _pct(growth.get(last)), "as_of": last, "level": level,
        "detail": (f"{last} 合計 {capex[last] / 1e8:,.0f} 億美元" + ("；連兩季成長放緩" if slowing else "")) if last else "",
        "what": "微軟、Alphabet、亞馬遜、Meta、甲骨文每季現金資本支出（買設備、蓋資料中心）合計，和去年同季比。",
        "why": "台灣 AI 供應鏈（台積電、伺服器組裝、散熱、電源）的訂單來自這筆錢；成長放緩或轉負是供應鏈營收往下的領先訊號。",
        "watch": "年增率低於 +20%，或連兩季成長放緩", "alert": "年增率轉負（開始縮減）",
        "source": "SEC 財報（10-Q／10-K，XBRL）", "frequency": "每季（財報公布後一天內）"})

    intensity = {quarter: capex[quarter] / revenue[quarter] for quarter in quarters if revenue.get(quarter)}
    indicators.append({
        "id": "ai_capex_intensity", "name": "資本支出占營收比（五巨頭合計）",
        "value": intensity.get(last), "value_text": "—" if intensity.get(last) is None else f"{intensity[last]:.1%}",
        "as_of": last, "level": grade(intensity.get(last), watch=0.25, alert=0.35, below=False),
        "detail": f"營收 {revenue[last] / 1e8:,.0f} 億美元" if last else "",
        "what": "每賺 100 元營收拿多少去蓋 AI 設備。",
        "why": "比例愈高，未來折舊愈重、獲利率愈容易被壓縮；投資人開始質疑回收時，股價與後續資本支出都會修正。",
        "watch": "超過 25%", "alert": "超過 35%", "source": "SEC 財報", "frequency": "每季"})

    cash_ratio = {quarter: capex[quarter] / ocf[quarter] for quarter in quarters if ocf.get(quarter)}
    indicators.append({
        "id": "ai_capex_cash", "name": "資本支出占營業現金流（五巨頭合計）",
        "value": cash_ratio.get(last), "value_text": "—" if cash_ratio.get(last) is None else f"{cash_ratio[last]:.0%}",
        "as_of": last, "level": grade(cash_ratio.get(last), watch=0.75, alert=0.95, below=False),
        "detail": f"營業現金流 {ocf[last] / 1e8:,.0f} 億美元" if last else "",
        "what": "本業賺進的現金有多少被資本支出用掉。",
        "why": "接近或超過 100% 表示要靠借錢、賣資產或減少買回股票才撐得住支出，支出的持續性下降。",
        "watch": "超過 75%", "alert": "超過 95%", "source": "SEC 財報", "frequency": "每季"})

    companies = []
    for ticker, (_cik, name) in {**SPENDERS, **SUPPLIERS, **BORROWERS}.items():
        series = sec.get(ticker) or {}
        cap, rev, cash, dep = (series.get(key) or {} for key in ("capex", "revenue", "ocf", "depreciation"))
        if not cap or not rev:
            continue
        end = max(set(cap) & set(rev)) if set(cap) & set(rev) else None
        if end is None:
            continue
        year_ago = min(cap, key=lambda day: abs((day - end.replace(year=end.year - 1)).days))
        rev_ago = min(rev, key=lambda day: abs((day - end.replace(year=end.year - 1)).days))
        companies.append({
            "ticker": ticker, "name": name, "end": end.isoformat(), "quarter": calendar_quarter(end),
            "role": "花錢的" if ticker in SPENDERS else ("賣晶片的" if ticker in SUPPLIERS else "借錢蓋的"),
            "capex": cap[end], "revenue": rev[end], "ocf": cash.get(end), "depreciation": dep.get(end),
            "capex_growth": cap[end] / cap[year_ago] - 1 if cap.get(year_ago) and abs((end - year_ago).days - 365) < 40 else None,
            "revenue_growth": rev[end] / rev[rev_ago] - 1 if rev.get(rev_ago) and abs((end - rev_ago).days - 365) < 40 else None,
            "intensity": cap[end] / rev[end] if rev[end] else None,
            "free_cash": (cash[end] - cap[end]) if cash.get(end) is not None else None,
            "capex_cash": cap[end] / cash[end] if cash.get(end) else None})
    burning = [row for row in companies if row["ticker"] in SPENDERS and row["free_cash"] is not None and row["free_cash"] < 0]
    thin = [row for row in companies if row["ticker"] in SPENDERS and row["free_cash"] is not None
            and 0 <= row["free_cash"] < 0.05 * row["revenue"]]
    indicators.append({
        "id": "ai_free_cash", "name": "自由現金流轉負的巨頭",
        "value": len(burning), "value_text": f"{len(burning)} 家" + (f"（{'、'.join(row['name'] for row in burning)}）" if burning else ""),
        "as_of": max((row["quarter"] for row in companies), default=None),
        "level": "alert" if burning else ("watch" if thin else "ok"),
        "detail": ("自由現金流低於營收 5%：" + "、".join(row["name"] for row in thin)) if thin else "",
        "what": "每家最新一季：營業現金流減資本支出。",
        "why": "自由現金流轉負，代表支出已經超過本業賺的現金，得靠舉債或砍支出；是最直接的「錢燒過頭」訊號。",
        "watch": "有巨頭的自由現金流低於營收的 5%", "alert": "有巨頭的自由現金流轉負", "source": "SEC 財報", "frequency": "每季"})

    dep_gap = None
    if last:
        dep_growth, rev_growth = _yoy(depreciation, last), _yoy(revenue, last)
        dep_gap = dep_growth - rev_growth if dep_growth is not None and rev_growth is not None else None
    indicators.append({
        "id": "ai_depreciation", "name": "折舊成長減營收成長（五巨頭合計）",
        "value": dep_gap, "value_text": "—" if dep_gap is None else f"{dep_gap * 100:+.1f} 個百分點", "as_of": last,
        "level": grade(dep_gap, watch=0.10, alert=0.20, below=False),
        "detail": (f"折舊年增 {_pct(_yoy(depreciation, last))}、營收年增 {_pct(_yoy(revenue, last))}" if last else ""),
        "what": "折舊年增率減掉營收年增率。",
        "why": "前幾年買的設備開始攤提折舊；折舊長得比營收快，獲利率會被壓縮，常是巨頭調降資本支出的前兆。",
        "watch": "高於 10 個百分點", "alert": "高於 20 個百分點", "source": "SEC 財報", "frequency": "每季"})

    debt_growth, debt_rows = _debt_growth(sec, list(SPENDERS) + list(BORROWERS))
    indicators.append({
        "id": "ai_debt", "name": "長期負債年增率（五巨頭＋CoreWeave）",
        "value": debt_growth, "value_text": _pct(debt_growth), "as_of": max((row["end"] for row in debt_rows), default=None),
        "level": grade(debt_growth, watch=0.30, alert=0.60, below=False),
        "detail": "；".join(f"{row['ticker']} {_pct(row['growth'], 0)}" for row in debt_rows if row["growth"] is not None),
        "what": "長期負債（非流動）和一年前比。",
        "why": "AI 建設愈來愈靠發債與租賃融資（甲骨文、Meta、CoreWeave）；負債快速增加時，利率或需求一變，風險會傳到供應鏈。",
        "watch": "年增超過 30%", "alert": "年增超過 60%", "source": "SEC 財報", "frequency": "每季"})

    nvda = next((row for row in companies if row["ticker"] == "NVDA"), None)
    indicators.append({
        "id": "ai_nvidia", "name": "輝達營收年增率",
        "value": nvda and nvda["revenue_growth"], "value_text": _pct(nvda and nvda["revenue_growth"]),
        "as_of": nvda and nvda["quarter"], "level": grade(nvda and nvda["revenue_growth"], watch=0.30, alert=0.0),
        "detail": f"單季營收 {nvda['revenue'] / 1e8:,.0f} 億美元" if nvda else "",
        "what": "AI 晶片最大賣方的營收和去年同季比。",
        "why": "巨頭的資本支出最後變成輝達的營收；成長明顯放緩代表下游拉貨轉弱。",
        "watch": "低於 +30%", "alert": "轉負", "source": "SEC 財報", "frequency": "每季"})

    tsmc, tsmc_last = monthly_revenue(history, [TSMC])
    tsmc_growth = _yoy_window(tsmc, tsmc_last) if tsmc_last else None
    indicators.append({
        "id": "ai_tsmc", "name": "台積電月營收（近 3 個月合計）年增率",
        "value": tsmc_growth, "value_text": _pct(tsmc_growth), "as_of": tsmc_last and f"{tsmc_last:%Y-%m}",
        "level": grade(tsmc_growth, watch=0.15, alert=0.0),
        "detail": f"最新一月 {tsmc[tsmc_last] / 1e8:,.0f} 億元" if tsmc_last else "",
        "what": "台積電每月營收，近 3 個月合計和去年同期比。",
        "why": "先進製程與先進封裝是 AI 晶片的瓶頸，台積電營收是最快、最可靠的 AI 需求溫度計（每月 10 日前公布）。",
        "watch": "低於 +15%", "alert": "轉負", "source": "公開資訊觀測站月營收（FinMind）", "frequency": "每月 10 日前"})

    chain, chain_last = monthly_revenue(history, list(AI_CHAIN))
    chain_growth = _yoy_window(chain, chain_last) if chain_last else None
    indicators.append({
        "id": "ai_chain", "name": "台灣 AI 伺服器供應鏈月營收（近 3 個月）年增率",
        "value": chain_growth, "value_text": _pct(chain_growth), "as_of": chain_last and f"{chain_last:%Y-%m}",
        "level": grade(chain_growth, watch=0.10, alert=0.0),
        "detail": f"{len(AI_CHAIN)} 家合計，最新一月 {chain[chain_last] / 1e8:,.0f} 億元" if chain_last else "",
        "what": "、".join(AI_CHAIN.values()) + " 的月營收合計，近 3 個月和去年同期比。",
        "why": "巨頭資本支出的大宗是伺服器、機櫃、散熱與電源，這些公司的營收反映訂單有沒有延續。",
        "watch": "低於 +10%", "alert": "轉負", "source": "公開資訊觀測站月營收（FinMind）", "frequency": "每月 10 日前"})

    sectors = _sectors(history)
    drawdown, drawdown_day = _drawdown(sectors, "computer")
    indicators.append({
        "id": "ai_stocks", "name": "電腦及週邊設備類指數距一年高點",
        "value": drawdown, "value_text": "—" if drawdown is None else f"{drawdown:.1%}", "as_of": drawdown_day,
        "level": grade(drawdown, watch=-0.15, alert=-0.25),
        "detail": _sector_detail(sectors, "semiconductor", "半導體類指數"),
        "what": "AI 伺服器族群所在的類股指數，比過去 250 個交易日的最高點跌了多少。",
        "why": "股價通常比營收早反應；類股從高點大跌時，市場已經在押注資本支出循環轉向。",
        "watch": "跌超過 15%", "alert": "跌超過 25%", "source": "證交所類股指數", "frequency": "每個交易日"})

    news = news_count(history, today, AI_NEWS)
    news_level, news_text = news_indicator(news, today)
    indicators.append({
        "id": "ai_news", "name": "「AI 泡沫、資本支出下修、砍單」新聞",
        "value": news["recent"], "value_text": news_text, "as_of": today.isoformat(), "level": news_level, "detail": "",
        "what": "財經新聞標題與摘要裡同時出現這些字的則數。", "why": "市場開始集體擔心時，新聞會先變多；只當作提醒，不單獨當訊號。",
        "watch": "近 7 天是之前每週平均的 2 倍以上（且至少 5 則）", "alert": "3 倍以上（且至少 10 則）",
        "source": "Yahoo 股市、中央社、經濟日報 RSS（每天 4 次）", "frequency": "每天"})

    reported = {}
    if quarters:
        newest = max(quarterly["capex"])
        reported = {"quarter": newest, "count": len(quarterly["capex"][newest]), "total": len(SPENDERS),
                    "capex": sum(quarterly["capex"][newest].values()),
                    "names": [SPENDERS[ticker][1] for ticker in quarterly["capex"][newest]]}
    return {
        "key": "ai", "title": "AI 科技巨頭資本支出", "short": "AI 資本支出",
        "summary": "巨頭蓋 AI 資料中心的錢從哪來、會不會燒過頭，以及這筆錢流到台灣供應鏈的溫度。",
        "indicators": indicators, "companies": companies, "debt": debt_rows, "reported": reported,
        "news": news["latest"],
        "series": {
            "quarters": [{"x": quarter, "capex": capex[quarter] / 1e8, "ocf": ocf.get(quarter, 0) / 1e8,
                          "revenue": revenue.get(quarter, 0) / 1e8, "growth": growth.get(quarter),
                          "intensity": intensity.get(quarter), "cash_ratio": cash_ratio.get(quarter),
                          "depreciation": depreciation.get(quarter, 0) / 1e8} for quarter in quarters[-16:]],
            "taiwan": [{"x": month.isoformat(), "tsmc": _yoy_window(tsmc, month), "chain": _yoy_window(chain, month)}
                       for month in sorted(set(tsmc) & set(chain)) if month >= date(today.year - 3, 1, 1)
                       and _yoy_window(tsmc, month) is not None and _yoy_window(chain, month) is not None],
            "stocks": _indexed(sectors, ("taiex", "computer", "semiconductor"), today),
        }}


# --- sector indices, the margin loans -------------------------------------------------------------------------------------
def _sectors(history: Path):
    import pandas as pd

    path = Path(history).parent / "issues" / "sectors.parquet"
    return pd.read_parquet(path).sort_values("date") if path.is_file() else None


def _drawdown(sectors, key: str) -> tuple[float | None, str | None]:
    if sectors is None or key not in sectors or not len(sectors):
        return None, None
    values = sectors[key].dropna()
    if len(values) < 20:
        return None, None
    window = values.tail(250)
    return float(window.iloc[-1] / window.max() - 1), str(sectors["date"].iloc[-1])


def _sector_detail(sectors, key: str, label: str) -> str:
    found, _day = _drawdown(sectors, key)
    return "" if found is None else f"{label}距一年高點 {found:.1%}"


def _relative(sectors, key: str, sessions: int = 60) -> tuple[float | None, str | None]:
    """The sector's return over ``sessions`` sessions less TAIEX's (both as price indices)."""
    if sectors is None or key not in sectors or len(sectors) <= sessions:
        return None, None
    now, then = sectors.iloc[-1], sectors.iloc[-1 - sessions]
    return float((now[key] / then[key]) / (now["taiex"] / then["taiex"]) - 1), str(now["date"])


def _indexed(sectors, keys, today: date, years: int = 3) -> list[dict]:
    """Month-end values of each key, 100 at the first month shown."""
    if sectors is None or not len(sectors):
        return []
    frame = sectors[sectors["date"] >= date(today.year - years, today.month, 1).isoformat()].copy()
    frame["month"] = frame["date"].str[:7]
    ends = frame.groupby("month").tail(1)
    if not len(ends):
        return []
    first = ends.iloc[0]
    return [{"x": row["date"], **{key: float(row[key] / first[key] * 100) for key in keys if row.get(key) and first.get(key)}}
            for _index, row in ends.iterrows()]


def _margin(history: Path):
    import pandas as pd

    path = Path(history).parent / "issues" / "margin.parquet"
    return pd.read_parquet(path).sort_values("date") if path.is_file() else None


# --- the housing and stocks issue -------------------------------------------------------------------------------------------
def lvr_monthly(history: Path) -> dict[str, dict]:
    """From the real-price registrations: by contract month, homes sold (成屋, nationwide), pre-sales and the six cities'
    median price (萬元 a 坪); by registration season (a whole season's file is complete, the months are not: a sale is
    registered weeks after the contract and the periods between seasons come only with the next season's file), the
    same and each city's."""
    import pandas as pd

    from quant_platform.research.history.issues_data import SIX

    path = Path(history).parent / "issues" / "lvr.parquet"
    if not path.is_file():
        return {}
    frame = pd.read_parquet(path)
    output: dict[str, dict] = {"existing": {}, "presale": {}, "price_six": {}, "price_presale_six": {}, "seasons": {}}
    for (kind, month), part in frame.groupby(["kind", "month"]):
        output[kind][month] = len(part)
        six = part[part["city"].isin(SIX)]["unit"]
        if len(six) >= 50:
            output["price_six" if kind == "existing" else "price_presale_six"][month] = float(six.median())
    for season, part in frame[frame["season"] != "current"].groupby("season"):
        existing, presale = part[part["kind"] == "existing"], part[part["kind"] == "presale"]
        output["seasons"][season] = {
            "existing": len(existing), "presale": len(presale),
            "price_six": float(existing[existing["city"].isin(SIX)]["unit"].median()) if len(existing) else None,
            "cities": {city: (len(rows), float(rows["unit"].median())) for city, rows in existing.groupby("city")}}
    return output


def season_label(season: str) -> str:
    """'115S3' -> '2026 年第 3 季登記（6/11～9/10）'."""
    year, quarter = int(season[:3]) + 1911, int(season[-1])
    first = "12/11" if quarter == 1 else f"{quarter * 3 - 3}/11"
    return f"{year} 年第 {quarter} 季登記（{first}～{quarter * 3}/10）"


def season_before(season: str) -> str:
    return f"{int(season[:3]) - 1}S{season[-1]}"


def complete_month(latest_season: str | None, today: date) -> date:
    """The latest contract month (almost) all registered in the seasons kept: three months before the last season's
    end (a sale is registered weeks to two months after the contract)."""
    if not latest_season:
        return _months_back(date(today.year, today.month, 1), 4)
    return _months_back(date(int(latest_season[:3]) + 1911, int(latest_season[-1]) * 3, 1), 3)


def housing_issue(history: Path, today: date) -> dict[str, object]:
    from quant_platform.research.daily import load_industries
    from quant_platform.research.history.issues_data import SIX, read_cbc

    indicators = []
    lvr = lvr_monthly(history)
    seasons = lvr.get("seasons") or {}
    latest = max(seasons) if seasons else None
    year_ago = seasons.get(season_before(latest)) if latest else None
    month = complete_month(latest, today)
    volume = {key: float(value) for key, value in (lvr.get("existing") or {}).items() if key <= month}
    presale = {key: float(value) for key, value in (lvr.get("presale") or {}).items() if key <= month}

    def season_growth(key: str) -> float | None:
        if not latest or not year_ago or not year_ago.get(key):
            return None
        return seasons[latest][key] / year_ago[key] - 1

    volume_growth = season_growth("existing")
    indicators.append({
        "id": "house_volume", "name": "全國住宅成交筆數（成屋，一季）年增率",
        "value": volume_growth, "value_text": _pct(volume_growth), "as_of": latest and season_label(latest),
        "level": grade(volume_growth, watch=-0.15, alert=-0.30),
        "detail": f"{seasons[latest]['existing']:,} 筆，去年同季 {year_ago['existing']:,} 筆" if latest and year_ago else "",
        "what": "實價登錄的住宅買賣（排除親友、特殊關係、瑕疵等非市場交易），一季登記的筆數和去年同季比。",
        "why": "量先價行：成交量連續大減通常領先房價走弱，也影響營建股的推案與交屋。",
        "watch": "年減超過 15%", "alert": "年減超過 30%", "source": "內政部實價登錄（每季整包）",
        "frequency": "每季（季後一個月內）"})
    presale_growth = season_growth("presale")
    indicators.append({
        "id": "house_presale", "name": "預售屋成交筆數（一季）年增率",
        "value": presale_growth, "value_text": _pct(presale_growth), "as_of": latest and season_label(latest),
        "level": grade(presale_growth, watch=-0.20, alert=-0.40),
        "detail": f"{seasons[latest]['presale']:,} 筆，去年同季 {year_ago['presale']:,} 筆" if latest and year_ago else "",
        "what": "預售屋買賣（解約的不算），一季登記的筆數和去年同季比。",
        "why": "預售屋是建商的未來營收與銀行建築貸款的來源；預售量急凍時，建商延後推案、營建股與相關放款先受影響。",
        "watch": "年減超過 20%", "alert": "年減超過 40%", "source": "內政部實價登錄（每季整包）", "frequency": "每季"})
    price = {key: value for key, value in (lvr.get("price_six") or {}).items() if key <= month}
    price_growth = season_growth("price_six")
    price_level = worst(grade(price_growth, watch=0.0, alert=-0.05), grade(price_growth, watch=0.12, alert=0.20, below=False))
    indicators.append({
        "id": "house_price", "name": "六都成屋單價中位數（一季）年增率",
        "value": price_growth, "value_text": _pct(price_growth), "as_of": latest and season_label(latest), "level": price_level,
        "detail": (f"中位數 {seasons[latest]['price_six']:,.1f} 萬元／坪（不含車位），去年同季 {year_ago['price_six']:,.1f}"
                   if latest and year_ago and year_ago.get("price_six") else ""),
        "what": "六都住宅成交單價（扣掉車位的總價 ÷ 扣掉車位的面積）的中位數，一季和去年同季比。",
        "why": "兩邊都要看：房價下跌傷害屋主財富與銀行擔保品；房價漲太快會讓央行加碼信用管制，衝擊營建與金融股。",
        "watch": "轉負，或年漲超過 12%", "alert": "年跌超過 5%，或年漲超過 20%", "source": "內政部實價登錄（每季整包）",
        "frequency": "每季"})

    cbc = read_cbc(history)
    loans = {row["month"]: row["home_amount"] for row in cbc["5newloan"] if row.get("home_amount")}
    rates = {row["month"]: row["home_rate"] for row in cbc["5newloan"] if row.get("home_rate")}
    loan_last = max(loans) if loans else None
    loan_growth = _yoy_window(loans, loan_last) if loan_last else None
    indicators.append({
        "id": "house_new_loans", "name": "五大銀行新承做購屋貸款（近 3 個月）年增率",
        "value": loan_growth, "value_text": _pct(loan_growth), "as_of": loan_last and f"{loan_last:%Y-%m}",
        "level": grade(loan_growth, watch=-0.20, alert=-0.35),
        "detail": f"{loan_last:%Y-%m} 單月 {loans[loan_last] / 100:,.0f} 億元" if loan_last else "",
        "what": "台銀、合庫、土銀、華銀、一銀每月新撥的購屋貸款金額。",
        "why": "房貸撥款是買氣的現金面；銀行收緊（央行管制、不動產放款接近上限）時會先在這裡看到。",
        "watch": "年減超過 20%", "alert": "年減超過 35%", "source": "中央銀行（每月下旬公布上個月）", "frequency": "每月"})
    rate_last = max(rates) if rates else None
    rate_change = (rates[rate_last] - rates[_months_back(rate_last, 12)]) if rate_last and rates.get(_months_back(rate_last, 12)) else None
    indicators.append({
        "id": "house_rate", "name": "購屋貸款利率一年變化",
        "value": rate_change, "value_text": "—" if rate_change is None else f"{rate_change:+.3f} 個百分點",
        "as_of": rate_last and f"{rate_last:%Y-%m}", "level": grade(rate_change, watch=0.125, alert=0.25, below=False),
        "detail": f"目前 {rates[rate_last]:.3f}%" if rate_last else "",
        "what": "五大銀行新承做購屋貸款的平均利率，和一年前比。",
        "why": "利率上升一碼（0.125 個百分點）就會增加每月房貸負擔；連續升息是房市降溫最常見的原因。",
        "watch": "一年上升 0.125 個百分點以上", "alert": "一年上升 0.25 個百分點以上", "source": "中央銀行",
        "frequency": "每月"})

    balances = {row["month"]: row for row in cbc["consumer"]}
    balance_last = max(balances) if balances else None
    home_growth = construction_growth = None
    if balance_last and balances.get(_months_back(balance_last, 12)):
        before = balances[_months_back(balance_last, 12)]
        home_growth = balances[balance_last]["home_balance"] / before["home_balance"] - 1
        construction_growth = balances[balance_last]["construction_balance"] / before["construction_balance"] - 1
    indicators.append({
        "id": "house_mortgage_balance", "name": "全體銀行購置住宅貸款餘額年增率",
        "value": home_growth, "value_text": _pct(home_growth), "as_of": balance_last and f"{balance_last:%Y-%m}",
        "level": worst(grade(home_growth, watch=0.09, alert=0.11, below=False), grade(home_growth, watch=0.02, alert=0.0)),
        "detail": f"餘額 {balances[balance_last]['home_balance'] / 1e6:,.2f} 兆元" if balance_last else "",
        "what": "所有銀行的房貸餘額和一年前比。",
        "why": "成長太快（2024 年 8 月 11%）會引來央行信用管制；轉為負成長代表房市去槓桿，房價與銀行獲利都承壓。",
        "watch": "年增超過 9% 或低於 2%", "alert": "年增超過 11% 或轉負", "source": "中央銀行", "frequency": "每月"})
    indicators.append({
        "id": "house_construction_loans", "name": "建築貸款餘額年增率",
        "value": construction_growth, "value_text": _pct(construction_growth), "as_of": balance_last and f"{balance_last:%Y-%m}",
        "level": grade(construction_growth, watch=0.0, alert=-0.05),
        "detail": f"餘額 {balances[balance_last]['construction_balance'] / 1e6:,.2f} 兆元" if balance_last else "",
        "what": "銀行借給建商蓋房子的錢，和一年前比。",
        "why": "建商買地蓋房的資金面；轉負代表建商縮手或銀行收緊，領先未來的推案與營建業營收。",
        "watch": "轉負", "alert": "年減超過 5%", "source": "中央銀行", "frequency": "每月"})

    sectors = _sectors(history)
    construction, construction_day = _relative(sectors, "construction")
    finance, finance_day = _relative(sectors, "finance")
    indicators.append({
        "id": "house_construction_stocks", "name": "營建股比大盤（近 60 個交易日）",
        "value": construction, "value_text": _pct(construction), "as_of": construction_day,
        "level": grade(construction, watch=-0.08, alert=-0.15),
        "detail": _sector_detail(sectors, "construction", "建材營造類指數"),
        "what": "建材營造類指數近 60 個交易日的漲跌，減掉加權指數同期的漲跌。",
        "why": "股市對房市的看法：營建股大幅落後大盤，通常在反映交易量、信用管制或房價的壞消息。",
        "watch": "落後大盤超過 8%", "alert": "落後大盤超過 15%", "source": "證交所類股指數", "frequency": "每個交易日"})
    indicators.append({
        "id": "house_finance_stocks", "name": "金融股比大盤（近 60 個交易日）",
        "value": finance, "value_text": _pct(finance), "as_of": finance_day,
        "level": grade(finance, watch=-0.08, alert=-0.15),
        "detail": _sector_detail(sectors, "finance", "金融保險類指數"),
        "what": "金融保險類指數近 60 個交易日的漲跌，減掉加權指數同期的漲跌。",
        "why": "銀行的放款有三成以上和不動產有關；房市出問題時，金融股通常會先落後。",
        "watch": "落後大盤超過 8%", "alert": "落後大盤超過 15%", "source": "證交所類股指數", "frequency": "每個交易日"})

    industries = load_industries(history)
    builders = [code for code, name in industries.items() if name == "建材營造"]
    builder_revenue, builder_last = _builders_revenue(history, builders)
    builder_growth = _yoy_window(builder_revenue, builder_last) if builder_last else None
    indicators.append({
        "id": "house_builders_revenue", "name": "營建業月營收（近 3 個月）年增率",
        "value": builder_growth, "value_text": _pct(builder_growth), "as_of": builder_last and f"{builder_last:%Y-%m}",
        "level": grade(builder_growth, watch=-0.20, alert=-0.40),
        "detail": f"{len(builders)} 家建材營造公司合計" if builders else "",
        "what": "建材營造類公司每月營收合計，近 3 個月和去年同期比。",
        "why": "建商的營收在交屋時認列，反映 2～3 年前預售的成果與現在的交屋進度；大減會傷到營建股獲利與股利。",
        "watch": "年減超過 20%", "alert": "年減超過 40%", "source": "公開資訊觀測站月營收（FinMind）", "frequency": "每月 10 日前"})

    margin = _margin(history)
    margin_growth = margin_change = None
    margin_day = None
    if margin is not None and len(margin) > 250:
        last_row = margin.iloc[-1]
        margin_day = str(last_row["date"])
        target = (date.fromisoformat(margin_day) - timedelta(days=365)).isoformat()
        earlier = margin[margin["date"] <= target]
        if len(earlier):
            margin_growth = float(last_row["balance"] / earlier.iloc[-1]["balance"] - 1)
        margin_change = float(last_row["balance"] / margin.iloc[-61]["balance"] - 1)
    indicators.append({
        "id": "stock_margin", "name": "上市融資餘額年增率",
        "value": margin_growth, "value_text": _pct(margin_growth), "as_of": margin_day,
        "level": worst(grade(margin_growth, watch=0.40, alert=0.60, below=False),
                       grade(margin_change, watch=0.25, alert=0.40, below=False)),
        "detail": (f"{margin.iloc[-1]['balance'] / 1e8:,.0f} 億元；近 60 個交易日 {_pct(margin_change)}" if margin_day else ""),
        "what": "投資人借錢買股票的總額（上市），和一年前、60 個交易日前比。",
        "why": "股市槓桿：融資快速堆高時，回檔容易引發斷頭賣壓；股市賺的錢流向房市（或反過來）也會在這裡與房貸一起看到。",
        "watch": "年增超過 40%，或 60 個交易日增加超過 25%", "alert": "年增超過 60%，或 60 個交易日增加超過 40%",
        "source": "證交所信用交易統計（FinMind）", "frequency": "每個交易日"})

    news = news_count(history, today, HOUSING_NEWS)
    news_level, news_text = news_indicator(news, today)
    indicators.append({
        "id": "house_news", "name": "「信用管制、打房、限貸、青安」新聞",
        "value": news["recent"], "value_text": news_text, "as_of": today.isoformat(), "level": news_level, "detail": "", "what": "財經新聞標題與摘要裡出現這些字的則數。",
        "why": "央行理監事會（每季）與金管會的房市政策常先在新聞發酵；只當作提醒。",
        "watch": "近 7 天是之前每週平均的 2 倍以上（且至少 5 則）", "alert": "3 倍以上（且至少 10 則）",
        "source": "財經新聞 RSS（每天 4 次）", "frequency": "每天"})

    cities = []
    for city in SIX:
        now_row = (seasons.get(latest) or {}).get("cities", {}).get(city) if latest else None
        then_row = (year_ago or {}).get("cities", {}).get(city)
        if not now_row or not then_row:
            continue
        cities.append({"city": city, "count": now_row[0], "count_growth": now_row[0] / then_row[0] - 1 if then_row[0] else None,
                       "price": now_row[1], "price_growth": now_row[1] / then_row[1] - 1 if then_row[1] else None})

    first = _months_back(month, 35)
    months = [_months_back(month, back) for back in range(35, -1, -1)]
    market_rows = []
    stock_index = _indexed(sectors, ("taiex", "construction", "finance"), today)
    # the combined chart starts every line at 100 in the same month (the housing months' first)
    by_month = {row["x"][:7]: row for row in _indexed(sectors, ("taiex", "construction"), today, years=4)}
    base_stock = by_month.get(f"{first:%Y-%m}") or {}
    by_month = {key: {name: row[name] / base_stock[name] * 100 for name in ("taiex", "construction")
                      if row.get(name) and base_stock.get(name)} for key, row in by_month.items()}
    base_volume = volume.get(first)
    base_loans = loans.get(first)
    for when in months:
        row = {"x": when.isoformat()}
        stock = by_month.get(f"{when:%Y-%m}")
        if stock:
            row.update(taiex=stock.get("taiex"), construction=stock.get("construction"))
        if base_volume and volume.get(when):
            row["volume"] = volume[when] / base_volume * 100
        if base_loans and loans.get(when):
            row["loans"] = loans[when] / base_loans * 100
        market_rows.append(row)
    return {
        "key": "housing", "title": "台灣房市與股市", "short": "房市與股市",
        "summary": "房市的量、價、錢（房貸與建築貸款），和股市裡跟房市連動的營建股、金融股與融資槓桿。",
        "indicators": indicators, "cities": cities, "news": news["latest"], "complete_month": f"{month:%Y-%m}",
        "season": latest and season_label(latest),
        "series": {
            "volume": [{"x": when.isoformat(), "existing": volume.get(when), "presale": presale.get(when)}
                       for when in months if volume.get(when) is not None],
            "price": [{"x": when.isoformat(), "six": price.get(when), "presale": (lvr.get("price_presale_six") or {}).get(when)}
                      for when in months if price.get(when) is not None],
            "seasons": [{"x": f"{int(key[:3]) + 1911}-{int(key[-1]) * 3:02d}-10", "season": key,
                         "existing": row["existing"], "presale": row["presale"], "price": row["price_six"]}
                        for key, row in sorted(seasons.items())],
            "loans": [{"x": when.isoformat(), "amount": loans[when] / 100, "rate": rates.get(when)}
                      for when in sorted(loans) if when >= _months_back(loan_last, 47)] if loan_last else [],
            "balances": [{"x": when.isoformat(), "home": _yoy(({key: row["home_balance"] for key, row in balances.items()}), when),
                          "construction": _yoy(({key: row["construction_balance"] for key, row in balances.items()}), when)}
                         for when in sorted(balances) if balance_last and when >= _months_back(balance_last, 47)],
            "stocks": stock_index,
            "together": [row for row in market_rows if len(row) > 1],
            "margin": ([{"x": str(row["date"]), "balance": float(row["balance"]) / 1e8}
                        for _index, row in margin.iloc[::5].iterrows() if str(row["date"]) >= f"{today.year - 3}"]
                       if margin is not None else []),
        }}


def _builders_revenue(history: Path, codes: list[str]) -> tuple[dict[date, float], date | None]:
    """Month -> the construction companies' revenue summed over those that reported in both that month and a year
    before (companies list and leave); the latest month at least 90% of them reported."""
    import pandas as pd

    path = Path(history) / "chips" / "TaiwanStockMonthRevenue.parquet"
    if not path.is_file() or not codes:
        return {}, None
    frame = pd.read_parquet(path, columns=["date", "code", "revenue"])
    frame = frame[frame["code"].isin(codes)]
    counts = frame.groupby("date")["code"].nunique()
    typical = counts.tail(13).median() if len(counts) else 0
    months = [month for month, count in counts.items() if count >= 0.9 * typical]
    output = {(month if isinstance(month, date) else pd.Timestamp(month).date()): float(
        frame[frame["date"] == month]["revenue"].sum()) for month in months}
    output = dict(sorted(output.items()))
    return output, (max(output) if output else None)


# --- build, read ------------------------------------------------------------------------------------------------------------
def build(research: str | Path, today: date | None = None, now: datetime | None = None) -> dict[str, object]:
    """Every issue's indicators from the data on disk; ``issues/latest.json``, changes of grade to ``log.jsonl``."""
    research = Path(research)
    history = research / "history"
    today = today or datetime.now(TAIPEI).date()
    now = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    topics = [ai_issue(history, today), housing_issue(history, today)]
    for topic in topics:
        for item in topic["indicators"]:
            item["rank"] = RANK[item["level"]]
    counts = {level: sum(1 for topic in topics for item in topic["indicators"] if item["level"] == level) for level in LEVELS}
    payload = {"built_at": now.isoformat(timespec="seconds"), "today": today.isoformat(), "topics": topics, "counts": counts}
    folder = research / "issues"
    folder.mkdir(parents=True, exist_ok=True)
    before = read(research)
    previous = {item["id"]: item["level"] for topic in (before or {}).get("topics", []) for item in topic["indicators"]}
    changes = [{"at": payload["built_at"], "id": item["id"], "name": item["name"], "from": previous.get(item["id"]),
                "to": item["level"], "value": item["value_text"]}
               for topic in topics for item in topic["indicators"] if previous.get(item["id"]) != item["level"]]
    partial = folder / "latest.partial"
    partial.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    partial.replace(folder / "latest.json")
    if changes:
        with open(folder / "log.jsonl", "a", encoding="utf-8") as handle:
            for change in changes:
                handle.write(json.dumps(change, ensure_ascii=False) + "\n")
    return payload


def read(research: str | Path) -> dict[str, object] | None:
    path = Path(research) / "issues" / "latest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        return None


def changes(research: str | Path, limit: int = 30) -> list[dict]:
    path = Path(research) / "issues" / "log.jsonl"
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return rows[-limit:][::-1]


def headline(payload: dict | None) -> dict[str, object] | None:
    """The home page's card: per issue the worst grade and the indicators past a line (alerts first)."""
    if not payload:
        return None
    topics = []
    for topic in payload["topics"]:
        flagged = sorted((item for item in topic["indicators"] if item["level"] in ("watch", "alert")),
                         key=lambda item: -RANK[item["level"]])
        topics.append({"key": topic["key"], "title": topic["title"], "short": topic["short"],
                       "level": worst("ok", *(item["level"] for item in topic["indicators"])),
                       "flagged": flagged, "total": len(topic["indicators"])})
    return {"built_at": payload["built_at"], "counts": payload["counts"], "topics": topics}


def refresh(research: str | Path, today: date | None = None, full: bool = False, token: str = "") -> dict[str, object]:
    """Fetch what is published (SEC, the central bank, the real-price registrations, the margin loans), read the sector
    indices kept, then build. A source that does not answer keeps its last data."""
    from quant_platform.research.history import issues_data

    research = Path(research)
    history = research / "history"
    today = today or datetime.now(TAIPEI).date()
    fetched: dict[str, object] = {}
    for name, step in (("sec", lambda: issues_data.fetch_sec(history)),
                       ("cbc", lambda: issues_data.fetch_cbc(history)),
                       ("lvr", lambda: issues_data.fetch_lvr(history, today, full=full)),
                       ("margin", lambda: issues_data.fetch_margin(history, token, today)),
                       ("sectors", lambda: issues_data.update_sectors(history))):
        try:
            fetched[name] = step()
        except Exception as error:  # noqa: BLE001 - one source down does not stop the rest
            fetched[name] = f"失敗：{str(error)[:120]}"
    payload = build(research, today)
    payload["fetched"] = fetched
    return payload


# --- the page's drawings ----------------------------------------------------------------------------------------------------
def _quarter_end(quarter: str) -> str:
    year, number = int(quarter[:4]), int(quarter[-1])
    return date(year, number * 3, 28).isoformat()


def _quarter_text(x, full: bool) -> str:
    when = date.fromisoformat(str(x)[:10])
    return f"{when.year} 年第 {(when.month - 1) // 3 + 1} 季" if full else f"{when.year}Q{(when.month - 1) // 3 + 1}"


def _month_text(x, full: bool) -> str:
    when = date.fromisoformat(str(x)[:10])
    return f"{when:%Y-%m}" if full else f"{when.year % 100:02d}/{when.month}"


def _pct_text(value: float) -> str:
    return f"{value * 100:.0f}%"


def charts(payload: dict) -> dict[str, str]:
    """Every issue's drawings (research/charts.py: wide and narrow, hover values, names at the line ends)."""
    from quant_platform.research import charts as svg

    output: dict[str, str] = {}
    topics = {topic["key"]: topic for topic in payload.get("topics") or []}
    ai = topics.get("ai")
    if ai:
        rows = [{**row, "x": _quarter_end(row["x"])} for row in ai["series"]["quarters"]]
        if rows:
            output["ai_cash"] = svg.line(rows, [
                {"key": "capex", "label": "資本支出", "css": "v1"},
                {"key": "ocf", "label": "營業現金流", "css": "v3"}],
                lambda value: f"{value:,.0f} 億", _quarter_text, "五巨頭合計：每季資本支出與營業現金流（億美元）")
            output["ai_ratio"] = svg.line(rows, [
                {"key": "intensity", "label": "占營收", "css": "v1"},
                {"key": "cash_ratio", "label": "占營業現金流", "css": "v2"}],
                _pct_text, _quarter_text, "資本支出占營收、占營業現金流（%）",
                guides=[(0.35, "占營收告警 35%"), (0.95, "占現金流告警 95%")])
            growth = [row for row in rows if row.get("growth") is not None]
            if growth:
                output["ai_growth"] = svg.line(growth, [{"key": "growth", "label": "資本支出年增率", "css": "v1"}],
                                               _pct_text, _quarter_text, "五巨頭合計資本支出年增率（%）",
                                               guides=[(0.20, "警示 +20%"), (0.0, "告警 0%")])
        if ai["series"]["taiwan"]:
            output["ai_taiwan"] = svg.line(ai["series"]["taiwan"], [
                {"key": "tsmc", "label": "台積電", "css": "v1"},
                {"key": "chain", "label": "AI 伺服器供應鏈", "css": "v2"}],
                _pct_text, _month_text, "台灣：月營收近 3 個月合計年增率（%）", guides=[(0.0, "告警 0%")])
        if ai["series"]["stocks"]:
            output["ai_stocks"] = svg.line(ai["series"]["stocks"], [
                {"key": "computer", "label": "電腦及週邊", "css": "v1"},
                {"key": "semiconductor", "label": "半導體", "css": "v2"},
                {"key": "taiex", "label": "加權指數", "css": "vref"}],
                lambda value: f"{value:,.0f}", _month_text, "類股指數（三年前＝100）")
        # the five that spend (CoreWeave spends many times its cash and would flatten the others; it is in the table)
        spenders = [row for row in ai["companies"] if row.get("capex_cash") is not None and row.get("role") == "花錢的"]
        if spenders:
            output["ai_companies"] = svg.bars(
                [{"label": row["name"], "short": row["ticker"], "values": {"cash": row["capex_cash"], "revenue": row["intensity"]},
                  "titles": {"cash": f"{row['name']} {row['quarter']}：資本支出占營業現金流 {row['capex_cash']:.0%}",
                             "revenue": f"{row['name']} {row['quarter']}：資本支出占營收 {row['intensity']:.0%}"}}
                 for row in spenders],
                [{"key": "cash", "label": "占營業現金流", "css": "v1"}, {"key": "revenue", "label": "占營收", "css": "v2"}],
                _pct_text, "每家最新一季：資本支出占營業現金流、占營收（%）")
    housing = topics.get("housing")
    if housing:
        series = housing["series"]
        if series["volume"]:
            output["house_volume"] = svg.line(series["volume"], [
                {"key": "existing", "label": "成屋", "css": "v1"}, {"key": "presale", "label": "預售屋", "css": "v2"}],
                lambda value: f"{value:,.0f} 筆", _month_text, "全國住宅成交筆數（依簽約月份）")
        if series.get("seasons"):
            output["house_seasons"] = svg.bars(
                [{"label": f"{int(row['season'][:3]) + 1911}Q{row['season'][-1]}", "short": f"{str(int(row['season'][:3]) + 1911)[2:]}Q{row['season'][-1]}",
                  "values": {"existing": row["existing"], "presale": row["presale"]},
                  "titles": {"existing": f"{season_label(row['season'])}：成屋 {row['existing']:,} 筆",
                             "presale": f"{season_label(row['season'])}：預售屋 {row['presale']:,} 筆"}}
                 for row in series["seasons"][-8:]],                       # two years: readable on a phone
                [{"key": "existing", "label": "成屋", "css": "v1"}, {"key": "presale", "label": "預售屋", "css": "v2"}],
                lambda value: f"{value:,.0f}", "每季登記的住宅成交筆數", value_labels=False)
        if series["price"]:
            output["house_price"] = svg.line(series["price"], [
                {"key": "six", "label": "成屋", "css": "v1"}, {"key": "presale", "label": "預售屋", "css": "v2"}],
                lambda value: f"{value:,.0f} 萬", _month_text, "六都住宅單價中位數（萬元／坪，不含車位）")
        if series["loans"]:
            output["house_loans"] = svg.line(series["loans"], [{"key": "amount", "label": "新承做購屋貸款", "css": "v1"}],
                                             lambda value: f"{value:,.0f} 億", _month_text, "五大銀行每月新承做購屋貸款（億元）")
            output["house_rate"] = svg.line(series["loans"], [{"key": "rate", "label": "購屋貸款利率", "css": "v2"}],
                                            lambda value: f"{value:.2f}%", _month_text, "五大銀行新承做購屋貸款利率（%）")
        balances = [row for row in series["balances"] if row.get("home") is not None]
        if balances:
            output["house_balances"] = svg.line(balances, [
                {"key": "home", "label": "購置住宅貸款", "css": "v1"}, {"key": "construction", "label": "建築貸款", "css": "v2"}],
                lambda value: f"{value * 100:.1f}%", _month_text, "全體銀行房貸與建築貸款餘額年增率（%）",
                guides=[(0.11, "房貸告警 11%"), (0.0, "0%")])
        if series["stocks"]:
            output["house_stocks"] = svg.line(series["stocks"], [
                {"key": "construction", "label": "建材營造", "css": "v1"}, {"key": "finance", "label": "金融保險", "css": "v2"},
                {"key": "taiex", "label": "加權指數", "css": "vref"}],
                lambda value: f"{value:,.0f}", _month_text, "類股指數（三年前＝100）")
        together = [row for row in series["together"] if len(row) > 2]
        if together:
            output["house_together"] = svg.line(together, [
                {"key": "taiex", "label": "加權指數", "css": "vref"}, {"key": "construction", "label": "營建股", "css": "v1"},
                {"key": "volume", "label": "住宅成交筆數", "css": "v2"}, {"key": "loans", "label": "新承做房貸", "css": "v3"}],
                lambda value: f"{value:,.0f}", _month_text, "股市與房市同一張圖（三年前＝100）")
        if series["margin"]:
            output["house_margin"] = svg.line(series["margin"], [{"key": "balance", "label": "上市融資餘額", "css": "v1"}],
                                              lambda value: f"{value:,.0f} 億", _month_text, "上市融資餘額（億元）")
    return output
