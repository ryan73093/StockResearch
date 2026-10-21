"""2026-10-22 (使用者 2026-10-20：我也要這個——盤中的資金流向; FinMind Sponsor 的即時快照): during the session, the
money each industry and stock trades and how much of it the buyers or the sellers pushed (外盤／內盤), from FinMind's
whole-market snapshot (taiwan_stock_tick_snapshot: cumulative money, the last trade's side; about every 10 s).

Every INTERVAL seconds from 09:00 to 13:30 on a trading day: each stock's money since the last snapshot is counted as
bought at the ask (TickType 1, 外盤), sold at the bid (2, 內盤) or unknown (0, or the money already traded before the
first snapshot) by the side of the snapshot's last trade — an estimate: between two snapshots several trades happen.
Kept under instance/research/live/<date>/: latest.json (every stock and industry, rewritten each snapshot) and
minutes.jsonl (the industries every 5 minutes, to compare with the session before at the same time). Display only;
real orders stay closed."""

from __future__ import annotations

import json
import time as clock
import urllib.request
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
URL = "https://api.finmindtrade.com/api/v4/taiwan_stock_tick_snapshot"
INTERVAL = 15
OPEN, CLOSE = time(9, 0), time(13, 30)      # research programs keep out of 13:30-14:40 (AGENTS.md)
EVERY = 300                     # seconds between the rows of minutes.jsonl


def folder(research: str | Path, day: date) -> Path:
    return Path(research) / "live" / day.isoformat()


def fetch(token: str) -> list[dict]:
    request = urllib.request.Request(URL, headers={"Authorization": f"Bearer {token}", "User-Agent": "StockResearch/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8")).get("data") or []


# the listed and TPEx markets name some industries differently (FinMind TaiwanStockInfo): one row each on this page
# only — the rules' industry cap keeps its own classification
SAME_INDUSTRY = {"其他電子類": "其他電子業", "居家生活類": "居家生活", "數位雲端類": "數位雲端", "綠能環保類": "綠能環保",
                 "農業科技業": "農業科技", "運動休閒類": "運動休閒", "金融業": "金融保險", "創新版股票": "創新板股票",
                 "觀光事業": "觀光餐旅"}


def common(code: str) -> bool:
    """A common stock (four digits, not an ETF or a TDR)."""
    return len(code) == 4 and code.isdigit() and code[0] != "0" and not code.startswith("91")


class Flow:
    """Each stock's money today split by side; ``update`` takes one snapshot."""

    def __init__(self, state: dict | None = None) -> None:
        self.stocks: dict[str, dict] = (state or {}).get("stocks") or {}

    def update(self, rows: list[dict]) -> int:
        changed = 0
        for row in rows:
            code = str(row.get("stock_id") or "")
            if not common(code):
                continue
            try:
                total = float(row.get("total_amount") or 0)
            except (TypeError, ValueError):
                continue
            item = self.stocks.get(code)
            if item is None:                  # the first snapshot: what traded before it is of unknown side
                item = self.stocks[code] = {"amount": total, "outer": 0.0, "inner": 0.0, "unknown": total}
                added = 0.0
            else:
                added = max(0.0, total - item["amount"])
                item["amount"] = max(item["amount"], total)
                side = {1: "outer", 2: "inner"}.get(int(row.get("TickType") or 0), "unknown")
                item[side] += added
            item.update(close=row.get("close"), change=row.get("change_rate"), at=str(row.get("date") or "")[11:19])
            changed += added > 0
        return changed

    def industries(self, groups: dict[str, str]) -> list[dict]:
        """Every industry: money, its share of the market's, bought and sold at the counter, the net, the share bought,
        how many rose and fell, the stock bought most on net."""
        market = sum(item["amount"] for code, item in self.stocks.items() if code in groups) or 1.0
        output: dict[str, dict] = {}
        for code, item in self.stocks.items():
            group = groups.get(code)
            if not group:
                continue
            group = SAME_INDUSTRY.get(group, group)
            entry = output.setdefault(group, {"industry": group, "amount": 0.0, "outer": 0.0, "inner": 0.0, "up": 0,
                                              "down": 0, "leader": None, "leader_net": None})
            entry["amount"] += item["amount"]
            entry["outer"] += item["outer"]
            entry["inner"] += item["inner"]
            change = item.get("change") or 0
            entry["up"] += change > 0
            entry["down"] += change < 0
            net = item["outer"] - item["inner"]
            if entry["leader_net"] is None or net > entry["leader_net"]:
                entry["leader"], entry["leader_net"] = code, net
        for entry in output.values():
            entry["share"] = entry["amount"] / market
            entry["net"] = entry["outer"] - entry["inner"]
            sided = entry["outer"] + entry["inner"]
            entry["bought"] = entry["outer"] / sided if sided else None
        return sorted(output.values(), key=lambda entry: -entry["net"])

    def stocks_by_net(self, groups: dict[str, str], count: int = 30) -> list[dict]:
        rows = [{"code": code, "industry": SAME_INDUSTRY.get(groups.get(code), groups.get(code)), "amount": item["amount"], "net": item["outer"] - item["inner"],
                 "bought": item["outer"] / (item["outer"] + item["inner"]) if item["outer"] + item["inner"] else None,
                 "close": item.get("close"), "change": item.get("change")} for code, item in self.stocks.items()]
        rows.sort(key=lambda row: -row["net"])
        return rows[:count] + rows[-count:][::-1] if len(rows) > 2 * count else rows


