"""2026-10-23 (使用者：績效的成長幅度有沒有個線型圖，從什麼時候開始、我現在這個數字、到底進步了多少、到底有沒有在成長；
研究總覽策略數怎麼會只有這麼少，不是已經跑過破億個嗎): how the research has grown.

- ``timeline``: the best backtest as it was known at each hour — every rule's newest run up to then (a newer engine
  replaces an older one's result, so an engine that removed a flaw shows as a drop), the best of the ordinary T0
  candidates, of the high-risk ones and of every passing rule (T1 too); when the best changed; each engine's first run.
- ``search_scale`` / ``searches``: the settings searched — the large searches' rounds added up, the biggest blend round
  (each round holds the earlier rounds' blends) — and the rules run through the full engine, over time.
- ``forward``: the accounts of the month's list day by day since its start, as cash if everything were sold that day.
- ``families``: the best rule of each family now.
- 2026-10-23 (使用者：短賽道績效呢): the same rules from 2020-10-26 on (a fresh 30 萬＋每月 1 萬 account, the second
  check every rule passes), and the short-history track (rules on data known only from 2021, graded from their start)
  kept apart from the 2015 lines — their end values start years later and are not comparable.

Kept until the files they read change (the forward records are 190 MB)."""

from __future__ import annotations

import json
import re
import statistics
from datetime import date, datetime
from html import escape
from pathlib import Path

_CACHE: dict[str, object] = {}
GROUPS = (("any", "全部過門檻的最好（含 T1、高風險）"), ("risky", "T0 候選（高風險）最好"), ("t0", "一般 T0 候選最好"))
ANY_TIERS = ("T0", "T0（高風險）", "T0 候選", "T0 候選（高風險）", "T1")
PASSING = ("T0", "T0（高風險）", "T0 候選", "T0 候選（高風險）")
_STAMP = re.compile(r"(\d{8})-(\d{6})")


def _signature(*paths: Path) -> tuple:
    out = []
    for path in paths:
        if path.is_dir():
            out.append(tuple((item.name, item.stat().st_mtime_ns) for item in sorted(path.iterdir())))
        elif path.is_file():
            out.append((path.stat().st_mtime_ns, path.stat().st_size))
        else:
            out.append(0)
    return tuple(out)


def _cached(name: str, key: tuple, build):
    entry = _CACHE.get(name)
    if entry and entry[0] == key:
        return entry[1]
    value = build()
    _CACHE[name] = (key, value)
    return value


def _when(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=None)     # the registry's times are Taipei's


def _events(research: Path) -> list[dict[str, object]]:
    """The searches: each large-search round (its folder's time) and each blend round (its file's time)."""
    rows = []
    for path in sorted((research / "scans").glob("*/summary.json")) if (research / "scans").is_dir() else []:
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        found = _STAMP.search(path.parent.name)
        at = (datetime.strptime("".join(found.groups()), "%Y%m%d%H%M%S") if found
              else datetime.fromtimestamp(path.stat().st_mtime))
        rows.append({"at": at, "kind": "scan", "candidates": int(summary.get("candidates") or 0), "name": path.parent.name})
    for path in sorted((research / "blend_scan").glob("finalists-*.json")) if (research / "blend_scan").is_dir() else []:
        try:
            summary = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows.append({"at": datetime.fromtimestamp(path.stat().st_mtime), "kind": "blend",
                     "candidates": int(summary.get("candidates") or 0), "pool": summary.get("pool"), "name": path.name})
    return sorted(rows, key=lambda row: row["at"])


def _trial_count(research: Path) -> int:
    path = research / "trials.jsonl"
    if not path.is_file():
        return 0
    with path.open("rb") as handle:
        return sum(1 for line in handle if line.strip())


