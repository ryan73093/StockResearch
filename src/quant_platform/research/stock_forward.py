"""Forward simulation of stock rules (research roadmap R2, 2026-10-03).

Which rules: every stock rule that beat 0050 DCA in both independent periods (development and
validation gates, research/pool.py) is added to ``forward/stocks/tracked.json`` the first time
it qualifies, with that day as its start — a rule found later never gets the earlier forward
days. Rules can also be added by hand there (``reason`` says why).

What is recorded: after the close on each trading day (15:30, after the 15:15 refresh appended
the day's all-market quotes) every tracked rule is replayed from its start with the standard
cash flow (NT$10,000 on the 5th), the broker's real fees (國泰) and the research fill model, and
the day's state is appended to ``forward/stocks/log.jsonl``: cash, every holding with its close,
the day's trades and units adjustments (ex-rights, splits — dividends stay invested as units, as
in the research), totals, and 0050 DCA from the same start in the same engine. Records are only
appended. Each new record also replays the earlier days and lists any day whose replayed value
differs from what was written then (a data revision shows up there, it never rewrites the log);
``reconcile`` checks that each day follows from the day before through the logged trades.
Forward results are observations; they do not pick rules.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from quant_platform.research.costs import CostModel, broker_costs
from quant_platform.research.legacy_challenger import BENCHMARK, simulate
from quant_platform.research.registry import TrialRegistry
from quant_platform.research.stock_rules import (
    ENGINE_VERSION,
    LUMP_SUM,
    SEED_CAPITAL,
    SEED_MONTHLY,
    STANDARD_PLAN,
    WARMUP_YEARS,
    LumpSumPlan,
    Panel,
    SeedPlan,
    StockRule,
    load_stock_data,
    rankings,
    rule_variant,
    stock_fingerprint,
)

TAIPEI = ZoneInfo("Asia/Taipei")
BROKER = "cathay"            # the fees the stock research ran with
VALUE_TOLERANCE = 1.0        # NT$: rounding in the log
UNITS_TOLERANCE = 1e-3


FIXED_HOLD = ("2026-10-14 平台把固定持有天數的規則改成「月線上續抱」版本追蹤（紀錄保留）；"
              "2026-10-20 使用者更正：持有天數與月線上續抱都是策略的一部分，平台不該替規則改，這個轉換已撤回")
META_STOPPED = ("2026-10-15：AI 研究員還沒有合格的規則，這個帳戶整個放 0050，等於比較基準本身，不符合策略一律 100% 個股"
                "（使用者 2026-10-09），停止追蹤（紀錄保留）；研究員（10-09 已重開）有合格規則時再以 100% 個股的版本重新開始")


_SUMMARY: dict[str, object] = {}
_CHAINED: dict[str, object] = {}
_HELD_OLD = re.compile(r"，已持有滿 (\d+) 個交易日")


def reworded(text: str) -> str:
    """A reason written before 2026-10-20 noon in today's words: the minimum holding is the strategy's own setting
    (使用者 2026-10-20：持有 20 天也是策略), not a rule of the platform. The decision records keep what was written."""
    return _HELD_OLD.sub(lambda match: f"；這個策略設定買進後至少持有 {match.group(1)} 個交易日，已滿", str(text))


def plan_of(item: dict[str, object], calendar: list | None = None):
    """A tracked rule's cash flow: one lump sum (the owner's NT$300,000 account, the default since
    2026-10-04) or the monthly plan (items written before that carry no ``plan``). ``calendar``: every session, so a
    month's contribution falls on its real session (none in the start month of an account started after the 5th)."""
    if item.get("plan") == "seed":
        return SeedPlan(float(item.get("initial") or SEED_CAPITAL), float(item.get("monthly") or SEED_MONTHLY),
                        calendar=calendar)
    if item.get("plan") == "lump_sum":
        return LumpSumPlan(float(item.get("amount") or LUMP_SUM))
    return STANDARD_PLAN


META_AI = {"rule_hash": "meta-ai-researcher-v1", "name": "AI 研究員（整體）：每季跟隨它最好的合格規則",
           "kind": "meta-ai",
           "reason": "AI 研究員本身當成一個策略（使用者 2026-10-09）：開始那天與每季第一個交易日，換到它提出、"
                     "等級最好（T0 → T0 候選 → T1，再比 2015-06 起比 0050 多多少）的規則；還沒有合格的就放 0050。"
                     "換規則那天以收盤賣掉原持股（含費稅），再照新規則買。"}
REVIEW_MONTHS = (1, 4, 7, 10)


def review_days(sessions: list[date], start: date, last: date) -> list[date]:
    """The meta account's decision days: its first session and the first session of each quarter after it."""
    days = [day for day in sessions if start <= day <= last]
    if not days:
        return []
    output = [days[0]]
    for previous, day in zip(days, days[1:]):
        if day.month in REVIEW_MONTHS and day.month != previous.month:
            output.append(day)
    return output


def _report_spec(base: Path, report_file: str | None) -> dict:
    try:
        return json.loads((base / "reports" / str(report_file)).read_text(encoding="utf-8"))["spec"]
    except (OSError, ValueError, KeyError):
        return {}


