"""2026-10-18 (使用者：繼續研究有什麼更好的策略；錢賺得多就可以，市場不好時不要比 0050 跌得多): the two-stage search over
accounts split across rules.

The first spread accounts showed it (research_method §7): the deep rule with the calm scan rule #4547 kept #4932's money
and fell less when 0050 fell, because the two rules' worst stretches came at different times. This searches every pair
and triple of the registered rules that beat 0050 in both periods (the passing tiers and T1) the same way:

1. stage 1 reads only each rule's registered month-end values up to the development period's end (2020-09). A blend's
   sleeves each run on their share of every amount and are never rebalanced against each other, so its value is the
   shares' sum of its members' values (the costs scale with the money). The shares are fixed by risk parity on the
   development months' volatility (as #4932 and the spread accounts). A candidate stays if, in the development period,
   it beats 0050, its month-end drawdown is at most DRAWDOWN_ROOM deeper than 0050's and it fell at most CALM_GAP further
   than 0050 in each of 0050's 10% falls (2015's, 2018's end, 2020's spring); no two members are near copies.
2. the best by development excess — rules whose development months correlate above NEAR_COPY count as one family (the
   deep rule's versions); a family in at most MEMBER_LIMIT finalists, no two finalists sharing two families — run in the
   engine as blends (research/blend.py) and registered — judged by the same tiers as any rule.

The members were themselves kept for beating 0050 over the whole period, so 2020-10 onwards is not a clean check here;
the month's rolling selection and the forward accounts are.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from datetime import datetime
from pathlib import Path

import numpy as np

SCAN_VERSION = "blend-scan-1.0.0"
DEV_END = "2020-09"           # the development period's last month: nothing after it is read in stage 1
START = "2015-06"             # members whose accounts start here (the common start)
CALM_GAP = 0.01               # at most this much further than 0050 in each of its 10% falls
DRAWDOWN_ROOM = 0.05          # the month-end drawdown at most this much deeper than 0050's
NEAR_COPY = 0.9               # two members whose development month returns correlate above this are one rule
MEMBER_LIMIT = 5              # a family (rules whose development months move together, NEAR_COPY) in at most
                              # this many finalists
FINALISTS = 20
RECENT_MONTH = "2020-10"      # 2026-10-28 (第九輪): the recent track's first month — the gate, and 3/4 of the ranking
KEEP_BEST = 300_000           # 2026-10-28: the best passing blends kept while the search runs (memory)
FRONTIER_COPY = 0.95          # 2026-10-28 (第十輪): near copies at 0.95 (0.9 made 14 families of 1,576 rules)
FRONTIER_ROOM = 0.15          # 第十輪: the drawdown at most this much deeper than 0050's (the high-risk tier's room)


def label(name: str) -> str:
    head, _, rest = name.partition("：")
    if head in ("掃描", "每天") and rest:
        head = rest.split("：")[0]
    return head.replace("（25 個 T0 候選投票）", "")[:12]


def pool(research: str | Path) -> list[dict]:
    """Every newest-engine 100%-stock single rule graded T0 候選 (high-risk or not), T0 or T1, its account from START:
    its month-end values to DEV_END, 0050's and the money put in."""
    from quant_platform.research.categories import uses_0050
    from quant_platform.research.daily import ENGINE_VERSION, PASSING_TIERS, DailyRule, tier
    from quant_platform.research.registry import TrialRegistry, newest

    research = Path(research)
    records = newest([record for record in TrialRegistry(research / "trials.jsonl").records()
                      if record.kind == "candidate" and record.period == "recent"
                      and str(record.metrics.get("engine", "")) == ENGINE_VERSION])
    output = []
    for record in sorted(records.values(), key=lambda record: record.trial_id):
        grade = tier(record.metrics)[0]
        if grade not in PASSING_TIERS and grade != "T1":
            continue
        try:
            report = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))
            rule = DailyRule.model_validate(report["spec"])
        except (OSError, ValueError, KeyError):
            continue
        if uses_0050(report["spec"]) or rule.free or rule.universe != "twse":
            continue
        curve = report.get("curve") or {}
        months = [month for month in curve if month <= DEV_END]
        if not months or months[0] != START:
            continue
        output.append({"trial": record.trial_id, "name": record.spec_name, "tier": grade, "rule": rule,
                       "months": months, "values": np.array([curve[month][0] for month in months], dtype=float),
                       "bench": np.array([curve[month][1] for month in months], dtype=float),
                       "paid": np.array([curve[month][2] for month in months], dtype=float)})
    if not output:
        return output
    # one cash flow and one 0050 account for all: the members whose money and 0050 match the most common ones
    common = max(output, key=lambda item: sum(np.array_equal(item["paid"], other["paid"])
                                              and np.allclose(item["bench"], other["bench"], rtol=0.005) for other in output))
    return [item for item in output if len(item["months"]) == len(common["months"])
            and np.array_equal(item["paid"], common["paid"]) and np.allclose(item["bench"], common["bench"], rtol=0.005)]