def search_scale(research: str | Path) -> dict[str, int]:
    """The settings searched so far: the large searches' rounds added up (each screened its own settings), plus the
    biggest blend round (every round searches all pairs and threes of the pool then, so it holds the earlier ones)."""
    research = Path(research)
    key = _signature(research / "scans", research / "blend_scan", research / "trials.jsonl")

    def build():
        events = _events(research)
        scans = [row for row in events if row["kind"] == "scan"]
        blends = [row for row in events if row["kind"] == "blend"]
        biggest = max((row["candidates"] for row in blends), default=0)
        return {"scans": sum(row["candidates"] for row in scans), "scan_rounds": len(scans), "blends": biggest,
                "blend_rounds": len({(row["pool"], row["candidates"]) for row in blends}),
                "total": sum(row["candidates"] for row in scans) + biggest, "trials": _trial_count(research)}
    return _cached(f"scale:{research.resolve()}", key, build)


def searches(research: Path) -> list[dict[str, object]]:
    """After each search round: the large searches added up, the biggest blend round, their total."""
    scans = blends = 0
    out = []
    for row in _events(research):
        if row["kind"] == "scan":
            scans += row["candidates"]
        else:
            blends = max(blends, row["candidates"])
        out.append({"at": row["at"], "scans": scans, "blends": blends, "total": scans + blends, "kind": row["kind"],
                    "added": row["candidates"], "name": row["name"]})
    return out


def timeline(research: str | Path, rows: dict[str, dict] | None = None) -> dict[str, object]:
    """The best backtest known at the end of each hour with a new run (2015-06～2026-09, 30 萬＋每月 1 萬, 100% stocks);
    ``rows``: spec hash → the pool's row (which rules hold 0050), the pool's by default."""
    from quant_platform.research.daily import short_track, tier
    from quant_platform.research.registry import TrialRegistry, engine_order

    research = Path(research)
    if rows is None:
        from quant_platform.research.pool import pool_view

        rows = {row["spec_hash"]: row for row in pool_view(research, basis="seed")["daily"]}
    records = [record for record in TrialRegistry(research / "trials.jsonl").records()
               if record.kind == "candidate" and record.period == "recent" and record.spec_hash in rows
               and not rows[record.spec_hash].get("uses_0050") and (record.metrics or {}).get("final_value")]
    records.sort(key=lambda record: (record.created_at, record.trial_id))
    current: dict[str, tuple] = {}
    points: list[dict[str, object]] = []
    changes: list[dict[str, object]] = []
    engines: dict[str, datetime] = {}
    holders: dict[str, object] = {key: None for key, _label in GROUPS}
    benchmark = recent_benchmark = short_benchmark = None

    def flush(at: datetime, runs: int) -> None:
        best: dict[str, tuple | None] = {key: None for key, _label in GROUPS}
        passing = short_passing = 0
        recent: dict[str, float | None] = {"any": None, "t0": None}
        short_best = None
        for _order, record, grade in current.values():
            value = float(record.metrics["final_value"])
            if short_track(record.metrics):              # from 2021: its own line, not the 2015 ones
                short_best = value if short_best is None else max(short_best, value)
                short_passing += grade in PASSING
                continue
            since = record.metrics.get("since_2020_final_value")
            if since and grade in ANY_TIERS:
                recent["any"] = since if recent["any"] is None else max(recent["any"], since)
            if since and grade in ("T0", "T0 候選"):
                recent["t0"] = since if recent["t0"] is None else max(recent["t0"], since)
            keys = (["any"] if grade in ANY_TIERS else []) + (["risky"] if "高風險" in grade and grade in PASSING else []) \
                + (["t0"] if grade in ("T0", "T0 候選") else [])
            passing += grade in PASSING
            for key in keys:
                if best[key] is None or value > best[key][0]:
                    best[key] = (value, record, grade)
        point = {"at": at, "passing": passing, "rules": len(current), "runs": runs, "recent_any": recent["any"],
                 "recent_t0": recent["t0"], "short": short_best, "short_passing": short_passing}
        for key, _label in GROUPS:
            point[key] = best[key][0] if best[key] else None
            before = holders[key]
            if best[key] and key in ("any", "t0") and (before is None or abs(best[key][0] - before[0]) >= 0.005 * before[0]
                                                       or best[key][1].spec_hash != before[1].spec_hash):
                changes.append({"at": at, "group": key, "trial": best[key][1].trial_id, "name": best[key][1].spec_name,
                                "spec_hash": best[key][1].spec_hash, "grade": best[key][2], "value": best[key][0],
                                "previous": before[0] if before else None,
                                "engine": str(best[key][1].metrics.get("engine") or "").split("+")[0]})
            if best[key]:
                holders[key] = best[key]
        points.append(point)

    hour, runs = None, 0
    for record in records:
        at = _when(record.created_at)
        stamp = at.replace(minute=0, second=0, microsecond=0)
        if hour is not None and stamp != hour:
            flush(hour, runs)
        hour = stamp
        runs += 1
        engine = str(record.metrics.get("engine") or "").split("+")[0]
        engines.setdefault(engine, at)
        old = current.get(record.spec_hash)
        order = engine_order(record)
        if old is None or order >= old[0]:
            current[record.spec_hash] = (order, record, tier(record.metrics)[0])
        if short_track(record.metrics):
            short_benchmark = float(record.metrics.get("benchmark_final_value") or 0) or short_benchmark
        else:
            benchmark = float(record.metrics.get("benchmark_final_value") or 0) or benchmark
            recent_benchmark = float(record.metrics.get("since_2020_benchmark_final_value") or 0) or recent_benchmark
    if hour is not None:
        flush(hour, runs)
    return {"points": points, "changes": changes, "benchmark": benchmark, "recent_benchmark": recent_benchmark,
            "short_benchmark": short_benchmark,
            "engines": sorted(({"engine": name, "at": at} for name, at in engines.items()), key=lambda item: item["at"])}


