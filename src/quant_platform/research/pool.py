"""The candidate pool: every rule ever tried, where it stands and why (研究選手池, 2026-10-03).

Built from the append-only records — the trial registry (ETF rules and stock rules), the
promotion ledger, the AI researcher's journal and the latest statistics — so the research page,
its JSON twin (for an assistant that answers questions from the website) and the weekly report
all describe the same state. Nothing here is written; the sources are.
"""

from __future__ import annotations

import json
from pathlib import Path

from quant_platform.research.promotion import (
    STAGE_LABELS,
    TRACK_LABELS,
    PromotionLedger,
    drawdown_gate,
    window_gate,
)
from quant_platform.research.categories import FAMILY_ORDER, classify
from quant_platform.research.categories import summary as category_summary
from quant_platform.research.registry import TrialRegistry, current_basis, distinct_rules
from quant_platform.research.reports import latest_stats

BASES = {"seed": "SeedPlan", "lump_sum": "LumpSumPlan", "dca": "ContributionPlan"}
BASIS_LABELS = {
    "seed": "啟動資金 30 萬＋每月 5 日投入 1 萬，整個帳戶都能買賣、獲利再投入；對同樣的錢全部買 0050",
    "lump_sum": "一次投入 30 萬、之後不再投入；對同樣 30 萬放 0050",
    "dca": "每月 5 日投入 1 萬（沒有啟動資金）；對 0050 定期定額",
}
STATUS_ORDER = (
    "approved", "forward", "holdout_passed", "validation_passed", "holdout_failed", "validation_failed",
    "window_ok", "eliminated",
)
STATUS_LABELS = {
    "approved": "已核准", "forward": "前向模擬中", "holdout_passed": "最終驗證期通過", "validation_passed": "驗證期通過",
    "holdout_failed": "最終驗證期未通過", "validation_failed": "驗證期未通過",
    "window_ok": "開發期通過視窗、待驗證", "eliminated": "開發期淘汰",
}


def _window(metrics: dict, key: str) -> dict:
    return (metrics.get("windows") or {}).get(key) or {}


def _gate(metrics: dict) -> list[str]:
    return window_gate(metrics) + drawdown_gate(metrics)


def _activity(metrics: dict) -> dict[str, object]:
    """Fees and tax against the money put in (R3). Runs before 2026-10-03 lack ``contributed``; with
    the standard plan it is NT$10,000 for every month from the first contribution to the end."""
    contributed = metrics.get("contributed")
    plan = metrics.get("plan") or {}
    if contributed is None and metrics.get("start") and metrics.get("end") and plan.get("kind", "ContributionPlan") == "ContributionPlan":
        start, end = str(metrics["start"]), str(metrics["end"])
        months = (int(end[:4]) * 12 + int(end[5:7])) - (int(start[:4]) * 12 + int(start[5:7])) + 1
        contributed = months * float(plan.get("monthly_amount") or 10_000)
    costs = metrics.get("costs")
    share = metrics.get("cost_share")
    if share is None and costs is not None and contributed:
        share = round(costs / contributed, 6)
    return {"cost_share": share, "turnover": metrics.get("turnover"), "orders_per_month": metrics.get("orders_per_month")}


def _sources(research_dir: Path) -> dict[str, str]:
    """spec_hash → where the rule came from (批次名稱 or AI 研究員)."""
    from quant_platform.research.batches import BATCHES
    from quant_platform.research.daily import BATCHES as DAILY_BATCHES
    from quant_platform.research.stock_rules import BATCHES as STOCK_BATCHES

    origin: dict[str, str] = {}
    for name, batch in DAILY_BATCHES.items():
        for rule in batch():
            origin.setdefault(rule.rule_hash, f"每天決策批次 {name}")
    for name, batch in STOCK_BATCHES.items():
        for rule in batch():
            origin.setdefault(rule.rule_hash, f"個股批次 {name}")
    for name, batch in BATCHES.items():
        for spec in batch():
            origin.setdefault(spec.spec_hash, f"規則批次 {name}")
    journal = research_dir / "journal.jsonl"
    if journal.is_file():
        for line in journal.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            for item in entry.get("accepted") or []:
                if item.get("spec_hash"):
                    origin.setdefault(item["spec_hash"], f"AI 研究員 {entry.get('round_id', '')}")
    return origin


