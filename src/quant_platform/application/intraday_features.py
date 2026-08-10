from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from quant_platform.application.ports import (
    FeatureLabelStoreRepository,
    PointInTimeDataRepository,
    ResearchUniverseRepository,
    SchedulerJobRunRepository,
)
from quant_platform.domain.entities import FeatureRevision, FeatureValue, JobRunStatus
from quant_platform.feature_engineering.intraday_derivatives import (
    IntradayDerivativeFeatureEngine,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IntradayFeaturePipelineResult:
    run_id: int
    status: str
    dataset_count: int
    observation_count: int
    computed_feature_count: int
    inserted_revision_count: int
    failed: int
    failures: dict[str, str]
    started_at: datetime
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class IntradayFeaturePreview:
    symbol: str
    feature_name: str
    display_name: str
    value: float
    unit: str
    formatted_value: str
    event_time: datetime
    available_time: datetime
    computed_at: datetime
    input_fingerprint: str
    input_count: int
    source_dataset: str


@dataclass(frozen=True, slots=True)
class IntradayFeatureOverview:
    definition_count: int
    revision_count: int
    symbol_count: int
    last_available_time: datetime | None
    latest: tuple[IntradayFeaturePreview, ...]
    definitions: tuple[dict[str, object], ...]
    recent_runs: tuple[object, ...]


FEATURE_DISPLAY_NAMES = {
    "intraday_return": "盤中報酬",
    "intraday_range_pct": "盤中振幅",
    "intraday_realized_volatility": "盤中實現波動率",
    "intraday_vwap_deviation": "收盤偏離 VWAP",
    "intraday_volume_shares": "盤中成交量",
    "intraday_max_minute_volume_share": "最大分鐘量占比",
    "odd_lot_trade_price": "零股成交價",
    "odd_lot_turnover_twd": "零股成交金額",
    "odd_lot_volume_shares": "零股成交股數",
    "odd_lot_trade_count": "零股成交筆數",
    "odd_lot_average_trade_size": "零股平均每筆股數",
    "odd_lot_spread_bps": "零股最佳報價價差",
    "odd_lot_order_imbalance": "零股報價量不平衡",
    "futures_front_close": "近月期貨收盤",
    "futures_front_return_1d": "近月期貨一日報酬",
    "futures_front_range_pct": "近月期貨振幅",
    "futures_volume_contracts": "近月期貨成交量",
    "futures_open_interest_contracts": "近月未平倉量",
    "futures_open_interest_change_1d": "近月未平倉變化率",
    "option_put_call_volume_ratio": "選擇權賣買權成交量比",
    "option_put_call_oi_ratio": "選擇權賣買權未平倉比",
    "option_total_volume_contracts": "選擇權總成交量",
    "option_active_contract_count": "選擇權有效契約數",
    "option_median_strike": "選擇權履約價中位數",
    "tw_option_vix_level": "臺指選擇權波動率指數",
    "tw_option_vix_change_1d": "臺指選擇權波動率一日變化",
}


class IntradayDerivativeFeaturePipeline:
    def __init__(
        self,
        point_in_time_repository: PointInTimeDataRepository,
        feature_repository: FeatureLabelStoreRepository,
        universe_repository: ResearchUniverseRepository,
        run_repository: SchedulerJobRunRepository,
        engine: IntradayDerivativeFeatureEngine | None = None,
    ) -> None:
        self._point_in_time = point_in_time_repository
        self._features = feature_repository
        self._universe = universe_repository
        self._runs = run_repository
        self._engine = engine or IntradayDerivativeFeatureEngine()
        self._features.register_definitions(list(self._engine.definitions))

    def run(
        self,
        now: datetime | None = None,
        lookback_days: int = 400,
        dataset_keys: list[str] | None = None,
    ) -> IntradayFeaturePipelineResult:
        started = self._aware(now or datetime.now(UTC))
        run_id = self._runs.start("intraday_derivative_feature_build", "TW", started)
        definitions = [
            item for item in self._point_in_time.list_datasets(enabled=True)
            if item.dataset_key in self._engine.supported_datasets
            and (not dataset_keys or item.dataset_key in dataset_keys)
        ]
        resolver = self._symbol_resolver()
        revisions: list[FeatureRevision] = []
        observation_count = 0
        failures: dict[str, str] = {}
        for definition in definitions:
            try:
                observations = self._point_in_time.list_observations(
                    definition.dataset_key,
                    start=started - timedelta(days=max(2, min(3650, lookback_days))),
                    as_of=started,
                )
                observation_count += len(observations)
                revisions.extend(self._engine.compute(
                    definition, observations, resolver, computed_at=started
                ))
            except Exception as exc:
                logger.exception("Intraday feature build failed for %s", definition.dataset_key)
                failures[definition.dataset_key] = str(exc)
        self._validate(revisions, started)
        current_values = [FeatureValue(
            symbol=item.symbol,
            feature_name=item.feature_name,
            feature_version=item.feature_version,
            event_time=item.event_time,
            available_time=item.available_time,
            computed_at=item.computed_at,
            value=item.value,
        ) for item in revisions]
        self._features.upsert_features(current_values)
        inserted = self._features.add_feature_revisions(revisions)
        completed = datetime.now(UTC)
        status = (
            JobRunStatus.SUCCEEDED if not failures else
            JobRunStatus.PARTIAL if revisions else JobRunStatus.FAILED
        )
        result = IntradayFeaturePipelineResult(
            run_id=run_id, status=status.value, dataset_count=len(definitions),
            observation_count=observation_count, computed_feature_count=len(revisions),
            inserted_revision_count=inserted, failed=len(failures), failures=failures,
            started_at=started, completed_at=completed,
        )
        self._runs.finish(
            run_id, status.value, completed,
            json.dumps(asdict(result), ensure_ascii=False, default=str),
            "; ".join(f"{key}: {value}" for key, value in failures.items()) or None,
        )
        return result

    def overview(self, symbol: str | None = None, limit: int = 100) -> IntradayFeatureOverview:
        definition_rows = [
            item for item in self._features.list_definitions()
            if item.name in FEATURE_DISPLAY_NAMES
        ]
        metadata = {item.name: json.loads(item.parameters_json) for item in definition_rows}
        revisions = self._features.list_feature_revisions(
            symbols=[self._normalize_query_symbol(symbol)] if symbol else None,
            feature_names=list(FEATURE_DISPLAY_NAMES), limit=max(1, min(limit, 500)),
        )
        previews = []
        for item in revisions:
            lineage = json.loads(item.lineage_json)
            previews.append(IntradayFeaturePreview(
                symbol=item.symbol, feature_name=item.feature_name,
                display_name=FEATURE_DISPLAY_NAMES.get(item.feature_name, item.feature_name),
                value=item.value, unit=str(metadata.get(item.feature_name, {}).get("unit", "無單位")),
                formatted_value=self._format_value(
                    item.feature_name, item.value,
                    str(metadata.get(item.feature_name, {}).get("unit", "無單位")),
                ),
                event_time=item.event_time, available_time=item.available_time,
                computed_at=item.computed_at, input_fingerprint=item.input_fingerprint,
                input_count=int(lineage.get("input_count", 0)),
                source_dataset=str(lineage.get("source_dataset", "")),
            ))
        return IntradayFeatureOverview(
            definition_count=len(definition_rows), revision_count=len(revisions),
            symbol_count=len({item.symbol for item in revisions}),
            last_available_time=max((item.available_time for item in revisions), default=None),
            latest=tuple(previews),
            definitions=tuple({
                "name": item.name,
                "display_name": FEATURE_DISPLAY_NAMES.get(item.name, item.name),
                "family": item.family, "description": item.description,
                "lookback": item.lookback,
                "unit": metadata[item.name].get("unit", "無單位"),
                "source_datasets": metadata[item.name].get("source_datasets", []),
            } for item in definition_rows),
            recent_runs=tuple(
                item for item in self._runs.list_recent(50)
                if item.job_name == "intraday_derivative_feature_build"
            )[:10],
        )

    def _symbol_resolver(self):
        aliases = {}
        for asset in self._universe.list_all():
            aliases[asset.symbol.upper()] = asset.symbol.upper()
            aliases[asset.symbol.upper().removesuffix(".TW").removesuffix(".TWO")] = asset.symbol.upper()
        def resolve(value: str) -> str:
            normalized = value.upper()
            if normalized in aliases:
                return aliases[normalized]
            if normalized.isdigit() and len(normalized) in {4, 5, 6}:
                return f"{normalized}.TW"
            return normalized
        return resolve

    @staticmethod
    def _format_value(name: str, value: float, unit: str) -> str:
        if unit == "%":
            displayed = value if name.startswith("tw_option_vix") else value * 100
            return f"{displayed:,.2f}%"
        if unit in {"股", "口", "筆", "元", "個契約"}:
            return f"{value:,.0f} {unit}"
        if unit == "bps":
            return f"{value:,.2f} bps"
        if unit == "倍":
            return f"{value:,.3f} 倍"
        return f"{value:,.2f} {unit}"

    @staticmethod
    def _normalize_query_symbol(symbol: str) -> str:
        normalized = symbol.strip().upper()
        if normalized.isdigit() and len(normalized) in {4, 5, 6}:
            return f"{normalized}.TW"
        return normalized

    @staticmethod
    def _validate(values: list[FeatureRevision], as_of: datetime) -> None:
        for item in values:
            event = IntradayDerivativeFeaturePipeline._aware(item.event_time)
            available = IntradayDerivativeFeaturePipeline._aware(item.available_time)
            computed = IntradayDerivativeFeaturePipeline._aware(item.computed_at)
            if available < event or computed < available or available > as_of:
                raise ValueError("衍生特徵違反事件≤來源可用≤計算≤As-of 時間順序")
            if not item.input_fingerprint or len(item.input_fingerprint) != 64:
                raise ValueError("衍生特徵缺少有效輸入資料指紋")

    @staticmethod
    def _aware(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