def forward(research: str | Path, start: str, named: dict[str, str] | None = None) -> dict[str, object]:
    """The month's accounts (started ``start``) day by day, each as the cash if everything were sold at that close
    (fees, tax, slippage), against the same money in 0050; ``named``: rule hash → label, those accounts' own lines."""
    from quant_platform.research.stock_forward import StockForwardTracker, sell_all

    tracker = StockForwardTracker(research)
    live = {item["rule_hash"] for item in tracker.tracked() if not item.get("stopped") and item.get("since") == start}
    by_day: dict[str, dict[str, dict]] = {}
    for record in tracker.records():
        if record["rule_hash"] in live and str(record.get("since")) == start:
            by_day.setdefault(record["date"], {})[record["rule_hash"]] = sell_all(record, tracker._costs)
    series = []
    for day in sorted(by_day):
        cashed = by_day[day]
        ahead = sorted(item["ahead"] for item in cashed.values())
        gains = sorted(item["gain"] for item in cashed.values())
        count = len(ahead)
        row = {"date": day, "accounts": count, "ahead_share": sum(value > 0 for value in ahead) / count,
               "median_ahead": statistics.median(ahead), "p25_gain": gains[int(count * 0.25)],
               "p75_gain": gains[min(count - 1, int(count * 0.75))], "median_gain": statistics.median(gains),
               "best_gain": gains[-1], "worst_gain": gains[0],
               "bench_gain": statistics.median(item["bench_gain"] for item in cashed.values())}
        for rule_hash in named or {}:
            row[rule_hash] = cashed[rule_hash]["gain"] if rule_hash in cashed else None
        series.append(row)
    return {"series": series, "start": start, "accounts": len(live), "named": named or {}}


def short_rules(research: Path, summary: dict[str, dict]) -> list[dict[str, object]]:
    """The short-history track's rules (each newest run), richest first, with the grade's reason and the forward."""
    from quant_platform.research.daily import short_track, tier
    from quant_platform.research.registry import TrialRegistry, newest

    latest = newest(TrialRegistry(research / "trials.jsonl").records(),
                    lambda record: record.kind == "candidate" and record.period == "recent"
                    and short_track(record.metrics or {}))
    out = []
    for record in latest.values():
        metrics = record.metrics
        grade, why = tier(metrics)
        cashed = (summary.get(record.spec_hash) or {}).get("sell_all") or {}
        out.append({"trial": record.trial_id, "spec_hash": record.spec_hash, "name": record.spec_name, "tier": grade,
                    "why": why, "start": str(metrics.get("start"))[:10], "final_value": metrics.get("final_value"),
                    "benchmark": metrics.get("benchmark_final_value"), "max_drawdown": metrics.get("max_drawdown"),
                    "benchmark_drawdown": metrics.get("benchmark_max_drawdown"),
                    "win_1y": ((metrics.get("windows") or {}).get("1y") or {}).get("win_ratio"),
                    "forward_gain": cashed.get("gain"), "forward_ahead": cashed.get("ahead")})
    return sorted(out, key=lambda row: -(row["final_value"] or 0))