def month_returns(values: np.ndarray, paid: np.ndarray) -> np.ndarray:
    """Month returns net of the month's money (values: … × months)."""
    flow = np.diff(paid)
    return (values[..., 1:] - flow) / values[..., :-1] - 1


def falls(bench_returns: np.ndarray) -> list[tuple[int, int]]:
    from quant_platform.research.downside import FALL, falls as found

    return found(bench_returns, FALL)


def measure(values: np.ndarray, bench: np.ndarray, paid: np.ndarray, stretches: list[tuple[int, int]],
            split: int | None = None) -> dict:
    """Development excess, month-end drawdown and the worst gap to 0050 in its falls, for each row of ``values``;
    with ``split`` (the index of RECENT_MONTH) also the yearly excess over 0050 of the time-weighted return after the
    month before it (``recent``) and up to it (``early``), as the full search measures them (scan_full)."""
    returns = month_returns(values, paid)
    units = np.concatenate([np.ones((len(values), 1)), np.cumprod(1 + returns, axis=1)], axis=1)
    drawdown = (units / np.maximum.accumulate(units, axis=1) - 1).min(axis=1)
    base = np.concatenate([[1.0], np.cumprod(1 + month_returns(bench, paid))])
    gap = np.full(len(values), np.inf)
    for peak, trough in stretches:
        gap = np.minimum(gap, (units[:, trough] / units[:, peak] - 1) - (base[trough] / base[peak] - 1))
    output = {"excess": (values[:, -1] - bench[-1]) / paid[-1], "drawdown": drawdown,
              "bench_drawdown": float((base / np.maximum.accumulate(base) - 1).min()),
              "gap": np.where(np.isfinite(gap), gap, 0.0)}
    if split is not None:
        last = units.shape[1] - 1
        output["recent"] = ((units[:, last] / units[:, split - 1]) / (base[last] / base[split - 1])) ** (12 / (last - split + 1)) - 1
        output["early"] = (units[:, split - 1] / base[split - 1]) ** (12 / max(split - 1, 1)) - 1
    return output


def combo_blocks(count: int, size: int, block: int = 2_000_000):
    """2026-10-28: every ``size``-member combination of ``count`` members, a block at a time, in itertools order (the
    triples of 1,576 rules would not fit in memory at once)."""
    if size == 2:
        left, right = np.triu_indices(count, 1)
        pairs = np.column_stack([left, right])
        for begin in range(0, len(pairs), block):
            yield pairs[begin:begin + block]
        return
    if size == 3:
        for first in range(count - 2):
            left, right = np.triu_indices(count - first - 1, 1)
            yield np.column_stack([np.full(len(left), first), left + first + 1, right + first + 1])
        return
    yield np.array(list(itertools.combinations(range(count), size)), dtype=int).reshape(-1, size)


