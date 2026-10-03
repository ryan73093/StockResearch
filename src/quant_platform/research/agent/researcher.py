"""The AI researcher's nightly loop (S4-W04).

One round: read the development-period results and the research journal →
the model writes an analysis of the last round, a hypothesis, its rationale
and up to N strategy specs → each spec is validated (StrategySpec v1, only
catalog ETFs with development data, no duplicate rules) → valid specs run as
registered candidate trials on the development period only, so they count in
the multiple-testing correction → the round, its cost and every rejection go
to the append-only journal (instance/research/journal.jsonl). The model never
sees validation or holdout results and never places orders (REQUIREMENTS §7,
§8). A night stops at the trial limit, the round limit, the monthly budget
or the first model error.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from quant_platform.research.agent.llm import BudgetExceeded, LLMError, ResponsesClient
from quant_platform.research.batches import BATCHES
from quant_platform.research.cashflow import ContributionPlan
from quant_platform.research.costs import broker_costs
from quant_platform.research.history.catalog import SERIES_BY_KEY
from quant_platform.research.market import MarketData
from quant_platform.research.periods import PERIODS, ResearchGateError, period_basis, run_trial
from quant_platform.research.registry import TrialRegistry, current_basis, distinct_rules
from quant_platform.research.spec import BASELINES, TRADABLE, StrategySpec, json_schema
from quant_platform.research.summary import DSR_GATE, PBO_GATE, WIN_RATIO_GATE, passes_development_gate

logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
PERIOD = "development"
STANDARD_PLAN = ContributionPlan(monthly_amount=10_000, day_of_month=5)
LOCK_STALE_SECONDS = 6 * 60 * 60
NIGHT_HOURS = 20  # rounds within this window count against one night's limits


@dataclass(frozen=True)
class AgentLimits:
    rounds_per_night: int = 3
    specs_per_round: int = 4
    trials_per_night: int = 12


def rule_key(spec: StrategySpec) -> str:
    """Hash of the rule itself; the name and description do not make a new rule."""
    body = spec.canonical_dict(exclude={"name", "description"})
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = path

    def entries(self) -> list[dict[str, object]]:
        if not self.path.is_file():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows

    def append(self, entry: dict[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


INSTRUCTIONS = """你是台股盤後投資研究員，只負責提出並檢驗「投入規則」，不做個別交易判斷。
研究題目：薪資每月入帳的資金，在相同現金流下，長期能否勝過「定期定額基準」——每月 5 日入帳（遇休市順延），當天以盤後零股把可用現金全數買進 0050，股利留到下次一起投入。

規範（違反的設定會被拒絕並記錄）：
1. 只能用 StrategySpec v1（使用者訊息中的 JSON Schema）描述規則；不得指名個股；配置與訊號標的只能從「可用標的」清單選。除了投入時點與金額倍數，也可以用 allocation.defensive（趨勢控制：訊號收盤跌破均線時改用防守配置）與 allocation.rotation（ETF 輪動；搭配 core 就是核心＋衛星）；訊號一律使用還原分割後的收盤價。
2. 你只會看到開發期（2004-02-11～2016-12-31）的結果；驗證期與最終驗證期的結果不提供，也不要推測或引用 2017 年以後的行情。
3. 每個不同的設定都計入多重檢定（同一設定在資料更新後重跑只算一次），試越多越難證明有效。每輪最多提出 {specs_per_round} 個設定；只提出有明確經濟機制的假設，不要做參數掃描、不要只微調已失敗設定的數字。
4. 不要重複已測過的規則（名稱與說明不同但規則相同也算重複）。
5. 成本：手續費不打折、每筆最低 20 元；盤後零股成交價＝收盤加 20 bps 進位；賣出 ETF 證交稅 0.1%（債券 ETF 免）；現金股利除息後 25 天入帳。
6. 門檻：開發期 3 年滾動視窗勝率 ≥ {win_gate:.0%} 且中位超額 > 0；扣除多重檢定後 DSR ≥ {dsr_gate}、PBO ≤ {pbo_gate:.0%}；最大回撤不比定期定額深超過 5 個百分點（一般賽道），更深的只能走進攻型賽道（回撤須在使用者計畫的可承受範圍內，其餘門檻相同）。勝率只是參考，主要看相同現金流下的超額與最差期間。
7. 名稱格式「方向：說明」（例如「回撤：…」「配置：…」「時點：…」），40 字以內。

