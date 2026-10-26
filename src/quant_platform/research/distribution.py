"""2026-10-26 (使用者：我要看那個分佈圖，投入的金額是在中間，左邊右邊分別是哪個策略賺比較多錢；x 軸就是你的最後的錢，y 軸是
你的數量；我要看你每個策略是在 x 軸的哪一個點，然後你跑過所有策略的數量他的分佈; 同晚：短歷史賽道你應該要加重權重的去看).

Every rule the engine ran (its latest result, 100% stocks): the money its account holds at the end, on two tracks —
the short one from 2020-10 (NT$300,000 then NT$10,000 a month, first) and the whole one from 2015-06 — counted in bins
on a log axis centred on the money put in (left: lost money; right: made it), with the same money in 0050 marked.
Each rule is also a point under the bars, and the window lists who sits where."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path

TRACKS = {"recent": {"label": "2020-10 起（短賽道）", "value": "since_2020_final_value",
                     "bench": "since_2020_benchmark_final_value", "report": ("since_2020", "strategy")},
          "full": {"label": "2015-06 起（全段）", "value": "final_value", "bench": "benchmark_final_value",
                   "report": ("strategy",)}}
SIDES = {"lost": ("v2", "賠錢（比投入少）"), "behind": ("vput", "賺錢但輸 0050"), "ahead": ("v1", "贏同樣的錢買 0050")}
HALF_BINS = 20


def _contributed(research: Path, rows: list[dict], path: tuple[str, ...]) -> float | None:
    """The money put in on a track, the most common among a sample of the rules' reports."""
    seen: Counter = Counter()
    for row in rows[:40]:
        try:
            report = json.loads((research / "reports" / str(row.get("report_file"))).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        part = report
        for key in path:
            part = part.get(key) or {}
        if part.get("contributed"):
            seen[round(float(part["contributed"]))] += 1
    return float(seen.most_common(1)[0][0]) if seen else None


def side(value: float, invested: float, bench: float) -> str:
    return "lost" if value < invested else ("behind" if value < bench else "ahead")


def edges(values: list[float], invested: float, half: int = HALF_BINS) -> list[float]:
    """Bin edges in 萬元 on a log axis, the money put in exactly in the middle, as far left as right."""
    reach = max(max(values) / invested, invested / max(min(values), 1e-9), 1.5) * 1.04
    return [invested * reach ** (step / half) for step in range(-half, half + 1)]


def view(research: str | Path, rows: list[dict]) -> dict[str, object]:
    """Per track: the money put in, 0050's, every rule's end money and side, the bins, the counts."""
    from quant_platform.research.daily import short_track

    research = Path(research)
    rows = [row for row in rows if not row.get("uses_0050")]
    output = {}
    for key, track in TRACKS.items():
        members = [row for row in rows if row.get(track["value"])
                   and (key == "recent" or not short_track({"start": row.get("start")}))]
        if not members:
            continue
        invested = _contributed(research, members, track["report"])
        bench = Counter(round(row[track["bench"]]) for row in members if row.get(track["bench"])).most_common(1)
        if not invested or not bench:
            continue
        bench_value = float(bench[0][0])
        items = sorted(({"trial": row.get("trial_id"), "name": row.get("name"), "tier": row.get("tier"),
                         "family": (row.get("category") or {}).get("family") or "其他", "spec_hash": row.get("spec_hash"),
                         "value": float(row[track["value"]]),
                         "side": side(float(row[track["value"]]), invested, bench_value)} for row in members),
                       key=lambda item: -item["value"])
        wan = [item["value"] / 10000 for item in items]
        cut = edges(wan, invested / 10000)
        bins = []
        for low, high in zip(cut, cut[1:]):
            inside = [item for item in items if low <= item["value"] / 10000 < high]
            middle = math.sqrt(low * high) * 10000
            counts = Counter(item["side"] for item in inside)
            names = "；".join(f"#{item['trial']} {str(item['name'])[:28]} {item['value'] / 10000:,.0f} 萬" for item in inside[:3])
            bins.append({"low": low, "high": high, "count": len(inside),
                         "css": SIDES[side(middle, invested, bench_value)][0],
                         "title": (f"{low:,.0f}～{high:,.0f} 萬：{len(inside)} 個規則"
                                   + (f"（贏 0050 {counts['ahead']}、輸 0050 {counts['behind']}、賠錢 {counts['lost']}）" if inside else "")
                                   + (f"。錢最多的：{names}" if names else ""))})
        output[key] = {"label": track["label"], "invested": invested, "bench": bench_value, "items": items,
                       "bins": bins, "counts": {name: sum(1 for item in items if item["side"] == name) for name in SIDES},
                       "median": wan[len(wan) // 2], "best": items[0], "worst": items[-1]}
    return output


def charts(found: dict[str, object], with_dots: bool = False) -> dict[str, str]:
    """The dashboard's drawings (bars only; a bar's title names its richest rules) or, ``with_dots``, the window's:
    every rule a point under the bars (mid and wide drawings; the phone's has the bars)."""
    from quant_platform.research.charts import histogram

    output = {}
    for key, track in found.items():
        refs = [{"value": track["invested"] / 10000, "label": f"投入 {track['invested'] / 10000:,.0f} 萬"},
                {"value": track["bench"] / 10000, "label": f"0050 {track['bench'] / 10000:,.0f} 萬"}]
        label = f"{track['label']}每個規則最後有多少錢的分佈"
        if not with_dots:
            output[key] = histogram(track["bins"], refs, label, count_text=lambda value: f"{value:,.0f} 個")
            continue
        dots = [{"value": item["value"] / 10000, "css": SIDES[item["side"]][0],
                 "title": f"#{item['trial']} {str(item['name'])[:40]} {item['value'] / 10000:,.0f} 萬"}
                for item in track["items"]]
        output[key] = (histogram(track["bins"], refs, label, count_text=lambda value: f"{value:,.0f} 個", sizes=("narrow",))
                       + histogram(track["bins"], refs, label, dots, count_text=lambda value: f"{value:,.0f} 個",
                                   sizes=("mid", "wide")))
    return output
