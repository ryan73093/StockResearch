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
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from quant_platform.research.costs import CostModel, broker_costs
from quant_platform.research.forward import FORWARD_START
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


def plan_of(item: dict[str, object]):
    """A tracked rule's cash flow: one lump sum (the owner's NT$300,000 account, the default since
    2026-10-04) or the monthly plan (items written before that carry no ``plan``)."""
    if item.get("plan") == "seed":
        return SeedPlan(float(item.get("initial") or SEED_CAPITAL), float(item.get("monthly") or SEED_MONTHLY))
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
    latest = {}
    for record in TrialRegistry(base / "trials.jsonl").records():
        if record.spec_hash in mine and record.kind == "candidate" and record.period == "recent":
            latest[record.spec_hash] = record
    best = None
    for spec_hash, record in latest.items():
        grade, _why = tier(record.metrics)
        if grade not in ("T0", "T0 候選", "T1"):
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
    return [
        (base.model_copy(update={"name": base.name + "、新聞利空不買（LLM）", "news_veto": "v1"}),
         "R15 D 新聞否決實驗：LLM 標出利空且有數字或風險旗標的股票不新買（只前向；和下一列同一天開始比）"),
        (base.model_copy(update={"name": base.name + "、新聞否決對照組", "news_veto": "v1-off"}),
         "R15 D 對照組：同一規則、同一天開始、不看新聞（和上一列比）"),
    ]