def search(members: list[dict], sizes=(2, 3), count: int = FINALISTS, taken: set[str] | None = None,
           recent_split: int | None = None, near_copy: float = NEAR_COPY, frontier: bool = False,
           extend: bool = False) -> dict:
    """Stage 1 over every pair and triple of ``members``: the counts and the finalists (members, shares, development
    measures), best development excess first. ``taken``: blend hashes already registered (left out). ``recent_split``
    (2026-10-28, 第九輪: the index of RECENT_MONTH): the gate is beating 0050 from that month (time-weighted), the
    ranking EARLY_WEIGHT x the yearly excess before it + the rest x the one after (scan_full's). ``frontier`` (第十輪):
    instead of "at most CALM_GAP further in 0050's falls", a blend stays only if no member of the pool beats it on both
    the money at the end and the worst gap in 0050's falls (the frontier's two measures, in the same months), with the
    drawdown at most FRONTIER_ROOM deeper than 0050's. ``extend`` (第十一輪): then every triple that stayed again with a
    fourth member (one numbered after its three, so each set once), judged the same way."""
    from quant_platform.research.blend import BlendRule, Sleeve
    from quant_platform.research.scan_full import EARLY_WEIGHT

    if not members:
        return {"version": SCAN_VERSION, "pool": 0, "candidates": 0, "passed": 0, "finalists": []}
    paid, bench = members[0]["paid"], members[0]["bench"]
    values = np.stack([member["values"] for member in members])
    returns = month_returns(values, paid)
    volatility = returns.std(axis=1, ddof=1)
    correlation = np.corrcoef(returns)
    stretches = falls(month_returns(bench, paid))
    family = list(range(len(members)))                    # near copies joined (union-find)

    def root(index: int) -> int:
        while family[index] != index:
            family[index] = family[family[index]]
            index = family[index]
        return index
    for left, right in zip(*np.nonzero(np.triu(correlation > near_copy, 1))):
        family[root(int(left))] = root(int(right))
    if frontier:                                          # the pool's own frontier: money at the end, worst fall gap
        own = measure(values, bench, paid, stretches)
        money, gaps = values[:, -1], own["gap"]
        beaten = [(money >= money[index]) & (gaps >= gaps[index]) & ((money > money[index]) | (gaps > gaps[index]))
                  for index in range(len(members))]
        top = np.array([not row.any() for row in beaten])
        front_money, front_gap = money[top], gaps[top]
    counted, passed = 0, 0
    width = 4 if extend else max(sizes)
    kept: dict[str, list] = {name: [] for name in ("score", "excess", "drawdown", "gap", "recent", "early", "combo", "shares")}

    def prune(limit: int) -> None:
        joined = {name: np.concatenate(parts) for name, parts in kept.items() if parts}
        if not joined:
            return
        order = np.argsort(-joined["score"], kind="stable")[:limit]
        for name in kept:
            kept[name] = [joined[name][order]]
    def consider(combos: np.ndarray, size: int) -> None:
        nonlocal counted, passed
        counted += len(combos)
        apart = np.ones(len(combos), dtype=bool)
        for left, right in itertools.combinations(range(size), 2):
            apart &= correlation[combos[:, left], combos[:, right]] <= near_copy
        combos = combos[apart]
        for begin in range(0, len(combos), 20_000):                  # a block at a time (memory)
            block = combos[begin:begin + 20_000]
            weights = 1 / volatility[block]
            shares = weights / weights.sum(axis=1, keepdims=True)
            blended = np.einsum("km,kmt->kt", shares, values[block])
            found = measure(blended, bench, paid, stretches, recent_split)
            beat = found["recent"] if recent_split is not None else found["excess"]
            if frontier:
                end, gap = blended[:, -1][:, None], found["gap"][:, None]
                dominated = ((front_money[None, :] >= end) & (front_gap[None, :] >= gap)
                             & ((front_money[None, :] > end) | (front_gap[None, :] > gap))).any(axis=1)
                keep = (beat > 0) & (found["drawdown"] >= found["bench_drawdown"] - FRONTIER_ROOM) & ~dominated
            else:
                keep = ((beat > 0) & (found["drawdown"] >= found["bench_drawdown"] - DRAWDOWN_ROOM)
                        & (found["gap"] >= -CALM_GAP))
            if not keep.any():
                continue
            passed += int(keep.sum())
            score = (EARLY_WEIGHT * found["early"] + (1 - EARLY_WEIGHT) * found["recent"]
                     if recent_split is not None else found["excess"])
            pad = width - size
            kept["score"].append(score[keep])
            kept["excess"].append(found["excess"][keep])
            kept["drawdown"].append(found["drawdown"][keep])
            kept["gap"].append(found["gap"][keep])
            kept["recent"].append(found["recent"][keep] if recent_split is not None else np.full(int(keep.sum()), np.nan))
            kept["early"].append(found["early"][keep] if recent_split is not None else np.full(int(keep.sum()), np.nan))
            kept["combo"].append(np.pad(block[keep], ((0, 0), (0, pad)), constant_values=-1))
            kept["shares"].append(np.pad(shares[keep], ((0, 0), (0, pad))))
        if sum(len(part) for part in kept["score"]) > 2 * KEEP_BEST:
            prune(KEEP_BEST)

    for size in sizes:
        for combos in combo_blocks(len(members), size):
            if len(combos):
                consider(combos, size)
    prune(KEEP_BEST)
    if extend and kept["combo"]:                          # 第十一輪: a fourth member on the triples that stayed
        triples = kept["combo"][0][(kept["combo"][0] >= 0).sum(axis=1) == 3][:, :3]
        others = np.arange(len(members))
        for begin in range(0, len(triples), 1_000):
            part = triples[begin:begin + 1_000]
            rows_at, added = np.nonzero(others[None, :] > part.max(axis=1)[:, None])
            if len(rows_at):
                consider(np.column_stack([part[rows_at], others[added]]), 4)
        prune(KEEP_BEST)
    rows = []
    if kept["score"]:
        for index in range(len(kept["score"][0])):
            combo = [int(member) for member in kept["combo"][0][index] if member >= 0]
            rows.append((float(kept["score"][0][index]), combo, kept["shares"][0][index][:len(combo)].tolist(),
                         float(kept["drawdown"][0][index]), float(kept["gap"][0][index]),
                         float(kept["excess"][0][index]), float(kept["recent"][0][index]), float(kept["early"][0][index])))
    used: dict[int, int] = {}
    chosen: list[dict] = []
    for score, combo, shares, drawdown, gap, excess, recent, early in rows:
        if len(chosen) >= count:
            break
        groups = {root(member) for member in combo}
        if any(used.get(group, 0) >= MEMBER_LIMIT for group in groups):
            continue
        if any(len(groups & item["groups"]) >= min(2, len(groups)) for item in chosen):
            continue
        rounded = [round(share, 3) for share in shares[:-1]]
        rounded.append(round(1 - sum(rounded), 3))
        parts = [members[member] for member in combo]
        blend = BlendRule(name=("組合搜尋：" + "＋".join(f"{label(part['name'])}#{part['trial']} {share:.0%}"
                                                    for part, share in zip(parts, rounded)))[:80],
                          sleeves=tuple(Sleeve(rule=part["rule"], share=share) for part, share in zip(parts, rounded)))
        if blend.rule_hash in (taken or set()):
            continue
        for group in groups:
            used[group] = used.get(group, 0) + 1
        chosen.append({"combo": combo, "groups": groups, "blend": blend, "members": [part["trial"] for part in parts], "shares": rounded,
                       "dev_excess": round(excess, 4), "dev_drawdown": round(drawdown, 4), "dev_gap": round(gap, 4),
                       **({"recent": round(recent, 4), "early": round(early, 4), "score": round(score, 4)}
                          if recent_split is not None else {})})
    return {"version": SCAN_VERSION, "pool": len(members), "families": len({root(index) for index in range(len(members))}),
            "ranking": "recent" if recent_split is not None else "excess", "candidates": counted, "passed": passed,
            "bench_drawdown": round(float(measure(values[:1], bench, paid, stretches)["bench_drawdown"]), 4),
            "finalists": chosen}


