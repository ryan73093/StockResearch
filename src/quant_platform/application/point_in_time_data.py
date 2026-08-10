from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from quant_platform.application.ports import PointInTimeDataRepository
from quant_platform.domain.entities import (
    PointInTimeCoverage,
    PointInTimeDataset,
    PointInTimeObservation,
)


FINMIND_TECHNICAL_URL = "https://finmind.github.io/tutor/TaiwanMarket/Technical/"
FINMIND_DERIVATIVE_URL = "https://finmind.github.io/tutor/TaiwanMarket/Derivative/"
TWSE_OPENAPI_URL = "https://openapi.twse.com.tw/"


CATALOG_SPECS: tuple[dict[str, object], ...] = (
    {
        "dataset_key": "tw_stock_1m", "display_name": "台股一分鐘 K 線",
        "category": "盤中行情", "market": "TW", "frequency": "1m",
        "entity_type": "股票", "source": "finmind", "source_dataset": "TaiwanStockKBar",
        "access_tier": "sponsor", "history_start": date(2019, 1, 1),
        "unit_schema": {"open": "元／股", "high": "元／股", "low": "元／股", "close": "元／股", "volume": "股"},
        "publication_rule": "歷史批次資料於交易日 15:50 後可用；不可假裝成盤中即時可見。",
        "revision_policy": "同一股票、分鐘與來源內容保留修訂；as-of 只取當時已公布版本。",
        "source_url": FINMIND_TECHNICAL_URL, "enabled": True,
    },
    {
        "dataset_key": "tw_futures_daily", "display_name": "台灣期貨日成交資訊",
        "category": "期貨", "market": "TW", "frequency": "1d",
        "entity_type": "期貨契約", "source": "finmind", "source_dataset": "TaiwanFuturesDaily",
        "access_tier": "free_or_token", "history_start": None,
        "unit_schema": {"open": "指數點／元", "max": "指數點／元", "min": "指數點／元", "close": "指數點／元", "volume": "口", "open_interest": "口"},
        "publication_rule": "交易日收盤後批次資料，保守採台北時間 18:00 可用。",
        "revision_policy": "契約月份與交易時段分開保存，來源更正視為新修訂。",
        "source_url": FINMIND_DERIVATIVE_URL, "enabled": True,
    },
    {
        "dataset_key": "tw_options_daily", "display_name": "台灣選擇權日成交資訊",
        "category": "選擇權", "market": "TW", "frequency": "1d",
        "entity_type": "選擇權契約", "source": "finmind", "source_dataset": "TaiwanOptionDaily",
        "access_tier": "free_or_token", "history_start": None,
        "unit_schema": {"strike_price": "指數點／元", "open": "點／元", "close": "點／元", "volume": "口", "open_interest": "口"},
        "publication_rule": "交易日收盤後批次資料，保守採台北時間 18:00 可用。",
        "revision_policy": "履約價、買賣權、契約月份與交易時段組成邏輯紀錄鍵。",
        "source_url": FINMIND_DERIVATIVE_URL, "enabled": True,
    },
    {
        "dataset_key": "tw_futures_tick", "display_name": "台灣期貨逐筆成交",
        "category": "期貨", "market": "TW", "frequency": "tick",
        "entity_type": "期貨契約", "source": "finmind", "source_dataset": "TaiwanFuturesTick",
        "access_tier": "sponsorpro", "history_start": date(2026, 1, 2),
        "unit_schema": {"price": "指數點／元", "volume": "口"},
        "publication_rule": "整日物件檔為盤後研究資料，保守採台北時間 18:00 可用。",
        "revision_policy": "逐筆順序與相同秒內發生次數分開保存，不合併不同成交。",
        "source_url": FINMIND_DERIVATIVE_URL, "enabled": True,
    },
    {
        "dataset_key": "tw_options_tick", "display_name": "台灣選擇權逐筆成交",
        "category": "選擇權", "market": "TW", "frequency": "tick",
        "entity_type": "選擇權契約", "source": "finmind", "source_dataset": "TaiwanOptionTick",
        "access_tier": "sponsorpro", "history_start": date(2026, 1, 2),
        "unit_schema": {"ExercisePrice": "指數點／元", "price": "點／元", "volume": "口"},
        "publication_rule": "整日物件檔為盤後研究資料，保守採台北時間 18:00 可用。",
        "revision_policy": "履約價、買賣權、月份與逐筆發生次數共同識別成交。",
        "source_url": FINMIND_DERIVATIVE_URL, "enabled": True,
    },
    {
        "dataset_key": "tw_option_vix", "display_name": "臺指選擇權波動率指數",
        "category": "風險指標", "market": "TW", "frequency": "tick",
        "entity_type": "指數", "source": "finmind", "source_dataset": "TaiwanOptionVix",
        "access_tier": "backer_or_sponsor", "history_start": date(2026, 3, 1),
        "unit_schema": {"vix": "%（波動率指數）"},
        "publication_rule": "官方文件標示每日 18:00 更新，盤中時間戳不等於當時已可下載。",
        "revision_policy": "每個來源時間點保留內容修訂。",
        "source_url": FINMIND_DERIVATIVE_URL, "enabled": True,
    },
    {
        "dataset_key": "tw_odd_lot_daily", "display_name": "集中市場零股交易行情",
        "category": "盤後零股", "market": "TW", "frequency": "1d",
        "entity_type": "股票", "source": "twse_openapi", "source_dataset": "TWT53U",
        "access_tier": "public_latest", "history_start": None,
        "unit_schema": {"TradeVolume": "股", "Transaction": "筆", "TradeValue": "元", "TradePrice": "元／股", "BestBidPrice": "元／股", "BestBidVolume": "股", "BestAskPrice": "元／股", "BestAskVolume": "股", "成交股數": "股", "成交筆數": "筆", "成交金額": "元", "成交價": "元／股"},
        "publication_rule": "證交所公開介面提供行情單；歷史累積必須每日自行快照。",
        "revision_policy": "每日快照不可回填成過去已知資料；更正版本另存。",
        "source_url": TWSE_OPENAPI_URL, "enabled": True,
    },
    {
        "dataset_key": "tw_earnings_call", "display_name": "台股法說會事件",
        "category": "事件", "market": "TW", "frequency": "event",
        "entity_type": "公司", "source": "mops", "source_dataset": "earnings_call",
        "access_tier": "public_current_and_latest_detail", "history_start": None,
        "unit_schema": {"簡報文件數": "份文件", "影音／相關連結數": "個連結"},
        "publication_rule": "每日重大訊息以公司發言時間為可用時間；單一公司最新明細以實際下載時間為可用時間。",
        "revision_policy": "延期、取消、簡報與影音連結更正均新增版本；OpenAPI 歷史由平台每日累積，不回填為過去已知。",
        "source_url": "https://mops.twse.com.tw/mops/", "enabled": True,
    },
    {
        "dataset_key": "google_trends", "display_name": "Google 搜尋趨勢",
        "category": "替代資料", "market": "GLOBAL", "frequency": "1d",
        "entity_type": "關鍵字", "source": "google_trends", "source_dataset": "interest_over_time",
        "access_tier": "adapter_planned", "history_start": None,
        "unit_schema": {"interest": "0～100 相對熱度"},
        "publication_rule": "保存實際下載時間；歷史值可能因取樣與正規化而修訂。",
        "revision_policy": "每次下載均保留內容雜湊，回測只使用當時已取得版本。",
        "source_url": "https://trends.google.com/trends/", "enabled": False,
    },
)


