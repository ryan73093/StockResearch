"""2026-10-22 (使用者：麻煩請在研究首頁建一個表——因子有哪些、策略類別、分析項目有哪些、每個成長的幅度、版本、策略角色成長，
讓我可以快速知道現在狀況): the research overview's status table, from what the platform already keeps.

- categories: every strategy role (categories.FAMILY_ORDER; research/roles.py) — how many strategies, how many pass,
  its best one, and how much the best grew in a day, in seven days, since before its latest optimization and since
  its first strategy;
- factors: every rule factor — its family, the latest strength report's verdict and columns, and how many passing
  strategies use it (a blend's sleeves counted);
- analyses: each standing analysis and check — when it last ran and what it found;
- versions: the engine, every trained model (its gate, when trained, refitted to the month's cutoff, how many forward
  accounts read it) and how fresh each data source is;
- the roles' growth over time (the best's end value on each day a strategy was added).

Kept in memory until a file it reads changes."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
_CACHE: dict[str, object] = {}


def _newest(folder: Path, pattern: str) -> Path | None:
    files = sorted(folder.glob(pattern), key=lambda path: path.stat().st_mtime) if folder.is_dir() else []
    return files[-1] if files else None


def _read(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _stamp(path: Path | None) -> str | None:
    return datetime.fromtimestamp(path.stat().st_mtime, TAIPEI).strftime("%Y-%m-%d %H:%M") if path and path.is_file() else None


def _growth(history: list[dict], now: float | None, before: date) -> float | None:
    """The best's change since its last point on or before ``before`` (history: the best on each day a strategy
    was added to the role)."""
    earlier = [point for point in history if point.get("day") and point["day"] <= before.isoformat() and point.get("final")]
    if not earlier or not now:
        return None
    return now / earlier[-1]["final"] - 1


def categories(research: Path, today: date) -> dict[str, object]:
    from quant_platform.research.daily import PASSING_TIERS
    from quant_platform.research.roles import summary

    found = summary(research)
    rows = []
    for row in found.get("roles") or []:
        now = (row.get("now") or {}).get("final")
        grades = row.get("grades") or {}
        rows.append({
            "role": row["role"], "note": row.get("note"), "rules": row.get("rules"),
            "passing": sum(count for grade, count in grades.items() if grade in PASSING_TIERS),
            "t0": grades.get("T0 候選", 0) + grades.get("T0", 0),
            "risky": grades.get("T0 候選（高風險）", 0) + grades.get("T0（高風險）", 0), "t1": grades.get("T1", 0),
            "best": row.get("now") or {}, "change_1d": _growth(row.get("history") or [], now, today - timedelta(days=1)),
            "change_7d": _growth(row.get("history") or [], now, today - timedelta(days=7)),
            "change_last": row.get("change"), "last_run": row.get("last_run"), "growth_total": row.get("growth_total"),
            "first": row.get("first"), "history": row.get("history") or []})
    return {"rows": rows, "benchmark": found.get("benchmark"),
            "rules": sum(row["rules"] or 0 for row in rows), "passing": sum(row["passing"] for row in rows)}


def factors(research: Path) -> dict[str, object]:
    """Every factor of the latest strength report with its family and how many passing strategies use it."""
    from quant_platform.research.categories import _family
    from quant_platform.research.daily import ENGINE_VERSION, PASSING_TIERS, tier
    from quant_platform.research.registry import TrialRegistry, newest

    report_path = _newest(research / "factors", "strength-*-recent.json")
    report = _read(report_path)
    used: dict[str, int] = {}
    latest = newest(TrialRegistry(research / "trials.jsonl").records(),
                    lambda record: record.kind == "candidate" and record.period == "recent"
                    and str((record.metrics or {}).get("engine", "")).startswith(ENGINE_VERSION.split("+")[0]))
    for record in latest.values():
        if tier(record.metrics or {})[0] not in PASSING_TIERS:
            continue
        spec = _read(research / "reports" / record.report_file).get("spec") or {}
        rules = [sleeve.get("rule") or {} for sleeve in spec.get("sleeves") or []] if spec.get("kind") == "blend" else [spec]
        names = {name for rule in rules for name in (rule.get("factors") or {})}
        for name in names:
            used[name] = used.get(name, 0) + 1
    rows = []
    order = {"強": 0, "強（短歷史）": 1, "反向": 2, "反向（短歷史）": 3, "不穩": 4, "不穩（短歷史）": 5, "弱": 6}
    for name, item in (report.get("factors") or {}).items():
        periods = item.get("periods") or {}
        rows.append({"name": name, "label": item.get("label"), "family": _family(name, 1.0), "verdict": item.get("verdict"),
                     "before": (periods.get("before_regime") or {}).get("top_excess_year"),
                     "regime": (periods.get("regime") or {}).get("top_excess_year"),
                     "recent": (periods.get("since_2024") or {}).get("top_excess_year"),
                     "t": (periods.get("recent_all") or {}).get("t"), "used": used.get(name, 0)})
    rows.sort(key=lambda row: (order.get(row["verdict"], 9), -(row["used"]), -(row["before"] or -9)))
    models = sorted(((name, count) for name, count in used.items() if name.startswith(("ml_", "dl_", "rl_", "t0_"))),
                    key=lambda pair: -pair[1])
    return {"rows": rows, "built": (report.get("generated_at") or "")[:16].replace("T", " "),
            "strong": sum(1 for row in rows if row["verdict"] == "強"), "models_used": models}


def analyses(research: Path) -> list[dict[str, object]]:
    """Each standing analysis or check: name, when, what it found, where to look."""
    items = []

    checks = _read(research / "invariants.json")
    passed = [check for check in checks.get("checks") or [] if check.get("ok")]
    items.append({"name": "需求檢查（使用者的決定逐條核對）", "when": str(checks.get("at") or "")[:16].replace("T", " "),
                  "result": f"{len(passed)}／{len(checks.get('checks') or [])} 項符合", "link": "/system"})
    audit_path = _newest(research / "audit", "trade-audit-*.json")
    audit = _read(audit_path)
    violations = sum((audit.get("violation_counts") or {}).values()) if audit else None
    items.append({"name": "交易規則稽核（每筆委託照台股規則）", "when": _stamp(audit_path),
                  "result": (f"{len(audit.get('rules') or [])} 個規則、{int(audit.get('orders') or 0):,} 筆委託、違規 {violations}"
                             if audit else "還沒跑"), "link": "/research?tab=jobs"})
    strength_path = _newest(research / "factors", "strength-*-recent.json")
    strength = _read(strength_path)
    strong = sum(1 for item in (strength.get("factors") or {}).values() if item.get("verdict") == "強")
    items.append({"name": "因子強弱（2015-06 起每週排名）", "when": _stamp(strength_path),
                  "result": f"{len(strength.get('factors') or {})} 個因子、{strong} 個強", "link": "/research?tab=factors"})
    window = _read(_newest(research / "diagnostics", "window-weight-*.json"))
    whole = ((window.get("summary") or {}).get("window:all") or {}).get("ic")
    reliability = window.get("reliability")
    items.append({"name": "長歷史與短歷史的權重", "when": _stamp(_newest(research / "diagnostics", "window-weight-*.json")),
                  "result": (f"2015 起全部等權最準（排序相關 {whole}）；最近 5 年的可信度 {reliability} → 主賽道不變、"
                             "已開短歷史賽道（使用者 10/21 同意）" if window else "還沒跑"),
                  "link": "/project/research_method"})
    intraday_path = _newest(research / "diagnostics", "intraday-*.json")
    intraday = _read(intraday_path)
    if intraday:
        gains = intraday.get("gains") or {}
        sell = ((gains.get("open") or {}).get("sell") or {}).get("mean")
        buy = ((gains.get("open") or {}).get("buy") or {}).get("mean")
        result = (f"{intraday.get('sampled')} 筆：賣在隔天開盤比收盤多 {sell:+.2%}、買在開盤比收盤 {buy:+.2%}"
                  "（→ 開盤賣、收盤買）" if sell is not None and buy is not None else "")
    else:
        result = "還沒跑"
    items.append({"name": "盤中成交時機（分 K）", "when": _stamp(intraday_path), "result": result,
                  "link": "/project/research_method"})
    feasible_path = _newest(research / "diagnostics", "open-feasibility-*.json")
    feasible = _read(feasible_path)
    if feasible.get("sells"):
        at_open = (feasible.get("gain_open") or {}).get("mean") or 0.0
        at_odd = (feasible.get("gain_first_odd_lot") or {}).get("mean") or 0.0
        cap = (feasible.get("engine_cap_share_of_auction") or {}).get("median") or 0.0
        result = (f"{feasible['sells']} 筆賣出：開盤賣比收盤多 {at_open:+.2%}、零股 09:10 賣多 {at_odd:+.2%}"
                  + ("（零股 09:10 也站得住）" if at_odd >= at_open / 2 else "（零股 09:10 不到一半：要另算）")
                  + f"；引擎上限是開盤量的 {cap:.0%}（中位數）" + ("，沒超過 25% 不改引擎" if cap <= 0.25 else "，超過 25% 要改引擎"))
    else:
        result = "還沒跑"
    items.append({"name": "開盤賣做不做得到（零股 09:10、開盤量）", "when": _stamp(feasible_path), "result": result,
                  "link": "/project/research_method"})
    checks = sorted((research / "diagnostics").glob("limits-check-*.json"), key=lambda path: path.stat().st_mtime) if (
        research / "diagnostics").is_dir() else []
    engine_path = next((path for path in reversed(checks) if not _read(path).get("official_limits")), None)
    official_path = next((path for path in reversed(checks) if _read(path).get("official_limits")), None)
    limits, verified = _read(engine_path), _read(official_path)
    result = (f"{limits.get('compared', 0):,} 個股票×交易日：鎖死判斷不同 {limits.get('locks_differ')} 個" if limits else "還沒跑")
    if verified:                                 # 2026-10-22: engine 1.6.0's limits checked before the switch
        result += f"；引擎 1.6.0（交易所漲跌停價，10/31 打開）驗證：不同 {verified.get('locks_differ')} 個"
    elif limits:
        result += "（引擎 1.6.0 修正，10/31 部署）"
    items.append({"name": "官方漲跌停價核對引擎", "when": _stamp(official_path or engine_path), "result": result,
                  "link": "/project/research_method"})
    signals_path = _newest(research / "diagnostics", "market-signals-*.json")
    signals = _read(signals_path).get("signals") or {}
    useful = [name for name, item in signals.items() if item.get("useful")]
    # 2026-10-22: a pass whose worst fifth is a middle one (not the most bought or sold) has no story — watched forward,
    # not made a filter (research_method §7「八大行庫」)
    middle = [name for name in useful if any((period.get("worst") or 0) not in (1, 5)
                                             for period in (signals[name].get("periods") or {}).values())]
    if not signals:
        result = "還沒跑"
    elif not useful:
        result = f"{len(signals)} 個訊號、過條件 0 個（兩段期間最差的組別不同，不做成濾網）"
    else:
        result = (f"{len(signals)} 個訊號、過條件 {len(useful)} 個：{'、'.join(useful)}"
                  + ("（最差的是中間一組、沒有機制，不做成濾網，前向觀察）" if middle else ""))
    items.append({"name": "大盤訊號（融資維持率、外資台指期、八大行庫）", "when": _stamp(signals_path), "result": result,
                  "link": "/project/research_method"})
    robust_path = _newest(research / "robustness", "*.json")
    robust = _read(robust_path)
    items.append({"name": "穩健性（成本加倍、晚一天成交）", "when": _stamp(robust_path),
                  "result": f"{len(robust.get('rows') or [])} 個規則" if robust else "還沒跑", "link": "/research#robustness"})
    leak_path = _newest(research / "leakcheck", "leakcheck-*.json")
    items.append({"name": "防偷看檢查（日期截斷重算因子）", "when": _stamp(leak_path), "result": "有紀錄" if leak_path else "還沒跑",
                  "link": "/project/research_method"})
    scans = [(path, _read(path)) for path in (research / "scans").glob("*/summary.json")] if (research / "scans").is_dir() else []
    total = sum(int(summary.get("candidates") or 0) for _path, summary in scans)
    latest_scan = max(scans, key=lambda pair: pair[0].stat().st_mtime) if scans else None
    blends = [(path, _read(path)) for path in (research / "blend_scan").glob("finalists-*.json")] if (
        research / "blend_scan").is_dir() else []
    # every blend round searches all pairs and threes of the pool at that time, so a later round holds the earlier
    # rounds' blends: the biggest round is the count, not the sum; a rerun of the same pool is not a round
    rounds = {(summary.get("pool"), summary.get("candidates")): summary for _path, summary in blends}
    biggest = max(rounds.values(), key=lambda summary: int(summary.get("candidates") or 0)) if rounds else {}
    tiers = latest_scan[1].get("tiers") or {} if latest_scan else {}
    items.append({"name": "大規模搜尋（單一策略）", "when": _stamp(latest_scan[0]) if latest_scan else None,
                  "result": f"{len(scans)} 輪、各輪合計 {total:,} 個策略設定" + (
                      f"；最近一輪 {int(latest_scan[1].get('candidates') or 0):,} 個，最後名單"
                      + ("、".join(f"{tier} {count} 個" for tier, count in sorted(tiers.items())) or "沒有過門檻的")
                      if latest_scan else ""),
                  "link": "/project/research_method"})
    items.append({"name": "組合搜尋（兩檔／三檔組合）",
                  "when": _stamp(max((path for path, _summary in blends), key=lambda path: path.stat().st_mtime)) if blends else None,
                  "result": (f"{len(rounds)} 輪；最大一輪 {int(biggest.get('candidates') or 0):,} 個組合"
                             f"（{biggest.get('pool')} 個策略的兩檔、三檔組合，每輪都含前幾輪的組合，所以不加總）")
                  if rounds else "還沒跑", "link": "/project/research_method"})
    from quant_platform.research import rolling

    month = rolling.summary(rolling.latest(research)) or {}
    items.append({"name": "每月滾動（截止日以前挑、之後前向）", "when": month.get("data_through"),
                  "result": (f"截止 {month.get('cutoff')}：{month.get('graded')} 個規則、{month.get('passed')} 個過門檻、"
                             f"前向測試贏 0050 {len(month.get('usable') or [])}／{len(month.get('holdout') or [])}")
                  if month else "還沒跑", "link": "/research/roles#rolling"})
    live_path = _newest(research / "live", "????-??-??/latest.json")
    items.append({"name": "盤中資金流（每 15 秒全市場快照）", "when": _stamp(live_path),
                  "result": "交易日 09:00～13:30" if live_path else "還沒有資料", "link": "/market/live"})
    return items


def versions(research: Path, history: Path) -> dict[str, object]:
    from quant_platform.research.daily import ENGINE_VERSION, MODEL_FACTORS
    from quant_platform.research.model import MODELS
    from quant_platform.research.rolling import cutoff_for
    from quant_platform.research.scan import failed_models
    from quant_platform.research.stock_forward import StockForwardTracker

    models_root = research / "models"
    failed = failed_models(models_root)
    cutoff = cutoff_for(datetime.now(TAIPEI).date()).isoformat()
    tracker = StockForwardTracker(research)
    reads: dict[str, int] = {}
    for item in tracker.tracked():
        if item.get("stopped"):
            continue
        for name in tracker.uses(item):
            reads[name] = reads.get(name, 0) + 1
    rows = []
    for name, spec in MODELS.items():
        folder = models_root / spec["version"]
        if not folder.is_dir():
            continue
        meta = _read(folder / "meta.json")
        monthly = folder / "monthly"
        refitted = monthly.is_dir() and any(monthly.glob(f"{cutoff}*"))
        rows.append({"name": name, "label": MODEL_FACTORS.get(name, name), "version": spec["version"],
                     "gate": "沒過" if name in failed else "過",
                     "trained": str(meta.get("trained_at") or "")[:10], "refitted": refitted, "accounts": reads.get(name, 0)})
    rows.sort(key=lambda row: (-row["accounts"], row["version"]))

    def newest_date(path: Path, column: str = "date") -> str | None:
        import pyarrow.compute as pc
        import pyarrow.parquet as pq

        if not path.is_file():
            return None
        try:
            value = pc.max(pq.read_table(path, columns=[column])[column]).as_py()
        except Exception:  # noqa: BLE001 - a column of another type
            return None
        return str(value)[:10] if value is not None else None

    prices = sorted((history / "stocks" / "twse").glob("*.parquet"))
    plan = _read(history / "raw" / "finmind" / "plan.json")
    data = [
        {"name": "上市行情（收盤）", "through": newest_date(prices[-1]) if prices else None},
        {"name": "籌碼（外資持股、融資融券、法人）", "through": newest_date(history / "chips" / "TaiwanStockShareholding.parquet")},
        {"name": "集保股權分散（每週）", "through": newest_date(history / "tdcc" / "holding.parquet")},
        {"name": "融資維持率（FinMind 付費）", "through": newest_date(history / "paid" / "TaiwanStockMarginMaintenance.parquet")},
        {"name": "鉅額交易（FinMind 付費）", "through": newest_date(history / "paid" / "TaiwanStockBlockTrade.parquet")},
    ]
    for item in data:
        value = item["through"]
        if value and len(value) == 8 and value.isdigit():
            item["through"] = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return {"engine": ENGINE_VERSION, "models": rows, "cutoff": cutoff, "data": data,
            "finmind": {"title": plan.get("title"), "limit": plan.get("limit"), "expires": plan.get("expires")} if plan else None,
            "accounts": sum(1 for item in tracker.tracked() if not item.get("stopped"))}


def overview(research_dir: str | Path) -> dict[str, object]:
    research = Path(research_dir)
    history = research / "history"
    watched = [research / "trials.jsonl", _newest(research / "factors", "strength-*.json"),
               _newest(research / "diagnostics", "*.json"), research / "invariants.json",
               research / "forward" / "stocks" / "tracked.json", _newest(research / "audit", "*.json")]
    key = tuple(path.stat().st_mtime if path and path.is_file() else 0 for path in watched)
    cached = _CACHE.get(str(research))
    if cached and cached[0] == key:
        return cached[1]
    today = datetime.now(TAIPEI).date()
    found = {"built": datetime.now(TAIPEI).strftime("%Y-%m-%d %H:%M"), "categories": categories(research, today),
             "factors": factors(research), "analyses": analyses(research), "versions": versions(research, history)}
    _CACHE[str(research)] = (key, found)
    return found