ROLE_SPLITS = (
    # 2026-10-20 (使用者：因子、策略、DL、RL、ML 各負責什麼): one account a role — the deep model attacks (it alone beats
    # 0050 in 0050's strong months), the factor rules defend (they beat 0050 in its falling months), each its own
    # sleeve, shares by risk parity on the development months (as the search)
    ("分工帳戶：深度學習進攻＋共識、穩健、跌市穩三個防守", (("深度學習進攻", 4941), ("共識防守", 4589), ("穩健防守", 5052),
                                          ("跌市穩防守", 4547))),
    ("分工帳戶：深度學習與機器學習進攻＋穩健、跌市穩防守", (("深度學習進攻", 4941), ("機器學習進攻", 5040), ("穩健防守", 5052),
                                         ("跌市穩防守", 4547))),
)


TOP10_SPLITS = (
    # 2026-10-20 (#5983 深度學習前 10 名固定持有 20 天，5,256 萬，但 0050 大跌時多跌 10.4 點): #5155 and #5163 with their
    # deep sleeve replaced by #5983 — the blend search's stage 1 (at most 1 point further in each fall) leaves it out
    ("組合：深度學習前 10 名＋跌市穩", (("深度學習前 10 名", 5983), ("跌市穩", 5106))),
    ("組合：共識＋深度學習前 10 名＋跌市穩", (("共識", 4317), ("深度學習前 10 名", 5983), ("跌市穩", 5106))),
)


