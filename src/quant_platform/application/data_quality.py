from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta

from quant_platform.application.ports import (
    DataQualityRepository,
    FeatureLabelStoreRepository,
    MacroDataRepository,
    MarketBarRepository,
    PointInTimeDataRepository,
    ResearchUniverseRepository,
    TaiwanDataRepository,
)
from quant_platform.domain.entities import (
    DataQualitySeverity,
    DataQualitySnapshot,
    DataQualityStatus,
    FeatureValue,
    MarketBar,
)


@dataclass(frozen=True, slots=True)
class DataQualityPolicy:
    version: str = "data-quality-v1"
    recent_sessions: int = 63
    minimum_bar_coverage_rate: float = 0.90
    maximum_market_business_day_lag: int = 5
    asset_lag_warning_sessions: int = 1
    asset_lag_critical_sessions: int = 3
    missing_session_warning_rate: float = 0.10
    missing_session_critical_rate: float = 0.25
    minimum_technical_features: int = 8
    minimum_feature_coverage_rate: float = 0.80
    feature_lag_critical_sessions: int = 3


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    code: str
    category: str
    severity: DataQualitySeverity
    symbol: str | None
    message: str
    observed: float | int | str | None
    threshold: float | int | str
    unit: str
    blocks_research: bool


@dataclass(frozen=True, slots=True)
class DataQualityCheck:
    key: str
    label: str
    passed: bool
    value: float | int | str | None
    threshold: float | int | str
    unit: str
    explanation: str


@dataclass(frozen=True, slots=True)
class DataQualitySnapshotView:
    snapshot: DataQualitySnapshot
    issues: tuple[DataQualityIssue, ...]
    checks: tuple[DataQualityCheck, ...]


@dataclass(frozen=True, slots=True)
class DataQualityOverview:
    latest: tuple[DataQualitySnapshotView, ...]
    recent: tuple[DataQualitySnapshotView, ...]
    policy: DataQualityPolicy


class DataQualityGateError(RuntimeError):
    pass


