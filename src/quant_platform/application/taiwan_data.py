from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from collections.abc import Callable
from typing import Any

from quant_platform.application.ports import (
    FeatureLabelStoreRepository,
    ProviderAccessBlockedError,
    ResearchUniverseRepository,
    SchedulerJobRunRepository,
    TaiwanDataProvider,
    TaiwanDataRepository,
)
from quant_platform.domain.entities import (
    FeatureDefinition,
    FeatureValue,
    JobRunStatus,
    TaiwanDataCoverage,
    TaiwanDataRecord,
)

logger = logging.getLogger(__name__)

TAIWAN_DATASETS = {
    "TaiwanStockInstitutionalInvestorsBuySell": "三大法人買賣",
    "TaiwanStockMarginPurchaseShortSale": "融資融券",
    "TaiwanStockSecuritiesLending": "借券",
    "TaiwanStockPER": "本益比、股價淨值比與殖利率",
    "TaiwanStockMonthRevenue": "月營收",
    "TaiwanStockFinancialStatements": "綜合損益表",
    "TaiwanStockBalanceSheet": "資產負債表",
    "TaiwanStockCashFlowsStatement": "現金流量表",
    "TaiwanStockNews": "個股新聞",
    "TaiwanStockShareholdingDistribution": "集保戶股權分散",
}
QUARTERLY_DATASETS = {
    "TaiwanStockFinancialStatements",
    "TaiwanStockBalanceSheet",
    "TaiwanStockCashFlowsStatement",
}

TAIWAN_FEATURES = (
    FeatureDefinition("institutional_net_buy", "1.0.0", "籌碼", "三大法人買賣超股數", 1, "{}"),
    FeatureDefinition("foreign_net_buy", "1.0.0", "籌碼", "外資買賣超股數", 1, "{}"),
    FeatureDefinition("investment_trust_net_buy", "1.0.0", "籌碼", "投信買賣超股數", 1, "{}"),
    FeatureDefinition("dealer_net_buy", "1.0.0", "籌碼", "自營商買賣超股數", 1, "{}"),
    FeatureDefinition("margin_purchase_balance", "1.0.0", "籌碼", "融資今日餘額", 1, "{}"),
    FeatureDefinition("short_sale_balance", "1.0.0", "籌碼", "融券今日餘額", 1, "{}"),
    FeatureDefinition("securities_lending_quantity", "1.0.0", "籌碼", "借券交易數量", 1, "{}"),
    FeatureDefinition("pe_ratio", "1.0.0", "估值", "本益比", 1, "{}"),
    FeatureDefinition("pb_ratio", "1.0.0", "估值", "股價淨值比", 1, "{}"),
    FeatureDefinition("dividend_yield", "1.0.0", "估值", "現金殖利率", 1, "{}"),
    FeatureDefinition("monthly_revenue", "1.0.0", "基本面", "單月營收", 1, "{}"),
    FeatureDefinition("quarterly_eps", "1.0.0", "基本面", "季度基本每股盈餘", 1, "{}"),
    FeatureDefinition("roe_annualized", "1.0.0", "基本面", "年化股東權益報酬率", 1, "{}"),
    FeatureDefinition("roa_annualized", "1.0.0", "基本面", "年化資產報酬率", 1, "{}"),
    FeatureDefinition("gross_margin", "1.0.0", "基本面", "營業毛利率", 1, "{}"),
    FeatureDefinition("free_cash_flow", "1.0.0", "基本面", "自由現金流", 1, "{}"),
    FeatureDefinition("free_cash_flow_margin", "1.0.0", "基本面", "自由現金流率", 1, "{}"),
    FeatureDefinition("news_sentiment_daily", "1.0.0", "市場情緒", "當日個股新聞文字情緒分數", 1, "{}"),
    FeatureDefinition("large_holder_ratio_1000_lots", "1.0.0", "籌碼", "持股一千張以上占集保庫存比例", 1, "{}"),
    FeatureDefinition("retail_holder_ratio_under_1_lot", "1.0.0", "籌碼", "持股未滿一張占集保庫存比例", 1, "{}"),
    FeatureDefinition("large_holder_count_1000_lots", "1.0.0", "籌碼", "持股一千張以上人數", 1, "{}"),
    FeatureDefinition("shareholder_count", "1.0.0", "籌碼", "集保戶總人數", 1, "{}"),
)


