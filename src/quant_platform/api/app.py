from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime

import uvicorn
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from quant_platform import __version__
from quant_platform.application.authentication import api_write_authorization
from quant_platform.container import Container, build_container


class DailyIngestionRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=40, examples=["SPY"])
    market: str = Field(default="US", pattern="^(US|TW)$")
    start: datetime
    end: datetime


class PointInTimeIngestionRequest(BaseModel):
    dataset_key: str = Field(min_length=2, max_length=100, examples=["tw_stock_1m"])
    entity_id: str = Field(min_length=1, max_length=100, examples=["2330"])
    start: datetime
    end: datetime


class GoogleTrendsCsvImportRequest(BaseModel):
    csv_text: str = Field(min_length=1, max_length=5_000_000)
    downloaded_at: datetime | None = None
    source_uri: str = Field(
        default="https://trends.google.com/trends/", max_length=500
    )
    entity_ids: list[str] | None = Field(default=None, max_length=25)


class AutomationScheduleRequest(BaseModel):
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    max_retries: int = Field(default=1, ge=0, le=5)
    enabled: bool = True
    notify_on_success: bool = True
    notify_on_failure: bool = True


class PromotionDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    reviewer: str = Field(min_length=2, max_length=120)
    note: str = Field(min_length=10, max_length=1000)


class PromotionRevocationRequest(BaseModel):
    reviewer: str = Field(min_length=2, max_length=120)
    note: str = Field(min_length=10, max_length=1000)


class ModelGovernanceDecisionRequest(BaseModel):
    reviewer: str = Field(min_length=2, max_length=120)
    note: str = Field(min_length=10, max_length=1000)


