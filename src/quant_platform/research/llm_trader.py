"""2026-10-20 (使用者：LLM 有沒有辦法做成交易 agent 或其他輔助功能): the LLM trader, a forward-only experiment.

An LLM read the years it would be tested on, so no backtest can judge it (research_method §9, the AI-methods review);
it is judged forward only, against a mechanical twin that sees the same candidates. Every trading day after the close
(15:20, once the day's prices are in):

- the candidates are the day's top TOP_EACH of each sleeve of the best normal T0 candidate (SOURCE_TRIAL, #5163:
  the T0 consensus, the deep model, the calm rule — each its own sleeve, no minimum holding), at most 3 × TOP_EACH;
- the LLM sees, for each, its sleeve and rank there, a few factor percentiles, whether it is held and how it has done
  since bought, and the last week's scored news about it (research/news_events.py), and picks at most HOLD to hold
  with a one-line reason each — only candidates or current holdings, at most a third from one industry;
- the twin (control) keeps the holdings still among the candidates and fills up round-robin by the sleeves' ranks.

Both lists are stored (forward/llm_trader/decisions.jsonl, one line a day, never rewritten) and become the scores of
two rule factors, ``llm_trader`` and ``llm_control`` (``scores``): a day's choice ranks its stocks on the next session,
so the forward accounts (stock_forward.forward_only) trade them at the next close — decided after the close, filled
the next day. An answer that breaks a hard rule, or no answer, keeps the previous day's choice (recorded as such).
The accounts start at the first session after the first decision and are not restarted monthly (there is no history).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

VERSION = "llm-trader-1.0.0"
OPERATION = "llm_trader"
SOURCE_TRIAL = 5163
TOP_EACH = 20
HOLD = 20
INDUSTRY_SHARE = 0.3          # the accounts' industry cap too (6 of 20)
NEWS_DAYS = 7
FACTORS = ("momentum_6", "trend_200", "high_52w", "low_volatility_60", "foreign_buy_20", "eps_growth")
TAIPEI = ZoneInfo("Asia/Taipei")
INSTRUCTIONS = """你是台股研究平台的「LLM 交易員」前向實驗（模擬帳戶，不會真的下單）。每天收盤後，你從量化策略給的候選股裡，
決定明天收盤要持有哪些股票（最多 20 檔，等額）。規則：
1. 只能從「候選股」與「目前持股」中挑；不能挑 0050 或任何 ETF。
2. 同一產業最多 6 檔。
3. 你的價值在於讀新聞與事件、判斷哪些候選的上漲理由還在、哪些有新的風險；不要只照名次排。
4. 每檔寫一句繁體中文理由（30 字內），說明是名次、因子、新聞的哪一點。
5. 不確定時可以少於 20 檔，剩下的錢放現金。
回覆 JSON：{"hold": [{"code": "2330", "reason": "…"}], "view": "一句今天的市場看法"}"""
PROMPT_VERSION = hashlib.sha256(INSTRUCTIONS.encode("utf-8")).hexdigest()[:12]


def build_client(settings, research: str | Path):
    """The model client inside its own share of the monthly LLM budget; None without a key."""
    from quant_platform.research.agent.llm import LLMError, ResponsesClient, UsageLedger, resolve_openai_key

    try:
        key = resolve_openai_key(settings.openai_api_key)
    except LLMError:
        return None
    if not key:
        return None
    return ResponsesClient(key, settings.research_agent_model, UsageLedger(Path(research) / "agent" / "usage.jsonl"),
                           settings.llm_trader_monthly_budget_usd, operations=(OPERATION,),
                           total_budget_usd=settings.llm_monthly_budget_usd, label="LLM 交易員")


def folder(research: str | Path) -> Path:
    return Path(research) / "forward" / "llm_trader"


def decisions(research: str | Path) -> list[dict[str, object]]:
    path = folder(research) / "decisions.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def source_rules(research: str | Path) -> list[tuple[str, object]]:
    """(label, daily rule) of each sleeve of SOURCE_TRIAL."""
    from quant_platform.research.blend import BlendRule, parse_spec
    from quant_platform.research.categories import classify
    from quant_platform.research.registry import TrialRegistry

    research = Path(research)
    record = next(record for record in TrialRegistry(research / "trials.jsonl").records()
                  if record.trial_id == SOURCE_TRIAL)
    rule = parse_spec(json.loads((research / "reports" / record.report_file).read_text(encoding="utf-8"))["spec"])
    sleeves = rule.sleeves if isinstance(rule, BlendRule) else ()
    return [(classify(sleeve.rule.canonical())["family"], sleeve.rule) for sleeve in sleeves]


def candidates(fp, sources: list[tuple[str, object]], position: int) -> list[dict[str, object]]:
    """The day's top TOP_EACH of each source, each stock once (its best rank kept, every source named)."""
    found: dict[str, dict] = {}
    for label, rule in sources:
        for rank, symbol in enumerate(fp.ranked(rule, position)[:TOP_EACH], start=1):
            item = found.setdefault(symbol, {"symbol": symbol, "code": symbol.split(".")[0], "sources": [],
                                             "best_rank": rank})
            item["sources"].append(f"{label}第{rank}名")
            item["best_rank"] = min(item["best_rank"], rank)
    return sorted(found.values(), key=lambda item: (item["best_rank"], item["code"]))