@dataclass(frozen=True, slots=True)
class TaiwanDataPipelineResult:
    run_id: int
    status: str
    asset_count: int
    dataset_count: int
    received: int
    inserted: int
    feature_values: int
    failed: int
    failures: dict[str, str]


@dataclass(frozen=True, slots=True)
class TaiwanDataPreview:
    symbol: str
    dataset: str
    dataset_name: str
    event_time: datetime
    available_time: datetime
    fields: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TaiwanFinancialSnapshot:
    symbol: str
    event_time: datetime
    available_time: datetime
    values: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class TaiwanDataOverview:
    coverage: tuple[TaiwanDataCoverage, ...]
    latest: tuple[TaiwanDataPreview, ...]
    dataset_names: dict[str, str]
    total_rows: int
    symbol_count: int
    last_available_time: datetime | None
    financial_snapshots: tuple[TaiwanFinancialSnapshot, ...]


class TaiwanDataPipeline:
    def __init__(
        self,
        universe_repository: ResearchUniverseRepository,
        data_repository: TaiwanDataRepository,
        feature_repository: FeatureLabelStoreRepository,
        run_repository: SchedulerJobRunRepository,
        provider: TaiwanDataProvider,
        paused: Callable[[], bool] | None = None,
    ) -> None:
        # S9-W04 (2026-10-06): the SQLite Taiwan data (chips, revenue, news) is retired while the legacy
        # research is paused; the research keeps its own (history/chips, nightly from the exchanges)
        self._paused = paused
        self._universe_repository = universe_repository
        self._data_repository = data_repository
        self._feature_repository = feature_repository
        self._run_repository = run_repository
        self._provider = provider

    def provider_status(self) -> dict[str, Any]:
        status = getattr(self._provider, "status", None)
        return status() if callable(status) else {}

    def rebuild_features(self, now: datetime | None = None) -> int:
        """Rebuild derived Taiwan features from raw records already in storage.

        A download can be interrupted after its raw revisions were committed but
        before feature materialization finishes.  Requiring another full network
        download makes recovery slow and leaves a populated database looking
        empty to the research layer, so recovery is intentionally local-only.
        """
        started = now or datetime.now(UTC)
        if started.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        run_id = self._run_repository.start("taiwan_feature_rebuild", "TW", started)
        try:
            assets = [
                item.symbol
                for item in self._universe_repository.list_active("TW")
                if item.symbol.endswith((".TW", ".TWO"))
            ]
            self._feature_repository.register_definitions(list(TAIWAN_FEATURES))
            features = self._materialize_features(
                assets=assets,
                computed_at=started,
                records=None,
            )
            feature_count = self._feature_repository.upsert_features(features)
        except Exception as exc:
            self._run_repository.finish(
                run_id,
                JobRunStatus.FAILED.value,
                datetime.now(UTC),
                json.dumps({"feature_values": 0}, ensure_ascii=False),
                str(exc),
            )
            raise
        self._run_repository.finish(
            run_id,
            JobRunStatus.SUCCEEDED.value,
            datetime.now(UTC),
            json.dumps(
                {"asset_count": len(assets), "feature_values": feature_count},
                ensure_ascii=False,
            ),
            None,
        )
        return feature_count

    def run(
        self,
        market: str = "TW",
        now: datetime | None = None,
        full_refresh: bool = False,
        symbols: list[str] | None = None,
    ) -> TaiwanDataPipelineResult:
        if market.upper() != "TW":
            return TaiwanDataPipelineResult(0, "skipped", 0, 0, 0, 0, 0, 0, {})
        if self._paused is not None and self._paused():
            return TaiwanDataPipelineResult(0, "paused", 0, 0, 0, 0, 0, 0, {
                "legacy_research": "舊版台股資料流程已停用（舊版研究暫停中）；籌碼與基本面改由研究資料每晚 21:30 更新"})
        started = now or datetime.now(UTC)
        if started.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        selected_symbols = {item.strip().upper() for item in symbols} if symbols else None
        assets = [
            item
            for item in self._universe_repository.list_active("TW")
            if item.symbol.endswith((".TW", ".TWO"))
            and (selected_symbols is None or item.symbol in selected_symbols)
        ]
        run_id = self._run_repository.start("taiwan_research_data", "TW", started)
        received = 0
        inserted = 0
        failures: dict[str, str] = {}
        successful_calls = 0
        successful_datasets: set[str] = set()
        materialized_records: list[TaiwanDataRecord] = []
        provider_blocked = False
        ordered_datasets = (
            "TaiwanStockPER",
            *(name for name in TAIWAN_DATASETS if name != "TaiwanStockPER"),
        )
        fetch_many = getattr(self._provider, "fetch_many", None)
        batch_datasets = set(getattr(
            self._provider, "batch_datasets", {"TaiwanStockPER"}
        ))
        if callable(fetch_many) and assets:
            for dataset in tuple(
                item for item in ordered_datasets if item in batch_datasets
            ):
                try:
                    values = fetch_many(
                        dataset,
                        [item.symbol for item in assets],
                        min(
                            datetime.combine(item.data_start, time.min, tzinfo=UTC)
                            for item in assets
                        ),
                        started,
                    )
                    self._validate(values)
                    received += len(values)
                    inserted += self._data_repository.add_revisions(values)
                    materialized_records.extend(values)
                    successful_calls += 1
                    successful_datasets.add(dataset)
                except Exception as exc:
                    logger.exception(
                        "Official Taiwan market-wide %s ingestion failed", dataset
                    )
                    failures[f"market/{dataset}"] = str(exc)
                finally:
                    # A failed market-wide endpoint must not be retried once per
                    # security; that was the source of hour-long partial runs.
                    ordered_datasets = tuple(
                        item for item in ordered_datasets if item != dataset
                    )
        for asset in assets:
            for dataset in ordered_datasets:
                if (
                    provider_blocked
                    and getattr(self._provider, "continues_after_primary_block", False)
                    and dataset != "TaiwanStockPER"
                ):
                    continue
                key = f"{asset.symbol}/{dataset}"
                try:
                    source_for = getattr(self._provider, "source_for", None)
                    source = (
                        source_for(dataset) if callable(source_for) else self._provider.name
                    )
                    latest = self._data_repository.latest_event_time(
                        dataset, asset.symbol, source
                    )
                    if full_refresh or latest is None:
                        start = datetime.combine(asset.data_start, time.min, tzinfo=UTC)
                    else:
                        start = self._aware(latest) - timedelta(days=40)
                    values = self._provider.fetch(dataset, asset.symbol, start, started)
                    self._validate(values)
                    received += len(values)
                    inserted += self._data_repository.add_revisions(values)
                    materialized_records.extend(values)
                    successful_calls += 1
                    successful_datasets.add(dataset)
                except ProviderAccessBlockedError as exc:
                    logger.warning("Taiwan data provider access blocked: %s", exc)
                    failures[f"provider/{self._provider.name}"] = str(exc)
                    provider_blocked = True
                    break
                except Exception as exc:
                    logger.exception("Taiwan dataset ingestion failed: %s", key)
                    failures[key] = str(exc)
            if provider_blocked and not getattr(
                self._provider, "continues_after_primary_block", False
            ):
                break

        self._feature_repository.register_definitions(list(TAIWAN_FEATURES))
        features = self._materialize_features(
            assets=[item.symbol for item in assets],
            computed_at=started,
            datasets=successful_datasets,
            records=materialized_records,
        )
        feature_count = self._feature_repository.upsert_features(features)
        completed = datetime.now(UTC)
        if not failures:
            status = JobRunStatus.SUCCEEDED
        elif successful_calls:
            status = JobRunStatus.PARTIAL
        else:
            status = JobRunStatus.FAILED
        result = TaiwanDataPipelineResult(
            run_id=run_id,
            status=status.value,
            asset_count=len(assets),
            dataset_count=len(TAIWAN_DATASETS),
            received=received,
            inserted=inserted,
            feature_values=feature_count,
            failed=len(failures),
            failures=failures,
        )
        self._run_repository.finish(
            run_id,
            status.value,
            completed,
            json.dumps(asdict(result), ensure_ascii=False),
            "; ".join(f"{key}: {value}" for key, value in failures.items()) or None,
        )
        return result

    def _materialize_features(
        self,
        assets: list[str],
        computed_at: datetime,
        datasets: set[str] | None = None,
        records: list[TaiwanDataRecord] | None = None,
    ) -> list[FeatureValue]:
        if records is None:
            records = []
            selected_datasets = datasets or set(TAIWAN_DATASETS)
            selected_assets = set(assets)
            for dataset in selected_datasets:
                records.extend(
                    item
                    for item in self._data_repository.list_records(dataset=dataset)
                    if item.symbol in selected_assets
                )
        latest: dict[tuple[str, str, datetime, str], TaiwanDataRecord] = {}
        for item in records:
            key = (item.dataset, item.symbol, item.event_time, item.record_key)
            if key not in latest or item.ingested_at > latest[key].ingested_at:
                latest[key] = item
        grouped: dict[tuple[str, str, datetime], list[TaiwanDataRecord]] = {}
        for item in latest.values():
            grouped.setdefault((item.dataset, item.symbol, item.event_time), []).append(item)
        output: list[FeatureValue] = []
        for (dataset, symbol, event_time), values in grouped.items():
            raws = [json.loads(item.fields_json) for item in values]
            available = max(self._aware(item.available_time) for item in values)
            feature_values = self._extract(dataset, raws)
            for name, value in feature_values.items():
                if value is None:
                    continue
                output.append(
                    FeatureValue(
                        symbol=symbol,
                        feature_name=name,
                        feature_version="1.0.0",
                        event_time=self._aware(event_time),
                        available_time=available,
                        computed_at=computed_at,
                        value=float(value),
                    )
                )
        quarterly: dict[tuple[str, datetime], dict[str, tuple[list[dict[str, Any]], datetime]]] = {}
        for (dataset, symbol, event_time), values in grouped.items():
            if dataset not in QUARTERLY_DATASETS:
                continue
            quarterly.setdefault((symbol, event_time), {})[dataset] = (
                [json.loads(item.fields_json) for item in values],
                max(self._aware(item.available_time) for item in values),
            )
        for (symbol, event_time), statements in quarterly.items():
            features = self._extract_quarterly(event_time, statements)
            if not features:
                continue
            available = max(item[1] for item in statements.values())
            for name, value in features.items():
                if value is not None:
                    output.append(
                        FeatureValue(
                            symbol=symbol,
                            feature_name=name,
                            feature_version="1.0.0",
                            event_time=self._aware(event_time),
                            available_time=available,
                            computed_at=computed_at,
                            value=float(value),
                        )
                    )
        return output

    def _extract_quarterly(
        self,
        event_time: datetime,
        statements: dict[str, tuple[list[dict[str, Any]], datetime]],
    ) -> dict[str, float | None]:
        income = self._statement_values(statements.get("TaiwanStockFinancialStatements"))
        balance = self._statement_values(statements.get("TaiwanStockBalanceSheet"))
        cashflow = self._statement_values(statements.get("TaiwanStockCashFlowsStatement"))
        revenue = self._first_value(income, "OperatingRevenue", "Revenue", "Income")
        gross_profit = self._first_value(income, "GrossProfit")
        net_income = self._first_value(income, "IncomeAfterTaxes", "NetIncome")
        assets = self._first_value(balance, "TotalAssets", "Assets")
        equity = self._first_value(
            balance, "TotalEquity", "Equity", "EquityAttributableToOwnersOfParent"
        )
        operating_cash = self._first_value(cashflow, "CashFlowsFromOperatingActivities")
        capital_expenditure = self._first_value(
            cashflow,
            "PropertyAndPlantAndEquipment",
            "AcquisitionOfPropertyPlantAndEquipment",
        )
        free_cash_flow = None
        if operating_cash is not None and capital_expenditure is not None:
            free_cash_flow = operating_cash - abs(capital_expenditure)
        annualizer = {3: 4.0, 6: 2.0, 9: 4.0 / 3.0, 12: 1.0}.get(event_time.month, 1.0)
        return {
            "quarterly_eps": self._first_value(income, "EPS", "BasicEarningsPerShare"),
            "roe_annualized": self._safe_ratio(net_income, equity, annualizer),
            "roa_annualized": self._safe_ratio(net_income, assets, annualizer),
            "gross_margin": self._safe_ratio(gross_profit, revenue),
            "free_cash_flow": free_cash_flow,
            "free_cash_flow_margin": self._safe_ratio(free_cash_flow, revenue),
        }

    @staticmethod
    def _statement_values(
        statement: tuple[list[dict[str, Any]], datetime] | None,
    ) -> dict[str, float]:
        if statement is None:
            return {}
        output: dict[str, float] = {}
        for item in statement[0]:
            key = str(item.get("type", ""))
            value = TaiwanDataPipeline._optional_number(item, "value")
            if key and value is not None:
                output[key] = value
        return output

    @staticmethod
    def _first_value(values: dict[str, float], *keys: str) -> float | None:
        for key in keys:
            if key in values:
                return values[key]
        return None

    @staticmethod
    def _safe_ratio(
        numerator: float | None, denominator: float | None, multiplier: float = 1.0
    ) -> float | None:
        if numerator is None or denominator in {None, 0}:
            return None
        return numerator / denominator * multiplier

    def _extract(self, dataset: str, raws: list[dict[str, Any]]) -> dict[str, float | None]:
        if dataset == "TaiwanStockInstitutionalInvestorsBuySell":
            if any("institutional_net_buy" in item for item in raws):
                return {
                    name: sum(
                        self._optional_number(item, name) or 0.0 for item in raws
                    )
                    for name in (
                        "institutional_net_buy", "foreign_net_buy",
                        "investment_trust_net_buy", "dealer_net_buy",
                    )
                }
            return {
                "institutional_net_buy": sum(
                    self._number(item, "buy") - self._number(item, "sell") for item in raws
                )
            }
        if dataset == "TaiwanStockMarginPurchaseShortSale":
            item = raws[-1]
            return {
                "margin_purchase_balance": self._optional_number(item, "MarginPurchaseTodayBalance"),
                "short_sale_balance": self._optional_number(item, "ShortSaleTodayBalance"),
            }
        if dataset == "TaiwanStockSecuritiesLending":
            return {
                "securities_lending_quantity": sum(
                    self._number(item, "volume", "transaction_quantity", "TransactionQuantity")
                    for item in raws
                )
            }
        if dataset == "TaiwanStockPER":
            item = raws[-1]
            return {
                "pe_ratio": self._optional_number(item, "PER"),
                "pb_ratio": self._optional_number(item, "PBR"),
                "dividend_yield": self._optional_number(item, "dividend_yield", "DividendYield"),
            }
        if dataset == "TaiwanStockMonthRevenue":
            return {"monthly_revenue": self._optional_number(raws[-1], "revenue")}
        if dataset == "TaiwanStockNews":
            positive = ("成長", "創高", "上調", "獲利", "擴產", "利多", "優於", "突破", "growth", "upgrade")
            negative = ("衰退", "下修", "虧損", "裁員", "利空", "低於", "違約", "調查", "跌停", "downgrade")
            text = " ".join(
                str(item.get(key, ""))
                for item in raws for key in ("title", "description", "summary")
            ).lower()
            score = sum(text.count(word) for word in positive) - sum(text.count(word) for word in negative)
            return {"news_sentiment_daily": max(-1.0, min(1.0, score / 3))}
        if dataset == "TaiwanStockShareholdingDistribution":
            levels = {
                int(value): item
                for item in raws
                if (value := self._optional_number(item, "holding_level")) is not None
            }
            large = levels.get(15, {})
            retail = levels.get(1, {})
            total = levels.get(17, {})
            return {
                "large_holder_ratio_1000_lots": self._optional_number(
                    large, "ratio_pct"
                ),
                "retail_holder_ratio_under_1_lot": self._optional_number(
                    retail, "ratio_pct"
                ),
                "large_holder_count_1000_lots": self._optional_number(
                    large, "holders"
                ),
                "shareholder_count": self._optional_number(total, "holders"),
            }
        return {}

    @staticmethod
    def _number(item: dict[str, Any], *keys: str) -> float:
        return TaiwanDataPipeline._optional_number(item, *keys) or 0.0

    @staticmethod
    def _optional_number(item: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = item.get(key)
            if value not in {None, "", "-"}:
                try:
                    return float(str(value).replace(",", ""))
                except ValueError:
                    continue
        return None

    @staticmethod
    def _validate(values: list[TaiwanDataRecord]) -> None:
        for item in values:
            if item.event_time.tzinfo is None or item.available_time.tzinfo is None:
                raise ValueError("Taiwan data timestamps must be timezone-aware")
            if item.available_time < item.event_time:
                raise ValueError("available_time cannot precede event_time")
            if item.ingested_at < item.available_time:
                raise ValueError("data cannot be ingested before publication")

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)


