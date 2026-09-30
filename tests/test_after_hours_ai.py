from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
import time

from quant_platform.application.after_hours_ai import AfterHoursAiService, ModelReplayPolicy
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.dashboard.app import create_app
from quant_platform.domain.entities import (
    DailyDecision, MarketBar, ModelExperiment, ModelPrediction,
)


NOW = datetime(2026, 7, 27, 5, 50, tzinfo=UTC)


def _decision(
    status: str = "候選",
    weight: float = 0.10,
    risks: str = "[]",
    gates: str = "[]",
    symbol: str = "2330.TW",
) -> DailyDecision:
    return DailyDecision(
        id=1,
        market="TW",
        symbol=symbol,
        decision_version="1.0.0",
        event_time=NOW,
        status=status,
        score=0.82,
        predicted_return_5d=0.03,
        prediction_dispersion=0.01,
        model_rank=0.9,
        regime="BULL_NORMAL_VOL",
        factor_score=0.7,
        strategy_score=0.8,
        suggested_weight=weight,
        reasons_json='["多模型與策略一致偏多"]',
        risks_json=risks,
        gate_checks_json=gates,
        computed_at=NOW,
    )


def _bar() -> MarketBar:
    return MarketBar(
        symbol="2330.TW",
        market="TW",
        interval="1d",
        event_time=NOW,
        available_time=NOW,
        ingested_at=NOW,
        open=Decimal("98"),
        high=Decimal("102"),
        low=Decimal("97"),
        close=Decimal("100"),
        adjusted_close=Decimal("100"),
        volume=1_000_000,
        source="test",
    )


class DecisionRepo:
    def __init__(self, decision):
        self.decision = decision

    def list_latest(self, market=None):
        if isinstance(self.decision, (list, tuple)):
            return list(self.decision)
        return [self.decision] if self.decision else []


class BarRepo:
    def list_bars(self, symbol, interval="1d", source=None, as_of=None):
        return [_bar()] if symbol.endswith(".TW") else []


class PaperService:
    def __init__(self):
        self.orders = []

    def overview(self, now=None):
        return SimpleNamespace(
            account=SimpleNamespace(cash=Decimal("1000000")),
            positions=(),
            equity=Decimal("1000000"),
            orders=tuple(self.orders),
            fills=(),
            total_return=Decimal("0"),
        )

    def submit_order(self, symbol, side, quantity, now=None):
        order = SimpleNamespace(
            symbol=symbol, side=side, quantity=quantity, submitted_at=now,
            status=SimpleNamespace(value="pending"),
        )
        self.orders.append(order)
        return order


def test_after_hours_ai_converts_candidate_weight_to_odd_lot_quantity():
    plan = AfterHoursAiService(
        DecisionRepo(_decision()), BarRepo(), PaperService()
    ).generate(NOW)

    assert plan.buy_count == 1
    order = plan.orders[0]
    assert order.side == "BUY"
    assert order.quantity == 1000
    assert order.odd_lot_order_count == 2
    assert order.target_weight == 0.10
    assert order.estimated_amount == Decimal("100000.00")


def test_after_hours_ai_submits_to_paper_idempotently():
    paper = PaperService()
    service = AfterHoursAiService(DecisionRepo(_decision()), BarRepo(), paper)
    plan = service.generate(NOW)

    first = service.submit_to_paper(plan=plan, now=NOW)
    second = service.submit_to_paper(plan=plan, now=NOW)

    assert first.submitted == 1
    assert first.reused == 0
    assert second.submitted == 0
    assert second.reused == 1
    assert len(paper.orders) == 1


def test_after_hours_ai_refuses_to_submit_after_odd_lot_window():
    paper = PaperService()
    service = AfterHoursAiService(DecisionRepo(_decision()), BarRepo(), paper)
    after_window = datetime(2026, 7, 27, 6, 31, tzinfo=UTC)
    plan = service.generate(after_window)

    assert plan.buy_count == 1
    assert plan.snapshot_kind == "expired"
    assert plan.submission_allowed is False
    assert "禁止補送" in plan.headline
    assert service.submit_to_paper(plan=plan, now=after_window).submitted == 0
    assert paper.orders == []


def test_after_hours_ai_refuses_blocked_candidate():
    plan = AfterHoursAiService(
        DecisionRepo(_decision(gates='["股票池少於 30 檔"]')),
        BarRepo(),
        PaperService(),
    ).generate(NOW)

    assert plan.orders == ()
    assert "今日不交易" in plan.headline
    assert plan.watchlist[0].blocker == "股票池少於 30 檔"


def test_after_hours_ai_keeps_yesterday_plan_as_read_only_history():
    paper = PaperService()
    service = AfterHoursAiService(DecisionRepo(_decision()), BarRepo(), paper)
    plan = service.generate(datetime(2026, 7, 28, 5, 30, tzinfo=UTC))

    assert plan.buy_count == 1
    assert plan.snapshot_kind == "historical"
    assert plan.submission_allowed is False
    assert "事後補算：2026-07-27" in plan.headline
    assert "當時未送單" in plan.headline
    submission = service.submit_to_paper(
        plan=plan, now=datetime(2026, 7, 28, 5, 30, tzinfo=UTC)
    )
    assert submission.submitted == 0
    assert paper.orders == []