def recent_best(pool_rows: list[dict], count: int = 15) -> list[dict[str, object]]:
    """The richest passing rules from 2020-10-26 (each rule's second check: a fresh account then), 100% stocks."""
    rows = [row for row in pool_rows if not row.get("uses_0050") and row.get("tier") in ANY_TIERS
            and row.get("since_2020_final_value")]
    return sorted(rows, key=lambda row: -row["since_2020_final_value"])[:count]


def toward_t0(chained: dict[str, dict], pool_rows: list[dict], summary: dict[str, dict], count: int = 15) -> dict[str, object]:
    """How far the monthly lists' rules are from T0 (使用者 2026-10-23：T0 為什麼又要 12/29): the chained forward
    sessions so far, how many are not behind 0050, the passing rules nearest to it (most ahead first)."""
    from quant_platform.research.daily import FORWARD_SESSIONS

    rows = {row["spec_hash"]: row for row in pool_rows}
    candidates = [(key, value) for key, value in chained.items()
                  if (rows.get(key) or {}).get("tier") in PASSING and not (rows.get(key) or {}).get("uses_0050")]
    sessions = max((value["sessions"] for _key, value in candidates), default=0)
    nearest = sorted(candidates, key=lambda item: (-item[1]["sessions"], -item[1]["excess"]))[:count]
    return {"sessions": sessions, "needed": FORWARD_SESSIONS, "rules": len(candidates),
            "not_behind": sum(value["excess"] >= 0 for _key, value in candidates),
            "nearest": [{"spec_hash": key, "name": rows[key]["name"], "tier": rows[key]["tier"],
                         "final_value": rows[key].get("final_value"), **value,
                         "gain": ((summary.get(key) or {}).get("sell_all") or {}).get("gain")} for key, value in nearest]}


def families(pool_rows: list[dict], summary: dict[str, dict]) -> list[dict[str, object]]:
    """Each family's best (100% stocks; categories.best_of — the pool's and the roles page's pick), how many rules it
    has and how many passed, its forward account."""
    groups: dict[str, list[dict]] = {}
    for row in pool_rows:
        if not row.get("uses_0050") and row.get("final_value"):
            groups.setdefault((row.get("category") or {}).get("family") or "其他", []).append(row)
    out = []
    from quant_platform.research.categories import best_of

    for family, members in groups.items():
        best = best_of(members)
        cashed = (summary.get(best["spec_hash"]) or {}).get("sell_all") or {}
        out.append({"family": family, "rules": len(members), "passing": sum(row["tier"] in PASSING for row in members),
                    "name": best["name"], "spec_hash": best["spec_hash"], "tier": best["tier"],
                    "final_value": best["final_value"], "benchmark": best.get("benchmark_final_value"),
                    "max_drawdown": best.get("max_drawdown"), "forward_gain": cashed.get("gain"),
                    "forward_ahead": cashed.get("ahead")})
    return sorted(out, key=lambda row: -row["final_value"])


def daily_table(points: list[dict], search_rows: list[dict], forward_rows: list[dict]) -> list[dict[str, object]]:
    """One row per day: the best at the day's end, the rules passing, the runs that day, the settings searched by then
    and the month's forward accounts that day."""
    by_day: dict[date, dict] = {}
    for point in points:
        by_day[point["at"].date()] = point
    days = sorted(by_day)
    out, before = [], None
    for day in days:
        point = by_day[day]
        searched = [row["total"] for row in search_rows if row["at"].date() <= day]
        fwd = next((row for row in forward_rows if row["date"] == day.isoformat()), None)
        out.append({"date": day, "any": point["any"], "risky": point["risky"], "t0": point["t0"],
                    "passing": point["passing"], "rules": point["rules"],
                    "runs": point["runs"] - (before["runs"] if before else 0), "searched": searched[-1] if searched else 0,
                    "change_any": (point["any"] - before["any"]) if before and point["any"] and before["any"] else None,
                    "change_t0": (point["t0"] - before["t0"]) if before and point["t0"] and before["t0"] else None,
                    "forward": fwd})
        before = point
    return out[::-1]