只輸出一個 JSON 物件，欄位如下：
{{"analysis": "對上一輪結果的分析（第一輪寫對目前結果的觀察）",
  "hypothesis": "本輪要檢驗的假設",
  "rationale": "為什麼這個機制可能在相同現金流下勝過定期定額，以及可能失敗的原因",
  "specs": [StrategySpec 物件，最多 {specs_per_round} 個],
  "extension_ideas": ["StrategySpec v1 表達不了、但值得研究的規則（可為空陣列）"]}}
"""


class ResearchAgent:
    def __init__(
        self,
        research_dir: str | Path,
        client: ResponsesClient | None,
        limits: AgentLimits = AgentLimits(),
        clock=None,
    ) -> None:
        self._dir = Path(research_dir)
        self._client = client
        self._limits = limits
        self._clock = clock or (lambda: datetime.now(UTC))
        self.registry = TrialRegistry(self._dir / "trials.jsonl")
        self.journal = Journal(self._dir / "journal.jsonl")

    # --- context ----------------------------------------------------------
    @staticmethod
    def eligible_assets(market: MarketData) -> dict[str, date]:
        """Tradable catalog ETFs with at least three years of development-period data."""
        _start, end = PERIODS[PERIOD]
        cutoff = date(end.year - 3, end.month, end.day)
        return {
            asset: market.first_day(asset)
            for asset in sorted(market.closes)
            if asset in TRADABLE and market.first_day(asset) <= cutoff
        }

    def tested_rules(self) -> set[str]:
        keys = {rule_key(spec) for spec in BASELINES.values()}
        keys |= {rule_key(spec) for batch in BATCHES.values() for spec in batch()}
        for entry in self.journal.entries():
            keys |= {str(item.get("rule_key")) for item in entry.get("accepted") or []}
        return keys

    def build_prompt(self, market: MarketData) -> tuple[str, str]:
        limits = self._limits
        instructions = INSTRUCTIONS.format(
            specs_per_round=limits.specs_per_round, win_gate=WIN_RATIO_GATE, dsr_gate=DSR_GATE, pbo_gate=PBO_GATE,
        )
        assets = self.eligible_assets(market)
        names = "、".join(
            f"{key}（{SERIES_BY_KEY[key].name}，{first.isoformat()} 起"
            + (f"，{market.notes[key]}" if key in market.notes else "") + "）"
            for key, first in assets.items()
        )
        current, older = current_basis(self.registry.records(), PERIOD)

        def three(record) -> dict:
            return (record.metrics.get("windows") or {}).get("3y") or {}

        def five(record) -> dict:
            return (record.metrics.get("windows") or {}).get("5y") or {}

        ranked = sorted(current, key=lambda record: three(record).get("median_excess") or -1, reverse=True)
        lines = [
            f"- {record.spec_name}｜3年勝率 {_pct(three(record).get('win_ratio'))}｜"
            f"3年中位超額 {_signed(three(record).get('median_excess'))}｜最差3年 {_signed(three(record).get('worst_excess'))}｜"
            f"5年勝率 {_pct(five(record).get('win_ratio'))}｜全期超額 {_signed(record.metrics.get('full_period_excess'))}"
            for record in ranked
        ]
        stats = self._latest_stats()
        # The benchmark itself (the first night's model asked for it: every excess is relative to it).
        # Every trial report carries the benchmark over the same span; the longest span describes it best.
        benchmark = BASELINES["benchmark_dca"]
        spanning = ([record for record in current if record.metrics.get("benchmark_xirr") is not None
                     and record.metrics.get("benchmark_max_drawdown") is not None]   # older trials lack the drawdown
                    or [record for record in current if record.metrics.get("benchmark_xirr") is not None])
        reference = min(spanning, key=lambda record: str(record.metrics.get("start"))) if spanning else None
        benchmark_line = (
            f"比較基準「{benchmark.name}」（每月 5 日入帳當天全數買 0050）開發期 "
            f"{reference.metrics.get('start')}～{reference.metrics.get('end')}：XIRR {_pct2(reference.metrics.get('benchmark_xirr'))}、"
            f"最大回撤 {_pct2(reference.metrics.get('benchmark_max_drawdown'))}；下列勝率與超額都是相對它計算。"
            if reference is not None else "下列勝率與超額都是相對定期定額基準計算。"
        )
        journal_lines = []
        for entry in self.journal.entries()[-5:]:
            results = "；".join(
                f"{item['name']} 3年勝率 {_pct(item.get('win_3y'))}、中位 {_signed(item.get('median_3y'))}"
                for item in entry.get("accepted") or []
            ) or "沒有可回測的設定"
            journal_lines.append(
                f"- {entry.get('round_id')}：假設「{entry.get('hypothesis', '')}」→ {results}"
                + (f"；拒絕 {len(entry.get('rejected') or [])} 個" if entry.get("rejected") else "")
            )
        user_input = "\n".join([
            f"可用標的（開發期有 3 年以上資料）：{names}；訊號標的另可用 TAIEX（加權指數）。",
            "StrategySpec v1 JSON Schema：",
            json.dumps(json_schema(), ensure_ascii=False, separators=(",", ":")),
            benchmark_line,
            f"已測設定（開發期、目前資料版本 {len(current)} 個，依 3 年中位超額排序；超額以投入金額計）：",
            *lines,
            f"多重檢定：本期間累計 {distinct_rules(current + older)} 個不同設定"
            + (f"；最佳 DSR {stats['best_dsr']:.2f}、PBO {stats['pbo']:.0%}" if stats else "") + "。",
            "最近研究日誌：" if journal_lines else "研究日誌：尚無（這是第一輪）。",
            *journal_lines,
            f"請提出本輪假設與最多 {self._limits.specs_per_round} 個設定。",
        ])
        return instructions, user_input

    def _latest_stats(self) -> dict | None:
        from quant_platform.research.reports import latest_stats

        stats = latest_stats(self._dir / "stats", PERIOD)
        if not stats or not stats.get("candidates"):
            return None
        values = [item["dsr"]["deflated_sharpe"] for item in stats["candidates"]]
        return {"best_dsr": max(values), "pbo": (stats.get("pbo") or {}).get("pbo") or 0.0}

    def check(self) -> dict[str, object]:
        """A tiny call that proves the key, the model and the JSON format work (not research)."""
        if self._client is None:
            raise LLMError("AI 研究員未啟用或沒有 OPENAI_API_KEY")
        payload, usage = self._client.json_call(
            "只輸出 JSON 物件。", '請回傳 {"ok": true, "model_ready": true}', "connection_check",
        )
        return {"payload": payload, "usage": usage, "budget": self._client.budget_status()}

    # --- one round ------------------------------------------------------------
    def validate(self, payload: dict, market: MarketData, seen: set[str]) -> tuple[list[StrategySpec], list[dict]]:
        assets = set(self.eligible_assets(market))
        accepted: list[StrategySpec] = []
        rejected: list[dict] = []
        items = payload.get("specs")
        if not isinstance(items, list):
            return [], [{"name": "", "reason": "specs 不是陣列"}]
        for index, item in enumerate(items):
            name = str(item.get("name", "")) if isinstance(item, dict) else ""
            if len(accepted) >= self._limits.specs_per_round:
                rejected.append({"name": name, "reason": f"超過每輪 {self._limits.specs_per_round} 個上限"})
                continue
            try:
                spec = StrategySpec.model_validate(item)
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(part) for part in first.get("loc", ()))
                rejected.append({"name": name, "reason": f"不符合 StrategySpec：{where} {first.get('msg', '')}".strip()})
                continue
            unavailable = sorted((set(spec.assets) | ({spec.signal} - {"TAIEX"})) - assets)
            if unavailable:
                rejected.append({"name": spec.name, "reason": f"開發期資料不足：{', '.join(unavailable)}"})
                continue
            key = rule_key(spec)
            if key in seen:
                rejected.append({"name": spec.name, "reason": "與已測規則相同"})
                continue
            seen.add(key)
            accepted.append(spec)
        return accepted, rejected

    def run_round(self, market: MarketData, index: int, trials_left: int) -> dict[str, object]:
        started = self._clock()
        round_id = f"{started.astimezone(TAIPEI):%Y%m%d-%H%M%S}-{index}"
        entry: dict[str, object] = {
            "round_id": round_id,
            "started_at": started.isoformat(timespec="seconds"),
            "model": self._client.model if self._client else None,
            "period": PERIOD,
            "basis": period_basis(market, PERIOD)[:12],
            "accepted": [],
            "rejected": [],
        }
        if self._client is None:
            entry.update(status="disabled", error="沒有可用的模型設定")
            return self._finish(entry)
        instructions, user_input = self.build_prompt(market)
        try:
            payload, usage = self._client.json_call(instructions, user_input, "research_round", round_id)
        except BudgetExceeded as exc:
            entry.update(status="budget_exceeded", error=str(exc))
            return self._finish(entry)
        except LLMError as exc:
            entry.update(status="llm_error", error=str(exc))
            return self._finish(entry)
        entry.update(
            analysis=str(payload.get("analysis", ""))[:2000],
            hypothesis=str(payload.get("hypothesis", ""))[:1000],
            rationale=str(payload.get("rationale", ""))[:2000],
            extension_ideas=[str(item)[:300] for item in (payload.get("extension_ideas") or [])][:5],
            usage={key: usage.get(key) for key in ("input_tokens", "output_tokens")},
        )
        specs, rejected = self.validate(payload, market, self.tested_rules())
        if len(specs) > trials_left:
            rejected += [{"name": spec.name, "reason": "已達今晚試驗上限"} for spec in specs[trials_left:]]
            specs = specs[:trials_left]
        stamp = f"{started.astimezone(TAIPEI):%Y%m%d-%H%M%S}"
        accepted = []
        for spec in specs:
            try:
                outcome = run_trial(
                    kind="candidate", spec=spec, period=PERIOD, market=market, plan=STANDARD_PLAN,
                    registry=self.registry, reports_dir=self._dir / "reports", costs=broker_costs("conservative"),
                    generated_at=stamp,
                )
            except (ResearchGateError, ValueError) as exc:
                rejected.append({"name": spec.name, "reason": f"無法回測：{exc}"})
                continue
            record = outcome.record
            window = (record.metrics.get("windows") or {}).get("3y") or {}
            accepted.append({
                "name": spec.name,
                "spec": spec.model_dump(mode="json"),
                "spec_hash": spec.spec_hash,
                "rule_key": rule_key(spec),
                "trial_id": record.trial_id,
                "reused": outcome.reused,
                "win_3y": window.get("win_ratio"),
                "median_3y": window.get("median_excess"),
                "worst_3y": window.get("worst_excess"),
                "full_excess": record.metrics.get("full_period_excess"),
                "passes_gate": passes_development_gate(record),
            })
        entry.update(status="ok", accepted=accepted, rejected=rejected)
        return self._finish(entry)

    def _finish(self, entry: dict[str, object]) -> dict[str, object]:
        entry["finished_at"] = self._clock().isoformat(timespec="seconds")
        if self._client is not None:
            entry["budget"] = self._client.budget_status()
        self.journal.append(entry)
        return entry

    # --- a night --------------------------------------------------------------
    def used_tonight(self) -> tuple[int, int]:
        """Rounds and new trials of the last NIGHT_HOURS: a manual run and the scheduled one
        share one night's limits (rounds that ended in an error do not count)."""
        since = self._clock() - timedelta(hours=NIGHT_HOURS)
        rounds = trials = 0
        for entry in self.journal.entries():
            try:
                started = datetime.fromisoformat(str(entry.get("started_at")))
            except ValueError:
                continue
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if entry.get("status") == "ok" and started >= since:
                rounds += 1
                trials += sum(1 for item in entry.get("accepted") or [] if not item.get("reused"))
        return rounds, trials

    def run_night(self, market: MarketData) -> list[dict[str, object]]:
        """Rounds until a limit, the budget or an error stops them; then the stats."""
        lock = self._dir / "agent" / "running.lock"
        if not _acquire(lock):
            logger.info("AI researcher skipped: another round is running")
            return []
        entries: list[dict[str, object]] = []
        try:
            used_rounds, used_trials = self.used_tonight()
            trials_left = self._limits.trials_per_night - used_trials
            if used_rounds:
                logger.info("AI researcher: %s rounds and %s trials already used tonight", used_rounds, used_trials)
            for index in range(used_rounds + 1, self._limits.rounds_per_night + 1):
                if trials_left <= 0:
                    break
                entry = self.run_round(market, index, trials_left)
                entries.append(entry)
                trials_left -= sum(1 for item in entry.get("accepted") or [] if not item.get("reused"))
                if entry.get("status") != "ok":
                    break
            if any(entry.get("accepted") for entry in entries):
                from quant_platform.research.significance import save_stats, significance

                basis = period_basis(market, PERIOD)
                save_stats(significance(self.registry, self._dir / "reports", PERIOD, fingerprint=basis),
                           self._dir / "stats", PERIOD, self._clock())
        finally:
            lock.unlink(missing_ok=True)
        return entries


