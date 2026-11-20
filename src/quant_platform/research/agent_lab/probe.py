"""How much of the past the model already knows. A replay hands the agent only the day's data, but the model may
remember what happened next: asked with no data at all whether a stock did better or worse than the middle of a list
in a given month, a model that knows nothing is right half the time. The months it is right clearly more often than
half are months it has read — a replay over them shows nothing about skill (and the months after are too few to).

One call a month: STOCKS stocks (the most traded at the month's start, so the list uses nothing from the month), each
answered 高 (above the list's median return of the month) or 低 — a forced choice: allowed to say 不知道, gpt-6-luna
said it of all 40 stocks of all 36 months (2026-11-20), which measures its caution, not its memory. The result is
stored with the model's name; a new model is probed again."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

TAIPEI = ZoneInfo("Asia/Taipei")
OPERATION = "agent_lab"
STOCKS = 40
INSTRUCTIONS = ("這是一個記憶測驗，不是預測。不要推理、不要查資料，只憑你記得的回答：下面每一檔台股在指定的那個月份，"
                "股價漲跌幅是高於還是低於這份名單的中位數？只回覆一個 JSON 物件 {\"a\": {\"2330\": \"高\", \"2317\": \"低\"}}，"
                "每一檔都要答，只能答「高」或「低」；不確定也要憑印象選一個。")


def months(sessions: list[date], count: int) -> list[tuple[str, int, int]]:
    """(YYYY-MM, the position of the last session before the month, the position of its last session) of the last
    ``count`` complete months."""
    last_of: dict[str, int] = {}
    for position, day in enumerate(sessions):
        last_of[f"{day:%Y-%m}"] = position
    names = sorted(last_of)
    complete = names[:-1]                           # the running month is not over
    return [(name, last_of[names[names.index(name) - 1]], last_of[name]) for name in complete[-count:] if names.index(name) > 0]


def month_list(fp, before: int, after: int, stocks: int = STOCKS) -> list[tuple[str, float]]:
    """(code, the month's return) of the ``stocks`` most traded listed common stocks at the month's start."""
    close = np.asarray(fp.panel.filled, dtype=float)
    turnover = np.nan_to_num(fp.turnover_20[:, before], nan=0.0)
    output = []
    for row in np.argsort(-turnover):
        code = fp.symbols[row].split(".")[0]
        start, end = close[row, before], close[row, after]
        if len(code) == 4 and code.isdigit() and not code.startswith("00") and np.isfinite(start) and np.isfinite(end) and start > 0:
            output.append((code, float(end / start - 1)))
        if len(output) >= stocks:
            break
    return output


def score(listed: list[tuple[str, float]], answers: dict) -> dict[str, float | int]:
    median = float(np.median([moved for _code, moved in listed]))
    right = wrong = unknown = 0
    for code, moved in listed:
        said = str((answers or {}).get(code, "")).strip()
        if said not in ("高", "低"):
            unknown += 1
        elif (said == "高") == (moved > median):
            right += 1
        else:
            wrong += 1
    answered = right + wrong
    # how far above a coin the answers are, in standard errors (0 when nothing was answered)
    z = (right - answered / 2) / (0.5 * answered ** 0.5) if answered else 0.0
    return {"right": right, "wrong": wrong, "unknown": unknown, "accuracy": round(right / answered, 3) if answered else 0.0,
            "z": round(float(z), 2)}


def path_for(research: str | Path, model: str) -> Path:
    return Path(research) / "agent_lab" / "probe" / f"{model or 'model'}.json"


def done(research: str | Path, model: str, least: int = 24) -> bool:
    """Whether ``model`` has a probe with at least ``least`` months answered (a run cut short is done again)."""
    path = path_for(research, model)
    if not path.is_file():
        return False
    try:
        rows = json.loads(path.read_text(encoding="utf-8")).get("months") or []
    except ValueError:
        return False
    return sum(1 for row in rows if "error" not in row and row.get("right", 0) + row.get("wrong", 0) > 0) >= least


def run(fp, base: Path, research: Path, client, count: int = 36, names=None, now: datetime | None = None) -> dict[str, object]:
    rows = []
    for name, before, after in months(list(fp.sessions), count):
        listed = month_list(fp, before, after)
        if len(listed) < 10:
            continue
        year, month = name.split("-")
        prompt = f"月份：{year} 年 {int(month)} 月。\n名單：" + "、".join(
            f"{code} {(names or {}).get(code, '')}".strip() for code, _moved in listed) + "\n請回覆 JSON。"
        try:
            answer, _usage = client.json_call(INSTRUCTIONS, prompt, OPERATION, context=f"probe-{name}")
            rows.append({"month": name, **score(listed, answer.get("a") if isinstance(answer, dict) else {})})
        except Exception as error:  # noqa: BLE001 - a month that failed is recorded as such
            rows.append({"month": name, "error": str(error)[:120]})
    result = {"model": getattr(client, "model", ""), "made_at": (now or datetime.now(TAIPEI)).isoformat(timespec="seconds"),
              "stocks": STOCKS, "months": rows}
    path = path_for(research, str(result["model"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def text(result: dict[str, object]) -> str:
    lines = [f"模型 {result['model']}：每個月 {result['stocks']} 檔，不給資料問「當月漲跌幅高於或低於名單中位數」；亂猜是 50%"]
    for row in result["months"]:
        if "error" in row:
            lines.append(f"  {row['month']}：失敗（{row['error']}）")
        else:
            lines.append(f"  {row['month']}：答 {row['right'] + row['wrong']} 檔對 {row['right']}（{100 * row['accuracy']:.0f}%，"
                         f"比亂猜多 {row['z']:+.1f} 個標準誤），不知道 {row['unknown']} 檔")
    return "\n".join(lines)