def _best_records(records: list, period: str, plan_kind: str = "ContributionPlan") -> dict[str, object]:
    """One record per rule for the period and cash flow (monthly or lump sum): the newest that has
    rolling windows, else the newest. Lump-sum runs before engine 1.2.0 had monthly windows: skipped."""
    chosen: dict[str, object] = {}
    for record in records:
        if record.kind != "candidate" or record.period != period:
            continue
        if (record.metrics.get("plan") or {}).get("kind", "ContributionPlan") != plan_kind:
            continue
        if plan_kind == "LumpSumPlan" and str(record.metrics.get("engine") or "") < "stocks-1.2.0":
            continue
        current = chosen.get(record.spec_hash)
        has_windows = bool(_window(record.metrics, "3y").get("count"))
        if current is None or has_windows >= bool(_window(current.metrics, "3y").get("count")):
            chosen[record.spec_hash] = record
    return chosen


def rule_rows(research_dir: str | Path, basis: str = "seed") -> list[dict[str, object]]:
    base = Path(research_dir)
    kind = BASES[basis]
    registry = TrialRegistry(base / "trials.jsonl")
    records = registry.records()
    ledger = PromotionLedger(base / "promotions.jsonl")
    origin = _sources(base)
    dsr = {}
    for family in ("development", "stocks-development"):
        stats = latest_stats(base / "stats", family) or {}
        dsr.update({item["trial_id"]: item["dsr"]["deflated_sharpe"] for item in stats.get("candidates") or []})
    from quant_platform.research.stock_forward import StockForwardTracker

    forward_since = {item["rule_hash"]: item["since"] for item in StockForwardTracker(base).tracked()}
    etf_current, _older = current_basis(records, "development")
    etf_basis = {record.spec_hash for record in etf_current}
    development = _best_records(records, "development", kind)
    validation = _best_records(records, "validation", kind)
    holdout = _best_records(records, "holdout", kind)
    rows = []
    for spec_hash, dev in development.items():
        stock = dev.data_fingerprint.startswith("stocks:")
        if not stock and spec_hash not in etf_basis:
            continue  # ETF rules whose result is from a superseded data basis and never re-run
        dev_gate = _gate(dev.metrics)
        val = validation.get(spec_hash)
        val_gate = _gate(val.metrics) if val else None
        hold = holdout.get(spec_hash)
        hold_gate = _gate(hold.metrics) if hold else None
        stage, outcome = ledger.state(spec_hash)
        if stage == "approved":
            status = "approved"
        elif stage == "forward":
            status = "forward"
        elif dev_gate:
            status = "eliminated"          # a later-period run of an eliminated rule stays an illustration
        elif hold is not None:
            status = "holdout_passed" if not hold_gate else "holdout_failed"
        elif val is not None:
            status = "validation_passed" if not val_gate else "validation_failed"
        else:
            status = "window_ok"
        three, five = _window(dev.metrics, "3y"), _window(dev.metrics, "5y")
        rows.append({
            "spec_hash": spec_hash, "name": dev.spec_name, "family": "個股規則" if stock else "ETF 規則",
            "source": origin.get(spec_hash, "其他"), "status": status, "status_label": STATUS_LABELS[status],
            "track": TRACK_LABELS.get(ledger.track(spec_hash), "一般") if stage else None,
            "stage_label": STAGE_LABELS.get(stage, stage) if stage else None, "outcome": outcome,
            "forward_since": forward_since.get(spec_hash),
            "development": {
                "trial_id": dev.trial_id, "xirr": dev.metrics.get("xirr"), "benchmark_xirr": dev.metrics.get("benchmark_xirr"),
                "final_value": dev.metrics.get("final_value"), "benchmark_final_value": dev.metrics.get("benchmark_final_value"),
                "max_drawdown": dev.metrics.get("max_drawdown"),
                "benchmark_max_drawdown": dev.metrics.get("benchmark_max_drawdown"),
                "excess": dev.metrics.get("full_period_excess"),
                "win_3y": three.get("win_ratio"), "median_3y": three.get("median_excess"), "worst_3y": three.get("worst_excess"),
                "win_5y": five.get("win_ratio"), "median_5y": five.get("median_excess"),
                "costs": dev.metrics.get("costs"), "trades": dev.metrics.get("trades"),
                **_activity(dev.metrics),
                "dsr": dsr.get(dev.trial_id), "reasons": dev_gate, "report": dev.report_file,
            },
            "validation": None if val is None else {
                "trial_id": val.trial_id, "xirr": val.metrics.get("xirr"), "benchmark_xirr": val.metrics.get("benchmark_xirr"),
                "final_value": val.metrics.get("final_value"), "benchmark_final_value": val.metrics.get("benchmark_final_value"),
                "max_drawdown": val.metrics.get("max_drawdown"), "excess": val.metrics.get("full_period_excess"),
                "win_3y": _window(val.metrics, "3y").get("win_ratio"),
                "median_3y": _window(val.metrics, "3y").get("median_excess"),
                "worst_3y": _window(val.metrics, "3y").get("worst_excess"),
                **_activity(val.metrics),
                "reasons": val_gate, "report": val.report_file,
            },
            "holdout": None if hold is None else {
                "trial_id": hold.trial_id, "xirr": hold.metrics.get("xirr"), "excess": hold.metrics.get("full_period_excess"),
                "benchmark_xirr": hold.metrics.get("benchmark_xirr"), "final_value": hold.metrics.get("final_value"),
                "benchmark_final_value": hold.metrics.get("benchmark_final_value"),
                "win_3y": _window(hold.metrics, "3y").get("win_ratio"), "reasons": hold_gate, "report": hold.report_file,
            },
        })
    rows.sort(key=lambda row: (STATUS_ORDER.index(row["status"]), -(row["development"]["median_3y"] or -9)))
    return rows


