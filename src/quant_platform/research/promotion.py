"""Promotion of candidate strategies (S4-W06).

A candidate moves through fixed gates, each recorded with its evidence in an
append-only, hash-chained ledger (instance/research/promotions.jsonl):

1. development — on the current data basis: rolling 3- and 5-year windows win
   ≥ 60% with a positive median excess, the drawdown gate of its track,
   Deflated Sharpe ≥ 0.95 and the period's PBO ≤ 0.2;
2. validation (2017–2021) — the same window and drawdown gates, plus the
   robustness runs (costs doubled, one session late) still winning ≥ 50% of
   the 3-year windows;
3. holdout (2022–2026-09) — evaluated once (periods.check_gate), same gates;

Two tracks differ only in the drawdown gate (owner's decision 2026-10-02):
standard — no more than 5 points deeper than the DCA benchmark; aggressive —
for rules that fail that, no deeper than the acceptable drawdown of the
owner's current investment plan (closed while no plan exists). The track is
fixed at the development gate, written into the ledger evidence, and never
switched; approval re-checks the deepest recorded drawdown against the
plan's tolerance at that moment.
4. forward — tracked from the next session on (forward/tracked/); after at
   least 40 recorded sessions (≈ 8 weeks) it is ready for the user;
5. approved — only the user approves, on the research page; only approved
   specs can be chosen in the plan. A revocation is a new record.

Forward results are observed, never used to select (REQUIREMENTS §7).
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from quant_platform.research.batches import BATCHES
from quant_platform.research.costs import CostModel
from quant_platform.research.market import MarketData
from quant_platform.research.periods import ResearchGateError, period_basis, run_trial
from quant_platform.research.registry import (
    GENESIS, TrialRecord, TrialRegistry, current_basis, distinct_rules, one_per_rule,
)
from quant_platform.research.spec import BASELINES, StrategySpec

WIN_GATE = 0.60
ROBUST_WIN_GATE = 0.50
DRAWDOWN_SLACK = 0.05
DSR_GATE = 0.95
PBO_GATE = 0.20
FORWARD_SESSIONS = 40
STAGES = ("development", "validation", "holdout", "forward", "approved")
TRACK_LABELS = {"standard": "一般", "aggressive": "進攻型"}
STAGE_LABELS = {
    "development": "開發期", "validation": "驗證期", "holdout": "保留期", "forward": "前向模擬",
    "approved": "已核准", "revoked": "已撤銷",
}


def _digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


# --- gates -------------------------------------------------------------------

def window_gate(metrics: dict, min_win: float = WIN_GATE, windows: tuple[str, ...] = ("3y", "5y")) -> list[str]:
    """Reasons a trial fails the rolling-window gate; empty when it passes.
    Windows a period is too short for (count 0) are skipped, but 3y must exist."""
    reasons = []
    found = (metrics.get("windows") or {})
    if not (found.get("3y") or {}).get("count"):
        reasons.append("沒有 3 年滾動視窗")
    for key in windows:
        item = found.get(key) or {}
        if not item.get("count"):
            continue
        label = f"{key[:-1]} 年"
        if (item.get("win_ratio") or 0) < min_win:
            reasons.append(f"{label}勝率 {(item.get('win_ratio') or 0):.0%} < {min_win:.0%}")
        if min_win >= WIN_GATE and (item.get("median_excess") or 0) <= 0:
            reasons.append(f"{label}中位超額 {(item.get('median_excess') or 0):+.2%} ≤ 0")
    return reasons


def drawdown_gate(metrics: dict) -> list[str]:
    strategy, benchmark = metrics.get("max_drawdown"), metrics.get("benchmark_max_drawdown")
    if strategy is None or benchmark is None:
        return ["缺少最大回撤資料"]
    if strategy < benchmark - DRAWDOWN_SLACK:
        return [f"最大回撤 {strategy:.1%} 比定期定額 {benchmark:.1%} 深超過 5 個百分點"]
    return []


def tolerance_gate(metrics: dict, tolerance: float) -> list[str]:
    """Aggressive track: the period's drawdown must stay within the plan's acceptable drawdown."""
    strategy = metrics.get("max_drawdown")
    if strategy is None:
        return ["缺少最大回撤資料"]
    if strategy < -tolerance:
        return [f"最大回撤 {strategy:.1%} 超過計畫可承受回撤 {tolerance:.0%}"]
    return []


def drawdown_track(metrics: dict, tolerance: float | None) -> tuple[str | None, list[str]]:
    """Which track a development result enters: standard when it passes the benchmark-relative
    gate (the tolerance is never applied to those), aggressive when it fails that but stays within
    the plan's tolerance, else none with the reasons."""
    if metrics.get("max_drawdown") is None or metrics.get("benchmark_max_drawdown") is None:
        return None, ["缺少最大回撤資料"]
    standard = drawdown_gate(metrics)
    if not standard:
        return "standard", []
    if tolerance is None:
        return None, standard + ["尚未建立投資計畫，進攻型賽道未啟用"]
    within = tolerance_gate(metrics, tolerance)
    return (None, standard + within) if within else ("aggressive", [])