def control_choice(cands: list[dict], held: list[str]) -> list[str]:
    """The twin: holdings still among the candidates, then the best by rank (ties by code), up to HOLD."""
    codes = [item["code"] for item in cands]
    kept = [code for code in held if code in codes][:HOLD]
    for code in codes:
        if len(kept) >= HOLD:
            break
        if code not in kept:
            kept.append(code)
    return kept


def validate(answer: dict, cands: list[dict], held: list[str], industry) -> tuple[list[str], list[str], str | None]:
    """(codes, reasons, problem): the LLM's choice checked against the hard rules; a problem means it is not used."""
    allowed = {item["code"] for item in cands} | set(held)
    rows = answer.get("hold") if isinstance(answer, dict) else None
    if not isinstance(rows, list):
        return [], [], "回覆格式不對（沒有 hold 清單）"
    codes, reasons = [], []
    for row in rows:
        code = str((row or {}).get("code", "")).strip() if isinstance(row, dict) else ""
        if not code or code in codes:
            continue
        if code not in allowed:
            return [], [], f"挑了候選以外的股票 {code}"
        if code.startswith("00"):
            return [], [], f"挑了 ETF {code}"
        codes.append(code)
        reasons.append(str(row.get("reason", ""))[:60])
    if len(codes) > HOLD:
        return [], [], f"挑了 {len(codes)} 檔，超過 {HOLD} 檔"
    counts: dict[str, int] = {}
    for code in codes:
        counts[industry(code)] = counts.get(industry(code), 0) + 1
    limit = max(1, int(HOLD * INDUSTRY_SHARE))
    crowded = [name for name, count in counts.items() if count > limit]
    if crowded:
        return [], [], f"同一產業超過 {limit} 檔（{crowded[0]}）"
    return codes, reasons, None


def news_of(history: Path, codes: set[str], day: date) -> dict[str, list[str]]:
    """The scored news of the last NEWS_DAYS days for these codes: one short line each."""
    import pandas as pd

    output: dict[str, list[str]] = {}
    events = Path(history) / "news_events"
    for path in sorted(events.glob("*.parquet"))[-NEWS_DAYS:] if events.is_dir() else []:
        if path.stem > day.strftime("%Y%m%d"):
            continue
        frame = pd.read_parquet(path)
        for row in frame[frame["stock_id"].astype(str).isin(codes) & frame["about"].fillna(False)].to_dict("records"):
            mark = {1: "利多", -1: "利空", 0: "中性"}.get(int(row.get("direction") or 0), "")
            line = f"{row['news_day']} {mark}{'・風險' if row.get('risk') else ''}{'・有數字' if row.get('numbers') else ''}：{str(row['title'])[:60]}"
            output.setdefault(str(row["stock_id"]), []).append(line)
    return {code: lines[-4:] for code, lines in output.items()}


