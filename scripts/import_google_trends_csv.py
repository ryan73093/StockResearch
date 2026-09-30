from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from quant_platform.application.google_trends import GoogleTrendsCsvImportService
from quant_platform.container import build_container
from quant_platform.database.repositories import SqlAlchemyPointInTimeDataRepository


def main() -> None:
    parser = argparse.ArgumentParser(description="匯入 Google Trends 官方介面匯出的 CSV")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument(
        "--downloaded-at",
        help="實際下載時間（ISO 8601，須含時區）；未提供時採目前時間",
    )
    parser.add_argument(
        "--entity-id",
        action="append",
        dest="entity_ids",
        help="覆寫趨勢欄位的關鍵字；多欄時依序重複指定",
    )
    args = parser.parse_args()
    downloaded_at = (
        datetime.fromisoformat(args.downloaded_at) if args.downloaded_at else datetime.now(UTC)
    )
    container = build_container()
    repository = SqlAlchemyPointInTimeDataRepository(container.database.session_factory)
    result = GoogleTrendsCsvImportService(repository).import_csv(
        args.csv_path.read_bytes(),
        downloaded_at=downloaded_at,
        source_uri=args.csv_path.resolve().as_uri(),
        entity_ids=tuple(args.entity_ids) if args.entity_ids else None,
    )
    print(
        f"Google Trends 匯入完成：{result.series_count} 個序列、"
        f"收到 {result.received} 筆、新增 {result.inserted} 筆、"
        f"部分資料 {result.partial_count} 筆"
    )


if __name__ == "__main__":
    main()
