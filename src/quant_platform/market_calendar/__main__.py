"""Operator CLI for the Taiwan trading calendar.

Examples (run from the project root)::

    python -m quant_platform.market_calendar status
    python -m quant_platform.market_calendar refresh --year 2026 --year 2027
    python -m quant_platform.market_calendar add-closure 2026-10-15 "颱風休市" \
        --note "人事行政總處公告"
    python -m quant_platform.market_calendar remove-closure 2026-10-15
    python -m quant_platform.market_calendar export-packaged --from-year 2021 --to-year 2026
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from quant_platform.market_calendar.store import MarketCalendarStore, closure_records
from quant_platform.market_calendar.twse import TwseHolidayScheduleClient

PACKAGED_PATH = Path(__file__).resolve().parent / "data" / "twse_closures.json"


def _store(instance_dir: str) -> MarketCalendarStore:
    return MarketCalendarStore(instance_dir, client=TwseHolidayScheduleClient())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m quant_platform.market_calendar")
    parser.add_argument("--instance-dir", default="instance")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    refresh = commands.add_parser("refresh")
    refresh.add_argument("--year", type=int, action="append", required=True)
    add = commands.add_parser("add-closure")
    add.add_argument("day", type=date.fromisoformat)
    add.add_argument("name")
    add.add_argument("--note", default="")
    remove = commands.add_parser("remove-closure")
    remove.add_argument("day", type=date.fromisoformat)
    export = commands.add_parser("export-packaged", help="開發用：更新隨程式發布的日曆檔")
    export.add_argument("--from-year", type=int, required=True)
    export.add_argument("--to-year", type=int, required=True)
    args = parser.parse_args(argv)

    if args.command == "status":
        today = datetime.now(ZoneInfo("Asia/Taipei")).date()
        print(json.dumps(_store(args.instance_dir).status(today), ensure_ascii=False, indent=2))
    elif args.command == "refresh":
        results = _store(args.instance_dir).refresh(args.year)
        for year, count in results.items():
            print(f"{year}: {'尚未公布' if count is None else f'{count} 個休市日'}")
    elif args.command == "add-closure":
        closure = _store(args.instance_dir).add_manual_closure(args.day, args.name, args.note)
        print(f"已登錄 {closure.day} {closure.name}")
    elif args.command == "remove-closure":
        removed = _store(args.instance_dir).remove_manual_closure(args.day)
        print("已移除" if removed else "找不到該日的人工休市紀錄")
    elif args.command == "export-packaged":
        client = TwseHolidayScheduleClient()
        years: dict[str, object] = {}
        fetched_at = datetime.now(UTC).isoformat()
        for year in range(args.from_year, args.to_year + 1):
            closures = client.fetch_year(year)
            if closures is None:
                print(f"{year}: 尚未公布或不提供，略過")
                continue
            years[str(year)] = {
                "fetched_at": fetched_at,
                "source_url": client.source_url(year),
                "closures": closure_records(closures),
            }
            print(f"{year}: {len(closures)} 個休市日")
        PACKAGED_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"source": "TWSE 市場開休市日期", "years": years}
        PACKAGED_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"寫入 {PACKAGED_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