class TaiwanDataOverviewService:
    def __init__(
        self,
        repository: TaiwanDataRepository,
        feature_repository: FeatureLabelStoreRepository,
    ) -> None:
        self._repository = repository
        self._feature_repository = feature_repository

    def get_overview(self, symbol: str | None = None) -> TaiwanDataOverview:
        coverage = tuple(
            item for item in self._repository.list_coverage() if not symbol or item.symbol == symbol.upper()
        )
        records = self._repository.list_latest(symbol=symbol)
        latest_by_key: dict[tuple[str, str], TaiwanDataRecord] = {}
        for item in records:
            key = (item.symbol, item.dataset)
            previous = latest_by_key.get(key)
            if previous is None or (item.event_time, item.ingested_at) > (
                previous.event_time,
                previous.ingested_at,
            ):
                latest_by_key[key] = item
        latest = tuple(
            TaiwanDataPreview(
                symbol=item.symbol,
                dataset=item.dataset,
                dataset_name=TAIWAN_DATASETS.get(item.dataset, item.dataset),
                event_time=item.event_time,
                available_time=item.available_time,
                fields=json.loads(item.fields_json),
            )
            for item in sorted(latest_by_key.values(), key=lambda value: (value.symbol, value.dataset))
        )
        symbols = sorted({item.symbol for item in coverage})
        financial_names = {
            "quarterly_eps", "roe_annualized", "roa_annualized", "gross_margin",
            "free_cash_flow", "free_cash_flow_margin",
        }
        feature_rows = self._feature_repository.list_features(symbols, sorted(financial_names))
        latest_features: dict[tuple[str, str], FeatureValue] = {}
        for item in feature_rows:
            key = (item.symbol, item.feature_name)
            previous = latest_features.get(key)
            if previous is None or item.event_time > previous.event_time:
                latest_features[key] = item
        financial_snapshots = []
        for current_symbol in symbols:
            values = [
                item for (row_symbol, _), item in latest_features.items()
                if row_symbol == current_symbol
            ]
            if values:
                financial_snapshots.append(
                    TaiwanFinancialSnapshot(
                        symbol=current_symbol,
                        event_time=max(item.event_time for item in values),
                        available_time=max(item.available_time for item in values),
                        values=tuple(sorted((item.feature_name, item.value) for item in values)),
                    )
                )
        return TaiwanDataOverview(
            coverage=coverage,
            latest=latest,
            dataset_names=TAIWAN_DATASETS,
            total_rows=sum(item.row_count for item in coverage),
            symbol_count=len({item.symbol for item in coverage}),
            last_available_time=max((item.last_available_time for item in coverage), default=None),
            financial_snapshots=tuple(financial_snapshots),
        )
