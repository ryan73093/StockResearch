"""Forward simulation from 2026-10-01 (S5-W05).

After each Taiwan session the tracked strategies are replayed from the
forward start to that day with a standard cash-flow plan, and the day's
state and trades are appended to ``instance/research/forward/log.jsonl``.
Records are only appended: a later data revision can change a replay but
never the record written on the day, which is what the promotion gate
(roadmap S4) looks at. Built-in baselines are always tracked; promoted
candidates are added as JSON specs under ``forward/tracked/``.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_platform.research.cashflow import ContributionPlan
from quant_platform.research.costs import CostModel
from quant_platform.research.engine import ENGINE_VERSION, simulate
from quant_platform.research.market import load_market
from quant_platform.research.spec import BASELINES, StrategySpec

TAIPEI = ZoneInfo("Asia/Taipei")
FORWARD_START = date(2026, 10, 1)
STANDARD_PLAN = ContributionPlan(monthly_amount=10_000, day_of_month=5)


class ForwardTracker:
    def __init__(self, research_dir: str | Path, plan: ContributionPlan = STANDARD_PLAN) -> None:
        self._base = Path(research_dir)
        self._folder = self._base / "forward"
        self._plan = plan

    @property
    def log_path(self) -> Path:
        return self._folder / "log.jsonl"

    def tracked(self) -> list[StrategySpec]:
        specs = {spec.spec_hash: spec for spec in BASELINES.values()}
        folder = self._folder / "tracked"
        if folder.is_dir():
            for path in sorted(folder.glob("*.json")):
                spec = StrategySpec.model_validate_json(path.read_text(encoding="utf-8"))
                specs[spec.spec_hash] = spec
        return list(specs.values())

    def records(self) -> list[dict[str, object]]:
        if not self.log_path.is_file():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def record(self, today: date, costs: CostModel | None = None) -> list[dict[str, object]]:
        """Append today's state for every tracked spec that has none yet."""
        if today < FORWARD_START:
            return []
        seen = {(item["date"], item["spec_hash"]) for item in self.records()}
        written = []
        for spec in self.tracked():
            if (today.isoformat(), spec.spec_hash) in seen:
                continue
            assets = sorted(set(spec.assets) | {spec.signal})
            market = load_market(assets, self._base / "history")
            if today not in market.sessions or any(market.close(asset, today) is None for asset in spec.assets):
                continue
            result = simulate(spec, market, self._plan, costs, start=FORWARD_START, end=today)
            if result.end != today:
                continue
            record = {
                "date": today.isoformat(),
                "spec_hash": spec.spec_hash,
                "name": spec.name,
                "value": round(result.final_value, 2),
                "cash": round(result.final_cash, 2),
                "contributed": round(result.total_contributed, 2),
                "trades_today": [
                    {"asset": trade.asset, "side": trade.side, "shares": trade.shares, "price": trade.price}
                    for trade in result.trades if trade.day == today
                ],
                "data_fingerprint": market.fingerprint,
                "engine": ENGINE_VERSION,
                "recorded_at": datetime.now(TAIPEI).isoformat(timespec="seconds"),
            }
            written.append(record)
        if written:
            self._folder.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                for record in written:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return written

    def summary(self) -> list[dict[str, object]]:
        """Latest record per spec, with the difference to the DCA benchmark on that day."""
        latest: dict[str, dict[str, object]] = {}
        days: dict[str, set[str]] = {}
        for record in self.records():
            latest[record["spec_hash"]] = record
            days.setdefault(record["spec_hash"], set()).add(record["date"])
        benchmark = latest.get(BASELINES["benchmark_dca"].spec_hash)
        rows = []
        for spec_hash, record in latest.items():
            difference = None
            if benchmark and benchmark["date"] == record["date"] and record["contributed"]:
                difference = (record["value"] - benchmark["value"]) / record["contributed"]
            rows.append({
                "name": record["name"],
                "date": record["date"],
                "sessions": len(days[spec_hash]),
                "contributed": record["contributed"],
                "value": record["value"],
                "excess": difference,
                "benchmark": spec_hash == BASELINES["benchmark_dca"].spec_hash,
            })
        return sorted(rows, key=lambda row: (not row["benchmark"], row["name"]))
