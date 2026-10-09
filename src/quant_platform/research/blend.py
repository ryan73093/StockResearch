"""2026-10-09 (使用者：請繼續研究並找出更多的 T0 策略): one account split across strategy families.

A blend gives each sleeve, a daily rule run on its own, a fixed share of the money (the starting capital
and every monthly amount) and puts the rest into 0050 as it arrives. The sleeves are not rebalanced against
each other: a sleeve that does well grows. The account is the sum of its sleeves, so it is judged by the
same gate, rolling windows and forward observation as a single rule, and every blend is one more trial.

Why: the families' monthly gaps to 0050 are related but not the same (the model rule and the trend rule
0.75 over 136 months; only half of their worst twelve months coincide), so an account holding both can fall
less than either alone while keeping more of their return than a fixed half in 0050.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quant_platform.research.legacy_challenger import RunResult

BLEND_VERSION = "blend-1.0.0"


class Sleeve(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule: "DailyRule"
    share: float = Field(gt=0.0, le=1.0)


class BlendRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["blend"] = "blend"
    name: str = Field(min_length=1, max_length=80)
    sleeves: tuple[Sleeve, ...] = Field(min_length=2, max_length=4)
    core: float = Field(default=0.0, ge=0.0, le=0.9)          # share bought into 0050 as the money arrives

    @model_validator(mode="after")
    def _shares(self) -> BlendRule:
        total = sum(sleeve.share for sleeve in self.sleeves) + self.core
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"the sleeves and the 0050 core must add up to the whole account, not {total:.4f}")
        if any(sleeve.rule.core for sleeve in self.sleeves):
            raise ValueError("a sleeve keeps no 0050 of its own; the blend's core does")
        if any(sleeve.rule.universe != "twse" for sleeve in self.sleeves):
            raise ValueError("blends are listed stocks only")
        return self

    @property
    def universe(self) -> str:
        return "twse"

    @property
    def factors(self) -> dict[str, float]:
        merged: dict[str, float] = {}
        for sleeve in self.sleeves:
            merged.update(sleeve.rule.factors)
        return merged

    @property
    def label(self) -> str:
        return "＋".join(sleeve.rule.label for sleeve in self.sleeves)

    def canonical(self) -> dict[str, object]:
        return {"kind": "blend", "name": self.name, "core": self.core,
                "sleeves": [{"rule": sleeve.rule.canonical(), "share": sleeve.share} for sleeve in self.sleeves]}

    @property
    def rule_hash(self) -> str:
        body = {"core": self.core, "engine_family": "blend",
                "sleeves": [{"rule": {key: value for key, value in sleeve.rule.canonical().items() if key != "name"},
                             "share": sleeve.share} for sleeve in self.sleeves]}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()


def _late_imports():
    from quant_platform.research.daily import DailyRule

    Sleeve.model_rebuild(_types_namespace={"DailyRule": DailyRule})
    BlendRule.model_rebuild()


_late_imports()


def scaled(plan, share: float):
    from quant_platform.research.stock_rules import SeedPlan

    return SeedPlan(initial=plan.initial * share, monthly_amount=plan.monthly_amount * share,
                    day_of_month=plan.day_of_month)


def combine(name: str, runs: list[RunResult]) -> RunResult:
    """The account that holds every sleeve: values, flows and money summed day by day."""
    days = runs[0].days
    if any(run.days != days for run in runs):
        raise ValueError("sleeves must cover the same sessions")
    contributions: dict[date, float] = defaultdict(float)
    for run in runs:
        for when, amount in run.contributions:
            contributions[when] += amount
    combined = RunResult(name, days, [sum(values) for values in zip(*(run.values for run in runs), strict=True)],
                         [sum(flows) for flows in zip(*(run.flows for run in runs), strict=True)],
                         sorted(contributions.items()))
    combined.trades = sum(run.trades for run in runs)
    combined.fees = sum(run.fees for run in runs)
    combined.taxes = sum(run.taxes for run in runs)
    combined.bought = sum(run.bought for run in runs)
    combined.sold = sum(run.sold for run in runs)
    return combined


class BlendAccount:
    """A blend replayed on one factor panel: each sleeve's ranking, weights and parking computed once over
    the selection period (as ``daily.evaluate`` does for a single rule); ``run`` any stretch of it."""

    def __init__(self, data, fp, blend: BlendRule, costs, start: date, end: date) -> None:
        from quant_platform.research.daily import account_parking, daily_rankings, daily_weights, exposure_schedule

        self.data, self.blend, self.costs = data, blend, costs
        self.parts = []
        for sleeve in blend.sleeves:
            ranks = daily_rankings(fp, sleeve.rule, start, end)
            self.parts.append((sleeve, ranks, daily_weights(fp, sleeve.rule, ranks), account_parking(data, fp, sleeve.rule, costs),
                               exposure_schedule(data, fp, sleeve.rule, costs)))
        self.first = max(next((day for day in sorted(ranks) if ranks[day]), start) for _sleeve, ranks, *_rest in self.parts)

    def run(self, start: date, end: date, plan=None, ledger: list | None = None, snapshots: dict | None = None) -> RunResult:
        from quant_platform.research.daily import simulate_daily
        from quant_platform.research.stock_rules import SeedPlan

        plan = plan or SeedPlan()
        runs, ledgers, books = [], [], []
        for sleeve, ranks, weights, parked, exposure in self.parts:
            part_ledger, part_book = ([] if ledger is not None else None), ({} if snapshots is not None else None)
            runs.append(simulate_daily(self.data, sleeve.rule, self.costs, start, end, ranks, scaled(plan, sleeve.share),
                                       ledger=part_ledger, snapshots=part_book, weights=weights, parked=parked,
                                       exposure=exposure))
            ledgers.append(part_ledger)
            books.append(part_book)
        if self.blend.core:
            part_ledger, part_book = ([] if ledger is not None else None), ({} if snapshots is not None else None)
            runs.append(simulate_daily(self.data, None, self.costs, start, end, plan=scaled(plan, self.blend.core),
                                       ledger=part_ledger, snapshots=part_book))
            ledgers.append(part_ledger)
            books.append(part_book)
        if ledger is not None:
            merged = [entry for part in ledgers for entry in part]
            merged.sort(key=lambda entry: entry["day"])          # stable: each sleeve's order within a day kept
            ledger.extend(merged)
        if snapshots is not None:
            for day in books[0]:
                cash, units = 0.0, defaultdict(float)
                for book in books:
                    part_cash, part_units = book[day]
                    cash += part_cash
                    for symbol, count in part_units.items():
                        units[symbol] += count
                snapshots[day] = (cash, dict(units))
        return combine(self.blend.rule_hash[:12], runs)


def evaluate_blend(data, fp, blend: BlendRule, costs, benchmark_cache: dict | None = None) -> dict[str, object]:
    from quant_platform.research.daily import ENGINE_VERSION, RECENT_END, RECENT_START, account_report

    account = BlendAccount(data, fp, blend, costs, RECENT_START, RECENT_END)
    report = account_report(data, account.run, account.first, costs, benchmark_cache)
    return {"engine": f"{ENGINE_VERSION}+{BLEND_VERSION}", "spec": blend.canonical(), "rule_hash": blend.rule_hash,
            "label": blend.label, **report}


def run_blend_trial(blend: BlendRule, history: str | Path, registry, reports_dir: Path, costs, data, fp,
                    data_fingerprint: str, benchmark_cache: dict | None = None) -> tuple[object, dict]:
    from quant_platform.research.daily import ENGINE_VERSION, PERIOD, SEED_PLAN, input_digests, record_trial

    payload = {"blend": blend.canonical(), "period": PERIOD, "costs": costs.as_dict(), "data": data_fingerprint,
               "engine": f"{ENGINE_VERSION}+{BLEND_VERSION}", "plan": SEED_PLAN}
    payload.update(input_digests(history, list(blend.factors),
                                 any(sleeve.rule.exit_model != "none" for sleeve in blend.sleeves)))
    return record_trial(registry, reports_dir, blend.rule_hash, blend.name, payload,
                        lambda: evaluate_blend(data, fp, blend, costs, benchmark_cache), data_fingerprint, "blend",
                        f"{ENGINE_VERSION}+{BLEND_VERSION}")


def parse_spec(spec: dict):
    """A new-design spec from a report: a blend or a single daily rule."""
    from quant_platform.research.daily import DailyRule

    return BlendRule.model_validate(spec) if spec.get("kind") == "blend" else DailyRule.model_validate(spec)


def family_rules_for_blends():
    from quant_platform.research.daily import DailyRule

    model = DailyRule(name="機器學習（含財報）：前 20 名、同產業最多 3 成、每週決策", factors={"ml_gbm_statements": 1.0},
                      industry_cap=0.3, check="weekly")
    trend = DailyRule(name="每天 站上 200 日均線：前 20 名、同產業最多 3 成、依波動度配置", factors={"trend_200": 1.0},
                      industry_cap=0.3, weighting="inverse_vol")
    return model, trend


def t0_hunt_batch() -> list:
    """2026-10-09 (使用者：再繼續找 T0), fixed in advance: the revenue event factor weekly (alone and half in
    0050); the model with the trend-plus-revenue rule (T1); the model, the trend rule and trend-plus-revenue
    a sixth each; the model with the revenue event rule; each blend half in 0050."""
    from quant_platform.research.daily import DailyRule

    model, trend = family_rules_for_blends()
    trend_revenue = DailyRule(name="每天 站上 200 日均線＋月營收年增：前 20 名、同產業最多 3 成",
                              factors={"trend_200": 1.0, "revenue_yoy": 1.0}, industry_cap=0.3)
    event = DailyRule(name="每週 月營收加速：前 20 名、同產業最多 3 成", factors={"revenue_accel": 1.0},
                      industry_cap=0.3, check="weekly")
    sixth = 1 / 6
    return [
        event,
        event.model_copy(update={"name": event.name + "、一半放 0050", "core": 0.5}),
        BlendRule(name="組合：機器學習每週＋站上 200 日均線＋月營收年增 各 1/4、一半放 0050",
                  sleeves=(Sleeve(rule=model, share=0.25), Sleeve(rule=trend_revenue, share=0.25)), core=0.5),
        BlendRule(name="組合：機器學習每週＋趨勢（依波動度）＋趨勢＋月營收 各 1/6、一半放 0050",
                  sleeves=(Sleeve(rule=model, share=sixth), Sleeve(rule=trend, share=sixth),
                           Sleeve(rule=trend_revenue, share=sixth)), core=0.5),
        BlendRule(name="組合：機器學習每週＋月營收加速每週 各 1/4、一半放 0050",
                  sleeves=(Sleeve(rule=model, share=0.25), Sleeve(rule=event, share=0.25)), core=0.5),
    ]


def low_drawdown_batch() -> list:
    """2026-10-09 (使用者：回撤門檻維持；0050 自己持有): 100% stocks, aiming at a drawdown within 5 points of
    0050's while still beating it. Fixed in advance: trend among calm stocks, the model among calm stocks
    (low-volatility momentum crashes less), and two blends that add a calm family."""
    from quant_platform.research.daily import DailyRule

    model, trend = family_rules_for_blends()
    calm = DailyRule(name="每天 60 日低波動：前 20 名、同產業最多 3 成", factors={"low_volatility_60": 1.0},
                     industry_cap=0.3)
    trend_calm = DailyRule(name="每天 站上 200 日均線＋60 日低波動：前 20 名、同產業最多 3 成",
                           factors={"trend_200": 1.0, "low_volatility_60": 1.0}, industry_cap=0.3)
    model_calm = DailyRule(name="機器學習（含財報）＋60 日低波動：前 20 名、同產業最多 3 成、每週決策",
                           factors={"ml_gbm_statements": 1.0, "low_volatility_60": 1.0}, industry_cap=0.3, check="weekly")
    third = 1 / 3
    return [
        trend_calm,
        model_calm,
        BlendRule(name="組合：機器學習每週＋站上 200 日均線（依波動度）＋60 日低波動 各 1/3",
                  sleeves=(Sleeve(rule=model, share=third), Sleeve(rule=trend, share=third), Sleeve(rule=calm, share=third))),
        BlendRule(name="組合：機器學習每週＋站上 200 日均線＋60 日低波動 各半",
                  sleeves=(Sleeve(rule=model, share=0.5), Sleeve(rule=trend_calm, share=0.5))),
    ]


def vol_scale_batch() -> list:
    """2026-10-09 (使用者：好的策略應該要能夠避免回撤、知道什麼時候要賣): the three best 100%-stock rules with
    the stock part scaled down when their own volatility runs above its usual level (vol_scale "v1", cash
    for the rest). Fixed in advance; judged by the same gate (drawdown within 5 points of 0050's)."""
    model, trend = family_rules_for_blends()
    model_v = model.model_copy(update={"name": model.name + "、波動大時減碼（現金）", "vol_scale": "v1"})
    trend_v = trend.model_copy(update={"name": trend.name + "、波動大時減碼（現金）", "vol_scale": "v1"})
    return [trend_v, model_v,
            BlendRule(name="組合：機器學習每週＋站上 200 日均線（依波動度）各半、波動大時減碼（現金）",
                      sleeves=(Sleeve(rule=model_v, share=0.5), Sleeve(rule=trend_v, share=0.5)))]


LARGE_CAPS = 100


def large_cap_batch() -> list:
    """2026-10-09 (使用者：0050 不放進策略，也不要照 0050 的比例買台積電): 100% stocks picked only among the
    100 largest listed stocks by market value (0050 holds the largest 50 at their market-value shares; these
    rules pick 20 of the 100 and buy equal amounts, so no stock is more than about a twentieth). Large caps
    beat the average stock since 2020-10 (factor strength: market value +5.5%, since 2024 +11.6%) and swing
    less. Fixed in advance, one rule per factor family: trend, the model, foreign and trust buying, revenue
    and earnings growth, and the model with the trend rule half each."""
    from quant_platform.research.daily import DailyRule

    common = {"industry_cap": 0.3, "large_caps": LARGE_CAPS}
    trend = DailyRule(name="大型股（市值前 100）站上 200 日均線：前 20 名、同產業最多 3 成、每檔等額",
                      factors={"trend_200": 1.0}, **common)
    model = DailyRule(name="大型股（市值前 100）機器學習（含財報）：前 20 名、同產業最多 3 成、每週決策",
                      factors={"ml_gbm_statements": 1.0}, check="weekly", **common)
    flows = DailyRule(name="大型股（市值前 100）外資＋投信買超：前 20 名、同產業最多 3 成、每週決策",
                      factors={"foreign_buy_20": 1.0, "trust_buy_20": 1.0}, check="weekly", **common)
    growth = DailyRule(name="大型股（市值前 100）月營收年增＋每股盈餘成長：前 20 名、同產業最多 3 成、每週決策",
                       factors={"revenue_yoy": 1.0, "eps_growth": 1.0}, check="weekly", **common)
    return [trend, model, flows, growth,
            BlendRule(name="組合：大型股機器學習每週＋大型股站上 200 日均線 各半",
                      sleeves=(Sleeve(rule=model, share=0.5), Sleeve(rule=trend, share=0.5)))]


def large_cap_second_batch() -> list:
    """2026-10-09, after the first large-cap batch (trend among the 100 largest: +293%, drawdown -44%, a
    third of the costs; the model and the trend rule half each: drawdown -39.2%, 0.2 points past the gate).
    Fixed in advance, one hypothesis each, all 100% stocks among the 100 largest, top 20, at most 3 in 10
    from one industry: (1) the best full-market configuration, trend weighted by calmness, moved to the
    large caps (does the universe help the champion?); (2) trend confirmed by revenue growth (a price and a
    fundamental factor, both strong); (3) one list scored on trend and the model together (stocks both
    signals agree on, instead of two lists side by side)."""
    from quant_platform.research.daily import DailyRule

    common = {"industry_cap": 0.3, "large_caps": LARGE_CAPS}
    return [
        DailyRule(name="大型股（市值前 100）站上 200 日均線：前 20 名、同產業最多 3 成、依波動度配置",
                  factors={"trend_200": 1.0}, weighting="inverse_vol", **common),
        DailyRule(name="大型股（市值前 100）站上 200 日均線＋月營收年增：前 20 名、同產業最多 3 成",
                  factors={"trend_200": 1.0, "revenue_yoy": 1.0}, **common),
        DailyRule(name="大型股（市值前 100）站上 200 日均線＋機器學習 同時排名：前 20 名、同產業最多 3 成、每週決策",
                  factors={"trend_200": 1.0, "ml_gbm_statements": 1.0}, check="weekly", **common),
    ]


def blend_batch() -> list[BlendRule]:
    """Fixed in advance: the weekly model rule and the volatility-weighted trend rule (both T1 alone), half
    each, a quarter each with half in 0050, and a third each with a third in 0050."""
    from quant_platform.research.daily import DailyRule

    model = DailyRule(name="機器學習（含財報）：前 20 名、同產業最多 3 成、每週決策", factors={"ml_gbm_statements": 1.0},
                      industry_cap=0.3, check="weekly")
    trend = DailyRule(name="每天 站上 200 日均線：前 20 名、同產業最多 3 成、依波動度配置", factors={"trend_200": 1.0},
                      industry_cap=0.3, weighting="inverse_vol")
    output = []
    for share, core, word in ((0.5, 0.0, "各半"), (0.25, 0.5, "各 1/4、一半放 0050"), (1 / 3, 1 / 3, "各 1/3、1/3 放 0050")):
        output.append(BlendRule(name=f"組合：機器學習每週＋站上 200 日均線（依波動度）{word}",
                                sleeves=(Sleeve(rule=model, share=share), Sleeve(rule=trend, share=share)),
                                core=1.0 - 2 * share if core else 0.0))
    return output