def statistics_gate(record: TrialRecord, stats: dict | None, rules: int | None = None) -> list[str]:
    if not stats or stats.get("basis") != record.data_fingerprint:
        return ["統計檢定尚未以目前資料版本完成"]
    if rules is not None and (stats.get("trials") or 0) < rules:
        return ["統計檢定的設定數落後登錄檔，需重算"]
    reasons = []
    item = next((entry for entry in stats.get("candidates") or [] if entry.get("trial_id") == record.trial_id), None)
    dsr = (item or {}).get("dsr", {}).get("deflated_sharpe")
    if dsr is None or dsr < DSR_GATE:
        reasons.append(f"DSR {dsr if dsr is not None else float('nan'):.2f} < {DSR_GATE}")
    pbo = (stats.get("pbo") or {}).get("pbo")
    if pbo is not None and pbo > PBO_GATE:
        reasons.append(f"PBO {pbo:.0%} > {PBO_GATE:.0%}")
    return reasons


# --- ledger ------------------------------------------------------------------

@dataclass(frozen=True)
class PromotionEvent:
    seq: int
    at: str
    spec_hash: str
    name: str
    stage: str
    outcome: str        # passed | failed | started | approved | revoked
    actor: str          # system | user
    evidence: dict
    spec: dict | None
    prev_hash: str
    record_hash: str


class PromotionLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def events(self) -> list[PromotionEvent]:
        if not self.path.is_file():
            return []
        return [
            PromotionEvent(**json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()
        ]

    def append(
        self, spec: StrategySpec, stage: str, outcome: str, evidence: dict, actor: str = "system",
        now: datetime | None = None,
    ) -> PromotionEvent:
        events = self.events()
        body = {
            "seq": len(events) + 1,
            "at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
            "spec_hash": spec.spec_hash,
            "name": spec.name,
            "stage": stage,
            "outcome": outcome,
            "actor": actor,
            "evidence": evidence,
            "spec": spec.model_dump(mode="json"),
            "prev_hash": events[-1].record_hash if events else GENESIS,
        }
        event = PromotionEvent(**body, record_hash=_digest(body))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({**body, "record_hash": event.record_hash}, ensure_ascii=False) + "\n")
        return event

    def verify(self) -> list[str]:
        problems, previous = [], GENESIS
        for index, event in enumerate(self.events(), 1):
            body = {name: getattr(event, name) for name in PromotionEvent.__dataclass_fields__ if name != "record_hash"}
            if event.seq != index or event.prev_hash != previous or _digest(body) != event.record_hash:
                problems.append(f"第 {index} 筆晉級紀錄被修改、刪除或重排")
            previous = event.record_hash
        return problems

    def history(self, spec_hash: str) -> list[PromotionEvent]:
        return [event for event in self.events() if event.spec_hash == spec_hash]

    def track(self, spec_hash: str) -> str:
        """The track fixed at the development gate; records written before tracks existed are standard."""
        for event in self.history(spec_hash):
            if event.evidence.get("track"):
                return str(event.evidence["track"])
        return "standard"

    def deepest_drawdown(self, spec_hash: str) -> float | None:
        """The deepest drawdown recorded over the development, validation and holdout gates."""
        values = [event.evidence["max_drawdown"] for event in self.history(spec_hash)
                  if event.evidence.get("max_drawdown") is not None]
        return min(values) if values else None

    def state(self, spec_hash: str) -> tuple[str | None, str | None]:
        """(stage, outcome) of the latest event for the spec."""
        history = self.history(spec_hash)
        return (history[-1].stage, history[-1].outcome) if history else (None, None)

    def approved_specs(self) -> list[StrategySpec]:
        latest: dict[str, PromotionEvent] = {}
        for event in self.events():
            if event.stage in {"approved", "revoked"}:
                latest[event.spec_hash] = event
        return [
            StrategySpec.model_validate(event.spec)
            for event in latest.values() if event.stage == "approved" and event.spec
        ]


