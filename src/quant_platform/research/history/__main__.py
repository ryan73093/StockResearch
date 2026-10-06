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
    parser.add_argument("command", choices=("fetch", "build", "actions", "oddlot", "status", "crosscheck", "stocks", "finmind",
                                            "chips", "tpex", "fundamentals"))
    parser.add_argument("--datasets", help="finmind：逗號分隔的資料集，預設全部；statements＝三種財報")
    parser.add_argument("--codes", default="twse", choices=("twse", "tpex", "all", "lists"),
                        help="finmind：上市、上櫃、全部，或 lists（下市清單、股票基本資料、期貨法人部位）")
    parser.add_argument("--exchange", default="twse,tpex", help="stocks：twse、tpex 或兩者")
    parser.add_argument("--from-year", type=int, default=2004, help="stocks：起始年")
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
            tr = entry.get("total_return_vs_yahoo_adjclose") or {}
            print(
                f"{key:9s} 共同 {entry['common_days']:5d} 天；僅官方 {entry['only_official']}、僅 Yahoo {entry['only_yahoo']}；"
                f">0.5% {entry['over_0_5pct']}、>2% {entry['over_2pct']}；最大 {entry['max_relative_difference']}；"
                f"分割 {entry['splits'] or '無'}（{entry['split_source'] or '－'}）；"
                f"Yahoo 未調整分割 {entry['yahoo_unadjusted_days']} 天；Yahoo 起 {entry['first_yahoo_day']}；"
                f"總報酬對 Yahoo 還原價 {tr.get('first', '－')} 起年化漂移 {tr.get('annualized_drift', '－')}",
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
                recent = entry.get("recent") or {}
                if recent.get("traded_sessions"):
                    rates = recent["fill_rates"]
                    print(
                        f"        {recent['since']} 起抽樣 {recent['sampled_sessions']} 天：中位 {recent['median_bps']:+.1f} bps、"
                        f"P95 {recent['p95_bps']:+.1f}、最高 {recent['max_bps']:+.1f}；買進限價收盤加 0.2% 成交 {rates['20']:.0%}、"
                        f"加 0.5% {rates['50']:.0%}、加 1% {rates['100']:.0%}、加 1.5% {rates['150']:.0%}",
                        flush=True,
                    )
        print(f"報告：{base / 'odd_lot.json'}")
        return 0

    if args.command == "stocks":
        from datetime import date as _date

        from quant_platform.research.history.dataset import read_series
        from quant_platform.research.history.stocks import TWSE_ALL_START, fetch_exchange, save_summary

        sessions = [row["date"] for row in read_series(base / "daily" / "TAIEX.parquet")]
        sessions = [day for day in sessions if day >= max(TWSE_ALL_START, _date(args.from_year, 1, 1))]
        client = OfficialHistoryClient(base / "raw")
        for exchange in [item.strip() for item in args.exchange.split(",") if item.strip()]:
            try:
                result = fetch_exchange(exchange, client, sessions, base, progress=lambda m: print(m, flush=True))
            except SourceRefused as exc:
                print(f"{exchange}：官方來源拒絕連線，已停止：{exc}", file=sys.stderr)
                return 2
            print(f"{exchange}：請求 {result['requests']} 次、寫入年份 {result['years']}", flush=True)
        path = save_summary(base)
        print(json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False))
        return 0

    if args.command == "finmind":
        from quant_platform.config import Settings
        from quant_platform.research.history.finmind import (
            DATASETS, STATEMENTS, fetch_all, fetch_lists, stock_codes, tpex_codes,
        )
        from quant_platform.research.jobs import JobLog

        token = Settings.from_env().finmind_token
        if not token:
            print("沒有 FINMIND_TOKEN（.env）", file=sys.stderr)
            return 2
        if args.codes == "lists":
            with JobLog(base.parent).start("下載清單資料（FinMind：下市、股票基本資料、期貨法人部位）",
                                           "python -m quant_platform.research.history " + " ".join(sys.argv[1:])) as job:
                counts = fetch_lists(base, token)
                job.payload["summary"] = "、".join(f"{key} {value:,} 列" for key, value in counts.items())
            print(json.dumps(counts, ensure_ascii=False), flush=True)
            return 0
        text = args.datasets or ",".join(DATASETS)
        text = text.replace("statements", ",".join(STATEMENTS))
        datasets = [item.strip() for item in text.split(",") if item.strip()]
        codes = {"twse": stock_codes(base), "tpex": tpex_codes(base),
                 "all": sorted(set(stock_codes(base)) | set(tpex_codes(base)))}[args.codes]
        market = {"twse": "上市", "tpex": "上櫃", "all": "上市＋上櫃"}[args.codes]
        with JobLog(base.parent).start(f"下載 FinMind（{market}，{len(datasets)} 種 × {len(codes)} 檔：{'、'.join(datasets)}）"[:120],
                                       "python -m quant_platform.research.history " + " ".join(sys.argv[1:])) as job:
            result = fetch_all(base, token, codes, datasets, job=job)
        print(json.dumps(result, ensure_ascii=False)[:2000], flush=True)
        return 0

    if args.command == "fundamentals":      # 2026-10-06: the quarterly statement table
        from quant_platform.research.fundamentals import build as build_fundamentals

        print(json.dumps(build_fundamentals(base), ensure_ascii=False), flush=True)
        return 0

    if args.command == "tpex":
        from quant_platform.research.history.stocks import build_tpex_from_finmind, save_summary

        result = build_tpex_from_finmind(base)
        save_summary(base)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0

    if args.command == "chips":
        from quant_platform.research.chips import build as build_chips
        from quant_platform.research.jobs import JobLog

        with JobLog(base.parent).start("整理籌碼與基本面資料（FinMind → Parquet）",
                                       "python -m quant_platform.research.history chips") as job:
            written = build_chips(base, job=job)
        print(json.dumps(written, ensure_ascii=False), flush=True)
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
            check = entry.get("yahoo_dividend_check") or {}
            print(
                f"{key:7s} {entry['first']}～{entry['last']} 現金股利 {entry['cash_dividends']} 次、"
                f"配股 {entry['stock_dividends']} 次、分割 {entry['splits'] or '無'}；"
                f"總報酬年化 {entry['total_return_cagr']:.2%}"
                f"（未調整價格 {entry['price_only_cagr_unadjusted']:.2%}）；"
                f"參考價不符 {len(entry['reference_mismatches'])} 筆；"
                f"Yahoo 股利 {check.get('yahoo_events', '－')} 次、金額不符 {len(check.get('amount_mismatches') or [])} 筆；"
                f"來源 {', '.join(entry['sources']) or '無'}",
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
