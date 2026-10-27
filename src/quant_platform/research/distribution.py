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
    """0050 first: an account ahead of 0050 is "ahead" even when 0050 lost money too (the forward months)."""
    return "ahead" if value >= bench else ("lost" if value < invested else "behind")


def _quantile(values: list[float], share: float) -> float:
    position = (len(values) - 1) * share
    low, high = math.floor(position), math.ceil(position)
    return values[low] + (values[high] - values[low]) * (position - low)


ROW_DOTS = 60       # a category with at most this many rules shows each one as a point; a larger one its own small spread


def _bins(items: list[dict], cut: list[float], invested: float, bench: float, noun: str) -> list[dict]:
    bins = []
    for low, high in zip(cut, cut[1:]):
        inside = [item for item in items if low <= item["value"] / 10000 < high]
        middle = math.sqrt(low * high) * 10000
        counts = Counter(item["side"] for item in inside)
        names = "；".join(f"{_who(item)} {str(item['name'])[:28]} {item['value'] / 10000:,.0f} 萬" for item in inside[:3])
        bins.append({"low": low, "high": high, "count": len(inside),
                     "css": SIDES[side(middle, invested, bench)][0],
                     "title": (f"{low:,.0f}～{high:,.0f} 萬：{len(inside)} {noun}"
                               + (f"（贏 0050 {counts['ahead']}、輸 0050 {counts['behind']}、賠錢 {counts['lost']}）" if inside else "")
                               + (f"。錢最多的：{names}" if names else ""))})
    return bins


def _who(item: dict) -> str:
    return f"#{item['trial']}" if item.get("trial") is not None else "・"


def family_rows(items: list[dict], cut: list[float]) -> list[dict]:
    """2026-10-27 (使用者：深度學習、RL、多因子、趨勢動能這些類別的分佈在哪一個位置，連標都沒標): per category (in
    FAMILY_ORDER), its rules' end money — how many, the middle half and the median (萬元), how many on each side, the
    richest — and its counts in the bins ``cut`` (萬元)."""
    from bisect import bisect_right

    from quant_platform.research.categories import FAMILY_ORDER

    groups: dict[str, list[dict]] = {}
    for item in items:
        groups.setdefault(item["family"], []).append(item)
    order = {name: index for index, name in enumerate(FAMILY_ORDER)}
    output = []
    for family, members in sorted(groups.items(), key=lambda pair: (order.get(pair[0], len(order)), pair[0])):
        wan = sorted(item["value"] / 10000 for item in members)
        counts = [0] * (len(cut) - 1)
        for value in wan:
            counts[min(max(bisect_right(cut, value) - 1, 0), len(counts) - 1)] += 1
        sides = Counter(item["side"] for item in members)
        output.append({"family": family, "count": len(members), "q1": _quantile(wan, .25), "median": _quantile(wan, .5),
                       "q3": _quantile(wan, .75), "bins": counts, "lost": sides["lost"], "behind": sides["behind"],
                       "ahead": sides["ahead"], "best": max(members, key=lambda item: item["value"]), "items": members})
    return output