def strategy_catalog(research_dir: str | Path) -> dict[str, StrategySpec]:
    """Built-in baselines plus approved candidates (key ``approved:<hash>``)."""
    catalog = dict(BASELINES)
    for spec in PromotionLedger(Path(research_dir) / "promotions.jsonl").approved_specs():
        catalog[f"approved:{spec.spec_hash[:16]}"] = spec
    return catalog


def required_tolerance(research_dir: str | Path) -> dict[str, float]:
    """Plan strategy key → the acceptable drawdown an approved aggressive strategy needs (its
    deepest recorded drawdown, positive). A plan that adopts it may not set a lower tolerance."""
    ledger = PromotionLedger(Path(research_dir) / "promotions.jsonl")
    needs = {}
    for spec in ledger.approved_specs():
        deepest = ledger.deepest_drawdown(spec.spec_hash)
        if ledger.track(spec.spec_hash) == "aggressive" and deepest is not None:
            needs[f"approved:{spec.spec_hash[:16]}"] = -deepest
    return needs


def promotion_limits(plan) -> dict[str, object]:
    """Constructor arguments from the owner's current plan (None: the aggressive track is closed)."""
    if plan is None:
        return {"drawdown_tolerance": None, "plan_version": None}
    return {"drawdown_tolerance": plan.max_drawdown_tolerance, "plan_version": plan.version}


# --- the pipeline ------------------------------------------------------------

