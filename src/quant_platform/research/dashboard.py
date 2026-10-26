"""2026-10-23 (使用者：研究進展看不懂；我要的是總覽就是一個完整的 dashboard，點深度學習就跳出浮動視窗顯示它目前大致的
訓練狀況，只放重要資料；策略類別有用新的付費資料做訓練嗎、有去做重新的優化嗎): what the research overview's dashboard
shows — the headline numbers, one card per strategy family and, for the window a card opens, the family's models
(what they learn from, which paid data, when trained, their gate), the data its rules read, its best rules then and
since 2020-10, how it did in each period (research/eras.py), its forward accounts and its latest optimizations."""

from __future__ import annotations

import json
import statistics
from datetime import date
from pathlib import Path

_MODEL_META: dict[str, dict] = {}
_VIEW: dict[str, object] = {}
# the robots' own models (their rules name the robot, not a model factor)
MODEL_PREFIXES = {"RL 機器人": ("block-rl", "free-rl", "factor-rl"), "技術分析機器人": ("tech-rl",)}


def source_groups() -> list[tuple[str, str, bool, frozenset]]:
    """(key, label, paid, factor names) for each kind of data a factor reads."""
    from quant_platform.research.chips import (
        BRANCH_FACTORS, CHIP_FACTORS, DEALER_FACTORS, GOVBANK_FACTORS, HOLDER_FACTORS, LENDING_FACTORS, PAID_FACTORS,
        SHORT_FACTORS,
    )
    from quant_platform.research.fundamentals import STATEMENT_FACTORS
    from quant_platform.research.model import MONTHLY_REVENUE

    paid = frozenset(PAID_FACTORS) - frozenset(GOVBANK_FACTORS) - frozenset(BRANCH_FACTORS)
    return [("chips", "法人、融資券、借券（證交所）", False,
             frozenset(CHIP_FACTORS) | frozenset(DEALER_FACTORS) | frozenset(SHORT_FACTORS) | frozenset(LENDING_FACTORS)),
            ("statements", "季財報", False, frozenset(STATEMENT_FACTORS)),
            ("revenue", "月營收（落後指標）", False, frozenset(MONTHLY_REVENUE)),
            ("holders", "集保股東人數與大戶（FinMind 付費）", True, frozenset(HOLDER_FACTORS)),
            ("paid", "融資維持率、借貸、鉅額、主力（FinMind 付費）", True, paid),
            ("govbank", "八大行庫（FinMind 付費）", True, frozenset(GOVBANK_FACTORS)),
            ("branches", "分點（FinMind 付費）", True, frozenset(BRANCH_FACTORS))]


