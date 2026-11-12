"""Are the factors and models the forward accounts read still working? (2026-11-13; problem register P7)

The factor strength analysis (research/factors.py ``run_recent``) runs only when a study asks for it, stops at the
pool's end (RECENT_END) and leaves the models out; a factor or a model that stopped working would show only when the
accounts reading it had lost money for a while. Every week, for every factor and model a live forward account reads:
each week from 2015-06 the eligible listed stocks ranked by it, against the next 20 sessions' return (the same
measure as the strength analysis: the rank correlation, and the top fifth against the average) — up to the last week
whose 20 sessions are over. The last RECENT_WEEKS weeks are set against the weeks before them.

The verdict, written before the first run (2026-11-13; docs/research/results.md「因子與模型的衰減監控」):
- the direction is the sign of the factor's correlation before the recent weeks (a factor read reversed works
  reversed);
- 長期就弱: before the recent weeks, |t| < 2 — no judgment of a change;
- 反過來了: in the recent weeks the correlation is against the direction, by more than 2 standard errors;
- 變弱: the recent weeks' correlation is below the earlier one (in the direction) by more than 2 standard errors;
- 正常: otherwise.
The standard error is the recent weeks' spread over the square root of their independent count: four weekly
rankings share their 20 sessions, so RECENT_WEEKS weeks count as RECENT_WEEKS × STEP ÷ HORIZON.

2026-11-13 02:30, after the first run (written after seeing its numbers): the verdict above, on the rank correlation
over every eligible stock, said 長期就弱 of the deep model the most accounts read (ml_deep2_top, 1,839 accounts: its
correlation −0.020, t −1.3, while its top fifth made +15.6% a year over the average) — it learns which stocks reach the
day's top 10%, not an order of the whole market, and the rules buy only the top. The same verdict is taken on the top
fifth's excess over the average, and that one is the verdict (``verdict``); the rank correlation's stays beside it
(``ic_verdict``). Nothing else changed.
"""
from __future__ import annotations

import json
import math
import statistics
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
FOLDER = "decay"
HORIZON = 20                 # sessions ahead, as the strength analysis
STEP = 5                     # a ranking every week
RECENT_WEEKS = 26            # about half a year
EDGE = 2.0                   # standard errors
FLAGGED = ("反過來了", "變弱")


def used(research: str | Path) -> dict[str, int]:
    """Every factor or model a live forward account reads (a blend's sleeves, the bear-market factors too): how many
    accounts read it."""
    path = Path(research) / "forward" / "stocks" / "tracked.json"
    try:
        tracked = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    counts: dict[str, int] = {}
    for item in tracked:
        if item.get("stopped"):
            continue
        rule = item.get("rule") or {}
        rules = [sleeve.get("rule") or {} for sleeve in rule.get("sleeves") or []] if item.get("kind") == "blend" else [rule]
        names = set()
        for one in rules:
            names |= set((one.get("factors") or {}).keys()) | set((one.get("bear_factors") or {}).keys())
        for name in names:
            counts[name] = counts.get(name, 0) + 1
    return counts


def label(name: str) -> str:
    from quant_platform.research.daily import EARNINGS_FACTORS, FACTOR_LABELS, MODEL_FACTORS
    from quant_platform.research.scan import SHORT

    if name in MODEL_FACTORS:
        return f"{SHORT.get(name, name)}：{MODEL_FACTORS[name].replace('同上，', '')}"
    return str({**FACTOR_LABELS, **EARNINGS_FACTORS}.get(name, SHORT.get(name, name)))


def independent(count: int) -> float:
    """Weekly rankings that share their 20 sessions are not independent: ``count`` of them count as this many."""
    return max(1.0, count * STEP / HORIZON)


def _ratio(value: float, error: float) -> float:
    """``value`` in standard errors; with no spread at all, as far as it goes (a constant series is all edge)."""
    if error > 0:
        return value / error
    return math.copysign(math.inf, value) if value else 0.0


def _finite(value: float) -> float | None:
    return round(value, 2) if math.isfinite(value) else None


def _verdict(series: list[float], recent: int) -> dict[str, object]:
    """The verdict of the module text on one weekly series (oldest first): its earlier weeks against the recent ones."""
    early, late = series[:-recent], series[-recent:]
    long_mean, long_sd = statistics.fmean(early), statistics.stdev(early)
    long_t = _ratio(long_mean, long_sd / math.sqrt(independent(len(early))))
    late_mean, late_sd = statistics.fmean(late), statistics.stdev(late)
    error = late_sd / math.sqrt(independent(len(late)))
    direction = 1.0 if long_mean >= 0 else -1.0
    against = _ratio(-direction * late_mean, error)                # the recent weeks against the direction
    below = _ratio(direction * (long_mean - late_mean), error)     # below the earlier weeks, in the direction
    if abs(long_t) < EDGE:
        verdict = "長期就弱"
    elif against > EDGE:
        verdict = "反過來了"
    elif below > EDGE:
        verdict = "變弱"
    else:
        verdict = "正常"
    return {"verdict": verdict, "direction": int(direction), "long": long_mean, "long_t": _finite(long_t),
            "recent": late_mean, "recent_t": _finite(_ratio(late_mean, error)), "below": _finite(below)}