NEXT10_SPLITS = (
    # 2026-10-21 (#6090 = #5983 filled at the next session's close: 6,697 萬, 7.7 points further than 0050 in its worst
    # fall, the frontier's top): #6086 and #6087 with #6090 in place of #5983
    ("組合：深度學習前 10 名隔天收盤＋跌市穩", (("深度學習前 10 名隔天收盤", 6090), ("跌市穩", 5106))),
    ("組合：共識＋深度學習前 10 名隔天收盤＋跌市穩", (("共識", 4317), ("深度學習前 10 名隔天收盤", 6090), ("跌市穩", 5106))),
)


SPLIT_BLENDS = (
    # 2026-10-22 (research_method §7「開盤賣、收盤買」): the frontier's blends #6365 and #6366 with every sleeve's
    # split version — the deep top 10 #6490 (6,750 萬), the calm rule #6503 (3,130 萬), the consensus #6505 (3,137 萬)
    ("組合：深度學習前 10 名＋跌市穩（開盤賣、收盤買）", (("深度學習前 10 名", 6490), ("跌市穩", 6503))),
    ("組合：共識＋深度學習前 10 名＋跌市穩（開盤賣、收盤買）", (("共識", 6505), ("深度學習前 10 名", 6490), ("跌市穩", 6503))),
)


def role_blends(research: str | Path = Path("instance") / "research", splits=ROLE_SPLITS) -> list:
    """The ``splits`` (ROLE_SPLITS, TOP10_SPLITS) as blends: each member's share by risk parity on its development
    months (volatility of its month returns to DEV_END); the development measures printed."""
    from quant_platform.research.blend import BlendRule, Sleeve

    members = {item["trial"]: item for item in pool(research)}
    output = []
    for name, parts in splits:
        found = [members.get(trial) for _role, trial in parts]
        if any(item is None for item in found):
            print(f"{name}：成員不在組合池（{[trial for (_role, trial), item in zip(parts, found) if item is None]}）", flush=True)
            continue
        paid, bench = found[0]["paid"], found[0]["bench"]
        values = np.stack([item["values"] for item in found])
        volatility = month_returns(values, paid).std(axis=1, ddof=1)
        shares = (1 / volatility) / (1 / volatility).sum()
        rounded = [round(float(share), 3) for share in shares[:-1]]
        rounded.append(round(1 - sum(rounded), 3))
        blended = np.einsum("m,mt->t", np.array(rounded), values)[None]
        measured = measure(blended, bench, paid, falls(month_returns(bench, paid)))
        label_ = "＋".join(f"{role}#{trial} {share:.0%}" for (role, trial), share in zip(parts, rounded))
        print(f"{name}：{label_}；開發期比 0050 {measured['excess'][0]:+.1%}、月底回撤 {measured['drawdown'][0]:.1%}、"
              f"跌市最糟 {measured['gap'][0]:+.1%}", flush=True)
        output.append(BlendRule(name=f"{name}（{label_}）"[:80],
                                sleeves=tuple(Sleeve(rule=item["rule"], share=share) for item, share in zip(found, rounded))))
    return output


