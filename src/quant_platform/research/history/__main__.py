"""Long-history research dataset CLI (S3-W01, S3-W02).

    python -m quant_platform.research.history fetch [--series 0050,TAIEX]
    python -m quant_platform.research.history build [--series ...]   # from cache only
    python -m quant_platform.research.history actions                # dividends, splits, total return
    python -m quant_platform.research.history status
    python -m quant_platform.research.history crosscheck [--series ...]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from quant_platform.research.history.actions import build_actions
from quant_platform.research.history.catalog import SERIES_BY_KEY
from quant_platform.research.history.crosscheck import _yahoo_json, crosscheck
from quant_platform.research.history.dataset import HistoryDataset
from quant_platform.research.history.odd_lot import build_odd_lot_report
from quant_platform.research.history.official import OfficialHistoryClient, SourceRefused

DEFAULT_BASE = Path("instance") / "research" / "history"


def _keys(value: str | None) -> list[str] | None:
    if not value:
        return None
    keys = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [key for key in keys if key not in SERIES_BY_KEY]
    if unknown:
        raise SystemExit(f"未知序列：{', '.join(unknown)}；可用：{', '.join(SERIES_BY_KEY)}")
    return keys


def _summary(manifest: dict) -> None:
    for key, entry in (manifest.get("series") or {}).items():
        quality = entry.get("quality") or {}
        stress = quality.get("stress_covered") or {}
        print(
            f"{key:9s} {quality.get('rows', 0):6,d} 筆 {quality.get('first')}～{quality.get('last')} "
            f"缺交易日 {quality.get('missing_sessions', 0)}、無成交 {quality.get('no_trade_days', 0)}、"
            f"開高低收異常 {len(quality.get('ohlc_inconsistent') or [])}、分割註記 {quality.get('split_markers') or '無'}；"
            f"涵蓋下跌期 {sum(stress.values())}/{len(stress)}",
            flush=True,
        )
    if manifest.get("requests"):
        print(f"請求：網路 {manifest['requests']['network']} 次、快取 {manifest['requests']['cache']} 次")


def main() -> int:
    parser = argparse.ArgumentParser(description="長歷史研究資料集")
    parser.add_argument("command", choices=("fetch", "build", "actions", "oddlot", "status", "crosscheck"))
    parser.add_argument("--every", type=int, default=5, help="oddlot：每幾個交易日抽樣一次")
    parser.add_argument("--series", help="逗號分隔，預設全部")
    parser.add_argument("--base", default=str(DEFAULT_BASE))
    args = parser.parse_args()
    base = Path(args.base)
    keys = _keys(args.series)

    if args.command == "status":
        manifest_path = base / "manifest.json"
        if not manifest_path.is_file():
            print("尚未建立資料集")
            return 1
        _summary(json.loads(manifest_path.read_text(encoding="utf-8")))
        return 0
    if args.command == "crosscheck":
        report = crosscheck(base, keys=keys)
        for key, entry in report["series"].items():
            print(
                f"{key:9s} 共同 {entry['common_days']:5d} 天；僅官方 {entry['only_official']}、僅 Yahoo {entry['only_yahoo']}；"
                f">0.5% {entry['over_0_5pct']}、>2% {entry['over_2pct']}；最大 {entry['max_relative_difference']}；"
                f"分割 {entry['splits'] or '無'}；Yahoo 起 {entry['first_yahoo_day']}",
                flush=True,
            )
        print(f"報告：{base / 'crosscheck.json'}")
        return 0

    if args.command == "oddlot":
        try:
            report = build_odd_lot_report(
                base, OfficialHistoryClient(base / "raw"), every=args.every,
                progress=lambda message: print(message, flush=True),
            )
        except SourceRefused as exc:
            print(f"官方來源拒絕連線，已停止：{exc}", file=sys.stderr)
            return 2
        for key, entry in report["series"].items():
            if entry.get("traded_sessions"):
                print(
                    f"{key:7s} 抽樣 {entry['sampled_sessions']} 天、有成交 {entry['traded_sessions']} 天；"
                    f"成交價相對收盤 中位 {entry['median_bps']:+.1f} bps、P10 {entry['p10_bps']:+.1f}、P90 {entry['p90_bps']:+.1f}；"
                    f"不高於收盤 {entry['at_or_below_close']:.0%}",
                    flush=True,
                )
        print(f"報告：{base / 'odd_lot.json'}")
        return 0

    if args.command == "actions":
        client = OfficialHistoryClient(base / "raw")
        try:
            report = build_actions(
                base, client, yahoo_fetch=_yahoo_json, progress=lambda message: print(message, flush=True)
            )
        except SourceRefused as exc:
            print(f"官方來源拒絕連線，已停止：{exc}", file=sys.stderr)
            return 2
        for key, entry in report["series"].items():
            print(
                f"{key:7s} {entry['first']}～{entry['last']} 現金股利 {entry['cash_dividends']} 次、"
                f"分割 {entry['splits'] or '無'}；總報酬年化 {entry['total_return_cagr']:.2%}"
                f"（未調整價格 {entry['price_only_cagr_unadjusted']:.2%}）；來源 {', '.join(entry['sources']) or '無'}",
                flush=True,
            )
        print(f"報告：{base / 'actions.json'}")
        return 0

    client = OfficialHistoryClient(base / "raw", offline=args.command == "build")
    dataset = HistoryDataset(base, client=client, progress=lambda message: print(message, flush=True))
    try:
        manifest = dataset.build(keys)
    except SourceRefused as exc:
        print(f"官方來源拒絕連線，已停止（快取保留，可稍後續抓）：{exc}", file=sys.stderr)
        return 2
    _summary(manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
