import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pandas as pd

from quant_platform.backtest.engine import (
    BiasSafeBacktestEngine,
    ExecutionPolicy,
    RiskPolicy,
    WalkForwardPolicy,
)
from quant_platform.domain.entities import MarketBar
from quant_platform.config import Settings
from quant_platform.container import build_container
from quant_platform.database.repositories import SqlAlchemyBacktestResearchRepository


def make_bars(prices: list[float]) -> list[MarketBar]:
    bars = []
    for index, price_value in enumerate(prices):
        event_time = datetime(2024, 1, 1, 21, tzinfo=UTC) + timedelta(days=index)
        price = Decimal(str(price_value))
        bars.append(
            MarketBar(
                symbol="TEST",
                market="US",
                interval="1d",
                event_time=event_time,
                available_time=event_time + timedelta(minutes=15),
                ingested_at=event_time + timedelta(hours=1),
                open=price,
                high=price,
                low=price,
                close=price,
                adjusted_close=price,
                volume=100_000,
                source="test",
            )
        )
    return bars


class TrainWinnerStrategy:
    name = "train_winner"
    version = "1.0.0"
    warmup = 1
    parameter_grid = ({"active": 1}, {"active": 0})

    def generate(self, frame, parameters, regimes_by_date):
        return pd.Series(float(parameters["active"]), index=frame.index)


def test_signal_is_executed_at_next_session_open():
    bars = make_bars([100 + index for index in range(12)])
    engine = BiasSafeBacktestEngine(
        ExecutionPolicy(0, 0), RiskPolicy(stop_loss=1, take_profit=10, target_volatility=10)
    )
    frame = engine._frame(bars)
    signals = pd.Series([0, 0, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0], dtype=float)

    simulation = engine._simulate(frame, signals, 1, 10)

    assert simulation.trades[0].entry_time == bars[6].event_time
    assert simulation.trades[0].exit_time == bars[8].event_time


def test_walk_forward_selects_on_train_and_reports_losing_oos_fold():
    train_up = [100 + index for index in range(40)]
    test_down = [140 - index * 1.5 for index in range(20)]
    second_train = [110 + index * 0.3 for index in range(20)]
    prices = train_up + test_down + second_train + test_down
    engine = BiasSafeBacktestEngine(
        ExecutionPolicy(0, 0),
        RiskPolicy(stop_loss=1, take_profit=10, target_volatility=10),
        WalkForwardPolicy(train_sessions=40, test_sessions=20, step_sessions=20, minimum_folds=2),
    )

    artifact = engine.run(make_bars(prices), TrainWinnerStrategy())

    assert artifact is not None
    assert json.loads(artifact.folds[0].selected_parameters_json) == {"active": 1}
    assert artifact.folds[0].test_return < 0
    assert artifact.run.observation_count == 60
    assert artifact.run.promotion_gate == "RESEARCH"
    assert artifact.run.fold_count < 4


def test_cost_and_slippage_reduce_oos_equity():
    prices = [100 + (index % 8) for index in range(120)]
    policy = WalkForwardPolicy(train_sessions=40, test_sessions=20, step_sessions=20, minimum_folds=2)
    risk = RiskPolicy(stop_loss=1, take_profit=10, target_volatility=10)
    free = BiasSafeBacktestEngine(ExecutionPolicy(0, 0), risk, policy)
    costly = BiasSafeBacktestEngine(ExecutionPolicy(25, 25), risk, policy)

    free_artifact = free.run(make_bars(prices), TrainWinnerStrategy())
    costly_artifact = costly.run(make_bars(prices), TrainWinnerStrategy())

    assert free_artifact is not None and costly_artifact is not None
    assert costly_artifact.run.total_return < free_artifact.run.total_return


def test_backtest_repository_replaces_children_idempotently(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'backtests.db'}"))
    repository = SqlAlchemyBacktestResearchRepository(container.database.session_factory)
    engine = BiasSafeBacktestEngine(
        ExecutionPolicy(0, 0),
        RiskPolicy(stop_loss=1, take_profit=10, target_volatility=10),
        WalkForwardPolicy(train_sessions=40, test_sessions=20, step_sessions=20, minimum_folds=2),
    )
    artifact = engine.run(make_bars([100 + index * 0.2 for index in range(120)]), TrainWinnerStrategy())
    assert artifact is not None

    first_id = repository.save(artifact)
    second_id = repository.save(artifact)
    stored = repository.get(first_id)

    assert first_id == second_id
    assert len(repository.list_runs()) == 1
    assert stored is not None
    assert len(stored.folds) == len(artifact.folds)
    assert len(stored.equity) == len(artifact.equity)


def test_backtest_lab_renders_empty_research_state(tmp_path):
    container = build_container(Settings(database_url=f"sqlite:///{tmp_path / 'page.db'}"))
    from quant_platform.dashboard.app import create_app

    response = create_app(container).test_client().get("/backtests")

    assert response.status_code == 200
    assert "Bias-safe Backtest Lab" in response.get_data(as_text=True)