def qualifying_daily(research_dir: str | Path) -> list[tuple[object, str]]:
    """Daily-decision rules (S9-W02) that pass the new design's gate on 2015-06..2026-09."""
    from quant_platform.research.blend import parse_spec
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.daily import tier

    base = Path(research_dir)
    latest: dict[str, object] = {}
    for record in TrialRegistry(base / "trials.jsonl").records():
        if record.kind == "candidate" and record.period == "recent":
            latest[record.spec_hash] = record
    output = []
    for record in latest.values():
        grade, _why = tier(record.metrics)
        if grade not in ("T0 候選", "T1"):            # T1 too: beats 0050, more volatile (owner 2026-10-04)
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
        """Add rules that newly won both periods, starting today (never earlier than the forward start), and
        the forward-only experiments (FORWARD_ONLY) the first time this runs after they were defined."""
        from quant_platform.research.categories import NO_0050, uses_0050

        items = self.tracked()
        known = {item["rule_hash"] for item in items}
        added = []
        stopped = False
        for item in items:                          # 2026-10-09: accounts that hold 0050 stop (records kept)
            if not item.get("stopped") and item.get("kind") in ("daily", "blend") and uses_0050(item.get("rule") or {}):
                item.update(stopped=today.isoformat(), stop_reason=NO_0050)
                stopped = True
        if self._experiments and META_AI["rule_hash"] not in known:
            added.append({**META_AI, "rule": {}, "plan": "seed", "initial": SEED_CAPITAL, "monthly": SEED_MONTHLY,
                          "since": max(FORWARD_START, today).isoformat(),
                          "added_at": datetime.now(TAIPEI).isoformat(timespec="seconds")})
            known.add(META_AI["rule_hash"])
        for rule, reason in (forward_only() if self._experiments else []):
            if rule.rule_hash in known:
                continue
            added.append({"rule_hash": rule.rule_hash, "name": rule.name, "rule": rule.model_dump(mode="json"),
                          "kind": "daily", "plan": "seed", "initial": SEED_CAPITAL, "monthly": SEED_MONTHLY,
                          "since": max(FORWARD_START, today).isoformat(), "reason": reason,
                          "added_at": datetime.now(TAIPEI).isoformat(timespec="seconds")})
            known.add(rule.rule_hash)
        for rule, reason in qualifying_rules(self._base):
            if rule.rule_hash in known:
                continue
            added.append({"rule_hash": rule.rule_hash, "name": rule.name, "rule": rule.model_dump(mode="json"),
                          "plan": "seed", "initial": SEED_CAPITAL, "monthly": SEED_MONTHLY,
                          "since": max(FORWARD_START, today).isoformat(), "reason": "兩段期間都贏：" + reason,
                          "added_at": datetime.now(TAIPEI).isoformat(timespec="seconds")})
            known.add(rule.rule_hash)
        for rule, reason in qualifying_daily(self._base):
            if rule.rule_hash in known:
                continue
            added.append({"rule_hash": rule.rule_hash, "name": rule.name, "rule": rule.model_dump(mode="json"),
                          "kind": "blend" if getattr(rule, "kind", None) == "blend" else "daily", "plan": "seed", "initial": SEED_CAPITAL, "monthly": SEED_MONTHLY,
                          "since": max(FORWARD_START, today).isoformat(), "reason": reason,
                          "added_at": datetime.now(TAIPEI).isoformat(timespec="seconds")})
            known.add(rule.rule_hash)
        if added or stopped:
            self._folder.mkdir(parents=True, exist_ok=True)
            self.tracked_path.write_text(json.dumps(items + added, ensure_ascii=False, indent=1), encoding="utf-8")
        return added

    def records(self) -> list[dict[str, object]]:
        if not self.log_path.is_file():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def record(self, today: date, now: datetime | None = None) -> list[dict[str, object]]:
        """Append the state of every tracked rule for today and for any earlier session since its
        start that has no record yet (the computer was off at 15:30, or the quotes came late): those
        are marked ``late``. A day whose quotes (stocks and 0050) are not on disk is left for later."""
        if today < FORWARD_START:
            return []
        self.sync(today)
        items = [item for item in self.tracked() if date.fromisoformat(item["since"]) <= today and not item.get("stopped")]
        earlier = self.records()
        done = {(item["date"], item["rule_hash"]) for item in earlier}
        if not items:
            return []
        first_year = min(date.fromisoformat(item["since"]).year for item in items) - WARMUP_YEARS
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
                 if day >= FORWARD_START and count >= self._min_quotes and day in data.closes.get(BENCHMARK, {})]
        todo = {item["rule_hash"]: [day for day in ready if day >= date.fromisoformat(item["since"])
                                    and (day.isoformat(), item["rule_hash"]) not in done] for item in items}
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
            plan = plan_of(item)
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
                    run = BlendAccount(data, factor_panel, rule, self._costs, start, last).run(
                        start, last, plan, ledger=ledger, snapshots=snapshots)
                else:
                    rule = DailyRule.model_validate(item["rule"])
                    ranks = daily_rankings(factor_panel, rule, start, last)
                    run = simulate_daily(data, rule, self._costs, start, last, ranks, plan, ledger=ledger,
                                         snapshots=snapshots, weights=daily_weights(factor_panel, rule, ranks),
                                         parked=account_parking(data, factor_panel, rule, self._costs))
                benchmark = simulate_daily(data, None, self._costs, start, last, plan=plan)
            else:
                rule = StockRule.model_validate(item["rule"])
                run = simulate(data, rule_variant(rule), self._costs, start, last, rankings(panel, rule, start, last),
                               plan, ledger=ledger, snapshots=snapshots)
                benchmark = simulate(data, None, self._costs, start, last, plan=plan)
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
        return written

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
            "trades_total": len(trades), "fees_total": sum(int(entry["fee"]) for entry in trades),
            "taxes_total": sum(int(entry["tax"]) for entry in trades),
            "replay_check": replayed, "costs": BROKER, "data_fingerprint": fingerprint, "engine": ENGINE_VERSION,
            "recorded_at": moment.isoformat(timespec="seconds"), "late": moment.date() != day,
        }

    def summary(self) -> list[dict[str, object]]:
        """Per tracked rule: the latest record, days recorded, the latest trades and any problem."""
        records = self.records()
        problems = reconcile(records)
        finals = final_validation(self._base)
        rows = []
        for item in self.tracked():
            mine = [record for record in records if record["rule_hash"] == item["rule_hash"]]
            latest = mine[-1] if mine else None
            traded = next((record for record in reversed(mine) if record["trades_today"]), None)
            mismatches = sum(len((record.get("replay_check") or {}).get("mismatches") or []) for record in mine)
            rows.append({
                "rule_hash": item["rule_hash"], "name": item["name"], "since": item["since"], "reason": item.get("reason"),
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
                segment_plan = SeedPlan(initial=carry, monthly_amount=plan.monthly_amount, day_of_month=plan.day_of_month)
            part_ledger, part_book = [], {}
            if decision.get("spec"):
                rule = DailyRule.model_validate(decision["spec"])
                ranks = daily_rankings(factor_panel, rule, first, end)
                part = simulate_daily(data, rule, self._costs, first, end, ranks, segment_plan, ledger=part_ledger,
                                      snapshots=part_book, weights=daily_weights(factor_panel, rule, ranks),
                                      parked=account_parking(data, factor_panel, rule, self._costs))
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
        trades = [{**trade, "date": record["date"], "amount": trade["shares"] * trade["price"]}
                  for record in reversed(days) for trade in record["trades_today"]]
        adjustments = [{**entry, "date": record["date"]} for record in reversed(days)
                       for entry in record.get("adjustments_today") or []]
        series = [{"date": record["date"], "value": record["value"], "benchmark": record["benchmark_value"],
                   "contributed": record["contributed"]} for record in days]
        problems = reconcile(days).get(rule_hash, [])
        decisions = self.meta_decisions() if item.get("kind") == "meta-ai" else []
        return {"item": item, "latest": latest, "holdings": holdings, "trades": trades, "adjustments": adjustments,
                "decisions": list(reversed(decisions)),
                "series": series, "sessions": len(days), "problems": problems,
                "buys": sum(1 for trade in trades if trade["side"] == "BUY"),
                "sells": sum(1 for trade in trades if trade["side"] == "SELL")}


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
    return {"code": code, "name": names.get(code, ""), "side": entry["side"], "shares": round(float(entry["shares"]), 6),
            "price": entry["price"], "fee": entry["fee"], "tax": entry["tax"]}


def _names(history: Path, year: int) -> dict[str, str]:
    import pyarrow.parquet as pq

    names: dict[str, str] = {}
    for exchange in ("tpex", "twse"):              # a stock that moved to TWSE keeps its TWSE name
        path = history / "stocks" / exchange / f"{year}.parquet"
        if path.is_file():
            table = pq.read_table(path, columns=["code", "name"])
            names.update(zip(table["code"].to_pylist(), table["name"].to_pylist()))
    return names


def _replay_check(earlier: list[dict[str, object]], rule_hash: str, replayed: dict[date, float]) -> dict[str, object]:
    """Days written before whose value the replay no longer reproduces (a data revision)."""
    mine = [record for record in earlier if record["rule_hash"] == rule_hash]
    mismatches = []
    for record in mine:
        value = replayed.get(date.fromisoformat(record["date"]))
        if value is not None and abs(value - record["value"]) > VALUE_TOLERANCE:
            mismatches.append({"date": record["date"], "recorded": record["value"], "replayed": round(value, 2)})
    return {"days": len(mine), "mismatches": mismatches}


def reconcile(records: list[dict[str, object]]) -> dict[str, list[str]]:
    """Per rule: every record's value equals cash plus its holdings at the closes, and every record
    follows from the one before through its logged adjustments, trades and new money."""
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
        if before is None:
            continue
        units = {item["code"]: item["units"] for item in before["holdings"]}
        for item in record.get("adjustments_today") or []:
            if item["code"] in units:
                units[item["code"]] *= item["factor"]
        cash = before["cash"] + record["contributed"] - before["contributed"]
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
    return {key: value for key, value in problems.items() if value}