PERIOD_LABELS = {"recent": "近期 2015-06 起・每天決策", "development": "開發期", "validation": "驗證期",
                 "holdout": "最終驗證期", "full": "全期間"}
PERIOD_ORDER = ("recent", "development", "validation", "holdout", "full")


_SPECS: dict[str, dict] = {}


def _spec_of(base: Path, report_file: str | None) -> dict:
    """A trial's rule spec from its report (reports never change once written, so kept after the first read)."""
    if not report_file:
        return {}
    if report_file not in _SPECS:
        try:
            _SPECS[report_file] = json.loads((base / "reports" / report_file).read_text(encoding="utf-8")).get("spec") or {}
        except (OSError, ValueError):
            return {}
    return _SPECS[report_file]


def daily_rows(research_dir: str | Path) -> list[dict[str, object]]:
    """The new design (S9-W02): every daily-decision rule's latest run on 2015-06..2026-09 with the
    owner's account, passed rules first."""
    from quant_platform.research.daily import TIER_LABELS, TIERS, tier
    from quant_platform.research.daily import gate as daily_gate
    from quant_platform.research.stock_forward import StockForwardTracker

    base = Path(research_dir)
    latest: dict[str, object] = {}
    for record in TrialRegistry(base / "trials.jsonl").records():
        if record.kind == "candidate" and record.period == "recent":
            latest[record.spec_hash] = record
    origin = _sources(base) if latest else {}
    tracker = StockForwardTracker(base)
    since = {item["rule_hash"]: item["since"] for item in tracker.tracked()}
    forward = {row["rule_hash"]: row for row in tracker.summary()} if latest else {}
    rows = []
    for spec_hash, record in latest.items():
        metrics = record.metrics
        reasons = daily_gate(metrics)
        one, three = _window(metrics, "1y"), _window(metrics, "3y")
        grade, why = tier(metrics, forward.get(spec_hash))
        rows.append({
            "tier": grade, "tier_label": TIER_LABELS[grade], "tier_reason": why,
            "spec_hash": spec_hash, "name": record.spec_name, "source": origin.get(spec_hash, "其他"),
            "trial_id": record.trial_id, "passed": not reasons, "reasons": reasons,
            "final_value": metrics.get("final_value"), "benchmark_final_value": metrics.get("benchmark_final_value"),
            "excess": metrics.get("full_period_excess"), "since_2020_excess": metrics.get("since_2020_excess"),
            "since_2020_final_value": metrics.get("since_2020_final_value"),
            "since_2020_benchmark_final_value": metrics.get("since_2020_benchmark_final_value"),
            "win_1y": one.get("win_ratio"), "win_3y": three.get("win_ratio"), "median_3y": three.get("median_excess"),
            "max_drawdown": metrics.get("max_drawdown"), "benchmark_max_drawdown": metrics.get("benchmark_max_drawdown"),
            "xirr": metrics.get("xirr"), "benchmark_xirr": metrics.get("benchmark_xirr"),
            "cost_share": metrics.get("cost_share"), "orders_per_month": metrics.get("orders_per_month"),
            "forward_since": since.get(spec_hash), "report_file": record.report_file,
            "category": classify(_spec_of(base, record.report_file), origin.get(spec_hash, "")),
        })
    rows.sort(key=lambda row: (TIERS.index(row["tier"]), -(row["excess"] if row["excess"] is not None else -9)))
    return rows