def test_after_hours_ai_refuses_old_snapshot_outside_history_window():
    plan = AfterHoursAiService(
        DecisionRepo(_decision()), BarRepo(), PaperService()
    ).generate(datetime(2026, 8, 3, 5, 30, tzinfo=UTC))

    assert plan.orders == ()
    assert plan.headline == "今日不交易：決策資料不是今日盤後快照"
    assert "決策不是今日盤後快照" in plan.watchlist[0].blocker


def test_after_hours_ai_shows_latest_close_plan_on_weekend():
    friday_close = datetime(2026, 7, 31, 6, 0, tzinfo=UTC)
    saturday_decision = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
    sunday_review = datetime(2026, 8, 2, 6, 0, tzinfo=UTC)
    decision = replace(_decision(), event_time=saturday_decision, computed_at=saturday_decision)

    class WeekendBarRepo:
        def list_bars(self, symbol, interval="1d", source=None, as_of=None):
            return [replace(
                _bar(), event_time=friday_close, available_time=friday_close,
                ingested_at=friday_close,
            )]

    plan = AfterHoursAiService(
        DecisionRepo(decision), WeekendBarRepo(), PaperService()
    ).generate(sunday_review)

    assert plan.buy_count == 1
    assert "休市預覽：下個交易日模擬買進 1 筆" in plan.headline
    assert plan.mode.startswith("休市預覽")
    assert plan.submission_allowed is False


def test_after_hours_ai_limits_daily_actions_and_buys():
    decisions = [
        _decision(symbol=f"{2300 + index}.TW")
        for index in range(8)
    ]
    plan = AfterHoursAiService(
        DecisionRepo(decisions), BarRepo(), PaperService()
    ).generate(NOW)

    assert len(plan.orders) <= 3
    assert plan.buy_count == 2
    assert any("每日最多 3 個買賣動作" in rule for rule in plan.hard_rules)


def test_after_hours_ai_allows_small_research_only_paper_trade():
    decision = _decision(
        status="觀察",
        weight=0,
        gates='["模型尚未通過候選門檻", "策略整合尚未通過候選門檻"]',
    )
    plan = AfterHoursAiService(
        DecisionRepo(decision), BarRepo(), PaperService()
    ).generate(NOW)

    assert plan.buy_count == 1
    assert plan.orders[0].target_weight == 0.05
    assert plan.orders[0].quantity == 500
    assert "研究模擬" in plan.orders[0].reason


def test_after_hours_ai_page_is_available(tmp_path):
    container = build_container(
        Settings(database_url=f"sqlite:///{tmp_path / 'after-hours-ai.db'}")
    )
    response = create_app(container).test_client().get("/ai-trading")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "盤後 AI 決策" in body
    assert "今日模擬委託" in body
    assert "真實交易關閉" in body


def test_expired_daily_validation_cache_starts_a_new_worker(monkeypatch):
    service = AfterHoursAiService(DecisionRepo(None), BarRepo(), PaperService())
    service._validation_cache = (time.monotonic() - 301, SimpleNamespace())

    class FakeThread:
        def __init__(self, **kwargs):
            self.started = False

        def is_alive(self):
            return self.started

        def start(self):
            self.started = True

    monkeypatch.setattr(
        "quant_platform.application.after_hours_ai.threading.Thread", FakeThread
    )

    service._start_validation_job()

    assert service._validation_cache is None
    assert service._validation_job is not None
    assert service._validation_job.started is True


def test_expired_model_validation_cache_starts_a_new_worker(monkeypatch):
    service = AfterHoursAiService(DecisionRepo(None), BarRepo(), PaperService())
    service._model_validation_cache = (time.monotonic() - 901, None)

    class FakeThread:
        def __init__(self, **kwargs):
            self.started = False

        def is_alive(self):
            return self.started

        def start(self):
            self.started = True

    monkeypatch.setattr(
        "quant_platform.application.after_hours_ai.threading.Thread", FakeThread
    )

    service._start_model_validation_job()

    assert service._model_validation_cache is None
    assert service._model_validation_job is not None
    assert service._model_validation_job.started is True


def test_after_hours_page_plan_is_cached_until_data_changes():
    class CountingDecisionRepo(DecisionRepo):
        def __init__(self):
            super().__init__(None)
            self.calls = 0

        def list_latest(self, market=None):
            self.calls += 1
            return super().list_latest(market)

    decisions = CountingDecisionRepo()
    service = AfterHoursAiService(decisions, BarRepo(), PaperService())

    first = service.plan_for_page()
    second = service.plan_for_page()

    assert first is second
    assert decisions.calls == 1

    service.invalidate_validation_cache()
    service.plan_for_page()
    assert decisions.calls == 2


