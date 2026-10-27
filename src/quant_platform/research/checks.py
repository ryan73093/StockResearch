"""2026-10-26 (使用者：上億個策略訓練後的結果跟數據還有訓練狀況我完全沒有看到；你現在全部都是正確的嗎、上億個策略都是用
正確的方法去做訓練、去做測試的嗎): what the research overview shows about it.

- ``funnel``: every large-search round (the settings screened, how many passed the screen, how many the engine then ran
  in full, how many passed the tiers and how many beat 0050 over the whole period) and every blend round.
- ``screen_vs_engine``: the screen's yearly excess (simplified, 2015-06 to the round's development end) against the
  engine's own account over the same months, finalist by finalist — the same quantity, two calculations. Saved weekly
  (``audit/screen-vs-engine-*.json``) by the audit pipeline.
- ``correctness``: the latest result of every check (factor truncation, trade rules, forward against backtest, the
  owner's decisions, the pool's results run again (2026-10-28), screen against engine), with its date; a check older
  than it should be is marked.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
_STAMP = re.compile(r"(\d{8})-(\d{6})")
WEEKLY_DAYS = 8          # the audit pipeline runs every Sunday
DAILY_DAYS = 6           # the 16:00 check runs on trading days (a long weekend plus a typhoon day is five)


def _when(name: str) -> datetime | None:
    found = _STAMP.search(name)
    return datetime.strptime("".join(found.groups()), "%Y%m%d%H%M%S") if found else None


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def funnel(research: str | Path, pool: list[dict] | None = None) -> dict[str, object]:
    """Each large-search round and each blend round, and the totals. With ``pool`` (the pool's rows, the latest
    engine's results), each round's finalists are counted again as they stand now: an engine fix since the round can
    move them (``now_beat``: more money than the same money in 0050 at 2026-09; ``now_passing``)."""
    from quant_platform.research.daily import PASSING_TIERS
    from quant_platform.research.registry import TrialRegistry

    research = Path(research)
    rows_now = {row["spec_hash"]: row for row in pool or [] if row.get("spec_hash")}
    hashes = ({record.trial_id: record.spec_hash for record in TrialRegistry(research / "trials.jsonl").records()}
              if pool is not None and (research / "trials.jsonl").is_file() else {})
    rounds = []
    for path in sorted((research / "scans").glob("*/summary.json")) if (research / "scans").is_dir() else []:
        summary = _read(path)
        if not summary or path.parent.name.startswith("scan-full"):     # the full-period search: ``full`` below
            continue
        results = summary.get("results") or []
        tiers = summary.get("tiers") or {}
        now = [rows_now.get(hashes.get(row.get("trial_id"))) for row in results]
        now = [row for row in now if row and row.get("final_value")]
        rounds.append({"at": _when(path.parent.name), "name": path.parent.name,
                       "now_beat": sum(1 for row in now if row["final_value"] > (row.get("benchmark_final_value") or 1e30)),
                       "now_passing": sum(1 for row in now if row.get("tier") in PASSING_TIERS), "now_found": len(now),
                       "large": path.parent.name.startswith("scan-large"),
                       "candidates": int(summary.get("candidates") or 0),
                       "passed": int(summary.get("passed_screen") or 0), "finalists": len(results),
                       "passing": sum(count for tier, count in tiers.items() if tier in PASSING_TIERS),
                       "beat_full": sum(1 for row in results if (row.get("full_excess") or 0) > 0),
                       "minutes": summary.get("minutes")})
    blends = []
    for path in sorted((research / "blend_scan").glob("finalists-*.json")) if (research / "blend_scan").is_dir() else []:
        summary = _read(path)
        if not summary:
            continue
        stamp = _when(str(summary.get("stamp") or "")) or datetime.fromtimestamp(path.stat().st_mtime)
        blends.append({"at": stamp, "pool": summary.get("pool"), "candidates": int(summary.get("candidates") or 0),
                       "passed": int(summary.get("passed") or 0), "finalists": len(summary.get("finalists") or [])})
    rounds.sort(key=lambda row: row["at"] or datetime.min)
    blends.sort(key=lambda row: row["at"])
    total = {key: sum(row[key] for row in rounds) for key in ("candidates", "passed", "finalists", "passing", "beat_full",
                                                              "now_beat", "now_passing", "now_found")}
    biggest = max(blends, key=lambda row: row["candidates"], default=None)
    return {"rounds": rounds, "blends": blends, "total": total, "biggest_blend": biggest, "full": full_search(research),
            "blend_finalists": sum(row["finalists"] for row in blends), "now": pool is not None,
            "minutes": round(sum(row["minutes"] or 0 for row in rounds))}


def screen_vs_engine(research: str | Path) -> dict[str, object]:
    """Every finalist the engine ran: the screen's yearly excess against the engine's over the same months."""
    from quant_platform.research.metrics import unit_values
    from quant_platform.research.registry import TrialRegistry

    research = Path(research)
    records = {record.trial_id: record for record in TrialRegistry(research / "trials.jsonl").records()}
    rows = []
    for path in sorted((research / "scans").glob("*/summary.json")) if (research / "scans").is_dir() else []:
        summary = _read(path) or {}
        end = (summary.get("development") or ["", "2020-09-30"])[1][:7]
        for result in summary.get("results") or []:
            record = records.get(result.get("trial_id"))
            if record is None or result.get("screen_excess") is None or not record.report_file:
                continue
            report = _read(research / "reports" / record.report_file) or {}
            curve = report.get("curve") or {}
            months = [month for month in sorted(curve) if "2015-06" <= month <= end]
            if len(months) < 24:
                continue
            values, bench, paid = ([curve[month][column] for month in months] for column in (0, 1, 2))
            flows = [paid[0]] + [paid[index] - paid[index - 1] for index in range(1, len(paid))]
            mine, base = unit_values(values, flows), unit_values(bench, flows)
            engine = (mine[-1] / base[-1]) ** (12 / len(months)) - 1
            rows.append({"trial": result.get("trial_id"), "round": path.parent.name,
                         "screen": float(result["screen_excess"]), "engine": float(engine)})
    if not rows:
        return {"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "compared": 0}
    screen = np.array([row["screen"] for row in rows])
    engine = np.array([row["engine"] for row in rows])
    gap = engine - screen

    def ranks(values: np.ndarray) -> np.ndarray:
        return values.argsort().argsort().astype(float)

    correlation = float(np.corrcoef(ranks(screen), ranks(engine))[0, 1]) if len(rows) > 2 else None
    return {"at": datetime.now(TAIPEI).isoformat(timespec="seconds"), "compared": len(rows),
            "screen_median": float(np.median(screen)), "engine_median": float(np.median(engine)),
            "gap_median": float(np.median(gap)), "gap_p10": float(np.percentile(gap, 10)),
            "gap_p90": float(np.percentile(gap, 90)), "rank_correlation": correlation,
            "both_ahead": int(((screen > 0) & (engine > 0)).sum()),
            "screen_only": int(((screen > 0) & (engine <= 0)).sum()), "rows": rows}


def save_screen_check(research: str | Path) -> Path:
    research = Path(research)
    found = screen_vs_engine(research)
    folder = research / "audit"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screen-vs-engine-{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _newest(folder: Path, pattern: str) -> Path | None:
    found = sorted(folder.glob(pattern), key=lambda path: path.name) if folder.is_dir() else []
    return found[-1] if found else None


def _age(at: datetime | None, now: datetime, days: int) -> bool:
    return at is None or (now - at).total_seconds() > days * 86400


def correctness(research: str | Path, now: datetime | None = None) -> list[dict[str, object]]:
    """The latest result of each check: ok (True, False, or None for a comparison shown as it is), when, what."""
    research = Path(research)
    now = now or datetime.now()
    out = []

    path = _newest(research / "leakcheck", "leakcheck-*.json")
    found = _read(path) if path else None
    at = _when(path.name) if path else None
    out.append({"key": "leak", "title": "因子沒有偷看未來", "at": at, "stale": _age(at, now, WEEKLY_DAYS),
                "ok": (not found["leaks"]) if found else False,
                "headline": (f"{len(found['leaks'])} 個因子有差異" if found else "還沒跑過"),
                "detail": (f"{len(found['factors'])} 個因子 × {len(found['days'])} 個隨機交易日（{'、'.join(found['days'])}）"
                           if found else ""),
                "how": "把資料截到那一天（股價、籌碼、月營收、財報都只留那天以前公布的），重算每一個因子，和用全部歷史算的值逐檔比；"
                       "不一樣就代表那個因子用到了未來的資料。"})

    path = _newest(research / "audit", "trade-audit-*.json")
    found = _read(path) if path else None
    at = _when(path.name) if path else None
    violations = sum((found.get("violation_counts") or {}).values()) if found else None
    out.append({"key": "trades", "title": "每筆委託都照當時的交易所規則", "at": at, "stale": _age(at, now, WEEKLY_DAYS),
                "ok": (violations == 0) if found else False,
                "headline": f"{violations:,} 筆違規" if found else "還沒跑過",
                "detail": (f"{len(found.get('rules') or []):,} 個規則、{int(found.get('orders') or 0):,} 筆委託"
                           f"（{found.get('start')}～{found.get('end')}，引擎 {found.get('engine')}）" if found else ""),
                "parts": list((found.get("checks") or {}).values()) if found else [],
                "how": "完整回測的每一筆委託逐筆檢查下面每一項；有一筆違規就沒過。"})

    path = _newest(research / "forward" / "stocks", "audit-*.json")
    found = _read(path) if path else None
    at = _when(path.name) if path else None
    out.append({"key": "forward", "title": "前向帳戶和回測是同一個策略", "at": at, "stale": _age(at, now, WEEKLY_DAYS),
                "ok": (int(found.get("differing_accounts") or 0) == 0) if found else False,
                "headline": f"{int(found.get('differing_accounts') or 0):,} 個帳戶不一樣" if found else "還沒跑過",
                "detail": (f"{int(found.get('accounts') or 0):,} 個帳戶、{int(found.get('checked_days') or 0):,} 個決策日"
                           if found else ""),
                "how": "用回測引擎從前向起點重算每個帳戶每一天該買賣什麼，和帳上真的做的比；10/25 就是這樣抓到前向少載股價、"
                       "「看成本才換股」從不換股。"})

    found = _read(research / "invariants.json")
    at = datetime.fromisoformat(found["at"]).replace(tzinfo=None) if found and found.get("at") else None
    checks = (found or {}).get("checks") or []
    failed = [item for item in checks if not item.get("ok")]
    out.append({"key": "decisions", "title": "每天的需求檢查（你的每一個決定）", "at": at, "stale": _age(at, now, DAILY_DAYS),
                "ok": (not failed) if found else False,
                "headline": (f"{len(checks) - len(failed)}／{len(checks)} 項通過" if found else "還沒跑過"),
                "detail": "；".join(f"沒過：{item.get('name')}（{item.get('detail')}）" for item in failed[:3]),
                "parts": [f"{'通過' if item.get('ok') else '沒過'}・{item.get('name')}" for item in checks],
                "how": "交易日 16:00 用程式核對：前向起算日、現金流、100% 個股、沒有轉換持有方式、模型重訓到截止日、對帳、稽核、"
                       "備份、知識庫、沒有視窗等。"})

    path = _newest(research / "audit", "reproduce-*.json")
    found = _read(path) if path else None
    at = _when(path.name) if path else None
    out.append({"key": "reproduce", "title": "選手池的結果重算得出來", "at": at, "stale": _age(at, now, WEEKLY_DAYS),
                "ok": (int(found.get("differing") or 0) == 0) if found else False,
                "headline": f"{int(found.get('differing') or 0)} 個和登錄的不一樣" if found else "還沒跑過",
                "detail": (f"選手池 {int(found.get('pool') or 0):,} 個最新結果隨機抽 {int(found.get('compared') or 0)} 個"
                           if found else ""),
                "how": "用回測引擎把抽到的規則從頭重算一次，期末金額要和登錄的一元不差。不一樣就表示有輸入沒算進「同樣輸入不重跑」"
                       "的指紋（10/28 抓到深度模型的檔案沒算進去，222 個結果過期一週），或輸入改了沒重跑。"})

    path = _newest(research / "audit", "screen-vs-engine-*.json")
    found = _read(path) if path else None
    at = _when(path.name) if path else None
    if found and found.get("compared"):
        headline = (f"排名相關 {found['rank_correlation']:.2f}；初篩贏、完整回測輸的 {found['screen_only']} 個"
                    f"（共 {found['compared']} 個）")
        detail = (f"同樣的開發期，初篩每年比 0050 多 {found['screen_median']:+.1%}（中位數），完整回測 "
                  f"{found['engine_median']:+.1%}")
    else:
        headline, detail = "還沒跑過", ""
    out.append({"key": "screen", "title": "初篩的分數和完整回測差多少", "at": at, "stale": _age(at, now, WEEKLY_DAYS),
                "ok": None if found else False, "headline": headline, "detail": detail,
                "how": "大搜尋第一階段用簡化的帳戶先篩：每天照比重重新配、用隔天的報酬、扣手續費、交易稅與滑價，但不管漲跌停"
                       "買不到、整股零股與每月投入的錢。每輪最好的幾個才用真正的回測引擎完整重跑。簡化帳戶會高估，所以名單、"
                       "成績與等級一律用完整回測，初篩只用來縮小範圍。"})
    return out


def full_search(research: str | Path) -> dict[str, object] | None:
    """2026-10-26: the full-period search (research/scan_full.py) — each phase's plan, its stage 1's progress over its
    parts while it runs, its summary when done."""
    from math import comb

    research = Path(research)
    phases = []
    for out in sorted((research / "scans").glob("scan-full-*")) if (research / "scans").is_dir() else []:
        plan = _read(out / "plan.json")
        if not plan:
            continue
        planned = sum(comb(len(plan["names"]), size) for size in plan["sizes"]) * 24
        parts = [_read(path) or {} for path in sorted(out.glob("progress-*-of-*.json"))]
        summary = _read(out / "summary.json")
        phases.append({"phase": plan["phase"], "cutoff": plan["cutoff"], "signals": len(plan["names"]), "sizes": plan["sizes"],
                       "planned": planned, "screened": sum(part.get("screened", 0) for part in parts),
                       "passed": sum(part.get("passed", 0) for part in parts),
                       "per_minute": sum(part.get("per_minute", 0) for part in parts if not part.get("finished_at")),
                       "eta_hours": max([part.get("eta_hours") or 0 for part in parts if not part.get("finished_at")] or [0]),
                       "summary": summary})
    if not phases:
        return None
    from quant_platform.research.scan_full import SIZES, TOP_SIGNALS

    # phase b's plan is written when phase a ends; until then its size is known from the plan written in advance
    planned = sum(item["planned"] for item in phases) + (
        0 if any(item["phase"] == "b" for item in phases) else sum(comb(TOP_SIGNALS, size) for size in SIZES["b"]) * 24)
    return {"phases": phases, "screened": sum(item["screened"] for item in phases),
            "passed": sum(item["passed"] for item in phases),
            "finalists": sum(len((item["summary"] or {}).get("results") or []) for item in phases),
            "passing": sum(sum(1 for row in (item["summary"] or {}).get("results") or []
                               if row.get("tier") in ("T0", "T0（高風險）", "T0 候選", "T0 候選（高風險）")) for item in phases),
            "planned": planned}