def run_day(base: Path, research: Path, day: date, client=None, now: datetime | None = None) -> dict[str, object] | None:
    """Decide ``day`` once (None when already decided or not a session); the record appended to decisions.jsonl."""
    from quant_platform.research import daily
    from quant_platform.research.agent.llm import call_cost
    from quant_platform.research.stock_forward import _names

    if any(item["date"] == day.isoformat() for item in decisions(research)):
        return None
    data, fp = daily.load(base)
    position = fp.index.get(day)
    if position is None:
        return None
    sources = source_rules(research)
    cands = candidates(fp, sources, position)
    previous = decisions(research)
    held_llm = list((previous[-1].get("llm") or {}).get("hold") or []) if previous else []
    held_control = list((previous[-1].get("control") or {}).get("hold") or []) if previous else []
    names = _names(base, day.year)
    news = news_of(base, {item["code"] for item in cands} | set(held_llm), day)
    lines = []
    for item in cands:
        row = fp.row.get(item["symbol"])
        values = []
        for factor in FACTORS:
            column = fp.matrix(factor)[:, position]
            known = np.isfinite(column)
            value = column[row] if row is not None else np.nan
            values.append(f"{factor}={int(round(100 * (column[known] < value).mean())) if np.isfinite(value) and known.any() else '-'}")
        lines.append(f"{item['code']} {names.get(item['code'], '')}｜{fp.industry(item['symbol'])}｜{'、'.join(item['sources'])}"
                     f"｜{'持有中' if item['code'] in held_llm else '未持有'}｜百分位 {' '.join(values)}"
                     + (f"｜新聞：{' / '.join(news[item['code']])}" if item["code"] in news else ""))
    for code in held_llm:
        if code not in {item["code"] for item in cands}:
            lines.append(f"{code} {names.get(code, '')}｜目前持有、今天已不在候選｜新聞："
                         f"{' / '.join(news.get(code, [])) or '無'}")
    user = (f"日期 {day.isoformat()}（收盤後）。目前持有 {len(held_llm)} 檔：{'、'.join(held_llm) or '無'}。\n"
            f"候選股（{len(cands)} 檔，來源是量化策略各子帳戶的前 {TOP_EACH} 名）：\n" + "\n".join(lines)
            + "\n請以 JSON 回覆。")
    record = {"date": day.isoformat(), "version": VERSION, "prompt_version": PROMPT_VERSION,
              "decided_at": (now or datetime.now(TAIPEI)).isoformat(timespec="seconds"),
              "candidates": [item["code"] for item in cands],
              "control": {"hold": control_choice(cands, held_control)}}
    llm = {"hold": held_llm, "fallback": "沒有呼叫模型"}
    if client is not None:
        try:
            answer, usage = client.json_call(INSTRUCTIONS, user, OPERATION, context=day.isoformat())
            codes, reasons, problem = validate(answer, cands, held_llm,
                                               lambda code: fp.industry(f"{code}.TW"))
            if problem:
                llm = {"hold": held_llm, "fallback": f"沿用前一天：{problem}", "raw": answer}
            else:
                llm = {"hold": codes, "reasons": dict(zip(codes, reasons, strict=True)),
                       "view": str(answer.get("view", ""))[:120],
                       "cost_usd": call_cost(getattr(client, "model", ""), usage)}   # as the usage ledger
        except Exception as error:  # noqa: BLE001 - a failed call keeps yesterday's choice, recorded
            llm = {"hold": held_llm, "fallback": f"沿用前一天：{str(error)[:120]}"}
    record["llm"] = llm
    path = folder(research) / "decisions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def scores(fp, research: str | Path, which: str) -> np.ndarray:
    """sessions × symbols: each decision's stocks ranked on the session after it (``which``: "llm" or "control"),
    carried until the next decision; NaN elsewhere (the rule holds nothing before the first decision)."""
    output = np.full((len(fp.sessions), len(fp.symbols)), np.nan, dtype=np.float32)
    made = {item["date"]: list((item.get(which) or {}).get("hold") or []) for item in decisions(research)}
    current: list[str] | None = None
    for position in range(1, len(fp.sessions)):
        day = fp.sessions[position - 1].isoformat()
        if day in made:
            current = made[day]
        if current is None:
            continue
        for place, code in enumerate(current):
            row = fp.row.get(f"{code}.TW")
            if row is not None:
                output[position, row] = len(current) - place
    return output


def first_session(fp_sessions: list[date], research: str | Path) -> date | None:
    """The accounts' start: the session after the first decision."""
    made = sorted(item["date"] for item in decisions(research))
    if not made:
        return None
    return next((day for day in fp_sessions if day.isoformat() > made[0]), None)
