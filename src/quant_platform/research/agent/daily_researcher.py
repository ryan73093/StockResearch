"""S9-W07 (2026-10-09, 使用者：兩個都做，上限 5 美元): the AI researcher on the new design.

Once a week the model proposes at most two daily-decision rules, each with the factor family it bets on,
the hypothesis, why it should beat the same money in 0050 and how it could fail. The AI-methods review
(reports/AI 交易方法 實證與架構.md) sets the controls:

- a closed set of tools: the model only writes a rule in the DailyRule fields below (factors from the
  platform's list, a few settings); it runs no code and reads no data;
- what it sees stops at 2020-10: the factor strength of 2015-06..2020-10 and each tried rule's gap to 0050
  over the same months. The check period (2020-10 on), the tiers and the forward record stay unseen;
- no near-repeats: a rule whose factor set (with signs) and 0050 share were already tried is refused;
- every proposal is written to the journal before it runs, then registered as a trial (it counts in the
  multiple testing like any rule) and judged by the same gate; it shows in the pool as 「AI 研究員」.

The researcher as a whole is a strategy too: ``research/stock_forward.py`` keeps an account that follows
its best qualifying rule, switching only on quarterly review days.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from quant_platform.research.agent.llm import BudgetExceeded, LLMError, ResponsesClient
from quant_platform.research.agent.researcher import Journal

logger = logging.getLogger(__name__)
TAIPEI = ZoneInfo("Asia/Taipei")
VERSION = "researcher-2.0.0"
OPERATION = "research_daily_round"
MAX_PROPOSALS = 2
SEEN_UNTIL = "2020-09"                      # the last month of results the model sees
TOPS = (10, 15, 20, 25, 30)
CORES = (0.0, 0.5)
CAPS = (0.0, 0.2, 0.3)

INSTRUCTIONS = """你是台股量化研究員，只負責提出「每天收盤後決策的選股規則」，不做個別股票判斷，也不預測行情。
帳戶：啟動資金 30 萬、每月 5 日再投入 1 萬，整個帳戶都能買賣；比較對象是同樣的錢全部買 0050。
規則的寫法（只能用這些欄位）：
- name：「AI：」開頭，40 字以內。
- factors：1～3 個因子，只能從清單選；權重 -1～1、不能是 0（負的代表押相反方向，例如買超賣的）；多個因子是百分位加權相加。
- top：持有前幾名，只能是 10、15、20、25、30。
- check：daily（每天）、weekly（每週）、monthly（每月）檢查換股。
- industry_cap：同產業最多佔幾成，0（不限）、0.2、0.3。
- weighting：equal（等額）或 inverse_vol（依波動度，波動小的買多）。
- core：帳戶放在 0050 的比例，0 或 0.5。
規範：
1. 你只看得到 2015-06～2020-09 的結果；2020-10 以後的結果、規則的等級與前向紀錄都不提供，也不要猜。
2. 每一個提出的規則都算一次試驗，試越多越難證明有效。每次最多 {max_proposals} 個，只提出有明確經濟理由、而且和已試過的不同的想法：不要只改參數（例如前 20 改前 30），因子組合與 0050 比例和已試過的一樣的會被拒絕。
3. 規則會用 2015-06～2026-09 的完整資料、同一套門檻檢驗，過的才會進前向觀察。
只輸出一個 JSON 物件：
{{"analysis": "對目前結果的觀察",
  "proposals": [{{"family": "押注的因子類別", "hypothesis": "假設", "rationale": "為什麼可能贏 0050", "failure": "可能失敗的原因", "rule": {{...}}}}]}}"""


def _early_gap(report: dict) -> float | None:
    """The rule's monthly gaps to 0050 summed over the months the researcher may see, per year."""
    months = {month: value for month, value in (report.get("monthly_active_returns") or {}).items() if month <= SEEN_UNTIL}
    if not months:
        return None
    return sum(months.values()) / (len(months) / 12)