def model_meta(models: Path, version: str) -> dict:
    key = f"{models}:{version}"
    if key not in _MODEL_META:
        try:
            _MODEL_META[key] = json.loads((models / version / "meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _MODEL_META[key] = {}
    return _MODEL_META[key]


def factor_names(spec: dict) -> set[str]:
    """Every factor a rule reads: its own, its vetoes', its sleeves' (a blend)."""
    from quant_platform.research.daily import VETO_FACTORS

    names = set((spec.get("factors") or {}).keys())
    names.update(VETO_FACTORS.get(str(spec.get("exclude") or "none"), ()))
    for sleeve in spec.get("sleeves") or []:
        names |= factor_names(sleeve.get("rule") or {})
    return names


def sources(spec: dict, models: Path) -> tuple[set[str], set[str]]:
    """(the data kinds a rule reads — models through their features —, the model versions it uses)."""
    from quant_platform.research.model import MODELS

    names = factor_names(spec)
    versions = {MODELS[name]["version"] for name in names if name in MODELS}
    for version in versions:
        names |= set(model_meta(models, version).get("features") or [])
    found = {key for key, _label, _paid, group in source_groups() if names & group}
    if "news_veto" in names or spec.get("news_veto"):
        found.add("news")
    return found, versions


LABELS = {"rank": "之後 20 日報酬的排名", "excess": "之後 20 日比當天中位數多賺多少", "ensemble": "提升樹集成（多個模型平均）",
          "deep": "深度學習第一版（排名）", "deep2": "之後 20 日會不會進當天前 10%（深度學習）",
          "deep_risk": "之後會不會大跌（風險模型）", "deep_risk2": "扣掉波動後會不會大跌", "meta": "這次買會不會贏 0050（第二層）"}
PREFIX_LABELS = {"block-rl": "每年學各訊號區塊的權重（強化學習）", "free-rl": "每天自己決定每檔比重與現金（強化學習）",
                 "factor-rl": "學全部因子的權重（強化學習）", "tech-rl": "只看價量的機器人（強化學習）",
                 "worst-rl": "學著賠最多（反向檢查）", "exit-": "學什麼時候賣"}


def model_view(models: Path, version: str, gates: dict[str, object]) -> dict[str, object]:
    """One model: what it learns, its features (and the paid ones), how it is trained, when, its gate."""
    meta = model_meta(models, version)
    features = meta.get("features") or meta.get("factors") or []
    paid = [label for key, label, is_paid, group in source_groups() if is_paid and set(features) & group]
    monthly = sorted(path.stem for path in (models / version / "monthly").glob("*.pkl")) \
        if (models / version / "monthly").is_dir() else []
    years = sorted((meta.get("years") or meta.get("yearly") or {}).keys())
    label = LABELS.get(str(meta.get("label"))) or next((text for prefix, text in PREFIX_LABELS.items()
                                                         if version.startswith(prefix)), meta.get("label"))
    return {"version": version, "label": label, "features": len(features), "paid": paid,
            "trained_at": str(meta.get("trained_at") or "")[:10], "years": f"{years[0]}～{years[-1]}" if years else "",
            "monthly": monthly[-1] if monthly else None, "gate": gates.get(version)}


LOG_FAMILIES = {"深度學習": ("深度學習",), "機器學習": ("機器學習", "新資料（FinMind）", "新資料（集保）"),
                "RL 機器人": ("強化學習",), "技術分析機器人": ("技術機器人",), "組合帳戶": ("組合", "合併"),
                "多因子": ("搜尋", "大規模搜尋", "規則"), "籌碼": ("新資料", "新資料（八大行庫）", "新資料（分點）")}
LOG_WORDS = {"深度學習": ("深度學習",), "機器學習": ("模型 ", "機器學習"), "RL 機器人": ("RL", "機器人"),
             "T0 共識清單": ("共識",), "組合帳戶": ("組合",), "趨勢動能": ("趨勢", "動能"), "籌碼": ("籌碼", "集保")}


def family_log(rows: list[dict], family: str, count: int = 6) -> list[dict]:
    families, words = LOG_FAMILIES.get(family, ()), LOG_WORDS.get(family, (family,))
    picked = [row for row in rows if row.get("family") in families or any(word in str(row.get("what")) for word in words)]
    return picked[:count]


def view(research: str | Path, start: str, optimizations: list[dict], gates: dict[str, object]) -> dict[str, object]:
    """The dashboard (kept until the files it reads change: about 7 s to build)."""
    research = Path(research)
    paths = [research / "trials.jsonl", research / "forward" / "stocks" / "log.jsonl",
             research / "forward" / "stocks" / "tracked.json", research / "rolling", research / "models",
             research / "scans", research / "blend_scan", research / "stress", research / "audit", research / "leakcheck",
             research / "invariants.json", research / "forward" / "stocks",
             *sorted((research / "scans").glob("scan-full-*/spread.npz")),
             *sorted((research / "scans").glob("scan-full-*/progress-*-of-*.json"))]
    key = (str(research.resolve()), start, len(optimizations), json.dumps(optimizations[:1], ensure_ascii=False),
           tuple((path.stat().st_mtime_ns, path.stat().st_size) if path.exists() else 0 for path in paths))
    if _VIEW.get("key") != key:
        _VIEW.update(key=key, value=_build(research, start, optimizations, gates))
    return _VIEW["value"]                           # type: ignore[return-value]


def wan(value: float) -> str:
    return f"{value / 10000:,.0f} 萬"


def pct(value: float) -> str:
    return "0%" if abs(value) < 0.005 else f"{value * 100:+.0f}%"


def curve(research: Path, row: dict | None) -> dict[str, list] | None:
    """A rule's month-end account (value, 0050's, the money put in) from its report."""
    if not row or not row.get("report_file"):
        return None
    try:
        return json.loads((research / "reports" / row["report_file"]).read_text(encoding="utf-8")).get("curve") or None
    except (OSError, ValueError):
        return None


def calmest(research: Path) -> dict | None:
    """The richest passing rule on the money-against-falls frontier that fell no further than 0050 in 0050's worst
    fall (the frontier's calmest end when none did)."""
    from quant_platform.research.downside import table

    from quant_platform.research.registry import TrialRegistry

    rows = [row for row in table(research) if row.get("frontier") and row.get("worst_fall")]
    calm = [row for row in rows if row["worst_fall"]["gap"] >= 0]
    found = (max(calm, key=lambda row: row["final_value"] or 0) if calm
             else max(rows, key=lambda row: row["worst_fall"]["gap"], default=None))
    if found is None:
        return None
    record = next((record for record in TrialRegistry(research / "trials.jsonl").records()
                   if record.trial_id == found["trial"]), None)
    return {**found, "trial_id": found["trial"], "spec_hash": found["rule_hash"],
            "report_file": record.report_file if record else None}


def charts(research: Path, start: str, overall: dict | None, ordinary: dict | None, steady: dict | None,
           benchmark: float | None, periods: dict) -> dict[str, str]:
    """2026-10-25 (使用者：我的 dashboard 呢、線圖呢；每年比 0050 那些數字是什麼意思): the dashboard's charts (research/charts.py)."""
    from datetime import datetime as _datetime

    from quant_platform.research import charts as svg
    from quant_platform.research.progress import forward, timeline
    from quant_platform.research.stress import latest as stress_latest

    output: dict[str, str] = {}

    def month(text: str) -> date:
        return date(int(text[:4]), int(text[5:7]), 1)

    # 1. the money over 2015-06～2026-09: the richest, the richest ordinary, the calmest, 0050 and the money put in
    picks, seen = [], set()
    for row, css, short in ((overall, "v1", "錢最多"), (ordinary, "v2", "一般風險錢最多"), (steady, "v3", "跌市最穩")):
        key = (row or {}).get("spec_hash") or (row or {}).get("rule_hash")
        if row and key not in seen and curve(research, row):
            seen.add(key)
            picks.append((row, css, short))
    if picks:
        base = curve(research, picks[0][0])
        points = [{"x": month(key), "bench": value[1], "paid": value[2]} for key, value in sorted(base.items())]
        lines = []
        for number, (row, css, short) in enumerate(picks):
            found = curve(research, row)
            for point in points:
                value = found.get(point["x"].strftime("%Y-%m"))
                point[f"s{number}"] = value[0] if value else None
            lines.append({"key": f"s{number}", "label": f"{short} #{row.get('trial_id') or row.get('trial')}", "css": css,
                          "short": short})
        lines += [{"key": "bench", "label": "0050（同樣的錢）", "css": "vref", "dashed": True, "short": "0050"},
                  {"key": "paid", "label": "投入的錢", "css": "vput", "dashed": True, "short": "投入"}]
        output["growth"] = svg.line(points, lines, wan, lambda x, full: x.strftime("%Y-%m") if full else str(x.year),
                                    "30 萬＋每月 1 萬，帳戶每月底的價值")
        output["growth_lines"] = lines
    # 2. the research's progress: the best backtest known at each hour
    line = timeline(research)
    if line["points"]:
        output["progress"] = svg.line(
            line["points"], [{"key": "any", "label": "全部過門檻的最好", "css": "v1", "short": "全部最好"},
                             {"key": "t0", "label": "一般風險（T0 候選）最好", "css": "v2", "short": "一般風險最好"}],
            wan, lambda x, full: x.strftime("%m/%d %H:%M" if full else "%m/%d"), "研究進展：回測最好的成績",
            x_key="at", step=True,
            guides=[(line["benchmark"], f"0050 同樣的錢 {wan(line['benchmark'])}")] if line.get("benchmark") else [],
            # an engine version fixes earlier errors and runs every rule again: the best can drop there
            marks=[(item["at"], "引擎 " + item["engine"].replace("daily-", "")[:3]) for item in line.get("engines") or []
                   if str(item.get("engine", "")).endswith(".0")])
    # 3. the forward accounts since the cutoff: everything sold at each close
    fwd = forward(research, start)
    if fwd["series"]:
        points = [{**row, "x": date.fromisoformat(row["date"])} for row in fwd["series"]]
        output["forward"] = svg.line(
            points, [{"key": "median_gain", "label": "帳戶中位數", "css": "v1", "short": "帳戶中位數"},
                     {"key": "bench_gain", "label": "0050（同樣的錢）", "css": "vref", "dashed": True, "short": "0050"}],
            lambda value: f"{value:+,.0f} 元", lambda x, full: x.strftime("%m/%d"), "前向帳戶全部賣掉後賺賠",
            band=("p25_gain", "p75_gain"), guides=[(0.0, "沒賺沒賠")])
        output["forward_last"] = fwd["series"][-1]
    # 4. each period: what 0050 made a year, what the median rule made a year (and how many beat 0050)
    groups = []
    for label in periods.get("labels") or []:
        bench, found = periods["benchmark"].get(label), periods["all"]["periods"].get(label) or {}
        if bench is None or found.get("median") is None:
            continue
        short = "–".join(part[:4] if index == 0 else part[2:4] for index, part in enumerate(label.split("～")))
        groups.append({"label": short, "short": short[2:], "note": f"{found['beat']:.0%} 贏",
                       "values": {"bench": bench, "median": bench + found["median"]},
                       "titles": {"bench": f"{label} 0050 每年 {pct(bench)}",
                                  "median": f"{label} 規則每年 {pct(bench + found['median'])}（中位數），"
                                            f"{found['beat']:.0%} 的規則贏 0050"}})
    if groups:
        output["periods"] = svg.bars(groups, [{"key": "bench", "label": "0050", "css": "vref"},
                                              {"key": "median", "label": "規則（中位數）", "css": "v1"}],
                                     pct, "各時期每年報酬：0050 與規則的中位數")
    # 5. each bear: how far 0050 fell, the strategies (median) and the same with the half guard
    report, checked = stress_latest(research), stress_latest(research, "protect")
    if report and report.get("scenarios"):
        rows = [row for row in report.get("rows") or [] if not row.get("error")]
        halves = [row["variants"]["taiex_half"] for row in (checked or {}).get("rows") or []
                  if not row.get("error") and "bears" in row.get("variants", {}).get("taiex_half", {})]
        groups = []
        for position, scenario in enumerate(report["scenarios"]):
            falls = [row["bears"][position]["fall"] for row in rows if len(row.get("bears") or []) > position]
            half = [item["bears"][position]["fall"] for item in halves if len(item["bears"]) > position]
            values = {"bench": scenario["fall"], "plain": statistics.median(falls) if falls else None,
                      "half": statistics.median(half) if half else None}
            year = scenario["high"][:4] if scenario["high"][:4] != "2007" else "2008"
            groups.append({"label": year, "short": year[2:],
                           "note": f"撐過 {scenario.get('held', 0)}／{scenario.get('tested', len(rows))}",
                           "values": values,
                           "titles": {"bench": f"{scenario['high'][:7]}～{scenario['low'][:7]} 0050 跌 {pct(scenario['fall'])}",
                                      "plain": f"同一段 20 個策略跌幅中位數 {pct(values['plain'] or 0)}",
                                      "half": f"加「跌破年線減碼一半」後中位數 {pct(values['half'] or 0)}"}})
        output["stress"] = svg.bars(groups, [{"key": "bench", "label": "0050", "css": "vref"},
                                             {"key": "plain", "label": "策略（中位數）", "css": "v1"},
                                             {"key": "half", "label": "加減碼一半", "css": "v3"}],
                                    pct, "每一次空頭：0050 與策略跌多少")
    output["built"] = _datetime.now().strftime("%H:%M")
    return output


def family_charts(research: Path, family: dict, periods: dict) -> dict[str, str]:
    """2026-10-25: the window's charts — the family's best rule against 0050, and each period's yearly return."""
    from quant_platform.research import charts as svg

    output: dict[str, str] = {}
    best = family.get("best")
    found = curve(research, best)
    if found:
        points = [{"x": date(int(key[:4]), int(key[5:7]), 1), "rule": value[0], "bench": value[1], "paid": value[2]}
                  for key, value in sorted(found.items())]
        output["growth"] = svg.line(points, [
            {"key": "rule", "label": f"最好的 #{best.get('trial_id')}", "css": "v1", "short": "最好的"},
            {"key": "bench", "label": "0050（同樣的錢）", "css": "vref", "dashed": True, "short": "0050"},
            {"key": "paid", "label": "投入的錢", "css": "vput", "dashed": True, "short": "投入"}],
            wan, lambda x, full: x.strftime("%Y-%m") if full else str(x.year), "這一類最好的規則與 0050")
    own = (family.get("eras") or {}).get("periods") or {}
    groups = []
    for label in periods.get("labels") or []:
        bench, item = periods["benchmark"].get(label), own.get(label) or {}
        if bench is None or item.get("median") is None:
            continue
        short = "–".join(part[:4] if index == 0 else part[2:4] for index, part in enumerate(label.split("～")))
        groups.append({"label": short, "short": short[2:],
                       "note": f"{item['beat']:.0%} 贏" if item.get("beat") is not None else "",
                       "values": {"bench": bench, "median": bench + item["median"]}})
    if groups:
        output["periods"] = svg.bars(groups, [{"key": "bench", "label": "0050", "css": "vref"},
                                              {"key": "median", "label": "這一類（中位數）", "css": "v1"}],
                                     pct, "各時期每年報酬：0050 與這一類的中位數")
    return output


def _build(research: Path, start: str, optimizations: list[dict], gates: dict[str, object]) -> dict[str, object]:
    """The dashboard: headline numbers and one entry per family (the window's contents too)."""
    from quant_platform.research.categories import FAMILY_NOTES, FAMILY_ORDER, best_of
    from quant_platform.research.daily import PASSING_TIERS, short_track
    from quant_platform.research.eras import analyze as eras
    from quant_platform.research.pool import _spec_of, pool_view
    from quant_platform.research.progress import search_scale, toward_t0
    from quant_platform.research.stock_forward import StockForwardTracker

    research = Path(research)
    models = research / "models"
    pool = pool_view(research, basis="seed")
    tracker = StockForwardTracker(research)
    summary = {row["rule_hash"]: row for row in tracker.summary()}
    live = {key: row for key, row in summary.items() if not row.get("stopped") and row.get("since") == start and row.get("sell_all")}
    periods = eras(research)
    by_rule = {row["spec_hash"]: row["periods"] for row in periods["rows"]}       # each rule's own periods
    labels = {key: label for key, label, _paid, _group in source_groups()}
    labels["news"] = "新聞（LLM 標題事件）"
    paid_keys = {key for key, _label, paid, _group in source_groups() if paid}
    main = [row for row in pool["daily"] if not row.get("uses_0050") and row.get("final_value")
            and not short_track({"start": row.get("start")})]
    groups: dict[str, list[dict]] = {}
    for row in pool["daily"]:
        if not row.get("uses_0050") and row.get("final_value"):
            groups.setdefault((row.get("category") or {}).get("family") or "其他", []).append(row)
    benchmark = next((row.get("benchmark_final_value") for row in main if row.get("benchmark_final_value")), None)
    recent_benchmark = next((row.get("since_2020_benchmark_final_value") for row in main
                             if row.get("since_2020_benchmark_final_value")), None)
    families = []
    for family, members in groups.items():
        spec_of = {row["spec_hash"]: _spec_of(research, row.get("report_file")) for row in members}
        used: dict[str, set[str]] = {}
        versions: set[str] = set()
        for key, spec in spec_of.items():
            used[key], found = sources(spec, models)
            versions |= found
        prefixes = MODEL_PREFIXES.get(family)
        if prefixes and models.is_dir():
            versions |= {path.name for path in models.iterdir() if path.name.startswith(prefixes)}
        mains = [row for row in members if not short_track({"start": row.get("start")})]
        passing = [row for row in mains if row["tier"] in PASSING_TIERS]
        best = best_of(mains) or best_of(members)
        recent = max((row for row in passing if row.get("since_2020_final_value")),
                     key=lambda row: row["since_2020_final_value"], default=None)
        with_paid = [row for row in mains if used[row["spec_hash"]] & paid_keys]
        accounts = [live[row["spec_hash"]] for row in members if row["spec_hash"] in live]
        ahead = [item["sell_all"]["ahead"] for item in accounts]
        counts: dict[str, int] = {}
        for row in mains:
            for key in used[row["spec_hash"]]:
                counts[key] = counts.get(key, 0) + 1
        top = sorted(passing or mains, key=lambda row: -row["final_value"])[:8]
        families.append({
            "family": family, "note": FAMILY_NOTES.get(family, ""), "rules": len(mains), "passing": len(passing),
            "short_rules": len(members) - len(mains), "best": best, "recent": recent,
            "paid_rules": len(with_paid), "paid_passing": sum(row["tier"] in PASSING_TIERS for row in with_paid),
            "best_paid": best_of(with_paid), "uses_paid": bool(with_paid),
            "sources": [{"key": key, "label": labels[key], "paid": key in paid_keys, "rules": counts.get(key, 0)}
                        for key in labels if counts.get(key)],
            "models": [model_view(models, version, gates) for version in sorted(versions)],
            "accounts": len(accounts), "ahead_share": (sum(value > 0 for value in ahead) / len(ahead)) if ahead else None,
            "median_ahead": statistics.median(ahead) if ahead else None, "best_ahead": max(ahead) if ahead else None,
            "eras": periods["families"].get(family), "log": family_log(optimizations, family),
            "faded": [row for row in periods["faded"] if row["family"] == family][:5],
            "top": [{**row, "forward": (live.get(row["spec_hash"]) or {}).get("sell_all"),
                     "periods": by_rule.get(row["spec_hash"])} for row in top],
        })
    order = {name: index for index, name in enumerate(FAMILY_ORDER)}
    families.sort(key=lambda item: (-(item["best"]["final_value"] if item["best"] else 0), order.get(item["family"], 99)))
    overall = best_of(main)
    ordinary = max((row for row in main if row["tier"] in ("T0", "T0 候選")), key=lambda row: row["final_value"], default=None)
    recent_all = max((row for row in main if row["tier"] in PASSING_TIERS and row.get("since_2020_final_value")),
                     key=lambda row: row["since_2020_final_value"], default=None)
    if overall and overall.get("benchmark_final_value"):          # 0050 from the same run as the best rule
        benchmark = overall["benchmark_final_value"]
    if recent_all and recent_all.get("since_2020_benchmark_final_value"):
        recent_benchmark = recent_all["since_2020_benchmark_final_value"]
    cashed = [row["sell_all"] for row in live.values()]
    forward = {"accounts": len(cashed), "start": start,
               "median_gain": statistics.median(item["gain"] for item in cashed) if cashed else None,
               "bench_gain": statistics.median(item["bench_gain"] for item in cashed) if cashed else None,
               "ahead_share": sum(item["ahead"] > 0 for item in cashed) / len(cashed) if cashed else None}
    from quant_platform.research.stress import SHORT, latest as stress_latest

    stress = None
    found = stress_latest(research)
    if found:                                   # 2026-10-24: the bear-market stress test, from 2007
        rows = [row for row in found.get("rows") or [] if not row.get("error")]
        scenarios = found.get("scenarios") or []
        steady = max(rows, key=lambda row: (row.get("held_count") or 0, row["final"]), default=None)
        stress = {"tested": len(rows), "scenarios": len(scenarios),
                  "big": sum(1 for item in scenarios if item.get("size") == "大空頭"),
                  "held_2008": sum(1 for row in rows if row.get("held_2008")),
                  "held_all": sum(1 for row in rows if row.get("held_all")),
                  "beat": sum(1 for row in rows if row["final"] > row["base_final"]),
                  "steady": {"trial": steady["trial"], "held": steady.get("held_count"), "of": steady.get("scenario_count")}
                  if steady else None}
        checked = stress_latest(research, "protect")             # 2026-10-24: holding fewer stocks in a bear
        if checked and checked.get("verdicts"):
            stress["protect"] = [{"label": SHORT.get(key, key), "verdict": found.get("verdict", "—")}
                                 for key, found in checked["verdicts"].items()]
        graded = stress_latest(research, "guards")               # 2026-10-25: the same ways graded from 2015-06
        if graded:
            stress["guards"] = graded.get("ways") or []
    steady = calmest(research)
    from quant_platform.research.checks import correctness, funnel

    from quant_platform.research import distribution

    # 2026-10-26 (使用者：x 軸是最後的錢、y 軸是數量，投入的金額在中間；短歷史賽道加重): 2020-10 first, then 2015-06
    spread = distribution.view(research, pool["daily"])
    drawings = distribution.charts(spread)
    spread = {key: {**{name: track[name] for name in ("label", "invested", "bench", "counts", "median", "best", "worst")},
                    "svg": drawings.get(key, "")} for key, track in spread.items()}
    searched = distribution.search_view(research)
    if searched:
        drawn = distribution.search_charts(searched)
        searched = {key: {**{name: value for name, value in track.items() if name != "bins"}, "svg": drawn[key]}
                    for key, track in searched.items()}
    return {"checks": correctness(research), "funnel": funnel(research, pool["daily"]), "spread": spread,
            "search_spread": searched,
            "benchmark": benchmark, "recent_benchmark": recent_benchmark, "overall": overall, "ordinary": ordinary,
            "stress": stress, "steady": steady,
            "charts": charts(research, start, overall, ordinary, steady, benchmark, periods),
            "recent": recent_all, "forward": forward, "scale": search_scale(research),
            "t0": toward_t0(tracker.chained(date.today()), pool["daily"], summary, count=0),
            "families": families, "eras": periods, "passing": sum(row["tier"] in PASSING_TIERS for row in main),
            "rules": len(main)}