def view(research: str | Path, start: str) -> dict[str, object]:
    """Everything the progress page shows."""
    from quant_platform.research.downside import table as downside_table
    from quant_platform.research.pool import pool_view
    from quant_platform.research.stock_forward import StockForwardTracker

    research = Path(research)
    forward_dir = research / "forward" / "stocks"
    key = _signature(research / "trials.jsonl", research / "scans", research / "blend_scan",
                     forward_dir / "log.jsonl", forward_dir / "tracked.json", research / "rolling") + (start,)

    def build():
        line = timeline(research)
        frontier = [row for row in downside_table(research) if row.get("frontier")]
        frontier.sort(key=lambda row: -(row.get("final_value") or 0))
        named = {}
        if frontier:                 # the frontier's richest, and its richest that falls at most 1 point more than 0050
            named[frontier[0]["rule_hash"]] = f"#{frontier[0]['trial']}（前緣錢最多）"
            steady = [row for row in frontier if row.get("worst_fall") and row["worst_fall"]["gap"] >= -0.01]
            if steady and steady[0]["rule_hash"] not in named:
                named[steady[0]["rule_hash"]] = f"#{steady[0]['trial']}（跌市多跌不到 1 點裡錢最多）"
        fwd = forward(research, start, named)
        summary = {row["rule_hash"]: row for row in StockForwardTracker(research).summary()}
        search_rows = searches(research)
        pool = pool_view(research, basis="seed")
        latest = fwd["series"][-1] if fwd["series"] else None
        frontier_rows = []
        for row in frontier:
            cashed = (summary.get(row["rule_hash"]) or {}).get("sell_all") or {}
            frontier_rows.append({**row, "forward_gain": cashed.get("gain"), "forward_ahead": cashed.get("ahead"),
                                  "realized": (summary.get(row["rule_hash"]) or {}).get("realized"),
                                  "sessions": (summary.get(row["rule_hash"]) or {}).get("sessions")})
        return {"timeline": line, "searches": search_rows, "scale": search_scale(research), "forward": fwd,
                "short": short_rules(research, summary), "recent": recent_best(pool["daily"]),
                "t0": toward_t0(StockForwardTracker(research).chained(date.today()), pool["daily"], summary),
                "latest_forward": latest, "frontier": frontier_rows,
                "families": families(pool["daily"], summary),
                "days": daily_table(line["points"], search_rows, fwd["series"])}
    return _cached(f"view:{research.resolve()}", key, build)


# --- charts (inline SVG, the page's colours) ------------------------------------------------------------------------

def _ticks(low: float, high: float, count: int = 5) -> list[float]:
    import math

    span = high - low or abs(high) or 1.0
    raw = span / count
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(m * magnitude for m in (1, 2, 2.5, 5, 10) if m * magnitude >= raw)
    first = math.floor(low / step) * step
    return [first + step * index for index in range(int((high - first) / step) + 2) if first + step * index <= high + step * 0.01]