def ai_researcher_view(research_dir: str | Path, daily_rules: list[dict[str, object]]) -> dict[str, object]:
    """The AI researcher as a strategy (2026-10-09): its rules in the pool, the trials it used, what its
    forward account holds and when it next decides."""
    from quant_platform.research.agent.daily_researcher import proposals
    from quant_platform.research.stock_forward import META_AI, StockForwardTracker

    base = Path(research_dir)
    mine = {item["spec_hash"] for item in proposals(base)}
    rules = [row for row in daily_rules if row["spec_hash"] in mine]
    tracker = StockForwardTracker(base)
    decisions = tracker.meta_decisions()
    forward = next((row for row in tracker.summary() if row["rule_hash"] == META_AI["rule_hash"]), None)
    return {"rules": rules, "trials": len(mine), "decision": decisions[-1] if decisions else None,
            "forward": forward, "meta_hash": META_AI["rule_hash"]}


def plan_label(plan: dict) -> str:
    kind = plan.get("kind", "ContributionPlan")
    if kind == "SeedPlan":
        return f"啟動資金 {plan.get('initial', 0) / 10_000:,.0f} 萬＋每月 {plan.get('monthly_amount', 0):,.0f} 元"
    if kind == "LumpSumPlan":
        return f"一次投入 {plan.get('monthly_amount', 0) / 10_000:,.0f} 萬"
    return f"每月 {plan.get('monthly_amount', 10_000) or 10_000:,.0f} 元"


def basis_counts(research_dir: str | Path) -> dict[str, int]:
    """Rules with a development run per basis (the pool shows a basis only when it has results)."""
    records = TrialRegistry(Path(research_dir) / "trials.jsonl").records()
    output = {}
    for basis, kind in BASES.items():
        output[basis] = len(_best_records(records, "development", kind))
    return output