def before(research: str | Path, day: date, moment: str) -> dict[str, float]:
    """The industries' shares at the same time of the latest earlier session kept (industry → share)."""
    root = Path(research) / "live"
    earlier = sorted(path for path in root.glob("????-??-??") if path.name < day.isoformat()) if root.is_dir() else []
    for path in reversed(earlier):
        rows = [json.loads(line) for line in (path / "minutes.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()] if (path / "minutes.jsonl").is_file() else []
        rows = [row for row in rows if row["time"] <= moment]
        if rows:
            return {entry["industry"]: entry["share"] for entry in rows[-1]["industries"]}
    return {}


def save(research: str | Path, day: date, flow: Flow, groups: dict[str, str], moment: datetime, minute_row: bool) -> dict:
    target = folder(research, day)
    target.mkdir(parents=True, exist_ok=True)
    industries = flow.industries(groups)
    earlier = before(research, day, moment.strftime("%H:%M:%S"))
    for entry in industries:
        entry["share_change"] = entry["share"] - earlier[entry["industry"]] if entry["industry"] in earlier else None
    payload = {"date": day.isoformat(), "updated": moment.isoformat(timespec="seconds"), "industries": industries,
               "stocks": flow.stocks_by_net(groups), "state": {"stocks": flow.stocks}}
    partial = target / "latest.partial"
    partial.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    partial.replace(target / "latest.json")
    if minute_row:
        with (target / "minutes.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": moment.strftime("%H:%M:%S"), "industries": [
                {key: entry[key] for key in ("industry", "amount", "share", "net")} for entry in industries]},
                ensure_ascii=False) + "\n")
    return payload


def latest(research: str | Path) -> dict | None:
    """The newest session's page data (no per-stock state)."""
    root = Path(research) / "live"
    days = sorted(root.glob("????-??-??")) if root.is_dir() else []
    for path in reversed(days):
        if (path / "latest.json").is_file():
            payload = json.loads((path / "latest.json").read_text(encoding="utf-8"))
            payload.pop("state", None)
            return payload
    return None


def run(history: str | Path, research: str | Path, token: str, now=lambda: datetime.now(TAIPEI), get=None,
        sleep=clock.sleep, job=None) -> dict[str, int]:
    """Snapshots every INTERVAL seconds until CLOSE (from where a stopped run left off)."""
    from quant_platform.research.daily import load_industries

    groups = load_industries(history)
    day = now().date()
    path = folder(research, day) / "latest.json"
    flow = Flow(json.loads(path.read_text(encoding="utf-8")).get("state") if path.is_file() else None)
    taken = errors = 0
    last_row: datetime | None = None
    while True:
        moment = now()
        if moment.time() >= CLOSE:
            break
        if moment.time() < OPEN:
            sleep(min(60, (datetime.combine(day, OPEN, TAIPEI) - moment).total_seconds() + 1))
            continue
        try:
            rows = (get or fetch)(token)
            flow.update(rows)
            due = last_row is None or (moment - last_row).total_seconds() >= EVERY
            save(research, day, flow, groups, moment, minute_row=due)
            if due:
                last_row = moment
            taken += 1
            if job:
                job.update(done=taken, current=f"{moment:%H:%M:%S}：{len(flow.stocks)} 檔")
        except Exception:  # noqa: BLE001 - a failed snapshot is skipped, the next one tried
            errors += 1
        sleep(INTERVAL)
    return {"snapshots": taken, "errors": errors, "stocks": len(flow.stocks)}