def test_after_hours_ai_replays_historical_oos_model_predictions():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    symbols = ["0050.TW"] + [f"{2300 + index}.TW" for index in range(10)]
    bars = {}
    for symbol_index, symbol in enumerate(symbols):
        growth = Decimal("0.001") if symbol == "0050.TW" else Decimal("0.002")
        price = Decimal("100") + symbol_index
        values = []
        for index in range(90):
            event_time = start + timedelta(days=index)
            open_price = price
            price *= Decimal("1") + growth
            values.append(MarketBar(
                symbol=symbol, market="TW", interval="1d", event_time=event_time,
                available_time=event_time, ingested_at=event_time,
                open=open_price, high=max(open_price, price), low=min(open_price, price),
                close=price, adjusted_close=price, volume=1_000_000,
                source="test",
            ))
        bars[symbol] = values

    experiment = ModelExperiment(
        id=1, market="TW", model_name="ridge_linear", model_version="1",
        label_name="future_return_5d", experiment_version="1.3.0",
        promotion_gate="RESEARCH", data_start=start, data_end=start + timedelta(days=79),
        observation_count=800, fold_count=3, feature_count=2,
        rmse=.02, mae=.01, r2=.01, directional_accuracy=.55,
        rank_ic=.03, long_short_spread=.01, feature_names_json='["a","b"]',
        parameters_json="{}", fold_metrics_json="[]", feature_importance_json="{}",
        limitations_json="[]", computed_at=start + timedelta(days=100),
    )
    predictions = []
    for day in range(80):
        event_time = start + timedelta(days=day)
        for index, symbol in enumerate(symbols[1:]):
            predictions.append(ModelPrediction(
                id=None, experiment_id=1, market="TW", symbol=symbol,
                model_name="ridge_linear", label_name="future_return_5d", horizon=5,
                event_time=event_time, available_time=event_time,
                predicted_value=0.005 + index * 0.001,
                rank_score=index / 9, computed_at=experiment.computed_at,
            ))

    model_repository = SimpleNamespace(
        list_runs=lambda market=None: [experiment],
        list_predictions_for_experiments=lambda experiment_ids: predictions,
    )
    bar_repository = SimpleNamespace(
        list_bars=lambda symbol, interval="1d", source=None, as_of=None: bars.get(symbol, [])
    )
    service = AfterHoursAiService(
        DecisionRepo(None), bar_repository, PaperService(), model_repository=model_repository
    )

    validation = service.validation(start + timedelta(days=100))

    assert validation.strategy_name == "AI 模型排序＋0050 核心"
    assert validation.decision_sessions >= 70
    assert validation.simulated_orders > 20
    assert validation.trades
    assert validation.benchmark_return is not None
    assert all("AI 五日再平衡" in item.reason for item in validation.trades)

    guarded = service.model_oos_validation(
        start + timedelta(days=100),
        policy=ModelReplayPolicy(
            name="測試跌幅保護",
            drawdown_trigger=0.01,
            drawdown_guard_gross_weight=0.10,
            drawdown_guard_rebalances=2,
        ),
    )

    assert guarded is not None
    assert guarded.strategy_name == "測試跌幅保護"
    assert any("組合跌幅保護啟動" in item.reason for item in guarded.trades)

    after_hours_proxy = service.model_oos_validation(
        start + timedelta(days=100),
        policy=ModelReplayPolicy(
            name="測試盤後零股代理",
            execution_mode="after_hours_close_proxy",
        ),
    )

    assert after_hours_proxy is not None
    assert "14:30 盤後零股成交" in after_hours_proxy.execution_assumption
    assert any("13:30 收盤價加滑價" in item.reason for item in after_hours_proxy.trades)

    tracker = service.model_forward_tracking(start + timedelta(days=100))
    assert tracker is not None
    assert tracker["id"] == 1
    assert tracker["tracked_sessions"] == 0
    assert tracker["target_sessions"] == 252


def test_new_negative_challenger_does_not_hide_replayable_model():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    replayable = ModelExperiment(
        id=1, market="TW", model_name="ridge_linear", model_version="1",
        label_name="future_return_5d", experiment_version="1.4.0",
        promotion_gate="RESEARCH", data_start=start, data_end=start + timedelta(days=79),
        observation_count=800, fold_count=3, feature_count=2,
        rmse=.02, mae=.01, r2=.01, directional_accuracy=.55,
        rank_ic=.03, long_short_spread=.01, feature_names_json='["a","b"]',
        parameters_json="{}", fold_metrics_json="[]", feature_importance_json="{}",
        limitations_json="[]", computed_at=start + timedelta(days=100),
    )
    negative_challenger = replace(
        replayable,
        id=2,
        experiment_version="1.5.0",
        rank_ic=-.01,
        computed_at=start + timedelta(days=101),
    )
    model_repository = SimpleNamespace(
        prediction_coverage=lambda experiment_id: (800, 80),
    )
    service = AfterHoursAiService(
        DecisionRepo(None), BarRepo(), PaperService(), model_repository=model_repository
    )

    selected = service._select_replay_experiments(
        [replayable, negative_challenger], {"ridge_linear"}
    )

    assert selected == (replayable,)