def describe_rule(spec: dict) -> list[str]:
    """A stock rule in plain words (ETF specs: their name and assets)."""
    from quant_platform.research.stock_rules import FACTORS

    if spec.get("kind") == "blend":                     # 2026-10-09: one account across strategy families
        lines = [f"帳戶分成 {len(spec['sleeves']) + (1 if spec.get('core') else 0)} 份，每份各自照自己的規則操作，"
                 "啟動資金與每月投入都照比例分；各份之間不再平衡"]
        lines += [f"{sleeve['share']:.0%}：{sleeve['rule']['name']}" for sleeve in spec["sleeves"]]
        if spec.get("core"):
            lines.append(f"{spec['core']:.0%}：0050（錢進來就買，不賣）")
        return lines
    if "factor" not in spec:
        return [str(spec.get("description") or spec.get("name") or "")]
    factors = [FACTORS.get(spec["factor"], spec["factor"])] + [
        f"{FACTORS.get(name, name)}（權重 {weight:g}）" for name, weight in (spec.get("extra") or {}).items()]
    lines = [
        "依「" + "＋".join(factors) + "」把合格的上市股票排名" + ("（多個因子時用百分位相加）" if spec.get("extra") else ""),
        f"持有前 {spec.get('top', 20)} 名，每檔金額相同；每{'月' if spec.get('rebalance', 'monthly') == 'monthly' else '季'} 5 日檢查換股",
    ]
    if (spec.get("buffer") or 1) > 1:
        lines.append(f"已持有的股票跌出前 {spec['top'] * spec['buffer']} 名才賣")
    if spec.get("min_hold"):
        lines.append(f"新買的股票至少放 {spec['min_hold']} 個月（不再合格仍會賣）")
    if spec.get("band"):
        lines.append(f"某檔比目標金額少 {spec['band']:.0%} 以上才加碼")
    if spec.get("min_trade"):
        lines.append(f"每筆至少 {spec['min_trade']:,.0f} 元")
    lines.append(f"合格：收盤 ≥ {spec.get('min_price', 10):g} 元、近 20 日日均成交值 ≥ {spec.get('min_turnover', 2e7) / 1e4:,.0f} 萬、"
                 f"上市滿 {spec.get('min_history', 252)} 個交易日")
    return lines


def _svg(curve: dict[str, list[float]], width: int = 640, height: int = 220) -> dict[str, object] | None:
    if not curve or len(curve) < 2:
        return None
    months = list(curve)
    peak = max(max(values) for values in curve.values()) or 1.0

    def points(column: int) -> str:
        return " ".join(f"{index * width / (len(months) - 1):.1f},{height - curve[month][column] / peak * (height - 10):.1f}"
                        for index, month in enumerate(months))

    return {"rule": points(0), "benchmark": points(1), "put_in": points(2), "width": width, "height": height,
            "first": months[0], "last": months[-1], "peak": peak}


FLOW_WEIGHT = 0.8   # the month's contribution arrives on the 5th: invested for most of the month


def yearly_returns(curve: dict[str, list[float]]) -> dict[str, dict[str, float]]:
    """Calendar-year returns of the rule and of 0050 from the report's month-end curve [rule, 0050,
    contributed so far] (使用者 2026-10-09：每一年的績效). Each month's return nets out that month's
    contribution (Modified Dietz, the contribution weighted 0.8); the first month treats the money put in
    as there from the start; the months compound within the year. ``months`` counts the year's months."""
    output: dict[str, dict[str, float]] = {}
    previous: list[float] | None = None
    for month in sorted(curve):
        values = curve[month]
        flow = values[2] - (previous[2] if previous else 0.0)
        item = output.setdefault(month[:4], {"strategy": 1.0, "benchmark": 1.0, "months": 0})
        for column, key in ((0, "strategy"), (1, "benchmark")):
            start = previous[column] if previous else 0.0
            base = start + FLOW_WEIGHT * flow if previous else flow
            change = (values[column] - start - flow) / base if base > 0 else 0.0
            item[key] *= 1 + change
        item["months"] += 1
        previous = values
    return {year: {"strategy": round(item["strategy"] - 1, 4), "benchmark": round(item["benchmark"] - 1, 4),
                   "months": item["months"]} for year, item in output.items()}