def _track(label: str, invested: float, bench: float, items: list[dict], noun: str = "個規則", least: float = 1.5) -> dict:
    """A track's drawing data: the bins (萬元, the money put in in the middle), every item's side, each category's row."""
    items = sorted(items, key=lambda item: -item["value"])
    for item in items:
        item["side"] = side(item["value"], invested, bench)
    wan = [item["value"] / 10000 for item in items]
    cut = edges(wan, invested / 10000, least=least)
    return {"label": label, "invested": invested, "bench": bench, "items": items, "noun": noun, "cut": cut,
            "bins": _bins(items, cut, invested, bench, noun), "families": family_rows(items, cut),
            "counts": {name: sum(1 for item in items if item["side"] == name) for name in SIDES},
            "median": wan[len(wan) // 2], "best": items[0], "worst": items[-1]}


def focus(track: dict, family: str) -> dict | None:
    """One category of a track on the track's own axis: its bins, its points, the rest left out."""
    items = [item for item in track["items"] if item["family"] == family]
    if not items:
        return None
    wan = [item["value"] / 10000 for item in items]
    return {**track, "label": f"{track['label']}・{family}", "family": family, "items": items, "families": [],
            "bins": _bins(items, track["cut"], track["invested"], track["bench"], track["noun"]),
            "counts": {name: sum(1 for item in items if item["side"] == name) for name in SIDES},
            "median": wan[len(wan) // 2], "best": items[0], "worst": items[-1]}


def edges(values: list[float], invested: float, half: int = HALF_BINS, least: float = 1.5) -> list[float]:
    """Bin edges in 萬元 on a log axis, the money put in exactly in the middle, as far left as right (at least ``least``
    times either way)."""
    reach = max(max(values) / invested, invested / max(min(values), 1e-9), least) * 1.04
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
        items = [{"trial": row.get("trial_id"), "name": row.get("name"), "tier": row.get("tier"),
                  "family": (row.get("category") or {}).get("family") or "其他", "spec_hash": row.get("spec_hash"),
                  "value": float(row[track["value"]])} for row in members]
        output[key] = _track(track["label"], invested, float(bench[0][0]), items)
    return output


FORWARD_LABEL = "前向帳戶"


def forward_view(rows: list[dict], summary: list[dict], start: str) -> dict[str, object]:
    """2026-10-27 (使用者：獲益的那些分佈圖我也要看): the forward accounts opened from ``start`` that are still watched —
    each one's money if everything were sold at its last session (the tracker's sell_all), with the same cash flow in 0050
    — on the same kind of drawing, each category marked."""
    by_hash = {row.get("spec_hash"): row for row in rows}
    live = [row for row in summary if not row.get("stopped") and row.get("since") == start and row.get("sell_all")
            and row.get("contributed")]
    if not live:
        return {}
    invested = Counter(round(float(row["contributed"])) for row in live).most_common(1)[0][0]
    bench = Counter(round(float(row["sell_all"]["bench_back"])) for row in live
                    if round(float(row["contributed"])) == invested).most_common(1)[0][0]
    items = []
    for row in live:
        if round(float(row["contributed"])) != invested:
            continue
        pool = by_hash.get(row["rule_hash"]) or {}
        items.append({"trial": pool.get("trial_id"), "name": pool.get("name") or row.get("name"), "tier": pool.get("tier"),
                      "family": (pool.get("category") or {}).get("family") or ("組合帳戶" if row.get("kind") == "blend" else "其他"),
                      "spec_hash": row["rule_hash"], "value": float(row["sell_all"]["cash_back"])})
    last = max(row.get("date") or "" for row in live)
    found = _track(f"{FORWARD_LABEL}（{start[5:7].lstrip('0')}/{start[8:].lstrip('0')} 起、{last[5:7].lstrip('0')}/{last[8:].lstrip('0')} 全部賣掉）",
                   float(invested), float(bench), items, "個帳戶", least=1.02)   # two months: a few percent either way
    found.update(start=start, end=last)
    return {"forward": found}


def _rows(key: str, track: dict) -> list[dict]:
    noun = track.get("noun", "個規則")
    output = []
    for row in track.get("families") or []:
        best = row["best"]
        digits = 1 if track["invested"] < 1_000_000 else 0          # the forward accounts all sit near 32 萬
        title = (f"{row['family']}：{row['count']:,} {noun}，中位數 {row['median']:,.{digits}f} 萬、中間一半 "
                 f"{row['q1']:,.{digits}f}～{row['q3']:,.{digits}f} 萬；贏 0050 {row['ahead']:,}、賺錢但輸 0050 {row['behind']:,}、"
                 f"賠錢 {row['lost']:,}；最多 {_who(best)} {str(best['name'])[:30]} {best['value'] / 10000:,.{digits}f} 萬"
                 "（點一下看這一類自己的分佈）")
        dots = ([{"value": item["value"] / 10000, "css": SIDES[item["side"]][0],
                  "title": f"{_who(item)} {str(item['name'])[:40]} {item['value'] / 10000:,.0f} 萬"} for item in row["items"]]
                if row["count"] <= ROW_DOTS else None)
        output.append({"label": row["family"], "count": row["count"], "title": title, "q1": row["q1"],
                       "median": row["median"], "q3": row["q3"], "bins": row["bins"], "dots": dots,
                       "data": {"spread-family": row["family"], "spread-track": key}})
    return output


def charts(found: dict[str, object], with_dots: bool = False) -> dict[str, str]:
    """The dashboard's drawings (bars, and a row a category under them; a bar's title names its richest rules) or,
    ``with_dots``, the window's: every rule also a point under the bars (mid and wide drawings; the phone's has the
    bars)."""
    from quant_platform.research.charts import histogram

    output = {}
    for key, track in found.items():
        digits = 1 if track["invested"] < 1_000_000 else 0      # the forward accounts: 32 萬 against 31.1 萬
        refs = [{"value": track["invested"] / 10000, "label": f"投入 {track['invested'] / 10000:,.{digits}f} 萬"},
                {"value": track["bench"] / 10000, "label": f"0050 {track['bench'] / 10000:,.{digits}f} 萬"}]
        label = f"{track['label']}每個{track.get('noun', '個規則')[1:]}最後有多少錢的分佈"
        rows = _rows(key, track)
        if not with_dots:
            output[key] = histogram(track["bins"], refs, label, count_text=lambda value: f"{value:,.0f} 個", rows=rows)
            continue
        dots = [{"value": item["value"] / 10000, "css": SIDES[item["side"]][0],
                 "title": f"{_who(item)} {str(item['name'])[:40]} {item['value'] / 10000:,.0f} 萬"}
                for item in track["items"]]
        output[key] = (histogram(track["bins"], refs, label, count_text=lambda value: f"{value:,.0f} 個", sizes=("narrow",),
                                 rows=rows)
                       + histogram(track["bins"], refs, label, dots, count_text=lambda value: f"{value:,.0f} 個",
                                   sizes=("mid", "wide"), rows=rows))
    return output


def unmarked(research: str | Path) -> list[str]:
    """The daily check (使用者 2026-10-27：這些類別的分佈在哪一個位置，連標都沒標): every category of the dashboard's
    drawings has its row."""
    from html import escape

    from quant_platform.research.pool import pool_view

    found = view(research, pool_view(research, basis="seed")["daily"])
    drawn = charts(found)
    output = []
    for key, track in found.items():
        for name in sorted({item["family"] for item in track["items"]}):
            if f'data-spread-family="{escape(name)}"' not in drawn[key]:
                output.append(f"{track['label']}：{name}")
    return output


# --- 2026-10-26: the full-period search's settings (research/scan_full.py), from its samples ------------------------
SHORT_START = "2020-10-26"     # the engine's short account opens on the first session from here (its since_2020 part)
SPREAD_FILE = "spread.npz"


def account_ends(returns, days, start_index: int):
    """End money of NT$300,000 put in at the close of ``start_index`` and NT$10,000 on each later month's first
    session from the 5th, every row of daily ``returns`` (row × session; column t is session t to t+1); and the money
    put in."""
    import numpy as np

    flows = {start_index: 300_000.0}
    month = (days[start_index].year, days[start_index].month)
    for index in range(start_index + 1, len(days) - 1):
        day = days[index]
        if (day.year, day.month) != month and day.day >= 5:
            flows[index] = 10_000.0
            month = (day.year, day.month)
    growth = np.cumprod(1 + np.asarray(returns, dtype=np.float64), axis=-1)
    end = growth[..., -1]
    total = np.zeros(growth.shape[:-1])
    for index, amount in flows.items():
        before = growth[..., index - 1] if index else 1.0
        total = total + amount * end / before
    return total, sum(flows.values())


def update_spread(out: Path) -> dict[str, object]:
    """The end money of every sampled setting of a search folder, both accounts, kept in ``spread.npz`` (only the
    sample files not read yet are read)."""
    import numpy as np

    from quant_platform.research.scan import load_arrays

    out = Path(out)
    arrays_folder = out / "arrays" if (out / "arrays").is_dir() else out.with_name(out.name[:-1] + "a") / "arrays"
    arrays = load_arrays(arrays_folder)
    days = arrays.days
    short_index = next(index for index, day in enumerate(days) if day.isoformat() >= SHORT_START)
    cache = out / SPREAD_FILE
    seen: set[str] = set()
    values = {"recent": np.zeros(0), "full": np.zeros(0)}
    if cache.is_file():
        stored = np.load(cache, allow_pickle=False)
        values = {"recent": stored["recent"], "full": stored["full"]}
        seen = set(str(name) for name in stored["files"])
    fresh = sorted(path for path in out.glob("sample-*.excess.npy") if path.name not in seen)
    added = {"recent": [], "full": []}
    bench = arrays.bench
    for path in fresh:
        excess = np.load(path).astype(np.float64)
        if not len(excess):
            continue
        returns = excess + bench[None, : excess.shape[1]]
        added["full"].append(account_ends(returns, days, 0)[0])
        added["recent"].append(account_ends(returns, days, short_index)[0])
    for key in values:
        if added[key]:
            values[key] = np.concatenate([values[key], *added[key]])
    names = np.array(sorted(seen | {path.name for path in fresh}))
    full_bench, full_in = account_ends(bench[None, :-1], days, 0)
    recent_bench, recent_in = account_ends(bench[None, :-1], days, short_index)
    np.savez(cache, recent=values["recent"], full=values["full"], files=names,
             bench=np.array([recent_bench[0], full_bench[0]]), invested=np.array([recent_in, full_in]),
             end=np.array([days[-1].isoformat()]))
    return {"samples": int(len(values["full"])), "files": len(names)}


def search_view(research: str | Path) -> dict[str, object] | None:
    """The search's sampled end money per account (from ``spread.npz``), binned like the rules', counts scaled up by
    the sampling (every SAMPLE_EVERY-th setting)."""
    import numpy as np

    from quant_platform.research.scan_large import SAMPLE_EVERY

    folders = sorted((Path(research) / "scans").glob("scan-full-*")) if (Path(research) / "scans").is_dir() else []
    caches = [folder / SPREAD_FILE for folder in folders if (folder / SPREAD_FILE).is_file()]
    if not caches:
        return None
    loaded = [np.load(path, allow_pickle=False) for path in caches]
    output = {}
    for position, (key, label) in enumerate((("recent", "2020-10 起（短賽道）"), ("full", "2015-06 起（全段）"))):
        values = np.concatenate([item[key] for item in loaded])
        if not len(values):
            continue
        invested, bench = float(loaded[0]["invested"][position]), float(loaded[0]["bench"][position])
        cut = edges(list(values / 10000), invested / 10000)
        counts, _ = np.histogram(values / 10000, bins=cut)
        sides = {"lost": int((values < invested).sum()), "behind": int(((values >= invested) & (values < bench)).sum()),
                 "ahead": int((values >= bench).sum())}
        bins = [{"low": low, "high": high, "count": int(count) * SAMPLE_EVERY,
                 "css": SIDES[side(math.sqrt(low * high) * 10000, invested, bench)][0],
                 "title": f"{low:,.0f}～{high:,.0f} 萬：約 {int(count) * SAMPLE_EVERY:,} 個策略設定（抽樣 {int(count):,} 個 × {SAMPLE_EVERY}）"}
                for low, high, count in zip(cut, cut[1:], counts)]
        output[key] = {"label": label, "invested": invested, "bench": bench, "bins": bins, "samples": int(len(values)),
                       "estimated": int(len(values)) * SAMPLE_EVERY,
                       "counts": {name: count * SAMPLE_EVERY for name, count in sides.items()},
                       "share": {name: count / len(values) for name, count in sides.items()},
                       "median": float(np.median(values)) / 10000, "end": str(loaded[0]["end"][0])}
    return output


def search_charts(found: dict[str, object]) -> dict[str, str]:
    from quant_platform.research.charts import histogram

    return {key: histogram(track["bins"], [{"value": track["invested"] / 10000, "label": f"投入 {track['invested'] / 10000:,.0f} 萬"},
                                           {"value": track["bench"] / 10000, "label": f"0050 {track['bench'] / 10000:,.0f} 萬"}],
                           f"全段大搜尋{track['label']}每個策略設定最後有多少錢的分佈（抽樣）",
                           count_text=lambda value: f"{value / 10000:,.0f} 萬" if value >= 10000 else f"{value:,.0f}")
            for key, track in found.items()}