class DataQualityService:
    """Produces persisted, reproducible evidence before research is trusted."""

    _technical_features = (
        "return_1d", "return_5d", "momentum_20d", "momentum_60d",
        "close_to_sma_5", "close_to_sma_20", "rsi_14", "atr_14_pct",
        "volatility_20d", "drawdown_252d",
    )
    _tw_core_datasets = (
        "TaiwanStockInstitutionalInvestorsBuySell",
        "TaiwanStockMarginPurchaseShortSale",
        "TaiwanStockPER",
    )
    _macro_series = (
        "CPIAUCSL", "FEDFUNDS", "UNRATE", "GDP", "DGS10", "DGS2", "T10Y2Y",
    )

    def __init__(
        self,
        repository: DataQualityRepository,
        bars: MarketBarRepository,
        universe: ResearchUniverseRepository,
        features: FeatureLabelStoreRepository,
        taiwan: TaiwanDataRepository,
        macro: MacroDataRepository,
        point_in_time: PointInTimeDataRepository | None = None,
        policy: DataQualityPolicy | None = None,
    ) -> None:
        self._repository = repository
        self._bars = bars
        self._universe = universe
        self._features = features
        self._taiwan = taiwan
        self._macro = macro
        self._point_in_time = point_in_time
        self._policy = policy or DataQualityPolicy()

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @staticmethod
    def _business_days_after(start: date, end: date) -> int:
        if end <= start:
            return 0
        count = 0
        cursor = start + timedelta(days=1)
        while cursor <= end:
            if cursor.weekday() < 5:
                count += 1
            cursor += timedelta(days=1)
        return count

    @staticmethod
    def _finite_price(bar: MarketBar) -> bool:
        values = (bar.open, bar.high, bar.low, bar.close)
        return all(math.isfinite(float(value)) and float(value) > 0 for value in values)

    def evaluate(
        self, market: str, as_of: datetime | None = None, stage: str = "full"
    ) -> DataQualitySnapshotView:
        normalized = market.upper()
        if normalized not in {"TW", "US"}:
            raise ValueError("market must be TW or US")
        if stage not in {"raw", "full"}:
            raise ValueError("stage must be raw or full")
        computed_at = self._aware(as_of or datetime.now(UTC))
        assets = self._universe.list_active(normalized)
        active_symbols = [item.symbol for item in assets]
        coverage = {
            item.symbol: item for item in self._bars.list_coverage()
            if item.market == normalized and item.interval == "1d" and item.symbol in active_symbols
        }
        covered_symbols = [symbol for symbol in active_symbols if symbol in coverage]
        coverage_rate = len(covered_symbols) / len(active_symbols) if active_symbols else 0.0
        data_as_of = max(
            (self._aware(item.last_event_time) for item in coverage.values()), default=None
        )
        issues: list[DataQualityIssue] = []

        def issue(
            code: str, category: str, severity: DataQualitySeverity,
            symbol: str | None, message: str, observed: object,
            threshold: object, unit: str,
        ) -> None:
            issues.append(DataQualityIssue(
                code=code, category=category, severity=severity, symbol=symbol,
                message=message, observed=observed, threshold=threshold, unit=unit,
                blocks_research=severity == DataQualitySeverity.CRITICAL,
            ))

        if not active_symbols:
            issue("EMPTY_UNIVERSE", "完整性", DataQualitySeverity.CRITICAL, None,
                  "目前市場沒有啟用中的研究標的。", 0, "> 0", "個")
        if coverage_rate < self._policy.minimum_bar_coverage_rate:
            issue(
                "BAR_COVERAGE", "完整性", DataQualitySeverity.CRITICAL, None,
                "啟用標的的日線覆蓋率不足。", coverage_rate,
                self._policy.minimum_bar_coverage_rate, "%",
            )
        for symbol in sorted(set(active_symbols) - set(covered_symbols)):
            issue("MISSING_BARS", "完整性", DataQualitySeverity.CRITICAL, symbol,
                  "啟用標的完全沒有日線資料。", 0, "> 0", "筆")

        if data_as_of is not None:
            market_lag = self._business_days_after(data_as_of.date(), computed_at.date())
            if market_lag > self._policy.maximum_market_business_day_lag:
                issue(
                    "MARKET_STALE", "時效性", DataQualitySeverity.CRITICAL, None,
                    "整體市場行情距離檢查時間過久。", market_lag,
                    self._policy.maximum_market_business_day_lag, "個工作日",
                )

        bars_by_symbol: dict[str, list[MarketBar]] = {}
        session_counts: Counter[date] = Counter()
        temporal_violations = invalid_values = 0
        recent_loader = getattr(self._bars, "list_recent_bars", None)
        audit_start = computed_at - timedelta(days=max(180, self._policy.recent_sessions * 3))
        for symbol in covered_symbols:
            rows = (
                recent_loader(symbol, audit_start, computed_at)
                if callable(recent_loader)
                else self._bars.list_bars(symbol, interval="1d", as_of=computed_at)
            )
            bars_by_symbol[symbol] = rows
            for bar in rows:
                event = self._aware(bar.event_time)
                available = self._aware(bar.available_time)
                ingested = self._aware(bar.ingested_at)
                if available < event or ingested < available:
                    temporal_violations += 1
                    issue(
                        "TEMPORAL_ORDER", "時間因果", DataQualitySeverity.CRITICAL,
                        symbol, "行情時間不符合事件≤公開≤匯入的因果順序。",
                        f"{event.isoformat()}／{available.isoformat()}／{ingested.isoformat()}",
                        "事件時間 ≤ 公開時間 ≤ 匯入時間", "時間戳",
                    )
                if (
                    not self._finite_price(bar)
                    or bar.high < max(bar.open, bar.close)
                    or bar.low > min(bar.open, bar.close)
                    or bar.high < bar.low
                    or bar.volume < 0
                ):
                    invalid_values += 1
                    issue(
                        "INVALID_OHLCV", "合理性", DataQualitySeverity.CRITICAL,
                        symbol, "OHLCV 出現負值、非有限值或高低價關係錯誤。",
                        event.date().isoformat(), "價格 > 0、成交量 ≥ 0 且低價 ≤ 開收盤 ≤ 高價",
                        "交易日",
                    )
                session_counts[event.date()] += 1

        consensus_minimum = max(1, math.ceil(max(len(covered_symbols), 1) * 0.50))
        consensus_dates = sorted(
            day for day, count in session_counts.items() if count >= consensus_minimum
        )[-self._policy.recent_sessions:]
        consensus_index = {day: index for index, day in enumerate(consensus_dates)}
        missing_sessions = stale_assets = 0
        for asset in assets:
            rows = bars_by_symbol.get(asset.symbol, [])
            if not rows or not consensus_dates:
                continue
            observed_dates = {self._aware(item.event_time).date() for item in rows}
            expected = [day for day in consensus_dates if day >= asset.data_start]
            missing = sum(day not in observed_dates for day in expected)
            missing_sessions += missing
            missing_rate = missing / len(expected) if expected else 0.0
            if missing_rate >= self._policy.missing_session_critical_rate:
                issue(
                    "SESSION_GAPS", "連續性", DataQualitySeverity.CRITICAL, asset.symbol,
                    "近期共同交易日缺漏比例過高。", missing_rate,
                    self._policy.missing_session_critical_rate, "%",
                )
            elif missing_rate >= self._policy.missing_session_warning_rate:
                issue(
                    "SESSION_GAPS", "連續性", DataQualitySeverity.WARNING, asset.symbol,
                    "近期共同交易日有明顯缺漏。", missing_rate,
                    self._policy.missing_session_warning_rate, "%",
                )
            latest_day = self._aware(rows[-1].event_time).date()
            lag = len(consensus_dates) - 1 - consensus_index.get(latest_day, -1)
            if lag > self._policy.asset_lag_critical_sessions:
                stale_assets += 1
                issue(
                    "ASSET_STALE", "時效性", DataQualitySeverity.CRITICAL, asset.symbol,
                    "標的行情落後市場共同交易日。", lag,
                    self._policy.asset_lag_critical_sessions, "個交易日",
                )
            elif lag > self._policy.asset_lag_warning_sessions:
                stale_assets += 1
                issue(
                    "ASSET_STALE", "時效性", DataQualitySeverity.WARNING, asset.symbol,
                    "標的行情略為落後市場共同交易日。", lag,
                    self._policy.asset_lag_warning_sessions, "個交易日",
                )

        taiwan_keys = self._check_taiwan_and_macro(
            normalized, computed_at, issues, issue, active_symbols
        )
        self._check_point_in_time(normalized, issues, issue)
        feature_coverage_rate: float | None = None
        if stage == "full":
            feature_coverage_rate = self._check_features(
                active_symbols, bars_by_symbol, consensus_dates, computed_at, issues, issue
            )

        temporal_violations = sum(
            item.code in {
                "TEMPORAL_ORDER", "FEATURE_TEMPORAL_ORDER",
                "TW_TEMPORAL_ORDER", "MACRO_TEMPORAL_ORDER",
                "PIT_TEMPORAL_ORDER",
            }
            for item in issues
        )

        blocking_count = sum(item.blocks_research for item in issues)
        warning_count = sum(item.severity == DataQualitySeverity.WARNING for item in issues)
        status = (
            DataQualityStatus.BLOCKED if blocking_count else
            DataQualityStatus.WARNING if warning_count else DataQualityStatus.PASS
        )
        checks = self._checks(
            len(active_symbols), len(covered_symbols), coverage_rate,
            feature_coverage_rate, stale_assets, temporal_violations,
            invalid_values, missing_sessions,
            sum(item.code == "SESSION_GAPS" for item in issues),
        )
        fingerprint_payload = {
            "market": normalized,
            "stage": stage,
            "bars": sorted(
                (item.symbol, item.row_count, self._aware(item.last_event_time).isoformat())
                for item in coverage.values()
            ),
            "feature_coverage_rate": feature_coverage_rate,
            "taiwan": sorted(taiwan_keys),
            "macro": sorted(
                (
                    series, len(rows),
                    self._aware(rows[-1].event_time).isoformat() if rows else None,
                    rows[-1].content_hash if rows else None,
                )
                for series in self._macro_series
                for rows in ([
                    item for item in self._macro.list_latest(series)
                    if self._aware(item.available_time) <= computed_at
                ],)
            ),
            "point_in_time": sorted(
                (
                    item.dataset_key, item.entity_id, item.row_count, item.revision_count,
                    self._aware(item.last_available_time).isoformat(),
                )
                for item in self._point_in_time.list_coverage()
            ) if normalized == "TW" and self._point_in_time is not None else [],
            "issues": [asdict(item) for item in issues],
        }
        fingerprint = hashlib.sha256(
            json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, default=str).encode()
        ).hexdigest()
        snapshot = DataQualitySnapshot(
            id=None, market=normalized, stage=stage, policy_version=self._policy.version,
            data_fingerprint=fingerprint, status=status, data_as_of=data_as_of,
            active_asset_count=len(active_symbols), covered_asset_count=len(covered_symbols),
            bar_coverage_rate=coverage_rate, feature_coverage_rate=feature_coverage_rate,
            stale_asset_count=stale_assets,
            temporal_violation_count=temporal_violations,
            invalid_value_count=invalid_values,
            missing_session_count=missing_sessions, issue_count=len(issues),
            blocking_issue_count=blocking_count, research_allowed=blocking_count == 0,
            issues_json=json.dumps([asdict(item) for item in issues], ensure_ascii=False, default=str),
            checks_json=json.dumps([asdict(item) for item in checks], ensure_ascii=False, default=str),
            computed_at=computed_at,
        )
        snapshot_id = self._repository.save(snapshot)
        return DataQualitySnapshotView(
            snapshot=replace(snapshot, id=snapshot_id), issues=tuple(issues), checks=checks
        )

    def _check_features(
        self, symbols: list[str], bars_by_symbol: dict[str, list[MarketBar]],
        consensus_dates: list[date], as_of: datetime, issues: list[DataQualityIssue], issue,
    ) -> float:
        recent_loader = getattr(self._features, "list_recent_features", None)
        feature_start = as_of - timedelta(days=max(180, self._policy.recent_sessions * 3))
        values = (
            recent_loader(symbols, list(self._technical_features), feature_start)
            if callable(recent_loader)
            else self._features.list_features(symbols, list(self._technical_features))
        )
        latest: dict[tuple[str, str], FeatureValue] = {}
        for value in values:
            event, available, computed = (
                self._aware(value.event_time), self._aware(value.available_time),
                self._aware(value.computed_at),
            )
            if available < event or computed < available or available > as_of:
                issue(
                    "FEATURE_TEMPORAL_ORDER", "時間因果", DataQualitySeverity.CRITICAL,
                    value.symbol, "特徵時間不符合事件≤公開≤計算，或使用檢查時點後資料。",
                    value.feature_name, "事件時間 ≤ 公開時間 ≤ 計算時間 ≤ 檢查時間", "特徵",
                )
            key = (value.symbol, value.feature_name)
            current = latest.get(key)
            if current is None or event > self._aware(current.event_time):
                latest[key] = value
        ready = 0
        date_index = {day: index for index, day in enumerate(consensus_dates)}
        for symbol in symbols:
            names = {name for (row_symbol, name) in latest if row_symbol == symbol}
            rows = bars_by_symbol.get(symbol, [])
            latest_feature_dates = [
                self._aware(value.event_time).date()
                for (row_symbol, _), value in latest.items() if row_symbol == symbol
            ]
            lag = 0
            if rows and latest_feature_dates and consensus_dates:
                feature_day = max(latest_feature_dates)
                lag = len(consensus_dates) - 1 - date_index.get(feature_day, -1)
            sufficient = len(names) >= self._policy.minimum_technical_features
            fresh = bool(latest_feature_dates) and lag <= self._policy.feature_lag_critical_sessions
            if sufficient and fresh:
                ready += 1
            else:
                issue(
                    "FEATURE_COVERAGE", "特徵完整性", DataQualitySeverity.WARNING,
                    symbol, "技術特徵數量不足或落後最新行情。",
                    f"{len(names)} 項／落後 {lag} 個交易日",
                    f"至少 {self._policy.minimum_technical_features} 項且落後不超過 {self._policy.feature_lag_critical_sessions} 個交易日",
                    "狀態",
                )
        rate = ready / len(symbols) if symbols else 0.0
        if rate < self._policy.minimum_feature_coverage_rate:
            issue(
                "FEATURE_MARKET_COVERAGE", "特徵完整性", DataQualitySeverity.CRITICAL,
                None, "具備足量且最新技術特徵的標的比例不足。", rate,
                self._policy.minimum_feature_coverage_rate, "%",
            )
        return rate

    def _check_taiwan_and_macro(
        self, market, as_of, issues, issue, active_symbols
    ) -> set[tuple[str, str]]:
        available: set[tuple[str, str]] = set()
        if market == "TW":
            key_loader = getattr(self._taiwan, "available_dataset_keys", None)
            if callable(key_loader):
                available = key_loader(
                    active_symbols, list(self._tw_core_datasets), as_of
                )
            else:
                latest = self._taiwan.list_latest()
                available = {
                    (item.symbol, item.dataset) for item in latest
                    if self._aware(item.available_time) <= as_of
                }
            for asset in self._universe.list_active("TW"):
                if not asset.symbol.endswith((".TW", ".TWO")):
                    continue
                required = list(self._tw_core_datasets)
                if asset.asset_type == "ETF":
                    required.remove("TaiwanStockPER")
                missing = [
                    name for name in required
                    if (asset.symbol, name) not in available
                ]
                if missing:
                    labels = {
                        "TaiwanStockInstitutionalInvestorsBuySell": "三大法人買賣",
                        "TaiwanStockMarginPurchaseShortSale": "融資融券",
                        "TaiwanStockPER": "本益比／股價淨值比／殖利率",
                    }
                    issue(
                        "TW_CORE_MISSING", "台股研究資料", DataQualitySeverity.WARNING,
                        asset.symbol,
                        "尚缺：" + "、".join(labels[name] for name in missing) + "。",
                        len(missing), 0, "個資料集",
                    )
        missing_macro: list[str] = []
        for series in self._macro_series:
            rows = [item for item in self._macro.list_latest(series) if self._aware(item.available_time) <= as_of]
            if not rows:
                missing_macro.append(series)
                continue
            for row in rows:
                if (
                    self._aware(row.available_time) < self._aware(row.event_time)
                    or self._aware(row.ingested_at) < self._aware(row.available_time)
                ):
                    issue(
                        "MACRO_TEMPORAL_ORDER", "時間因果", DataQualitySeverity.CRITICAL,
                        None, "總經資料時間不符合事件≤公開≤匯入。", series,
                        "事件時間 ≤ 公開時間 ≤ 匯入時間", "序列",
                    )
        if missing_macro:
            issue(
                "MACRO_MISSING", "總經完整性", DataQualitySeverity.WARNING, None,
                "必要總經序列尚未齊全。", len(missing_macro), 0, "個序列",
            )
        return available

    def _check_point_in_time(self, market, issues, issue) -> None:
        if market != "TW" or self._point_in_time is None:
            return
        for dataset in self._point_in_time.list_datasets(enabled=True):
            for row in self._point_in_time.list_observations(dataset.dataset_key, limit=1000):
                event = self._aware(row.event_time)
                available = self._aware(row.available_time)
                ingested = self._aware(row.ingested_at)
                invalid_event_order = dataset.frequency != "event" and available < event
                if invalid_event_order or ingested < available:
                    issue(
                        "PIT_TEMPORAL_ORDER", "時間因果", DataQualitySeverity.CRITICAL,
                        row.entity_id,
                        "Point-in-time 資料時間不符合該資料集的公開與匯入規則。",
                        dataset.display_name,
                        (
                            "來源可用時間 ≤ 匯入時間（排程事件可在發生前公告）"
                            if dataset.frequency == "event" else
                            "事件時間 ≤ 來源可用時間 ≤ 匯入時間"
                        ), "資料集",
                    )

    def _checks(
        self, active: int, covered: int, bar_rate: float,
        feature_rate: float | None, stale: int, temporal: int,
        invalid: int, missing: int, gap_issue_count: int,
    ) -> tuple[DataQualityCheck, ...]:
        return (
            DataQualityCheck("universe", "啟用標的", active > 0, active, "> 0", "個", "Universe 必須有研究標的。"),
            DataQualityCheck("bars", "日線覆蓋率", bar_rate >= self._policy.minimum_bar_coverage_rate, bar_rate, self._policy.minimum_bar_coverage_rate, "%", f"已有 {covered} 個標的具備日線。"),
            DataQualityCheck("temporal", "時間因果違規", temporal == 0, temporal, 0, "筆", "事件、公開、匯入與計算時間不得倒置。"),
            DataQualityCheck("values", "不合理 OHLCV", invalid == 0, invalid, 0, "筆", "價格及成交量必須符合基本市場限制。"),
            DataQualityCheck("stale", "落後市場標的", stale == 0, stale, 0, "個", "以共同交易日比較，不把週末直接算成缺漏。"),
            DataQualityCheck("gaps", "近期缺漏交易日", gap_issue_count == 0, f"{missing} 個交易日（全市場合計）", f"各標的缺漏率 < {self._policy.missing_session_warning_rate * 100:.0f}%", "狀態", "以市場過半標的共有日期建立代理交易日曆。"),
            DataQualityCheck("features", "特徵覆蓋率", feature_rate is None or feature_rate >= self._policy.minimum_feature_coverage_rate, feature_rate if feature_rate is not None else "原始資料階段不檢查", self._policy.minimum_feature_coverage_rate, "%" if feature_rate is not None else "狀態", "完整階段要求每檔至少八項最新技術特徵。"),
        )

    def ensure_research_ready(self, view: DataQualitySnapshotView) -> None:
        if not view.snapshot.research_allowed:
            raise DataQualityGateError(
                f"{view.snapshot.market} 資料品質閘門阻擋研究："
                f"{view.snapshot.blocking_issue_count} 個嚴重問題。"
            )

    def overview(self) -> DataQualityOverview:
        latest = []
        for market in ("TW", "US"):
            snapshot = self._repository.latest(market, "full")
            if snapshot:
                latest.append(self._view(snapshot))
        recent = tuple(self._view(item) for item in self._repository.list_snapshots(limit=30))
        return DataQualityOverview(tuple(latest), recent, self._policy)

    @staticmethod
    def _view(snapshot: DataQualitySnapshot) -> DataQualitySnapshotView:
        issues = tuple(
            DataQualityIssue(
                **{**item, "severity": DataQualitySeverity(item["severity"])}
            )
            for item in json.loads(snapshot.issues_json)
        )
        checks = tuple(DataQualityCheck(**item) for item in json.loads(snapshot.checks_json))
        return DataQualitySnapshotView(snapshot, issues, checks)