def yearly_matrix(research_dir: str | Path, rows: list[dict[str, object]]) -> dict[str, object]:
    """Yearly returns of the given new-design rules (the overview's T0／T1 list) and of 0050."""
    base = Path(research_dir)
    table, years, benchmark = [], set(), {}
    for row in rows:
        try:
            report = json.loads((base / "reports" / str(row.get("report_file"))).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        returns = yearly_returns(report.get("curve") or {})
        if not returns:
            continue
        years.update(returns)
        if not benchmark:
            benchmark = {year: item["benchmark"] for year, item in returns.items()}
        table.append({"name": row["name"], "spec_hash": row["spec_hash"], "tier": row["tier"], "years": returns})
    ordered = sorted(years)
    months = {year: max((item["years"].get(year, {}).get("months", 0) for item in table), default=0) for year in ordered}
    return {"years": ordered, "months": months, "rows": table, "benchmark": benchmark}


def rule_detail(research_dir: str | Path, spec_hash: str) -> dict[str, object] | None:
    """Every backtest run of one rule (all periods and cash flows), with its report: what the website's
    rule page shows (使用者 2026-10-04：模擬回測的記錄要看得到)."""
    from quant_platform.research.stock_forward import StockForwardTracker, final_validation

    base = Path(research_dir)
    records = [record for record in TrialRegistry(base / "trials.jsonl").records()
               if record.spec_hash == spec_hash and record.kind == "candidate"]
    if not records:
        return None
    spec: dict = {}
    runs = []
    for record in records:
        report: dict = {}
        if record.report_file:
            try:
                report = json.loads((base / "reports" / record.report_file).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                report = {}
        spec = spec or report.get("spec") or {}
        metrics = record.metrics
        strategy, benchmark = report.get("strategy") or {}, report.get("benchmark") or {}
        yearly: dict[str, float] = {}
        for month, value in (report.get("monthly_active_returns") or {}).items():
            yearly[month[:4]] = yearly.get(month[:4], 0.0) + value
        has_windows = bool(_window(metrics, "3y").get("count"))
        if record.period == "recent":
            from quant_platform.research.daily import gate as daily_gate

            reasons = daily_gate(metrics)
        else:
            reasons = _gate(metrics) if has_windows else None
        runs.append({
            "trial_id": record.trial_id, "created_at": str(record.created_at)[:16].replace("T", " "),
            "period": record.period, "period_label": PERIOD_LABELS.get(record.period, record.period),
            "plan": plan_label(metrics.get("plan") or {}), "engine": metrics.get("engine"),
            "start": metrics.get("start") or report.get("start"), "end": metrics.get("end") or report.get("end"),
            "final_value": metrics.get("final_value") or strategy.get("final_value"),
            "benchmark_final_value": metrics.get("benchmark_final_value") or benchmark.get("final_value"),
            "contributed": metrics.get("contributed") or strategy.get("contributed"),
            "xirr": metrics.get("xirr"), "benchmark_xirr": metrics.get("benchmark_xirr"),
            "max_drawdown": metrics.get("max_drawdown"), "benchmark_max_drawdown": metrics.get("benchmark_max_drawdown"),
            "excess": metrics.get("full_period_excess"), "three": _window(metrics, "3y"), "five": _window(metrics, "5y"),
            "trades": metrics.get("trades"), "costs": metrics.get("costs"), **_activity(metrics),
            "reasons": reasons, "screen": not has_windows and record.period != "recent",
            "one": _window(metrics, "1y"), "since_2020_excess": metrics.get("since_2020_excess"),
            "yearly": {year: round(value, 4) for year, value in sorted(yearly.items())},
            "returns": yearly_returns(report.get("curve") or {}),
            "chart": _svg(report.get("curve") or {}), "report_file": record.report_file,
        })
    runs.sort(key=lambda run: (PERIOD_ORDER.index(run["period"]) if run["period"] in PERIOD_ORDER else 9, run["created_at"]))
    forward = next((row for row in StockForwardTracker(base).summary() if row["rule_hash"] == spec_hash), None)
    return {"spec_hash": spec_hash, "name": records[-1].spec_name, "spec": spec, "description": describe_rule(spec),
            "family": "個股規則" if records[-1].data_fingerprint.startswith("stocks:") else "ETF 規則",
            "source": _sources(base).get(spec_hash, "其他"), "runs": runs, "forward": forward,
            "final": final_validation(base).get(spec_hash)}


def ai_rounds(research_dir: str | Path) -> list[dict[str, object]]:
    journal = Path(research_dir) / "journal.jsonl"
    if not journal.is_file():
        return []
    rounds = []
    for line in journal.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        rounds.append({
            "round_id": entry.get("round_id"), "at": str(entry.get("at") or entry.get("started_at") or "")[:16],
            "status": entry.get("status"), "hypothesis": entry.get("hypothesis"), "analysis": entry.get("analysis"),
            "accepted": [item.get("name") for item in entry.get("accepted") or []],
            "rejected": [f"{item.get('name')}：{item.get('reason')}" for item in entry.get("rejected") or []],
            "cost_usd": (entry.get("budget") or {}).get("spent_usd"),
        })
    return list(reversed(rounds))


def pool_view(research_dir: str | Path, top: int = 20, basis: str = "seed") -> dict[str, object]:
    """What the pool page shows: counts, the lists an owner asks about, and every rule, on one basis
    (lump sum, the owner's strategy account since 2026-10-04, or the monthly plan)."""
    rows = rule_rows(research_dir, basis)
    daily_rules = daily_rows(research_dir)
    counts = {status: sum(1 for row in rows if row["status"] == status) for status in STATUS_ORDER}
    families = {family: sum(1 for row in rows if row["family"] == family) for family in ("ETF 規則", "個股規則")}
    ranked = [row for row in rows if row["development"]["win_3y"] is not None]
    high_win = sorted(ranked, key=lambda row: (-row["development"]["win_3y"], -(row["development"]["median_3y"] or 0)))[:top]
    both = [row for row in rows if row["validation"] and not row["development"]["reasons"] and not row["validation"]["reasons"]]
    from quant_platform.research.stock_forward import StockForwardTracker

    stats = latest_stats(Path(research_dir) / "stats", "development") or {}
    stock_stats = latest_stats(Path(research_dir) / "stats", "stocks-development") or {}
    attempts = distinct_rules([record for record in TrialRegistry(Path(research_dir) / "trials.jsonl").records()
                               if record.kind == "candidate" and record.period == "development"])
    best_dsr = max((item["dsr"]["deflated_sharpe"] or 0, item["name"])
                   for item in (stats.get("candidates") or []) + (stock_stats.get("candidates") or [])
                   or [{"dsr": {"deflated_sharpe": 0}, "name": ""}])
    return {
        "rules": rows, "counts": counts, "families": families, "total": len(rows),
        "attempts": attempts, "best_dsr": best_dsr, "pbo": (stats.get("pbo") or {}).get("pbo"),
        "stock_pbo": (stock_stats.get("pbo") or {}).get("pbo"),
        "high_win": high_win, "both_periods": both, "ai_rounds": ai_rounds(research_dir),
        "forward_stocks": StockForwardTracker(research_dir).summary(),
        "daily": daily_rules, "daily_passed": sum(1 for row in daily_rules if row["passed"]),
        "categories": category_summary(daily_rules),
        "ai_researcher": ai_researcher_view(research_dir, daily_rules),
        "category_order": [family for family in FAMILY_ORDER if any(row["category"]["family"] == family for row in daily_rules)],
        "traits": sorted({trait for row in daily_rules for trait in row["category"]["traits"]}),
        "daily_tiers": {grade: sum(1 for row in daily_rules if row["tier"] == grade)
                        for grade in ("T0", "T0 候選", "T1", "T2", "T3")},
        "status_labels": STATUS_LABELS, "basis": basis, "basis_label": BASIS_LABELS[basis], "bases": BASIS_LABELS,
    }