def build_agent(settings, research_dir: str | Path) -> ResearchAgent:
    """The agent with a model client when enabled and a key is configured."""
    from quant_platform.research.agent.llm import UsageLedger, resolve_openai_key

    research_dir = Path(research_dir)
    limits = AgentLimits(
        settings.research_agent_rounds_per_night, settings.research_agent_specs_per_round,
        settings.research_agent_trials_per_night,
    )
    client = None
    if settings.research_agent_enabled:
        try:
            key = resolve_openai_key(settings.openai_api_key)
        except LLMError as exc:
            logger.warning("AI researcher has no key: %s", exc)
            key = ""
        if key:
            client = ResponsesClient(
                key, settings.research_agent_model, UsageLedger(research_dir / "agent" / "usage.jsonl"),
                settings.research_agent_monthly_budget_usd,
            )
    return ResearchAgent(research_dir, client, limits)


def agent_status(settings, research_dir: str | Path, now: datetime | None = None) -> dict[str, object]:
    """What the research page shows; reads only local files (no model call)."""
    from quant_platform.research.agent.llm import UsageLedger, resolve_openai_key

    research_dir = Path(research_dir)
    try:
        configured = bool(resolve_openai_key(settings.openai_api_key))
    except LLMError:
        configured = False
    entries = Journal(research_dir / "journal.jsonl").entries()
    spent = UsageLedger(research_dir / "agent" / "usage.jsonl").month_spend(now or datetime.now(UTC))
    trials = sum(
        1 for entry in entries for item in entry.get("accepted") or [] if not item.get("reused")
    )
    return {
        "enabled": settings.research_agent_enabled,
        "configured": configured,
        "model": settings.research_agent_model,
        "budget_usd": settings.research_agent_monthly_budget_usd,
        "spent_usd": spent,
        "hour": settings.research_agent_hour,
        "limits": {
            "rounds": settings.research_agent_rounds_per_night,
            "specs": settings.research_agent_specs_per_round,
            "trials": settings.research_agent_trials_per_night,
        },
        "rounds": len(entries),
        "trials": trials,
        "recent": list(reversed(entries[-3:])),
    }


def _acquire(lock: Path) -> bool:
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        if lock.exists() and time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
            lock.unlink()  # left behind by a crashed run
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w") as handle:
        handle.write(str(os.getpid()))
    return True


def _pct(value) -> str:
    return "—" if value is None else f"{float(value):.0%}"


def _pct2(value) -> str:
    return "—" if value is None else f"{float(value):.2%}"


def _signed(value) -> str:
    return "—" if value is None else f"{float(value):+.2%}"