class PointInTimeProvider(Protocol):
    name: str

    def fetch(
        self,
        definition: PointInTimeDataset,
        entity_id: str,
        start: datetime,
        end: datetime,
        ingested_at: datetime,
    ) -> list[PointInTimeObservation]: ...


@dataclass(frozen=True, slots=True)
class PointInTimeIngestionResult:
    dataset_key: str
    entity_id: str
    received: int
    inserted: int
    started_at: datetime
    completed_at: datetime
    status: str


@dataclass(frozen=True, slots=True)
class PointInTimeBatchResult:
    requested: int
    succeeded: int
    failed: int
    received: int
    inserted: int
    errors: dict[str, str]


@dataclass(frozen=True, slots=True)
class PointInTimePreview:
    dataset_key: str
    entity_id: str
    event_time: datetime
    available_time: datetime
    ingested_at: datetime
    revision_key: str
    payload: dict[str, object]
    units: dict[str, str]
    source_uri: str


@dataclass(frozen=True, slots=True)
class PointInTimeOverview:
    datasets: tuple[PointInTimeDataset, ...]
    coverage: tuple[PointInTimeCoverage, ...]
    observations: tuple[PointInTimePreview, ...]
    dataset_count: int
    enabled_count: int
    stored_row_count: int
    revision_count: int
    selected_dataset: str
    selected_entity: str
    selected_as_of: datetime | None
    token_configured: bool