def create_api(container: Container | None = None) -> FastAPI:
    dependencies = container or build_container()
    app = FastAPI(
        title=dependencies.settings.app_name,
        version=__version__,
        description="Quant research, model inference and portfolio service API",
    )

    @app.middleware("http")
    async def protect_write_endpoints(request: Request, call_next):
        denied = api_write_authorization(
            dependencies.settings, request.method,
            request.headers.get("Authorization", ""),
        )
        if denied:
            code, detail = denied
            return JSONResponse({"detail": detail}, status_code=code)
        return await call_next(request)

    @app.get("/api/v1/health", tags=["system"])
    def health(response: Response) -> dict[str, str]:
        snapshot = dependencies.health_service.check()
        if snapshot.status != "healthy":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return asdict(snapshot)

    @app.get("/api/v1/data/coverage", tags=["market-data"])
    def data_coverage() -> dict[str, object]:
        overview = dependencies.market_data_overview_service.get_overview()
        return {
            **overview,
            "coverage": [asdict(item) for item in overview["coverage"]],
        }

    @app.get("/api/v1/point-in-time/datasets", tags=["point-in-time-data"])
    def point_in_time_datasets() -> dict[str, object]:
        return jsonable_encoder(dependencies.point_in_time_data_service.overview())

    @app.get("/api/v1/point-in-time/observations", tags=["point-in-time-data"])
    def point_in_time_observations(
        dataset_key: str,
        entity_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        return jsonable_encoder(dependencies.point_in_time_data_service.query(
            dataset_key, entity_id, start, end, as_of, limit
        ))

    @app.post("/api/v1/point-in-time/ingestions", tags=["point-in-time-data"])
    def ingest_point_in_time(payload: PointInTimeIngestionRequest) -> dict[str, object]:
        return jsonable_encoder(dependencies.point_in_time_data_service.ingest(
            payload.dataset_key, payload.entity_id, payload.start, payload.end
        ))

    @app.get("/api/v1/google-trends", tags=["alternative-data"])
    def google_trends(
        keyword: str = "",
        as_of: datetime | None = None,
        limit: int = 100,
    ) -> dict[str, object]:
        return jsonable_encoder(dependencies.google_trends_service.overview(
            keyword, as_of=as_of, limit=limit
        ))

    @app.post("/api/v1/google-trends/imports", tags=["alternative-data"])
    def import_google_trends(
        payload: GoogleTrendsCsvImportRequest,
    ) -> dict[str, object]:
        return jsonable_encoder(dependencies.google_trends_service.import_csv(
            payload.csv_text,
            downloaded_at=payload.downloaded_at,
            source_uri=payload.source_uri,
            entity_ids=tuple(payload.entity_ids) if payload.entity_ids else None,
        ))

    @app.get("/api/v1/intraday-features", tags=["feature-store"])
    def intraday_features(symbol: str | None = None) -> dict[str, object]:
        return jsonable_encoder(
            dependencies.intraday_derivative_feature_pipeline.overview(symbol)
        )

    @app.post("/api/v1/pipelines/intraday-features", tags=["feature-store"])
    def build_intraday_features(lookback_days: int = 400) -> dict[str, object]:
        return jsonable_encoder(dependencies.intraday_derivative_feature_pipeline.run(
            lookback_days=max(2, min(3650, lookback_days))
        ))

    @app.get("/api/v1/corporate-events", tags=["events"])
    def corporate_events(
        symbol: str | None = None,
        scope: str = "all",
        limit: int = 200,
    ) -> dict[str, object]:
        return jsonable_encoder(dependencies.earnings_call_service.overview(
            symbol, scope if scope in {"all", "upcoming", "past"} else "all",
            limit=max(1, min(limit, 1000)),
        ))

    @app.post("/api/v1/pipelines/corporate-events", tags=["events"])
    def refresh_corporate_events(symbol: str = "ALL") -> dict[str, object]:
        return jsonable_encoder(dependencies.earnings_call_service.refresh(symbol))

    @app.post("/api/v1/data/ingestions", tags=["market-data"])
    def ingest_daily(payload: DailyIngestionRequest) -> dict[str, object]:
        result = dependencies.market_data_ingestion_service.ingest_daily(
            symbol=payload.symbol,
            market=payload.market,
            start=payload.start,
            end=payload.end,
        )
        return asdict(result)

    @app.get("/api/v1/analytics/command-center", tags=["analytics"])
    def command_center() -> dict[str, object]:
        return jsonable_encoder(
            dependencies.quant_analytics_service.command_center(
                dependencies.research_universe_service.active_symbols()
            )
        )

    @app.get("/api/v1/universe", tags=["universe"])
    def universe() -> list[dict[str, object]]:
        return jsonable_encoder(dependencies.research_universe_service.list_all())

    @app.get("/api/v1/universe/history", tags=["universe"])
    def universe_history(effective_date: date | None = None, market: str = "TW") -> dict[str, object]:
        audit = dependencies.universe_history_service.audit(effective_date, market)
        return jsonable_encoder({
            "audit": audit,
            "members": dependencies.universe_history_service.members_on(
                effective_date or date.today(), market
            ),
        })

    @app.post("/api/v1/universe/history/sync", tags=["universe"])
    def sync_universe_history() -> dict[str, object]:
        return jsonable_encoder(dependencies.universe_history_service.sync_taiwan())

    @app.post("/api/v1/pipelines/market-data/{market}", tags=["scheduler"])
    def run_market_data_pipeline(market: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.daily_market_data_pipeline.run(market))

    @app.post("/api/v1/pipelines/daily-research/{market}", tags=["scheduler"])
    def run_daily_research_pipeline(market: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.daily_research_pipeline.run(market))

    @app.post("/api/v1/pipelines/features/{market}", tags=["feature-store"])
    def build_feature_store(market: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.feature_label_pipeline.run(market))

    @app.post("/api/v1/pipelines/taiwan-data", tags=["taiwan-data"])
    def run_taiwan_data() -> dict[str, object]:
        return jsonable_encoder(dependencies.taiwan_data_pipeline.run("TW"))

    @app.get("/api/v1/taiwan-data", tags=["taiwan-data"])
    def taiwan_data(symbol: str | None = None) -> dict[str, object]:
        return jsonable_encoder(dependencies.taiwan_data_overview_service.get_overview(symbol))

    @app.get("/api/v1/news", tags=["news"])
    def news(symbol: str | None = None, limit: int = 100) -> dict[str, object]:
        return jsonable_encoder(dependencies.news_research_service.get_overview(symbol, limit))

    @app.get("/api/v1/reports", tags=["reports-rag"])
    def reports(question: str | None = None) -> dict[str, object]:
        return jsonable_encoder(dependencies.daily_report_knowledge_service.overview(question))

    @app.post("/api/v1/reports/generate/{market}", tags=["reports-rag"])
    def generate_report(market: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.daily_report_knowledge_service.generate(market))

    @app.post("/api/v1/reports/reindex", tags=["reports-rag"])
    def reindex_reports() -> dict[str, object]:
        return jsonable_encoder(dependencies.daily_report_knowledge_service.sync_index())

    @app.get("/api/v1/automation", tags=["automation"])
    def automation_overview() -> dict[str, object]:
        return jsonable_encoder(dependencies.automation_service.overview())

    @app.put("/api/v1/automation/schedules/{job_key}", tags=["automation"])
    def update_automation_schedule(job_key: str, payload: AutomationScheduleRequest) -> dict[str, object]:
        return jsonable_encoder(dependencies.automation_service.update_schedule(
            job_key, **payload.model_dump()
        ))

    @app.post("/api/v1/automation/run/{job_key}", tags=["automation"])
    def run_automation(job_key: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.automation_service.execute(job_key))

    @app.post("/api/v1/automation/email/test", tags=["automation"])
    def test_automation_email() -> dict[str, str]:
        return {"status": dependencies.automation_service.test_email()}

    @app.post("/api/v1/pipelines/macro-data", tags=["macro-data"])
    def run_macro_data() -> dict[str, object]:
        return jsonable_encoder(dependencies.macro_data_pipeline.run())

    @app.get("/api/v1/macro-data", tags=["macro-data"])
    def macro_data() -> dict[str, object]:
        return jsonable_encoder(dependencies.macro_data_overview_service.get_overview())

    @app.get("/api/v1/features/coverage", tags=["feature-store"])
    def feature_store_coverage() -> dict[str, object]:
        return jsonable_encoder(dependencies.feature_store_overview_service.get_overview())

    @app.post("/api/v1/pipelines/factor-research/{market}", tags=["factor-research"])
    def run_factor_research(market: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.factor_research_pipeline.run(market))

    @app.get("/api/v1/factors/research", tags=["factor-research"])
    def factor_research_results() -> dict[str, object]:
        return jsonable_encoder(dependencies.factor_research_overview_service.get_overview())

    @app.post("/api/v1/pipelines/backtests/{market}", tags=["backtest"])
    def run_backtest_research(market: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.backtest_research_pipeline.run(market))

    @app.get("/api/v1/backtests", tags=["backtest"])
    def backtest_results() -> dict[str, object]:
        return jsonable_encoder(dependencies.backtest_research_overview_service.get_overview())

    @app.get("/api/v1/backtests/{run_id}", tags=["backtest"])
    def backtest_detail(run_id: int) -> dict[str, object]:
        artifact = dependencies.backtest_research_overview_service.get_artifact(run_id)
        if artifact is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Backtest run not found")
        return jsonable_encoder(artifact)

    @app.post("/api/v1/pipelines/ensembles/{market}", tags=["strategy-ensemble"])
    def run_strategy_ensemble(market: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.ensemble_research_pipeline.run(market))

    @app.get("/api/v1/ensembles", tags=["strategy-ensemble"])
    def ensemble_results() -> dict[str, object]:
        return jsonable_encoder(dependencies.ensemble_overview_service.get_overview())

    @app.get("/api/v1/ensembles/{run_id}", tags=["strategy-ensemble"])
    def ensemble_detail(run_id: int) -> dict[str, object]:
        artifact = dependencies.ensemble_overview_service.get_artifact(run_id)
        if artifact is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Ensemble run not found")
        return jsonable_encoder(artifact)

    @app.post("/api/v1/pipelines/portfolios/{market}", tags=["portfolio-risk"])
    def run_portfolio_research(market: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.portfolio_research_pipeline.run(market))

    @app.get("/api/v1/portfolios", tags=["portfolio-risk"])
    def portfolio_results() -> dict[str, object]:
        return jsonable_encoder(dependencies.portfolio_overview_service.get_overview())

    @app.get("/api/v1/portfolios/{run_id}", tags=["portfolio-risk"])
    def portfolio_detail(run_id: int) -> dict[str, object]:
        artifact = dependencies.portfolio_overview_service.get_artifact(run_id)
        if artifact is None:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Portfolio run not found")
        return jsonable_encoder(artifact)

    @app.get("/api/v1/pipelines/runs", tags=["scheduler"])
    def pipeline_runs() -> list[dict[str, object]]:
        return jsonable_encoder(dependencies.daily_market_data_pipeline.list_recent_runs())

    @app.post("/api/v1/pipelines/models/{market}", tags=["model-zoo"])
    def run_model_research(market: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.model_research_pipeline.run(market))

    @app.get("/api/v1/models/experiments", tags=["model-zoo"])
    def model_experiments() -> dict[str, object]:
        return jsonable_encoder(dependencies.model_research_overview_service.get_overview())

    @app.post("/api/v1/pipelines/decisions/{market}", tags=["decision-support"])
    def run_decisions(market: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.daily_decision_pipeline.run(market))

    @app.get("/api/v1/decisions", tags=["decision-support"])
    def decisions(market: str | None = None) -> dict[str, object]:
        return jsonable_encoder(dependencies.decision_overview_service.get_overview(market))

    @app.get("/api/v1/stocks/{symbol}/research", tags=["decision-support"])
    def stock_research(symbol: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.stock_research_service.get(symbol))

    @app.get("/api/v1/explanations/{symbol}", tags=["explainability"])
    def model_explanations(symbol: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.explainability_service.get(symbol))

    @app.get("/api/v1/rl/environment/{symbol}", tags=["reinforcement-learning"])
    def rl_environment(symbol: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.rl_environment_service.overview(symbol))

    @app.post("/api/v1/rl/walk-forward/{symbol}", tags=["reinforcement-learning"])
    def run_rl_walk_forward(symbol: str) -> dict[str, object]:
        return jsonable_encoder(
            dependencies.rl_environment_service.run_walk_forward(symbol)
        )

    @app.post("/api/v1/rl/cpu-agent/{symbol}", tags=["reinforcement-learning"])
    def run_rl_cpu_agent(symbol: str) -> dict[str, object]:
        return jsonable_encoder(
            dependencies.rl_environment_service.run_cpu_agent(symbol)
        )

    @app.post(
        "/api/v1/rl/neural-agent/{algorithm}/{symbol}",
        tags=["reinforcement-learning"],
    )
    def run_rl_neural_agent(algorithm: str, symbol: str) -> dict[str, object]:
        return jsonable_encoder(
            dependencies.rl_environment_service.run_neural_agent(algorithm, symbol)
        )

    @app.get("/api/v1/shadow-trading", tags=["shadow-trading"])
    def shadow_trading() -> dict[str, object]:
        return jsonable_encoder(dependencies.shadow_trading_service.overview())

    @app.post(
        "/api/v1/shadow-trading/orders/{algorithm}/{symbol}",
        tags=["shadow-trading"],
    )
    def generate_shadow_order(algorithm: str, symbol: str) -> dict[str, object]:
        return jsonable_encoder(
            dependencies.shadow_trading_service.generate(symbol, algorithm)
        )

    @app.post("/api/v1/shadow-trading/process", tags=["shadow-trading"])
    def process_shadow_orders() -> dict[str, object]:
        return jsonable_encoder(
            dependencies.shadow_trading_service.process_pending()
        )

    @app.get("/api/v1/promotions", tags=["promotion-sandbox"])
    def promotions() -> dict[str, object]:
        return jsonable_encoder(dependencies.promotion_service.overview())

    @app.post(
        "/api/v1/promotions/evaluate/{algorithm}/{symbol}",
        tags=["promotion-sandbox"],
    )
    def evaluate_promotion(algorithm: str, symbol: str) -> dict[str, object]:
        return jsonable_encoder(dependencies.promotion_service.evaluate(symbol, algorithm))

    @app.post(
        "/api/v1/promotions/{review_id}/decision", tags=["promotion-sandbox"]
    )
    def decide_promotion(
        review_id: int, payload: PromotionDecisionRequest
    ) -> dict[str, object]:
        return jsonable_encoder(dependencies.promotion_service.review(
            review_id, payload.decision, payload.reviewer, payload.note
        ))

    @app.post(
        "/api/v1/promotions/{review_id}/revoke", tags=["promotion-sandbox"]
    )
    def revoke_promotion(
        review_id: int, payload: PromotionRevocationRequest
    ) -> dict[str, object]:
        return jsonable_encoder(dependencies.promotion_service.revoke(
            review_id, payload.reviewer, payload.note
        ))

    @app.post(
        "/api/v1/promotions/{review_id}/sandbox/{shadow_order_id}",
        tags=["promotion-sandbox"],
    )
    def submit_promotion_sandbox(
        review_id: int, shadow_order_id: int
    ) -> dict[str, object]:
        return jsonable_encoder(
            dependencies.promotion_service.submit_sandbox(review_id, shadow_order_id)
        )

    @app.get("/api/v1/model-governance", tags=["model-governance"])
    def model_governance() -> dict[str, object]:
        return jsonable_encoder(dependencies.model_governance_service.overview())

    @app.get("/api/v1/data-quality", tags=["data-quality"])
    def data_quality() -> dict[str, object]:
        return jsonable_encoder(dependencies.data_quality_service.overview())

    @app.post("/api/v1/data-quality/{market}", tags=["data-quality"])
    def evaluate_data_quality(market: str, stage: str = "full") -> dict[str, object]:
        return jsonable_encoder(dependencies.data_quality_service.evaluate(market, stage=stage))

    @app.post("/api/v1/model-governance/refresh", tags=["model-governance"])
    def refresh_model_governance() -> dict[str, object]:
        return jsonable_encoder(dependencies.model_governance_service.refresh())

    @app.post(
        "/api/v1/model-governance/{entry_id}/promote", tags=["model-governance"]
    )
    def promote_model_champion(
        entry_id: int, payload: ModelGovernanceDecisionRequest
    ) -> dict[str, object]:
        return jsonable_encoder(dependencies.model_governance_service.promote(
            entry_id, payload.reviewer, payload.note
        ))

    @app.post(
        "/api/v1/model-governance/{entry_id}/demote", tags=["model-governance"]
    )
    def demote_model_champion(
        entry_id: int, payload: ModelGovernanceDecisionRequest
    ) -> dict[str, object]:
        return jsonable_encoder(dependencies.model_governance_service.demote(
            entry_id, payload.reviewer, payload.note
        ))

    return app


app = create_api()


def main() -> None:
    settings = build_container().settings
    uvicorn.run("quant_platform.api.app:app", host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