class DailyResearcher:
    def __init__(self, research_dir: str | Path, client: ResponsesClient | None, clock=None) -> None:
        self._dir = Path(research_dir)
        self._client = client
        self._clock = clock or (lambda: datetime.now(UTC))
        self.journal = Journal(self._dir / "journal.jsonl")

    # --- what the model sees ---------------------------------------------------------------------
    def tried(self) -> list[dict[str, object]]:
        """Every new-design rule tried so far: name, family, factor set, 0050 share, early yearly gap."""
        from quant_platform.research.categories import classify
        from quant_platform.research.pool import _spec_of
        from quant_platform.research.registry import TrialRegistry

        latest = {}
        for record in TrialRegistry(self._dir / "trials.jsonl").records():
            if record.kind == "candidate" and record.period == "recent":
                latest[record.spec_hash] = record
        rows = []
        for spec_hash, record in latest.items():
            spec = _spec_of(self._dir, record.report_file)
            if not spec or spec.get("kind") == "blend":
                continue
            try:
                report = json.loads((self._dir / "reports" / record.report_file).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                report = {}
            rows.append({"spec_hash": spec_hash, "name": record.spec_name, "family": classify(spec)["family"],
                         "signature": signature(spec.get("factors") or {}, float(spec.get("core") or 0)),
                         "early_gap": _early_gap(report)})
        return rows

    def build_prompt(self) -> tuple[str, str]:
        from quant_platform.research.categories import FACTOR_FAMILY
        from quant_platform.research.daily import RULE_FACTORS
        from quant_platform.research.factors import latest as latest_strength

        lines = [f"- {name}：{label}（{FACTOR_FAMILY.get(name, '機器學習' if name.startswith('ml_gbm') else '其他')}）"
                 for name, label in RULE_FACTORS.items() if name != "ml_gbm"]
        strength = latest_strength(self._dir / "factors") or {}
        strength_lines = []
        for item in strength.get("factors") or []:
            early = (item.get("periods") or {}).get("before_regime") or {}
            if early:
                strength_lines.append(f"- {item['label']}：前五分之一比平均每年 {_pp(early.get('top_excess_year'))}、"
                                      f"比 0050 {_pp(early.get('top_vs_0050_year'))}、排序相關 {early.get('ic')}")
        tried = sorted(self.tried(), key=lambda row: (row["family"], row["name"]))
        tried_lines = [f"- [{row['family']}] {row['name']}：2015-06～2020-09 平均每年比 0050 {_pp(row['early_gap'])}"
                       for row in tried]
        mine = [entry for entry in self.journal.entries() if entry.get("version") == VERSION][-6:]
        journal_lines = []
        for entry in mine:
            for item in entry.get("accepted") or []:
                journal_lines.append(f"- {entry.get('round_id')}：{item.get('name')}（假設：{item.get('hypothesis', '')}）→ "
                                     f"2015-06～2020-09 平均每年比 0050 {_pp(item.get('early_gap'))}")
        user_input = "\n".join([
            "可用的因子：", *lines,
            "因子強弱（2015-06～2020-10，每週排名、看之後 20 個交易日）：", *(strength_lines or ["（沒有資料）"]),
            f"已試過的規則（{len(tried)} 個，同一套帳戶；只列 2015-06～2020-09 的結果）：", *tried_lines,
            "你之前的提案：" if journal_lines else "你之前的提案：尚無。", *journal_lines,
            f"請提出最多 {MAX_PROPOSALS} 個規則，以 JSON 回覆。",
        ])
        return INSTRUCTIONS.format(max_proposals=MAX_PROPOSALS), user_input

    # --- checking a proposal ---------------------------------------------------------------------------
    def validate(self, payload: dict, tried: list[dict[str, object]]) -> tuple[list[tuple[object, dict]], list[dict]]:
        from quant_platform.research.daily import RULE_FACTORS, DailyRule

        seen = {row["signature"] for row in tried} | {row["spec_hash"] for row in tried}
        accepted, rejected = [], []
        for item in (payload.get("proposals") or [])[:10]:
            if not isinstance(item, dict) or not isinstance(item.get("rule"), dict):
                rejected.append({"name": "", "reason": "提案格式不對"})
                continue
            raw = dict(item["rule"])
            name = str(raw.get("name") or "")
            if len(accepted) >= MAX_PROPOSALS:
                rejected.append({"name": name, "reason": f"超過每次 {MAX_PROPOSALS} 個"})
                continue
            factors = raw.get("factors") or {}
            unknown = [factor for factor in factors if factor not in RULE_FACTORS or factor == "ml_gbm"]
            if unknown or not 1 <= len(factors) <= 3 or any(not isinstance(w, (int, float)) or w == 0 or abs(w) > 1
                                                            for w in factors.values()):
                rejected.append({"name": name, "reason": f"因子不合規定：{', '.join(unknown) or '數量或權重'}"})
                continue
            settings = {"top": raw.get("top", 20), "check": raw.get("check", "daily"),
                        "industry_cap": float(raw.get("industry_cap") or 0), "weighting": raw.get("weighting", "equal"),
                        "core": float(raw.get("core") or 0)}
            if (settings["top"] not in TOPS or settings["core"] not in CORES or settings["industry_cap"] not in CAPS
                    or settings["check"] not in ("daily", "weekly", "monthly")
                    or settings["weighting"] not in ("equal", "inverse_vol")):
                rejected.append({"name": name, "reason": "設定超出允許的選項"})
                continue
            titled = name if name.startswith("AI：") else "AI：" + name
            try:
                rule = DailyRule(name=titled[:40], factors={key: float(value) for key, value in factors.items()}, **settings)
            except ValidationError as exc:
                rejected.append({"name": name, "reason": f"不符合規則格式：{exc.errors()[0].get('msg', '')}"})
                continue
            key = signature(rule.factors, rule.core)
            if rule.rule_hash in seen or key in seen:
                rejected.append({"name": name, "reason": "因子組合與 0050 比例和已試過的規則相同"})
                continue
            seen |= {rule.rule_hash, key}
            accepted.append((rule, {"family": str(item.get("family", ""))[:40],
                                    "hypothesis": str(item.get("hypothesis", ""))[:500],
                                    "rationale": str(item.get("rationale", ""))[:800],
                                    "failure": str(item.get("failure", ""))[:500]}))
        return accepted, rejected

    # --- one weekly round --------------------------------------------------------------------------------
    def run_round(self, history: str | Path, load=None) -> dict[str, object]:
        """Propose, pre-register in the journal, run each proposal as a trial, record what it may see later."""
        started = self._clock()
        round_id = f"{started.astimezone(TAIPEI):%Y%m%d-%H%M%S}-v2"
        entry: dict[str, object] = {"version": VERSION, "round_id": round_id, "started_at": started.isoformat(timespec="seconds"),
                                    "model": self._client.model if self._client else None, "period": "recent",
                                    "accepted": [], "rejected": []}
        if self._client is None:
            entry.update(status="disabled", error="沒有可用的模型設定")
            self.journal.append(entry)
            return entry
        instructions, user_input = self.build_prompt()
        try:
            payload, usage = self._client.json_call(instructions, user_input, OPERATION, round_id)
        except (BudgetExceeded, LLMError) as exc:
            entry.update(status="budget_exceeded" if isinstance(exc, BudgetExceeded) else "llm_error", error=str(exc))
            self.journal.append(entry)
            return entry
        proposals, rejected = self.validate(payload, self.tried())
        # pre-registration: the proposals are on record before any of them runs
        self.journal.append({**entry, "status": "registered", "analysis": str(payload.get("analysis", ""))[:1500],
                             "registered": [{"name": rule.name, "rule": rule.canonical(), **notes} for rule, notes in proposals],
                             "rejected": rejected, "usage": {key: usage.get(key) for key in ("input_tokens", "output_tokens")}})
        accepted = []
        if proposals:
            from quant_platform.research import daily as daily_research
            from quant_platform.research.costs import broker_costs
            from quant_platform.research.registry import TrialRegistry

            data, fp = (load or daily_research.load)(Path(history))
            fingerprint = daily_research.fingerprint(Path(history))
            registry = TrialRegistry(self._dir / "trials.jsonl")
            for rule, notes in proposals:
                record, report = daily_research.run_trial(rule, history, registry, self._dir / "reports",
                                                          broker_costs("cathay"), data, fp, fingerprint)
                accepted.append({"name": rule.name, "spec_hash": rule.rule_hash, "trial_id": record.trial_id,
                                 "early_gap": _early_gap(report), **notes})
        entry.update(status="ok", accepted=accepted, rejected=rejected,
                     finished_at=self._clock().isoformat(timespec="seconds"), budget=self._client.budget_status())
        self.journal.append(entry)
        return entry


def signature(factors: dict, core: float) -> str:
    """The factor set with signs and the 0050 share: two rules alike here are near-repeats."""
    return json.dumps({"factors": sorted(f"{'-' if weight < 0 else '+'}{name}" for name, weight in factors.items()),
                       "core": round(float(core), 2)}, sort_keys=True)


def build(settings, research_dir: str | Path) -> DailyResearcher:
    from quant_platform.research.agent.llm import UsageLedger, resolve_openai_key

    client = None
    if settings.research_agent_enabled:
        try:
            key = resolve_openai_key(settings.openai_api_key)
        except LLMError:
            key = ""
        if key:
            client = ResponsesClient(key, settings.research_agent_model,
                                     UsageLedger(Path(research_dir) / "agent" / "usage.jsonl"),
                                     settings.research_agent_monthly_budget_usd,
                                     operations=(OPERATION, "research_round", "connection_check"),
                                     total_budget_usd=settings.llm_monthly_budget_usd, label="AI 研究員")
    return DailyResearcher(research_dir, client)


def ran_within(research_dir: str | Path, days: int, now: datetime | None = None) -> bool:
    """Whether a round (registered or finished) started in the last ``days`` days: the researcher is weekly."""
    from datetime import timedelta

    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    for entry in Journal(Path(research_dir) / "journal.jsonl").entries():
        if entry.get("version") != VERSION or entry.get("status") not in ("registered", "ok"):
            continue
        try:
            started = datetime.fromisoformat(str(entry.get("started_at")))
        except ValueError:
            continue
        if started >= cutoff:
            return True
    return False


def proposals(research_dir: str | Path) -> list[dict[str, object]]:
    """Every rule the new-design researcher proposed and ran (newest first)."""
    output = []
    for entry in reversed(Journal(Path(research_dir) / "journal.jsonl").entries()):
        if entry.get("version") == VERSION and entry.get("status") == "ok":
            for item in entry.get("accepted") or []:
                output.append({**item, "round_id": entry.get("round_id")})
    return output


def _pp(value) -> str:
    return "—" if value is None else f"{value * 100:+.1f} 個百分點"