class PointInTimeDataService:
    def __init__(
        self,
        repository: PointInTimeDataRepository,
        provider: PointInTimeProvider,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self.ensure_catalog()

    def ensure_catalog(self, now: datetime | None = None) -> None:
        updated_at = self._aware(now or datetime.now(UTC))
        values = []
        for spec in CATALOG_SPECS:
            data = dict(spec)
            data["unit_schema_json"] = json.dumps(data.pop("unit_schema"), ensure_ascii=False, sort_keys=True)
            data["updated_at"] = updated_at
            values.append(PointInTimeDataset(**data))
        self._repository.upsert_datasets(values)

    def ingest(
        self,
        dataset_key: str,
        entity_id: str,
        start: datetime,
        end: datetime,
        now: datetime | None = None,
    ) -> PointInTimeIngestionResult:
        started = self._aware(now or datetime.now(UTC))
        start, end = self._aware(start), self._aware(end)
        if start > end:
            raise ValueError("開始時間不可晚於結束時間")
        definition = self._repository.get_dataset(dataset_key)
        if definition is None:
            raise ValueError(f"未知資料集：{dataset_key}")
        if not definition.enabled:
            raise ValueError(f"{definition.display_name} 的來源介接器尚未啟用")
        maximum = timedelta(
            days=31 if definition.frequency in {"1m", "tick"}
            else 730 if definition.frequency == "event" else 366
        )
        if end - start > maximum:
            raise ValueError(f"{definition.display_name} 單次最多下載 {maximum.days} 天")
        values = self._provider.fetch(definition, entity_id.strip().upper() or "ALL", start, end, started)
        self._validate(definition, values, started)
        inserted = self._repository.add_revisions(values)
        return PointInTimeIngestionResult(
            dataset_key=dataset_key,
            entity_id=entity_id.strip().upper() or "ALL",
            received=len(values),
            inserted=inserted,
            started_at=started,
            completed_at=datetime.now(UTC),
            status="succeeded",
        )

    def run_scheduled(
        self,
        specifications: str,
        lookback_days: int = 7,
        now: datetime | None = None,
    ) -> PointInTimeBatchResult:
        completed_at = self._aware(now or datetime.now(UTC))
        pairs = []
        for raw in specifications.split(","):
            if not raw.strip():
                continue
            if ":" not in raw:
                raise ValueError(f"排程資料規格錯誤：{raw}，應為 dataset:entity")
            dataset_key, entity_id = raw.split(":", 1)
            pairs.append((dataset_key.strip(), entity_id.strip()))
        errors: dict[str, str] = {}
        results: list[PointInTimeIngestionResult] = []
        for dataset_key, entity_id in pairs:
            try:
                results.append(self.ingest(
                    dataset_key, entity_id,
                    completed_at - timedelta(days=max(1, min(31, lookback_days))),
                    completed_at, now=completed_at,
                ))
            except Exception as exc:
                errors[f"{dataset_key}:{entity_id}"] = str(exc)
        if pairs and not results:
            raise RuntimeError("盤中／衍生資料排程全部失敗：" + "; ".join(
                f"{key}={value}" for key, value in errors.items()
            ))
        return PointInTimeBatchResult(
            requested=len(pairs), succeeded=len(results), failed=len(errors),
            received=sum(item.received for item in results),
            inserted=sum(item.inserted for item in results), errors=errors,
        )

    def query(
        self,
        dataset_key: str,
        entity_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[PointInTimeObservation]:
        return self._repository.list_observations(
            dataset_key,
            entity_id=entity_id,
            start=self._aware(start) if start else None,
            end=self._aware(end) if end else None,
            as_of=self._aware(as_of) if as_of else None,
            limit=max(1, min(limit, 1000)),
        )

    def overview(
        self,
        dataset_key: str = "tw_stock_1m",
        entity_id: str = "2330",
        as_of: datetime | None = None,
        limit: int = 30,
    ) -> PointInTimeOverview:
        datasets = tuple(self._repository.list_datasets())
        definition = next((item for item in datasets if item.dataset_key == dataset_key), None)
        if definition is None and datasets:
            definition = datasets[0]
            dataset_key = definition.dataset_key
        rows = self.query(dataset_key, entity_id or None, as_of=as_of, limit=limit) if definition else []
        units = json.loads(definition.unit_schema_json) if definition else {}
        observations = tuple(PointInTimePreview(
            dataset_key=item.dataset_key,
            entity_id=item.entity_id,
            event_time=item.event_time,
            available_time=item.available_time,
            ingested_at=item.ingested_at,
            revision_key=item.revision_key,
            payload=json.loads(item.payload_json),
            units=units,
            source_uri=item.source_uri,
        ) for item in rows)
        coverage = tuple(self._repository.list_coverage())
        return PointInTimeOverview(
            datasets=datasets,
            coverage=coverage,
            observations=observations,
            dataset_count=len(datasets),
            enabled_count=sum(item.enabled for item in datasets),
            stored_row_count=sum(item.row_count for item in coverage),
            revision_count=sum(item.revision_count for item in coverage),
            selected_dataset=dataset_key,
            selected_entity=entity_id,
            selected_as_of=as_of,
            token_configured=bool(getattr(self._provider, "has_token", False)),
        )

    @staticmethod
    def _validate(
        definition: PointInTimeDataset,
        values: list[PointInTimeObservation],
        as_of: datetime,
    ) -> None:
        for item in values:
            for field in (item.event_time, item.available_time, item.ingested_at):
                if field.tzinfo is None:
                    raise ValueError("Point-in-time 時間必須包含時區")
            if item.dataset_key != definition.dataset_key:
                raise ValueError("來源回傳錯誤的 dataset_key")
            if definition.frequency != "event" and item.available_time < item.event_time:
                raise ValueError("資料可用時間不可早於事件時間")
            if item.ingested_at < item.available_time or item.available_time > as_of:
                raise ValueError("不可保存尚未公開的資料版本")
            payload = json.loads(item.payload_json)
            if not isinstance(payload, dict):
                raise ValueError("觀測值 payload 必須是 JSON 物件")
            if len(item.content_hash) != 64:
                raise ValueError("內容雜湊必須是 SHA-256")

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("時間必須包含時區")
        return value.astimezone(UTC)
