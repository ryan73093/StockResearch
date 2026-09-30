"""Research periods and the holdout lock (S4-W02).

Candidate strategies are developed on the development period, confirmed on
the validation period and evaluated on the holdout exactly once, and only
after a validation trial exists (REQUIREMENTS §7). Built-in baselines are
benchmarks, not selected by research, so they may run on any period.

Known limitation: data from 2025 onwards was already seen by the pre-2026-09
research modules; the forward simulation (S5) is the clean test.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from quant_platform.research.cashflow import ContributionPlan
from quant_platform.research.compare import compare_to_benchmark
from quant_platform.research.costs import CostModel
from quant_platform.research.market import MarketData
from quant_platform.research.registry import TrialRecord, TrialRegistry
from quant_platform.research.spec import BASELINES, StrategySpec

PERIODS: dict[str, tuple[date | None, date | None]] = {
    "development": (date(2004, 2, 11), date(2016, 12, 31)),
    "validation": (date(2017, 1, 1), date(2021, 12, 31)),
    "holdout": (date(2022, 1, 1), date(2026, 9, 30)),
    "full": (None, None),
}
CANDIDATE_PERIODS = ("development", "validation", "holdout")


class ResearchGateError(PermissionError):
    """The research rules do not allow this evaluation."""


@dataclass(frozen=True)
class TrialOutcome:
    record: TrialRecord
    report: dict[str, object]
    report_path: Path
    reused: bool


def check_gate(registry: TrialRegistry, kind: str, spec: StrategySpec, period: str) -> None:
    if period not in PERIODS:
        raise ResearchGateError(f"未知期間：{period}")
    if kind == "baseline":
        if spec.spec_hash not in {item.spec_hash for item in BASELINES.values()}:
            raise ResearchGateError("baseline 只能用內建基準策略")
        return
    if kind != "candidate":
        raise ResearchGateError(f"未知試驗類型：{kind}")
    if period not in CANDIDATE_PERIODS:
        raise ResearchGateError("候選策略不能用全期間評估（會看到保留期）")
    if period == "holdout":
        records = [record for record in registry.records() if record.spec_hash == spec.spec_hash]
        if not any(record.kind == "candidate" and record.period == "validation" for record in records):
            raise ResearchGateError("保留期前必須先有驗證期試驗")
        if any(record.kind == "candidate" and record.period == "holdout" for record in records):
            raise ResearchGateError("此設定檔已評估過保留期，每個候選只能評估一次")


def trial_hash(report: dict[str, object], benchmark: StrategySpec, windows: tuple[int, ...]) -> str:
    payload = {
        "strategy_input": report["strategy"]["input_hash"],
        "benchmark": benchmark.spec_hash,
        "windows": list(windows),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def run_trial(
    *,
    kind: str,
    spec: StrategySpec,
    period: str,
    market: MarketData,
    plan: ContributionPlan,
    registry: TrialRegistry,
    reports_dir: str | Path,
    costs: CostModel | None = None,
    benchmark: StrategySpec | None = None,
    window_months: tuple[int, ...] = (36, 60),
    dividend_lag_days: int = 25,
    execution_lag: int = 0,
    generated_at: str = "",
) -> TrialOutcome:
    check_gate(registry, kind, spec, period)
    benchmark = benchmark or BASELINES["benchmark_dca"]
    start, end = PERIODS[period]
    report = compare_to_benchmark(
        spec, market, plan, costs or CostModel(), benchmark, start, end, window_months,
        dividend_lag_days, execution_lag,
    )
    report["period"] = period
    report["kind"] = kind
    report["generated_at"] = generated_at
    key = trial_hash(report, benchmark, window_months)
    existing = registry.find(kind, period, key)
    folder = Path(reports_dir)
    if existing is not None:
        return TrialOutcome(existing, report, folder / existing.report_file, reused=True)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{period}-{spec.spec_hash[:8]}-{report['report_hash'][:12]}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    windows = report["windows"]
    record = registry.register(
        kind=kind,
        period=period,
        spec_hash=spec.spec_hash,
        spec_name=spec.name,
        input_hash=key,
        data_fingerprint=report["data_fingerprint"],
        metrics={
            "start": report["strategy"]["start"],
            "end": report["strategy"]["end"],
            "full_period_excess": report["full_period_excess"],
            "xirr": report["strategy"]["xirr"],
            "benchmark_xirr": report["benchmark"]["xirr"],
            "max_drawdown": report["strategy"]["max_drawdown"],
            "trades": report["strategy"]["trades"],
            "costs": report["strategy"]["fees"] + report["strategy"]["taxes"],
            "windows": {
                name: {field: item.get(field) for field in ("count", "win_ratio", "median_excess")}
                | {"worst_excess": (item.get("worst") or {}).get("excess")}
                for name, item in windows.items()
            },
        },
        report_file=path.name,
    )
    return TrialOutcome(record, report, path, reused=False)