PERIOD_YEARS = ((2015, 2016), (2017, 2019), (2020, 2021), (2022, 2023), (2024, 2026))   # research/eras.py's


def every_period(member: dict) -> bool:
    """2026-10-24 (使用者：每個時期同樣的策略是否還適用): the member beat 0050 in each period (research/eras.py's five,
    the last one cut at DEV_END), each period's month returns net of the money put in, compounded and annualized."""
    rule = month_returns(member["values"], member["paid"])
    bench = month_returns(member["bench"], member["paid"])
    years = np.array([int(month[:4]) for month in member["months"][1:]])
    for first, last in PERIOD_YEARS:
        inside = (years >= first) & (years <= last)
        if inside.sum() < 6:
            continue
        months = inside.sum()
        mine = np.prod(1 + rule[inside]) ** (12 / months) - 1
        theirs = np.prod(1 + bench[inside]) ** (12 / months) - 1
        if mine <= theirs:
            return False
    return True


def consistent_batch(research: str | Path = Path("instance") / "research", today=None) -> list:
    """2026-10-24 (research_method §7 組合搜尋第七輪; written before the run): the search over the rules that beat 0050 in
    every period up to the month's cutoff only (every_period), the development months up to the cutoff as rolling_batch."""
    from datetime import date

    from quant_platform.research.rolling import cutoff_for

    global DEV_END
    kept = DEV_END
    DEV_END = cutoff_for(today or date.today()).strftime("%Y-%m")
    try:
        return batch(research, keep=every_period)
    finally:
        DEV_END = kept


def guarded(member: dict) -> bool:
    """2026-10-25 (research_method §7 組合搜尋第八輪): a rule that holds fewer stocks while TAIEX is below its 200-session
    average — half (vol_scale "taiex_half") or none (market_filter "taiex_200"), the two guards that passed grading."""
    rule = member["rule"]
    return rule.vol_scale == "taiex_half" or rule.market_filter == "taiex_200"


def guarded_batch(research: str | Path = Path("instance") / "research", today=None) -> list:
    """2026-10-25 (research_method §7 組合搜尋第八輪; written before the run): the search over the guarded rules only
    (``guarded``), the development months up to the month's cutoff as rolling_batch."""
    from datetime import date

    from quant_platform.research.rolling import cutoff_for

    global DEV_END
    kept = DEV_END
    DEV_END = cutoff_for(today or date.today()).strftime("%Y-%m")
    try:
        return batch(research, keep=guarded)
    finally:
        DEV_END = kept


def recent_batch(research: str | Path = Path("instance") / "research", today=None) -> list:
    """2026-10-28 (research_method §7 組合搜尋第九輪; written before the run; 使用者 10/26-27: 2015～2020 不當門檻、排序只佔
    四分之一，同類的地方直接照做): the search with the development months up to the month's cutoff, gated on beating 0050
    from 2020-10 and ranked 3/4 on that (search's ``recent_split``)."""
    from datetime import date

    from quant_platform.research.rolling import cutoff_for

    global DEV_END
    kept = DEV_END
    DEV_END = cutoff_for(today or date.today()).strftime("%Y-%m")
    try:
        return batch(research, recent=True)
    finally:
        DEV_END = kept