def judge(ics: list[float], tops: list[float | None], recent: int = RECENT_WEEKS) -> dict[str, object]:
    """One factor's weekly rank correlations and its top fifth's excess over the average (per 20 sessions; None where
    a week has no top fifth), oldest first: the verdict on the top fifth (what the rules buy), the rank correlation's
    beside it."""
    values = [value for value in tops if value is not None]
    if len(ics) < recent + 52 or len(values) < recent + 52:
        return {"verdict": "資料不夠", "weeks": len(ics)}
    ic, top = _verdict(ics, recent), _verdict(values, recent)
    scale = 252 / HORIZON
    return {"verdict": top["verdict"], "ic_verdict": ic["verdict"], "weeks": len(ics), "direction": top["direction"],
            "long_ic": round(ic["long"], 4), "long_t": ic["long_t"], "recent_ic": round(ic["recent"], 4),
            "recent_t": ic["recent_t"], "ic_below": ic["below"],
            "long_top_year": round(top["long"] * scale, 4), "top_t": top["long_t"],
            "recent_top_year": round(top["recent"] * scale, 4), "recent_top_t": top["recent_t"], "below": top["below"]}


def weekly(fp, factor: str, positions: list[int]) -> tuple[list[str], list[float], list[float | None]]:
    """Each ranking week's day, rank correlation with the next HORIZON sessions and top fifth over the average."""
    from quant_platform.research import daily
    from quant_platform.research.factors import _fifths, _ranks

    eligibility = daily.DailyRule(name="資格", factors={"momentum_3": 1.0})
    matrix = fp.matrix(factor)
    days, ics, tops = [], [], []
    for position in positions:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            forward = fp.panel.filled[:, position + HORIZON] / fp.panel.filled[:, position] - 1
        score = np.asarray(matrix[:, position], dtype=float)
        mask = fp.eligible(eligibility, position) & np.isfinite(score) & np.isfinite(forward)
        if int(mask.sum()) < 50:
            continue
        s, r = score[mask], forward[mask]
        with np.errstate(invalid="ignore", divide="ignore"):
            ic = float(np.corrcoef(_ranks(s), _ranks(r))[0, 1])
        if not np.isfinite(ic):
            continue
        top, _bottom = _fifths(s, r)
        days.append(fp.sessions[position].isoformat())
        ics.append(ic)
        tops.append(float(top.mean() - r.mean()) if len(top) else None)
    return days, ics, tops


def measure(history: str | Path, research: str | Path, now: datetime | None = None, job=None) -> dict[str, object]:
    """Every used factor and model, judged; written to ``decay/<time>.json``."""
    from quant_platform.research import daily

    counts = used(research)
    data, fp = daily.load(history)
    sessions = fp.sessions
    last = len(sessions) - 1 - HORIZON                   # the last ranking whose 20 sessions are over
    first = next((index for index, day in enumerate(sessions) if day >= daily.RECENT_START), len(sessions))
    positions = list(range(last, first - 1, -STEP))[::-1]
    output: dict[str, dict] = {}
    names = sorted(counts, key=lambda name: (-counts[name], name))
    if job:
        job.update(total=len(names), done=0, force=True)
    for number, name in enumerate(names):
        if job:
            job.update(done=number, current=label(name)[:40])
        try:
            days, ics, tops = weekly(fp, name, positions)
        except Exception as error:                      # noqa: BLE001 - one factor not measurable is written as such
            output[name] = {"label": label(name), "accounts": counts[name], "verdict": "沒辦法算",
                            "error": f"{type(error).__name__}: {str(error)[:160]}"}
            continue
        output[name] = {"label": label(name), "accounts": counts[name], **judge(ics, tops),
                        "first": days[0] if days else None, "last": days[-1] if days else None,
                        "recent_from": days[-RECENT_WEEKS] if len(days) >= RECENT_WEEKS else None}
    moment = (now or datetime.now(TAIPEI)).astimezone(TAIPEI)
    report = {"at": moment.isoformat(timespec="seconds"), "ranked_through": sessions[last].isoformat() if last >= 0 else None,
              "returns_through": sessions[-1].isoformat() if sessions else None, "recent_weeks": RECENT_WEEKS,
              "horizon": HORIZON, "step": STEP, "edge": EDGE, "factors": output}
    folder = Path(research) / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"decay-{moment:%Y%m%d-%H%M%S}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                                                encoding="utf-8")
    return report


def latest(research: str | Path) -> dict[str, object] | None:
    folder = Path(research) / FOLDER
    for path in sorted(folder.glob("decay-*.json"), reverse=True) if folder.is_dir() else []:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def rows(report: dict[str, object] | None) -> list[dict[str, object]]:
    """For the page: the flagged first, then by the accounts reading it."""
    if not report:
        return []
    order = {"反過來了": 0, "變弱": 1, "沒辦法算": 2, "正常": 3, "長期就弱": 4, "資料不夠": 5}
    found = [{"factor": name, **item} for name, item in (report.get("factors") or {}).items()]
    return sorted(found, key=lambda row: (order.get(row.get("verdict"), 9), -(row.get("accounts") or 0), row["factor"]))


def flagged(report: dict[str, object] | None) -> list[dict[str, object]]:
    return [row for row in rows(report) if row.get("verdict") in FLAGGED]