def chart(points: list[dict], x_key: str, lines: list[dict], y_text, width: int = 720, height: int = 280,
          band: tuple[str, str] | None = None, guides: list[tuple[float, str]] = (), marks: list[tuple[object, str]] = (),
          step: bool = False, label: str = "") -> str:
    """An SVG line chart: ``lines`` = [{key, label, css, dashed}], ``x_key`` a datetime or ISO date per point, ``y_text``
    formats an axis value; ``band`` = (low key, high key) shaded; ``guides`` horizontal lines; ``marks`` vertical ones
    (x, text); ``step``: a best-so-far line (flat until the next change). Hover shows each point's value."""
    left, right, top, bottom = 64, 16, 14, 30
    xs = [_x(point[x_key]) for point in points]
    if not xs:
        return ""
    values = [point[line["key"]] for point in points for line in lines if point.get(line["key"]) is not None]
    if band:
        values += [point[key] for point in points for key in band if point.get(key) is not None]
    values += [value for value, _text in guides]
    if not values:
        return ""
    low, high = min(values), max(values)
    pad = (high - low) * 0.06 or abs(high) * 0.1 or 1.0
    low, high = low - pad, high + pad
    x0, x1 = min(xs), max(xs)
    x1 = x1 if x1 > x0 else x0 + 1

    def sx(value: float) -> float:
        return left + (value - x0) / (x1 - x0) * (width - left - right)

    def sy(value: float) -> float:
        return top + (high - value) / (high - low) * (height - top - bottom)

    parts = [f'<svg class="progress-chart" viewBox="0 0 {width} {height}" role="img" aria-label="{escape(label)}">']
    for value in _ticks(low, high):
        if low <= value <= high:
            parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{sy(value):.1f}" y2="{sy(value):.1f}"/>'
                         f'<text x="{left - 6}" y="{sy(value) + 4:.1f}" text-anchor="end">{escape(y_text(value))}</text>')
    seen = set()
    for index in sorted({0, len(xs) - 1, *range(0, len(xs), max(1, len(xs) // 6))}):
        day = _day(points[index][x_key])
        if day in seen:
            continue
        seen.add(day)
        parts.append(f'<text x="{sx(xs[index]):.1f}" y="{height - 10}" text-anchor="middle">{day}</text>')
    if band:
        upper = [(sx(x), sy(point[band[1]])) for x, point in zip(xs, points) if point.get(band[1]) is not None]
        lower = [(sx(x), sy(point[band[0]])) for x, point in zip(xs, points) if point.get(band[0]) is not None]
        if upper and lower:
            shape = " ".join(f"{x:.1f},{y:.1f}" for x, y in upper + lower[::-1])
            parts.append(f'<polygon class="band" points="{shape}"/>')
    for value, text in guides:
        parts.append(f'<line class="guide" x1="{left}" x2="{width - right}" y1="{sy(value):.1f}" y2="{sy(value):.1f}"/>'
                     f'<text class="guide-text" x="{width - right - 4}" y="{sy(value) - 4:.1f}" text-anchor="end">{escape(text)}</text>')
    for index, (at, text) in enumerate(marks):          # labels on two rows so close ones do not overlap
        x = sx(_x(at))
        if left <= x <= width - right:
            parts.append(f'<line class="mark" x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{height - bottom}"/>'
                         f'<text class="mark-text" x="{x + 3:.1f}" y="{top + 10 + 12 * (index % 2)}">{escape(text)}</text>')
    for line in lines:
        coords, last = [], None
        for x, point in zip(xs, points):
            value = point.get(line["key"])
            if value is None:
                continue
            if step and last is not None:
                coords.append(f"{sx(x):.1f},{sy(last):.1f}")
            coords.append(f"{sx(x):.1f},{sy(value):.1f}")
            last = value
        if coords:
            dashed = ' stroke-dasharray="5 4"' if line.get("dashed") else ""
            wide = " wide" if line.get("wide") else ""          # under the others: still seen where they coincide
            parts.append(f'<polyline class="{line["css"]}{wide}"{dashed} points="{" ".join(coords)}"/>')
        for x, point in zip(xs, points):
            value = point.get(line["key"])
            if value is not None:
                parts.append(f'<circle class="dot {line["css"]}" cx="{sx(x):.1f}" cy="{sy(value):.1f}" r="5">'
                             f'<title>{escape(_day(point[x_key], True))} {escape(line["label"])}：{escape(y_text(value))}</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


def _x(value) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day).timestamp()
    return datetime.fromisoformat(str(value)).timestamp()


def _day(value, full: bool = False) -> str:
    if isinstance(value, datetime):
        return value.strftime("%m/%d %H:%M" if full else "%m/%d")
    if isinstance(value, date):
        return value.strftime("%m/%d")
    text = str(value)
    return f"{text[5:7]}/{text[8:10]}"