def four_batch(research: str | Path = Path("instance") / "research", today=None) -> list:
    """2026-10-28 (research_method §7 組合搜尋第十一輪; written before the run): round ten, and every triple it keeps
    again with a fourth member (search's ``extend``)."""
    from datetime import date

    from quant_platform.research.rolling import cutoff_for

    global DEV_END
    kept = DEV_END
    DEV_END = cutoff_for(today or date.today()).strftime("%Y-%m")
    try:
        return batch(research, recent=True, near_copy=FRONTIER_COPY, frontier=True, extend=True)
    finally:
        DEV_END = kept


def frontier_batch(research: str | Path = Path("instance") / "research", today=None) -> list:
    """2026-10-28 (research_method §7 組合搜尋第十輪; written before the run): round nine's months, gate and ranking, the
    near copies at FRONTIER_COPY, and only the blends the pool's own frontier does not beat (search's ``frontier``)."""
    from datetime import date

    from quant_platform.research.rolling import cutoff_for

    global DEV_END
    kept = DEV_END
    DEV_END = cutoff_for(today or date.today()).strftime("%Y-%m")
    try:
        return batch(research, recent=True, near_copy=FRONTIER_COPY, frontier=True)
    finally:
        DEV_END = kept


def rolling_batch(research: str | Path = Path("instance") / "research", today=None) -> list:
    """2026-10-22 (research_method §7 組合搜尋第六輪; written before the run): the same search with the development months
    up to the month's cutoff (the end of the month two months back) instead of 2020-09 — how the monthly list grades
    single rules; the forward test from the day after the cutoff is the out-of-sample check."""
    from datetime import date

    from quant_platform.research.rolling import cutoff_for

    global DEV_END
    kept = DEV_END
    DEV_END = cutoff_for(today or date.today()).strftime("%Y-%m")
    try:
        return batch(research)
    finally:
        DEV_END = kept


def batch(research: str | Path = Path("instance") / "research", keep=None, recent: bool = False,
          near_copy: float = NEAR_COPY, frontier: bool = False, extend: bool = False) -> list:
    """The finalists as blends to run (daily --name blendscan); stage 1 written to research/blend_scan/<stamp>.json.
    ``keep``: which pool members to search over (all by default)."""
    from quant_platform.research.registry import TrialRegistry

    research = Path(research)
    taken = {record.spec_hash for record in TrialRegistry(research / "trials.jsonl").records()}
    members = pool(research)
    if keep is not None:
        members = [member for member in members if keep(member)]
    split = members[0]["months"].index(RECENT_MONTH) if recent and members else None
    found = search(members, taken=taken, recent_split=split, near_copy=near_copy, frontier=frontier, extend=extend)
    folder = research / "blend_scan"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    # 2026-10-20: the pool names every batch's rules (pool._build_sources), so this runs again whenever the registry
    # changes — the same finalists are written once, under their digest, not once a call
    digest = hashlib.sha256(json.dumps(sorted(item["blend"].rule_hash for item in found["finalists"])).encode()).hexdigest()[:12]
    target = folder / f"finalists-{digest}.json"
    summary = {**{key: value for key, value in found.items() if key != "finalists"}, "stamp": stamp,
               "dev_end": DEV_END, "calm_gap": CALM_GAP, "drawdown_room": DRAWDOWN_ROOM, "near_copy": near_copy,
               "frontier": frontier, "extend": extend,
               "finalists": [{key: value for key, value in item.items() if key not in ("blend", "combo", "groups")}
                             | {"name": item["blend"].name, "rule_hash": item["blend"].rule_hash}
                             for item in found["finalists"]]}
    if not target.is_file():
        target.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"組合搜尋第一階段：{found['pool']} 個規則、{found['candidates']} 個兩檔／三檔{'／四檔' if extend else ''}組合、"
          f"{found['passed']} 個{'2020-10 起' if recent else '開發期'}贏 0050 且跌市不多跌、{len(found['finalists'])} 個進引擎",
          flush=True)
    return [item["blend"] for item in found["finalists"]]
