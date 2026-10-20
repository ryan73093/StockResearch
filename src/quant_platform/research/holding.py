"""2026-10-20 (使用者：持有 20 天是策略、月線上續抱也是策略，不是規則；20 天、月線上的比較都沒看到): the holding styles
side by side.

Every newest-engine single rule is grouped with the rules that differ from it only in their holding style
(``daily.holding_key``): a fixed number of sessions (``min_hold`` > 0), trend (``hold="trend"``: kept while above its
20-session average once out of the keep zone) or no minimum. Each group with more than one style is a row: each
style's end value, value from 2020-10, drawdown and tier. Shown on the strategy roles page (研究 › 策略角色 › 持有方式).
"""

from __future__ import annotations

import json
from pathlib import Path

STYLES = ("固定持有 20 天", "月線上續抱", "不設最短持有")
_cache: dict[str, object] = {}


def style_of(spec: dict) -> str | None:
    if spec.get("hold") == "trend":
        return "月線上續抱"
    minimum = spec.get("min_hold", 20)
    if minimum in (None, 20):
        return "固定持有 20 天"
    return "不設最短持有" if not minimum else None


HOLD_WORDS = ("、月線上續抱", "、不設最短持有", "，月線上續抱", "＋月線上續抱")


def named(spec: dict) -> str:
    """A rule's name stating the holding style it has (a blend's sleeves restyled by holding_families_batch kept the
    name of the style they came from, e.g. 「…、月線上續抱」 on a sleeve holding with no minimum)."""
    stem = str(spec.get("name", "")).split("｜持有：")[0]
    for word in HOLD_WORDS:
        stem = stem.replace(word, "")
    style = style_of(spec)
    return f"{stem}｜持有：{style}" if style else stem


def blend_key(rule) -> str:
    """A blend's sleeves and shares without the sleeves' holding styles or names."""
    import hashlib

    parts = [{"share": sleeve.share, "rule": {key: value for key, value in sleeve.rule.canonical().items()
                                              if key not in ("name", "hold", "min_hold")}} for sleeve in rule.sleeves]
    return "blend:" + hashlib.sha256(json.dumps({"core": rule.core, "sleeves": parts}, sort_keys=True,
                                                ensure_ascii=False).encode()).hexdigest()


def llm_forward(research: Path) -> list[dict[str, object]]:
    """The LLM news experiments (forward only: no news before 2026-10-05), each holding style, from the accounts."""
    from quant_platform.research.stock_forward import StockForwardTracker

    output = []
    try:
        for row in StockForwardTracker(research).summary():
            if row.get("stopped") or "新聞" not in row["name"]:
                continue
            style = row["name"].split("｜持有：")[1] if "｜持有：" in row["name"] else "固定持有 20 天"
            output.append({"name": row["name"].split("｜持有：")[0], "style": style, "since": row["since"],
                           "sessions": row["sessions"], "excess": row["excess"], "rule_hash": row["rule_hash"],
                           "veto": "利空不買" in row["name"]})
    except (OSError, ValueError, KeyError):
        return []
    return sorted(output, key=lambda item: (not item["veto"], item["style"]))


def compare(research: str | Path) -> dict[str, object]:
    """{"rows": [{"name", "styles": {style: {...} | None}, "best"}], "wins": {style: n}} (cached by the registry)."""
    from quant_platform.research.blend import BlendRule, parse_spec
    from quant_platform.research.categories import classify
    from quant_platform.research.daily import ENGINE_VERSION, holding_key, tier
    from quant_platform.research.registry import TrialRegistry, newest

    research = Path(research)
    path = research / "trials.jsonl"
    key = f"{path.stat().st_mtime_ns}" if path.is_file() else "none"
    if _cache.get("key") == key:
        return _cache["value"]                                      # type: ignore[return-value]
    groups: dict[str, dict[str, dict]] = {}
    names: dict[str, str] = {}
    records = TrialRegistry(path).records() if path.is_file() else []
    latest = newest([record for record in records if record.kind == "candidate" and record.period == "recent"
                     and str(record.metrics.get("engine", "")).startswith(ENGINE_VERSION.split("+")[0])])
    families: dict[str, str] = {}
    for record in latest.values():
        try:
            spec = json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"]
            rule = parse_spec(spec)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if isinstance(rule, BlendRule):               # 2026-10-20: a blend whose sleeves all hold one way
            styles_of = {style_of(sleeve.rule.canonical()) for sleeve in rule.sleeves}
            style = styles_of.pop() if len(styles_of) == 1 else None
            group = blend_key(rule) if style else None
        else:
            style = style_of(rule.canonical())
            group = holding_key(rule.canonical())
        if style is None:
            continue
        families.setdefault(group, classify(spec)["family"])
        metrics = record.metrics
        entry = {"trial": record.trial_id, "rule_hash": record.spec_hash, "tier": tier(metrics)[0],
                 "final_value": metrics.get("final_value"), "since_2020": metrics.get("since_2020_final_value"),
                 "drawdown": metrics.get("max_drawdown")}
        known = groups.setdefault(group, {})
        if style not in known or record.trial_id > known[style]["trial"]:
            known[style] = entry
        base = record.spec_name.split("｜持有：")[0]
        for word in HOLD_WORDS:
            base = base.replace(word, "")
        names.setdefault(group, base)
    rows, wins = [], {style: 0 for style in STYLES}
    for group, styles in groups.items():
        if len(styles) < 2:
            continue
        best = max(styles, key=lambda style: styles[style]["final_value"] or 0)
        if len(styles) == len(STYLES):
            wins[best] += 1
        rows.append({"name": names[group], "family": families.get(group, ""),
                     "styles": {style: styles.get(style) for style in STYLES}, "best": best,
                     "top": max(item["final_value"] or 0 for item in styles.values())})
    rows.sort(key=lambda row: -row["top"])
    by_family: dict[str, dict[str, int]] = {}
    for row in rows:
        if all(row["styles"].values()):
            counts = by_family.setdefault(row["family"], {style: 0 for style in STYLES})
            counts[row["best"]] += 1
    value = {"rows": rows, "wins": wins, "complete": sum(1 for row in rows if all(row["styles"].values())),
             "by_family": by_family, "llm": llm_forward(research)}
    _cache.update(key=key, value=value)
    return value
