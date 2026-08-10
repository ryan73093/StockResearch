from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from quant_platform.application.ports import (
    FeatureLabelStoreRepository,
    MacroDataRepository,
    ResearchUniverseRepository,
    SchedulerJobRunRepository,
)
from quant_platform.domain.entities import FeatureDefinition, FeatureValue, JobRunStatus


MACRO_SERIES = {
    "CPIAUCSL": "美國消費者物價指數",
    "FEDFUNDS": "聯邦基金有效利率",
    "UNRATE": "美國失業率",
    "GDP": "美國名目 GDP",
    "DGS10": "美國十年期公債殖利率",
    "DGS2": "美國兩年期公債殖利率",
    "T10Y2Y": "十年期減兩年期利差",
}
MACRO_FEATURES = {
    "CPIAUCSL": "cpi_yoy",
    "FEDFUNDS": "fed_funds_rate",
    "UNRATE": "unemployment_rate",
    "GDP": "gdp_yoy",
    "DGS10": "treasury_10y",
    "DGS2": "treasury_2y",
    "T10Y2Y": "yield_curve_10y2y",
}


@dataclass(frozen=True, slots=True)
class MacroPipelineResult:
    run_id: int
    status: str
    received: int
    inserted: int
    feature_values: int
    failed: int
    failures: dict[str, str]


class MacroDataPipeline:
    """Downloads macro series, preserves revisions and materializes point-in-time features."""

    def __init__(
        self,
        universe: ResearchUniverseRepository,
        repository: MacroDataRepository,
        features: FeatureLabelStoreRepository,
        runs: SchedulerJobRunRepository,
        provider: object,
    ) -> None:
        self._universe = universe
        self._repository = repository
        self._features = features
        self._runs = runs
        self._provider = provider

    def run(
        self,
        now: datetime | None = None,
        download: bool = True,
        symbols: list[str] | None = None,
    ) -> MacroPipelineResult:
        started = now or datetime.now(UTC)
        run_id = self._runs.start("fred_macro_data", "GLOBAL", started)
        received = inserted = 0
        failures: dict[str, str] = {}
        if download:
            for series in MACRO_SERIES:
                try:
                    values = self._provider.fetch(
                        series, datetime(2010, 1, 1, tzinfo=UTC), started
                    )
                    received += len(values)
                    inserted += self._repository.add_revisions(values)
                except Exception as exc:
                    failures[series] = str(exc)
        definitions = [
            FeatureDefinition(
                name,
                "1.0.0",
                "macro",
                MACRO_SERIES[series],
                1,
                json.dumps({"series_id": series}),
            )
            for series, name in MACRO_FEATURES.items()
        ]
        self._features.register_definitions(definitions)
        materialized = self._materialize(started, symbols=symbols)
        feature_count = self._features.upsert_features(materialized)
        status = (
            JobRunStatus.SUCCEEDED
            if not failures
            else JobRunStatus.PARTIAL
            if received
            else JobRunStatus.FAILED
        )
        result = MacroPipelineResult(
            run_id, status.value, received, inserted, feature_count, len(failures), failures
        )
        self._runs.finish(
            run_id,
            status.value,
            datetime.now(UTC),
            json.dumps(asdict(result), ensure_ascii=False),
            json.dumps(failures, ensure_ascii=False) if failures else None,
        )
        return result

    def _materialize(
        self, computed_at: datetime, symbols: list[str] | None = None
    ) -> list[FeatureValue]:
        requested = {item.strip().upper() for item in symbols or []}
        assets = [
            item.symbol
            for item in self._universe.list_active()
            if item.asset_type in {"EQUITY", "ETF"}
            and (not requested or item.symbol in requested)
        ]
        output: list[FeatureValue] = []
        for series in MACRO_SERIES:
            rows = sorted(self._repository.list_latest(series), key=lambda x: x.event_time)
            for index, row in enumerate(rows):
                if series in {"CPIAUCSL", "GDP"}:
                    lag = 12 if series == "CPIAUCSL" else 4
                    if index < lag or rows[index - lag].value == 0:
                        continue
                    value = row.value / rows[index - lag].value - 1
                else:
                    value = row.value
                for symbol in assets:
                    output.append(
                        FeatureValue(
                            symbol,
                            MACRO_FEATURES[series],
                            "1.0.0",
                            row.event_time,
                            row.available_time,
                            computed_at,
                            float(value),
                        )
                    )
        return output


class MacroDataOverviewService:
    def __init__(self, repository: MacroDataRepository) -> None:
        self._repository = repository

    def get_overview(self) -> dict[str, object]:
        series: list[dict[str, object]] = []
        for series_id, name in MACRO_SERIES.items():
            rows = sorted(self._repository.list_latest(series_id), key=lambda x: x.event_time)
            latest = rows[-1] if rows else None
            series.append(
                {
                    "series_id": series_id,
                    "name": name,
                    "row_count": len(rows),
                    "latest": {
                        "event_time": latest.event_time,
                        "available_time": latest.available_time,
                        "value": latest.value,
                        "source": latest.source,
                    }
                    if latest
                    else None,
                    "points": [
                        {"date": row.event_time.date().isoformat(), "value": row.value}
                        for row in rows[-120:]
                    ],
                }
            )
        return {
            "series": series,
            "total_rows": sum(int(item["row_count"]) for item in series),
            "series_count": len(series),
        }