def best_ai_rule(research_dir: str | Path) -> dict[str, object] | None:
    """The researcher's best qualifying rule now: T0, then T0 候選, then T1; ties by the excess since 2015-06."""
    from quant_platform.research.agent.daily_researcher import proposals
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.daily import TIERS, tier

    base = Path(research_dir)
    mine = {item["spec_hash"] for item in proposals(base)}
    from quant_platform.research.registry import newest

    latest = newest(TrialRegistry(base / "trials.jsonl").records(),
                    lambda record: record.spec_hash in mine and record.kind == "candidate" and record.period == "recent")
    best = None
    for spec_hash, record in latest.items():
        grade, _why = tier(record.metrics)
        if grade not in ("T0", "T0（高風險）", "T0 候選", "T0 候選（高風險）", "T1"):
            continue
        if uses_0050(_report_spec(base, record.report_file)):
            continue
        key = (TIERS.index(grade), -(record.metrics.get("full_period_excess") or 0))
        if best is None or key < best[0]:
            try:
                spec = json.loads((base / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
            except (OSError, ValueError, KeyError):
                continue
            best = (key, {"rule_hash": spec_hash, "name": record.spec_name, "tier": grade, "spec": spec,
                          "trial_id": record.trial_id})
    return best[1] if best else None


def forward_only() -> list[tuple[object, str]]:
    """Experiments that can only be judged forward (no history to test them on), tracked in pairs from the
    same day: R15 D (2026-10-09) — the weekly model rule half in 0050 with the bad-news veto and its control."""
    from quant_platform.research.daily import DailyRule

    base = DailyRule(name="機器學習（含財報）：前 20 名、同產業最多 3 成、每週決策",
                     factors={"ml_gbm_statements": 1.0}, industry_cap=0.3, check="weekly")
    output = [
        (base.model_copy(update={"name": base.name + "、新聞利空不買（LLM）", "news_veto": "v1"}),
         "R15 D 新聞否決實驗：LLM 標出利空且有數字或風險旗標的股票不新買（只前向；和下一列同一天開始比）"),
        (base.model_copy(update={"name": base.name + "、新聞否決對照組", "news_veto": "v1-off"}),
         "R15 D 對照組：同一規則、同一天開始、不看新聞（和上一列比）"),
    ]
    # 2026-10-20 (使用者：LLM 有沒有辦法做成交易 agent): the LLM trader and its twin (research/llm_trader.py); 2026-10-21
    # (使用者：不要跟我說前向觀察只能做一天): from the month's common start like every account
    for factor, label in (("llm_trader", "LLM 交易員"), ("llm_control", "LLM 交易員對照組")):
        output.append((DailyRule(name=f"{label}：#5163 三個子帳戶前 20 名中挑最多 20 檔、隔天收盤成交", factors={factor: 1.0},
                                 top=20, keep=1, min_hold=0, industry_cap=0.3),
                       "LLM 交易員前向實驗（和對照組同一天開始比）" if factor == "llm_trader"
                       else "LLM 交易員的對照組：同樣候選、固定規則（和上一列比）"))
    # 2026-10-20 (使用者：ML、DL、RL、技術分析、LLM 都要加入評估；持有方式都是策略): the same pair in the other two
    # holding styles, forward only (no news before 2026-10-05)
    for label, style in (("月線上續抱", {"hold": "trend", "min_hold": 0}), ("不設最短持有", {"min_hold": 0})):
        for rule, reason in list(output[:2]):
            output.append((DailyRule.model_validate({**rule.model_dump(), **style, "name": f"{rule.name}｜持有：{label}"}),
                           f"{reason}（持有：{label}）"))
    return output


def llm_trader_start(research_dir: str | Path) -> str | None:
    """The LLM trader's first decision day (research/llm_trader.py); None before one."""
    from quant_platform.research.llm_trader import decisions

    made = sorted(item["date"] for item in decisions(research_dir))
    return made[0] if made else None


def qualifying_daily(research_dir: str | Path) -> list[tuple[object, str]]:
    """Daily-decision rules (S9-W02) that pass the new design's gate on 2015-06..2026-09."""
    from quant_platform.research.blend import parse_spec
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.daily import tier

    base = Path(research_dir)
    from quant_platform.research.registry import newest

    latest = newest(TrialRegistry(base / "trials.jsonl").records(),
                    lambda record: record.kind == "candidate" and record.period == "recent")
    output = []
    for record in latest.values():
        grade, _why = tier(record.metrics)
        if grade not in ("T0 候選", "T0 候選（高風險）", "T1"):   # T1 too: beats 0050, more volatile (owner 2026-10-04)
            continue
        try:
            spec = json.loads((base / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
        except (OSError, ValueError, KeyError):
            continue
        if uses_0050(spec):                         # 2026-10-09: the owner holds 0050 apart (NO_0050)
            continue
        reason = (f"新設計 {grade}：2015-06 起比 0050 {record.metrics.get('full_period_excess'):+.1%}、"
                  f"2020-10 起 {record.metrics.get('since_2020_excess'):+.1%}（試驗 #{record.trial_id}）")
        output.append((parse_spec(spec), reason))      # a daily rule, or a blend of them (2026-10-09)
    return output


def qualifying_rules(research_dir: str | Path, plan_kind: str = "SeedPlan") -> list[tuple[StockRule, str]]:
    """Stock rules whose development and validation runs on that cash flow both pass the window and
    drawdown gates."""
    from quant_platform.research.pool import _best_records, _gate

    base = Path(research_dir)
    records = [record for record in TrialRegistry(base / "trials.jsonl").records()
               if record.data_fingerprint.startswith("stocks:")]
    development = _best_records(records, "development", plan_kind)
    validation = _best_records(records, "validation", plan_kind)
    output = []
    for spec_hash, dev in development.items():
        val = validation.get(spec_hash)
        if val is None or _gate(dev.metrics) or _gate(val.metrics):
            continue
        try:
            spec = json.loads((base / "reports" / dev.report_file).read_text(encoding="utf-8"))["spec"]
        except (OSError, ValueError, KeyError):
            continue
        reason = (f"開發期 {dev.metrics.get('full_period_excess'):+.1%}、驗證期 {val.metrics.get('full_period_excess'):+.1%}"
                  f"（試驗 #{dev.trial_id}、#{val.trial_id}）")
        output.append((StockRule.model_validate(spec), reason))
    return output


class ForwardLock:
    """2026-10-22 (前向帳戶 194 個對帳不一致): one writer of the forward records and decisions at a time, across processes.
    On 10-21 23:31 a model refit restarted accounts (records and decisions archived, recorded again with the refit
    model) while another pipeline's forward step, holding the decisions it had read before, saved them back over the
    new ones — the records were the new model's, the decisions the old one's, and the next day did not follow on.
    ``record`` and ``refit`` hold this for their whole run; a lock older than ``stale`` seconds (a dead process) goes."""

    def __init__(self, folder: Path, stale: float = 3 * 3600) -> None:
        self._lock = Path(folder) / "forward.lock"
        self._stale = stale
        self._held = False

    def __enter__(self):
        import time

        self._lock.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                handle = os.open(self._lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(handle, f"{os.getpid()} {datetime.now(TAIPEI).isoformat(timespec='seconds')}".encode())
                os.close(handle)
                self._held = True
                return self
            except (FileExistsError, PermissionError):
                try:
                    if time.time() - self._lock.stat().st_mtime > self._stale:
                        self._lock.unlink()
                        continue
                except OSError:
                    pass
                time.sleep(1.0)

    def __exit__(self, *_exc) -> None:
        if self._held:
            try:
                self._lock.unlink()
            except OSError:
                pass
            self._held = False


class StockForwardTracker:
    def __init__(self, research_dir: str | Path, costs: CostModel | None = None, min_quotes: int = 100,
                 experiments: bool = True) -> None:
        self._experiments = experiments      # the forward-only experiments and the AI researcher's account
        self._base = Path(research_dir)
        self._history = self._base / "history"
        self._folder = self._base / "forward" / "stocks"
        self._costs = costs or broker_costs(BROKER)
        self._min_quotes = min_quotes   # fewer stocks quoted today means the day is not on disk yet

    @property
    def log_path(self) -> Path:
        return self._folder / "log.jsonl"

    @property
    def tracked_path(self) -> Path:
        return self._folder / "tracked.json"

    def tracked(self) -> list[dict[str, object]]:
        if not self.tracked_path.is_file():
            return []
        return json.loads(self.tracked_path.read_text(encoding="utf-8"))

    def sync(self, today: date) -> list[dict[str, object]]:
        """2026-10-17 (使用者：前向觀察的起點照現在的月份往前推兩個月): the accounts follow the month's rolling selection
        (research/rolling.py) — the rules that pass the gate on data up to the cutoff (the end of the month two months
        back), each from the day after the cutoff. A rule not in the month's selection stops (its records stay); one
        whose account started elsewhere starts again from the month's start (its records moved to an archive). Until
        the month's selection is graded nothing is added. Accounts that hold 0050 stop (NO_0050); the forward-only
        experiments (FORWARD_ONLY) start from the month's start too."""
        from quant_platform.research.blend import BlendRule, parse_spec
        from quant_platform.research.categories import NO_0050, uses_0050
        from quant_platform.research.rolling import cutoff_for, folder as rolling_folder

        cutoff = cutoff_for(today)
        start = (cutoff.fromordinal(cutoff.toordinal() + 1)).isoformat()
        items = self.tracked()
        changed = False
        for item in items:                          # 2026-10-09: accounts that hold 0050 stop (records kept)
            if not item.get("stopped") and item.get("kind") in ("daily", "blend") and uses_0050(item.get("rule") or {}):
                item.update(stopped=today.isoformat(), stop_reason=NO_0050)
                changed = True
        moment = datetime.now(TAIPEI).isoformat(timespec="seconds")
        experiments = {rule.rule_hash: (rule, reason) for rule, reason in (forward_only() if self._experiments else [])}
        # 2026-10-21 (使用者：不要跟我說前向觀察只能做一天): the LLM trader's pair start on the common start like every
        # account; added once its decisions reach back to the cutoff day (llm_trader.replay), so it never sits in cash
        fixed: dict[str, str] = {}
        llm_first = llm_trader_start(self._base)
        if llm_first is None or llm_first > cutoff.isoformat():
            for rule, _reason in (forward_only() if self._experiments else []):
                if any(name in rule.factors for name in ("llm_trader", "llm_control")):
                    experiments.pop(rule.rule_hash, None)
        month_path = rolling_folder(self._base) / f"{cutoff.isoformat()}.json"
        month = None
        if month_path.is_file():
            try:
                month = json.loads(month_path.read_text(encoding="utf-8"))
            except ValueError:
                month = None
        target: dict[str, tuple] = {}
        if month and month.get("graded"):
            for row in month["graded"]:
                # 2026-10-21 (REQUIREMENTS 選手池分級，使用者 2026-10-04：T1 與 T0 候選自動進入前向觀察): T1 too
                if not row.get("passed") and row.get("tier") != "T1":
                    continue
                try:
                    rule = parse_spec(row["spec"])          # 2026-10-18: a blend that passes too
                except (KeyError, ValueError):
                    continue
                target[rule.rule_hash] = (rule, f"每月滾動：只用 {cutoff.isoformat()} 以前的資料挑出（{row.get('tier')}）")
        target.update(experiments)
        if not target:
            if changed:
                self.tracked_path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
            return []
        restart: set[str] = set()
        by_hash: dict[str, dict] = {}
        for item in items:
            by_hash.setdefault(item["rule_hash"], item)
            live = not item.get("stopped")
            if item.get("kind") not in ("daily", "blend") or item["rule_hash"] == META_AI["rule_hash"]:
                continue
            if item["rule_hash"] not in target:
                if live and month and month.get("graded"):
                    item.update(stopped=today.isoformat(), stop_reason=f"不在 {cutoff.isoformat()} 的每月滾動名單")
                    changed = True
                continue
            own = fixed.get(item["rule_hash"]) or start
            if item["since"] != own or not live:
                restart.add(item["rule_hash"])
                item.setdefault("added", item["since"])
                item.update(since=own, reason=target[item["rule_hash"]][1], restarted={"at": moment, "month": cutoff.isoformat()})
                item.pop("stopped", None)
                item.pop("stop_reason", None)
                changed = True
        added = []
        for rule_hash, (rule, reason) in target.items():
            if rule_hash in by_hash:
                continue
            added.append({"rule_hash": rule_hash, "name": rule.name, "rule": rule.model_dump(mode="json"),
                          "kind": "blend" if isinstance(rule, BlendRule) else "daily", "plan": "seed", "initial": SEED_CAPITAL, "monthly": SEED_MONTHLY,
                          "since": fixed.get(rule_hash) or start, "added": today.isoformat(), "reason": reason,
                          "added_at": moment})
        if restart:
            self._archive_records(restart, moment)
            # 2026-10-27: the decisions too — an account stopped before the 10-21 refit kept its old model's decisions
            # under the same start and replayed them when the 2020-10 gate passed it again (the audit: 27 of 27 days)
            self._archive_decisions(restart, moment)
        if added or changed:
            self._folder.mkdir(parents=True, exist_ok=True)
            self.tracked_path.write_text(json.dumps(items + added, ensure_ascii=False, indent=1), encoding="utf-8")
        return added

    def _archive_decisions(self, hashes: set[str], moment: str) -> int:
        """Move these accounts' decisions to ``decisions-archive.json`` (kept, never deleted) and off the store, so the
        account is decided again from its start."""
        from quant_platform.research.decisions import DecisionStore

        store = DecisionStore(self._base)
        moved = store.drop({name for name in store._data if name.split("@")[0] in hashes})
        if moved:
            archive = store.path.with_name("decisions-archive.json")
            kept = json.loads(archive.read_text(encoding="utf-8")) if archive.is_file() else {}
            kept.update({f"{name}~{moment}": value for name, value in moved.items()})
            archive.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
        return len(moved)

    def _archive_records(self, hashes: set[str], moment: str) -> int:
        """Move these accounts' records to ``log-archive.jsonl`` (appended, never deleted) and off the log."""
        records = self.records()
        moved = [record for record in records if record["rule_hash"] in hashes]
        if not moved:
            return 0
        with open(self._folder / "log-archive.jsonl", "a", encoding="utf-8") as handle:
            for record in moved:
                handle.write(json.dumps({**record, "archived_at": moment}, ensure_ascii=False, default=str) + "\n")
        kept = [record for record in records if record["rule_hash"] not in hashes]
        partial = self.log_path.with_suffix(".partial")
        partial.write_text("".join(json.dumps(record, ensure_ascii=False, default=str) + "\n" for record in kept),
                           encoding="utf-8")
        os.replace(partial, self.log_path)
        marks = self.rebases()
        if any(key in marks for key in hashes):
            self.rebases_path.write_text(json.dumps({key: value for key, value in marks.items() if key not in hashes},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
        return len(moved)

    def archived(self) -> list[dict[str, object]]:
        """The records moved off the log when an account started again (``_archive_records``)."""
        path = self._folder / "log-archive.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def chained(self, today: date) -> dict[str, dict[str, object]]:
        """2026-10-19 (使用者選「串接滾動前向紀錄」): each rule's chained rolling forward record — see ``chain``.
        2026-10-23 (使用者：整個網頁在使用的速度很慢): kept until the records, the archive or a monthly list change —
        reading the 190 MB of records took 3.3 s of every pool and overview page."""
        from quant_platform.research.rolling import folder as rolling_folder

        lists_folder = rolling_folder(self._base)
        paths = [self.log_path, self._folder / "log-archive.jsonl",
                 *(sorted(lists_folder.glob("*.json")) if lists_folder.is_dir() else [])]
        key = (str(self._folder.resolve()), today.isoformat(),
               tuple((str(path), path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else 0 for path in paths))
        if _CHAINED.get("key") == key:
            return {rule: dict(value) for rule, value in _CHAINED["value"].items()}
        lists: dict[str, set[str]] = {}
        for path in sorted(lists_folder.glob("*.json")) if lists_folder.is_dir() else []:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            passed = {str(row.get("rule_hash")) for row in payload.get("graded") or [] if row.get("passed")}
            if passed and payload.get("cutoff"):
                lists[payload["cutoff"]] = passed
        found = chain(self.archived() + self.records(), lists, today)
        _CHAINED.update(key=key, value=found)
        return {rule: dict(value) for rule, value in found.items()}

    def records(self) -> list[dict[str, object]]:
        if not self.log_path.is_file():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    @property
    def rebases_path(self) -> Path:
        return self._folder / "rebases.json"

    def rebases(self) -> dict[str, dict[str, str]]:
        """rule hash -> {day: why}: records that start again from what the replay held that day (a known cause, not
        a reconciliation error); see ``rebase``."""
        if not self.rebases_path.is_file():
            return {}
        return json.loads(self.rebases_path.read_text(encoding="utf-8"))

    def rebase(self, reason: str) -> int:
        """2026-10-15: every record that does not follow from the day before (holdings or cash changed without a
        trade) is marked with ``reason`` — the replay rewrote a decision already taken (an engine or model change,
        data that came after the decision); from then on the decisions are kept (research/decisions.py). Returns
        how many records were marked."""
        marks = self.rebases()
        found = 0
        for (rule_hash, day) in continuity_breaks(self.records(), marks):
            marks.setdefault(rule_hash, {})[day] = reason
            found += 1
        if found:
            self._folder.mkdir(parents=True, exist_ok=True)
            self.rebases_path.write_text(json.dumps(marks, ensure_ascii=False, indent=1), encoding="utf-8")
        return found

    def restart(self, start: date, reason: str, now: datetime | None = None) -> dict[str, object]:
        """2026-10-17 (使用者：前向觀察一律從 10/01 開始，兩個交易日不能當觀察): every live account starts on ``start``
        again — its records are moved to an archive file (kept, not deleted), its start set to ``start`` (the day it
        joined kept as ``added``), its rebase marks archived too; the next ``record`` writes every session from
        ``start`` with the engine of the day. Stopped accounts keep their records."""
        stamp = (now or datetime.now(TAIPEI)).strftime("%Y%m%d-%H%M%S")
        items = self.tracked()
        live = {item["rule_hash"] for item in items if not item.get("stopped")}
        records = self.records()
        archive = self._folder / f"log-before-restart-{stamp}.jsonl"
        if self.log_path.is_file():
            archive.write_bytes(self.log_path.read_bytes())
        kept = [record for record in records if record["rule_hash"] not in live]
        partial = self.log_path.with_suffix(".partial")
        partial.write_text("".join(json.dumps(record, ensure_ascii=False, default=str) + "\n" for record in kept),
                           encoding="utf-8")
        os.replace(partial, self.log_path)
        for item in items:
            if item["rule_hash"] in live:
                item.setdefault("added", item["since"])
                item["since"] = start.isoformat()
                item["restarted"] = {"at": stamp, "reason": reason}
        self.tracked_path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        marks = self.rebases()
        if marks:
            (self._folder / f"rebases-before-restart-{stamp}.json").write_text(
                json.dumps(marks, ensure_ascii=False, indent=1), encoding="utf-8")
            self.rebases_path.write_text(json.dumps({key: value for key, value in marks.items() if key not in live},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
        return {"accounts": len(live), "archived_records": len(records) - len(kept), "archive": archive.name}

    def uses(self, item: dict) -> set[str]:
        """The factors (and risk overlays) an account's rule reads, a blend's sleeves included."""
        rule = item.get("rule") or {}
        rules = [sleeve.get("rule") or {} for sleeve in rule.get("sleeves") or []] if item.get("kind") == "blend" else [rule]
        found: set[str] = set()
        for one in rules:
            found |= set((one.get("factors") or {}).keys())
            if one.get("risk_veto") and one.get("risk_veto") != "none":
                found.add(str(one["risk_veto"]))
            if one.get("weighting") == "inverse_risk":           # the weights read the risk model too
                found.add("dl_risk")
            if one.get("vol_scale") == "risk_half":              # 2026-10-25: so does the model-timed half
                found.add("risk_half")
        return found

    def refit(self, factors: set[str], reason: str, now: datetime | None = None) -> list[str]:
        """2026-10-21 (使用者 2026-10-16：每月滾動，模型也要更新): the live accounts reading a model just retrained to the
        month's cutoff are recorded again from their start with it — their records moved to log-archive.jsonl and their
        decisions to decisions-archive.json (kept, not deleted). Returns their names."""
        items = self.tracked()
        hashes = {item["rule_hash"] for item in items if not item.get("stopped") and item.get("kind") in ("daily", "blend")
                  and self.uses(item) & factors}
        return self.restart_accounts(hashes, reason, now, field="refit")

    def restart_accounts(self, hashes: set[str], reason: str, now: datetime | None = None, field: str = "restarted"
                         ) -> list[str]:
        """These live accounts recorded again from their start: records to log-archive.jsonl, decisions to
        decisions-archive.json (kept), the reason on the account (``field``). Under the forward lock."""
        with ForwardLock(self._folder):
            return self._restart_accounts(hashes, reason, now, field)

    def _restart_accounts(self, hashes: set[str], reason: str, now: datetime | None, field: str) -> list[str]:
        moment = (now or datetime.now(TAIPEI)).isoformat(timespec="seconds")
        items = self.tracked()
        hit = [item for item in items if item["rule_hash"] in hashes and not item.get("stopped")]
        if not hit:
            return []
        hashes = {item["rule_hash"] for item in hit}
        self._archive_records(hashes, moment)
        self._archive_decisions(hashes, moment)
        for item in hit:
            item[field] = {"at": moment, "reason": reason}
        self.tracked_path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        return [item["name"] for item in hit]

    def stop(self, rule_hash: str, today: date, reason: str) -> bool:
        """Stop recording one tracked account (its records stay); False when it is not tracked or already stopped."""
        items = self.tracked()
        hit = [item for item in items if item["rule_hash"] == rule_hash and not item.get("stopped")]
        for item in hit:
            item.update(stopped=today.isoformat(), stop_reason=reason)
        if hit:
            self.tracked_path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        return bool(hit)

    def freeze(self, today: date) -> list[dict[str, object]]:
        """2026-10-15 (research/decisions.py): write down the decisions behind every daily rule's and blend's latest
        record, replayed with today's data; returns the accounts whose replayed holdings differ from that record."""
        return self.record(today, freeze=True)

    def record(self, today: date, now: datetime | None = None, freeze: bool = False) -> list[dict[str, object]]:
        """Under the forward lock (2026-10-22): ``_record``."""
        with ForwardLock(self._folder):
            return self._record(today, now, freeze)

    def _record(self, today: date, now: datetime | None = None, freeze: bool = False) -> list[dict[str, object]]:
        """Append the state of every tracked rule for today and for any earlier session since its
        start that has no record yet (the computer was off at 15:30, or the quotes came late): those
        are marked ``late``. A day whose quotes (stocks and 0050) are not on disk is left for later.
        2026-10-15: a daily rule's or blend's decisions on days already recorded are taken as they were made
        (research/decisions.py); the new days' decisions are kept with the records."""
        from quant_platform.research.decisions import DecisionStore
        from quant_platform.research.decisions import key as decision_key
        from quant_platform.research.decisions import rankings as kept_rankings

        if not freeze:
            self.sync(today)
        items = [item for item in self.tracked() if date.fromisoformat(item["since"]) <= today and not item.get("stopped")]
        earlier = self.records()
        done = {(item["date"], item["rule_hash"]) for item in earlier}
        if not items:
            return []
        # 2026-10-25 (使用者：最強的策略 9/7 到 10/5 都沒交易，怪怪的): the same years as every backtest
        # (daily.load) — loaded from the accounts' start only, cost-aware switching ("cost1") found no development years
        # to price a swap with and never swapped (698 accounts held their first picks since 9/1)
        from quant_platform.research.daily import RECENT_START

        first_year = min([RECENT_START.year] + [date.fromisoformat(item["since"]).year for item in items]) - WARMUP_YEARS
        data = load_stock_data(self._history, first_year, today.year)
        start_all = min(date.fromisoformat(item["since"]) for item in items)
        quoted = {day: 0 for day in data.sessions if start_all <= day <= today}
        for symbol, closes in data.closes.items():
            if symbol == BENCHMARK:
                continue
            for day in quoted:
                if day in closes:
                    quoted[day] += 1
        ready = [day for day, count in sorted(quoted.items())
                 if count >= self._min_quotes and day in data.closes.get(BENCHMARK, {})]
        todo = {item["rule_hash"]: [day for day in ready if day >= date.fromisoformat(item["since"])
                                    and (day.isoformat(), item["rule_hash"]) not in done] for item in items}
        latest = {record["rule_hash"]: record for record in earlier}            # the log is in order
        if freeze:                          # the day of each daily rule's or blend's latest record, nothing new
            todo = {item["rule_hash"]: [date.fromisoformat(latest[item["rule_hash"]]["date"])]
                    if item["rule_hash"] in latest and item.get("kind") in ("daily", "blend") else [] for item in items}
        store = DecisionStore(self._base)
        differences = []
        if not any(todo.values()):
            return []
        twse_data = data
        loaded: dict[str, list] = {}             # universe -> [data, panel, fingerprint, factor panel]
        names = _names(self._history, today.year)
        moment = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
        written = []
        for item in items:
            days = todo[item["rule_hash"]]
            if not days:
                continue
            # R6: a daily rule on listed plus TPEx stocks reads both; every other rule the TWSE data
            universe = (item.get("rule") or {}).get("universe", "twse") if item.get("kind") == "daily" else "twse"
            if universe not in loaded:
                universe_data = twse_data if universe == "twse" else load_stock_data(self._history, first_year, today.year,
                                                                                       universe=universe)
                loaded[universe] = [universe_data, Panel(universe_data),
                                    stock_fingerprint(self._history, first_year, today.year, universe=universe), None]
            data, panel, fingerprint, factor_panel = loaded[universe]
            start, last = date.fromisoformat(item["since"]), days[-1]
            ledger: list[dict[str, object]] = []
            snapshots: dict = {}
            plan = plan_of(item, list(data.sessions))
            if item.get("kind") in ("daily", "blend", "meta-ai"):
                from quant_platform.research.blend import BlendAccount, BlendRule
                from quant_platform.research.daily import (
                    DailyRule,
                    ChipStore,
                    FactorPanel,
                    Industries,
                    account_parking,
                    daily_rankings,
                    daily_weights,
                    exposure_schedule,
                    load_industries,
                    market_closes,
                    simulate_daily,
                )

                from quant_platform.research.model import models_root

                factor_panel = factor_panel or FactorPanel(
                    panel, Industries(load_industries(self._history)),
                    ChipStore(self._history, panel.sessions, panel.symbols, panel.close),
                    market_closes(self._history, panel.sessions), models_root(self._history))
                loaded[universe][3] = factor_panel
                if item.get("kind") == "meta-ai":
                    rule = SimpleNamespace(rule_hash=item["rule_hash"], name=item["name"])
                    run = self.meta_run(data, factor_panel, start, last, plan, ledger, snapshots)
                elif item.get("kind") == "blend":
                    rule = BlendRule.model_validate(item["rule"])
                    run = BlendAccount(data, factor_panel, rule, self._costs, start, last, store=store,
                                       name=decision_key(item["rule_hash"], start), through=last).run(
                        start, last, plan, ledger=ledger, snapshots=snapshots)
                else:
                    rule = DailyRule.model_validate(item["rule"])
                    ranks = kept_rankings(factor_panel, rule, start, last, store, decision_key(item["rule_hash"], start), last)
                    run = simulate_daily(data, rule, self._costs, start, last, ranks, plan, ledger=ledger,
                                         snapshots=snapshots, weights=daily_weights(factor_panel, rule, ranks),
                                         parked=account_parking(data, factor_panel, rule, self._costs),
                                         exposure=exposure_schedule(data, factor_panel, rule, self._costs))
                benchmark = simulate_daily(data, None, self._costs, start, last, plan=plan)
            else:
                rule = StockRule.model_validate(item["rule"])
                run = simulate(data, rule_variant(rule), self._costs, start, last, rankings(panel, rule, start, last),
                               plan, ledger=ledger, snapshots=snapshots)
                benchmark = simulate(data, None, self._costs, start, last, plan=plan)
            if freeze:
                _cash, units = snapshots[last]
                replayed_units = {symbol.split(".")[0]: round(count, 6) for symbol, count in units.items() if count > 1e-9}
                logged = {row["code"]: row["units"] for row in latest[item["rule_hash"]]["holdings"] if row["units"] > 1e-9}
                if replayed_units != logged:
                    differences.append({"rule_hash": item["rule_hash"], "name": item["name"], "date": last.isoformat(),
                                        "recorded": logged, "replayed": replayed_units})
                continue
            values, benchmark_values = dict(zip(run.days, run.values)), dict(zip(benchmark.days, benchmark.values))
            replayed = _replay_check(earlier, rule.rule_hash, values)
            for day in days:
                written.append(self._day_record(rule, start, day, data, names, ledger, snapshots, values[day],
                                                benchmark_values[day], run, replayed, fingerprint, moment))
        if written:
            self._folder.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                for record in written:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        if written or freeze:
            store.save()
        return differences if freeze else written

    def _day_record(self, rule, start, day, data, names, ledger, snapshots, value, benchmark_value, run, replayed,
                    fingerprint, moment) -> dict[str, object]:
        cash, units = snapshots[day]
        holdings = []
        for symbol, count in units.items():
            close = data.last_close(symbol, day) or 0.0
            code = symbol.split(".")[0]
            holdings.append({"code": code, "name": names.get(code, ""), "units": round(count, 6),
                             "close": close, "value": round(count * close, 2)})
        holdings.sort(key=lambda row: -row["value"])
        contributed = sum(amount for when, amount in run.contributions if when <= day)
        trades = [entry for entry in ledger if entry["day"] <= day and entry["side"] in ("BUY", "SELL")]
        return {
            "date": day.isoformat(), "rule_hash": rule.rule_hash, "name": rule.name, "since": start.isoformat(),
            "value": round(value, 2), "cash": round(cash, 2), "contributed": round(contributed, 2),
            "benchmark_value": round(benchmark_value, 2),
            "excess": round((value - benchmark_value) / contributed, 6) if contributed else None,
            "holdings": holdings,
            "trades_today": [_trade(entry, names) for entry in trades if entry["day"] == day],
            "adjustments_today": [{"code": str(entry["symbol"]).split(".")[0], "factor": entry["factor"]}
                                  for entry in ledger if entry["day"] == day and entry["side"] == "ADJUST"],
            # daily engine 1.4.0: dividends net of the health-insurance premium, owned from the ex-date — the
            # cash shown includes what is declared and not yet paid
            "income_today": round(sum(float(entry.get("net") or 0.0) for entry in ledger
                                      if entry["day"] == day and entry["side"] == "DIVIDEND")
                                  + sum(float(entry.get("cash") or 0.0) for entry in ledger      # 1.5.0: money back
                                        if entry["day"] == day and entry["side"] == "REFUND"), 2),
            "trades_total": len(trades), "fees_total": sum(int(entry["fee"]) for entry in trades),
            "taxes_total": sum(int(entry["tax"]) for entry in trades),
            "replay_check": replayed, "costs": BROKER, "data_fingerprint": fingerprint, "engine": ENGINE_VERSION,
            "daily_engine": _daily_engine(),
            "recorded_at": moment.isoformat(timespec="seconds"), "late": moment.date() != day,
        }

    def summary(self) -> list[dict[str, object]]:
        """Per tracked rule: the latest record, days recorded, the latest trades and any problem. 2026-10-20: kept until
        the records, the accounts, the rebase marks or the trial registry change (the research pages ask up to three
        times a view; 264 accounts, 9,232 records took about 1.2 s each)."""
        paths = (self.log_path, self.tracked_path, self.rebases_path, Path(self._base) / "trials.jsonl")
        key = (str(self._folder.resolve()), str(self._costs),
               tuple((path.stat().st_mtime_ns, path.stat().st_size) if path.is_file() else 0 for path in paths))
        if _SUMMARY.get("key") != key:
            _SUMMARY.update(key=key, rows=self._summary())
        return [dict(row) for row in _SUMMARY["rows"]]

    def _summary(self) -> list[dict[str, object]]:
        from quant_platform.research.holding import named

        records = self.records()
        marks = self.rebases()
        problems = reconcile(records, marks)
        finals = final_validation(self._base)
        rows = []
        by_rule: dict[str, list[dict[str, object]]] = {}
        for record in records:
            by_rule.setdefault(record["rule_hash"], []).append(record)
        for item in self.tracked():
            mine = by_rule.get(item["rule_hash"], [])
            latest = mine[-1] if mine else None
            traded = next((record for record in reversed(mine) if record["trades_today"]), None)
            based = marks.get(item["rule_hash"]) or {}
            mismatches = sum(len((record.get("replay_check") or {}).get("mismatches") or []) for record in mine
                             if record["date"] not in based)     # a marked day's replay differences have their reason
            # 2026-10-20 (使用者：未實現損益不叫賺錢): the sales' results and the cash if all were sold today, on the list
            by_day = {record["date"]: record for record in mine}
            _trades, results = explain_trades([by_day[day] for day in sorted(by_day)], {},
                                              float((item.get("rule") or {}).get("trim_above") or 0.0))
            cashed = sell_all(latest, self._costs) if latest else None
            rows.append({
                "rule_hash": item["rule_hash"], "since": item["since"], "reason": item.get("reason"),
                # 2026-10-21 (AGENTS：名稱寫出持有方式): a daily rule's name states the holding style it has
                "name": named(item["rule"]) if item.get("kind") == "daily" and item.get("rule") else item["name"],
                "plan": item.get("plan", "dca"), "amount": item.get("amount"),
                "sessions": len(mine), "first": mine[0]["date"] if mine else None,
                "date": latest["date"] if latest else None,
                "contributed": latest["contributed"] if latest else 0.0, "value": latest["value"] if latest else 0.0,
                "benchmark_value": latest["benchmark_value"] if latest else 0.0,
                "excess": latest["excess"] if latest else None,
                "holdings": latest["holdings"] if latest else [], "cash": latest["cash"] if latest else 0.0,
                "costs": (latest["fees_total"] + latest["taxes_total"]) if latest else 0,
                "trades_total": latest["trades_total"] if latest else 0,
                "last_trade_day": traded["date"] if traded else None,
                "last_trades": traded["trades_today"] if traded else [],
                "late": sum(1 for record in mine if record.get("late")),
                "problems": problems.get(item["rule_hash"], []), "replay_mismatches": mismatches,
                "final": finals.get(item["rule_hash"]), "stopped": item.get("stopped"), "stop_reason": item.get("stop_reason"),
                "rebased": sorted((marks.get(item["rule_hash"]) or {}).items()),
                "realized": results["realized"], "wins": results["wins"], "losses": results["losses"],
                "unrealized": results["unrealized"], "sell_all": cashed,
            })
        return rows

    @property
    def meta_path(self) -> Path:
        return self._folder / "meta_ai.jsonl"

    def meta_decisions(self) -> list[dict[str, object]]:
        if not self.meta_path.is_file():
            return []
        return [json.loads(line) for line in self.meta_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def decide_meta(self, sessions: list[date], start: date, last: date, choose=None) -> list[dict[str, object]]:
        """Log a decision for every review day up to ``last`` that has none (made with what is known now and
        never recomputed: a late run decides with today's knowledge, and says so)."""
        decisions = self.meta_decisions()
        done = {item["date"] for item in decisions}
        new = []
        for day in review_days(sessions, start, last):
            if day.isoformat() in done:
                continue
            chosen = (choose or best_ai_rule)(self._base)
            new.append({"date": day.isoformat(), "decided_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
                        "rule_hash": chosen["rule_hash"] if chosen else None, "name": chosen["name"] if chosen else "0050",
                        "tier": chosen["tier"] if chosen else None, "spec": chosen["spec"] if chosen else None,
                        "trial_id": chosen["trial_id"] if chosen else None})
        if new:
            self._folder.mkdir(parents=True, exist_ok=True)
            with self.meta_path.open("a", encoding="utf-8") as handle:
                for item in new:
                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        return decisions + new

    def meta_run(self, data, factor_panel, start: date, last: date, plan, ledger: list, snapshots: dict, choose=None):
        """The researcher-as-a-strategy account: one segment per decision, the holdings sold at the switch
        day's close (fees and tax) and the proceeds carried into the next rule bought the same close."""
        from quant_platform.research.daily import (
            DailyRule,
            account_parking,
            daily_rankings,
            daily_weights,
            exposure_schedule,
            simulate_daily,
        )
        from quant_platform.research.legacy_challenger import RunResult, _tax_kind, _tick
        from quant_platform.research.costs import fill_price

        decisions = [item for item in self.decide_meta(data.sessions, start, last, choose)
                     if start.isoformat() <= item["date"] <= last.isoformat()]
        days_all = [day for day in data.sessions if start <= day <= last]
        bounds = [date.fromisoformat(item["date"]) for item in decisions] + [None]
        combined = RunResult("meta-ai", [], [], [], [])
        carry = None
        for number, decision in enumerate(decisions):
            first = bounds[number]
            following = bounds[number + 1]
            end = days_all[days_all.index(following) - 1] if following else last
            if carry is None:
                segment_plan = plan
            else:
                segment_plan = SeedPlan(initial=carry, monthly_amount=plan.monthly_amount, day_of_month=plan.day_of_month,
                                        calendar=getattr(plan, "calendar", None))   # a segment from mid-month: no second payment
            part_ledger, part_book = [], {}
            if decision.get("spec"):
                rule = DailyRule.model_validate(decision["spec"])
                ranks = daily_rankings(factor_panel, rule, first, end)
                part = simulate_daily(data, rule, self._costs, first, end, ranks, segment_plan, ledger=part_ledger,
                                      snapshots=part_book, weights=daily_weights(factor_panel, rule, ranks),
                                      parked=account_parking(data, factor_panel, rule, self._costs),
                                      exposure=exposure_schedule(data, factor_panel, rule, self._costs))
            else:
                part = simulate_daily(data, None, self._costs, first, end, plan=segment_plan, ledger=part_ledger,
                                      snapshots=part_book)
            if carry is not None:                   # the carried money is not new money
                part.flows[0] -= carry
                part.contributions = [(when, amount - (carry if when == first else 0.0)) for when, amount in part.contributions]
                part.contributions = [(when, amount) for when, amount in part.contributions if amount > 1e-9]
            ledger.extend(part_ledger)
            snapshots.update(part_book)
            combined.days += part.days
            combined.values += part.values
            combined.flows += part.flows
            combined.contributions += part.contributions
            combined.trades += part.trades
            combined.fees += part.fees
            combined.taxes += part.taxes
            combined.bought += part.bought
            combined.sold += part.sold
            if following:                           # sell everything at the next decision day's close
                cash, units = part_book[end]
                sells = []
                for symbol, count in units.items():
                    count = count * data.factors.get(symbol, {}).get(following, 1.0)
                    close = data.closes.get(symbol, {}).get(following) or data.last_close(symbol, following) or 0.0
                    price = fill_price(close, "SELL", self._costs.slippage_bps, _tick(symbol))
                    amount = count * price
                    fee, tax = self._costs.fee(amount), self._costs.tax(amount, _tax_kind(symbol), "SELL")
                    cash += amount - fee - tax
                    combined.fees += fee
                    combined.taxes += tax
                    combined.trades += 1
                    combined.sold += amount
                    sells.append({"day": following, "symbol": symbol, "side": "SELL", "shares": count, "price": price,
                                  "fee": fee, "tax": tax})
                ledger.extend(sells)
                carry = cash
        return combined

    def audit_decisions(self, sample: int = 0, seed: int = 0) -> Path:
        """2026-10-25 (使用者：你所有策略確定都是合理的嗎；#6490 從 9/7 到 10/5 都沒交易): every live account's decisions
        ranked again from its start the way a backtest ranks them (daily.load: the years from 2013), against the
        decisions the account kept — a difference means the account did not trade as its rule says. ``sample``: that
        many accounts picked at random (``seed``) instead of all. Written to forward/stocks/audit-<time>.json."""
        import random

        from quant_platform.research.after_close import revisions
        from quant_platform.research.blend import BlendRule
        from quant_platform.research.daily import DailyRule, daily_rankings, load
        from quant_platform.research.decisions import DecisionStore, key

        _data, fp = load(self._history)
        store = DecisionStore(self._base)
        items = [item for item in self.tracked() if not item.get("stopped") and item.get("kind") in ("daily", "blend")]
        if sample and sample < len(items):
            items = random.Random(seed).sample(items, sample)
        rows = []
        for item in items:
            start = date.fromisoformat(item["since"])
            if item["kind"] == "blend":
                parts = [(key(item["rule_hash"], start, number), sleeve.rule)
                         for number, sleeve in enumerate(BlendRule.model_validate(item["rule"]).sleeves)]
            else:
                parts = [(key(item["rule_hash"], start), DailyRule.model_validate(item["rule"]))]
            days = differed = 0
            first = None
            for name, rule in parts:
                fixed = store.fixed(name)
                if not fixed or getattr(rule, "universe", "twse") != "twse":
                    continue
                through = fixed["through"]
                again = daily_rankings(fp, rule, start, through if isinstance(through, date) else date.fromisoformat(str(through)))
                for day, symbols in sorted(fixed["days"].items()):
                    days += 1
                    found = again.get(day if isinstance(day, date) else date.fromisoformat(str(day)), [])
                    if set(found) != set(symbols):
                        differed += 1
                        if first is None:
                            first = {"day": str(day), "kept": sorted(symbols), "again": sorted(found)}
                            # 2026-10-27: a day the after-close orders decided at 13:50 — the stocks that moved, as
                            # the orders' table had them and as the day's data has them
                            moved = set(found) ^ set(symbols)
                            changed = revisions(self._base, self._history, date.fromisoformat(str(day)), moved)
                            if changed:
                                first["table"] = changed
            rows.append({"rule_hash": item["rule_hash"], "name": item["name"], "days": days, "differed": differed,
                         "first": first})
        moment = datetime.now(TAIPEI)
        report = {"at": moment.isoformat(timespec="seconds"), "accounts": len(rows), "sample": sample,
                  "checked_days": sum(row["days"] for row in rows),
                  "differing_accounts": sum(1 for row in rows if row["differed"]),
                  "rows": sorted(rows, key=lambda row: -row["differed"])}
        path = self._folder / f"audit-{moment:%Y%m%d-%H%M%S}.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def explain_backfill(self) -> dict[str, int]:
        """2026-10-19 (使用者：每一筆買賣要有原因): the reasons for the decisions kept before reasons were written down.
        Each live account's sessions are ranked again from its start, as when they were decided, with the reasons
        traced; a day's reasons are kept only when the list ranked again is the list that was kept — otherwise the page
        says only that the stock came in or left."""
        from quant_platform.research.blend import BlendRule
        from quant_platform.research.daily import DailyRule, daily_rankings, load
        from quant_platform.research.decisions import DecisionStore, key

        _data, fp = load(self._history)
        store = DecisionStore(self._base)
        counts = {"accounts": 0, "days": 0, "explained": 0, "differed": 0}
        for item in self.tracked():
            if item.get("stopped") or item.get("kind") not in ("daily", "blend"):
                continue
            start = date.fromisoformat(item["since"])
            if item["kind"] == "blend":
                parts = [(key(item["rule_hash"], start, number), sleeve.rule)
                         for number, sleeve in enumerate(BlendRule.model_validate(item["rule"]).sleeves)]
            else:
                parts = [(key(item["rule_hash"], start), DailyRule.model_validate(item["rule"]))]
            for name, rule in parts:
                fixed = store.fixed(name)
                if not fixed or getattr(rule, "universe", "twse") != "twse":
                    continue
                trace: dict = {}
                again = daily_rankings(fp, rule, start, fixed["through"], trace=trace)
                traced, found = trace.get("why") or {}, {}
                for day, symbols in fixed["days"].items():
                    if day not in traced:
                        continue
                    counts["days"] += 1
                    if set(again.get(day, [])) == set(symbols):
                        found[day] = traced[day]
                    else:
                        counts["differed"] += 1
                if found:
                    store.add_why(name, found)
                    counts["explained"] += len(found)
                counts["accounts"] += 1
        store.save()
        return counts

    def detail(self, rule_hash: str) -> dict[str, object] | None:
        """One tracked rule's forward record (使用者 2026-10-09：要看它買了哪些股票、做了哪些操作): every
        recorded day's value against 0050, today's holdings with their weights, and every trade and
        ex-rights adjustment since it started, newest first."""
        item = next((entry for entry in self.tracked() if entry["rule_hash"] == rule_hash), None)
        if item is None:
            return None
        by_day: dict[str, dict] = {}
        for record in self.records():
            if record["rule_hash"] == rule_hash:
                by_day[record["date"]] = record          # a re-recorded day keeps its latest record
        days = [by_day[day] for day in sorted(by_day)]
        latest = days[-1] if days else None
        holdings = []
        if latest:
            total = latest["value"] or 1.0
            holdings = sorted(({**row, "weight": row["value"] / total} for row in latest["holdings"]),
                              key=lambda row: -row["value"])
        # 2026-10-19 (使用者：每一筆買賣賺多少賠多少、要有原因): average-cost results and the decisions' reasons
        from quant_platform.research.decisions import DecisionStore, key as decision_key

        store, start = DecisionStore(self._base), date.fromisoformat(item["since"])
        sleeves = len((item.get("rule") or {}).get("sleeves") or []) if item.get("kind") == "blend" else 0
        why: dict[str, dict[str, str]] = {}
        for name in ([decision_key(rule_hash, start, number) for number in range(sleeves)] if sleeves
                     else [decision_key(rule_hash, start)]):
            for day, notes in store.why(name).items():
                why.setdefault(day, {}).update({code: reworded(text) for code, text in notes.items()})
        if "llm_trader" in ((item.get("rule") or {}).get("factors") or {}):   # 2026-10-22: the model's own words
            from quant_platform.research.llm_trader import trade_reasons

            for day, notes in trade_reasons(self._base, days).items():
                why.setdefault(day, {}).update(notes)
        trades, results = explain_trades(days, why, float((item.get("rule") or {}).get("trim_above") or 0.0))
        adjustments = [{**entry, "date": record["date"]} for record in reversed(days)
                       for entry in record.get("adjustments_today") or []]
        series = [{"date": record["date"], "value": record["value"], "benchmark": record["benchmark_value"],
                   "contributed": record["contributed"]} for record in days]
        marks = self.rebases()
        problems = reconcile(days, marks).get(rule_hash, [])
        decisions = self.meta_decisions() if item.get("kind") == "meta-ai" else []
        return {"item": item, "latest": latest, "holdings": holdings, "trades": trades, "results": results,
                "sell_all": sell_all(latest, self._costs) if latest else None,
                "adjustments": adjustments,
                "decisions": list(reversed(decisions)), "rebased": sorted((marks.get(rule_hash) or {}).items()),
                "series": series, "sessions": len(days), "problems": problems,
                "buys": sum(1 for trade in trades if trade["side"] == "BUY"),
                "sells": sum(1 for trade in trades if trade["side"] == "SELL")}


def explain_trades(days: list[dict], why: dict[str, dict[str, str]], trim_above: float = 0.0
                   ) -> tuple[list[dict], dict[str, object]]:
    """2026-10-19 (使用者：前向觀察要看到每一筆買賣賺多少賠多少，還要有買賣的原因): every trade of one account, newest
    first, with what it made and why.

    Average cost per stock, fees and taxes included: a buy adds what it cost; a sale takes the cost of the shares it
    sells (its share of the shares held before it) and its proceeds less fee and tax minus that cost is what it made.
    Shares changed by ex-rights or a capital reduction keep the cost. Why: the day's decision (research/decisions.py)
    for a stock coming in or leaving; a buy of a stock already held tops it up to its target (the month's money, cash
    from the day's sales, or other cash such as dividends); a partial sale trims it. Each buy also shows the stock's
    position now (value against its cost)."""
    from collections import defaultdict

    units: dict[str, float] = defaultdict(float)
    basis: dict[str, float] = defaultdict(float)
    episode: dict[str, int] = defaultdict(int)    # a stock's holding from a first buy until it is all sold
    closed: dict[tuple[str, int], str] = {}       # (stock, episode) -> the day it was all sold
    rows: list[dict] = []
    realized, wins, losses, paid_before = 0.0, 0, 0, None
    for record in days:
        day = record["date"]
        notes = {str(symbol).split(".")[0]: text for symbol, text in (why.get(day) or {}).items()}
        paid_today = paid_before is not None and float(record["contributed"]) > paid_before + 0.5
        sold_today = any(trade["side"] == "SELL" for trade in record["trades_today"])
        for trade in record["trades_today"]:
            code, shares, price = trade["code"], float(trade["shares"]), float(trade["price"])
            costs = float(trade.get("fee") or 0) + float(trade.get("tax") or 0)
            row = {**trade, "date": day, "amount": shares * price}
            if trade["side"] == "BUY":
                fresh = units[code] <= 1e-9
                row["why"] = notes.get(code) or trade.get("engine_why") or (
                    "新進名單" if fresh else "每月 5 日投入的錢，補到目標比重" if paid_today
                    else "賣出後的現金，補到目標比重" if sold_today else "帳上現金（股利等），補到目標比重")
                basis[code] += shares * price + costs
                units[code] += shares
                row["episode"] = episode[code]
            else:
                before = units[code]
                cost = basis[code] * (min(1.0, shares / before) if before > 1e-9 else 1.0)
                made = shares * price - costs - cost
                full = shares >= before - 1e-6
                row.update(cost=cost, pnl=made, pnl_pct=made / cost if cost else None)
                # 2026-10-25: the engine's reason first for a partial sale (it was guessed from the rule before)
                row["why"] = (trade.get("engine_why") or notes.get(code) or "離開名單") if not full else (
                    notes.get(code) or trade.get("engine_why") or "離開名單")
                if not full and not trade.get("engine_why"):
                    row["why"] = notes.get(code) or (f"部分賣出（舊紀錄沒有寫原因；這個策略設定漲到目標比重 {trim_above:g} 倍以上賣回）"
                                                     if trim_above else "部分賣出（舊紀錄沒有寫原因）")
                basis[code] -= cost
                units[code] = max(0.0, before - shares)
                if full:
                    closed[(code, episode[code])] = day
                    episode[code] += 1
                realized += made
                wins += made > 0
                losses += made <= 0
            rows.append(row)
        held = {row["code"]: float(row["units"]) for row in record["holdings"]}
        for code in list(units):              # ex-rights and reductions change the shares, not what they cost
            if units[code] > 1e-9 and held.get(code, 0.0) <= 1e-9 and (code, episode[code]) not in closed:
                closed[(code, episode[code])] = day          # gone without a sale (written off)
                episode[code] += 1
            units[code] = held.get(code, 0.0)
            if units[code] <= 1e-9:
                basis[code] = 0.0
        paid_before = float(record["contributed"])
    positions = {}
    if days:
        for row in days[-1]["holdings"]:
            cost = basis.get(row["code"]) or 0.0
            if cost > 0:
                positions[row["code"]] = {"value": row["value"], "cost": cost, "pnl": row["value"] - cost,
                                          "pnl_pct": (row["value"] - cost) / cost}
    for row in rows:
        if row["side"] == "BUY":
            ended = closed.get((row["code"], row.pop("episode")))
            row["now"] = None if ended else positions.get(row["code"])
            row["sold_on"] = ended
    summary = {"realized": realized, "wins": wins, "losses": losses, "sells": wins + losses,
               "unrealized": sum(item["pnl"] for item in positions.values()), "positions": positions}
    by_day: dict[str, list[dict]] = {}
    for row in rows:
        by_day.setdefault(row["date"], []).append(row)
    # newest day first, a day's trades in the order they were made (as the page listed them before)
    return [row for day in sorted(by_day, reverse=True) for row in by_day[day]], summary


def sell_all(record: dict, costs) -> dict[str, float]:
    """2026-10-20 (使用者：未實現損益不叫賺錢): what selling every holding at the latest close would put in the account
    after the fee, the tax and the slippage — the cash an owner would hold — and the same for the 0050 account."""
    from quant_platform.research.legacy_challenger import _tax_kind

    back, paid = float(record["cash"]), 0.0
    for row in record["holdings"]:
        units, close = float(row["units"]), float(row["close"])
        if units <= 0 or close <= 0:
            continue
        amount = units * close * (1 - costs.slippage_bps / 10_000)
        fee, tax = costs.fee(amount), costs.tax(amount, _tax_kind(row["code"]), "SELL")
        back += amount - fee - tax
        paid += fee + tax + units * close - amount
    bench = float(record["benchmark_value"]) * (1 - costs.slippage_bps / 10_000)
    bench_back = bench - costs.fee(bench) - costs.tax(bench, "stock_etf", "SELL")
    contributed = float(record["contributed"])
    return {"cash_back": back, "costs": paid, "gain": back - contributed, "bench_back": bench_back,
            "bench_gain": bench_back - contributed, "ahead": back - bench_back}


def trade_chart(trades: list[dict], results: dict | None, width: int = 640, height: int = 220) -> dict | None:
    """2026-10-20 (使用者：我要有每一筆買賣的資產增加累積圖): every sale in order — a bar for what it made (up) or lost
    (down) and a line of what the sales have made so far; a last dashed step adds what the stocks still held are up or
    down on paper. None before the first sale."""
    sales = [trade for trade in reversed(trades) if trade["side"] == "SELL" and trade.get("pnl") is not None]
    if not sales:
        return None
    running, totals = 0.0, []
    for trade in sales:
        running += float(trade["pnl"])
        totals.append(running)
    unrealized = float((results or {}).get("unrealized") or 0.0)
    final = running + unrealized
    low = min(0.0, final, *totals, *(float(trade["pnl"]) for trade in sales))
    high = max(0.0, final, *totals, *(float(trade["pnl"]) for trade in sales))
    span = (high - low) or 1.0
    steps = len(sales) + 1                                  # one more place for the paper result
    pad = 8

    def y(value: float) -> float:
        return round(pad + (high - value) / span * (height - 2 * pad), 1)

    def x(index: int) -> float:
        return round((index + 0.5) * width / steps, 1)
    bar = max(2.0, min(24.0, width / steps * 0.6))
    bars = [{"x": round(x(index) - bar / 2, 1), "y": min(y(float(trade["pnl"])), y(0.0)), "width": round(bar, 1),
             "height": max(1.0, round(abs(y(float(trade["pnl"])) - y(0.0)), 1)),
             "up": float(trade["pnl"]) >= 0, "title": f"{trade['date']} 賣 {trade['code']} {trade.get('name', '')}："
             f"{'賺' if float(trade['pnl']) >= 0 else '賠'} {abs(float(trade['pnl'])):,.0f}，累計 {total:+,.0f}"}
            for index, (trade, total) in enumerate(zip(sales, totals, strict=True))]
    line = " ".join(f"{x(index)},{y(total)}" for index, total in enumerate(totals))
    return {"width": width, "height": height, "bars": bars, "line": line, "zero": y(0.0),
            "paper": f"{x(len(totals) - 1)},{y(totals[-1])} {x(len(totals))},{y(final)}", "paper_y": y(final),
            "paper_x": x(len(totals)), "realized": running, "unrealized": unrealized, "final": final,
            "first": sales[0]["date"], "last": sales[-1]["date"], "sales": len(sales), "high": high, "low": low}


def final_validation(research_dir: str | Path) -> dict[str, dict[str, object]]:
    """Stock rules' final-validation (holdout) result: passed, the excess over 0050 and the reasons."""
    from quant_platform.research.pool import _best_records, _gate

    path = Path(research_dir) / "trials.jsonl"
    if not path.is_file():
        return {}
    records = [record for record in TrialRegistry(path).records() if record.data_fingerprint.startswith("stocks:")]
    output = {}
    holdouts = {**_best_records(records, "holdout", "ContributionPlan"), **_best_records(records, "holdout", "LumpSumPlan"),
                **_best_records(records, "holdout", "SeedPlan")}
    for spec_hash, record in holdouts.items():
        reasons = _gate(record.metrics)
        output[spec_hash] = {"passed": not reasons, "excess": record.metrics.get("full_period_excess"),
                             "xirr": record.metrics.get("xirr"), "benchmark_xirr": record.metrics.get("benchmark_xirr"),
                             "reasons": reasons, "trial_id": record.trial_id}
    return output


def _trade(entry: dict[str, object], names: dict[str, str]) -> dict[str, object]:
    code = str(entry["symbol"]).split(".")[0]
    found = {"code": code, "name": names.get(code, ""), "side": entry["side"], "shares": round(float(entry["shares"]), 6),
             "price": entry["price"], "fee": entry["fee"], "tax": entry["tax"]}
    if entry.get("why") or entry.get("reason"):       # 2026-10-25: the engine's own reason for the order
        found["engine_why"] = entry.get("why") or entry.get("reason")
    return found


def _names(history: Path, year: int) -> dict[str, str]:
    import pyarrow.parquet as pq

    names: dict[str, str] = {}
    for exchange in ("tpex", "twse"):              # a stock that moved to TWSE keeps its TWSE name
        path = history / "stocks" / exchange / f"{year}.parquet"
        if path.is_file():
            table = pq.read_table(path, columns=["code", "name"])
            names.update(zip(table["code"].to_pylist(), table["name"].to_pylist()))
    return names


def _daily_engine() -> str:
    from quant_platform.research.daily import ENGINE_VERSION as DAILY_ENGINE

    return DAILY_ENGINE


def _replay_check(earlier: list[dict[str, object]], rule_hash: str, replayed: dict[date, float]) -> dict[str, object]:
    """Days written before whose value the replay no longer reproduces (a data revision). Only records of the
    same engine version count: a new engine (1.3.0, 2026-10-13: the market's price limits) changes the replay
    by design, and those days keep what was recorded then."""
    from quant_platform.research.daily import ENGINE_VERSION as DAILY_ENGINE

    mine = [record for record in earlier if record["rule_hash"] == rule_hash
            and record.get("daily_engine", "daily-1.2.0") == DAILY_ENGINE]
    mismatches = []
    for record in mine:
        value = replayed.get(date.fromisoformat(record["date"]))
        if value is not None and abs(value - record["value"]) > VALUE_TOLERANCE:
            mismatches.append({"date": record["date"], "recorded": record["value"], "replayed": round(value, 2)})
    return {"days": len(mine), "mismatches": mismatches}


CHAIN_START = "2026-10"     # the first month with a rolling list (cutoff 2026-08-31)


def chain(records: list[dict], lists: dict[str, set[str]], today: date) -> dict[str, dict[str, object]]:
    """2026-10-19 (使用者：評估專案可能失敗的原因並排除；選「串接滾動前向紀錄」): the record a rule earns while it is on the
    monthly lists. Each month M is judged by M's list — the rules passing with data up to cutoff_for(M), the end of the
    month two months back — the list an owner would use that month. M's sessions come from the rule's forward account
    for that list (started the day after the cutoff, so a month before M: M's first session has the day before it);
    the unit returns (net of each day's money) are chained over the latest run of consecutive months up to ``today``'s
    month in which the rule was on the list; leaving a list starts it again. The same for 0050 from the same accounts.
    2026-10-23 (使用者：叫你要浮動的；12/29 距離現在兩個月嗎): the run's first month counts its list's whole forward
    account — every session from the day after the cutoff (the two months the cutoff leaves for the forward test, its
    first session against the money put in), not only that month's own sessions.

    ``records``: forward records, live and archived (each with ``since``); ``lists``: cutoff (ISO) → rule hashes that
    passed. Returns rule hash → sessions, months, start, the chained returns and ``excess`` (rule's minus 0050's)."""
    from quant_platform.research.rolling import cutoff_for

    months, month = [], CHAIN_START
    while month <= f"{today:%Y-%m}":
        months.append(month)
        year, number = int(month[:4]), int(month[5:])
        month = f"{year + number // 12}-{number % 12 + 1:02d}"
    by_account: dict[tuple[str, str], dict[str, dict]] = {}
    for record in records:
        by_account.setdefault((record["rule_hash"], str(record.get("since"))), {})[record["date"]] = record
    output: dict[str, dict[str, object]] = {}
    hashes = {key for passed in lists.values() for key in passed}
    for rule_hash in hashes:
        run: list[str] = []
        for month in months:
            cutoff = cutoff_for(date(int(month[:4]), int(month[5:]), 1))
            if rule_hash in lists.get(cutoff.isoformat(), set()):
                run.append(month)
            else:
                run = []
        if not run:
            continue
        rule_units, base_units, sessions = 1.0, 1.0, 0
        for position, month in enumerate(run):
            cutoff = cutoff_for(date(int(month[:4]), int(month[5:]), 1))
            since = (cutoff + timedelta(days=1)).isoformat()
            days = dict(by_account.get((rule_hash, since), {}))
            ordered = sorted(days)
            if not position and ordered:             # the account's first session, against the money put in
                start = float(days[ordered[0]]["contributed"])
                days[""] = {"value": start, "benchmark_value": start, "contributed": start}
                ordered = ["", *ordered]
            for previous, day in zip(ordered, ordered[1:]):
                if day[:7] > month or (position and not day.startswith(month)):
                    continue
                before, now = days[previous], days[day]
                flow = float(now["contributed"]) - float(before["contributed"])
                if before["value"] and before["benchmark_value"]:
                    rule_units *= (float(now["value"]) - flow) / float(before["value"])
                    base_units *= (float(now["benchmark_value"]) - flow) / float(before["benchmark_value"])
                    sessions += 1
        output[rule_hash] = {"sessions": sessions, "months": run, "start": run[0],
                             "return": round(rule_units - 1, 6), "benchmark_return": round(base_units - 1, 6),
                             "excess": round(rule_units - base_units, 6)}
    return output


def reconcile(records: list[dict[str, object]], rebased: dict[str, dict[str, str]] | None = None) -> dict[str, list[str]]:
    """Per rule: every record's value equals cash plus its holdings at the closes, and every record
    follows from the one before through its logged adjustments, trades and new money — except a record marked
    in ``rebased`` (StockForwardTracker.rebase), which starts again from what it holds."""
    problems: dict[str, list[str]] = {}
    previous: dict[str, dict[str, object]] = {}
    for record in records:
        rule_hash, day = record["rule_hash"], record["date"]
        issues = problems.setdefault(rule_hash, [])
        held = sum(item["units"] * item["close"] for item in record["holdings"])
        if abs(record["cash"] + held - record["value"]) > VALUE_TOLERANCE + 0.01 * len(record["holdings"]):
            issues.append(f"{day} 資產 {record['value']:,.0f} ≠ 現金＋持股 {record['cash'] + held:,.0f}")
        before = previous.get(rule_hash)
        previous[rule_hash] = record
        if before is None or day in ((rebased or {}).get(rule_hash) or {}):
            continue
        issues.extend(_continuity(before, record))
    return {key: value for key, value in problems.items() if value}


def continuity_breaks(records: list[dict[str, object]], rebased: dict[str, dict[str, str]] | None = None) -> list[tuple[str, str]]:
    """(rule hash, day) of every record that does not follow from the one before (not already marked)."""
    breaks = []
    previous: dict[str, dict[str, object]] = {}
    for record in records:
        rule_hash, day = record["rule_hash"], record["date"]
        before = previous.get(rule_hash)
        previous[rule_hash] = record
        if before is not None and day not in ((rebased or {}).get(rule_hash) or {}) and _continuity(before, record):
            breaks.append((rule_hash, day))
    return breaks


def _continuity(before: dict[str, object], record: dict[str, object]) -> list[str]:
    """What does not follow from ``before`` through ``record``'s adjustments, trades and new money."""
    issues: list[str] = []
    day = record["date"]
    units = {item["code"]: item["units"] for item in before["holdings"]}
    for item in record.get("adjustments_today") or []:
        if item["code"] in units:
            units[item["code"]] *= item["factor"]
    cash = before["cash"] + record["contributed"] - before["contributed"] + float(record.get("income_today") or 0.0)
    for trade in record["trades_today"]:
        amount = trade["shares"] * trade["price"]
        if trade["side"] == "BUY":
            units[trade["code"]] = units.get(trade["code"], 0.0) + trade["shares"]
            cash -= amount + trade["fee"]
        else:
            units[trade["code"]] = units.get(trade["code"], 0.0) - trade["shares"]
            cash += amount - trade["fee"] - trade["tax"]
    now = {item["code"]: item["units"] for item in record["holdings"]}
    for code in set(units) | set(now):
        if abs(units.get(code, 0.0) - now.get(code, 0.0)) > UNITS_TOLERANCE:
            issues.append(f"{day} {code} 股數 {now.get(code, 0.0):.4f}，前一天加今天交易應為 {units.get(code, 0.0):.4f}")
    if abs(cash - record["cash"]) > VALUE_TOLERANCE + 0.5 * len(record["trades_today"]):
        issues.append(f"{day} 現金 {record['cash']:,.2f}，前一天加今天交易應為 {cash:,.2f}")
    return issues