class PromotionPipeline:
    def __init__(self, research_dir: str | Path, plan, costs: CostModel | None = None, clock=None,
                 drawdown_tolerance: float | None = None, plan_version: int | None = None) -> None:
        if drawdown_tolerance is not None and not 0.05 <= drawdown_tolerance <= 0.8:
            raise ValueError("可承受回撤是 0.05～0.8 的小數")
        self._dir = Path(research_dir)
        self._plan = plan  # the standard cash flow of every trial, not the owner's amounts
        self._tolerance = drawdown_tolerance
        self._plan_version = plan_version
        self._costs = costs or CostModel()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.registry = TrialRegistry(self._dir / "trials.jsonl")
        self.ledger = PromotionLedger(self._dir / "promotions.jsonl")

    def _stats(self) -> dict | None:
        from quant_platform.research.reports import latest_stats

        return latest_stats(self._dir / "stats", "development")

    def spec_for(self, record: TrialRecord) -> StrategySpec | None:
        path = self._dir / "reports" / record.report_file
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if report.get("spec"):
                return StrategySpec.model_validate(report["spec"])
        except (OSError, ValueError):
            pass
        for batch in BATCHES.values():
            for spec in batch():
                if spec.spec_hash == record.spec_hash:
                    return spec
        journal = self._dir / "journal.jsonl"
        if journal.is_file():
            for line in journal.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                for item in entry.get("accepted") or []:
                    if item.get("spec_hash") == record.spec_hash and item.get("spec"):
                        return StrategySpec.model_validate(item["spec"])
        return None

    @staticmethod
    def _metrics(record: TrialRecord, reports_dir: Path) -> dict:
        metrics = dict(record.metrics)
        if "benchmark_max_drawdown" not in metrics:  # trials registered before 2026-10-01 12:00
            try:
                report = json.loads((reports_dir / record.report_file).read_text(encoding="utf-8"))
                metrics["benchmark_max_drawdown"] = report["benchmark"]["max_drawdown"]
            except (OSError, ValueError, KeyError):
                pass
        return metrics

    def development_review(self) -> list[dict[str, object]]:
        """Every development candidate on the current basis with the gates it fails."""
        records, older = current_basis(self.registry.records(), "development")
        rules = distinct_rules(records + older)
        stats = self._stats()
        rows = []
        for record in one_per_rule(records):
            metrics = self._metrics(record, self._dir / "reports")
            windows = window_gate(metrics)
            track, drawdown = drawdown_track(metrics, self._tolerance)
            reasons = windows + drawdown + statistics_gate(record, stats, rules)
            rows.append({
                "record": record, "window_ok": not windows, "reasons": reasons, "eligible": not reasons,
                "track": track, "metrics": metrics,
                "deep": metrics.get("max_drawdown") is not None and bool(drawdown_gate(metrics)),
            })
        return rows

    def _track_evidence(self, track: str, metrics: dict) -> dict[str, object]:
        evidence = {"track": track, "max_drawdown": metrics.get("max_drawdown"),
                    "benchmark_max_drawdown": metrics.get("benchmark_max_drawdown")}
        if track == "aggressive":
            evidence.update(drawdown_tolerance=self._tolerance, plan_version=self._plan_version)
        return evidence

    def _drawdown(self, track: str, metrics: dict) -> list[str]:
        if track != "aggressive":
            return drawdown_gate(metrics)
        if self._tolerance is None:  # cannot happen after the development gate; never let it pass silently
            return ["尚未建立投資計畫，進攻型賽道未啟用"]
        return tolerance_gate(metrics, self._tolerance)

    def advance(self, market: MarketData) -> list[PromotionEvent]:
        """Move every candidate as far as its gates allow; returns the new events."""
        new: list[PromotionEvent] = []
        for row in self.development_review():
            record = row["record"]
            if not row["eligible"] or self.ledger.state(record.spec_hash)[0] is not None:
                continue
            spec = self.spec_for(record)
            if spec is None:
                continue
            new.append(self.ledger.append(spec, "development", "passed", {
                "trial_id": record.trial_id, "report": record.report_file,
                "basis": period_basis(market, "development")[:12],
                **self._track_evidence(row["track"], row["metrics"]),
            }, now=self._clock()))
            new += self._validate_and_holdout(spec, market, row["track"])
        new += self._check_forward()
        return new

    def _trial(self, spec, period, market, costs=None, lag=0):
        return run_trial(
            kind="candidate", spec=spec, period=period, market=market, plan=self._plan,
            registry=self.registry, reports_dir=self._dir / "reports", costs=costs or self._costs,
            execution_lag=lag, generated_at=f"{self._clock():%Y%m%d-%H%M%S}",
        )

    def _validate_and_holdout(self, spec: StrategySpec, market: MarketData,
                              track: str = "standard") -> list[PromotionEvent]:
        events = []
        try:
            main = self._trial(spec, "validation", market)
            doubled = self._trial(spec, "validation", market, costs=self._costs.scaled(2))
            late = self._trial(spec, "validation", market, lag=1)
        except (ResearchGateError, ValueError) as exc:
            return [self.ledger.append(spec, "validation", "failed", {"error": str(exc)}, now=self._clock())]
        metrics = self._metrics(main.record, self._dir / "reports")
        reasons = window_gate(metrics) + self._drawdown(track, metrics)
        for label, outcome in (("成本加倍", doubled), ("晚一天執行", late)):
            reasons += [f"{label}：{reason}" for reason in window_gate(outcome.record.metrics, ROBUST_WIN_GATE, ("3y",))]
        evidence = {
            "trial_id": main.record.trial_id, "report": main.record.report_file,
            "cost_doubled_trial": doubled.record.trial_id, "late_trial": late.record.trial_id, "reasons": reasons,
            **self._track_evidence(track, metrics),
        }
        events.append(self.ledger.append(spec, "validation", "failed" if reasons else "passed", evidence,
                                         now=self._clock()))
        if reasons:
            return events
        try:
            holdout = self._trial(spec, "holdout", market)
        except (ResearchGateError, ValueError) as exc:
            events.append(self.ledger.append(spec, "holdout", "failed", {"error": str(exc)}, now=self._clock()))
            return events
        metrics = self._metrics(holdout.record, self._dir / "reports")
        reasons = window_gate(metrics) + self._drawdown(track, metrics)
        events.append(self.ledger.append(spec, "holdout", "failed" if reasons else "passed", {
            "trial_id": holdout.record.trial_id, "report": holdout.record.report_file, "reasons": reasons,
            **self._track_evidence(track, metrics),
        }, now=self._clock()))
        if not reasons:
            tracked = self._dir / "forward" / "tracked"
            tracked.mkdir(parents=True, exist_ok=True)
            (tracked / f"{spec.spec_hash[:16]}.json").write_text(
                json.dumps(spec.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            events.append(self.ledger.append(spec, "forward", "started", {
                "from": f"{self._clock():%Y-%m-%d}", "sessions_needed": FORWARD_SESSIONS, "track": track,
            }, now=self._clock()))
        return events

    def _check_forward(self) -> list[PromotionEvent]:
        from quant_platform.research.forward import ForwardTracker

        records = ForwardTracker(self._dir).records()
        events = []
        for spec_hash in {event.spec_hash for event in self.ledger.events()}:
            stage, outcome = self.ledger.state(spec_hash)
            if (stage, outcome) != ("forward", "started"):
                continue
            started = next(event for event in reversed(self.ledger.history(spec_hash)) if event.stage == "forward")
            sessions = sorted({
                item["date"] for item in records
                if item["spec_hash"] == spec_hash and item["date"] >= started.evidence.get("from", "")
            })
            if len(sessions) >= FORWARD_SESSIONS:
                spec = StrategySpec.model_validate(started.spec)
                events.append(self.ledger.append(spec, "forward", "passed", {
                    "sessions": len(sessions), "first": sessions[0], "last": sessions[-1],
                }, now=self._clock()))
        return events

    # --- the user's decision ---------------------------------------------------
    def approve(self, spec_hash: str, note: str = "") -> PromotionEvent:
        stage, outcome = self.ledger.state(spec_hash)
        if (stage, outcome) != ("forward", "passed"):
            raise ResearchGateError("只有完成前向模擬（至少 40 個交易日）的候選可以核准")
        last = self.ledger.history(spec_hash)[-1]
        evidence: dict[str, object] = {"note": note[:500]}
        if self.ledger.track(spec_hash) == "aggressive":
            # The tolerance may have changed since the development gate: check it again now.
            deepest = self.ledger.deepest_drawdown(spec_hash)
            if self._tolerance is None:
                raise ResearchGateError("進攻型策略需要先在計畫頁設定可承受回撤才能核准")
            if deepest is None or deepest < -self._tolerance:
                shown = "未知" if deepest is None else f"{deepest:.1%}"
                raise ResearchGateError(f"這個進攻型策略的最深回撤 {shown} 超過目前計畫的可承受回撤 {self._tolerance:.0%}")
            evidence.update(track="aggressive", drawdown_tolerance=self._tolerance, plan_version=self._plan_version)
        return self.ledger.append(StrategySpec.model_validate(last.spec), "approved", "approved",
                                  evidence, actor="user", now=self._clock())

    def revoke(self, spec_hash: str, note: str = "") -> PromotionEvent:
        stage, _outcome = self.ledger.state(spec_hash)
        if stage != "approved":
            raise ResearchGateError("只有已核准的策略可以撤銷")
        last = self.ledger.history(spec_hash)[-1]
        return self.ledger.append(StrategySpec.model_validate(last.spec), "revoked", "revoked",
                                  {"note": note[:500]}, actor="user", now=self._clock())

    def overview(self) -> dict[str, object]:
        """What the research page shows."""
        review = self.development_review()
        window_ok = [row for row in review if row["window_ok"]]
        stats = self._stats()
        best = None
        if stats and stats.get("candidates"):
            top = max(stats["candidates"], key=lambda item: item["dsr"]["deflated_sharpe"])
            best = {"name": top["name"], "dsr": top["dsr"]["deflated_sharpe"]}
        latest: dict[str, PromotionEvent] = {}
        for event in self.ledger.events():
            latest[event.spec_hash] = event
        pipeline = []
        for event in latest.values():
            track = self.ledger.track(event.spec_hash)
            deepest = self.ledger.deepest_drawdown(event.spec_hash)
            over = track == "aggressive" and (
                self._tolerance is None or deepest is None or deepest < -self._tolerance)
            pipeline.append({
                "spec_hash": event.spec_hash, "name": event.name, "stage": event.stage,
                "stage_label": STAGE_LABELS.get(event.stage, event.stage), "outcome": event.outcome,
                "at": event.at, "evidence": event.evidence,
                "track": track, "track_label": TRACK_LABELS.get(track, track),
                "deepest_drawdown": deepest, "over_tolerance": over,
                "can_approve": (event.stage, event.outcome) == ("forward", "passed") and not over,
                "can_revoke": event.stage == "approved",
            })
        # Rules whose windows pass but whose drawdown is deeper than the standard gate allows:
        # what tolerance each would need (shown rounded up; the gate itself uses the raw value).
        deep = [
            {
                "name": row["record"].spec_name, "max_drawdown": row["metrics"]["max_drawdown"],
                "needed": math.ceil(-row["metrics"]["max_drawdown"] * 100 - 1e-9),
                "within": row["track"] == "aggressive",
            }
            for row in window_ok if row["deep"]
        ]
        return {
            "candidates": len(review),
            "window_ok": len(window_ok),
            "eligible": sum(1 for row in review if row["eligible"]),
            "best_dsr": best,
            "pipeline": sorted(pipeline, key=lambda row: row["at"], reverse=True),
            "chain_ok": not self.ledger.verify(),
            "deep_drawdown": sorted(deep, key=lambda item: item["max_drawdown"], reverse=True)[:5],
            "gates": {"win": WIN_GATE, "robust": ROBUST_WIN_GATE, "drawdown": DRAWDOWN_SLACK, "dsr": DSR_GATE,
                      "pbo": PBO_GATE, "forward": FORWARD_SESSIONS, "aggressive_drawdown": self._tolerance},
        }
